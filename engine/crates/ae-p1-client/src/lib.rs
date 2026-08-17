use ae_common::{AetherError, BlockId, Result};
use ae_wal::{SharedWal, WalRecord};
use std::collections::HashMap;
use std::sync::{
    atomic::{AtomicBool, Ordering},
    Mutex,
};

pub trait P1Client: Send + Sync {
    fn put_block(&self, bytes: Vec<u8>) -> Result<BlockId>;
    fn get_block(&self, block_id: &BlockId) -> Result<Vec<u8>>;
    fn append_wal(&self, record: WalRecord) -> Result<()>;
}

pub struct MockP1Client {
    blocks: Mutex<HashMap<BlockId, Vec<u8>>>,
    wal: SharedWal,
    fail_next_put: AtomicBool,
    fail_next_get: AtomicBool,
    fail_next_wal: AtomicBool,
}

impl MockP1Client {
    pub fn new(wal: SharedWal) -> Self {
        Self {
            blocks: Mutex::new(HashMap::new()),
            wal,
            fail_next_put: AtomicBool::new(false),
            fail_next_get: AtomicBool::new(false),
            fail_next_wal: AtomicBool::new(false),
        }
    }

    pub fn fail_next_put(&self) {
        self.fail_next_put.store(true, Ordering::SeqCst);
    }

    pub fn fail_next_get(&self) {
        self.fail_next_get.store(true, Ordering::SeqCst);
    }

    pub fn fail_next_wal(&self) {
        self.fail_next_wal.store(true, Ordering::SeqCst);
    }
}

impl P1Client for MockP1Client {
    fn put_block(&self, bytes: Vec<u8>) -> Result<BlockId> {
        if self.fail_next_put.swap(false, Ordering::SeqCst) {
            return Err(AetherError::Storage("mock put_block failure".into()));
        }
        let block_id = BlockId::random();
        self.blocks
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .insert(block_id.clone(), bytes);
        Ok(block_id)
    }

    fn get_block(&self, block_id: &BlockId) -> Result<Vec<u8>> {
        if self.fail_next_get.swap(false, Ordering::SeqCst) {
            return Err(AetherError::Storage("mock get_block failure".into()));
        }
        self.blocks
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .get(block_id)
            .cloned()
            .ok_or_else(|| AetherError::NotFound(format!("block {}", block_id.0)))
    }

    fn append_wal(&self, record: WalRecord) -> Result<()> {
        if self.fail_next_wal.swap(false, Ordering::SeqCst) {
            return Err(AetherError::Storage("mock append_wal failure".into()));
        }
        self.wal.append(record)?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ae_wal::MemoryWal;
    use std::sync::Arc;

    #[test]
    fn mock_block_roundtrip() {
        let p1 = MockP1Client::new(Arc::new(MemoryWal::new()));
        let id = p1.put_block(b"abc".to_vec()).unwrap();
        assert_eq!(p1.get_block(&id).unwrap(), b"abc");
    }
}
