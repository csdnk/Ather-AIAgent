use ae_common::{
    AetherError, BlockId, CompactPolicy, CompactReport, EngineInstanceId, EngineKind,
    MigrationState, MigrationTaskId, RecoveryReport, Result, SegmentDescriptor, SegmentId,
    SnapshotId, StorageTier,
};
use std::collections::HashMap;
use std::sync::{Arc, RwLock};

pub trait TierManager: Send + Sync {
    fn describe_segments(&self) -> Result<Vec<SegmentDescriptor>>;

    fn current_tier(&self, segment: &SegmentId) -> Result<StorageTier>;

    fn is_hot(&self, segment: &SegmentId) -> Result<bool>;

    fn migrate(&self, segment: &SegmentId, target: StorageTier) -> Result<MigrationTaskId>;
}

/// Segment introspection + tier-migrate callback contract exposed to P3
/// (design doc §2.2.2 Segment Inspection / §2.2.3 Tier Migrate Callback).
///
/// The migration flow is: P3 calls `freeze` (write-fence the segment) → moves
/// the data → then calls `on_migrate_complete` (atomic route swap, segment
/// becomes writable/queryable again) or `on_migrate_failed` (keep old route,
/// just unfreeze). All three engines implement this against their own segment
/// model (E1 vector segment / E2 bucket / E3 graph segment).
pub trait SegmentControl: Send + Sync {
    /// All segment ids currently known to the engine.
    fn list_segments(&self) -> Result<Vec<SegmentId>>;

    fn migration_status(&self, segment: &SegmentId) -> Result<SegmentMigrationStatus>;

    fn prepare_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<MigrationAck>;

    fn cancel_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<MigrationAck>;

    fn complete_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
        new_block_ids: Vec<BlockId>,
    ) -> Result<MigrationAck>;

    fn fail_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
        reason: &str,
    ) -> Result<MigrationAck>;

    /// Write-fence a segment for migration. Engines reject the transition with
    /// `AetherError::Conflict` when the segment is not in a freezable state.
    fn freeze(&self, segment: &SegmentId) -> Result<()> {
        let status = self.migration_status(segment)?;
        if status.migration.active_migration_id.is_some() {
            return Err(AetherError::Conflict(format!(
                "segment {} already frozen",
                segment.0
            )));
        }
        self.prepare_migration(segment, &MigrationTaskId::random(), None)?;
        Ok(())
    }

    /// Release a write-fence without migrating (cancel / rollback).
    fn unfreeze(&self, segment: &SegmentId) -> Result<()> {
        let status = self.migration_status(segment)?;
        let migration_id = status
            .migration
            .active_migration_id
            .ok_or_else(|| AetherError::Conflict(format!("segment {} is not frozen", segment.0)))?;
        self.cancel_migration(segment, &migration_id, None)?;
        Ok(())
    }

    /// P3 finished migrating: atomically swap the segment route to the new
    /// blocks (double-buffering, design doc §3.6) and make it live again.
    fn on_migrate_complete(&self, segment: &SegmentId, new_block_ids: Vec<BlockId>) -> Result<()> {
        let status = self.migration_status(segment)?;
        let migration_id = status.migration.active_migration_id.ok_or_else(|| {
            AetherError::Conflict(format!(
                "segment {} must be frozen before migrate-complete",
                segment.0
            ))
        })?;
        self.complete_migration(segment, &migration_id, None, new_block_ids)?;
        Ok(())
    }

    /// P3 migration failed: keep the old route, release the fence.
    fn on_migrate_failed(&self, segment: &SegmentId, reason: &str) -> Result<()> {
        let status = self.migration_status(segment)?;
        let migration_id = status.migration.active_migration_id.ok_or_else(|| {
            AetherError::Conflict(format!(
                "segment {} must be frozen before migrate-failed",
                segment.0
            ))
        })?;
        self.fail_migration(segment, &migration_id, None, reason)?;
        Ok(())
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SegmentMigrationStatus {
    pub segment_id: SegmentId,
    pub state: String,
    pub migration: MigrationState,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MigrationAck {
    pub status: SegmentMigrationStatus,
    pub idempotent: bool,
}

impl MigrationAck {
    pub fn new(
        segment_id: SegmentId,
        state: impl Into<String>,
        migration: MigrationState,
        idempotent: bool,
    ) -> Self {
        Self {
            status: SegmentMigrationStatus {
                segment_id,
                state: state.into(),
                migration,
            },
            idempotent,
        }
    }
}

pub trait AetherEngine: Send + Sync {
    fn instance_id(&self) -> &EngineInstanceId;

    fn kind(&self) -> EngineKind {
        self.instance_id().kind
    }

    fn open(&self) -> Result<()>;

    fn recover(&self) -> Result<RecoveryReport>;

    fn snapshot(&self) -> Result<SnapshotId>;

    fn compact(&self, policy: CompactPolicy) -> Result<CompactReport>;

    fn tier_manager(&self) -> Option<Arc<dyn TierManager>> {
        None
    }
}

#[derive(Default)]
pub struct EngineRegistry {
    engines: RwLock<HashMap<EngineInstanceId, Arc<dyn AetherEngine>>>,
}

impl EngineRegistry {
    pub fn new() -> Self {
        Self {
            engines: RwLock::new(HashMap::new()),
        }
    }

    pub fn register(&self, engine: Arc<dyn AetherEngine>) -> Result<()> {
        let id = engine.instance_id().clone();
        let mut guard = self
            .engines
            .write()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        if guard.contains_key(&id) {
            return Err(AetherError::AlreadyExists(format!("engine instance {id}")));
        }
        guard.insert(id, engine);
        Ok(())
    }

    pub fn get(&self, id: &EngineInstanceId) -> Result<Arc<dyn AetherEngine>> {
        self.engines
            .read()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .get(id)
            .cloned()
            .ok_or_else(|| AetherError::NotFound(format!("engine instance {id}")))
    }

    pub fn list(&self) -> Result<Vec<EngineInstanceId>> {
        Ok(self
            .engines
            .read()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .keys()
            .cloned()
            .collect())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ae_common::{CompactReport, RecoveryReport};

    struct Dummy {
        id: EngineInstanceId,
    }

    impl AetherEngine for Dummy {
        fn instance_id(&self) -> &EngineInstanceId {
            &self.id
        }

        fn open(&self) -> Result<()> {
            Ok(())
        }

        fn recover(&self) -> Result<RecoveryReport> {
            Ok(RecoveryReport {
                replayed_records: 0,
                skipped_records: 0,
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

    #[test]
    fn registry_uses_instance_id() {
        let registry = EngineRegistry::new();
        let id = EngineInstanceId::new(EngineKind::Vector, "default");
        registry
            .register(Arc::new(Dummy { id: id.clone() }))
            .unwrap();
        assert_eq!(registry.get(&id).unwrap().kind(), EngineKind::Vector);
    }
}
