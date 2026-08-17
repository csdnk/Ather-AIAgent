# Monorepo Structure

## 公共底座

- `ae-common`: 所有公共类型、错误、ID、EngineKind、StorageTier。
- `ae-wal`: WAL 抽象与 Memory/File 实现。上层引擎只依赖 trait。
- `ae-kernel`: 引擎生命周期、Registry、TierManager capability。
- `ae-p1-client`: P1 Mock，用于 P1 接口未冻结阶段。
- `ae-proto`: v0.1 contract struct，占位 proto 见 `/proto/aether_engine.proto`。

## 引擎

- `e1-vector`: M1 使用 FlatIndex 验证闭环，M1.5 替换为 HNSW。
- `e2-object`: SQLite metadata + local object backend，后续接 S3 gateway / MinIO / SeaweedFS。
- `e3-graph`: 内存邻接表验证接口，后续替换 RocksDB Store。
