use ae_wal::MemoryWal;
use e1_vector::{CollectionSpec, InMemoryVectorEngine, VectorRecord};
use std::sync::Arc;

fn main() -> ae_common::Result<()> {
    let wal = Arc::new(MemoryWal::new());
    let vector = InMemoryVectorEngine::new("cli", wal);

    vector.create_collection(CollectionSpec {
        name: "quickstart".into(),
        dimension: 2,
    })?;

    vector.insert(
        "quickstart",
        vec![
            VectorRecord {
                id: "a".into(),
                values: vec![1.0, 0.0],
                graph_node_id: Some("node-a".into()),
                metadata_json: None,
            },
            VectorRecord {
                id: "b".into(),
                values: vec![0.0, 1.0],
                graph_node_id: Some("node-b".into()),
                metadata_json: None,
            },
        ],
    )?;

    let hits = vector.search("quickstart", &[1.0, 0.0], 2)?;
    println!("top hits: {hits:?}");
    Ok(())
}
