use ae_common::Result;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Object data-plane backend abstraction (design doc §4.1, §4.3).
///
/// The Adapter control plane (metadata, quota, S3 semantics) is kept separate
/// from the data plane. MVP ships `LocalFsBackend`; a `SeaweedFsBackend` plugs
/// in behind this trait without touching the engine, matching the doc's
/// "数据面复用 SeaweedFS, 控制面自研" boundary.
pub trait ObjectBackend: Send + Sync {
    fn put(&self, path: &str, data: &[u8]) -> Result<()>;
    fn get(&self, path: &str) -> Result<Vec<u8>>;
    fn delete(&self, path: &str) -> Result<()>;
    fn exists(&self, path: &str) -> Result<bool>;
}

pub type SharedBackend = Arc<dyn ObjectBackend>;

/// Local filesystem backend with atomic tmp+rename writes.
pub struct LocalFsBackend {
    root: PathBuf,
}

impl LocalFsBackend {
    pub fn new(root: impl AsRef<Path>) -> Result<Self> {
        let root = root.as_ref().to_path_buf();
        fs::create_dir_all(&root)?;
        Ok(Self { root })
    }

    fn full(&self, path: &str) -> PathBuf {
        self.root.join(path)
    }
}

impl ObjectBackend for LocalFsBackend {
    fn put(&self, path: &str, data: &[u8]) -> Result<()> {
        let full = self.full(path);
        if let Some(parent) = full.parent() {
            fs::create_dir_all(parent)?;
        }
        let tmp = full.with_extension("tmp");
        fs::write(&tmp, data)?;
        fs::rename(&tmp, &full)?;
        Ok(())
    }

    fn get(&self, path: &str) -> Result<Vec<u8>> {
        fs::read(self.full(path)).map_err(Into::into)
    }

    fn delete(&self, path: &str) -> Result<()> {
        let _ = fs::remove_file(self.full(path));
        Ok(())
    }

    fn exists(&self, path: &str) -> Result<bool> {
        Ok(self.full(path).exists())
    }
}
