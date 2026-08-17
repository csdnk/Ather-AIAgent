# AetherEngine Rust Monorepo

P2 · AetherEngine 的 Rust monorepo 工程骨架。目标是让 E1 向量引擎、E2 对象引擎、E3 图引擎在同一套公共底座上并行开发，避免各自实现 WAL、错误码、生命周期、Registry、P1 Mock 和恢复链路。

## 当前版本定位

这是 M0 → M1 过渡版骨架，已经把评审中要求进入 M1 前修掉的关键点纳入代码：

- `WalRecord v1`：结构化 WAL，包含 `EngineKind`、`EngineInstanceId`、`NamespaceId`、`OperationKind`、`Inline/BlockRef` payload、checksum。
- `FileWal`：magic + length + crc32 framing，支持尾部损坏识别并截断到最后一条有效记录。
- `EngineRegistry`：使用 `EngineInstanceId` 做 key，不再使用字符串。
- `TierManager`：以 capability trait 方式占位，不膨胀 `AetherEngine` 主 trait。
- `E1`：向量写入 WAL 中保存可恢复 payload，重启后可恢复并搜索。
- `E2`：默认 SQLite 元数据，ETag 使用 MD5 hex，本地路径使用 hash 映射，不直接拼接 object key。
- `E3`：Node/Edge 结构化 WAL，重启后可恢复 BFS 遍历。
- `Fusion`：保留 `VectorAnchoredSubgraph` proto 与服务占位。

## 目录结构

```text
aether-engine/
  crates/
    ae-common/          # 公共类型、错误码、EngineKind、ID、Tier、checksum
    ae-wal/             # MemoryWal、FileWal、WalRecord v1
    ae-kernel/          # AetherEngine trait、EngineRegistry、TierManager
    ae-p1-client/       # Mock P1 Block/WAL client
    ae-proto/           # v0.1 接口数据结构占位
  engines/
    e1-vector/          # 向量引擎，M1 用 FlatIndex，后续替换 HNSW
    e2-object/          # 对象引擎，SQLite metadata + local data backend
    e3-graph/           # 图引擎，M1 内存邻接表，后续替换 RocksDB
  services/
    ae-server/          # 本地 demo server / registry / fusion runner
    ae-cli/             # CLI smoke demo
  proto/
    aether_engine.proto # gRPC/protobuf v0.1 契约
  tests/
    integration/        # 集成测试说明
  docs/
    ACCEPTANCE.md
    M0_阶段交付清单与自测报告_v1.0.md   # 合同第一阶段评审入口
    P2_AetherEngine六态存储引擎需求文档_v1.0-0624.md
    P2_AetherEngine总体架构设计文档_v1.0.md
    P2_AetherEngine第一阶段阶段方案文档_v1.0.md
    P2_AetherEngine技术路线与实施计划_v1.0.md
    P2_AetherEngine_API接口文档_v0.1.md
    P2_P1P2P3接口依赖表_v1.0.md
    P2_E4E5E6三态引擎调研报告_v1.0.md
    NEXT_TASKS.md
    STRUCTURE.md
  scripts/
    dev_check.sh
```

## 快速验证

```bash
cd aether-engine
bash scripts/dev_check.sh
```

或手动执行：

```bash
cargo fmt --all -- --check
cargo test --workspace
cargo run -p ae-server
cargo run -p ae-cli
```

## 下一步

1. E1：将 `FlatIndex` 替换为 HNSW，保留当前 `VectorEngine` 接口不变。
2. E2：补 multipart 与 S3 gateway 对接，当前 core 已支持 Put/Get/Head/List/Delete。
3. E3：把内存 Store 替换为 RocksDB Store，保留 `GraphEngine` 接口不变。
4. 公共底座：接入 P1 `Storage Block API / WAL / Replication Group / Tiering Hook / OTel Bus`。
