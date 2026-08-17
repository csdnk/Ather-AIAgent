//! AetherEngine observability surface (contract M-MVP "OTel 接入 P1").
//!
//! Engines instrument their operations with `tracing` spans whose names and
//! fields follow OpenTelemetry semantic conventions. `tracing` is the standard
//! Rust foundation for OTel: in production a `tracing-opentelemetry` layer
//! bridges these spans to P1's OTel bus (interface IF-07 resource/span/tag);
//! when no subscriber is installed the spans compile to near-zero-cost no-ops.
//!
//! To keep M0/M-MVP unblocked by an OTel collector deployment we ship a small
//! self-contained [`SpanCounter`] subscriber. It records per-span-name call
//! counts so tests and local introspection can assert that instrumentation
//! fires, and it doubles as a cheap metrics tap. Install it per-scope with
//! [`tracing::subscriber::with_default`], which is thread-local and therefore
//! safe under parallel tests.
//!
//! Span naming convention: `ae.<engine>.<operation>` (e.g. `ae.vector.search`,
//! `ae.object.put`, `ae.graph.fusion`, `ae.segment.freeze`).

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
use tracing::span::{Attributes, Id, Record};
use tracing::{Event, Metadata, Subscriber};

/// Engine tag used in span names / OTel `aether.engine` attribute.
pub mod engine {
    pub const VECTOR: &str = "vector";
    pub const OBJECT: &str = "object";
    pub const GRAPH: &str = "graph";
    pub const SEGMENT: &str = "segment";
}

/// A minimal `tracing` subscriber that counts spans by name.
///
/// It intentionally ignores events, field values and span enter/exit; it only
/// tallies span creation, which is exactly what we need to verify an operation
/// was instrumented and to expose coarse call-rate metrics.
#[derive(Default)]
pub struct SpanCounter {
    next_id: AtomicU64,
    counts: Mutex<HashMap<String, u64>>,
}

impl SpanCounter {
    pub fn new() -> Self {
        Self {
            // span Ids must be non-zero.
            next_id: AtomicU64::new(1),
            counts: Mutex::new(HashMap::new()),
        }
    }

    /// Number of spans created with `name` so far.
    pub fn count(&self, name: &str) -> u64 {
        self.counts
            .lock()
            .map(|m| m.get(name).copied().unwrap_or(0))
            .unwrap_or(0)
    }

    /// Total spans created across all names.
    pub fn total(&self) -> u64 {
        self.counts.lock().map(|m| m.values().sum()).unwrap_or(0)
    }

    /// Snapshot of all (span name -> count) pairs.
    pub fn snapshot(&self) -> HashMap<String, u64> {
        self.counts.lock().map(|m| m.clone()).unwrap_or_default()
    }
}

impl Subscriber for SpanCounter {
    fn enabled(&self, _metadata: &Metadata<'_>) -> bool {
        true
    }

    fn new_span(&self, span: &Attributes<'_>) -> Id {
        let name = span.metadata().name().to_string();
        if let Ok(mut counts) = self.counts.lock() {
            *counts.entry(name).or_insert(0) += 1;
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        Id::from_u64(id)
    }

    fn record(&self, _span: &Id, _values: &Record<'_>) {}
    fn record_follows_from(&self, _span: &Id, _follows: &Id) {}
    fn event(&self, _event: &Event<'_>) {}
    fn enter(&self, _span: &Id) {}
    fn exit(&self, _span: &Id) {}
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn counts_spans_by_name_within_scope() {
        let counter = Arc::new(SpanCounter::new());
        tracing::subscriber::with_default(counter.clone(), || {
            let _a = tracing::info_span!("ae.vector.search").entered();
            let _b = tracing::info_span!("ae.vector.search").entered();
            let _c = tracing::info_span!("ae.object.put").entered();
        });
        assert_eq!(counter.count("ae.vector.search"), 2);
        assert_eq!(counter.count("ae.object.put"), 1);
        assert_eq!(counter.count("ae.graph.fusion"), 0);
        assert_eq!(counter.total(), 3);
    }
}
