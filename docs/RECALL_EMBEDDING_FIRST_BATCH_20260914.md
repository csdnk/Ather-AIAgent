# Recall / Embedding 首批实现与字段对应说明

更新日期：2026-09-14。范围按本次确认的“先落实对象字段和首批可运行能力”执行。

本批完成 Recall 受理、Query 向量准备、纯 Query / Passage Embedding、投影与 P2 逻辑契约，以及可持久化的投影模拟器。Recall 当前停止在 `RUNNING_VECTOR_SEARCH`，返回 `PreparedQuery`；后续候选获取、正文读取、排序、最终复核与 ContextPack 交付尚未接通。

## 1. 依据与覆盖口径

依据工作区 `分工与项目理解/Recall流程/运行时详细设计_V0.1/` 下的《召回数据定义》《召回流程详细设计》《召回与P2接口对接需求》《跨模块待确认事项》，以及《Recall源码对照与开发顺序_2026-09-14》和 A 组 Recall 工作包。源码基线为 `f3eeb7b7e48fb22ddbc5dba8a020af9b8ebe6ce2`。

本批字段清单覆盖 **64 个对象或嵌套结构、477 个直接声明字段**，不重复计算继承的公共头；这不是全部后续阶段的实现数量。[字段清单](recall_contract_field_manifest.json) 逐对象列出源码文件及字段名；[契约测试](../tests/unit/recall/test_contracts.py) 检查这些字段确实存在且保留必填性。

字段名称对应文档不代表 P2 已提供同名 Proto 字段。`p2/contracts.py` 是逻辑契约，实际协议映射与能力确认仍须 P2 联调。

## 2. 对象、字段与代码的对应关系

| 对象/位置 | 本批字段或约束 | 实现用途 |
|---|---|---|
| [公共值类型](../src/aether_agent_memory/runtime/contract_types.py) | `Scope` 的 `tenant_id/project_id/agent_id/session_id/task_id`；`Hash`；`ByteRange`；公共头 | 统一强类型、UTC 毫秒时间、半开字节范围和 JCS/SHA-256 摘要；可空必填字段必须显式传入 `None` |
| [RecallRequest](../src/aether_agent_memory/memory/recall/models.py) | `request_ref/query/scope/principal_ref/authorization_ref/idempotency_key/request_fingerprint/deadline_at`；`retrieval_mode/source_selection/retrieval_constraints`；模型空间和 tokenizer/template 版本 | 三种选路由受理层根据授权推导；客户端不能指定内部选路结果 |
| 同文件 `RecallRequestIndex` | `principal_ref/scope_digest/idempotency_key/request_fingerprint/recall_id/request_ref` | 同租户、主体、scope、幂等键下固定一次受理 |
| 同文件 `RecallExecution` | `state/state_version`、租约、`checkpoint_refs/query_embedding_call/read_ledger/finalization/retention` | 持久化阶段状态、共享计算绑定和读取尝试；后续终结结构已定义，终结协调器待实现 |
| 同文件 `RecallCheckpoint/QueryEmbeddingResult` | `input_digest/output_ref/output_digest`；`vector_ref/usage/model_id/model_version/dimension/dtype/source_hash/retrieval_space_ref/source_evidence_ref` | 保存已校验阶段输出，恢复时检查摘要和输入绑定 |
| [SemanticEmbeddingRequest](../src/aether_agent_memory/b1/semantic/models.py) | `caller_ref/caller_request_ref/authorization_ref/usage/input_ref/source_hash/input_binding_digest/input_binding_ref/model_binding/reuse_digest/deadline_at/execution_policy_ref` | 固定 Query 或 B 批准的 Passage 输入、模型空间与用途；绑定共享执行 |
| 同文件 `EmbeddingModelBinding` | `model_id/model_version/dimension/dtype/embedding_schema_version/preprocessing_version/retrieval_space_ref/model_contract_ref` | 校验实际模型输出是否满足调用契约 |
| 同文件 `SemanticEmbeddingExecution/Result` | 执行状态、尝试次数、租约；`vector_ref/vector_hash/validation_evidence_ref/validated_at` | 原子保留计算尝试与结果，完成后才允许缓存复用 |
| [VectorProjectionRequest/Operation](../src/aether_agent_memory/memory/vector_projection/models.py) | 投影身份、目标、payload、授权/操作绑定及恢复字段 | 本批完成结构与端口；生产投影协调器待实现 |
| 同文件 `ProjectionPayload/Metadata/TargetBinding` | 向量、内容版本/范围、表示身份、作用域、模型绑定及摘要 | 保留 B 批准的目标与正文关联；不能把任意 chunk_id 当作完整投影身份 |
| 同文件 `ProviderResult` | `state/raw_status/object_present/index_queryable/binding_evidence_ref/completion_evidence_ref/error_code/retry_advice/result_digest` | A 输出机制观察；READY 必须有完成证据，不能直接映射 ACK；不会修改 B 的领域 Ready |
| [P2 逻辑 DTO](../src/aether_agent_memory/p2/contracts.py) | 公共调用/响应上下文、检索、正文读取、投影写入、操作查询、目标查询、删除等输入输出 | 26 个结构覆盖 P2 首批逻辑字段，包含证据、实际绑定及范围语义 |
| 既有 [core.Scope](../src/aether_agent_memory/core/scope.py) 与 [RequestContext](../src/aether_agent_memory/runtime/request_context.py) | 新增 `project_id`，贯穿映射、序列化和子上下文 | `user_id` 与 `project_id` 分开；旧服务尚无项目过滤时显式拒绝 project scope，避免静默扩大范围 |
| 既有 [P2GrpcClient](../src/aether_agent_memory/p2/client.py) | `P2ObjectMeta.md5_hex/blake3_hex`；新增 `P2ObjectBytes.data/meta` 与 `get_object_result()` | 保留正文响应中的元信息及 Proto 可选字段缺省状态；旧 `get_object()` 仍返回 bytes/None；etag 不冒充内容哈希或 B 版本 |
| 既有 [B1 Sidecar](../src/aether_agent_memory/b1/sidecar.py) | `InterceptItem.preserve_input`；响应 `input_type` | 新路径保持单段原文及空白，超限先拒绝；旧默认分段路径保持可用 |

## 3. 首批可运行链路

### Recall

入口是 [RecallAdmissionService.admit](../src/aether_agent_memory/memory/recall/admission.py) 和 [RecallQueryService.prepare](../src/aether_agent_memory/memory/recall/query.py)。

1. `RecallInput` 经可信 `RecallAuthorizationPort` 验证，推导 `working_only/long_term_only/combined`，原子建立请求、索引、执行及事件容量预留。授权等待计入总超时。
2. [RecallQueryInputAdapter](../src/aether_agent_memory/memory/recall/embedding_input.py) 从已受理记录读取原始 Query，并根据当前授权复核主体、范围与输入绑定。
3. 长期召回路径绑定共享计算，校验向量用途、模型、输入、维度和向量摘要，生成 `QueryEmbeddingResult` 与 `P2SearchInput`。Working-only 跳过 Embedding，两者为空。
4. 保存 checkpoint，结算本阶段读取尝试，推进到 `RUNNING_VECTOR_SEARCH`。该状态仅表示下一阶段入口就绪，不表示已经调用 P2 或召回完成。

重复请求保持原请求语义、原 policy 和原选路；权限撤销后拒绝访问。扩大权限不会扩大原请求选路。调用方取消不会取消已共享的推理任务。

### Embedding

[SemanticEmbeddingService](../src/aether_agent_memory/b1/semantic/service.py) 通过 `EmbeddingInputPort` 获取已授权固定输入，通过 `EmbeddingRuntime` 调用计算后端。Query / Passage 使用独立后端实例和准入配额。只允许显式 `TransientEmbeddingError` 在原预算内重试，默认最多两次；当前退避为固定间隔，随机抖动留待部署策略补齐。

输出拒绝错误模型绑定、错误维度、布尔值、NaN/Inf、全零及 dtype 溢出；向量按小端 float32/float64 固定字节编码计算摘要。服务不执行分段、Memory 写入或 P2 投影写入。

[BoundSidecarBackend](../src/aether_agent_memory/b1/semantic/sidecar.py) 可连接既有 `/v1/intercept`，显式传 `preserve_input=true` 和 `input_type`，核验实际响应的模型哈希、schema、用途、单段文本与范围，并保留原始响应证据。部署须提供经确认的模型/预处理契约和对应 tokenizer；适配器不会从裸向量推断这些保证。

### 投影

[StatefulVectorSimulator](../src/aether_agent_memory/p2/simulator.py) 支持持久化写入、丢失写入响应后查询原操作、显式索引可见性推进、目标核验、删除 tombstone 与迟到写阻断。它用于契约验证，未注册为生产 P2。

A 的 [VectorProjectionPort](../src/aether_agent_memory/memory/vector_projection/ports.py) 只定义机制提交/查询边界。B 的 Ready 判定、投影批准及最终领域状态变更仍由 B 提供。

## 4. 如何调用

环境要求 Python 3.13，安装项目及开发依赖：`python -m pip install -e ".[dev]"`。新增加的运行依赖为 `rfc8785`。

以下为应用内装配方式，传入实际授权适配器、模型 runtime 和规范化 `RecallInput`：

```python
from aether_agent_memory.b1.semantic.service import SemanticEmbeddingService
from aether_agent_memory.memory.recall.admission import RecallAdmissionService
from aether_agent_memory.memory.recall.embedding_input import RecallQueryInputAdapter
from aether_agent_memory.memory.recall.query import RecallQueryService
from aether_agent_memory.runtime.capability_store import SQLiteCapabilityStore

async def prepare_query(raw, authority, recall_policy, query_runtime, passage_runtime):
    store = SQLiteCapabilityStore("recall-state.db")
    admission = RecallAdmissionService(store, authority, recall_policy)
    inputs = RecallQueryInputAdapter(admission, caller_ref="recall-A")
    embedding = SemanticEmbeddingService(store, inputs, query_runtime, passage_runtime)
    query = RecallQueryService(
        admission, embedding, query_runtime.binding,
        caller_ref="recall-A", execution_policy_ref="embedding-policy-0.1",
    )
    try:
        return await query.prepare(raw)
    finally:
        await embedding.close()
        store.close()
```

上述输入适配器仅授权 Query。Passage 使用 B 提供的 `EmbeddingInputPort`，由它确认片段及正文版本/范围，再用 `make_embedding_request(usage="Passage", ...)` 调用同一计算实现。部署时应在应用生命周期内复用服务；示例按单次调用展示资源释放。

无需外部服务即可运行的装配样例与断言见 [test_query.py](../tests/unit/recall/test_query.py)、[test_embedding.py](../tests/unit/recall/test_embedding.py)、[test_provider.py](../tests/unit/recall/test_provider.py)。这些测试的确定性后端仅用于验证，没有替换生产模型。

## 5. 验证记录

本机 Python 3.13.15；测试环境位于工作区 `outputs/recall-venv`。

| 检查 | 结果 |
|---|---|
| `python -m pytest -q --ignore=tests/integration/test_compose_stack.py --tb=short` | 提交前复验：528 passed，2 skipped；包含新增的 53 项专项测试与 1 项 Sidecar 回归 |
| `tests/unit/recall/test_provider.py` | 7 passed，包含有/无 meta 及旧 bytes 接口兼容测试 |
| `python -m ruff check src tests scripts benchmarks` | 通过 |
| `python -m mypy src` | 新代码类型问题已修复；仍有既有 P2 generated 包缺少类型声明的 1 项 `import-untyped` 错误，不能称全量通过 |

修改前基线为 474 passed、2 skipped、1 failed；唯一失败是 Docker Desktop Linux engine 未运行导致 Compose 启动失败。因此上表全仓回归明确排除了该文件，没有改测试来隐藏失败。真实 B/P2、模型部署、硬件隔离与压力指标均未在本批验证。

## 6. 下一批需要补的模块

| 模块/边界 | 需要补充的实现或外部能力 |
|---|---|
| B 授权与候选接口 | Working 候选、候选资格、准确正文版本/范围/校验依据、最终复核；Passage 输入批准与解析 |
| P2 协议适配 | 将逻辑 DTO 映射为已确认 RPC；核实 scope/type/time 检索过滤、准确目标绑定、操作查询、索引可见性和删除屏障证据 |
| Recall 后续阶段 | 候选集合、资格核验、正文加载校验、排序预算、上下文组装、最终复核、trace/outbox 与终结发布 |
| 投影协调器 | B 批准后调用 P2，持久化原操作绑定，查询未知结果，核验准确目标，输出机制结果交 B |
| 生产状态存储 | 用 RF 原子/CAS/租约适配器替换 SQLite 参考实现；补齐终结恢复、数据留存清理和容量回收 |
| 运行部署 | 新链路 HTTP/worker 入口和生命周期装配、真实 tokenizer 与模型契约、独立 Query/Passage 资源、指标、压测与故障联调 |

SQLite 实现使用 WAL 与原子事务，不等价于分布式 RF。中断后不确定的计算保留原租约/尝试，等待原 deadline，不盲目重复推理；完整恢复与补偿仍待下一批。Recall 首批尚无终结清理，受理容量预留不会自动完成回收。新链路未接入现有生产 bootstrap，避免把阶段性准备结果当作完整 Recall 返回。

## 7. 开发分支与已知设计差异

本批通过 `codex/recall-embedding-first-batch` 开发分支交付，目标基线为 `main`。本批为阶段性实现，不具备完整 Recall 生产验收条件；后续评审与合并前仍须完成相应质量检查。

| 文档要求 | 当前差异与后续修改 |
|---|---|
| Query 失败时记录长期来源缺失；combined 在预算允许时继续 Working | 当前 `RecallQueryService.prepare` 会抛出 Embedding 异常；需补 Recall 错误码映射、来源失败记录与 Working 继续执行分支 |
| P-10 为复核与可靠收尾预留时间 | `finalization_reserve_ms` 已定义，尚未从 Query 调用可用时间中扣除；需接入阶段预算管理 |
| P-11/P-12 分别限制运行数与排队数 | 当前仅限制两者之和；需补实际调度、排队与终结后的容量回收 |
| EM-04 使用指数退避与有界 jitter | 当前为固定间隔退避；需补有界随机抖动 |

以上差异与尚未实现的后续阶段分开记录，不能用字段覆盖或单元测试通过替代全文档流程验收。
