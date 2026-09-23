// E1 向量引擎：提供高维向量的存储和相似度搜索功能
// 主要用于 AI 检索、语义搜索等场景

mod flat_index; // 暴力搜索索引（精确但慢，用于基准测试）
pub mod hnsw_index; // 分层可导航小世界图索引（快速近似搜索）
mod index; // 索引抽象 trait
pub mod ivfpq_index; // 倒排索引+乘积量化（大规模数据压缩）
mod segment; // 数据分段管理

use ae_common::{
    AetherError, BlockId, CompactPolicy, CompactReport, EngineInstanceId, EngineKind,
    MigrationState, MigrationTaskId, NamespaceId, OperationKind, RecoveryReport, Result, SegmentId,
    SegmentState, SnapshotId,
};
use ae_kernel::{AetherEngine, MigrationAck, SegmentControl, SegmentMigrationStatus};
use ae_wal::{SharedWal, WalPayload, WalRecord};
use segment::Segment;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::sync::RwLock;

/// 默认封存阈值：当一个分段达到 10,000 行时，触发 Growing -> Sealed 转换
/// 触发后会构建索引，提升查询性能
pub const DEFAULT_SEAL_THRESHOLD_ROWS: usize = 10_000;

/// 索引类型：决定 Sealed 分段使用哪种索引策略
/// - Flat: 暴力搜索，精确但慢，用于小集合和测试
/// - Hnsw: 分层可导航小世界图，主力索引，快速近似搜索
/// - IvfPq: 倒排索引+乘积量化，大规模数据压缩
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IndexKind {
    Flat,  // 暴力搜索
    Hnsw,  // HNSW 图索引
    IvfPq, // IVF-PQ 压缩索引
}

/// 集合规格：定义一个向量集合的名称和维度
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CollectionSpec {
    pub name: String,     // 集合名称
    pub dimension: usize, // 向量维度（所有向量必须是相同维度）
}

/// 分段统计信息：暴露给 P3 层的分段状态（设计文档 §2.2.2 SegmentStats）
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SegmentStat {
    pub segment_id: String,         // 分段 ID
    pub state: SegmentState,        // 分段状态（Growing/Sealed/Frozen 等）
    pub row_count: usize,           // 向量数量
    pub size_bytes: u64,            // 占用字节数
    pub index_type: Option<String>, // 索引类型（如 "flat", "hnsw"）
    pub access_count: u64,          // 访问次数（热度统计）
}

/// 向量记录：存储的单条向量数据
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VectorRecord {
    pub id: String,                    // 向量 ID（唯一标识）
    pub values: Vec<f32>,              // 向量值（浮点数数组）
    pub graph_node_id: Option<String>, // 关联的图节点 ID（用于 Fusion 查询）
    pub metadata_json: Option<String>, // 元数据（JSON 格式）
}

/// 搜索结果：查询返回的单条匹配结果
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchHit {
    pub id: String,                    // 向量 ID
    pub score: f32,                    // 相似度分数（余弦相似度，越大越相似）
    pub graph_node_id: Option<String>, // 关联的图节点 ID
    pub metadata_json: Option<String>, // 写入向量时携带的元数据
}

/// WAL 操作类型：记录到预写日志的操作，用于崩溃恢复
#[derive(Debug, Clone, Serialize, Deserialize)]
enum VectorWalOp {
    CreateCollection(CollectionSpec), // 创建集合
    Insert {
        // 插入向量
        collection: String,
        records: Vec<VectorRecord>,
    },
    Delete {
        // 删除向量（软删除）
        collection: String,
        ids: Vec<String>,
    },
    SetMigrationState {
        segment_id: SegmentId,
        migration: MigrationState,
    },
}

/// 集合状态：管理单个集合的所有数据和状态
struct CollectionState {
    spec: CollectionSpec,   // 集合规格（名称、维度）
    segments: Vec<Segment>, // 所有分段（包括 Growing 和 Sealed）
    next_segment_seq: u64,  // 下一个分段的序号
    /// 删除位图：按向量 ID 标记已删除的向量（设计文档 §3.3 删除处理）
    /// 删除操作不修改索引结构，查询时过滤掉墓碑（tombstone）标记的结果
    deleted: std::collections::HashSet<String>,
}

impl CollectionState {
    /// 创建新的集合状态，自动创建第一个 Growing 分段
    fn new(spec: CollectionSpec) -> Self {
        let mut state = Self {
            spec,
            segments: Vec::new(),
            next_segment_seq: 0,
            deleted: std::collections::HashSet::new(),
        };
        state.push_growing_segment();
        state
    }

    /// 添加一个新的 Growing 分段
    /// 分段 ID 按集合命名空间组织，确保引擎范围内唯一
    /// （P3 层通过这个 ID 进行 freeze/migrate 操作，无歧义）
    fn push_growing_segment(&mut self) {
        let seq = self.next_segment_seq;
        self.next_segment_seq += 1;
        // 格式：{集合名}/seg_{序号}，例如："my_collection/seg_000001"
        let id = SegmentId::new(format!("{}/seg_{seq:06}", self.spec.name));
        self.segments.push(Segment::new_growing(id));
    }

    /// 获取当前的 Growing 分段，如果尾部分段已封存则创建新的
    /// 始终返回一个可写的分段
    fn growing_segment(&mut self) -> &mut Segment {
        let needs_new = self
            .segments
            .last()
            .map(|s| !s.state.accepts_writes()) // 检查最后一个分段是否可写
            .unwrap_or(true); // 如果没有分段，也需要创建
        if needs_new {
            self.push_growing_segment();
        }
        self.segments.last_mut().expect("segment present")
    }
}

/// 内存向量引擎：E1 的核心实现
/// 管理多个向量集合，每个集合包含多个分段
pub struct InMemoryVectorEngine {
    id: EngineInstanceId,                                  // 引擎实例 ID
    namespace_id: NamespaceId,                             // 命名空间 ID
    wal: SharedWal,                                        // 预写日志（用于崩溃恢复）
    collections: RwLock<HashMap<String, CollectionState>>, // 所有集合的状态（线程安全）
    seal_threshold_rows: usize,                            // 封存阈值（达到多少行触发封存）
    index_kind: IndexKind,                                 // 使用的索引类型
}

impl InMemoryVectorEngine {
    /// 创建新的向量引擎实例（使用默认配置：10k 封存阈值，HNSW 索引）
    pub fn new(instance: impl Into<String>, wal: SharedWal) -> Self {
        Self::with_config(instance, wal, DEFAULT_SEAL_THRESHOLD_ROWS, IndexKind::Hnsw)
    }

    /// 创建向量引擎实例，指定封存阈值（使用 HNSW 索引）
    pub fn with_seal_threshold(
        instance: impl Into<String>,
        wal: SharedWal,
        seal_threshold_rows: usize,
    ) -> Self {
        Self::with_config(instance, wal, seal_threshold_rows, IndexKind::Hnsw)
    }

    /// 创建向量引擎实例，完全自定义配置
    pub fn with_config(
        instance: impl Into<String>,
        wal: SharedWal,
        seal_threshold_rows: usize,
        index_kind: IndexKind,
    ) -> Self {
        Self {
            id: EngineInstanceId::new(EngineKind::Vector, instance),
            namespace_id: NamespaceId::default_for_engine(EngineKind::Vector),
            wal,
            collections: RwLock::new(HashMap::new()),
            seal_threshold_rows: seal_threshold_rows.max(1), // 至少为 1
            index_kind,
        }
    }

    /// 创建向量集合：定义名称和维度
    pub fn create_collection(&self, spec: CollectionSpec) -> Result<()> {
        let op = VectorWalOp::CreateCollection(spec.clone());
        self.append_op(OperationKind::CreateNamespace, &op)?; // 先写 WAL
        self.apply_op(op) // 再应用到内存
    }

    /// 插入向量：向指定集合插入一批向量记录
    /// 返回成功插入的向量数量
    pub fn insert(&self, collection: &str, records: Vec<VectorRecord>) -> Result<usize> {
        let _span = tracing::info_span!("ae.vector.insert", engine = "vector").entered();
        if records.is_empty() {
            return Ok(0);
        }
        let op = VectorWalOp::Insert {
            collection: collection.to_string(),
            records,
        };
        let inserted = match &op {
            VectorWalOp::Insert { records, .. } => records.len(),
            _ => 0,
        };
        self.append_op(OperationKind::Insert, &op)?; // 先写 WAL（持久化）
        self.apply_op(op)?; // 再写内存（应用操作）
        Ok(inserted)
    }

    /// 删除向量：通过 ID 软删除（tombstone 墓碑机制）
    /// 索引结构不会被修改，ID 被加入集合的删除位图
    /// 查询时会过滤掉这些墓碑标记的结果（设计文档 §3.3 / §3.4.2 step 5）
    pub fn delete(&self, collection: &str, ids: Vec<String>) -> Result<usize> {
        let _span = tracing::info_span!("ae.vector.delete", engine = "vector").entered();
        if ids.is_empty() {
            return Ok(0);
        }
        let deleted = ids.len();
        let op = VectorWalOp::Delete {
            collection: collection.to_string(),
            ids,
        };
        self.append_op(OperationKind::Delete, &op)?; // 先写 WAL
        self.apply_op(op)?; // 再应用删除
        Ok(deleted)
    }

    /// 向量搜索：在指定集合中查找与查询向量最相似的 top_k 个结果
    ///
    /// 搜索流程：
    /// 1. 扇出到所有可查询的分段
    /// 2. 每个分段返回本地 top-k 结果
    /// 3. 全局合并所有分段的结果
    /// 4. 过滤墓碑标记的向量（已删除）
    /// 5. 按相似度排序，返回最终 top-k
    pub fn search(&self, collection: &str, query: &[f32], top_k: usize) -> Result<Vec<SearchHit>> {
        let _span = tracing::info_span!("ae.vector.search", engine = "vector").entered();
        if top_k == 0 {
            return Ok(Vec::new());
        }
        let mut guard = self
            .collections
            .write()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let state = guard
            .get_mut(collection)
            .ok_or_else(|| AetherError::NotFound(format!("collection {collection}")))?;
        if query.len() != state.spec.dimension {
            return Err(AetherError::InvalidArgument(format!(
                "query dimension {} != collection dimension {}",
                query.len(),
                state.spec.dimension
            )));
        }

        // 扇出到每个可查询的分段，然后全局合并 top-k
        // 每个分段多取一些结果，防止墓碑过滤后结果不足
        let local_k = top_k + state.deleted.len().min(top_k);
        let mut merged: Vec<SearchHit> = Vec::new();
        for seg in state.segments.iter_mut() {
            if !seg.state.is_queryable() {
                // 跳过不可查询的分段（如已归档）
                continue;
            }
            seg.access_count += 1; // 更新访问计数（用于热度统计）
            merged.extend(seg.search(query, local_k));
        }
        // 删除位图过滤：移除所有墓碑标记的向量（设计文档 §3.4.2 step 5）
        merged.retain(|hit| !state.deleted.contains(&hit.id));
        // 按相似度降序排序（分数越高越相似）
        merged.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        merged.truncate(top_k); // 截取 top-k 结果
        Ok(merged)
    }

    /// 强制封存所有 Growing 分段：构建索引并转换为 Sealed 状态
    /// 用于手动刷新、迁移前准备、召回率基准测试
    pub fn seal_all(&self, collection: &str) -> Result<usize> {
        let index_kind = self.index_kind;
        let mut guard = self
            .collections
            .write()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let state = guard
            .get_mut(collection)
            .ok_or_else(|| AetherError::NotFound(format!("collection {collection}")))?;
        let mut sealed = 0;
        for seg in state.segments.iter_mut() {
            if seg.state == SegmentState::Growing && seg.row_count() > 0 {
                seg.seal(index_kind); // 构建索引并转换状态
                sealed += 1;
            }
        }
        Ok(sealed)
    }

    /// 分段统计信息：供 P3 层查询分段状态（设计文档 §2.2.2 SegmentStats）
    pub fn segment_stats(&self, collection: &str) -> Result<Vec<SegmentStat>> {
        let guard = self
            .collections
            .read()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let state = guard
            .get(collection)
            .ok_or_else(|| AetherError::NotFound(format!("collection {collection}")))?;
        Ok(state
            .segments
            .iter()
            .map(|seg| SegmentStat {
                segment_id: seg.id.0.clone(),
                state: seg.state,
                row_count: seg.row_count(),
                size_bytes: seg.size_bytes(),
                index_type: seg.index_kind().map(|k| k.to_string()),
                access_count: seg.access_count,
            })
            .collect())
    }

    /// 追加操作到 WAL：先序列化操作，写入 WAL，再刷盘
    /// 这是崩溃恢复的关键：所有操作先写日志，再修改内存
    fn append_op(&self, operation: OperationKind, op: &VectorWalOp) -> Result<()> {
        let payload = serde_json::to_vec(op)?; // 序列化为 JSON
        let record = WalRecord::new(
            EngineKind::Vector,
            self.id.clone(),
            self.namespace_id.clone(),
            operation,
            WalPayload::Inline(payload),
        );
        self.wal.append(record)?; // 写入 WAL
        self.wal.flush()?; // 强制刷盘（确保持久化）
        Ok(())
    }

    /// 应用操作到内存：根据操作类型修改内存中的数据结构
    /// 这个函数在两个场景下被调用：
    /// 1. 正常写入：append_op 写完 WAL 后调用
    /// 2. 崩溃恢复：recover 从 WAL 重放时调用
    fn apply_op(&self, op: VectorWalOp) -> Result<()> {
        match op {
            // 创建集合：如果集合不存在则创建
            VectorWalOp::CreateCollection(spec) => {
                let mut guard = self
                    .collections
                    .write()
                    .map_err(|e| AetherError::Internal(e.to_string()))?;
                guard
                    .entry(spec.name.clone())
                    .or_insert_with(|| CollectionState::new(spec));
                Ok(())
            }
            // 插入向量：追加到当前 Growing 分段，达到阈值时自动封存
            VectorWalOp::Insert {
                collection,
                records,
            } => {
                let threshold = self.seal_threshold_rows;
                let index_kind = self.index_kind;
                let mut guard = self
                    .collections
                    .write()
                    .map_err(|e| AetherError::Internal(e.to_string()))?;
                let state = guard
                    .get_mut(&collection)
                    .ok_or_else(|| AetherError::NotFound(format!("collection {collection}")))?;

                let dimension = state.spec.dimension;
                for record in records {
                    // 验证向量维度
                    if record.values.len() != dimension {
                        return Err(AetherError::InvalidArgument(format!(
                            "vector {} dimension {} != collection dimension {}",
                            record.id,
                            record.values.len(),
                            dimension
                        )));
                    }
                    let seg = state.growing_segment(); // 获取当前可写分段
                    seg.push(record); // 追加向量
                    if seg.row_count() >= threshold {
                        // 达到阈值？
                        seg.seal(index_kind); // 封存并构建索引
                    }
                }
                Ok(())
            }
            // 删除向量：加入删除位图（墓碑标记）
            VectorWalOp::Delete { collection, ids } => {
                let mut guard = self
                    .collections
                    .write()
                    .map_err(|e| AetherError::Internal(e.to_string()))?;
                let state = guard
                    .get_mut(&collection)
                    .ok_or_else(|| AetherError::NotFound(format!("collection {collection}")))?;
                state.deleted.extend(ids); // 标记为已删除
                Ok(())
            }
            VectorWalOp::SetMigrationState {
                segment_id,
                migration,
            } => self.with_segment(&segment_id, |seg| {
                seg.state = if migration.active_migration_id.is_some() {
                    SegmentState::Frozen
                } else {
                    SegmentState::Sealed
                };
                seg.migration = migration;
                Ok(())
            }),
        }
    }

    /// 应用单条 WAL 记录：过滤掉不属于本引擎的记录
    /// 返回 Ok(true) 表示已应用，Ok(false) 表示跳过
    fn apply_record(&self, record: &WalRecord) -> Result<bool> {
        // 只处理属于本向量引擎实例的记录
        if record.engine != EngineKind::Vector || record.instance_id != self.id {
            return Ok(false);
        }
        match &record.payload {
            WalPayload::Inline(bytes) => {
                let op: VectorWalOp = serde_json::from_slice(bytes)?;
                self.apply_op(op)?;
                Ok(true)
            }
            WalPayload::BlockRef { .. } => Err(AetherError::Unsupported(
                "vector BlockRef replay requires P1 block client".into(),
            )),
        }
    }
}

// 实现 AetherEngine trait：引擎生命周期管理
impl AetherEngine for InMemoryVectorEngine {
    fn instance_id(&self) -> &EngineInstanceId {
        &self.id
    }

    /// 打开引擎：向量引擎是纯内存的，无需额外初始化
    fn open(&self) -> Result<()> {
        Ok(())
    }

    /// 崩溃恢复：从 WAL 重放所有操作，重建内存状态
    /// 这是 WAL 机制的核心：先清空内存，再从日志重建
    fn recover(&self) -> Result<RecoveryReport> {
        self.collections
            .write()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .clear(); // 清空所有内存状态

        let mut replayed = 0; // 已重放的记录数
        let mut skipped = 0; // 跳过的记录数（不属于本引擎）
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

    /// 创建快照：生成一个快照 ID（当前为占位实现）
    fn snapshot(&self) -> Result<SnapshotId> {
        Ok(SnapshotId::random())
    }

    /// 压缩：合并小分段，回收空间（当前为占位实现）
    fn compact(&self, _policy: CompactPolicy) -> Result<CompactReport> {
        Ok(CompactReport {
            compacted_segments: 0,
            reclaimed_bytes: 0,
        })
    }
}

// 实现 SegmentControl trait：分段控制和迁移管理
impl SegmentControl for InMemoryVectorEngine {
    /// 列出所有分段 ID
    fn list_segments(&self) -> Result<Vec<SegmentId>> {
        let guard = self
            .collections
            .read()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        Ok(guard
            .values()
            .flat_map(|c| c.segments.iter().map(|s| s.id.clone()))
            .collect())
    }

    fn migration_status(&self, segment: &SegmentId) -> Result<SegmentMigrationStatus> {
        self.with_segment(segment, |seg| {
            Ok(SegmentMigrationStatus {
                segment_id: seg.id.clone(),
                state: seg.state.as_str().to_string(),
                migration: seg.migration.clone(),
            })
        })
    }

    fn prepare_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<MigrationAck> {
        let _span = tracing::info_span!("ae.segment.freeze", engine = "vector").entered();
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
        let _span = tracing::info_span!("ae.segment.migrate_complete", engine = "vector").entered();
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

impl InMemoryVectorEngine {
    fn transition_migration(
        &self,
        segment: &SegmentId,
        operation: OperationKind,
        transition: impl FnOnce(&mut MigrationState) -> Result<bool>,
    ) -> Result<MigrationAck> {
        self.with_segment(segment, |seg| {
            let mut migration = seg.migration.clone();
            let idempotent = transition(&mut migration)?;

            if !idempotent {
                if operation == OperationKind::MigrationPrepare && seg.state != SegmentState::Sealed
                {
                    return Err(AetherError::Conflict(format!(
                        "segment {} is {:?}, only Sealed segments can be frozen",
                        seg.id.0, seg.state
                    )));
                }
                let op = VectorWalOp::SetMigrationState {
                    segment_id: seg.id.clone(),
                    migration: migration.clone(),
                };
                self.append_op(operation, &op)?;
                seg.state = if migration.active_migration_id.is_some() {
                    SegmentState::Frozen
                } else {
                    SegmentState::Sealed
                };
                seg.migration = migration.clone();
            }

            Ok(MigrationAck::new(
                seg.id.clone(),
                seg.state.as_str(),
                migration,
                idempotent,
            ))
        })
    }

    /// 在所有集合中查找指定 ID 的分段，并对其执行操作 `f`
    /// 这是分段控制的辅助函数
    fn with_segment<R>(
        &self,
        id: &SegmentId,
        f: impl FnOnce(&mut Segment) -> Result<R>,
    ) -> Result<R> {
        let mut guard = self
            .collections
            .write()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        for collection in guard.values_mut() {
            if let Some(seg) = collection.segments.iter_mut().find(|s| &s.id == id) {
                return f(seg); // 找到分段，执行操作
            }
        }
        Err(AetherError::NotFound(format!("segment {}", id.0)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ae_wal::MemoryWal;
    use std::collections::HashSet;
    use std::sync::Arc;

    #[test]
    fn vector_recover_then_search() {
        let wal = Arc::new(MemoryWal::new());
        let engine = InMemoryVectorEngine::new("default", wal.clone());

        engine
            .create_collection(CollectionSpec {
                name: "c1".into(),
                dimension: 3,
            })
            .unwrap();
        engine
            .insert(
                "c1",
                vec![
                    VectorRecord {
                        id: "v1".into(),
                        values: vec![1.0, 0.0, 0.0],
                        graph_node_id: Some("n1".into()),
                        metadata_json: Some(r#"{"tenant_id":"t1"}"#.into()),
                    },
                    VectorRecord {
                        id: "v2".into(),
                        values: vec![0.0, 1.0, 0.0],
                        graph_node_id: Some("n2".into()),
                        metadata_json: None,
                    },
                ],
            )
            .unwrap();

        let recovered = InMemoryVectorEngine::new("default", wal);
        recovered.recover().unwrap();
        let hits = recovered.search("c1", &[1.0, 0.0, 0.0], 1).unwrap();
        assert_eq!(hits[0].id, "v1");
        assert_eq!(hits[0].graph_node_id.as_deref(), Some("n1"));
        assert_eq!(
            hits[0].metadata_json.as_deref(),
            Some(r#"{"tenant_id":"t1"}"#)
        );
    }

    #[test]
    fn seals_segments_and_searches_across_them() {
        let wal = Arc::new(MemoryWal::new());
        // Seal every 4 rows so we exercise the Growing -> Sealed transition.
        // Flat index keeps the cross-segment merge result exact.
        let engine = InMemoryVectorEngine::with_config("default", wal, 4, IndexKind::Flat);
        engine
            .create_collection(CollectionSpec {
                name: "c1".into(),
                dimension: 2,
            })
            .unwrap();

        let mut records = Vec::new();
        for i in 0..10 {
            let angle = i as f32;
            records.push(VectorRecord {
                id: format!("v{i}"),
                values: vec![angle.cos(), angle.sin()],
                graph_node_id: None,
                metadata_json: None,
            });
        }
        engine.insert("c1", records).unwrap();

        let stats = engine.segment_stats("c1").unwrap();
        // 10 rows / 4 per seal => 2 sealed segments + 1 growing tail.
        let sealed = stats
            .iter()
            .filter(|s| s.state == SegmentState::Sealed)
            .count();
        let growing = stats
            .iter()
            .filter(|s| s.state == SegmentState::Growing)
            .count();
        assert_eq!(sealed, 2);
        assert_eq!(growing, 1);
        assert!(stats
            .iter()
            .any(|s| s.index_type.as_deref() == Some("flat")));

        // Global top-k merge must still find the exact nearest vector (v0).
        let hits = engine.search("c1", &[1.0, 0.0], 1).unwrap();
        assert_eq!(hits[0].id, "v0");
    }

    fn pseudo_vector(seed: u64, dim: usize) -> Vec<f32> {
        let mut x = seed | 1;
        (0..dim)
            .map(|_| {
                x ^= x >> 12;
                x ^= x << 25;
                x ^= x >> 27;
                let r = (x.wrapping_mul(0x2545F4914F6CDD1D) >> 11) as f32 / (1u64 << 53) as f32;
                r - 0.5
            })
            .collect()
    }

    #[test]
    fn hnsw_recall_matches_bruteforce() {
        let dim = 16;
        let n = 600;
        let wal_h = Arc::new(MemoryWal::new());
        let hnsw = InMemoryVectorEngine::with_config("h", wal_h, 100_000, IndexKind::Hnsw);
        let wal_f = Arc::new(MemoryWal::new());
        let flat = InMemoryVectorEngine::with_config("f", wal_f, 100_000, IndexKind::Flat);

        for eng in [&hnsw, &flat] {
            eng.create_collection(CollectionSpec {
                name: "c".into(),
                dimension: dim,
            })
            .unwrap();
        }

        let mut records = Vec::new();
        for i in 0..n {
            records.push(VectorRecord {
                id: format!("v{i}"),
                values: pseudo_vector(i as u64 + 7, dim),
                graph_node_id: None,
                metadata_json: None,
            });
        }
        hnsw.insert("c", records.clone()).unwrap();
        flat.insert("c", records).unwrap();
        // Force a single sealed HNSW segment for the whole set.
        hnsw.seal_all("c").unwrap();

        let top_k = 10;
        let queries = 20;
        let mut hit = 0usize;
        let mut total = 0usize;
        for q in 0..queries {
            let query = pseudo_vector(1_000_000 + q, dim);
            let truth: HashSet<String> = flat
                .search("c", &query, top_k)
                .unwrap()
                .into_iter()
                .map(|h| h.id)
                .collect();
            let got = hnsw.search("c", &query, top_k).unwrap();
            for h in got {
                if truth.contains(&h.id) {
                    hit += 1;
                }
            }
            total += truth.len();
        }
        let recall = hit as f64 / total as f64;
        assert!(recall >= 0.8, "recall@{top_k} too low: {recall}");
    }

    #[test]
    fn ivfpq_index_seals_and_searches() {
        let wal = Arc::new(MemoryWal::new());
        let engine = InMemoryVectorEngine::with_config("ivf", wal, 100_000, IndexKind::IvfPq);
        engine
            .create_collection(CollectionSpec {
                name: "c".into(),
                dimension: 8,
            })
            .unwrap();

        let mut records = Vec::new();
        for i in 0..200 {
            records.push(VectorRecord {
                id: format!("v{i}"),
                values: pseudo_vector(i as u64 + 3, 8),
                graph_node_id: None,
                metadata_json: None,
            });
        }
        // Make one vector an exact match target.
        let target = vec![0.5, 0.5, 0.5, 0.5, -0.5, -0.5, -0.5, -0.5];
        records.push(VectorRecord {
            id: "target".into(),
            values: target.clone(),
            graph_node_id: Some("nt".into()),
            metadata_json: None,
        });
        engine.insert("c", records).unwrap();
        engine.seal_all("c").unwrap();

        let stats = engine.segment_stats("c").unwrap();
        assert!(stats
            .iter()
            .any(|s| s.index_type.as_deref() == Some("ivfpq")));

        let hits = engine.search("c", &target, 1).unwrap();
        assert_eq!(hits[0].id, "target");
        assert_eq!(hits[0].graph_node_id.as_deref(), Some("nt"));
    }

    #[test]
    fn delete_tombstones_are_filtered_and_recover() {
        let wal = Arc::new(MemoryWal::new());
        let engine = InMemoryVectorEngine::with_config("d", wal.clone(), 100_000, IndexKind::Flat);
        engine
            .create_collection(CollectionSpec {
                name: "c".into(),
                dimension: 2,
            })
            .unwrap();
        engine
            .insert(
                "c",
                vec![
                    VectorRecord {
                        id: "v0".into(),
                        values: vec![1.0, 0.0],
                        graph_node_id: None,
                        metadata_json: None,
                    },
                    VectorRecord {
                        id: "v1".into(),
                        values: vec![0.99, 0.01],
                        graph_node_id: None,
                        metadata_json: None,
                    },
                ],
            )
            .unwrap();

        // Before delete, nearest to [1,0] is v0.
        assert_eq!(engine.search("c", &[1.0, 0.0], 1).unwrap()[0].id, "v0");
        // After tombstoning v0, the next-best (v1) is returned.
        engine.delete("c", vec!["v0".into()]).unwrap();
        let hits = engine.search("c", &[1.0, 0.0], 2).unwrap();
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].id, "v1");

        // Tombstones survive a WAL replay.
        let recovered = InMemoryVectorEngine::with_config("d", wal, 100_000, IndexKind::Flat);
        recovered.recover().unwrap();
        let hits = recovered.search("c", &[1.0, 0.0], 2).unwrap();
        assert!(hits.iter().all(|h| h.id != "v0"));
    }

    #[test]
    fn segment_freeze_migrate_lifecycle() {
        let wal = Arc::new(MemoryWal::new());
        let engine = InMemoryVectorEngine::with_config("m", wal, 100_000, IndexKind::Flat);
        engine
            .create_collection(CollectionSpec {
                name: "c".into(),
                dimension: 2,
            })
            .unwrap();
        engine
            .insert(
                "c",
                vec![VectorRecord {
                    id: "v0".into(),
                    values: vec![1.0, 0.0],
                    graph_node_id: None,
                    metadata_json: None,
                }],
            )
            .unwrap();
        engine.seal_all("c").unwrap();

        let segs = engine.list_segments().unwrap();
        let sealed = segs
            .iter()
            .find(|s| s.0.ends_with("seg_000000"))
            .cloned()
            .unwrap();

        // Cannot migrate-complete a segment that was never frozen.
        assert!(engine.on_migrate_complete(&sealed, vec![]).is_err());

        // Freeze (Sealed -> Frozen); double freeze conflicts. Frozen segments
        // stay read-only-queryable (design doc §3.6: reads continue while fenced).
        engine.freeze(&sealed).unwrap();
        assert!(engine.freeze(&sealed).is_err());
        assert_eq!(engine.search("c", &[1.0, 0.0], 1).unwrap()[0].id, "v0");

        // Migrate complete swaps the route and makes it queryable again.
        engine
            .on_migrate_complete(&sealed, vec![BlockId("blk-1".into())])
            .unwrap();
        let stat = engine
            .segment_stats("c")
            .unwrap()
            .into_iter()
            .find(|s| s.segment_id == sealed.0)
            .unwrap();
        assert_eq!(stat.state, SegmentState::Sealed);
        assert_eq!(engine.search("c", &[1.0, 0.0], 1).unwrap()[0].id, "v0");

        // Migrate failure path: freeze again, fail, route untouched, unfrozen.
        engine.freeze(&sealed).unwrap();
        engine.on_migrate_failed(&sealed, "io error").unwrap();
        assert_eq!(engine.search("c", &[1.0, 0.0], 1).unwrap()[0].id, "v0");

        assert!(engine.freeze(&SegmentId::new("c/seg_999999")).is_err());
    }

    #[test]
    fn migration_state_recovers_with_route_epoch() {
        let wal = Arc::new(MemoryWal::new());
        let engine = InMemoryVectorEngine::with_config("r", wal.clone(), 1, IndexKind::Flat);
        engine
            .create_collection(CollectionSpec {
                name: "c".into(),
                dimension: 2,
            })
            .unwrap();
        engine
            .insert(
                "c",
                vec![VectorRecord {
                    id: "v0".into(),
                    values: vec![1.0, 0.0],
                    graph_node_id: None,
                    metadata_json: None,
                }],
            )
            .unwrap();
        let segment = SegmentId::new("c/seg_000000");
        let migration = MigrationTaskId::new("vector-restart");
        engine
            .prepare_migration(&segment, &migration, Some(0))
            .unwrap();

        let recovered = InMemoryVectorEngine::with_config("r", wal.clone(), 1, IndexKind::Flat);
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
                vec![BlockId("vector-block".into())],
            )
            .unwrap();
        let restarted = InMemoryVectorEngine::with_config("r", wal, 1, IndexKind::Flat);
        restarted.recover().unwrap();
        let completed = restarted.migration_status(&segment).unwrap();
        assert_eq!(completed.state, "sealed");
        assert_eq!(completed.migration.route_epoch, 1);
        assert_eq!(completed.migration.block_ids[0].0, "vector-block");
    }
}
