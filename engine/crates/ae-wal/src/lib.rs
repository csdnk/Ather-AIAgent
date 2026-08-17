use ae_common::{
    crc32, AetherError, BlockId, Checksum, EngineInstanceId, EngineKind, Lsn, NamespaceId,
    OperationKind, Result,
};
use crc32fast::Hasher;
use serde::{Deserialize, Serialize};
use std::fs::{File, OpenOptions};
use std::io::{Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc, Mutex,
};

const MAGIC: &[u8; 8] = b"AEWALV1\0";

#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum WalPayload {
    Inline(Vec<u8>),
    BlockRef {
        block_id: BlockId,
        offset: u64,
        length: u64,
        checksum: Checksum,
    },
}

impl WalPayload {
    pub fn bytes_for_checksum(&self) -> Vec<u8> {
        match self {
            WalPayload::Inline(data) => data.clone(),
            WalPayload::BlockRef {
                block_id,
                offset,
                length,
                checksum,
            } => format!("{}:{}:{}:{}", block_id.0, offset, length, checksum.0).into_bytes(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct WalRecord {
    pub version: u16,
    pub lsn: Lsn,
    pub engine: EngineKind,
    pub instance_id: EngineInstanceId,
    pub namespace_id: NamespaceId,
    pub operation: OperationKind,
    pub payload: WalPayload,
    pub checksum: Checksum,
}

impl WalRecord {
    pub fn new(
        engine: EngineKind,
        instance_id: EngineInstanceId,
        namespace_id: NamespaceId,
        operation: OperationKind,
        payload: WalPayload,
    ) -> Self {
        let checksum = crc32(&payload.bytes_for_checksum());
        Self {
            version: 1,
            lsn: Lsn(0),
            engine,
            instance_id,
            namespace_id,
            operation,
            payload,
            checksum,
        }
    }

    pub fn verify(&self) -> Result<()> {
        let expected = crc32(&self.payload.bytes_for_checksum());
        if expected != self.checksum {
            return Err(AetherError::Corrupted(format!(
                "wal payload checksum mismatch at lsn {}",
                self.lsn.0
            )));
        }
        Ok(())
    }
}

pub trait Wal: Send + Sync {
    fn append(&self, record: WalRecord) -> Result<Lsn>;
    fn replay(&self) -> Result<Vec<WalRecord>>;
    fn flush(&self) -> Result<()>;
}

pub type SharedWal = Arc<dyn Wal>;

#[derive(Debug, Default)]
pub struct MemoryWal {
    next_lsn: AtomicU64,
    records: Mutex<Vec<WalRecord>>,
}

impl MemoryWal {
    pub fn new() -> Self {
        Self {
            next_lsn: AtomicU64::new(1),
            records: Mutex::new(Vec::new()),
        }
    }
}

impl Wal for MemoryWal {
    fn append(&self, mut record: WalRecord) -> Result<Lsn> {
        let lsn = Lsn(self.next_lsn.fetch_add(1, Ordering::SeqCst));
        record.lsn = lsn;
        record.checksum = crc32(&record.payload.bytes_for_checksum());
        self.records
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .push(record);
        Ok(lsn)
    }

    fn replay(&self) -> Result<Vec<WalRecord>> {
        let records = self
            .records
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .clone();
        for record in &records {
            record.verify()?;
        }
        Ok(records)
    }

    fn flush(&self) -> Result<()> {
        Ok(())
    }
}

#[derive(Debug)]
pub struct FileWal {
    path: PathBuf,
    file: Mutex<File>,
    next_lsn: AtomicU64,
}

impl FileWal {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        let path = path.as_ref().to_path_buf();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let file = OpenOptions::new()
            .create(true)
            .read(true)
            .append(true)
            .open(&path)?;

        let mut wal = Self {
            path,
            file: Mutex::new(file),
            next_lsn: AtomicU64::new(1),
        };

        let records = wal.recover_and_truncate()?;
        let next = records.last().map(|r| r.lsn.0 + 1).unwrap_or(1);
        wal.next_lsn.store(next, Ordering::SeqCst);
        Ok(wal)
    }

    fn recover_and_truncate(&mut self) -> Result<Vec<WalRecord>> {
        let mut file = OpenOptions::new().read(true).write(true).open(&self.path)?;
        let (records, valid_len) = read_records_with_valid_len(&mut file)?;
        let total_len = file.metadata()?.len();
        if valid_len < total_len {
            file.set_len(valid_len)?;
        }
        Ok(records)
    }
}

impl Wal for FileWal {
    fn append(&self, mut record: WalRecord) -> Result<Lsn> {
        let lsn = Lsn(self.next_lsn.fetch_add(1, Ordering::SeqCst));
        record.lsn = lsn;
        record.checksum = crc32(&record.payload.bytes_for_checksum());
        let encoded = serde_json::to_vec(&record)?;
        let frame_crc = crc32_raw(&encoded);

        let mut file = self
            .file
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        file.write_all(MAGIC)?;
        file.write_all(&(encoded.len() as u32).to_le_bytes())?;
        file.write_all(&frame_crc.to_le_bytes())?;
        file.write_all(&encoded)?;
        Ok(lsn)
    }

    fn replay(&self) -> Result<Vec<WalRecord>> {
        let mut file = OpenOptions::new().read(true).open(&self.path)?;
        let (records, _) = read_records_with_valid_len(&mut file)?;
        Ok(records)
    }

    fn flush(&self) -> Result<()> {
        let file = self
            .file
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        file.sync_all()?;
        Ok(())
    }
}

fn crc32_raw(data: &[u8]) -> u32 {
    let mut hasher = Hasher::new();
    hasher.update(data);
    hasher.finalize()
}

fn read_records_with_valid_len(file: &mut File) -> Result<(Vec<WalRecord>, u64)> {
    file.seek(SeekFrom::Start(0))?;
    let mut records = Vec::new();
    let mut valid_len: u64 = 0;

    loop {
        let frame_start = file.stream_position()?;
        let mut magic = [0u8; 8];

        match file.read_exact(&mut magic) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(e) => return Err(e.into()),
        }

        if &magic != MAGIC {
            break;
        }

        let mut len_buf = [0u8; 4];
        if file.read_exact(&mut len_buf).is_err() {
            break;
        }
        let len = u32::from_le_bytes(len_buf) as usize;

        let mut crc_buf = [0u8; 4];
        if file.read_exact(&mut crc_buf).is_err() {
            break;
        }
        let expected_crc = u32::from_le_bytes(crc_buf);

        let mut encoded = vec![0u8; len];
        if file.read_exact(&mut encoded).is_err() {
            break;
        }

        let actual_crc = crc32_raw(&encoded);
        if actual_crc != expected_crc {
            break;
        }

        let record: WalRecord = serde_json::from_slice(&encoded)?;
        record.verify()?;
        records.push(record);
        valid_len = frame_start + MAGIC.len() as u64 + 4 + 4 + len as u64;
    }

    Ok((records, valid_len))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn memory_wal_replays_records() {
        let wal = MemoryWal::new();
        let instance = EngineInstanceId::new(EngineKind::Vector, "default");
        let record = WalRecord::new(
            EngineKind::Vector,
            instance,
            NamespaceId::new("ns"),
            OperationKind::Insert,
            WalPayload::Inline(b"hello".to_vec()),
        );
        wal.append(record).unwrap();
        assert_eq!(wal.replay().unwrap().len(), 1);
    }

    #[test]
    fn file_wal_truncates_tail_corruption() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("test.wal");

        {
            let wal = FileWal::open(&path).unwrap();
            let instance = EngineInstanceId::new(EngineKind::Object, "default");
            let record = WalRecord::new(
                EngineKind::Object,
                instance,
                NamespaceId::new("ns"),
                OperationKind::PutObject,
                WalPayload::Inline(b"payload".to_vec()),
            );
            wal.append(record).unwrap();
            wal.flush().unwrap();
        }

        {
            let mut f = OpenOptions::new().append(true).open(&path).unwrap();
            f.write_all(b"broken-tail").unwrap();
            f.sync_all().unwrap();
        }

        let wal = FileWal::open(&path).unwrap();
        let records = wal.replay().unwrap();
        assert_eq!(records.len(), 1);
    }
}
