//! Verifies engine operations emit OTel-aligned `tracing` spans.
//!
//! This lives in its own integration-test binary on purpose: `tracing` caches
//! callsite "interest" globally, so unit tests in the lib (which run without a
//! subscriber) would poison the cache and hide spans. Here we install the
//! counter as the global default first, which rebuilds the interest cache.

use ae_telemetry::SpanCounter;
use ae_wal::MemoryWal;
use e1_vector::{CollectionSpec, IndexKind, InMemoryVectorEngine, VectorRecord};
use std::sync::Arc;

#[test]
fn vector_operations_emit_otel_spans() {
    let counter = Arc::new(SpanCounter::new());
    tracing::subscriber::set_global_default(counter.clone())
        .expect("global subscriber installs once");

    let wal = Arc::new(MemoryWal::new());
    let engine = InMemoryVectorEngine::with_config("t", wal, 100_000, IndexKind::Flat);
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
    engine.search("c", &[1.0, 0.0], 1).unwrap();
    engine.search("c", &[0.0, 1.0], 1).unwrap();
    engine.delete("c", vec!["v0".into()]).unwrap();

    assert_eq!(counter.count("ae.vector.insert"), 1);
    assert_eq!(counter.count("ae.vector.search"), 2);
    assert_eq!(counter.count("ae.vector.delete"), 1);
}
