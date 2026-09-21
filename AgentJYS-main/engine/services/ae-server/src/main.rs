use ae_kernel::{AetherEngine, EngineRegistry};
use ae_wal::MemoryWal;
use e1_vector::{CollectionSpec, InMemoryVectorEngine, VectorRecord};
use e2_object::LocalObjectEngine;
use e3_graph::{EdgeRecord, FusionParams, GraphFilter, InMemoryGraphEngine, NodeRecord};
use std::sync::Arc;

fn main() -> ae_common::Result<()> {
    let wal = Arc::new(MemoryWal::new());
    let registry = EngineRegistry::new();

    let vector = Arc::new(InMemoryVectorEngine::new("default", wal.clone()));
    let graph = Arc::new(InMemoryGraphEngine::new("default", wal.clone()));
    let tmp = tempfile::tempdir().expect("tempdir");
    let object = Arc::new(LocalObjectEngine::new_with_sqlite(
        "default",
        tmp.path(),
        wal.clone(),
    )?);

    registry.register(vector.clone())?;
    registry.register(object.clone())?;
    registry.register(graph.clone())?;

    vector.create_collection(CollectionSpec {
        name: "docs".into(),
        dimension: 3,
    })?;

    graph.create_node(NodeRecord {
        id: "n1".into(),
        properties_json: Some(r#"{"title":"alpha"}"#.into()),
    })?;
    graph.create_node(NodeRecord {
        id: "n2".into(),
        properties_json: Some(r#"{"title":"beta"}"#.into()),
    })?;
    graph.create_edge(EdgeRecord {
        id: "e1".into(),
        src: "n1".into(),
        dst: "n2".into(),
        label: Some("related_to".into()),
        properties_json: None,
    })?;

    vector.insert(
        "docs",
        vec![VectorRecord {
            id: "v1".into(),
            values: vec![1.0, 0.0, 0.0],
            graph_node_id: Some("n1".into()),
            metadata_json: None,
        }],
    )?;

    // VectorAnchoredSubgraph runs as a single read path inside E3: the vector
    // is projected onto the graph node, then anchor search + k-hop expansion
    // happen entirely in E3, with no online call to E1 (design doc §5).
    graph.project_vector("n1", vec![1.0, 0.0, 0.0])?;
    graph.project_vector("n2", vec![0.0, 1.0, 0.0])?;

    object.create_bucket("demo")?;
    let meta = object.put_object("demo", "hello.txt", b"hello aether")?;

    let fusion = graph.vector_anchored_subgraph(
        &[1.0, 0.0, 0.0],
        FusionParams {
            anchor_top_k: 1,
            hop: 1,
            ..FusionParams::default()
        },
        &GraphFilter::default(),
    )?;

    println!("AetherEngine demo server booted.");
    println!("registered engines: {:?}", registry.list()?);
    println!("object etag: {}", meta.etag);
    println!(
        "fusion anchors: {} (top={})",
        fusion.anchors.len(),
        fusion
            .anchors
            .first()
            .map(|a| a.node_id.as_str())
            .unwrap_or("-")
    );
    println!(
        "fusion subgraph: {} nodes, {} edges (truncated={})",
        fusion.subgraph.nodes.len(),
        fusion.subgraph.edges.len(),
        fusion.truncated
    );

    // Demonstrate common WAL replay lifecycle.
    vector.recover()?;
    graph.recover()?;
    object.recover()?;
    println!("recovery lifecycle passed.");

    Ok(())
}
