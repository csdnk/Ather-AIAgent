//! Hand-written Rust mirror of `proto/aether_engine.proto` (FROZEN v0.1).
//!
//! The wire schema lives in the `.proto`; this crate keeps a lightweight,
//! serde-friendly mirror so M0 is not blocked on a tonic/protoc toolchain. The
//! two are kept in lockstep — message field names and shapes match the proto.
//!
//! Scope (M0 contract): 三态统一查询 API + Segment 内省接口 + Tier Migrate
//! Callback (IF-04 / IF-05).

use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Shared
// ---------------------------------------------------------------------------

/// Uniform control-plane acknowledgement.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ack {
    pub ok: bool,
    #[serde(default)]
    pub engine: String,
    #[serde(default)]
    pub segment_id: String,
    #[serde(default)]
    pub migration_id: String,
    #[serde(default)]
    pub route_epoch: u64,
    #[serde(default)]
    pub idempotent: bool,
    #[serde(default)]
    pub state: String,
    #[serde(default)]
    pub block_ids: Vec<String>,
    #[serde(default)]
    pub outcome: Option<String>,
}

// ---------------------------------------------------------------------------
// E1 · Vector
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VectorRecordMessage {
    pub id: String,
    pub values: Vec<f32>,
    pub graph_node_id: Option<String>,
    pub metadata_json: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchHitMessage {
    pub id: String,
    pub score: f32,
    pub graph_node_id: Option<String>,
    pub metadata_json: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CreateCollectionRequest {
    pub collection: String,
    pub dimension: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InsertVectorRequest {
    pub collection: String,
    pub records: Vec<VectorRecordMessage>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeleteVectorsRequest {
    pub collection: String,
    pub ids: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchVectorRequest {
    pub collection: String,
    pub query: Vec<f32>,
    pub top_k: usize,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchVectorResponse {
    pub hits: Vec<SearchHitMessage>,
}

/// Segment introspection row for P3 (IF-04).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SegmentStatMessage {
    pub segment_id: String,
    pub state: String,
    pub row_count: u64,
    pub size_bytes: u64,
    pub index_type: Option<String>,
    pub access_count: u64,
}

// ---------------------------------------------------------------------------
// E2 · Object
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ObjectMetaMessage {
    pub bucket: String,
    pub key: String,
    pub etag: String,
    pub size: u64,
    pub md5_hex: Option<String>,
    pub blake3_hex: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GetObjectRangeRequest {
    pub bucket: String,
    pub key: String,
    pub start: u64,
    /// Inclusive end; `None` reads to EOF.
    pub end: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListObjectsPagedRequest {
    pub bucket: String,
    pub prefix: String,
    pub max_keys: usize,
    pub continuation_token: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListObjectsPagedResponse {
    pub objects: Vec<ObjectMetaMessage>,
    pub is_truncated: bool,
    pub next_continuation_token: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UploadPartRequest {
    pub upload_id: String,
    pub part_no: u32,
    pub data: Vec<u8>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PartInfoMessage {
    pub part_no: u32,
    pub size: u64,
    pub etag: String,
}

// ---------------------------------------------------------------------------
// E3 · Graph + Fusion
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NodeRecordMessage {
    pub id: String,
    pub properties_json: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EdgeRecordMessage {
    pub id: String,
    pub src: String,
    pub dst: String,
    pub label: Option<String>,
    pub properties_json: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct SubGraphMessage {
    pub nodes: Vec<NodeRecordMessage>,
    pub edges: Vec<EdgeRecordMessage>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ProjectVectorRequest {
    pub node_id: String,
    pub vector: Vec<f32>,
}

/// Traversal / fusion predicate pushdown (design doc §5.3.3).
#[derive(Debug, Clone, Serialize, Deserialize, Default)]
pub struct GraphFilterMessage {
    pub edge_labels: Vec<String>,
    pub node_property_key: Option<String>,
    pub node_property_value_json: Option<String>,
}

#[derive(Debug, Clone, Copy, Serialize, Deserialize)]
pub struct FusionParamsMessage {
    pub anchor_top_k: u32,
    pub hop: u32,
    pub fanout_cap: u32,
    pub max_nodes: u32,
    pub max_edges: u32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VectorAnchoredSubgraphRequest {
    pub collection: String,
    pub query: Vec<f32>,
    pub params: FusionParamsMessage,
    pub filter: GraphFilterMessage,
}

/// VectorAnchoredSubgraph result. Per design doc §5 the read path is served by a
/// single engine (E3); `anchors` are the vector-search anchors and `truncated`
/// signals fanout / node / edge caps were hit before full expansion.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct VectorAnchoredSubgraphResponse {
    pub anchors: Vec<SearchHitMessage>,
    pub subgraph: SubGraphMessage,
    pub truncated: bool,
}

// ---------------------------------------------------------------------------
// Segment control · introspection + tier-migrate callback (IF-04)
// Mirrors `ae_kernel::SegmentControl`.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListSegmentsRequest {
    pub engine: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ListSegmentsResponse {
    pub segment_ids: Vec<String>,
    #[serde(default)]
    pub segments: Vec<SegmentInfo>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SegmentInfo {
    pub engine: String,
    pub segment_id: String,
    pub state: String,
    pub route_epoch: u64,
    pub block_ids: Vec<String>,
    pub active_migration_id: Option<String>,
    pub last_migration_id: Option<String>,
    pub last_outcome: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SegmentRef {
    pub segment_id: String,
    #[serde(default)]
    pub engine: String,
    #[serde(default)]
    pub migration_id: String,
    #[serde(default)]
    pub expected_route_epoch: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OnMigrateCompleteRequest {
    pub segment_id: String,
    pub new_block_ids: Vec<String>,
    #[serde(default)]
    pub engine: String,
    #[serde(default)]
    pub migration_id: String,
    #[serde(default)]
    pub expected_route_epoch: Option<u64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OnMigrateFailedRequest {
    pub segment_id: String,
    pub reason: String,
    #[serde(default)]
    pub engine: String,
    #[serde(default)]
    pub migration_id: String,
    #[serde(default)]
    pub expected_route_epoch: Option<u64>,
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fusion_response_serde_round_trip() {
        let resp = VectorAnchoredSubgraphResponse {
            anchors: vec![SearchHitMessage {
                id: "a".into(),
                score: 0.9,
                graph_node_id: Some("na".into()),
                metadata_json: None,
            }],
            subgraph: SubGraphMessage {
                nodes: vec![NodeRecordMessage {
                    id: "a".into(),
                    properties_json: Some(r#"{"k":1}"#.into()),
                }],
                edges: vec![EdgeRecordMessage {
                    id: "e1".into(),
                    src: "a".into(),
                    dst: "b".into(),
                    label: Some("rel".into()),
                    properties_json: None,
                }],
            },
            truncated: true,
        };
        let json = serde_json::to_string(&resp).unwrap();
        let back: VectorAnchoredSubgraphResponse = serde_json::from_str(&json).unwrap();
        assert_eq!(back.anchors.len(), 1);
        assert_eq!(back.subgraph.nodes.len(), 1);
        assert_eq!(back.subgraph.edges[0].src, "a");
        assert!(back.truncated);
    }

    #[test]
    fn migrate_callback_serde_round_trip() {
        let req = OnMigrateCompleteRequest {
            segment_id: "c/seg_000001".into(),
            new_block_ids: vec!["blk-1".into(), "blk-2".into()],
            engine: "vector/default".into(),
            migration_id: "migration-1".into(),
            expected_route_epoch: Some(7),
        };
        let json = serde_json::to_string(&req).unwrap();
        let back: OnMigrateCompleteRequest = serde_json::from_str(&json).unwrap();
        assert_eq!(back.segment_id, "c/seg_000001");
        assert_eq!(back.new_block_ids, vec!["blk-1", "blk-2"]);
        assert_eq!(back.expected_route_epoch, Some(7));

        let legacy: OnMigrateCompleteRequest =
            serde_json::from_str(r#"{"segment_id":"legacy","new_block_ids":["blk"]}"#).unwrap();
        assert!(legacy.engine.is_empty());
        assert!(legacy.migration_id.is_empty());
        assert_eq!(legacy.expected_route_epoch, None);
    }
}
