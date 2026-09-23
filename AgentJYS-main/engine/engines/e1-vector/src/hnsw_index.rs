//! 纯 Rust HNSW 索引实现（基于 `VectorIndex` trait）
//!
//! 替代了早期的占位实现。保持存储核心无需外部 ANN/FFI 依赖，
//! 同时提供真正的近似索引（分层可导航小世界图，Malkov & Yashunin 2016）。
//! 公共 `VectorEngine` API 不变；封存分段时构建此索引。
//!
//! **余弦度量**：向量在构建时进行 L2 归一化，因此内积等于余弦相似度。
//! 内部使用距离 = 1 - cosine（越小越接近）排序。使用小型种子 RNG 使层级分配可重现。

use crate::index::VectorIndex;
use crate::{SearchHit, VectorRecord};
use std::cmp::Ordering;
use std::collections::{BinaryHeap, HashSet};

/// HNSW 参数配置
#[derive(Clone, Copy)]
pub struct HnswParams {
    pub m: usize,               // 每层的最大邻居数（除底层外）
    pub m_max0: usize,          // 底层（layer 0）的最大邻居数
    pub ef_construction: usize, // 构建时的搜索宽度（越大越精确但越慢）
    pub ef_search: usize,       // 查询时的搜索宽度（越大召回率越高但越慢）
    pub seed: u64,              // 随机数种子（用于层级分配）
}

impl Default for HnswParams {
    fn default() -> Self {
        Self {
            m: 16,                    // 标准配置
            m_max0: 32,               // 底层邻居数是普通层的 2 倍
            ef_construction: 200,     // 构建时搜索较宽
            ef_search: 64,            // 查询时平衡速度和召回率
            seed: 0x9E3779B97F4A7C15, // 固定种子保证可重现
        }
    }
}

/// 节点：存储向量和多层邻接表
struct Node {
    id: String,                    // 向量 ID
    graph_node_id: Option<String>, // 关联的图节点 ID
    metadata_json: Option<String>, // 向量元数据
    vector: Vec<f32>,              // 归一化后的向量
    neighbors: Vec<Vec<usize>>,    // 每层的邻居列表
}

/// HNSW 索引结构
pub struct HnswIndex {
    nodes: Vec<Node>,     // 所有节点
    entry: Option<usize>, // 入口节点（最高层的节点）
    max_layer: usize,     // 当前最大层数
    params: HnswParams,   // 索引参数
}

/// 候选节点：用于搜索过程中的优先队列
/// (distance, node) 按距离排序，使用 total_cmp 保证 f32 可以放入堆中
#[derive(Clone, Copy)]
struct Cand {
    dist: f32, // 距离（越小越接近）
    id: usize, // 节点索引
}

impl PartialEq for Cand {
    fn eq(&self, other: &Self) -> bool {
        self.dist == other.dist && self.id == other.id
    }
}
impl Eq for Cand {}
impl Ord for Cand {
    fn cmp(&self, other: &Self) -> Ordering {
        self.dist
            .total_cmp(&other.dist)
            .then(self.id.cmp(&other.id))
    }
}
impl PartialOrd for Cand {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

/// 简单的 xorshift64* 随机数生成器
struct Rng(u64);
impl Rng {
    fn next_u64(&mut self) -> u64 {
        // xorshift64*
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545F4914F6CDD1D)
    }
    /// 生成 (0, 1] 范围内的浮点数
    fn next_unit(&mut self) -> f64 {
        ((self.next_u64() >> 11) as f64 + 1.0) / (1u64 << 53) as f64
    }
}

/// L2 归一化：使向量长度为 1
/// 归一化后的向量，内积 = 余弦相似度
fn normalize(v: &[f32]) -> Vec<f32> {
    let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm == 0.0 {
        return v.to_vec();
    }
    v.iter().map(|x| x / norm).collect()
}

/// 余弦距离：distance = 1 - cosine_similarity
/// a, b 已归一化，因此 dot(a,b) = cosine(a,b)
fn cosine_dist(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    1.0 - dot
}

impl HnswIndex {
    /// 使用默认参数构建 HNSW 索引
    pub fn build(records: &[VectorRecord]) -> Self {
        Self::build_with(records, HnswParams::default())
    }

    /// 使用自定义参数构建 HNSW 索引
    pub fn build_with(records: &[VectorRecord], params: HnswParams) -> Self {
        let mut index = Self {
            nodes: Vec::with_capacity(records.len()),
            entry: None,
            max_layer: 0,
            params,
        };
        let mut rng = Rng(params.seed | 1);
        // 层级分配因子：ml = 1 / ln(M)
        let ml = 1.0 / (params.m as f64).max(2.0).ln();
        for record in records {
            // 指数分布：层级越高，节点越少（小世界结构）
            let level = (-rng.next_unit().ln() * ml).floor() as usize;
            index.insert(record, level);
        }
        index
    }

    /// 计算查询向量与指定节点的距离
    fn dist_to(&self, query: &[f32], node: usize) -> f32 {
        cosine_dist(query, &self.nodes[node].vector)
    }

    fn insert(&mut self, record: &VectorRecord, level: usize) {
        let vector = normalize(&record.values);
        let new_id = self.nodes.len();
        self.nodes.push(Node {
            id: record.id.clone(),
            graph_node_id: record.graph_node_id.clone(),
            metadata_json: record.metadata_json.clone(),
            vector,
            neighbors: vec![Vec::new(); level + 1],
        });

        let entry = match self.entry {
            None => {
                self.entry = Some(new_id);
                self.max_layer = level;
                return;
            }
            Some(e) => e,
        };

        let query = self.nodes[new_id].vector.clone();
        let mut cur = entry;

        // Greedy descent through layers above the new node's top level.
        let mut lc = self.max_layer;
        while lc > level {
            cur = self.greedy_nearest(&query, cur, lc);
            lc -= 1;
        }

        // Connect from min(level, max_layer) down to 0.
        let start = level.min(self.max_layer);
        for lc in (0..=start).rev() {
            let found = self.search_layer(&query, cur, self.params.ef_construction, lc);
            let m = if lc == 0 {
                self.params.m_max0
            } else {
                self.params.m
            };
            let selected = select_neighbors(&found, m);
            for &nb in &selected {
                self.nodes[new_id].neighbors[lc].push(nb);
                self.nodes[nb].neighbors[lc].push(new_id);
                self.prune(nb, lc);
            }
            cur = selected.first().copied().unwrap_or(cur);
        }

        if level > self.max_layer {
            self.max_layer = level;
            self.entry = Some(new_id);
        }
    }

    fn prune(&mut self, node: usize, layer: usize) {
        let m = if layer == 0 {
            self.params.m_max0
        } else {
            self.params.m
        };
        if self.nodes[node].neighbors[layer].len() <= m {
            return;
        }
        let base = self.nodes[node].vector.clone();
        let mut scored: Vec<Cand> = self.nodes[node].neighbors[layer]
            .iter()
            .map(|&nb| Cand {
                dist: cosine_dist(&base, &self.nodes[nb].vector),
                id: nb,
            })
            .collect();
        scored.sort();
        scored.truncate(m);
        self.nodes[node].neighbors[layer] = scored.into_iter().map(|c| c.id).collect();
    }

    fn greedy_nearest(&self, query: &[f32], entry: usize, layer: usize) -> usize {
        let mut best = entry;
        let mut best_dist = self.dist_to(query, entry);
        loop {
            let mut improved = false;
            for &nb in &self.nodes[best].neighbors[layer] {
                let d = self.dist_to(query, nb);
                if d < best_dist {
                    best_dist = d;
                    best = nb;
                    improved = true;
                }
            }
            if !improved {
                return best;
            }
        }
    }

    /// Layer-local best-first search returning up to `ef` nearest candidates.
    fn search_layer(&self, query: &[f32], entry: usize, ef: usize, layer: usize) -> Vec<Cand> {
        let mut visited = HashSet::new();
        visited.insert(entry);
        let d0 = self.dist_to(query, entry);

        // candidates: explore closest first (min-heap via Reverse).
        let mut candidates = BinaryHeap::new();
        candidates.push(std::cmp::Reverse(Cand {
            dist: d0,
            id: entry,
        }));
        // result set: keep ef nearest, evict farthest (max-heap on dist).
        let mut result = BinaryHeap::new();
        result.push(Cand {
            dist: d0,
            id: entry,
        });

        while let Some(std::cmp::Reverse(c)) = candidates.pop() {
            let farthest = result.peek().map(|c| c.dist).unwrap_or(f32::INFINITY);
            if c.dist > farthest && result.len() >= ef {
                break;
            }
            for &nb in &self.nodes[c.id].neighbors[layer] {
                if visited.insert(nb) {
                    let d = self.dist_to(query, nb);
                    let farthest = result.peek().map(|c| c.dist).unwrap_or(f32::INFINITY);
                    if d < farthest || result.len() < ef {
                        candidates.push(std::cmp::Reverse(Cand { dist: d, id: nb }));
                        result.push(Cand { dist: d, id: nb });
                        if result.len() > ef {
                            result.pop();
                        }
                    }
                }
            }
        }

        let mut out: Vec<Cand> = result.into_vec();
        out.sort();
        out
    }
}

fn select_neighbors(candidates: &[Cand], m: usize) -> Vec<usize> {
    let mut sorted = candidates.to_vec();
    sorted.sort();
    sorted.truncate(m);
    sorted.into_iter().map(|c| c.id).collect()
}

impl VectorIndex for HnswIndex {
    fn search(&self, query: &[f32], top_k: usize) -> Vec<SearchHit> {
        let entry = match self.entry {
            Some(e) => e,
            None => return Vec::new(),
        };
        let q = normalize(query);

        let mut cur = entry;
        let mut lc = self.max_layer;
        while lc > 0 {
            cur = self.greedy_nearest(&q, cur, lc);
            lc -= 1;
        }

        let ef = self.params.ef_search.max(top_k);
        let mut found = self.search_layer(&q, cur, ef, 0);
        found.truncate(top_k);
        found
            .into_iter()
            .map(|c| {
                let node = &self.nodes[c.id];
                SearchHit {
                    id: node.id.clone(),
                    // report cosine similarity to stay consistent with FlatIndex.
                    score: 1.0 - c.dist,
                    graph_node_id: node.graph_node_id.clone(),
                    metadata_json: node.metadata_json.clone(),
                }
            })
            .collect()
    }

    fn kind(&self) -> &'static str {
        "hnsw"
    }
}
