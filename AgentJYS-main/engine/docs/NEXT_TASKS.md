# Next Tasks

## 本周：公共底座冻结

1. 确认 `WalRecord v1` 字段和序列化格式。
2. 确认 `EngineInstanceId` 作为 Registry key。
3. 确认 `TierManager` capability trait，不直接塞满 `AetherEngine` 主 trait。
4. 确认 proto 字段编号 v0.1，不复用字段号。

## E1 负责人

1. 保留 `VectorEngine` API。
2. 把 `hnsw_index.rs` 替换为 HNSW 实现。
3. 完成 10 万条向量写入、重启、搜索测试。
4. 设计 Segment seal 和异步建索引流程。

## E2 负责人

1. 在当前 SQLite metadata 上补 S3 gateway 层。
2. 增加 Multipart 元数据表。
3. 增加原子写入流程测试。
4. 增加本地 data backend 与 SeaweedFS backend trait。

## E3 负责人

1. 增加 `GraphStore` trait。
2. 用 RocksDB 替换当前内存 `GraphState`。
3. 保持 `GraphEngine` API 不变。
4. 完成 E1 hit 的 `graph_node_id` 到 E3 BFS 的 fusion demo。

## 测试负责人

1. 加 workspace 级重启恢复测试。
2. 加 WAL 尾部损坏测试。
3. 加 MockP1 故障注入测试。
4. 把 `cargo fmt`、`cargo clippy`、`cargo test --workspace` 接到 CI。
