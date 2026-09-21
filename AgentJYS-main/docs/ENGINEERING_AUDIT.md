# P3 系统框架工程化审计报告

- 审计日期：2026-08-19
- 审计范围：`src/aether_agent_memory/`、`src/aether_p4_simulator/`、`scripts/p3_service.py`、
  `contracts/`、`compose.yaml`、`pyproject.toml`、`tests/`、验收脚本
- 方法：逐文件代码审查（`文件:行号` 证据），不采信文档自述。

## 一、事实矩阵

| 能力 | 当前入口 | 真实实现 | Mock/内存实现 | 状态事实源 | 故障行为 | 测试证据 |
| --- | --- | --- | --- | --- | --- | --- |
| HTTP 服务 | `scripts/p3_service.py` 手写 `ThreadingHTTPServer` | 真实 HTTP | — | 进程内 `STATE` 字典 | 非 `RuntimeErrorBase` 异常统一 422 | `tests/unit/test_*` |
| Runtime 生命周期 | `p3_service.py:79-84` `with_runtime` | 真实 | **每请求重建并 close** | — | close 失败不处理 | — |
| Embedding(B1) | `runtime/legacy.py:82-111` | Sidecar 真实 | **sidecar_url 空→`MockEmbeddingClient(dim=32)`** | — | Mock 静默降级 | Mock 测试 |
| Working/Episodic/Semantic | `runtime/legacy.py:116-130` | — | **全 `Mock*MemoryManager`** | `RedisMemoryStore`(生产默认) | — | Mock 测试 |
| Context 构建 | `context/builder.py` `MockContextPackBuilder` | — | **Mock，且不含长文档/P2 E1** | — | 单源超时降级 | Mock 测试 |
| 长文本链路 | `b2/celery_app.py` | 真实 Celery+B1+P2+Milvus | — | `RedisTaskStatusStore` + `RedisMemoryStore` | B1/P2 失败留半成功态 | **零单测** |
| Task 状态 | `b2/task_status.py` | 真实 Redis | — | `b2:task:{id}` | `set` 无 SETNX、PROCESSING 写在 try 外 | 零单测 |
| 幂等 | `runtime/request_context.py:37` | — | **未实现**：b2 目录零出现 | — | 重复提交→重复写入 | 零单测 |
| 投影状态 | `core/memory.py` + metadata | 散落字符串 | — | **顶层与 metadata 互相矛盾** | 失败只改顶层 | 零单测 |
| AccessTrace | `adapters/access_trace.py` | — | **InMemory list** | — | 进程重启丢失 | Mock 测试 |
| Signal | `signal/emitter.py` | — | **MockSignalEmitter 内存 list** | — | 进程重启丢失 | Mock 测试 |
| B3 调度 | `b3/scheduler.py` + `executor.py` | 真实 gRPC | 逻辑块路由(非物理迁移) | 进程内存 `_feedback_by_action` | execute_status=SUCCESS 易误读 | Mock 测试 |
| health 探测 | `runtime/service.py:131` + adapters | 部分 | Milvus/Celery/B3 静态兜底 | — | 不主动探测 | Mock 测试 |
| OpenAPI | — | — | **不存在** | — | — | — |
| 统一 Retrieval | `memory/retrieval/service.py` | 已实现 | **未接线生产(骨架)** | — | — | 零集成测试 |

## 二、10 项重点验证结论

1. **每请求重建 Runtime** — ✅ 成立。`p3_service.py:79-84` 每请求 `from_config` + `close`。
2. **Context 不含长文档/P2 E1** — ✅ 成立。`MockContextPackBuilder` 只查 3 个 Mock manager。
3. **状态不一致** — ✅ 成立。`embedding_status` 顶层恒 pending、metadata 写 succeeded；三套状态词汇（TaskState/ProjectionStatus/散落字符串）。
4. **idempotency_key 无持久化** — ✅ 成立。b2 目录零出现；进 B2 前被丢弃。
5. **AccessTrace/Signal/ActionLog 不持久化** — ✅ 成立。全 InMemory/Mock list。
6. **production 允许 Mock** — ✅ 成立。`legacy.py:104` sidecar_url 空即 Mock；manager 全 Mock。
7. **health 主动探测** — ⚠️ 部分。P3/P2 探测真实；Milvus/Celery/B3 静态兜底。
8. **验收 COMPLETED 误判 PASSED** — ✅ 成立。`summarize_b1_b2_acceptance.py:106` `phase1 status=="COMPLETED"` 即 PASS；phase2 不检查指标阈值。
9. **OpenAPI/错误码/契约一致** — ❌ OpenAPI 不存在；`contracts/*.json` 无 schema/错误码。
10. **scope 隔离** — ⚠️ 基本真实（Redis 索引/SQLite WHERE/manager 二次校验），但 `user_id/tenant_id=None` 不过滤、session 仅对 working 生效。

## 三、审计结论（按严重度）

- **P0**：每请求重建 Runtime；production 静默 Mock；验收 COMPLETED→PASSED 误判；非 RuntimeErrorBase→422；长文档闭环断裂；幂等未实现；状态顶层/metadata 矛盾；AccessTrace/Signal/ActionLog 不持久化。
- **P1**：无 OpenAPI；profile 缺 integration；compose 无 profile 语义 + `AETHER_ENABLE_DEMO=true` 硬编码 + minio 默认密码；统一 Retrieval 未接线；B3 无 Shadow Mode；health 部分静态兜底；无 Outbox/Reconciler；并发控制非持久化；原文双写（`Memory.content` 存全文 + P2 E2 存原文，与 ADR-0003 矛盾）；异步长文本 Celery 链路零单元覆盖。
- **P2**：reliability.py 占位；contracts JSON 无 schema；Token Budget 过简；scope 两处宽松。
