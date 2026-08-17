//! 纯 Rust IVF-PQ 索引实现（基于 `VectorIndex` trait）
//!
//! IVF-PQ 是 E1 的第二种索引（设计文档 §3.3 / 项目分解 §2.2.2；合同 M-MVP
//! "HNSW+IVF-PQ"，验收门槛召回率 ≥ 92%）。它以少量召回率换取大幅降低的内存
//! 和扫描成本，这使得 5e7 规模的向量集合变得可负担。
//!
//! **流水线**：
//!   1. 粗量化器：k-means 分成 `nlist` 个质心。每个向量被分配到最近的质心（一个倒排列表）。
//!   2. 乘积量化器：残差（向量 - 粗质心）被拆分成 `m` 个子向量；每个子空间有自己的
//!      k-means 码本（`ksub` 个质心），因此一个向量被存储为 `m` 个紧凑码。
//!   3. 查询：探测 `nprobe` 个最近的列表；对于每个列表，构建一个 ADC 查找表
//!      （非对称距离计算），包含残差查询与每个子码本条目之间的平方 L2 距离，
//!      然后通过查表求和来对候选者评分。
//!
//! **余弦度量**：向量和查询都进行 L2 归一化，因此平方 L2 距离 `d² = 2 - 2·cos`。
//! 我们在残差上计算 L2 距离，并报告 `score = 1 - d²/2` 以与 `FlatIndex`/`HnswIndex`
//! 的余弦保持一致。

use crate::index::VectorIndex;
use crate::{SearchHit, VectorRecord};

/// IVF-PQ 参数配置
#[derive(Clone, Copy)]
pub struct IvfPqParams {
    /// 粗量化（IVF）质心数 / 倒排列表数
    pub nlist: usize,
    /// 每次查询探测的列表数。越高 = 召回率越好，但越慢
    pub nprobe: usize,
    /// PQ 子量化器数量（每个码的子向量数）
    pub m: usize,
    /// 每个子量化器的质心数（码字母表，≤ 256 以适应一个字节）
    pub ksub: usize,
    /// 每次 k-means 运行的 Lloyd 迭代次数
    pub kmeans_iters: usize,
    pub seed: u64,
}

impl Default for IvfPqParams {
    fn default() -> Self {
        Self {
            nlist: 100,      // 100 个倒排列表
            nprobe: 16,      // 探测 16 个列表
            m: 8,            // 8 个子向量
            ksub: 256,       // 每个子向量 256 个质心（1 字节）
            kmeans_iters: 12,
            seed: 0x9E3779B97F4A7C15,
        }
    }
}

impl IvfPqParams {
    /// 推导集合规模感知参数（设计文档 §3.3 自适应建索引）
    ///
    /// 单一固定的 `nlist` 无法跨规模工作：在小分段上列表太多会使每个列表退化
    /// （召回率崩溃），在 5e7 集合上列表太少会使扫描昂贵。我们遵循常见的 FAISS
    /// 经验法则 `nlist ≈ √n`；查询的真正邻居集中在最接近查询的列表中，因此
    /// 探测约一半的列表（在大集合上设置上限以保持扫描有界）可以使召回率保持在
    /// 92% 门槛之上。
    pub fn adaptive(n: usize, dim: usize) -> Self {
        let n = n.max(1);
        let dim = dim.max(1);

        // nlist ≈ √n，限制在 [1, 4096]，且不超过 n
        let nlist = ((n as f64).sqrt().round() as usize).clamp(1, 4096).min(n);
        
        // 探测约一半的列表以确保召回率安全，下限为 min(nlist, 8) 使小集合保持近乎穷举，
        // 上限为 128 使大集合仍能积极剪枝
        let probe_floor = nlist.min(8);
        let probe_cap = nlist.min(128);
        let nprobe = (nlist / 2).clamp(probe_floor, probe_cap);
        
        // 每个子量化器约 2 维（更细 = 更准确），限制在合理的码宽度 64 字节/向量
        let m = (dim / 2).clamp(1, 64).min(dim);

        Self {
            nlist,
            nprobe,
            m,
            ..Self::default()
        }
    }
}

/// 倒排列表条目
struct Entry {
    id: String,                    // 向量 ID
    graph_node_id: Option<String>, // 关联的图节点 ID
    metadata_json: Option<String>, // 向量元数据
    /// PQ 码：每个子空间一个子量化器索引（1 字节）
    code: Vec<u8>,
}

/// IVF-PQ 索引结构
pub struct IvfPqIndex {
    dim: usize,                        // 向量维度
    nprobe: usize,                     // 查询时探测的列表数
    /// 粗质心：`nlist` × `dim`
    coarse: Vec<Vec<f32>>,
    /// 倒排列表：每个粗质心对应一个条目列表
    lists: Vec<Vec<Entry>>,
    /// 子空间边界：`[start, end)` 覆盖 `dim` 轴，共 `m` 个
    subspaces: Vec<(usize, usize)>,
    /// PQ 码本：`m` 个子量化器，每个 `ksub` × sub-dim 个质心
    codebooks: Vec<Vec<Vec<f32>>>,
}

struct Rng(u64);
impl Rng {
    fn next_u64(&mut self) -> u64 {
        let mut x = self.0;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.0 = x;
        x.wrapping_mul(0x2545F4914F6CDD1D)
    }
    fn next_usize(&mut self, bound: usize) -> usize {
        if bound == 0 {
            0
        } else {
            (self.next_u64() % bound as u64) as usize
        }
    }
}

fn normalize(v: &[f32]) -> Vec<f32> {
    let norm: f32 = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm == 0.0 {
        return v.to_vec();
    }
    v.iter().map(|x| x / norm).collect()
}

fn l2_sq(a: &[f32], b: &[f32]) -> f32 {
    a.iter().zip(b).map(|(x, y)| (x - y) * (x - y)).sum()
}

fn nearest(centroids: &[Vec<f32>], v: &[f32]) -> (usize, f32) {
    let mut best = 0usize;
    let mut best_d = f32::INFINITY;
    for (i, c) in centroids.iter().enumerate() {
        let d = l2_sq(c, v);
        if d < best_d {
            best_d = d;
            best = i;
        }
    }
    (best, best_d)
}

/// Lloyd's k-means with k-means++ seeding over equal-length vectors.
fn kmeans(data: &[Vec<f32>], k: usize, iters: usize, rng: &mut Rng) -> Vec<Vec<f32>> {
    let n = data.len();
    let dim = data.first().map(|v| v.len()).unwrap_or(0);
    if n == 0 || dim == 0 {
        return Vec::new();
    }
    let k = k.min(n).max(1);

    // k-means++ seeding.
    let mut centroids: Vec<Vec<f32>> = Vec::with_capacity(k);
    centroids.push(data[rng.next_usize(n)].clone());
    let mut dist: Vec<f32> = data.iter().map(|v| l2_sq(v, &centroids[0])).collect();
    while centroids.len() < k {
        let total: f64 = dist.iter().map(|&d| d as f64).sum();
        let next = if total <= 0.0 {
            rng.next_usize(n)
        } else {
            // Weighted pick proportional to squared distance.
            let mut target = (rng.next_u64() as f64 / u64::MAX as f64) * total;
            let mut chosen = n - 1;
            for (i, &d) in dist.iter().enumerate() {
                target -= d as f64;
                if target <= 0.0 {
                    chosen = i;
                    break;
                }
            }
            chosen
        };
        let c = data[next].clone();
        for (i, v) in data.iter().enumerate() {
            let d = l2_sq(v, &c);
            if d < dist[i] {
                dist[i] = d;
            }
        }
        centroids.push(c);
    }

    // Lloyd iterations.
    let mut assign = vec![0usize; n];
    for _ in 0..iters {
        let mut moved = false;
        for (i, v) in data.iter().enumerate() {
            let (a, _) = nearest(&centroids, v);
            if a != assign[i] {
                assign[i] = a;
                moved = true;
            }
        }
        let mut sums = vec![vec![0.0f32; dim]; centroids.len()];
        let mut counts = vec![0usize; centroids.len()];
        for (i, v) in data.iter().enumerate() {
            let a = assign[i];
            counts[a] += 1;
            for (s, x) in sums[a].iter_mut().zip(v) {
                *s += x;
            }
        }
        for (c, (sum, &count)) in centroids.iter_mut().zip(sums.iter().zip(counts.iter())) {
            if count > 0 {
                for (cv, s) in c.iter_mut().zip(sum) {
                    *cv = s / count as f32;
                }
            }
        }
        // Reseed empty clusters onto a random point to avoid dead centroids.
        for (ci, &count) in counts.iter().enumerate() {
            if count == 0 {
                centroids[ci] = data[rng.next_usize(n)].clone();
                moved = true;
            }
        }
        if !moved {
            break;
        }
    }
    centroids
}

/// Split `dim` into `m` contiguous, near-even sub-spaces.
fn make_subspaces(dim: usize, m: usize) -> Vec<(usize, usize)> {
    let m = m.min(dim).max(1);
    let base = dim / m;
    let rem = dim % m;
    let mut out = Vec::with_capacity(m);
    let mut start = 0;
    for j in 0..m {
        let len = base + usize::from(j < rem);
        out.push((start, start + len));
        start += len;
    }
    out
}

impl IvfPqIndex {
    /// Build with collection-size-aware parameters. This is the path Segment
    /// sealing uses; explicit tuning goes through `build_with`.
    pub fn build(records: &[VectorRecord]) -> Self {
        let dim = records.first().map(|r| r.values.len()).unwrap_or(0);
        Self::build_with(records, IvfPqParams::adaptive(records.len(), dim))
    }

    pub fn build_with(records: &[VectorRecord], params: IvfPqParams) -> Self {
        let dim = records.first().map(|r| r.values.len()).unwrap_or(0);
        let n = records.len();
        let mut rng = Rng(params.seed | 1);

        if n == 0 || dim == 0 {
            return Self {
                dim,
                nprobe: 1,
                coarse: Vec::new(),
                lists: Vec::new(),
                subspaces: Vec::new(),
                codebooks: Vec::new(),
            };
        }

        let normalized: Vec<Vec<f32>> = records.iter().map(|r| normalize(&r.values)).collect();

        // 1) Coarse quantizer. Cap nlist so lists are not degenerate.
        let nlist = params.nlist.min(n).max(1);
        let coarse = kmeans(&normalized, nlist, params.kmeans_iters, &mut rng);
        let nlist = coarse.len();

        let assign: Vec<usize> = normalized.iter().map(|v| nearest(&coarse, v).0).collect();

        // 2) Product quantizer over residuals.
        let subspaces = make_subspaces(dim, params.m);
        let residuals: Vec<Vec<f32>> = normalized
            .iter()
            .zip(&assign)
            .map(|(v, &a)| v.iter().zip(&coarse[a]).map(|(x, c)| x - c).collect())
            .collect();

        let ksub = params.ksub.clamp(1, 256);
        let mut codebooks: Vec<Vec<Vec<f32>>> = Vec::with_capacity(subspaces.len());
        for &(start, end) in &subspaces {
            let sub: Vec<Vec<f32>> = residuals.iter().map(|r| r[start..end].to_vec()).collect();
            codebooks.push(kmeans(&sub, ksub, params.kmeans_iters, &mut rng));
        }

        // 3) Encode every vector into PQ codes and bucket it into its list.
        let mut lists: Vec<Vec<Entry>> = (0..nlist).map(|_| Vec::new()).collect();
        for (i, record) in records.iter().enumerate() {
            let a = assign[i];
            let residual = &residuals[i];
            let mut code = Vec::with_capacity(subspaces.len());
            for (j, &(start, end)) in subspaces.iter().enumerate() {
                let (idx, _) = nearest(&codebooks[j], &residual[start..end]);
                code.push(idx as u8);
            }
            lists[a].push(Entry {
                id: record.id.clone(),
                graph_node_id: record.graph_node_id.clone(),
                metadata_json: record.metadata_json.clone(),
                code,
            });
        }

        Self {
            dim,
            nprobe: params.nprobe.min(nlist).max(1),
            coarse,
            lists,
            subspaces,
            codebooks,
        }
    }
}

impl VectorIndex for IvfPqIndex {
    fn search(&self, query: &[f32], top_k: usize) -> Vec<SearchHit> {
        if self.coarse.is_empty() || top_k == 0 || query.len() != self.dim {
            return Vec::new();
        }
        let q = normalize(query);

        // Probe the nprobe nearest coarse centroids.
        let mut coarse_scored: Vec<(usize, f32)> = self
            .coarse
            .iter()
            .enumerate()
            .map(|(i, c)| (i, l2_sq(c, &q)))
            .collect();
        coarse_scored.sort_by(|a, b| a.1.total_cmp(&b.1).then(a.0.cmp(&b.0)));
        coarse_scored.truncate(self.nprobe);

        let mut hits: Vec<(f32, &Entry)> = Vec::new();
        for (list_id, _) in coarse_scored {
            let centroid = &self.coarse[list_id];
            // Residual query for this list, then an ADC table per sub-space.
            let residual_q: Vec<f32> = q.iter().zip(centroid).map(|(x, c)| x - c).collect();
            let lut: Vec<Vec<f32>> = self
                .subspaces
                .iter()
                .enumerate()
                .map(|(j, &(start, end))| {
                    let rq = &residual_q[start..end];
                    self.codebooks[j].iter().map(|c| l2_sq(rq, c)).collect()
                })
                .collect();

            for entry in &self.lists[list_id] {
                let dist: f32 = entry
                    .code
                    .iter()
                    .enumerate()
                    .map(|(j, &c)| lut[j][c as usize])
                    .sum();
                hits.push((dist, entry));
            }
        }

        hits.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.id.cmp(&b.1.id)));
        hits.truncate(top_k);
        hits.into_iter()
            .map(|(dist, entry)| SearchHit {
                id: entry.id.clone(),
                // Normalized vectors: d² = 2 - 2cos => cos = 1 - d²/2.
                score: 1.0 - dist / 2.0,
                graph_node_id: entry.graph_node_id.clone(),
                metadata_json: entry.metadata_json.clone(),
            })
            .collect()
    }

    fn kind(&self) -> &'static str {
        "ivfpq"
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::flat_index::search_flat;
    use std::collections::HashSet;

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

    /// Contract acceptance gate (M-MVP): IVF-PQ recall@10 ≥ 92% vs exact search.
    #[test]
    fn ivfpq_recall_meets_contract_gate() {
        let dim = 16;
        let n = 2000;
        let records: Vec<VectorRecord> = (0..n)
            .map(|i| VectorRecord {
                id: format!("v{i}"),
                values: pseudo_vector(i as u64 + 7, dim),
                graph_node_id: None,
                metadata_json: None,
            })
            .collect();

        let params = IvfPqParams {
            nlist: 32,
            nprobe: 16,
            m: 8,
            ksub: 256,
            kmeans_iters: 16,
            ..IvfPqParams::default()
        };
        let index = IvfPqIndex::build_with(&records, params);

        let top_k = 10;
        let queries = 30;
        let mut hit = 0usize;
        let mut total = 0usize;
        for q in 0..queries {
            let query = pseudo_vector(1_000_000 + q, dim);
            let truth: HashSet<String> = search_flat(&records, &query, top_k)
                .into_iter()
                .map(|h| h.id)
                .collect();
            for h in index.search(&query, top_k) {
                if truth.contains(&h.id) {
                    hit += 1;
                }
            }
            total += truth.len();
        }
        let recall = hit as f64 / total as f64;
        assert!(recall >= 0.92, "IVF-PQ recall@{top_k} below gate: {recall}");
    }

    #[test]
    fn adaptive_params_scale_with_collection_size() {
        // nlist ≈ √n, clamped; nprobe never below min(nlist, 8).
        let small = IvfPqParams::adaptive(64, 16);
        assert_eq!(small.nlist, 8); // √64
        assert!(small.nprobe >= small.nlist.min(8));
        assert!(small.nprobe <= small.nlist);

        let large = IvfPqParams::adaptive(1_000_000, 768);
        assert_eq!(large.nlist, 1000); // √1e6
        assert!(large.nprobe >= 8 && large.nprobe <= large.nlist);
        assert_eq!(large.m, 64); // 768/4 capped at 64

        // Degenerate inputs never panic and stay valid.
        let tiny = IvfPqParams::adaptive(1, 1);
        assert_eq!(tiny.nlist, 1);
        assert_eq!(tiny.nprobe, 1);
        assert_eq!(tiny.m, 1);
    }

    /// The default (adaptive) build path — the one Segment sealing uses — must
    /// also clear the contract recall gate, not just hand-tuned params.
    #[test]
    fn ivfpq_adaptive_build_meets_recall_gate() {
        let dim = 16;
        let n = 3000;
        let records: Vec<VectorRecord> = (0..n)
            .map(|i| VectorRecord {
                id: format!("v{i}"),
                values: pseudo_vector(i as u64 + 11, dim),
                graph_node_id: None,
                metadata_json: None,
            })
            .collect();

        let index = IvfPqIndex::build(&records); // adaptive params

        let top_k = 10;
        let queries = 30;
        let mut hit = 0usize;
        let mut total = 0usize;
        for q in 0..queries {
            let query = pseudo_vector(2_000_000 + q, dim);
            let truth: HashSet<String> = search_flat(&records, &query, top_k)
                .into_iter()
                .map(|h| h.id)
                .collect();
            for h in index.search(&query, top_k) {
                if truth.contains(&h.id) {
                    hit += 1;
                }
            }
            total += truth.len();
        }
        let recall = hit as f64 / total as f64;
        assert!(
            recall >= 0.92,
            "adaptive IVF-PQ recall@{top_k} below gate: {recall}"
        );
    }

    #[test]
    fn ivfpq_finds_exact_nearest_on_clear_data() {
        let records: Vec<VectorRecord> = vec![
            VectorRecord {
                id: "x".into(),
                values: vec![1.0, 0.0, 0.0, 0.0],
                graph_node_id: Some("nx".into()),
                metadata_json: Some(r#"{"memory_id":"m1"}"#.into()),
            },
            VectorRecord {
                id: "y".into(),
                values: vec![0.0, 1.0, 0.0, 0.0],
                graph_node_id: None,
                metadata_json: None,
            },
            VectorRecord {
                id: "z".into(),
                values: vec![0.0, 0.0, 1.0, 0.0],
                graph_node_id: None,
                metadata_json: None,
            },
        ];
        let params = IvfPqParams {
            nlist: 2,
            nprobe: 2,
            m: 2,
            ksub: 4,
            kmeans_iters: 10,
            ..IvfPqParams::default()
        };
        let index = IvfPqIndex::build_with(&records, params);
        let hits = index.search(&[1.0, 0.0, 0.0, 0.0], 1);
        assert_eq!(hits[0].id, "x");
        assert_eq!(hits[0].graph_node_id.as_deref(), Some("nx"));
        assert_eq!(
            hits[0].metadata_json.as_deref(),
            Some(r#"{"memory_id":"m1"}"#)
        );
    }
}
