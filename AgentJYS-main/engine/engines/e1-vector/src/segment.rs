// 分段（Segment）模块：向量引擎的最小物理管理单元
//
// 分段是 E1 管理数据的最小单位（设计文档 §3.2）
// - Growing 分段：保存原始向量记录，使用暴力搜索
// - Sealed 分段：构建了索引，查询通过索引进行
//
// 生命周期：Growing -> Sealing -> Sealed -> Frozen -> Archived

use crate::flat_index::search_flat;
use crate::hnsw_index::HnswIndex;
use crate::index::{FlatIndex, VectorIndex};
use crate::ivfpq_index::IvfPqIndex;
use crate::{IndexKind, SearchHit, VectorRecord};
use ae_common::{MigrationState, SegmentId, SegmentState};

/// 分段：E1 管理向量数据的最小物理单元（设计文档 §3.2）
///
/// Growing 分段保存原始记录，查询时使用暴力搜索；
/// 达到封存阈值后构建 `VectorIndex` 并转为 Sealed 状态。
/// MVP 中索引构建是同步的；状态机已为未来的异步构建和 P3 迁移路径建模
/// （Sealing 和 Frozen 状态）。
pub(crate) struct Segment {
    pub id: SegmentId,                       // 分段唯一 ID
    pub state: SegmentState,                 // 分段状态（Growing/Sealed/Frozen 等）
    pub records: Vec<VectorRecord>,          // 原始向量记录
    pub index: Option<Box<dyn VectorIndex>>, // 索引（Sealed 后构建）
    pub access_count: u64,                   // 访问次数（热度统计）
    pub migration: MigrationState,
}

impl Segment {
    /// 创建新的 Growing 状态分段
    pub fn new_growing(id: SegmentId) -> Self {
        Self {
            id,
            state: SegmentState::Growing,
            records: Vec::new(),
            index: None,
            access_count: 0,
            migration: MigrationState::default(),
        }
    }

    /// 获取向量数量
    pub fn row_count(&self) -> usize {
        self.records.len()
    }

    /// 近似字节大小：用于封存和分层决策
    /// 仅计算向量值的大小，不包括 ID 和元数据
    pub fn size_bytes(&self) -> u64 {
        self.records
            .iter()
            .map(|r| (r.values.len() * std::mem::size_of::<f32>()) as u64)
            .sum()
    }

    /// 追加向量记录（仅 Growing 状态可写）
    pub fn push(&mut self, record: VectorRecord) {
        self.records.push(record);
    }

    /// 构建分段本地索引并转换为 Sealed 状态
    /// Growing -> Sealing -> Sealed
    pub fn seal(&mut self, kind: IndexKind) {
        if self.state != SegmentState::Growing {
            return; // 只能封存 Growing 分段
        }
        self.state = SegmentState::Sealing; // 开始构建索引
        let index: Box<dyn VectorIndex> = match kind {
            IndexKind::Flat => Box::new(FlatIndex::build(&self.records)),
            IndexKind::Hnsw => Box::new(HnswIndex::build(&self.records)),
            IndexKind::IvfPq => Box::new(IvfPqIndex::build(&self.records)),
        };
        self.index = Some(index);
        self.state = SegmentState::Sealed; // 索引构建完成
    }

    /// 获取索引类型名称（用于统计信息）
    pub fn index_kind(&self) -> Option<&'static str> {
        self.index.as_ref().map(|idx| idx.kind())
    }

    /// 本地 top-k 搜索
    /// - Sealed 分段：通过索引查询
    /// - Growing/Sealing 分段：暴力搜索原始记录
    pub fn search(&self, query: &[f32], top_k: usize) -> Vec<SearchHit> {
        match &self.index {
            Some(index) if self.state == SegmentState::Sealed => index.search(query, top_k),
            _ => search_flat(&self.records, query, top_k), // 暴力搜索回退
        }
    }
}
