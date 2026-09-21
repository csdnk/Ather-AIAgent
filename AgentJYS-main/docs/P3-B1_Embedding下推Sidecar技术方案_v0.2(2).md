# P3-B1 Embedding 下推 Sidecar 技术方案文档

**版本**：v0.2  
**适用范围**：P3-B1 Embedding 下推 Sidecar 架构设计、写入链路拦截、文本分块、CPU/ONNX/FastEmbed 向量化、P2-E1/E2 对接、B2 记忆链路协同、异常降级与后续性能演进  
**设计重点**：以 B1 存储侧旁路向量化为核心，重点覆盖 B1 自身以及 P4、B2、Redis/Celery、Milvus/P2-E1/E2 等依赖异常下的识别、降级、重试、熔断、幂等和补偿机制，保证 B1 故障不会拖死 P3 主服务和 Agent/RAG 主链路。  
**修订说明**：v0.2 参考 B3 技术方案文档结构，将内容从“内部流程说明”调整为“目标约束-架构设计-接口设计-处理流程-容错设计-部署测试-演进设计”的技术方案结构；同时以《P3-B1 Embedding 下推 Sidecar 方案对应性核对报告》为准，明确当前融合版只能判定为“核心 MVP 链路基本对应，工程契约和完整容错验收部分对应”，不能认定为已与方案完全对应。

---

## 0. 当前对应性结论

根据 2026-08-10 对应性核对报告，当前融合版项目的判定口径如下：

**总体结论**：融合后的项目不能认定为与 B1 技术方案完全对应。可以判定为：**核心 MVP 链路基本对应，工程契约和完整容错验收部分对应。**

已基本对应的范围：

- B1 HTTP Sidecar：FastAPI、`/v1/intercept`、health、metrics、基础批处理。
- CPU Embedding：FastEmbed + ONNX Runtime CPU，默认 BGE 中文模型。
- 输入校验与跳过：必填字段、二选一文本、空文本、大小限制、`skipped`。
- B2 长文本链路：P2-E2 保存原文 -> Redis/Celery -> B1 -> P2-E1。
- P2/Milvus 投影：P2-E1 默认写入，Milvus 可选投影。
- Query embedding 与 P2-E1 查询隔离已有基础链路。

仍不能判定为完全对应的范围：

- 已分块输入仍可能被 B1 二次分块。
- B1 直接响应缺少完整 chunk metadata。
- 幂等缓存只有容量淘汰，没有 TTL、跨进程和持久化能力。
- 缺少熔断、租户限流和完整错误码体系。
- Compose 中 `p3`、`celery-worker` 仍依赖 `b1-sidecar` healthy，B1 readiness 异常可能阻断 P3/B2 启动。
- Query embedding 没有在 B1 侧强制单向量，也没有 query 优先级队列。
- events 和 metrics 低于方案验收 schema，缺少模型加载耗时、docs/s、chunks/s、vectors/s、熔断状态等。
- 仍保留 32 维 Mock fallback，正式验收必须禁止。

## 1. 背景、目标与设计原则

### 1.1 背景

P3 是语义层与智能调度模块，B1 是其中的 Embedding 下推模块。B1 位于 P4 业务写入入口、P2 对象/向量引擎和 B2 Agent 记忆管理器之间，负责把进入系统的文本、文档片段、记忆片段、RAG 证据和工具结果转换为可检索、可追踪、可调度的向量资源。

传统方案通常在应用侧集中调用 Embedding 服务，存在以下问题：

1. 应用侧重复接入模型，接口不统一，难以控制向量维度、模型版本和 metadata 规范。
2. 长文本集中向量化容易阻塞 Agent/RAG 前台请求，导致用户感知延迟抖动。
3. 向量、原文、chunk、object、tenant/user/agent/session 之间缺少统一绑定，后续 B2 召回和 B3 调度难以追踪。
4. Embedding 失败、模型加载失败、队列拥塞和 P2 写入失败缺少统一状态，容易把局部故障扩大成全链路故障。

因此，B1 的定位不是一个普通 Embedding SDK，而是 P3 写入链路中的“语义化旁路服务”：在不侵入 P2/P4 主链路的前提下，对文本类数据进行识别、分块、向量化、写回和状态记录。

### 1.2 建设目标

B1 第一阶段目标是完成 **CPU 真实 Embedding Sidecar MVP**，形成以下最小闭环：

```text
P4 / B2 / P3 Runtime 写入请求
        ↓
B1 Sidecar 旁路接收
        ↓
输入校验、租户隔离、幂等判断
        ↓
文本识别与 chunk 分块
        ↓
CPU Embedding 后端
  FastEmbed / ONNX Runtime / Mock
        ↓
向量结果标准化
        ↓
返回 vector / chunk metadata / status / error_code / trace_id
        ↓
B2 写入 P2-E1 或 P2/Milvus 投影
```

阶段目标：

| 目标 | 说明 |
|---|---|
| 旁路接入 | 支持 P4、B2、P3 Runtime 通过 HTTP 调用 B1，不要求主链路同步依赖模型加载 |
| 文本分块 | 支持稳定 chunk_id、start/end offset、overlap 和 source_id 追踪 |
| 真实向量化 | 支持 FastEmbed/ONNX CPU 模型，默认中文模型输出 512 维向量 |
| 批处理 | 支持 batch items、model batch size 和 max concurrency 控制 |
| 幂等 | 同一 request_id/source_id/chunk 输入重复提交时可返回一致结果 |
| 可观测 | 返回 latency、status、error_code、trace_id，并暴露 health 与 metrics |
| 容错 | 模型未 ready、推理超时、队列拥塞、P2/B2 下游失败时可识别并降级 |
| 可演进 | 为 SIMD、INT8、OpenVINO、GPU 可选通道和多模态下推预留接口 |

当前融合版已覆盖上述核心 MVP 链路，但 TTL 幂等、熔断、租户限流、query 单向量约束、完整事件/metrics 和 fail-open 部署隔离仍属于待整改内容。

### 1.3 设计原则

1. **边界清晰**：B1 负责文本向量化与向量元数据生成，不负责 B2 记忆管理、不负责 B3 调度、不负责 P2 索引实现、不负责 P4 网关鉴权。
2. **旁路优先**：B1 作为 Sidecar 或旁路服务接入写入链路，故障时不应阻断对象原文写入主链路。
3. **失败可识别**：所有失败必须返回结构化 `status/error_code/error_message/component/trace_id`，禁止只返回 500 或空结果。
4. **有限等待**：模型加载、队列排队、后端推理、P2 写入、B2 调用都必须有超时边界，禁止无限等待。
5. **降级可控**：B1 可按 fail-open/fail-closed 策略运行；业务写入链路优先 fail-open，验收压测链路可 fail-closed。
6. **幂等可重放**：同一请求重试不得产生重复 chunk 或重复向量记录，便于 Celery、B2 和 P2 失败后补偿。
7. **模型版本可追踪**：每条向量必须携带 `embedding_model`、`embedding_dim`、`backend` 和 `schema_version`。
8. **资源受控**：通过 batch、并发、队列、输入大小和租户限流保护 CPU 与内存，避免单租户或单批长文本拖死 Sidecar。

---

## 2. 总体技术架构

### 2.1 B1 在 P3 内部的位置

```text
P4 接入层 / Agent / RAG / SDK
  负责上传文档、会话内容、工具结果、RAG 证据
        ↓
P3-B1 Embedding 下推 Sidecar
  负责文本识别、分块、Embedding、向量结果与 metadata 生成
        ↓
P2-E2 Object / Chunk
  保存原文对象、chunk 引用和 content_ref
        ↓
P2-E1 Vector Engine / Milvus 过渡投影
  保存向量、索引、collection、segment 和检索状态
        ↓
P3-B2 Agent 记忆管理器
  使用 B1 结果写入 Working/Episodic/Semantic Memory，构建 Context Pack
        ↓
P3-B3 语义智能分层调度器
  消费 B2 signal 与 P2 状态，进行调度决策
```

B1 与其他模块的关系：

- 对 P4：提供文本类写入的向量化能力，返回 status 和 vector metadata。
- 对 B2：提供记忆写入、长文本归档和 query embedding 能力。
- 对 P2-E1：提供向量写入所需的 `vector/chunk_id/source_id/metadata`。
- 对 P2-E2：引用原文对象和 chunk 内容，不直接替代对象存储。
- 对 B3：不直接服务 B3 调度，只通过 P2/B2 形成可调度的向量资源状态。

### 2.2 逻辑组件划分

| 组件 | 职责 | 第一阶段实现方式 |
|---|---|---|
| Request Gateway | 接收 `/v1/intercept`、`/health/ready`、metrics 请求 | FastAPI HTTP Sidecar |
| Input Validator | 校验 tenant/source/request/text/metadata/大小限制 | Pydantic schema + 显式限制 |
| Idempotency Cache | 缓存近期 request hash 与结果，支持重试去重 | LRU/TTL 内存缓存，后续可接 Redis |
| Text Detector | 判断是否需要 embedding，处理 `embedding_required=false` | 规则判断 + 上游显式字段 |
| Chunker | 按字符长度、自然边界、overlap 切分文本 | `chunk_max_chars` + `overlap_chars` |
| Batch Scheduler | 聚合 batch、控制并发、排队超时 | semaphore + model batch size |
| Embedding Backend | 调用真实或 mock embedding 模型 | FastEmbed/ONNX CPU，Mock 用于测试 |
| Vector Normalizer | 校验维度、有限数值、归一化策略 | 512 维 FP32，非有限数值拒绝 |
| Result Builder | 生成 chunk records、status、metrics、error_code | JSON envelope |
| Fault Controller | 超时、重试建议、熔断、降级状态 | 第一阶段内置错误码，第二阶段补熔断 |
| Metrics Store | 记录请求数、成功率、P95/P99、错误码 | 内存窗口，后续接 Prometheus/OTel |

---

## 3. 模块边界与接口依赖

### 3.1 P3 内部模块边界

| 模块 | 主责 | 向 B1 提供 | B1 输出 | B1 不应做的事 |
|---|---|---|---|---|
| P4 | 网关、SDK、Agent/RAG 接入 | 文档、对话、工具结果、tenant/user/session、trace | embedding 结果或 skipped/failed 状态 | B1 不做 P4 鉴权、协议路由、用户界面 |
| B1 | 文本分块和向量化 | - | chunk/vector/status/metadata | 不管理记忆生命周期、不做调度决策 |
| B2 | 记忆写入、长文本异步处理、上下文构建 | text、memory_id、session_id、agent_id、content_ref | B1 records、query vector | B1 不决定 Working/Episodic/Semantic 分层 |
| B3 | 调度评分和动作生成 | 不直接调用 B1 | 间接使用 B1 生成的 vector/chunk 状态 | B1 不生成 Promote/Demote/Pin 动作 |
| P3 Runtime | 本地集成与演示服务 | EmbeddingRequest、B2 long-text 请求 | EmbeddingResult、任务状态所需字段 | B1 不承担整个 P3 服务编排 |

### 3.2 外部模块边界

| 模块 | B1 依赖内容 | 主责方 | 异常时 B1 行为 |
|---|---|---|---|
| P2-E2 Object | object_id、source_id、content_ref、chunk 原文引用 | P2 | B1 不阻塞对象写入，返回向量化失败或 skipped |
| P2-E1 Vector | collection、vector schema、dimension、upsert 接口 | P2/B2 | B1 本身不强依赖 P2；由 B2/P3 写入 P2 时处理重试 |
| Redis | 可选幂等缓存、任务状态、Celery broker | B2/运维 | B1 MVP 不强依赖 Redis，避免 Redis 故障拖死 B1 |
| Celery | 长文本异步调度 | B2 | B1 不直接管理 Celery，只返回可重试错误码 |
| Milvus | 过渡向量投影或对比验证 | B2/P2 | B1 不直接依赖 Milvus |
| 模型缓存 | FastEmbed/ONNX 模型文件 | B1/运维 | 缓存缺失时 ready=false，触发加载或部署告警 |
| 监控/Trace | trace_id、metrics、日志平台 | 全模块 | 平台不可用时先本地记录，不阻塞推理 |

---

## 4. B1 数据模型与接口设计

### 4.1 P4/B2/P3 → B1：InterceptRequest

```python
class InterceptItem:
    request_id: str
    trace_id: str | None
    tenant_id: str
    source_type: str
    source_id: str
    object_id: str | None
    chunk_id: str | None
    text: str | None
    chunk_text: str | None
    metadata: dict
    embedding_required: bool
    input_type: Literal["passage", "query"]
```

字段约束：

| 字段 | 说明 | 是否必填 |
|---|---|---|
| `request_id` | 请求幂等 ID，同一请求重试保持不变 | 是 |
| `trace_id` | 全链路追踪 ID | 建议必填 |
| `tenant_id` | 租户隔离字段 | 是 |
| `source_type` | document/memory/rag_evidence/tool_result/query 等 | 是 |
| `source_id` | 上游来源 ID | 是 |
| `object_id` | P2-E2 对象 ID 或 B2 记忆对象 ID | 可选 |
| `chunk_id` | 上游已分块时的 chunk ID | 可选 |
| `text` | 原始文本，由 B1 分块 | 与 `chunk_text` 二选一 |
| `chunk_text` | 已分块文本，B1 只生成单 chunk vector | 与 `text` 二选一 |
| `metadata` | session_id、agent_id、memory_id、content_ref 等 | 可选 |
| `embedding_required` | 是否需要真实向量化 | 默认 true |
| `input_type` | passage 或 query，用于模型前缀和检索语义 | 默认 passage |

### 4.2 B1 → 调用方：InterceptResponse

```python
class InterceptResult:
    request_id: str
    trace_id: str | None
    tenant_id: str
    source_id: str
    object_id: str | None
    status: Literal["success", "skipped", "failed"]
    error_code: str | None
    error_message: str | None
    embedding_model: str
    embedding_dim: int | None
    backend: str
    latency_ms: float
    input_chars: int
    chunk_count: int
    vector_count: int
    chunks: list[EmbeddingChunk]
```

```python
class EmbeddingChunk:
    chunk_id: str
    chunk_index: int
    chunk_text: str
    start_char: int
    end_char: int
    vector: list[float] | None
    metadata: dict
```

当前融合版差异：B1 直接响应已经返回 `chunk_id`、offset、文本和 vector，但没有在每个 chunk 中直接输出完整 `metadata`。B2 在写 P2 前会补齐 `task_id`、`memory_id`、tenant、user、agent、session、`content_ref` 等字段，因此 B2->P2 链路基本可追踪；但 P4/P3 Runtime 直接调用 B1 时仍拿不到方案要求的完整 chunk metadata。本项应列为 P1 整改。

### 4.3 B1 → B2/P2：EmbeddingRecord

B1 不直接决定 B2 的记忆层级，但必须输出足够的可追踪字段，使 B2 可以写 P2-E1：

```python
class EmbeddingRecord:
    request_id: str
    trace_id: str | None
    source_id: str
    object_id: str | None
    chunk_id: str
    chunk_text: str
    vector: list[float]
    embedding_model: str
    metadata: dict
```

metadata 建议至少包含：

```text
tenant_id
user_id
agent_id
session_id
memory_id
content_ref
source_type
chunk_index
start_char
end_char
b1_schema_version
embedding_backend
embedding_dim
```

### 4.4 B1 健康检查接口

```text
GET /health/live
GET /health/ready
GET /metrics
```

`/health/live` 表示进程存活，不依赖模型加载；`/health/ready` 表示模型已加载且可执行 embedding；`/metrics` 输出请求数、成功率、失败率、P95/P99、队列超时、后端超时、熔断状态等。

---

## 5. B1 核心处理流程设计

### 5.1 流程目标

B1 的核心流程不是单纯“文本进、向量出”，而是要保证文本向量化具备稳定 ID、可重试、可审计和可降级能力：

```text
接收请求
  ↓
schema 校验与大小限制
  ↓
幂等 key 计算
  ↓
是否 embedding_required
  ↓
文本分块或使用上游 chunk
  ↓
进入批处理和并发控制
  ↓
调用 Embedding Backend
  ↓
向量维度、数值、数量校验
  ↓
构造 chunk records
  ↓
返回 success/skipped/failed
  ↓
记录 metrics 和 event
```

### 5.2 输入预过滤规则

| 条件 | 处理方式 |
|---|---|
| `request_id/tenant_id/source_id/source_type` 缺失 | 直接失败，不重试 |
| `text` 与 `chunk_text` 同时为空或同时存在 | 直接失败，不重试 |
| 文本为空白 | 返回 failed，error_code=`B1_EMPTY_TEXT` |
| 请求体超过 `max_body_bytes` | 返回 failed 或 HTTP 413 |
| 文本超过 `max_input_chars` | 返回 failed，建议上游拆分为异步长文本 |
| metadata 超过限制 | 返回 failed，防止日志和缓存膨胀 |
| `embedding_required=false` | 返回 skipped，不调用模型 |
| 当前租户超过限流 | 返回 failed，error_code=`B1_TENANT_RATE_LIMITED` |

### 5.3 文本分块策略

第一阶段使用规则分块，要求稳定、可解释、可复现：

```yaml
chunk_max_chars: 400
chunk_overlap_chars: 40
max_chunks_per_item: 96
boundary_priority:
  - newline
  - Chinese period/question/exclamation
  - English period
  - whitespace
  - hard cut
```

chunk_id 生成规则：

```text
chunk_id = "{object_id or memory_id or source_id}:{chunk_index:04d}"
```

分块要求：

1. start_char/end_char 必须指向原文偏移。
2. 同一文本、同一 chunk 参数、同一 source_id 下 chunk_id 必须稳定。
3. overlap 只用于提升召回，不得导致无限循环。
4. chunk 数超过上限时返回 `B1_TOO_MANY_CHUNKS`，由 B2 长文本异步链路处理。

当前融合版差异：核对报告指出，当前 `sidecar.py` 对所有输入统一调用 `_natural_chunks`。当上游传入 `chunk_text + chunk_id` 时，如果 `chunk_text` 超过 `chunk_max_chars`，仍可能被二次拆分为多个 chunk，导致上游 chunk_id 与 B1 offset、向量数量不一致。整改要求是：

```text
if chunk_text is supplied:
    do not split again
    return exactly one chunk vector
    keep upstream chunk_id
else:
    run B1 natural chunker
```

该项优先级为 P1。

### 5.4 批处理与并发控制

第一阶段采用“请求内批处理 + 全局并发限制”：

```yaml
max_batch_items: 32
model_batch_size: 8
max_concurrency: 1
queue_timeout_seconds: 5
backend_timeout_seconds: 120
```

控制目标：

- 防止并发请求同时抢占 CPU 导致 P95/P99 失控。
- 避免长文本 batch 长时间占用推理槽位。
- 在队列拥塞时快速返回 `B1_BUSY`，由 B2/Celery 重试或 P4 提示稍后再试。
- 保证小请求和 query embedding 不被大批量写入完全饿死。

后续可增加优先级队列：

| 请求类型 | 优先级 |
|---|---:|
| query embedding | 最高 |
| B2 前台短文本 | 高 |
| B2 长文本 Celery 任务 | 中 |
| 批量离线压测 | 低 |

### 5.5 Embedding Backend 设计

第一阶段后端：

| 后端 | 用途 | 说明 |
|---|---|---|
| Mock | 单元测试、离线流程测试 | 固定维度、确定性输出，不用于正式验收 |
| FastEmbed CPU | MVP 真实 embedding | 中文模型，512 维，适合容器化验证 |
| ONNX Runtime CPU | 性能优化主路径 | 支持线程数、batch、后续 INT8 |

模型输出校验：

1. vector 数量必须等于 chunk 数量。
2. vector 不能为空。
3. 所有 vector 维度必须一致。
4. 所有数值必须是有限浮点数，不允许 NaN/Inf。
5. embedding_dim 必须与 B2/P2 collection schema 一致。

### 5.6 结果写回策略

B1 第一阶段不直接强依赖 P2 写入，推荐职责拆分：

```text
B1: 生成 vector records
B2/P3 Runtime: 将 vector records 写入 P2-E1 或 Milvus 投影
P2: 负责 collection、segment、index、检索状态
```

这样 B1 模型服务不会因为 P2 短暂不可用被拖死。若后续需要 B1 直写 P2，应作为可选 sink，并遵守：

- P2 sink 失败不影响 B1 返回 embedding 结果。
- 直写模式必须携带幂等 key。
- P2 写入失败记录为 `sink_status=failed`，由补偿任务重试。

当前融合版对应情况：B2 长文本链路已经形成 `P2-E2 -> Redis/Celery -> B1 -> P2-E1` 的核心闭环，P2 record metadata 由 B2 的桥接层补齐；Milvus 是可选投影。但 B1 直连调用场景下 metadata 仍不足，不能把 B2 侧补齐能力等同为 B1 输出契约完全满足。

---

## 6. 长文本、已分块文本与查询向量策略

### 6.1 长文本处理

B1 不应直接承担无限长文本处理。长文本应由 B2/Celery 异步链路削峰：

```text
P3 HTTP 接收长文本
  ↓
P2-E2 保存原文对象
  ↓
Redis/Celery 提交异步任务
  ↓
Celery 调用 B1 分块和向量化
  ↓
B2 写 P2-E1
  ↓
任务状态写 Redis
```

B1 对长文本的策略：

| 情况 | B1 行为 |
|---|---|
| 文本在限制内 | 正常分块 |
| 文本超过 `max_input_chars` | 返回 `B1_INPUT_TOO_LARGE` |
| chunk 数超过 `max_chunks_per_item` | 返回 `B1_TOO_MANY_CHUNKS` |
| 上游已分块并传 `chunk_text` | B1 只处理单 chunk |

### 6.2 已分块文本

当 B2 或 P4 已完成 chunk 切分时，应传入 `chunk_text` 和 `chunk_id`。B1 只负责单 chunk embedding，不再二次分块，避免 chunk_id 与原文偏移错位。

当前融合版未完全满足本节要求。本项是当前对应性核对报告中优先级最高的 P1 差异之一，后续代码整改和验收用例必须覆盖：

- 输入 `chunk_text + chunk_id`；
- `chunk_text` 长度超过 `chunk_max_chars`；
- B1 仍只返回 1 个 chunk；
- 返回 chunk_id 与上游 chunk_id 一致；
- 不重新生成多个 `:0000/:0001` 子 chunk。

### 6.3 Query Embedding

查询向量使用 `input_type=query`，要求：

- 一次请求通常只返回一个 query vector。
- 优先级高于批量写入。
- 失败时返回 `B1_QUERY_EMBEDDING_FAILED`，由 B2 search 决定是否降级为关键词检索或返回检索不可用。

当前融合版差异：P3 search 侧会拒绝 B1 返回多个 query records，但 B1 Sidecar 本身仍对 query 与 passage 使用同一套分块和队列逻辑。超长 query 仍可能被切分成多个向量，且 query 没有更高优先级队列。本项为 P2 整改。

---

## 7. 向量元数据、状态快照与验收证据

B1 每次处理必须形成可追踪证据，服务于测试、联调、压测和验收。

### 7.1 处理事件

```python
class B1EmbeddingEvent:
    event_id: str
    request_id: str
    trace_id: str
    tenant_id: str
    source_id: str
    object_id: str | None
    status: str
    error_code: str | None
    backend: str
    model: str
    embedding_dim: int | None
    input_chars: int
    chunk_count: int
    vector_count: int
    queue_wait_ms: float
    backend_latency_ms: float
    total_latency_ms: float
    created_at: int
```

当前融合版差异：现有事件字段使用 `sequence` 和 `timestamp_epoch`，尚未完整包含 `event_id`、`queue_wait_ms`、`backend_latency_ms`、`created_at` 等验收 schema 字段。本项为 P2 整改。

### 7.2 统计指标

| 指标 | 说明 |
|---|---|
| `b1_requests_total` | 请求总数 |
| `b1_items_total` | item 总数 |
| `b1_success_total` | 成功 item 数 |
| `b1_skipped_total` | skipped item 数 |
| `b1_failed_total` | 失败 item 数 |
| `b1_queue_timeout_total` | 排队超时数 |
| `b1_backend_timeout_total` | 后端超时数 |
| `b1_busy_total` | 拥塞返回数 |
| `b1_latency_p50/p95/p99` | 延迟分位 |
| `b1_docs_per_second` | docs/s 吞吐 |
| `b1_chunks_per_second` | chunks/s 吞吐 |
| `b1_vectors_per_second` | vectors/s 吞吐 |
| `b1_model_load_seconds` | 模型加载耗时 |
| `b1_circuit_state` | 熔断状态 |

当前融合版差异：现有 metrics 已包含请求数、成功率、P50/P95/P99 等基础指标，但仍缺少 `docs/s`、`chunks/s`、`vectors/s`、模型加载耗时和熔断状态。本项为 P2 整改，不能作为 72 小时稳定性验收已完成的证据。

### 7.3 验收证据

B1 验收报告至少包含：

1. 模型名称、模型版本、后端类型、embedding_dim。
2. 输入数据规模、文本长度分布、chunk 参数。
3. QPS/docs/s、chunks/s、vectors/s。
4. P95/P99 延迟。
5. 错误率、超时率、busy 率。
6. 72 小时稳定性运行日志。
7. B1 异常时 B2/P3 主服务未被拖死的故障注入证据。

---

## 8. 故障识别、降级、重试与熔断设计

### 8.1 总体容错原则

B1 是 P3 的基础语义化能力，但不应成为全链路单点阻塞。所有异常按以下原则处理：

1. 模型未 ready 时，`/health/ready` 必须明确返回 not ready，调用方不得无限等待。
2. 队列拥塞时快速返回 `B1_BUSY`，由 B2/Celery 重试，P4 前台可提示稍后重试。
3. 输入错误不重试，直接返回业务错误。
4. B1 后端短暂失败可重试，但重试由调用方或异步任务控制，B1 服务本身不做无限内部重试。
5. B1 失败不得阻止 P2-E2 原文对象写入。
6. B1 不可用时，B2 可以保留待 embedding 状态，后续补偿重放。
7. 监控系统不可用时，不影响 B1 返回结果，但必须尽量写本地事件或内存窗口。

### 8.2 模块级故障矩阵

| 故障模块 | 故障表现 | B1 识别方式 | 降级策略 | 重试策略 | 是否拖死核心服务 |
|---|---|---|---|---|---|
| B1 进程 | Sidecar 无响应 | `/health/live` 失败、HTTP connection refused | P3 标记 B1 unavailable；B2 任务进入重试或待补偿 | 容器重启、调用方退避重试 | 否，P3/B2 前台不应无限等待 |
| B1 模型 | 模型加载失败、缓存缺失 | `/health/ready` 非 ready，load_error | fail-open 返回 failed result；fail-closed 拒绝服务 | 运维修复模型缓存后重新 ready | 否 |
| B1 队列 | 并发满、等待超时 | semaphore/queue timeout | 返回 `B1_BUSY` | B2/Celery 指数退避重试 | 否 |
| B1 后端 | ONNX/FastEmbed 推理超时 | backend timeout | 当前 item failed，批内其他 item 尽量独立返回 | 调用方限次重试；连续失败触发熔断 | 否 |
| 输入数据 | 文本过大、metadata 过大、字段缺失 | schema 和大小限制 | 快速失败，不进模型 | 不重试 | 否 |
| B2 | B2 调用 B1 后处理失败 | B1 已返回 success，但调用方未确认 | B1 不反向等待 B2；结果可按 request_id 重放 | B2 使用 task_id 补偿 | 否 |
| Redis/Celery | 长文本任务积压或 broker 不可用 | B1 不直接依赖；由 B2 health 暴露 | B1 仍可处理同步请求 | B2 侧重试 | 否 |
| P2-E1 | 向量写入失败 | 若 B1 不直写则由 B2 识别；直写模式识别 upsert error | B1 返回 embedding success + sink failed | P2 sink 幂等重试 | 否 |
| Milvus | 投影失败 | B1 不直接依赖 | 不影响 B1 | B2 侧补偿 | 否 |
| 监控/Trace | 指标上报失败 | exporter error | 本地内存统计继续 | 异步补报 | 否 |

当前融合版差异：fail-open/closed、超时、有限重试已经具备基础能力，但熔断状态机、租户限流器、运行时恢复和完整错误码体系尚未实现。本节描述的是目标方案，不应被解读为当前代码已全部实现。

### 8.3 降级级别

| 等级 | 触发条件 | B1 行为 |
|---|---|---|
| L0 正常 | 模型 ready、队列正常、后端正常 | 完整分块和向量化 |
| L1 慢启动 | 模型加载中 | live 正常、ready=false；调用方重试 |
| L2 队列拥塞 | 并发满或 queue timeout | 返回 `B1_BUSY`，不继续排队 |
| L3 后端异常 | backend timeout/error 连续出现 | 返回 failed；可进入熔断 |
| L4 跳过向量化 | `embedding_required=false` 或策略降级 | 返回 skipped 和 metadata |
| L5 只保留原文 | B1 整体不可用 | P2-E2/B2 保留 content_ref，后续补偿 embedding |

### 8.4 重试、熔断与幂等

推荐配置：

```yaml
retry_hint:
  retryable_error_codes:
    - B1_MODEL_NOT_READY
    - B1_BUSY
    - B1_EMBEDDING_TIMEOUT
    - B1_BACKEND_UNAVAILABLE
  non_retryable_error_codes:
    - B1_INVALID_REQUEST
    - B1_EMPTY_TEXT
    - B1_INPUT_TOO_LARGE
    - B1_TOO_MANY_CHUNKS
    - B1_VECTOR_DIMENSION_MISMATCH

circuit_breaker:
  failure_threshold: 5
  window_seconds: 60
  open_seconds: 120
  half_open_probe_requests: 3

idempotency:
  key: hash(tenant_id + request_id + source_id + text_hash + model + chunk_config)
  cache_size: 1024
  ttl_seconds: 3600
```

错误码建议：

| error_code | 含义 | 是否可重试 |
|---|---|---:|
| `B1_MODEL_NOT_READY` | 模型未加载完成 | 是 |
| `B1_BUSY` | 队列拥塞或并发超限 | 是 |
| `B1_EMBEDDING_TIMEOUT` | 推理超时 | 是 |
| `B1_BACKEND_UNAVAILABLE` | 模型后端不可用 | 是 |
| `B1_CIRCUIT_OPEN` | 熔断打开 | 是，等待半开 |
| `B1_INVALID_REQUEST` | 请求字段非法 | 否 |
| `B1_EMPTY_TEXT` | 文本为空 | 否 |
| `B1_INPUT_TOO_LARGE` | 输入过大 | 否 |
| `B1_TOO_MANY_CHUNKS` | 分块过多 | 否，需异步拆分 |
| `B1_VECTOR_DIMENSION_MISMATCH` | 维度不一致 | 否，需修配置 |

当前融合版已出现或基本对应的错误码主要包括：

- `B1_MODEL_NOT_READY`
- `B1_BUSY`
- `B1_EMBEDDING_TIMEOUT`
- `B1_EMBEDDING_BACKEND_ERROR`
- `B1_TEXT_TOO_LONG`

仍需补齐或调整的错误码包括：

- `B1_BACKEND_UNAVAILABLE`
- `B1_CIRCUIT_OPEN`
- `B1_INPUT_TOO_LARGE`
- `B1_TOO_MANY_CHUNKS`
- `B1_QUERY_EMBEDDING_FAILED`
- `B1_TENANT_RATE_LIMITED`

当前幂等缓存只按容量淘汰，没有 TTL。正式方案要求增加 `ttl_seconds`，避免旧 request_id 长期占用幂等空间，也避免长时间运行后缓存语义不清。

---

## 9. 部署与运行方案

### 9.1 第一阶段部署形态

| 环境 | 部署方式 | 说明 |
|---|---|---|
| 开发验证 | 本地 Python/FastAPI + Mock/FastEmbed | 验证接口、分块、错误码 |
| P3 内部联调 | Docker Compose：p3 + b1-sidecar + redis + engine | 验证 B1/B2/P2 闭环 |
| B1 压测 | 单独部署 b1-sidecar，固定模型缓存和 CPU 线程 | 验证 docs/s、P95/P99、busy 率 |
| 准真实联调 | B1 旁路接 P4/B2/P2，使用脱敏准真实文本 | 验证 trace、tenant 隔离、P2 写入 |
| 验收环境 | 固定硬件、固定模型、固定数据集、固定并发口径 | 输出正式测试报告 |

当前融合版部署差异：Compose 中 `p3` 和 `celery-worker` 对 `b1-sidecar` 使用 healthy 依赖。模型加载失败、模型缓存缺失或 B1 readiness 异常时，P3/B2 容器可能无法正常进入服务状态，实际效果接近 fail-closed。若方案要求 B1 fail-open，Compose 应调整为：

- P3 HTTP 服务不因 B1 ready 失败而拒绝启动；
- Celery worker 可启动，但任务处理时识别 B1 unavailable 并重试；
- `/health` 汇总 B1 状态，而不是用容器启动依赖替代运行时降级；
- demo/smoke 可以依赖 B1 healthy，但正式主服务不应硬依赖。

### 9.2 运行参数

```yaml
AETHER_B1_BACKEND: onnx
AETHER_B1_MODEL_NAME: BAAI/bge-small-zh-v1.5
AETHER_B1_CACHE_DIR: /app/.aether/b1/models
AETHER_B1_HOST: 0.0.0.0
AETHER_B1_PORT: 18081
AETHER_B1_THREADS: 1
AETHER_B1_MODEL_BATCH_SIZE: 8
AETHER_B1_MAX_BATCH_ITEMS: 32
AETHER_B1_MAX_CONCURRENCY: 1
AETHER_B1_QUEUE_TIMEOUT_SECONDS: 5
AETHER_B1_BACKEND_TIMEOUT_SECONDS: 120
AETHER_B1_CHUNK_MAX_CHARS: 400
AETHER_B1_CHUNK_OVERLAP_CHARS: 40
AETHER_B1_MAX_CHUNKS_PER_ITEM: 96
AETHER_B1_FAIL_MODE: open
```

### 9.3 硬件建议

| 阶段 | CPU | GPU | 内存 | 存储 | 说明 |
|---|---:|---:|---:|---:|---|
| B1 开发 | 不少于 32 vCPU | 共享 1×24GB 可选 | 不少于 64GB | 不少于 1TB SSD | 接口开发、Mock、基础 embedding 验证 |
| B1 联调 | 建议申请不少于 144 vCPU | 共享 1×48GB 或 2×24GB 可选 | 不少于 192GB | 不少于 3TB SSD | 对接 P4、B2、P2-E1/E2 |
| B1 压测/验收 | 建议 144 vCPU 级别 | 主路径不依赖 GPU，可预留 | 每节点不少于 256GB | 每节点不少于 4TB + 5TB 日志/trace | 验证 QPS、P95/P99、错误率、72 小时稳定性 |

GPU 不作为 B1 MVP 主路径硬依赖；后续多模态或 GPU embedding 通道可作为 M2 扩展。

---

## 10. 测试、联调与验收方案

### 10.1 开发验证

验证内容：

- schema 校验；
- text/chunk_text 二选一；
- chunk_id 稳定性；
- overlap 与自然边界；
- Mock embedding 确定性；
- FastEmbed/ONNX 真实 embedding；
- vector 维度和有限数值校验；
- fail-open/fail-closed 行为；
- `/health/live`、`/health/ready`、metrics。

### 10.2 P3 内部联调

验证内容：

- B2 调用 B1 生成 memory chunk vector；
- B2 将 B1 records 转换为 P2-E1 records；
- B1 query embedding 支持 B2 search；
- B1 返回 trace_id/request_id 并贯穿 B2 task status；
- B1 failed/skipped 状态不会导致 P3 HTTP 进程崩溃；
- 长文本经 Redis/Celery 异步调用 B1。

### 10.3 P2/P4 联调

验证内容：

- P4 写入请求携带 tenant/user/agent/session/source_id；
- P2-E2 保存原文对象，B1 输出 content_ref 对应 metadata；
- B2/P3 将 B1 向量写入 P2-E1 collection；
- collection dimension 与 B1 embedding_dim 一致；
- P2-E1 检索结果可反查 source_id/object_id/chunk_id。

### 10.4 故障注入测试

| 场景 | 操作 | 预期结果 |
|---|---|---|
| 模型未 ready | 启动 Sidecar 后延迟模型加载 | live 正常、ready=false；调用方收到 `B1_MODEL_NOT_READY` |
| 模型缓存缺失 | 删除模型缓存或配置错误路径 | ready=false，load_error 可见 |
| 队列拥塞 | 并发超过 max_concurrency | 部分请求 `B1_BUSY`，服务不崩溃 |
| 后端超时 | 注入慢推理或缩短 backend timeout | 返回 `B1_EMBEDDING_TIMEOUT` |
| 输入过大 | 提交超长文本 | 返回 `B1_INPUT_TOO_LARGE` |
| chunk 过多 | 提交会产生超限 chunk 的文本 | 返回 `B1_TOO_MANY_CHUNKS` |
| 非法 vector | mock 注入 NaN/Inf 或维度变化 | 返回 `B1_VECTOR_DIMENSION_MISMATCH` 或校验失败 |
| B2 worker 重试 | B1 临时返回 busy | Celery 限次重试，不阻塞 P3 HTTP |
| P2 不可用 | B1 成功但 P2 upsert 失败 | B1 不受影响，B2 任务失败或重试 |
| 监控不可用 | 关闭 metrics exporter | B1 推理正常，本地窗口仍记录 |

新增对应性整改测试：

| 场景 | 操作 | 预期结果 |
|---|---|---|
| 已分块输入不二次分块 | 提交 `chunk_text + chunk_id` 且文本超过 `chunk_max_chars` | B1 只返回一个 chunk，chunk_id 保持上游值 |
| B1 chunk metadata | 直接调用 B1 `/v1/intercept` | 每个 chunk 返回 metadata，至少含 tenant/source/model/schema/offset |
| 幂等 TTL | 同一 request_id 在 TTL 前后重复提交 | TTL 内 replay；TTL 后按新请求处理 |
| 熔断 | 连续注入 backend timeout | 达阈值后返回 `B1_CIRCUIT_OPEN`，半开探测成功后恢复 |
| 租户限流 | 单 tenant 高并发压测 | 单租户被限流，其他 tenant 不受拖累 |
| Query 单向量 | 提交超长 query | B1 直接拒绝或强制单向量，不返回多个 query records |
| Compose fail-open | B1 readiness 失败时启动 p3/celery | p3/celery 仍启动，接口状态标记 B1 unavailable |
| Mock fallback 禁用 | 验收环境不配置 B1 sidecar | 服务不得静默退回 32 维 Mock |

### 10.5 MVP 验收关注点

MVP 不应宣称 B1 已完成所有 SIMD/INT8/GPU 极限优化，除非对应压测和环境已经具备。根据对应性核对报告，当前只能判定为“核心 MVP 链路基本对应”，不能判定为“完整工程契约和完整容错验收已完成”。第一阶段重点验收：

1. B1 Sidecar 能独立启动并暴露 health。
2. B1 能接收 P4/B2/P3 Runtime 请求并返回真实 512 维向量。
3. B1 能稳定生成 chunk_id、offset、source_id、object_id、metadata。
4. B1 能在输入错误、模型未 ready、队列拥塞、推理超时时返回明确错误码。
5. B1 故障不会拖死 P3 HTTP 服务和 B2 异步任务队列。
6. B2 能基于 B1 records 写入 P2-E1，并完成 query 检索闭环。
7. 压测报告明确 docs/s、chunks/s、vectors/s、P95/P99、错误率和资源占用。
8. 正式验收禁止静默使用 32 维 Mock fallback，必须使用真实 512 维 CPU Embedding。
9. 72 小时稳定性证据必须包含故障注入、重试、降级、恢复和核心服务存活记录。

---

## 11. 后续演进方案设计

### 11.1 演进前提

B1 后续优化不应在基础链路不稳定时启动。至少需要满足：

1. HTTP Sidecar 接口稳定；
2. B1/B2/P2 向量维度和 metadata schema 冻结；
3. chunk 策略和 chunk_id 规则稳定；
4. 错误码、幂等、重试、metrics 可用；
5. 有固定压测集、INT8 校准集、召回验证集；
6. 有可复现的性能基线。

### 11.2 V2：ONNX Runtime + SIMD/INT8 性能优化

#### 11.2.1 设计目标

V2 目标是在不改变外部接口的前提下，把 B1 从“可用的真实 CPU embedding”升级为“稳定可压测的 CPU 优化 embedding 服务”。

优化方向：

- ONNX Runtime CPU Execution Provider；
- 线程数和 batch size 自动调优；
- INT8 量化；
- SIMD 指令集检测；
- 模型预热；
- query 与 passage 批次隔离；
- metrics 驱动的动态 batching。

#### 11.2.2 性能参数

```yaml
batching:
  min_batch_size: 1
  max_batch_size: 32
  max_wait_ms: 20

onnx:
  intra_op_threads: configurable
  inter_op_threads: configurable
  graph_optimization_level: all

int8:
  calibration_samples: 1000-10000
  quality_eval_samples: 500-2000
  max_recall_drop: to_be_confirmed
```

#### 11.2.3 风险控制

| 风险 | 控制方式 |
|---|---|
| INT8 召回质量下降 | 固定召回验证集，对比 FP32 baseline |
| batch 过大导致尾延迟升高 | 设置 max_wait_ms 和 query 优先级 |
| 线程数过高导致 CPU 争抢 | 线程数与容器 CPU quota 绑定 |
| 模型版本漂移 | embedding_model 和 model_hash 写入 metadata |
| 优化后维度变化 | 启动时强校验 dimension |

### 11.3 V3：OpenVINO / 可选 GPU / 多模态下推

#### 11.3.1 设计目标

V3 面向后续扩展，不作为 MVP 硬依赖。目标是在接口保持兼容的前提下，增加更多后端：

```text
Backend = Mock | FastEmbed | ONNXRuntime | OpenVINO | GPUEmbedding | MultiModal
```

#### 11.3.2 后端路由

```yaml
routing:
  text_short: onnx_cpu
  text_long_batch: onnx_cpu_int8
  image_text_multimodal: gpu_optional
  fallback: fastembed_cpu
```

后端路由必须显式写入结果 metadata：

```text
backend
model_name
model_hash
precision
device
embedding_dim
schema_version
```

#### 11.3.3 风险控制

| 风险 | 控制方式 |
|---|---|
| 多后端结果不可比 | 按 collection/schema/model_version 隔离 |
| GPU 不稳定影响 CPU 主链路 | GPU 后端作为可选通道，失败回退 CPU |
| 多模态输入拖慢文本链路 | 文本与多模态队列隔离 |
| 模型升级破坏召回 | 蓝绿模型版本，双写对比后切换 |

### 11.4 演进路线汇总

| 阶段 | 策略形态 | 技术重点 | 接口变化 | 风险控制 |
|---|---|---|---|---|
| V1 | CPU Sidecar MVP | 分块、FastEmbed/ONNX、状态、错误码、幂等 | 固定主接口 | fail-open、超时、限流 |
| V2 | CPU 性能优化 | ONNX Runtime、SIMD、INT8、动态 batch | 不改主接口，增加 metrics | 召回验证、P95/P99 保护 |
| V3 | 多后端扩展 | OpenVINO、GPU、多模态、后端路由 | 增加 backend metadata | 后端隔离、CPU fallback |

---

## 12. 当前关键结论与修改建议

### 12.1 对应性矩阵

| 领域 | 融合版现状 | 对应性 |
|---|---|---|
| B1 HTTP Sidecar | FastAPI、`/v1/intercept`、health、metrics、批处理 | 基本对应 |
| CPU Embedding | FastEmbed + ONNX Runtime CPU，默认 BGE 中文模型 | 基本对应 |
| 输入校验与跳过 | 必填字段、二选一文本、空文本、大小限制、`skipped` | 基本对应 |
| 文本分块 | offset、overlap、自然边界、最大 chunk 数 | 部分对应，已分块输入仍会二次分块 |
| 幂等 | 租户 + request_id、批内/并发/完成重放 | 部分对应，无 TTL、跨进程和持久化能力 |
| B1 输出契约 | status、error、vector、模型、维度、offset | 部分对应，chunk metadata 不由 B1 直接输出 |
| B2 长文本链路 | P2-E2 -> Redis/Celery -> B1 -> P2-E1 | 基本对应 |
| P2/Milvus 投影 | P2-E1 默认写入，Milvus 可选投影 | 基本对应 |
| Query embedding | B1 query 路径 + P2-E1 查询隔离 | 基本对应，但超长 query 不保证单向量 |
| 故障容错 | fail-open/closed、超时、有限重试 | 部分对应，缺熔断、错误码体系和运行时恢复 |
| 可观测与验收 | metrics、events、数据集工具、手工验证文档 | 部分对应，缺完整方案指标和 72 小时证据 |
| 部署隔离 | Compose 含 P2、P3、B1、Redis、Celery、Milvus | 部分对应，P3/B2 仍硬依赖 B1 healthy |

### 12.2 P1 整改项

1. **已分块输入不再二次分块**：传入 `chunk_text + chunk_id` 时，B1 必须只生成一个 chunk vector。
2. **B1 直接响应补齐 chunk metadata**：每个 chunk 必须直接返回 metadata，不能只依赖 B2 写 P2 前补齐。
3. **幂等缓存增加 TTL**：在容量淘汰之外增加写入时间和 `ttl_seconds`。
4. **补齐熔断、租户限流和错误码体系**：增加 `B1_BACKEND_UNAVAILABLE`、`B1_CIRCUIT_OPEN`、`B1_INPUT_TOO_LARGE` 等。
5. **调整 Compose fail-open 部署语义**：P3/B2 不应因 B1 readiness 异常无法启动。

### 12.3 P2 整改项

1. **Query embedding 单向量约束**：B1 侧对 query 做强约束，避免返回多个 query records。
2. **Query 优先级队列**：query 请求优先于批量写入和长文本任务。
3. **事件字段补齐**：增加 `event_id`、`queue_wait_ms`、`backend_latency_ms`、`created_at`。
4. **metrics 补齐**：增加 docs/s、chunks/s、vectors/s、模型加载耗时、熔断状态。
5. **验收环境禁用 32 维 Mock fallback**：Mock 只保留为开发和单元测试路径。

### 12.4 已完成核心闭环证据

当前融合版从代码结构上已经能对应以下链路：

```text
P3 / P4 长文本请求
    -> P2-E2 保存原文和 content_ref
    -> Redis/Celery 提交异步任务
    -> B1 Sidecar 分块和 CPU Embedding
    -> P2-E1 写入向量和 metadata
    -> 可选 Milvus 投影
    -> B1 query embedding
    -> P2-E1 隔离检索
```

因此，文档和汇报口径应写为：**B1 Sidecar MVP 核心链路基本跑通，但完整工程契约、容错能力和验收证据仍需补齐。**

---

## 13. 最终结论

B1 技术方案的第一阶段核心是：以 Sidecar 方式在写入链路旁路完成文本识别、稳定分块、CPU Embedding、向量结果标准化和状态可观测，形成 `source_id/object_id -> chunk_id -> vector -> metadata -> trace_id` 的可追踪闭环。当前融合版已经具备 B1 Sidecar MVP 的核心 HTTP、CPU Embedding、分块、向量校验、健康检查和基础幂等能力，也已经形成 B2 长文本异步链路以及 P2-E2/P2-E1 方向上的融合闭环。

但最终判定必须保持克制：当前不能认定为与方案完全对应。已分块输入二次分块、B1 chunk metadata 缺失、幂等 TTL 缺失、熔断和租户限流缺失、Compose fail-open 语义不足、query 单向量约束不足、metrics/events 验收字段不足、32 维 Mock fallback 未在验收环境禁用，仍是后续必须补齐的工程契约和验收项。B1 必须保持清晰边界，不越权承担 B2 记忆生命周期、B3 调度决策、P2 存储索引和 P4 网关接入职责；同时任何 B1 内部或上下游异常，都必须通过明确错误码、有限等待、fail-open/fail-closed、限流、幂等、重试建议和补偿入口处理，保证 B1 故障不会拖死 P3 主服务，也不会破坏后续 B2 召回和 B3 调度所依赖的语义资源链路。
