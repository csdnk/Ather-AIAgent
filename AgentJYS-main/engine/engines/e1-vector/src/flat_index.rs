// Flat 索引：暴力搜索实现
// 对所有向量计算与查询的相似度，时间复杂度 O(n)
// 用于 Growing 分段的回退方案和召回率基准测试

use crate::{SearchHit, VectorRecord};

/// 暴力搜索：遍历所有记录，计算余弦相似度，返回 top-k
pub fn search_flat(records: &[VectorRecord], query: &[f32], top_k: usize) -> Vec<SearchHit> {
    let mut hits: Vec<SearchHit> = records
        .iter()
        .map(|record| SearchHit {
            id: record.id.clone(),
            score: cosine_similarity(&record.values, query),  // 计算余弦相似度
            graph_node_id: record.graph_node_id.clone(),
            metadata_json: record.metadata_json.clone(),
        })
        .collect();

    // 按相似度降序排序（分数越高越相似）
    hits.sort_by(|a, b| {
        b.score
            .partial_cmp(&a.score)
            .unwrap_or(std::cmp::Ordering::Equal)
    });
    hits.truncate(top_k);  // 截取 top-k 结果
    hits
}

/// 计算余弦相似度：cos(a, b) = (a·b) / (||a|| * ||b||)
/// 值域 [-1, 1]，越接近 1 表示越相似
fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let mut dot = 0.0;     // 点积
    let mut norm_a = 0.0;  // ||a||²
    let mut norm_b = 0.0;  // ||b||²

    for (x, y) in a.iter().zip(b.iter()) {
        dot += x * y;
        norm_a += x * x;
        norm_b += y * y;
    }

    // 处理零向量情况
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    dot / (norm_a.sqrt() * norm_b.sqrt())
}
