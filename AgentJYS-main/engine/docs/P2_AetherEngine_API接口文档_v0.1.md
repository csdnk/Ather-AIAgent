# P2 AetherEngine API 接口文档（v0.1 FROZEN）

> 源文件：`proto/aether_engine.proto` | Rust 镜像：`crates/ae-proto`  
> 合同：M0 冻结六态统一查询 API + Segment 内省 + Tier Migrate Callback  
> 包名：`aether.engine.v1` | 传输：gRPC（P4 负责 S3/REST 映射）

---

## 1. 冻结规约

1. 本文档 v0.1 字段号与 RPC 签名 **稳定**；
2. 后续版本仅允许 **向后兼容** 新增 message 字段或 RPC；
3. **禁止** 重编号、删除已有字段或变更 RPC 签名；
4. 错误码使用 `P2Err_*`（见 `ae-common::P2ErrorCode`），经 gRPC status 返回。

---

## 2. 服务一览

| Service | 引擎 | 说明 |
|---|---|---|
| VectorService | E1 | 向量 CRUD + ANN 检索 + Segment 统计 |
| ObjectService | E2 | S3 兼容子集 + Range + 分页 + Multipart |
| GraphService | E3 | 节点/边/向量投影/遍历 |
| FusionService | E3 | 向量-图融合单读路径 |
| SegmentControlService | 三引擎 | 内省 + Tier 迁移回调 |

---

## 3. VectorService（E1）

### 3.1 RPC 列表

| RPC | 请求 | 响应 | 说明 |
|---|---|---|---|
| CreateCollection | CreateCollectionRequest | CreateCollectionResponse | 建集合，指定 dimension |
| InsertVector | InsertVectorRequest | InsertVectorResponse | 批量写入 |
| DeleteVectors | DeleteVectorsRequest | DeleteVectorsResponse | tombstone 删除 |
| SearchVector | SearchVectorRequest | SearchVectorResponse | ANN 检索 top_k |
| SegmentStats | SegmentStatsRequest | SegmentStatsResponse | **IF-04 内省** |

### 3.2 核心消息

**VectorRecord**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 向量 ID |
| values | float[] |  embedding |
| graph_node_id | string? | 关联 E3 节点 |
| metadata_json | string? | 元数据 JSON |

**SearchHit**

| 字段 | 类型 | 说明 |
|---|---|---|
| id | string | 命中 ID |
| score | float | 相似度分数 |
| graph_node_id | string? | 关联节点 |

**SegmentStat**（IF-04）

| 字段 | 类型 | 说明 |
|---|---|---|
| segment_id | string | 段 ID |
| state | string | growing / sealing / sealed / frozen / archived |
| row_count | uint64 | 行数 |
| size_bytes | uint64 | 字节数 |
| index_type | string? | flat / hnsw / ivfpq |
| access_count | uint64 | 访问计数 |

---

## 4. ObjectService（E2）

### 4.1 RPC 列表

| RPC | 说明 |
|---|---|
| CreateBucket | 创建桶 |
| PutObject / GetObject / HeadObject / DeleteObject | 基础对象操作 |
| GetObjectRange | Range GET，inclusive end |
| ListObjects | 简单列举 |
| ListObjectsPaged | LIST v2 分页 |
| CreateMultipartUpload / UploadPart / CompleteMultipartUpload / AbortMultipartUpload / ListParts | 分片上传 |

### 4.2 核心消息

**ObjectMeta**

| 字段 | 类型 | 说明 |
|---|---|---|
| bucket, key | string | 定位 |
| etag | string | MD5 hex |
| size | uint64 | 字节数 |
| md5_hex, blake3_hex | string? | 校验 |

**ListObjectsPagedResponse**

| 字段 | 说明 |
|---|---|
| objects | 当前页 |
| is_truncated | 是否还有下一页 |
| next_continuation_token | 下一页 token |

---

## 5. GraphService（E3）

| RPC | 请求 | 响应 |
|---|---|---|
| CreateNode | NodeRecord | NodeRecord |
| CreateEdge | EdgeRecord | EdgeRecord |
| ProjectVector | ProjectVectorRequest | Ack |
| Traverse | TraverseRequest | SubGraph |

**TraverseRequest**：`start_node_id` + `depth`（k-hop）。

---

## 6. FusionService（E3 单读路径）

### 6.1 VectorAnchoredSubgraph

**请求 VectorAnchoredSubgraphRequest**

| 字段 | 说明 |
|---|---|
| collection | 集合名（上下文，锚点在 E3 内向量列） |
| query | 查询向量 |
| params | FusionParams |
| filter | GraphFilter |

**FusionParams**

| 字段 | 默认语义 | 说明 |
|---|---|---|
| anchor_top_k | 锚点数 | 余弦 Top-K 节点 |
| hop | 扩展跳数 | k-hop BFS |
| fanout_cap | 扇出上限 | 每节点最大邻居 |
| max_nodes | 子图节点上限 | 硬/软限制 |
| max_edges | 子图边上限 | 硬/软限制 |

**GraphFilter**

| 字段 | 说明 |
|---|---|
| edge_labels | 允许的边标签，空=不限 |
| node_property_key | 属性键 |
| node_property_value_json | 属性期望值 JSON |

**响应 VectorAnchoredSubgraphResponse**

| 字段 | 说明 |
|---|---|
| anchors | SearchHit 列表 |
| subgraph | SubGraph |
| truncated | 是否因上限截断 |

---

## 7. SegmentControlService（IF-04 + IF-06）

| RPC | 请求 | 响应 | 说明 |
|---|---|---|---|
| ListSegments | ListSegmentsRequest | ListSegmentsResponse | 枚举 segment_ids |
| Freeze | SegmentRef | Ack | 写栅栏 |
| Unfreeze | SegmentRef | Ack | 取消栅栏 |
| OnMigrateComplete | OnMigrateCompleteRequest | Ack | 迁移成功，换路由 |
| OnMigrateFailed | OnMigrateFailedRequest | Ack | 迁移失败，保留旧路由 |

**OnMigrateCompleteRequest**

| 字段 | 说明 |
|---|---|
| segment_id | 段 ID |
| new_block_ids | 新 P1 Block 路由 |
| engine | 引擎实例范围 |
| migration_id | P3 生成的迁移幂等 ID |
| expected_route_epoch | 可选；迁移开始时读取的路由版本，用于拒绝过期回调 |

**SegmentRef / OnMigrateFailedRequest** 同样追加 `engine`、`migration_id`、`expected_route_epoch`；旧客户端不发送新增字段时仍按原 RPC 语义处理。

**ListSegmentsRequest.engine** 示例：`vector/default`、`object/default`、`graph/default`。
`ListSegmentsResponse` 保留 `segment_ids`，并追加 `segments`，返回状态、`route_epoch`、`block_ids`、活动/最近迁移 ID 与结果。

---

## 8. 共享消息

**Ack**

| 字段 | 说明 |
|---|---|
| ok | true 表示控制面成功 |
| engine / segment_id | 实际命中的引擎范围与 Segment |
| migration_id | 本次迁移幂等 ID |
| route_epoch / block_ids | 当前原子路由版本与物理块路由 |
| idempotent | true 表示重复请求已按首次结果返回，未重复执行 |
| state / outcome | 当前生命周期状态与最近迁移结果 |

---

## 9. 错误码映射（附录）

| P2Err_* | 典型场景 |
|---|---|
| P2Err_InvalidArgument | 维度不匹配、参数非法 |
| P2Err_NotFound | collection/bucket/segment 不存在 |
| P2Err_Conflict | 非法 Segment 状态转换 |
| P2Err_SegmentFrozen | 冻结期写入 |
| P2Err_ResultTooLarge | 融合子图超硬上限 |
| P2Err_Unsupported | BlockRef 未接入等 |
| P2Err_Upstream | P1 调用失败 |

---

## 10. 版本与实现对照

| 组件 | 路径 | 说明 |
|---|---|---|
| Proto 源 | `proto/aether_engine.proto` | FROZEN v0.1 |
| Rust 镜像 | `crates/ae-proto/src/lib.rs` | serde 结构，与 proto  lockstep |
| 引擎 trait | `crates/ae-kernel/src/lib.rs` | SegmentControl |
| Demo | `services/ae-server` | 本地联调 |

> tonic/protoc 代码生成计划在 M-MVP 接入，不阻塞 M0 冻结。

---

## 11. 变更记录

| 版本 | 日期 | 说明 |
|---|---|---|
| v0.1 | 2026-06-25 | M0 冻结：三态 API + SegmentControl + 融合参数 |
