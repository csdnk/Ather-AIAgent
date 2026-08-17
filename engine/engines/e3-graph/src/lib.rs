use ae_common::{
    AetherError, BlockId, CompactPolicy, CompactReport, EngineInstanceId, EngineKind,
    MigrationState, MigrationTaskId, NamespaceId, OperationKind, RecoveryReport, Result, SegmentId,
    SnapshotId,
};
use ae_kernel::{AetherEngine, MigrationAck, SegmentControl, SegmentMigrationStatus};
use ae_wal::{SharedWal, WalPayload, WalRecord};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::{Mutex, RwLock};

/// E3 manages a single logical graph segment. P3 freeze/migrate addresses it by
/// this id (design doc §5.6 write fence over a graph Segment).
pub const GRAPH_SEGMENT_ID: &str = "graph/default";

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct NodeRecord {
    pub id: String,
    pub properties_json: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EdgeRecord {
    pub id: String,
    pub src: String,
    pub dst: String,
    pub label: Option<String>,
    pub properties_json: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SubGraph {
    pub nodes: Vec<NodeRecord>,
    pub edges: Vec<EdgeRecord>,
}

/// Anchor returned by the fusion vector search step (design doc §5.3.3 Step 1).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct AnchorHit {
    pub node_id: String,
    pub score: f32,
}

/// Tunables for `vector_anchored_subgraph` (design doc §5.3.3).
#[derive(Debug, Clone, Copy)]
pub struct FusionParams {
    pub anchor_top_k: usize,
    pub hop: usize,
    pub fanout_cap: usize,
    pub max_nodes: usize,
    pub max_edges: usize,
}

impl Default for FusionParams {
    fn default() -> Self {
        // Production-safe defaults from the doc (5k nodes / 50k edges soft cap,
        // fanout 100 per hop).
        Self {
            anchor_top_k: 10,
            hop: 2,
            fanout_cap: 100,
            max_nodes: 5_000,
            max_edges: 50_000,
        }
    }
}

/// Hard ceiling on returned nodes; beyond this we fail with ResultTooLarge
/// (design doc §2.2.1 / appendix B P2Err_ResultTooLarge).
pub const HARD_MAX_NODES: usize = 100_000;

/// Predicate filter for traversal / fusion (design doc §5.3.3 edge_filter /
/// node_filter, the building block for the §5.4 OpenCypher WHERE subset).
#[derive(Debug, Clone, Default)]
pub struct GraphFilter {
    /// Allowed edge labels (relationship types). `None` = no restriction.
    pub edge_labels: Option<Vec<String>>,
    /// Equality predicate on a node property: `node.<key> == <value>`.
    pub node_property_eq: Option<(String, serde_json::Value)>,
}

impl GraphFilter {
    pub fn with_edge_labels(mut self, labels: impl IntoIterator<Item = String>) -> Self {
        self.edge_labels = Some(labels.into_iter().collect());
        self
    }

    pub fn with_node_property_eq(
        mut self,
        key: impl Into<String>,
        value: serde_json::Value,
    ) -> Self {
        self.node_property_eq = Some((key.into(), value));
        self
    }

    /// Edge-label pushdown predicate, applied before expansion.
    pub fn edge_label_allowed(&self, label: Option<&str>) -> bool {
        match &self.edge_labels {
            None => true,
            Some(allowed) => label
                .map(|l| allowed.iter().any(|a| a == l))
                .unwrap_or(false),
        }
    }

    /// Node-property equality predicate, applied to result inclusion.
    pub fn node_matches(&self, node: &NodeRecord) -> bool {
        let Some((key, expected)) = &self.node_property_eq else {
            return true;
        };
        let Some(json) = &node.properties_json else {
            return false;
        };
        serde_json::from_str::<serde_json::Value>(json)
            .ok()
            .and_then(|val| val.get(key).cloned())
            .map(|got| &got == expected)
            .unwrap_or(false)
    }
}

/// Single-path fusion result. The read path stays entirely inside E3: no
/// online call to E1 (design doc §5.1, §5.3.3).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct FusionResult {
    pub anchors: Vec<AnchorHit>,
    pub subgraph: SubGraph,
    pub truncated: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
enum GraphWalOp {
    CreateNode(NodeRecord),
    CreateEdge(EdgeRecord),
    ProjectVector { node_id: String, vector: Vec<f32> },
    SetMigrationState { migration: MigrationState },
}

#[derive(Default)]
struct GraphState {
    nodes: HashMap<String, NodeRecord>,
    edges: HashMap<String, EdgeRecord>,
    adj: HashMap<String, Vec<String>>,
    /// Fusion vector projection: node_id -> vector (design doc §5.2).
    vectors: HashMap<String, Vec<f32>>,
}

pub struct InMemoryGraphEngine {
    id: EngineInstanceId,
    namespace_id: NamespaceId,
    wal: SharedWal,
    state: RwLock<GraphState>,
    /// Write fence for the single graph segment (design doc §5.6). While set,
    /// mutations are rejected with `P2Err_SegmentFrozen`; reads still serve.
    migration: Mutex<MigrationState>,
}

impl InMemoryGraphEngine {
    pub fn new(instance: impl Into<String>, wal: SharedWal) -> Self {
        Self {
            id: EngineInstanceId::new(EngineKind::Graph, instance),
            namespace_id: NamespaceId::default_for_engine(EngineKind::Graph),
            wal,
            state: RwLock::new(GraphState::default()),
            migration: Mutex::new(MigrationState::default()),
        }
    }

    /// Reject mutations while the graph segment is frozen for migration.
    fn ensure_writable(&self) -> Result<()> {
        if self
            .migration
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .active_migration_id
            .is_some()
        {
            return Err(AetherError::SegmentFrozen(format!(
                "graph segment {GRAPH_SEGMENT_ID} is frozen for migration"
            )));
        }
        Ok(())
    }

    pub fn create_node(&self, node: NodeRecord) -> Result<()> {
        let _span = tracing::info_span!("ae.graph.create_node", engine = "graph").entered();
        self.ensure_writable()?;
        let op = GraphWalOp::CreateNode(node);
        self.append_op(OperationKind::CreateNode, &op)?;
        self.apply_op(op)
    }

    pub fn create_edge(&self, edge: EdgeRecord) -> Result<()> {
        self.ensure_writable()?;
        let op = GraphWalOp::CreateEdge(edge);
        self.append_op(OperationKind::CreateEdge, &op)?;
        self.apply_op(op)
    }

    /// Project a vector onto an existing node so fusion queries can run
    /// entirely inside E3 (design doc §5.2 Fusion Vector Projection).
    pub fn project_vector(&self, node_id: &str, vector: Vec<f32>) -> Result<()> {
        self.ensure_writable()?;
        let op = GraphWalOp::ProjectVector {
            node_id: node_id.to_string(),
            vector,
        };
        self.append_op(OperationKind::ProjectVector, &op)?;
        self.apply_op(op)
    }

    /// VectorAnchoredSubgraph, single read path (design doc §5.3.3).
    ///
    /// Step 1: cosine anchor search over the node vector projection.
    /// Step 2: bounded k-hop BFS expansion from the anchors (fanout capped).
    /// Step 3: pack the subgraph. No online E1 call.
    pub fn vector_anchored_subgraph(
        &self,
        query: &[f32],
        params: FusionParams,
        filter: &GraphFilter,
    ) -> Result<FusionResult> {
        let _span = tracing::info_span!("ae.graph.fusion", engine = "graph").entered();
        let state = self
            .state
            .read()
            .map_err(|e| AetherError::Internal(e.to_string()))?;

        // Step 1: anchor search.
        let mut scored: Vec<AnchorHit> = state
            .vectors
            .iter()
            .map(|(id, v)| AnchorHit {
                node_id: id.clone(),
                score: cosine_similarity(v, query),
            })
            .collect();
        scored.sort_by(|a, b| {
            b.score
                .partial_cmp(&a.score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| a.node_id.cmp(&b.node_id))
        });
        scored.truncate(params.anchor_top_k);

        // Step 2: bounded BFS expansion from anchors.
        let mut visited_nodes: HashSet<String> = HashSet::new();
        let mut visited_edges: HashSet<String> = HashSet::new();
        let mut queue: VecDeque<(String, usize)> = VecDeque::new();
        let mut truncated = false;

        for anchor in &scored {
            if state.nodes.contains_key(&anchor.node_id)
                && visited_nodes.insert(anchor.node_id.clone())
            {
                queue.push_back((anchor.node_id.clone(), 0));
            }
        }

        'bfs: while let Some((node_id, depth)) = queue.pop_front() {
            if depth >= params.hop {
                continue;
            }
            if let Some(edge_ids) = state.adj.get(&node_id) {
                // Deterministic, fanout-capped expansion.
                let mut edge_ids = edge_ids.clone();
                edge_ids.sort();
                for edge_id in edge_ids.into_iter().take(params.fanout_cap) {
                    let Some(edge) = state.edges.get(&edge_id) else {
                        continue;
                    };
                    // edge_filter pushdown: skip disallowed relationship types.
                    if !filter.edge_label_allowed(edge.label.as_deref()) {
                        continue;
                    }
                    if visited_nodes.len() > HARD_MAX_NODES {
                        return Err(AetherError::ResultTooLarge(format!(
                            "subgraph exceeded hard cap of {HARD_MAX_NODES} nodes"
                        )));
                    }
                    if visited_edges.len() >= params.max_edges {
                        truncated = true;
                        break 'bfs;
                    }
                    visited_edges.insert(edge.id.clone());
                    if !visited_nodes.contains(&edge.dst) {
                        if visited_nodes.len() >= params.max_nodes {
                            truncated = true;
                            break 'bfs;
                        }
                        visited_nodes.insert(edge.dst.clone());
                        queue.push_back((edge.dst.clone(), depth + 1));
                    }
                }
            }
        }

        // node_filter: keep only nodes matching the property predicate, then
        // drop edges whose endpoints were filtered out.
        let mut nodes: Vec<_> = visited_nodes
            .iter()
            .filter_map(|id| state.nodes.get(id).cloned())
            .filter(|n| filter.node_matches(n))
            .collect();
        nodes.sort_by(|a, b| a.id.cmp(&b.id));
        let kept: HashSet<&str> = nodes.iter().map(|n| n.id.as_str()).collect();

        let mut edges: Vec<_> = visited_edges
            .iter()
            .filter_map(|id| state.edges.get(id).cloned())
            .filter(|e| kept.contains(e.src.as_str()) && kept.contains(e.dst.as_str()))
            .collect();
        edges.sort_by(|a, b| a.id.cmp(&b.id));

        Ok(FusionResult {
            anchors: scored,
            subgraph: SubGraph { nodes, edges },
            truncated,
        })
    }

    pub fn get_node(&self, node_id: &str) -> Result<NodeRecord> {
        self.state
            .read()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .nodes
            .get(node_id)
            .cloned()
            .ok_or_else(|| AetherError::NotFound(format!("node {node_id}")))
    }

    pub fn neighbors(&self, start_node_id: &str, depth: usize) -> Result<SubGraph> {
        let state = self
            .state
            .read()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        if !state.nodes.contains_key(start_node_id) {
            return Err(AetherError::NotFound(format!("node {start_node_id}")));
        }

        let mut visited_nodes = HashSet::new();
        let mut visited_edges = HashSet::new();
        let mut queue = VecDeque::new();
        queue.push_back((start_node_id.to_string(), 0usize));
        visited_nodes.insert(start_node_id.to_string());

        while let Some((node_id, d)) = queue.pop_front() {
            if d >= depth {
                continue;
            }

            if let Some(edge_ids) = state.adj.get(&node_id) {
                for edge_id in edge_ids {
                    if let Some(edge) = state.edges.get(edge_id) {
                        visited_edges.insert(edge.id.clone());
                        if visited_nodes.insert(edge.dst.clone()) {
                            queue.push_back((edge.dst.clone(), d + 1));
                        }
                    }
                }
            }
        }

        let mut nodes: Vec<_> = visited_nodes
            .into_iter()
            .filter_map(|id| state.nodes.get(&id).cloned())
            .collect();
        nodes.sort_by(|a, b| a.id.cmp(&b.id));

        let mut edges: Vec<_> = visited_edges
            .into_iter()
            .filter_map(|id| state.edges.get(&id).cloned())
            .collect();
        edges.sort_by(|a, b| a.id.cmp(&b.id));

        Ok(SubGraph { nodes, edges })
    }

    pub fn merge_subgraphs(&self, starts: &[String], depth: usize) -> Result<SubGraph> {
        let mut node_map = HashMap::new();
        let mut edge_map = HashMap::new();

        for node in starts {
            if let Ok(sub) = self.neighbors(node, depth) {
                for n in sub.nodes {
                    node_map.insert(n.id.clone(), n);
                }
                for e in sub.edges {
                    edge_map.insert(e.id.clone(), e);
                }
            }
        }

        let mut nodes: Vec<_> = node_map.into_values().collect();
        nodes.sort_by(|a, b| a.id.cmp(&b.id));
        let mut edges: Vec<_> = edge_map.into_values().collect();
        edges.sort_by(|a, b| a.id.cmp(&b.id));
        Ok(SubGraph { nodes, edges })
    }

    fn append_op(&self, operation: OperationKind, op: &GraphWalOp) -> Result<()> {
        let payload = serde_json::to_vec(op)?;
        let record = WalRecord::new(
            EngineKind::Graph,
            self.id.clone(),
            self.namespace_id.clone(),
            operation,
            WalPayload::Inline(payload),
        );
        self.wal.append(record)?;
        self.wal.flush()?;
        Ok(())
    }

    fn apply_op(&self, op: GraphWalOp) -> Result<()> {
        let mut state = self
            .state
            .write()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        match op {
            GraphWalOp::CreateNode(node) => {
                state.nodes.insert(node.id.clone(), node);
                Ok(())
            }
            GraphWalOp::CreateEdge(edge) => {
                if !state.nodes.contains_key(&edge.src) {
                    return Err(AetherError::NotFound(format!("src node {}", edge.src)));
                }
                if !state.nodes.contains_key(&edge.dst) {
                    return Err(AetherError::NotFound(format!("dst node {}", edge.dst)));
                }
                state
                    .adj
                    .entry(edge.src.clone())
                    .or_default()
                    .push(edge.id.clone());
                state.edges.insert(edge.id.clone(), edge);
                Ok(())
            }
            GraphWalOp::ProjectVector { node_id, vector } => {
                if !state.nodes.contains_key(&node_id) {
                    return Err(AetherError::NotFound(format!("node {node_id}")));
                }
                state.vectors.insert(node_id, vector);
                Ok(())
            }
            GraphWalOp::SetMigrationState { migration } => {
                *self
                    .migration
                    .lock()
                    .map_err(|e| AetherError::Internal(e.to_string()))? = migration;
                Ok(())
            }
        }
    }

    fn apply_record(&self, record: &WalRecord) -> Result<bool> {
        if record.engine != EngineKind::Graph || record.instance_id != self.id {
            return Ok(false);
        }
        match &record.payload {
            WalPayload::Inline(bytes) => {
                let op: GraphWalOp = serde_json::from_slice(bytes)?;
                self.apply_op(op)?;
                Ok(true)
            }
            WalPayload::BlockRef { .. } => Err(AetherError::Unsupported(
                "graph BlockRef replay requires P1 block client".into(),
            )),
        }
    }
}

fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let mut dot = 0.0;
    let mut norm_a = 0.0;
    let mut norm_b = 0.0;
    for (x, y) in a.iter().zip(b.iter()) {
        dot += x * y;
        norm_a += x * x;
        norm_b += y * y;
    }
    if norm_a == 0.0 || norm_b == 0.0 {
        return 0.0;
    }
    dot / (norm_a.sqrt() * norm_b.sqrt())
}

impl AetherEngine for InMemoryGraphEngine {
    fn instance_id(&self) -> &EngineInstanceId {
        &self.id
    }

    fn open(&self) -> Result<()> {
        Ok(())
    }

    fn recover(&self) -> Result<RecoveryReport> {
        *self
            .state
            .write()
            .map_err(|e| AetherError::Internal(e.to_string()))? = GraphState::default();
        *self
            .migration
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))? = MigrationState::default();

        let mut replayed = 0;
        let mut skipped = 0;
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

    fn snapshot(&self) -> Result<SnapshotId> {
        Ok(SnapshotId::random())
    }

    fn compact(&self, _policy: CompactPolicy) -> Result<CompactReport> {
        Ok(CompactReport {
            compacted_segments: 0,
            reclaimed_bytes: 0,
        })
    }
}

impl SegmentControl for InMemoryGraphEngine {
    fn list_segments(&self) -> Result<Vec<SegmentId>> {
        Ok(vec![SegmentId::new(GRAPH_SEGMENT_ID)])
    }

    fn migration_status(&self, segment: &SegmentId) -> Result<SegmentMigrationStatus> {
        self.check_segment(segment)?;
        let migration = self
            .migration
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?
            .clone();
        Ok(SegmentMigrationStatus {
            segment_id: segment.clone(),
            state: Self::migration_lifecycle(&migration).into(),
            migration,
        })
    }

    fn prepare_migration(
        &self,
        segment: &SegmentId,
        migration_id: &MigrationTaskId,
        expected_route_epoch: Option<u64>,
    ) -> Result<MigrationAck> {
        let _span = tracing::info_span!("ae.segment.freeze", engine = "graph").entered();
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
        let _span = tracing::info_span!("ae.segment.migrate_complete", engine = "graph").entered();
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

impl InMemoryGraphEngine {
    fn transition_migration(
        &self,
        segment: &SegmentId,
        operation: OperationKind,
        transition: impl FnOnce(&mut MigrationState) -> Result<bool>,
    ) -> Result<MigrationAck> {
        self.check_segment(segment)?;
        let mut current = self
            .migration
            .lock()
            .map_err(|e| AetherError::Internal(e.to_string()))?;
        let mut migration = current.clone();
        let idempotent = transition(&mut migration)?;
        if !idempotent {
            let op = GraphWalOp::SetMigrationState {
                migration: migration.clone(),
            };
            self.append_op(operation, &op)?;
            *current = migration.clone();
        }

        Ok(MigrationAck::new(
            segment.clone(),
            Self::migration_lifecycle(&migration),
            migration,
            idempotent,
        ))
    }

    fn migration_lifecycle(migration: &MigrationState) -> &'static str {
        if migration.active_migration_id.is_some() {
            "frozen"
        } else {
            "sealed"
        }
    }

    fn check_segment(&self, segment: &SegmentId) -> Result<()> {
        if segment.0 == GRAPH_SEGMENT_ID {
            Ok(())
        } else {
            Err(AetherError::NotFound(format!("segment {}", segment.0)))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use ae_wal::MemoryWal;
    use std::sync::Arc;

    #[test]
    fn graph_recover_then_traverse() {
        let wal = Arc::new(MemoryWal::new());
        let graph = InMemoryGraphEngine::new("default", wal.clone());
        graph
            .create_node(NodeRecord {
                id: "n1".into(),
                properties_json: None,
            })
            .unwrap();
        graph
            .create_node(NodeRecord {
                id: "n2".into(),
                properties_json: None,
            })
            .unwrap();
        graph
            .create_edge(EdgeRecord {
                id: "e1".into(),
                src: "n1".into(),
                dst: "n2".into(),
                label: Some("knows".into()),
                properties_json: None,
            })
            .unwrap();

        let recovered = InMemoryGraphEngine::new("default", wal);
        recovered.recover().unwrap();
        let sub = recovered.neighbors("n1", 1).unwrap();
        assert_eq!(sub.nodes.len(), 2);
        assert_eq!(sub.edges.len(), 1);
    }

    #[test]
    fn fusion_single_path_recovers_and_expands() {
        let wal = Arc::new(MemoryWal::new());
        let graph = InMemoryGraphEngine::new("default", wal.clone());

        for id in ["a", "b", "c"] {
            graph
                .create_node(NodeRecord {
                    id: id.into(),
                    properties_json: None,
                })
                .unwrap();
        }
        graph
            .create_edge(EdgeRecord {
                id: "e_ab".into(),
                src: "a".into(),
                dst: "b".into(),
                label: Some("rel".into()),
                properties_json: None,
            })
            .unwrap();
        graph
            .create_edge(EdgeRecord {
                id: "e_bc".into(),
                src: "b".into(),
                dst: "c".into(),
                label: Some("rel".into()),
                properties_json: None,
            })
            .unwrap();

        // Vector projection lives inside E3 (no E1 dependency).
        graph.project_vector("a", vec![1.0, 0.0]).unwrap();
        graph.project_vector("b", vec![0.0, 1.0]).unwrap();
        graph.project_vector("c", vec![-1.0, 0.0]).unwrap();

        // Projections must survive a WAL replay.
        let engine = InMemoryGraphEngine::new("default", wal);
        engine.recover().unwrap();

        let params = FusionParams {
            anchor_top_k: 1,
            hop: 2,
            ..FusionParams::default()
        };
        let result = engine
            .vector_anchored_subgraph(&[1.0, 0.0], params, &GraphFilter::default())
            .unwrap();

        // Anchor is "a"; 2-hop expansion reaches a -> b -> c.
        assert_eq!(result.anchors.len(), 1);
        assert_eq!(result.anchors[0].node_id, "a");
        assert_eq!(result.subgraph.nodes.len(), 3);
        assert_eq!(result.subgraph.edges.len(), 2);
        assert!(!result.truncated);
    }

    #[test]
    fn fusion_truncates_on_node_cap() {
        let wal = Arc::new(MemoryWal::new());
        let graph = InMemoryGraphEngine::new("default", wal);
        for i in 0..5 {
            graph
                .create_node(NodeRecord {
                    id: format!("n{i}"),
                    properties_json: None,
                })
                .unwrap();
        }
        for i in 0..4 {
            graph
                .create_edge(EdgeRecord {
                    id: format!("e{i}"),
                    src: format!("n{i}"),
                    dst: format!("n{}", i + 1),
                    label: None,
                    properties_json: None,
                })
                .unwrap();
        }
        graph.project_vector("n0", vec![1.0, 0.0]).unwrap();

        let params = FusionParams {
            anchor_top_k: 1,
            hop: 10,
            fanout_cap: 100,
            max_nodes: 2,
            max_edges: 50_000,
        };
        let result = graph
            .vector_anchored_subgraph(&[1.0, 0.0], params, &GraphFilter::default())
            .unwrap();
        assert!(result.truncated);
        assert!(result.subgraph.nodes.len() <= 2);
    }

    #[test]
    fn fusion_filters_edge_label_and_node_property() {
        let wal = Arc::new(MemoryWal::new());
        let graph = InMemoryGraphEngine::new("default", wal);
        graph
            .create_node(NodeRecord {
                id: "a".into(),
                properties_json: Some(r#"{"kind":"doc"}"#.into()),
            })
            .unwrap();
        graph
            .create_node(NodeRecord {
                id: "b".into(),
                properties_json: Some(r#"{"kind":"doc"}"#.into()),
            })
            .unwrap();
        graph
            .create_node(NodeRecord {
                id: "c".into(),
                properties_json: Some(r#"{"kind":"user"}"#.into()),
            })
            .unwrap();
        // a -[likes]-> b, a -[blocks]-> c
        graph
            .create_edge(EdgeRecord {
                id: "e1".into(),
                src: "a".into(),
                dst: "b".into(),
                label: Some("likes".into()),
                properties_json: None,
            })
            .unwrap();
        graph
            .create_edge(EdgeRecord {
                id: "e2".into(),
                src: "a".into(),
                dst: "c".into(),
                label: Some("blocks".into()),
                properties_json: None,
            })
            .unwrap();
        graph.project_vector("a", vec![1.0, 0.0]).unwrap();

        let params = FusionParams {
            anchor_top_k: 1,
            hop: 1,
            ..FusionParams::default()
        };

        // Edge-label pushdown: only "likes" is traversed, so c is unreachable.
        let filter = GraphFilter::default().with_edge_labels(["likes".to_string()]);
        let r = graph
            .vector_anchored_subgraph(&[1.0, 0.0], params, &filter)
            .unwrap();
        let ids: Vec<&str> = r.subgraph.nodes.iter().map(|n| n.id.as_str()).collect();
        assert_eq!(ids, vec!["a", "b"]);

        // Node predicate: only kind == "user" nodes are kept (just c, but c is
        // unreachable under the "likes" filter, so combine with no edge filter).
        let filter = GraphFilter::default()
            .with_node_property_eq("kind", serde_json::Value::String("user".into()));
        let r = graph
            .vector_anchored_subgraph(&[1.0, 0.0], params, &filter)
            .unwrap();
        let ids: Vec<&str> = r.subgraph.nodes.iter().map(|n| n.id.as_str()).collect();
        assert_eq!(ids, vec!["c"]);
    }

    #[test]
    fn segment_freeze_fences_writes_and_migrate_clears_it() {
        let wal = Arc::new(MemoryWal::new());
        let graph = InMemoryGraphEngine::new("default", wal);
        graph
            .create_node(NodeRecord {
                id: "a".into(),
                properties_json: None,
            })
            .unwrap();

        let seg = SegmentId::new(GRAPH_SEGMENT_ID);
        assert_eq!(graph.list_segments().unwrap(), vec![seg.clone()]);

        // Migrate-complete requires a frozen segment.
        assert!(graph.on_migrate_complete(&seg, vec![]).is_err());

        // Freeze => mutations rejected with SegmentFrozen, reads still work.
        graph.freeze(&seg).unwrap();
        assert!(graph.freeze(&seg).is_err());
        let err = graph
            .create_node(NodeRecord {
                id: "b".into(),
                properties_json: None,
            })
            .unwrap_err();
        assert!(matches!(err, AetherError::SegmentFrozen(_)));
        assert!(graph.project_vector("a", vec![1.0]).is_err());
        assert!(graph.get_node("a").is_ok());

        // Migrate complete => writable again.
        graph
            .on_migrate_complete(&seg, vec![BlockId("blk-1".into())])
            .unwrap();
        graph
            .create_node(NodeRecord {
                id: "b".into(),
                properties_json: None,
            })
            .unwrap();

        // Failure path leaves the segment writable.
        graph.freeze(&seg).unwrap();
        graph.on_migrate_failed(&seg, "io error").unwrap();
        graph
            .create_node(NodeRecord {
                id: "c".into(),
                properties_json: None,
            })
            .unwrap();

        assert!(graph.freeze(&SegmentId::new("other")).is_err());
    }

    #[test]
    fn graph_migration_state_recovers_after_restart() {
        let wal = Arc::new(MemoryWal::new());
        let graph = InMemoryGraphEngine::new("restart", wal.clone());
        let segment = SegmentId::new(GRAPH_SEGMENT_ID);
        let migration = MigrationTaskId::new("graph-restart");
        graph
            .prepare_migration(&segment, &migration, Some(0))
            .unwrap();

        let recovered = InMemoryGraphEngine::new("restart", wal.clone());
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
                vec![BlockId("graph-block".into())],
            )
            .unwrap();
        let restarted = InMemoryGraphEngine::new("restart", wal);
        restarted.recover().unwrap();
        let completed = restarted.migration_status(&segment).unwrap();
        assert_eq!(completed.migration.route_epoch, 1);
        assert_eq!(completed.migration.block_ids[0].0, "graph-block");
    }
}
