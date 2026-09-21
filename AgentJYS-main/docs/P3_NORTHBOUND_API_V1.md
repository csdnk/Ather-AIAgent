# P3 北向接口冻结基线 v1

**状态**：Frozen  
**冻结日期**：2026-08-24  
**机器可读清单**：`contracts/p3-northbound-v1.json`

## 1. 冻结范围

P3 作为独立的 Intelligent Memory Service，对 P4、Agent、RAG 和 Workflow 提供记忆写入、
上下文构建、长文本任务与长期记忆检索能力。P4 只能通过 HTTP 使用这些能力，不导入 P3
内部 Python 模块，也不直接连接 Redis、Milvus、Celery 或 P2。

本次冻结的是 **v1 北向行为和兼容性**，不是禁止 P3 内部继续修复问题或优化性能。v1 接口
只允许增加可选字段；删除字段、改变字段含义、收紧原有合法输入或改变成功状态码均属于破坏性
变更，必须发布新版本。

## 2. 稳定接口

| 方法 | 路径 | P4 用途 | 成功响应 |
| --- | --- | --- | --- |
| `GET` | `/health` | 判断 P3 及依赖健康度 | `200` |
| `POST` | `/api/v1/memory/events` | 写入会话、用户、工具或 RAG 记忆事件 | `200` |
| `POST` | `/api/v1/context` | 推理前按作用域构建 ContextPack | `200` |
| `POST` | `/api/v1/b2/long-text` | 提交异步长文本记忆任务 | `202` |
| `GET` | `/api/v1/b2/tasks/{task_id}` | 查询异步任务状态 | `200` / `404` |
| `POST` | `/api/v1/b2/search` | 检索长期向量投影 | `200` |

`/api/v1/embeddings`、B1 状态、B3 候选/调度、Demo 和 Smoke 接口仍可供 P3 工程控制台
使用，但不属于 P4 v1 业务契约。P4 正常业务流程不应直接编排 B1/B2/B3。

## 3. 统一作用域

P4 调用写入、查询和长文本接口时应同时传递：

- `tenant_id`：公司或租户边界。
- `user_id`：最终用户边界。
- `agent_id`：Agent/应用边界。
- `session_id`：当前会话边界。
- `request_id`：单次请求标识。
- `trace_id`：跨 P4/P3/B1/B2/B3/P2 链路标识。

长文本还必须传递 `source_id`。重试同一业务写入时应复用 `idempotency_key`；当前 P3 已接收
该字段，但完整幂等存储仍作为后续工程项，P4 不应进行无限重试。

## 4. 标准业务顺序

### Agent 对话

1. P4 使用用户问题调用 `/api/v1/context`。
2. P4/Agent 使用返回的 `assembled_text`、`memory_refs` 和降级信息完成推理。
3. 回合结束后，P4 调用 `/api/v1/memory/events` 写入 `after_turn` 或明确的
   `user_memory`。
4. P4 向用户返回回答，同时保留 P3 返回的 `memory_id`、`request_id` 和 `trace_id`。

### 文档记忆

1. P4 调用 `/api/v1/b2/long-text`，收到 `202 + task_id`。
2. P4 轮询 `/api/v1/b2/tasks/{task_id}`。
3. 只有任务真实返回 `SUCCEEDED` 时，P4 才能显示处理完成；`PENDING`、`PROCESSING`、
   `FAILED` 必须原样呈现。

## 5. 降级与错误

- `422`：请求参数或作用域无效，P4 应修正请求，不自动重试。
- `409`：状态冲突或幂等冲突。
- `503`：P3 Busy/Timeout，可进行有上限的退避重试。
- `502`：P3 下游依赖不可用，P4 显示降级，不伪造成功。
- `500`：内部错误，记录 `request_id`/`trace_id` 后停止自动重试。

ContextPack 的 `status=degraded`、`complete=false` 或 `missing_sources` 不等于请求失败。
P4 可以继续回答，但必须在调试/运维视图中保留降级事实。

## 6. P2 边界

本冻结基线不修改 P2 Proto 或 P2 客户端。P4 不感知 P2 接口；P3 内部如何使用 P2、Redis、
Milvus、Celery 和 B1，均不改变上述 P4 业务契约。
