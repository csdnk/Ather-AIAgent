# P3 系统框架工程化强化 — 交付报告

- 日期：2026-08-25
- 审计报告：见 `docs/ENGINEERING_AUDIT.md`
- 服务器回归：见 `docs/SERVER_REGRESSION_20260825.md`

## 1. 审计发现及严重程度

审计确认的 P0 缺口：每请求重建 Runtime、production 静默 Mock、验收 COMPLETED→PASSED
误判、非 RuntimeErrorBase→422、长文档闭环断裂、幂等未实现、投影状态顶层/metadata 矛盾、
AccessTrace/Signal/ActionLog 不持久化。完整事实矩阵见审计报告。

## 2. 修改后的系统架构

```
P4 Simulator ──HTTP──▶ FastAPI Host (aether_agent_memory.app)
                        │ lifespan 创建/关闭「单例」MemoryRuntime（不再每请求重建）
                        │ 异常处理：RuntimeErrorBase→映射 / ValueError→422 / 其他→500
                        ▼
                    MemoryRuntime（长期存活）
                    │ 幂等 claim（RedisIdempotencyStore）＋ AccessTrace（Redis zset）
                    ▼
              B1 / B2 / B3 → Adapter → P2 / Redis / Celery / Milvus
```

启动入口：`scripts/p3_service.py`（兼容）→ `uvicorn aether_agent_memory.app:create_app`。
配置：`AppSettings`（pydantic-settings，populate_by_name，production 启动校验）。

## 3. 完整文件变更清单

新增：
- `src/aether_agent_memory/app.py`（FastAPI host + 全部北向/内部路由）
- `src/aether_agent_memory/config/app_settings.py`（强类型配置 + profile 校验）
- `src/aether_agent_memory/persistence/idempotency.py`（Redis SET NX 幂等）
- `compose.production.yaml`（production override）
- `tests/unit/test_app_contract.py`、`test_app_settings.py`、`test_idempotency.py`
- `tests/unit/test_b1_openvino_production_binding.py`（OpenVINO INT8 production 绑定防回退）
- `docs/ENGINEERING_AUDIT.md`、`docs/ENGINEERING_DELIVERY.md`

修改：
- `scripts/p3_service.py`（main 改为 uvicorn 入口；保留全部兼容符号）
- `src/aether_agent_memory/runtime/dependencies.py`（RuntimeProfile 加 INTEGRATION，LOCAL 别名；加 idempotency_store）
- `src/aether_agent_memory/runtime/service.py`（幂等 claim + RedisAccessTrace 接线）
- `src/aether_agent_memory/adapters/access_trace.py`（加 RedisAccessTraceAdapter）
- `scripts/summarize_b1_b2_acceptance.py`（gate_status，COMPLETED 不再误判 PASS）
- `compose.yaml`（profile 环境变量、demo 默认关闭、minio 密钥可配）
- `Dockerfile`（B1 production 镜像可安装 `b1-accelerated`，包含 OpenVINO/INT8 运行时）
- `pyproject.toml`（主依赖加 fastapi + uvicorn）

## 4. 北向 API 兼容性

北向 V1 六个稳定路径全部保留（FastAPI 路由 + OpenAPI 覆盖），字段与成功状态码不变
（`202` long-text、`404` task 不存在、`422` 参数错误）。`/api/status` 额外增加 `app`
能力视图（profile/version/内存后端/维度/B1 是否配置），不泄露密钥。契约测试
`test_p3_northbound_contract.py` 继续通过。

## 5. 状态事实源与状态机

- Memory Fact 权威：`RedisMemoryStore`（生产默认）或 `SQLiteMemoryStore`。
- Task 状态：`RedisTaskStatusStore`（`b2:task:{id}`，`TaskState` 四态枚举）。
- 幂等：`RedisIdempotencyStore`（`p3:idempotency:{key}`，SET NX）。
- AccessTrace：`RedisAccessTraceAdapter`（`p3:access:{memory_id}` zset）。
- 状态机枚举已定义：`MemoryFactStatus`、`ProjectionStatus`（六态）、`FormationStatus`。
- 已知遗留：celery 长文本路径仍用散落字符串状态（顶层/metadata 有重复字段），未在
  本轮强制统一（见第 8 节）。

## 6. Demo / Integration / Production 差异

| 维度 | demo | integration | production |
| --- | --- | --- | --- |
| 真实 B1 | 允许 Mock | 允许 Mock | 强制（启动校验） |
| Demo 开关 | 可开 | 默认关 | 强制关（启动失败） |
| P2/Redis 必配 | 否 | 否 | 是（启动校验） |
| AccessTrace | InMemory | Redis | Redis |
| 幂等 | 无 | Redis | Redis |
| B1 验收后端 | 可 ONNX/Mock | 默认 ONNX | OpenVINO INT8，fallback 关闭 |

`AETHER_RUNTIME_PROFILE`（demo/integration/production，兼容 local/dev→integration）。
production 由 `AppSettings.validate_for_profile()` 强制，缺失即启动失败。

## 7. 测试命令与结果（含服务器纠偏后）

```
服务器 python -m ruff check src tests scripts benchmarks → All checks passed
服务器 python -m pytest -q                              → 235 passed, 2 skipped
服务器 python -m mypy src                               → 118 source files, no issues
本地 python -m pytest tests/unit -q                     → 232 passed, 1 skipped
真实依赖功能回归                                        → B1/B2/P2/Milvus/B3 闭环通过
```

服务器没有 Docker Compose V2，因此 Compose 测试明确跳过；Docker CLI、P2 gRPC、Redis、
Milvus、Celery、B1 Sidecar 的真实功能回归已执行。OpenVINO 详细结果、服务器新增缺陷及证据
路径见 `docs/SERVER_REGRESSION_20260825.md`。

## 8. 未解决问题与真实风险（工程化纠偏后）

审查纠偏已落地：Production profile 绑定 `AETHER_RUNTIME_PROFILE` 并 fail-closed（含环境变量
回归测试）；验收汇总补 `contract.status` + `effective_item_qps>=2000` + Working P99 门禁
（PASS/PASSED 兼容）；幂等改为 PROCESSING/SUCCEEDED/FAILED + payload_hash + cached_response +
租户/操作命名空间（业务失败可重试）；OpenAPI 由 Pydantic 模型生成完整 schema + 强制 Scope
（tenant/user/agent 缺失 → 422）；长文档召回融合后统一 Token Budget 裁剪；任务记录持久化
tenant/user/agent 并在读取时校验归属；P2 向量写入/失败路径正确设置 vector_projection_status/
embedding_status；ruff 覆盖 benchmarks 全量通过。

仍保留的未解决项：

- **P0 统一 Retrieval 生产接线**：`/api/v1/context` 仍走 MockContextPackBuilder + 追加式长文档
  召回（虽已统一 budget），`MemoryRetrievalService` + RecallSource 融合未切换为唯一召回入口
  （需真实依赖环境验证后切换）。
- **P1 投影状态词汇收敛**：顶层/metadata 已一致，但散落小写字符串（`pipeline_status` 等）未
  迁移到 `ProjectionStatus` 枚举（契约值变更风险，需谨慎决策）。
- **P1 原文双写**：长文本 `Memory.content` 保存全文（`celery_app.py:123`）同时 P2 E2 存原文，
  与 ADR-0003「原文只在 P2 E2、P3 只持引用」矛盾，无单一所有权/回收机制。
- **P1 异步链路覆盖仍不完整**：真实 Celery 长文本、B1、P2、Milvus 链路已完成服务器冒烟，
  Redis Store 生命周期已有回归测试；故障注入、重试、Outbox/Reconciler 仍缺生产级覆盖。
- **P2 Outbox/Reconciler 缺失**：无持久事件机制与投影对账器。
- **物理迁移未实现**：`P2MigrationExecutor` 仍为确定性逻辑块路由（代码已诚实标注
  `route_mode=logical`），不得描述为物理迁移成功。

## 9. 下一阶段最多五项任务

1. 在无其他训练任务的 CPU 独占窗口，基于已通过功能 smoke 的 OpenVINO INT8 绑定路径，
   按冻结口径重跑正式 2000 Effective Item QPS 验收。
2. 统一 RetrievalService 生产接线：把 `MemoryRetrievalService` + `P2E1RecallSource` 作为
   唯一召回入口替换「MockContextPackBuilder + 追加式长文档召回」，使 Working/Episodic/
   Semantic/Long-document 四源统一融合（长文档闭环已打通，此步是收敛到单一检索框架）。
3. 投影状态词汇收敛：把 celery 长文本路径的散落小写字符串（`pipeline_status` 等）迁移到
   `ProjectionStatus` 枚举，三套状态词汇归一（顶层/metadata 已一致，此步是枚举收敛）。
4. Outbox/Projection Reconciler：持久事件机制与投影对账器（P2）。
5. 原文双写治理：长文本 `Memory.content` 与 P2 E2 的存储所有权收口（P1）。
