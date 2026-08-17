mod backend;
mod meta_store;
mod path_map;

pub use backend::{LocalFsBackend, ObjectBackend, SharedBackend};
pub use meta_store::{MemoryMetaStore, ObjectMetaStore, SqliteMetaStore};

use ae_common::{
    AetherError, BlockId, CompactPolicy, CompactReport, EngineInstanceId, EngineKind,
    MigrationState, MigrationTaskId, NamespaceId, OperationKind, RecoveryReport, Result, SegmentId,
    SnapshotId,
};
use ae_kernel::{AetherEngine, MigrationAck, SegmentControl, SegmentMigrationStatus};
use ae_wal::{SharedWal, WalPayload, WalRecord};
use path_map::object_data_path;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};
use std::path::Path;
use std::sync::{Arc, Mutex};
use uuid::Uuid;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ObjectMeta {
    pub bucket: String,
    pub key: String,
    pub etag: String,
    pub size: u64,
    pub md5_hex: Option<String>,
    pub blake3_hex: Option<String>,
    pub storage_path: String,
}

/// One page of a paginated object listing (design doc §4.6 LIST v2).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListPage {
    pub objects: Vec<ObjectMeta>,
    pub is_truncated: bool,
    pub next_continuation_token: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
enum ObjectWalOp {
    CreateBucket {
        bucket: String,
    },
    PutObject {
        meta: ObjectMeta,
        body_inline: Option<Vec<u8>>,
    },
    DeleteObject {
        bucket: String,
        key: String,
    },
    SetMigrationState {
        bucket: String,
        migration: MigrationState,
    },
}

/// In-flight multipart upload state (design doc §4.2 MultipartUpload/Part).
/// MVP keeps this in memory; parts live on the backend. Persisting the
/// multipart tables to SQLite + P1 WAL is a follow-up (doc §4.3.2).
struct MultipartState {
    bucket: String,
    key: String,
    parts: BTreeMap<u32, PartState>,
}

struct PartState {
    backend_path: String,
    size: u64,
    etag: String,
}

/// Per-bucket migration route state (design doc §3.6 / §4.5). A bucket is the
/// E2 freeze unit: while frozen it is read-only and rejects writes with
/// `P2Err_SegmentFrozen`; `route_epoch` bumps on each successful migrate.
pub struct LocalObjectEngine {
    id: EngineInstanceId,
    namespace_id: NamespaceId,
    wal: SharedWal,
    backend: SharedBackend,
    meta: Arc<dyn ObjectMetaStore>,
    uploads: Mutex<HashMap<String, MultipartState>>,
    routes: Mutex<HashMap<String, MigrationState>>,
}

impl LocalObjectEngine {
    pub fn new_with_sqlite(
        instance: impl Into<String>,
        data_dir: impl AsRef<Path>,
        wal: SharedWal,
    ) -> Result<Self> {
        let data_dir = data_dir.as_ref().to_path_buf();
        let sqlite_path = data_dir.join("metadata.sqlite");
        let meta = Arc::new(SqliteMetaStore::open(sqlite_path)?);
        let backend = Arc::new(LocalFsBackend::new(&data_dir)?);
        Ok(Self::new(instance, backend, wal, meta))
    }

    pub fn new(
        instance: impl Into<String>,
        backend: SharedBackend,
        wal: SharedWal,
        meta: Arc<dyn ObjectMetaStore>,
    ) -> Self {
        Self {
            id: EngineInstanceId::new(EngineKind::Object, instance),
            namespace_id: NamespaceId::default_for_engine(EngineKind::Object),
            wal,
            backend,
            meta,
            uploads: Mutex::new(HashMap::new()),
            routes: Mutex::new(HashMap::new()),
        }
    }

    /// Reject writes to a frozen bucket (design doc §4.5 write-fence).
    fn ensure_writable(&self, bucket: &str) -> Result<()> {
        let guard = self
            .routes
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        if guard
            .get(bucket)
            .and_then(|route| route.active_migration_id.as_ref())
            .is_some()
        {
            return Err(AetherError::SegmentFrozen(format!(
                "bucket {bucket} is frozen for migration"
            )));
        }
        Ok(())
    }

    pub fn create_bucket(&self, bucket: &str) -> Result<()> {
        validate_bucket(bucket)?;
        let op = ObjectWalOp::CreateBucket {
            bucket: bucket.to_string(),
        };
        self.append_op(OperationKind::CreateNamespace, &op)?;
        self.apply_op(op)
    }

    pub fn put_object(&self, bucket: &str, key: &str, data: &[u8]) -> Result<ObjectMeta> {
        let _span = tracing::info_span!("ae.object.put", engine = "object").entered();
        validate_bucket(bucket)?;
        validate_key(key)?;

        if !self.meta.bucket_exists(bucket)? {
            return Err(AetherError::NotFound(format!("bucket {bucket}")));
        }
        self.ensure_writable(bucket)?;

        let relative_path = object_data_path(bucket, key);
        self.backend.put(&relative_path, data)?;
        let meta = build_meta(bucket, key, data, relative_path);

        let body_inline = if data.len() <= 1024 * 1024 {
            Some(data.to_vec())
        } else {
            None
        };

        let op = ObjectWalOp::PutObject {
            meta: meta.clone(),
            body_inline,
        };
        self.append_op(OperationKind::PutObject, &op)?;
        self.meta.put_meta(meta.clone())?;
        Ok(meta)
    }

    pub fn get_object(&self, bucket: &str, key: &str) -> Result<Vec<u8>> {
        let _span = tracing::info_span!("ae.object.get", engine = "object").entered();
        let meta = self.head_object(bucket, key)?;
        self.backend.get(&meta.storage_path)
    }

    /// Range GET (S3 `Range: bytes=start-end`, inclusive end), design doc §4.6.
    /// `end` is inclusive; `None` reads to the end of the object.
    pub fn get_object_range(
        &self,
        bucket: &str,
        key: &str,
        start: u64,
        end: Option<u64>,
    ) -> Result<Vec<u8>> {
        let full = self.get_object(bucket, key)?;
        let len = full.len() as u64;
        if start >= len {
            return Err(AetherError::InvalidArgument(format!(
                "range start {start} beyond object size {len}"
            )));
        }
        // Inclusive end, clamped to the last byte.
        let last = end.unwrap_or(len - 1).min(len - 1);
        if last < start {
            return Err(AetherError::InvalidArgument(format!(
                "range end {last} < start {start}"
            )));
        }
        Ok(full[start as usize..=last as usize].to_vec())
    }

    pub fn head_object(&self, bucket: &str, key: &str) -> Result<ObjectMeta> {
        self.meta
            .get_meta(bucket, key)?
            .ok_or_else(|| AetherError::NotFound(format!("object {bucket}/{key}")))
    }

    pub fn delete_object(&self, bucket: &str, key: &str) -> Result<()> {
        let _span = tracing::info_span!("ae.object.delete", engine = "object").entered();
        self.ensure_writable(bucket)?;
        let op = ObjectWalOp::DeleteObject {
            bucket: bucket.to_string(),
            key: key.to_string(),
        };
        self.append_op(OperationKind::DeleteObject, &op)?;
        if let Some(meta) = self.meta.get_meta(bucket, key)? {
            self.backend.delete(&meta.storage_path)?;
        }
        self.meta.delete_meta(bucket, key)?;
        Ok(())
    }

    pub fn list_objects(&self, bucket: &str, prefix: &str) -> Result<Vec<ObjectMeta>> {
        self.meta.list_meta(bucket, prefix)
    }

    /// Paginated listing (S3 LIST v2 semantics, design doc §4.6 / §2.2.1).
    ///
    /// Keys are returned in ascending order. `continuation_token` is the last
    /// key of the previous page (exclusive start). The returned
    /// `next_continuation_token` is `Some(key)` when the result was truncated.
    pub fn list_objects_paged(
        &self,
        bucket: &str,
        prefix: &str,
        max_keys: usize,
        continuation_token: Option<&str>,
    ) -> Result<ListPage> {
        let all = self.meta.list_meta(bucket, prefix)?; // already sorted by key asc
        let start = match continuation_token {
            Some(token) => all.partition_point(|m| m.key.as_str() <= token),
            None => 0,
        };
        let max_keys = max_keys.max(1);
        let window: Vec<ObjectMeta> = all.into_iter().skip(start).take(max_keys + 1).collect();

        let truncated = window.len() > max_keys;
        let mut objects = window;
        objects.truncate(max_keys);
        let next_continuation_token = if truncated {
            objects.last().map(|m| m.key.clone())
        } else {
            None
        };
        Ok(ListPage {
            objects,
            is_truncated: truncated,
            next_continuation_token,
        })
    }

    // --- Multipart upload (design doc §4.6 Multipart API) ---

    /// Begin a multipart upload, returning an opaque upload_id.
    pub fn create_multipart_upload(&self, bucket: &str, key: &str) -> Result<String> {
        validate_bucket(bucket)?;
        validate_key(key)?;
        if !self.meta.bucket_exists(bucket)? {
            return Err(AetherError::NotFound(format!("bucket {bucket}")));
        }
        self.ensure_writable(bucket)?;
        let upload_id = Uuid::new_v4().to_string();
        self.uploads
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .insert(
                upload_id.clone(),
                MultipartState {
                    bucket: bucket.to_string(),
                    key: key.to_string(),
                    parts: BTreeMap::new(),
                },
            );
        Ok(upload_id)
    }

    /// Upload one part. `part_no` must be >= 1; re-uploading a part replaces it
    /// (idempotent on part_no, per doc §4.2 MultipartPart).
    pub fn upload_part(&self, upload_id: &str, part_no: u32, data: &[u8]) -> Result<String> {
        if part_no == 0 {
            return Err(AetherError::InvalidArgument("part_no must be >= 1".into()));
        }
        let mut guard = self
            .uploads
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let upload = guard
            .get_mut(upload_id)
            .ok_or_else(|| AetherError::NotFound(format!("upload {upload_id}")))?;
        let etag = md5_hex(data);
        let backend_path = format!(
            "uploads/{}/{}",
            upload_id,
            format_args!("part_{part_no:05}")
        );
        self.backend.put(&backend_path, data)?;
        upload.parts.insert(
            part_no,
            PartState {
                backend_path,
                size: data.len() as u64,
                etag: etag.clone(),
            },
        );
        Ok(etag)
    }

    /// Assemble the uploaded parts (ordered by part_no) into the final object.
    pub fn complete_multipart_upload(&self, upload_id: &str) -> Result<ObjectMeta> {
        let upload = {
            let mut guard = self
                .uploads
                .lock()
                .map_err(|e| AetherError::Internal(e.to_string()))?;
            guard
                .remove(upload_id)
                .ok_or_else(|| AetherError::NotFound(format!("upload {upload_id}")))?
        };

        if upload.parts.is_empty() {
            return Err(AetherError::InvalidArgument(
                "no parts uploaded for this upload_id".into(),
            ));
        }

        let mut assembled = Vec::new();
        for part in upload.parts.values() {
            assembled.extend(self.backend.get(&part.backend_path)?);
        }

        let meta = self.put_object(&upload.bucket, &upload.key, &assembled)?;

        // Best-effort cleanup of the staged parts.
        for part in upload.parts.values() {
            let _ = self.backend.delete(&part.backend_path);
        }
        Ok(meta)
    }

    /// List staged parts for an in-flight upload (part_no, size, etag).
    pub fn list_parts(&self, upload_id: &str) -> Result<Vec<(u32, u64, String)>> {
        let guard = self
            .uploads
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let upload = guard
            .get(upload_id)
            .ok_or_else(|| AetherError::NotFound(format!("upload {upload_id}")))?;
        Ok(upload
            .parts
            .iter()
            .map(|(no, p)| (*no, p.size, p.etag.clone()))
            .collect())
    }

    /// Discard an in-flight upload and its staged parts.
    pub fn abort_multipart_upload(&self, upload_id: &str) -> Result<()> {
        let upload = self
            .uploads
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .remove(upload_id);
        if let Some(upload) = upload {
            for part in upload.parts.values() {
                let _ = self.backend.delete(&part.backend_path);
            }
        }
        Ok(())
    }

    fn append_op(&self, operation: OperationKind, op: &ObjectWalOp) -> Result<()> {
        let payload = serde_json::to_vec(op)?;
        let record = WalRecord::new(
            EngineKind::Object,
            self.id.clone(),
            self.namespace_id.clone(),
            operation,
            WalPayload::Inline(payload),
        );
        self.wal.append(record)?;
        self.wal.flush()?;
        Ok(())
    }

    fn apply_op(&self, op: ObjectWalOp) -> Result<()> {
        match op {
            ObjectWalOp::CreateBucket { bucket } => self.meta.create_bucket(&bucket),
            ObjectWalOp::PutObject { meta, body_inline } => {
                // Restore object body on replay when it was small enough to be
                // inlined in the WAL and is missing from the backend.
                if let Some(bytes) = body_inline {
                    if !self.backend.exists(&meta.storage_path)? {
                        self.backend.put(&meta.storage_path, &bytes)?;
                    }
                }
                self.meta.create_bucket(&meta.bucket)?;
                self.meta.put_meta(meta)
            }
            ObjectWalOp::DeleteObject { bucket, key } => {
                if let Some(meta) = self.meta.get_meta(&bucket, &key)? {
                    self.backend.delete(&meta.storage_path)?;
                }
                self.meta.delete_meta(&bucket, &key)
            }
            ObjectWalOp::SetMigrationState { bucket, migration } => {
                self.routes
                    .lock()
                    .map_err(|e| AetherError::Internal(e.to_string()))?
                    .insert(bucket, migration);
                Ok(())
            }
        }
    }

    fn apply_record(&self, record: &WalRecord) -> Result<bool> {
        if record.engine != EngineKind::Object || record.instance_id != self.id {
            return Ok(false);
        }
        match &record.payload {
            WalPayload::Inline(bytes) => {
                let op: ObjectWalOp = serde_json::from_slice(bytes)?;
                self.apply_op(op)?;
                Ok(true)
            }
            WalPayload::BlockRef { .. } => Err(AetherError::Unsupported(
                "object BlockRef replay requires P1 block client".into(),
            )),
        }
    }
}

impl AetherEngine for LocalObjectEngine {
    fn instance_id(&self) -> &EngineInstanceId {
        &self.id
    }

    fn open(&self) -> Result<()> {
        Ok(())
    }

    fn recover(&self) -> Result<RecoveryReport> {
        self.routes
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .clear();
        let mut replayed = 0;
        let mut skipped = 0;
        for record in self.wal.replay()? {
            if self.apply_record(&record)? {
                replayed += 1;
            } else {
                skipped += 1;
            }
        }
        Ok(RecoveryReport {
            replayed_records: replayed,
            skipped_records: skipped,
        })
    }

    fn snapshot(&self) -> Result<SnapshotId> {
        Ok(SnapshotId::random())
    }

    fn compact(&self, _policy: CompactPolicy) -> Result<CompactReport> {
        Ok(CompactReport {
            compacted_segments: 0,
            reclaimed_bytes: 0,
        })
    }
}

impl SegmentControl for LocalObjectEngine {
    fn list_segments(&self) -> Result<Vec<SegmentId>> {
        Ok(self
            .meta
            .list_buckets()?
            .into_iter()
            .map(SegmentId::new)
            .collect())
    }

    fn migration_status(&self, segment: &SegmentId) -> Result<SegmentMigrationStatus> {
        let bucket = &segment.0;
        if !self.meta.bucket_exists(bucket)? {
            return Err(AetherError::NotFound(format!("bucket {bucket}")));
        }
        let mut guard = self
            .routes
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let migration = guard.entry(bucket.clone()).or_default().clone();
        Ok(SegmentMigrationStatus {
            segment_id: segment.clone(),
            state: Self::migration_lifecycle(&migration).into(),
            migration,
        })
    }

    fn prepare_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<MigrationAck> {
        let _span = tracing::info_span!("ae.segment.freeze", engine = "object").entered();
        self.transition_migration(segment, OperationKind::MigrationPrepare, |migration| {
            migration.prepare(migration_id, expected_route_epoch)
        })
    }

    fn cancel_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<MigrationAck> {
        self.transition_migration(segment, OperationKind::MigrationCancelled, |migration| {
            migration.cancel(migration_id, expected_route_epoch)
        })
    }

    fn complete_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
        new_block_ids: Vec<BlockId>,
    ) -> Result<MigrationAck> {
        let _span = tracing::info_span!("ae.segment.migrate_complete", engine = "object").entered();
        self.transition_migration(segment, OperationKind::MigrationComplete, |migration| {
            migration.complete(migration_id, expected_route_epoch, new_block_ids)
        })
    }

    fn fail_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
        _reason: &str,
    ) -> Result<MigrationAck> {
        self.transition_migration(segment, OperationKind::MigrationFailed, |migration| {
            migration.fail(migration_id, expected_route_epoch)
        })
    }
}

impl LocalObjectEngine {
    fn transition_migration(
        &self,
        segment: &SegmentId,
        operation: OperationKind,
        transition: impl FnOnce(&mut MigrationState) -> Result<bool>,
    ) -> Result<MigrationAck> {
        let bucket = &segment.0;
        if !self.meta.bucket_exists(bucket)? {
            return Err(AetherError::NotFound(format!("bucket {bucket}")));
        }

        let mut guard = self
            .routes
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let current = guard.entry(bucket.clone()).or_default();
        let mut migration = current.clone();
        let idempotent = transition(&mut migration)?;
        if !idempotent {
            let op = ObjectWalOp::SetMigrationState {
                bucket: bucket.clone(),
                migration: migration.clone(),
            };
            self.append_op(operation, &op)?;
            *current = migration.clone();
        }

        Ok(MigrationAck::new(
            segment.clone(),
            Self::migration_lifecycle(&migration),
            migration,
            idempotent,
        ))
    }

    fn migration_lifecycle(migration: &MigrationState) -> &'static str {
        if migration.active_migration_id.is_some() {
            "frozen"
        } else {
            "sealed"
        }
    }
}

fn md5_hex(data: &[u8]) -> String {
    format!("{:x}", md5::compute(data))
}

fn build_meta(bucket: &str, key: &str, data: &[u8], storage_path: String) -> ObjectMeta {
    let etag = md5_hex(data);
    let blake3_hex = blake3::hash(data).to_hex().to_string();
    ObjectMeta {
        bucket: bucket.to_string(),
        key: key.to_string(),
        etag: etag.clone(),
        size: data.len() as u64,
        md5_hex: Some(etag),
        blake3_hex: Some(blake3_hex),
        storage_path,
    }
}

fn validate_bucket(bucket: &str) -> Result<()> {
    if bucket.is_empty() || bucket.len() > 63 {
        return Err(AetherError::InvalidArgument(
            "bucket length must be 1..=63".into(),
        ));
    }
    Ok(())
}

fn validate_key(key: &str) -> Result<()> {
    if key.is_empty() {
        return Err(AetherError::InvalidArgument("object key is empty".into()));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use ae_wal::MemoryWal;
    use std::sync::Arc;

    #[test]
    fn object_uses_md5_etag_and_sqlite_persists_meta() {
        let dir = tempfile::tempdir().unwrap();
        let wal = Arc::new(MemoryWal::new());

        let engine =
            LocalObjectEngine::new_with_sqlite("default", dir.path(), wal.clone()).unwrap();
        engine.create_bucket("b1").unwrap();
        let meta = engine.put_object("b1", "nested/a.txt", b"hello").unwrap();
        assert_eq!(meta.etag, "5d41402abc4b2a76b9719d911017c592");

        let restarted =
            LocalObjectEngine::new_with_sqlite("default", dir.path(), wal.clone()).unwrap();
        let head = restarted.head_object("b1", "nested/a.txt").unwrap();
        assert_eq!(head.size, 5);
        assert_eq!(
            restarted.get_object("b1", "nested/a.txt").unwrap(),
            b"hello"
        );
    }

    #[test]
    fn multipart_assembles_parts_in_order() {
        let dir = tempfile::tempdir().unwrap();
        let wal = Arc::new(MemoryWal::new());
        let engine = LocalObjectEngine::new_with_sqlite("default", dir.path(), wal).unwrap();
        engine.create_bucket("b1").unwrap();

        let upload_id = engine.create_multipart_upload("b1", "big.bin").unwrap();
        // Upload out of order to prove BTreeMap orders by part_no.
        engine.upload_part(&upload_id, 2, b"world").unwrap();
        engine.upload_part(&upload_id, 1, b"hello ").unwrap();
        assert_eq!(engine.list_parts(&upload_id).unwrap().len(), 2);

        let meta = engine.complete_multipart_upload(&upload_id).unwrap();
        assert_eq!(meta.size, 11);
        assert_eq!(engine.get_object("b1", "big.bin").unwrap(), b"hello world");
        // Completing again must fail: the upload_id is consumed.
        assert!(engine.complete_multipart_upload(&upload_id).is_err());
    }

    #[test]
    fn range_get_returns_inclusive_slice() {
        let dir = tempfile::tempdir().unwrap();
        let wal = Arc::new(MemoryWal::new());
        let engine = LocalObjectEngine::new_with_sqlite("default", dir.path(), wal).unwrap();
        engine.create_bucket("b1").unwrap();
        engine.put_object("b1", "k", b"0123456789").unwrap();

        assert_eq!(
            engine.get_object_range("b1", "k", 2, Some(4)).unwrap(),
            b"234"
        );
        assert_eq!(engine.get_object_range("b1", "k", 7, None).unwrap(), b"789");
        // End past EOF clamps; start past EOF errors.
        assert_eq!(
            engine.get_object_range("b1", "k", 8, Some(99)).unwrap(),
            b"89"
        );
        assert!(engine.get_object_range("b1", "k", 10, None).is_err());
    }

    #[test]
    fn list_objects_paginates_with_continuation_token() {
        let dir = tempfile::tempdir().unwrap();
        let wal = Arc::new(MemoryWal::new());
        let engine = LocalObjectEngine::new_with_sqlite("default", dir.path(), wal).unwrap();
        engine.create_bucket("b1").unwrap();
        for i in 0..5 {
            engine.put_object("b1", &format!("k{i}"), b"x").unwrap();
        }

        let page1 = engine.list_objects_paged("b1", "k", 2, None).unwrap();
        assert_eq!(page1.objects.len(), 2);
        assert_eq!(page1.objects[0].key, "k0");
        assert_eq!(page1.objects[1].key, "k1");
        assert!(page1.is_truncated);
        let token = page1.next_continuation_token.clone().unwrap();
        assert_eq!(token, "k1");

        let page2 = engine
            .list_objects_paged("b1", "k", 2, Some(&token))
            .unwrap();
        assert_eq!(page2.objects[0].key, "k2");
        assert_eq!(page2.objects[1].key, "k3");
        assert!(page2.is_truncated);

        let page3 = engine
            .list_objects_paged("b1", "k", 2, page2.next_continuation_token.as_deref())
            .unwrap();
        assert_eq!(page3.objects.len(), 1);
        assert_eq!(page3.objects[0].key, "k4");
        assert!(!page3.is_truncated);
        assert!(page3.next_continuation_token.is_none());
    }

    #[test]
    fn bucket_freeze_rejects_writes_and_migrate_swaps_route() {
        let dir = tempfile::tempdir().unwrap();
        let wal = Arc::new(MemoryWal::new());
        let engine = LocalObjectEngine::new_with_sqlite("default", dir.path(), wal).unwrap();
        engine.create_bucket("b1").unwrap();
        engine.put_object("b1", "k0", b"v0").unwrap();

        let seg = SegmentId::new("b1");
        assert_eq!(engine.list_segments().unwrap(), vec![seg.clone()]);

        // Cannot migrate-complete a bucket that was never frozen.
        assert!(engine.on_migrate_complete(&seg, vec![]).is_err());

        // Freeze => writes rejected with SegmentFrozen, reads still work.
        engine.freeze(&seg).unwrap();
        assert!(engine.freeze(&seg).is_err());
        let err = engine.put_object("b1", "k1", b"v1").unwrap_err();
        assert!(matches!(err, AetherError::SegmentFrozen(_)));
        assert!(engine.delete_object("b1", "k0").is_err());
        assert_eq!(engine.get_object("b1", "k0").unwrap(), b"v0");

        // Migrate complete => writable again.
        engine
            .on_migrate_complete(&seg, vec![BlockId("blk-1".into())])
            .unwrap();
        engine.put_object("b1", "k1", b"v1").unwrap();
        assert_eq!(engine.get_object("b1", "k1").unwrap(), b"v1");

        // Failure path leaves the bucket writable.
        engine.freeze(&seg).unwrap();
        engine.on_migrate_failed(&seg, "disk full").unwrap();
        engine.put_object("b1", "k2", b"v2").unwrap();

        assert!(engine.freeze(&SegmentId::new("missing")).is_err());
    }

    #[test]
    fn bucket_migration_state_recovers_after_restart() {
        let dir = tempfile::tempdir().unwrap();
        let wal = Arc::new(MemoryWal::new());
        let engine =
            LocalObjectEngine::new_with_sqlite("restart", dir.path(), wal.clone()).unwrap();
        engine.create_bucket("b1").unwrap();
        let segment = SegmentId::new("b1");
        let migration = MigrationTaskId::new("object-restart");
        engine
            .prepare_migration(&segment, &migration, Some(0))
            .unwrap();

        let recovered =
            LocalObjectEngine::new_with_sqlite("restart", dir.path(), wal.clone()).unwrap();
        recovered.recover().unwrap();
        let frozen = recovered.migration_status(&segment).unwrap();
        assert_eq!(frozen.state, "frozen");
        assert_eq!(
            frozen.migration.active_migration_id.as_ref(),
            Some(&migration)
        );

        recovered
            .complete_migration(
                &segment,
                &migration,
                Some(0),
                vec![BlockId("object-block".into())],
            )
            .unwrap();
        let restarted = LocalObjectEngine::new_with_sqlite("restart", dir.path(), wal).unwrap();
        restarted.recover().unwrap();
        let completed = restarted.migration_status(&segment).unwrap();
        assert_eq!(completed.migration.route_epoch, 1);
        assert_eq!(completed.migration.block_ids[0].0, "object-block");
    }

    #[test]
    fn multipart_abort_discards_parts() {
        let dir = tempfile::tempdir().unwrap();
        let wal = Arc::new(MemoryWal::new());
        let engine = LocalObjectEngine::new_with_sqlite("default", dir.path(), wal).unwrap();
        engine.create_bucket("b1").unwrap();

        let upload_id = engine.create_multipart_upload("b1", "tmp.bin").unwrap();
        engine.upload_part(&upload_id, 1, b"data").unwrap();
        engine.abort_multipart_upload(&upload_id).unwrap();
        assert!(engine.list_parts(&upload_id).is_err());
        assert!(engine.head_object("b1", "tmp.bin").is_err());
    }
}
