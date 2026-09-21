use crate::ObjectMeta;
use ae_common::{AetherError, Result};
use rusqlite::{params, Connection};
use std::collections::{HashMap, HashSet};
use std::path::Path;
use std::sync::Mutex;

pub trait ObjectMetaStore: Send + Sync {
    fn create_bucket(&self, bucket: &str) -> Result<()>;
    fn bucket_exists(&self, bucket: &str) -> Result<bool>;
    fn list_buckets(&self) -> Result<Vec<String>>;
    fn put_meta(&self, meta: ObjectMeta) -> Result<()>;
    fn get_meta(&self, bucket: &str, key: &str) -> Result<Option<ObjectMeta>>;
    fn delete_meta(&self, bucket: &str, key: &str) -> Result<()>;
    fn list_meta(&self, bucket: &str, prefix: &str) -> Result<Vec<ObjectMeta>>;
}

#[derive(Default)]
pub struct MemoryMetaStore {
    buckets: Mutex<HashSet<String>>,
    objects: Mutex<HashMap<(String, String), ObjectMeta>>,
}

impl MemoryMetaStore {
    pub fn new() -> Self {
        Self::default()
    }
}

impl ObjectMetaStore for MemoryMetaStore {
    fn create_bucket(&self, bucket: &str) -> Result<()> {
        self.buckets
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .insert(bucket.to_string());
        Ok(())
    }

    fn bucket_exists(&self, bucket: &str) -> Result<bool> {
        Ok(self
            .buckets
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .contains(bucket))
    }

    fn list_buckets(&self) -> Result<Vec<String>> {
        let mut out: Vec<String> = self
            .buckets
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .iter()
            .cloned()
            .collect();
        out.sort();
        Ok(out)
    }

    fn put_meta(&self, meta: ObjectMeta) -> Result<()> {
        self.objects
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .insert((meta.bucket.clone(), meta.key.clone()), meta);
        Ok(())
    }

    fn get_meta(&self, bucket: &str, key: &str) -> Result<Option<ObjectMeta>> {
        Ok(self
            .objects
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .get(&(bucket.to_string(), key.to_string()))
            .cloned())
    }

    fn delete_meta(&self, bucket: &str, key: &str) -> Result<()> {
        self.objects
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .remove(&(bucket.to_string(), key.to_string()));
        Ok(())
    }

    fn list_meta(&self, bucket: &str, prefix: &str) -> Result<Vec<ObjectMeta>> {
        let mut out: Vec<_> = self
            .objects
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .values()
            .filter(|m| m.bucket == bucket && m.key.starts_with(prefix))
            .cloned()
            .collect();
        out.sort_by(|a, b| a.key.cmp(&b.key));
        Ok(out)
    }
}

pub struct SqliteMetaStore {
    conn: Mutex<Connection>,
}

impl SqliteMetaStore {
    pub fn open(path: impl AsRef<Path>) -> Result<Self> {
        if let Some(parent) = path.as_ref().parent() {
            std::fs::create_dir_all(parent)?;
        }
        let conn = Connection::open(path).map_err(|e| AetherError::Storage(e.to_string()))?;
        conn.execute_batch(
            r#"
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS buckets (
              bucket TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS objects (
              bucket TEXT NOT NULL,
              key TEXT NOT NULL,
              etag TEXT NOT NULL,
              size INTEGER NOT NULL,
              md5_hex TEXT,
              blake3_hex TEXT,
              storage_path TEXT NOT NULL,
              PRIMARY KEY(bucket, key)
            );
            "#,
        )
        .map_err(|e| AetherError::Storage(e.to_string()))?;

        Ok(Self {
            conn: Mutex::new(conn),
        })
    }
}

impl ObjectMetaStore for SqliteMetaStore {
    fn create_bucket(&self, bucket: &str) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        conn.execute(
            "INSERT OR IGNORE INTO buckets(bucket) VALUES (?1)",
            params![bucket],
        )
        .map_err(|e| AetherError::Storage(e.to_string()))?;
        Ok(())
    }

    fn bucket_exists(&self, bucket: &str) -> Result<bool> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let mut stmt = conn
            .prepare("SELECT COUNT(*) FROM buckets WHERE bucket = ?1")
            .map_err(|e| AetherError::Storage(e.to_string()))?;
        let count: i64 = stmt
            .query_row(params![bucket], |row| row.get(0))
            .map_err(|e| AetherError::Storage(e.to_string()))?;
        Ok(count > 0)
    }

    fn list_buckets(&self) -> Result<Vec<String>> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let mut stmt = conn
            .prepare("SELECT bucket FROM buckets ORDER BY bucket ASC")
            .map_err(|e| AetherError::Storage(e.to_string()))?;
        let rows = stmt
            .query_map([], |row| row.get::<_, String>(0))
            .map_err(|e| AetherError::Storage(e.to_string()))?;
        let mut out = Vec::new();
        for row in rows {
            out.push(row.map_err(|e| AetherError::Storage(e.to_string()))?);
        }
        Ok(out)
    }

    fn put_meta(&self, meta: ObjectMeta) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        conn.execute(
            r#"
            INSERT INTO objects(bucket, key, etag, size, md5_hex, blake3_hex, storage_path)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)
            ON CONFLICT(bucket, key) DO UPDATE SET
              etag = excluded.etag,
              size = excluded.size,
              md5_hex = excluded.md5_hex,
              blake3_hex = excluded.blake3_hex,
              storage_path = excluded.storage_path
            "#,
            params![
                meta.bucket,
                meta.key,
                meta.etag,
                meta.size as i64,
                meta.md5_hex,
                meta.blake3_hex,
                meta.storage_path
            ],
        )
        .map_err(|e| AetherError::Storage(e.to_string()))?;
        Ok(())
    }

    fn get_meta(&self, bucket: &str, key: &str) -> Result<Option<ObjectMeta>> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let mut stmt = conn
            .prepare(
                "SELECT bucket, key, etag, size, md5_hex, blake3_hex, storage_path
                 FROM objects WHERE bucket = ?1 AND key = ?2",
            )
            .map_err(|e| AetherError::Storage(e.to_string()))?;
        let mut rows = stmt
            .query(params![bucket, key])
            .map_err(|e| AetherError::Storage(e.to_string()))?;
        if let Some(row) = rows
            .next()
            .map_err(|e| AetherError::Storage(e.to_string()))?
        {
            Ok(Some(ObjectMeta {
                bucket: row
                    .get(0)
                    .map_err(|e| AetherError::Storage(e.to_string()))?,
                key: row
                    .get(1)
                    .map_err(|e| AetherError::Storage(e.to_string()))?,
                etag: row
                    .get(2)
                    .map_err(|e| AetherError::Storage(e.to_string()))?,
                size: row
                    .get::<_, i64>(3)
                    .map_err(|e| AetherError::Storage(e.to_string()))? as u64,
                md5_hex: row
                    .get(4)
                    .map_err(|e| AetherError::Storage(e.to_string()))?,
                blake3_hex: row
                    .get(5)
                    .map_err(|e| AetherError::Storage(e.to_string()))?,
                storage_path: row
                    .get(6)
                    .map_err(|e| AetherError::Storage(e.to_string()))?,
            }))
        } else {
            Ok(None)
        }
    }

    fn delete_meta(&self, bucket: &str, key: &str) -> Result<()> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        conn.execute(
            "DELETE FROM objects WHERE bucket = ?1 AND key = ?2",
            params![bucket, key],
        )
        .map_err(|e| AetherError::Storage(e.to_string()))?;
        Ok(())
    }

    fn list_meta(&self, bucket: &str, prefix: &str) -> Result<Vec<ObjectMeta>> {
        let conn = self
            .conn
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let like = format!("{}%", prefix);
        let mut stmt = conn
            .prepare(
                "SELECT bucket, key, etag, size, md5_hex, blake3_hex, storage_path
                 FROM objects
                 WHERE bucket = ?1 AND key LIKE ?2
                 ORDER BY key ASC",
            )
            .map_err(|e| AetherError::Storage(e.to_string()))?;

        let rows = stmt
            .query_map(params![bucket, like], |row| {
                Ok(ObjectMeta {
                    bucket: row.get(0)?,
                    key: row.get(1)?,
                    etag: row.get(2)?,
                    size: row.get::<_, i64>(3)? as u64,
                    md5_hex: row.get(4)?,
                    blake3_hex: row.get(5)?,
                    storage_path: row.get(6)?,
                })
            })
            .map_err(|e| AetherError::Storage(e.to_string()))?;

        let mut out = Vec::new();
        for row in rows {
            out.push(row.map_err(|e| AetherError::Storage(e.to_string()))?);
        }
        Ok(out)
    }
}
