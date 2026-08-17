# Acceptance Checklist

## M0 第一阶段文档交付（合同第二条 / 第四条 第一阶段）

阶段窗口：2026-06-05 ~ 2026-07-31。完整索引见 `docs/M0_阶段交付清单与自测报告_v1.0.md`。

- [x] 需求分析文档 — `docs/P2_AetherEngine六态存储引擎需求文档_v1.0-0624.md`
- [x] 总体架构设计 — `docs/P2_AetherEngine总体架构设计文档_v1.0.md`
- [x] 第一阶段阶段方案（E1/E2/E3 技术方案）— `docs/P2_AetherEngine第一阶段阶段方案文档_v1.0.md`
- [x] 技术路线与实施计划 — `docs/P2_AetherEngine技术路线与实施计划_v1.0.md`
- [x] E4/E5/E6 调研报告 — `docs/P2_E4E5E6三态引擎调研报告_v1.0.md`
- [x] P1/P2/P3 接口依赖表 — `docs/P2_P1P2P3接口依赖表_v1.0.md`
- [x] API 接口文档 v0.1 — `docs/P2_AetherEngine_API接口文档_v0.1.md`
- [x] M0 交付清单与自测报告 — `docs/M0_阶段交付清单与自测报告_v1.0.md`

## M0 接口冻结（合同第二条 / 项目分解 §2.2.3 / §5.1）

`proto/aether_engine.proto` 标记 **FROZEN v0.1**，`ae-proto` Rust 镜像同步。

- [x] 三态统一查询 API（VectorService / ObjectService / GraphService / FusionService），对齐引擎已实现能力（delete / range / 分页 / multipart / 融合参数+过滤）。
- [x] Segment 内省接口（`VectorService.SegmentStats` + `SegmentControlService.ListSegments`，IF-04）。
- [x] Tier Migrate Callback（`SegmentControlService.{Freeze,Unfreeze,OnMigrateComplete,OnMigrateFailed}`，对齐 `ae_kernel::SegmentControl`）。
- [x] 字段号 / RPC 签名冻结规约（仅允许向后兼容新增，禁止重编号/删除）。
- [ ] tonic/protoc 代码生成接入（当前手写 Rust 镜像，M0 不阻塞）。

## M0 → M1 门禁

- [x] Rust workspace monorepo 可构建。
- [x] `EngineKind` 不再使用散乱字符串。
- [x] `EngineRegistry` 使用 `EngineInstanceId`。
- [x] `WalRecord v1` 支持结构化 payload。
- [x] `FileWal` 支持 magic、length、CRC、尾部截断。
- [x] `TierManager` capability trait 占位。
- [x] E1 WAL payload 可恢复，重启后可 search。
- [x] E2 ETag 使用 MD5 hex。
- [x] E2 metadata 默认 SQLite。
- [x] E2 object key 不直接映射为本地路径。
- [x] E3 Node/Edge WAL replay 可恢复。
- [x] 统一错误码 `P2Err_*`（附录 B，`P2ErrorCode` + `AetherError::code()`）。
- [x] OTel 可观测接入（`ae-telemetry`：`tracing` span，命名 `ae.<engine>.<op>` + `engine` tag；三引擎核心操作 + SegmentControl 已插桩；自带 `SpanCounter` subscriber 供测试/内省）。真实 OTel 导出经 `tracing-opentelemetry` 桥接到 P1 OTel 总线（IF-07），随真实 P1 接入。
- [ ] P1Wal / P1BlockRef 真实接入。
- [ ] CI 接入 GitHub Actions / GitLab CI。

## 三引擎统一：Segment 冻结 / 迁移回调（P2 → P3 接口契约）

文档 §2.2.2 / §2.2.3 / §3.6。`ae_kernel::SegmentControl` trait（`list_segments` /
`freeze` / `unfreeze` / `on_migrate_complete` / `on_migrate_failed`）。

- [x] `SegmentControl` trait 落地（`ae-kernel`），统一冻结 + 迁移回调契约。
- [x] E1：Sealed↔Frozen 状态切换，`on_migrate_complete` 原子换路由（`block_ids` + `route_epoch`），Frozen 段只读仍可查（§3.6 双缓冲）。
- [x] E2：bucket 级冻结，冻结期间 `put/delete/multipart` 拒写 `P2Err_SegmentFrozen`，迁移完成换 `route_epoch`。
- [x] E3：单图段 write fence，冻结期间 `create_node/create_edge/project_vector` 拒写，读不受影响（§5.6）。
- [x] 非法状态转换返回 `P2Err_Conflict`，未知段返回 `P2Err_NotFound`。
- [x] `SegmentControlService` 已注册到 P2 gRPC `50052`，支持 E1/E2/E3 engine scope 路由。
- [x] Frozen、`route_epoch`、`block_ids` 与迁移任务状态已持久化到 `FileWal`，服务启动时自动恢复。
- [x] `migration_id` 幂等、活动任务冲突和 `expected_route_epoch` 过期回调保护已有状态机与重启测试覆盖。

## E1 下一阶段

- [x] 接入 HNSW（纯 Rust，`hnsw_index.rs`，recall@10 ≥ 0.8 对照测试）。
- [x] Growing / Sealing / Sealed Segment 状态机（`segment.rs` + `SegmentState`）。
- [x] `VectorIndex` trait 抽象（Flat / HNSW 可切换，公共 API 不变）。
- [x] SegmentStats 内省接口（`segment_stats`，供 P3）。
- [x] 删除 + tombstone（delete bitmap）+ 查询过滤 + WAL 恢复（`delete`，文档 §3.3/§3.4.2）。
- [x] IVF-PQ 第二索引（纯 Rust，`ivfpq_index.rs`：k-means 粗量化 + PQ 子量化 + ADC 查表）。**合同 M-MVP 验收门禁 recall@10 ≥ 0.92 对照测试通过**。
- [x] IVF-PQ 参数随集合规模自适应（`IvfPqParams::adaptive`：nlist≈√n、nprobe≈nlist/2 封顶 128、子向量≈2 维）。自适应 build 路径同样通过 recall ≥ 0.92 回归测试。
- [ ] 10 万条 768 维向量插入与 topK 查询（规模化压测）。
- [ ] IVF-PQ 编码 + 码本序列化落 P1 block + 异步建索引。

## E2 下一阶段

- [x] `ObjectBackend` trait + `LocalFsBackend`（数据面可替换）。
- [x] Multipart Upload（create / upload_part / complete / abort / list_parts）。
- [x] Range GET（`get_object_range`，inclusive end）。
- [x] ObjectList 分页（`list_objects_paged`，max_keys / continuation_token，LIST v2 语义）。
- [ ] S3 子集网关：PUT/GET/HEAD/DELETE/LIST（P4 联调）。
- [ ] Multipart 元数据持久化到 SQLite + P1 WAL（当前在内存）。
- [ ] 1GB 文件上传与预签名直传验证。
- [ ] SeaweedFS 后端适配（`SeaweedFsBackend` 实现 `ObjectBackend`）。

## E3 下一阶段

- [x] Fusion Vector Projection（节点向量列 + WAL 恢复，`project_vector`）。
- [x] VectorAnchoredSubgraph 单读路径（锚点检索 + k-hop + fanout_cap + truncated / ResultTooLarge），不在线调 E1（对齐设计文档 §5）。
- [x] `GraphFilter`：edge label 下推过滤 + node 属性谓词过滤（§5.3.3 edge_filter/node_filter）。
- [ ] OpenCypher 子集解析（把 `GraphFilter` 接到 MATCH/WHERE/RETURN/LIMIT 文本查询）。
- [ ] RocksDB / Kùzu 邻接表替换内存 `GraphState`。
- [ ] 1 万节点、10 万边写入与 1/2/3-hop 遍历压测。
- [ ] OpenCypher 子集解析（MATCH/WHERE/RETURN/LIMIT）。
