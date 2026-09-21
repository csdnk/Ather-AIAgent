// 索引抽象：定义分段本地索引的统一接口
// 
// 每个 Sealed 分段拥有一个 `VectorIndex`
// MVP 提供 `FlatIndex`（精确暴力搜索）用于验证完整的分段生命周期和查询扇出；
// HNSW / IVF-PQ 实现在此 trait 后面插入，无需修改公共 `VectorEngine` API（设计文档 §3.3）

use crate::flat_index::search_flat;
use crate::{SearchHit, VectorRecord};

/// 分段本地索引抽象
///
/// 每个 Sealed 分段拥有一个 `VectorIndex`。MVP 提供 `FlatIndex`
/// （精确暴力搜索），用于验证完整的分段生命周期和查询扇出；
/// HNSW / IVF-PQ 实现在此 trait 后面插入，无需修改公共 `VectorEngine` API（设计文档 §3.3）。
pub trait VectorIndex: Send + Sync {
    /// 返回查询向量的本地 top-k 匹配结果
    fn search(&self, query: &[f32], top_k: usize) -> Vec<SearchHit>;

    /// 稳定的索引类型标签，通过 SegmentStats 暴露给 P3 层
    fn kind(&self) -> &'static str;
}

/// 精确暴力搜索索引
/// 查询时间复杂度 O(n)，用于 Growing 分段回退和 MVP Sealed 索引
pub struct FlatIndex {
    records: Vec<VectorRecord>,  // 保存所有向量记录
}

impl FlatIndex {
    /// 构建 FlatIndex：直接复制所有记录
    pub fn build(records: &[VectorRecord]) -> Self {
        Self {
            records: records.to_vec(),
        }
    }
}

impl VectorIndex for FlatIndex {
    fn search(&self, query: &[f32], top_k: usize) -> Vec<SearchHit> {
        search_flat(&self.records, query, top_k)  // 暴力搜索
    }

    fn kind(&self) -> &'static str {
        "flat"
    }
}
