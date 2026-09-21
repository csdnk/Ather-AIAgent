# Integration Tests

当前 crate 内部已经包含以下测试：

- `ae-wal`: MemoryWal replay、FileWal tail corruption truncation
- `ae-kernel`: EngineRegistry 使用 EngineInstanceId
- `e1-vector`: 写入 WAL 后恢复并 search
- `e2-object`: MD5 ETag、SQLite metadata 重启后可 Head/Get
- `e3-graph`: Node/Edge WAL replay 后 BFS

后续进入 M1 时增加 workspace 级测试：

1. `vector_object_graph_smoke.rs`
2. `restart_recovery.rs`
3. `wal_corruption.rs`
4. `fusion_vector_anchored_subgraph.rs`
5. `mock_p1_fault_injection.rs`
