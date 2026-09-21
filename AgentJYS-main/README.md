# AetherStore

> **Recall 流程增量（2026-09-20）：** 已实现独立候选检索、融合、可配置 CrossEncoder、真实 token 预算及 Milvus VectorPort。使用与验证方式见[流程实现说明](../交付成果/架构设计/Recall与Embedding_流程实现说明.md)。默认本地模式保留 SQLite；Milvus 真实联调状态以本批验收记录为准。

> **团队协作入口：[开发与提交规范](CONTRIBUTING.md)**；完整交付见[协同开发基线](../交付成果/开发协作/README.md)。

> **P3 当前入口（PRD V1.3）：[总体设计与协作契约](docs/p3/README.md)。** [三个流程基础实现](docs/p3/development/05_三个流程基础实现与联调.md)已接入公共底座，可独立运行写入、召回与自动本地缓存调度。指标暂缓；真实记忆提取模型、生产存储及新HTTP路由尚未接通；Milvus适配已交付、真实联调待环境，旧服务说明不代表新契约已完成产品验收。

> 已补齐[本地日志、Trace 与健康检测](docs/p3/development/06_日志追踪与健康检测.md)：按 trace_id 查询节点过程及持久任务/事件状态，提供授权健康查询；本批不含容器和云端采集。

> [真实 Embedding](docs/p3/development/07_真实Embedding接入与旧实现清理.md)已接入 Remember/Recall，默认 BGE 中文 512 维 CPU 推理；完整三流程真实模型联调已通过。

AetherStore 是以 **P3 Intelligent Memory System** 为核心的 Agent 上下文与记忆运行时。
它把文本向量化、记忆形成与召回、上下文组织、派生投影和语义调度整合为一个可长期运行、
可观测、可恢复的工程系统，目标是通过稳定的 HTTP 契约供现有 Agent、企业平台和多端应用接入；当前新流程运行入口为 Python Host/CLI。

本仓库同时包含 P2 存储基座和 P4 参考应用，但二者主要用于验证 P3 的上下游边界：

- **P3 是当前产品与工程重点**：新框架按 Recall、Remember、Operate 三个流程组织，统一 Context Runtime 负责接入。
- **P2 是可替换的基础设施 Provider**：提供对象、向量和段控制能力，不反向定义 Memory Domain。
- **P4 是上层接入模拟器**：只通过冻结的 P3 Northbound v1 HTTP Contract 调用 P3。

> 新框架入口：`recall/`、`remember/`、`operate/`。Recall 的受理与执行骨架、共享
> 三流程当前实现位于各自 `basic/`，公共机制位于 `runtime/foundation/`，
> 由 `runtime/flows/host.py` 装配。旧 B1/B2/B3 及旧 Recall 骨架有兼容消费者，修改或删除须先验证引用与回归。
> 详见 [三个流程的结构与迁移边界](docs/flow_architecture.md) 和
> [Recall 第一批交付](docs/recall_admission_first_batch.md)。

## 新框架开发入口

```text
Recall                   Remember                   Operate
请求受理、选路、执行      记忆形成与生命周期          调度与优化
  ├── embedding/         批准 Passage 输入           策略与控制器
  └── vector_projection/ 投影领域状态                动作执行 Port
           \                |                         /
                  公共契约 + RF / P2 Port
```

新代码直接导入 `aether_agent_memory.recall` 下的实现。Embedding 的契约、共享执行和重试
位于 `recall/embedding/`，通过 Port 注入推理后端；不调用 B1 的 Embedding。
真实 ONNX/OpenVINO/IPEX 引擎已迁入该目录，ONNX 已完成本地真实推理验证，
启动与装配方式见 [真实 Embedding 使用说明](docs/recall_embedding_native.md)。
数据库、本地持久化及单机恢复由 RF 提供，生产恢复另验。当前统一门禁：

```text
python scripts/p3/validate_collaboration.py
```

以下仅为保留的旧受理骨架例子，不是当前三流程开发入口：

```bash
python examples/recall_admission.py
python -m pytest tests/unit/recall
```

以下旧系统说明记录仍在运行的兼容能力，不代表三个新流程已经全部迁移完成。

## 旧系统兼容架构

```text
企业平台 / Agent / Web / 微信小程序 / P4 Simulator
                         |
                 P3 Northbound API v1
                         |
                 AetherStore P3 Runtime
        +----------------+----------------+
        |                |                |
   B1 Embedding      B2 Memory       B3 Scheduler
        |                |                |
        +-------- Context Kernel ---------+
                         |
              Ports + Provider Adapters
                         |
           P2 / Redis / Milvus / Celery
```

浏览器、企业平台和小程序不直接访问 Redis、Milvus、Celery、B1 或 P2。所有调用统一经过 P3，
由 P3 负责身份 Scope、幂等、错误语义、降级边界和审计轨迹。

## 核心能力

### B1 Embedding Sidecar

B1 是独立 CPU Embedding 服务，负责旁路拦截、文本分块、向量化和吞吐指标：

- 支持单条与批量文本，返回 512 维归一化 FP32 向量。
- 支持 Query/Passage 输入语义。
- 已实现跨请求动态批处理、长度感知批处理、有界队列和 deadline fairness。
- 支持 FastEmbed/ONNX Runtime CPU 后端。
- Production Profile 已绑定 OpenVINO INT8、`AsyncInferQueue`、多 InferRequest，并禁止静默回退。
- 分别统计 `request_qps`、`effective_item_qps` 和 `vector_qps`。

合同中的 QPS 口径按 **Effective Embedding Item QPS** 管理：一条独立待向量化文本 Item
计为一次 Query，Batch 只是内部优化，不重复乘算。历史隔离实验曾达到
`2134.5 item/s`，但该结果不等于当前提交已完成正式验收；当前代码仍需在服务器 CPU 独占窗口
按冻结的模型、数据集、并发、亲和性和报告口径重新测试。

### B2 Memory Runtime

B2 已从“写入 + 检索脚本”扩展为完整的 Memory Application 层：

- Working、Episodic、Semantic Memory 的业务语义保持兼容。
- `Memory` 业务事实与 Projection、Processing、Placement、Value State 分离。
- `MemoryFormationPolicy` 将 Observation/Event 转换为 Memory、NoMemory 或 Pending。
- 统一 Recall Pipeline：Candidate Source → 校准/融合 → 策略过滤 → 排序 → Token Budget → ContextPack。
- 长文本经 Celery 异步处理，B1 负责分块与向量化，P2 保存对象和向量事实，Milvus 可作为可选投影。
- Recall 访问通过 best-effort telemetry 记录，不要求逐条同步重写主 Memory。
- 幂等生命周期、任务 Scope 校验、投影状态归一化和旧数据兼容已实现。

### B3 Semantic Scheduler

B3 将访问频率、语义相关性、时间因素、业务优先级和迁移成本转换为 Heat Score 与调度动作：

- 支持 `KEEP`、`PREFETCH`、`PROMOTE`、`DEMOTE` 等控制面动作。
- 候选转换和 signal logic 位于 B3 Application/Domain，不在 HTTP 层拼装。
- Action Log 和 Signal 可持久化并按 tenant/user/agent Scope 隔离。
- Production 默认使用 Shadow Mode，避免未验证策略直接改变数据面。
- 当前 P2 执行器完成的是确定性逻辑路由和状态回写，不代表真实对象字节已经物理迁移。

### Unified Context Kernel

P3 参考 [OpenViking](https://github.com/volcengine/OpenViking) 的统一上下文组织模式，建立了
自己的 provider-neutral Context Kernel；这里只参考架构思路，不复制其 AGPL 实现：

- 使用规范化 `aether://` URI 组织 Memory、Resource、Skill 和 Session。
- 提供 L0 Abstract、L1 Overview、L2 Detail/Transcript 分层内容。
- Context Catalog 支持目录浏览、单项读取、层级检索和有界 Reindex。
- Resource、Skill 和 Session Archive 均可进入独立派生投影队列。
- Session 支持消息追加、当前窗口、Commit、Archive 和异步 Memory Extraction。
- Context 查询记录 provider-neutral Retrieval Trace，便于解释命中来源与降级原因。
- Catalog 已作为 Recall Candidate Source 接入 `/api/v1/context`，新增来源不需要修改 Runtime 主流程。

## 旧系统运行时架构

P3 的稳定依赖方向为：

```text
FastAPI Host / API Routers
            |
      MemoryRuntime Facade
            |
   Application Use Cases
            |
 Memory Domain + Canonical Ports
            |
      Provider Adapters
   +--------+--------+---------+
   |        |        |         |
  B1       P2      Redis   Milvus/Celery
```

关键工程约束：

- `aether_agent_memory.app` 是唯一真实 P3 HTTP Host；`scripts/p3_service.py` 仅为兼容 wrapper。
- `AppSettings` 是 Host 的唯一配置 Source of Truth，经 Composition Root 构建长期存活的 Runtime。
- API Router 只调用 Runtime/Application Service，不直接访问 P2、Redis、Celery、Milvus 或具体 B1 Client。
- `MemoryRuntime` 是 Facade，不承载全部业务实现；Remember、BuildContext、SearchMemory、
  IngestLongMemory、ScheduleMemory 等 Use Case 位于 Application 层。
- Domain/Application 依赖 typed Ports 和 DTO；Adapter 负责把 Provider 数据转换为内部模型。
- P2 collection/namespace 规则封装为 Vector Namespace Strategy，不是 Memory Domain 规则。

异步恢复路径独立于同步北向请求：

```text
Memory write
   +--> Memory Projection Queue --> Projection Worker --> B1 + P2 vector projection
   |
Resource / Skill / Session Archive
   +--> Context Projection Queue --> Context Projection Worker --> semantic index
   |
Session commit
   +--> Session Extraction Queue --> Session Worker --> Formation Policy --> Memory
```

队列失败不回滚权威事实；租约、重试、Reconcile/Reindex 用于恢复漏投和临时 Provider 故障。

## 项目结构

```text
.
├── src/aether_agent_memory/
│   ├── recall/                 # 新 Recall 流程，含 embedding/、vector_projection/
│   ├── remember/               # 新 Remember 流程入口（实现待迁移）
│   ├── operate/                # Operate 模型、策略、控制器与 Port
│   ├── api/                    # FastAPI routers、dependencies、HTTP mappers
│   ├── application/            # P3 application use cases
│   ├── bootstrap/              # Composition Root
│   ├── core/                   # Memory Domain、Scope、enums、exceptions
│   ├── memory/                 # formation、retrieval、projection、repository
│   ├── context_store/          # URI、Catalog、层级检索、Reindex
│   ├── resource/               # Context Resource 模型与解析边界
│   ├── skill/                  # Skill 描述与 Port
│   ├── session/                # Session 生命周期、Archive、Extraction
│   ├── b1/                     # CPU Embedding Sidecar 与后端
│   ├── b2/                     # 长文本、压缩、任务状态与 Provider bridges
│   ├── b3/                     # Heat、策略、动作与执行边界
│   ├── adapters/               # P2/Redis/Milvus/Celery/B1 等实现
│   ├── runtime/                # 长生命周期 Facade、DTO、health、reliability
│   └── app.py                  # 唯一 P3 ASGI Host
├── src/aether_p4_simulator/    # 仅通过 P3 v1 契约接入的上层模拟器
├── engine/                     # P2 Rust 存储基座，当前不作为 P3 重构重点
├── web/                        # React + Vite + TypeScript 展示前端
├── contracts/                  # 冻结契约及验收输入
├── docs/adr/                   # 架构决策记录
├── benchmarks/                 # B1/B2/系统级性能脚本
├── scripts/                    # 兼容入口、部署、冒烟和验收脚本
├── tests/                      # 单元、契约、集成与 Compose 测试
├── compose.yaml                # integration 编排
└── compose.production.yaml     # production fail-closed override
```

## 快速启动

### Docker Compose 联调

要求 Docker、Compose 和足够的模型下载空间。默认启动 P2、P3、B1、Redis、Celery 及三个 P3
后台 Worker；Demo 与 Milvus 投影默认关闭。

```bash
docker compose up -d --build
docker compose ps
curl http://localhost:8080/health
```

启用 P4 参考应用：

```bash
docker compose --profile p4 up -d --build
```

启用可选 Milvus 投影：

```bash
AETHER_B2_MILVUS_PROJECTION=true docker compose --profile milvus up -d --build
```

Production Profile 使用 OpenVINO INT8 且 fail-closed：

```bash
docker compose -f compose.yaml -f compose.production.yaml up -d --build
```

服务地址：

| 服务 | 默认地址 | 说明 |
| --- | --- | --- |
| P3 API / OpenAPI | `http://localhost:8080` / `/docs` | 唯一北向入口 |
| B1 Sidecar | `http://localhost:18081` | P3 内部依赖，不建议直接暴露给业务端 |
| P2 gRPC | `localhost:50052` | P3 Provider |
| P4 Simulator | `http://localhost:8090` | 需启用 `p4` profile |

查看日志和停止：

```bash
docker compose logs -f p3 b1-sidecar celery-worker \
  p3-session-worker p3-projection-worker p3-context-projection-worker engine
docker compose down
```

### 本地 Python 开发

当前 P3 要求 Python 3.13：

```bash
uv sync --extra b1-sidecar --group dev --group demo
uv run python scripts/generate_proto.py
uv run python -m aether_agent_memory.app
```

兼容入口仍可使用，但最终会转发到同一个 Host：

```bash
uv run python scripts/p3_service.py
```

### Web 展示前端

前端只调用 P3 API，不直接连接基础设施：

```bash
cd web
npm install
npm run dev
```

默认页面包括 Overview、B1 Embedding、B2 Memory 和 B3 Scheduler。配置与真实 API 支持情况见
[web/README.md](web/README.md)。正式模式默认不使用 Mock；启用 Mock 时页面会显示 `MOCK MODE`。

## Runtime Profiles

| 能力 | demo | integration | production |
| --- | --- | --- | --- |
| Demo API | 可启用 | 默认关闭 | 禁止 |
| Mock/Fallback | 允许 | 受配置控制 | 禁止静默回退 |
| P2、Redis、B1 | 可选 | 联调依赖 | 必须且启动校验 |
| B1 默认策略 | Mock/ONNX | ONNX/真实 Sidecar | OpenVINO INT8 |
| B3 | Heuristic/Shadow | Heuristic/Shadow | 默认 Shadow |

使用 `AETHER_RUNTIME_PROFILE=demo|integration|production` 选择 Profile。Production 缺少真实依赖、
启用 Demo 或允许 B1 fallback 时，`AppSettings.validate_for_profile()` 会拒绝启动。

## HTTP API

### 冻结的 Northbound v1

以下路径的核心请求字段与成功状态码保持兼容，详细契约见
[docs/P3_NORTHBOUND_API_V1.md](docs/P3_NORTHBOUND_API_V1.md)：

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `GET` | `/health` | 核心 Runtime 与真实可探测组件状态 |
| `POST` | `/api/v1/memory/events` | 写入 Memory Event |
| `POST` | `/api/v1/context` | 构建带 Token Budget 的 ContextPack |
| `POST` | `/api/v1/b2/long-text` | 提交异步长文本任务 |
| `GET` | `/api/v1/b2/tasks/{task_id}` | 查询任务状态并校验 Scope |
| `POST` | `/api/v1/b2/search` | 检索长文本/向量记忆 |

### P3 扩展 API

| 范围 | 路径 |
| --- | --- |
| B1 | `/api/v1/embeddings`、`/api/v1/b1/embeddings`、`/api/v1/b1/status` |
| B3 | `/api/v1/b3/candidates`、`/api/v1/schedules` |
| Context Catalog | `/api/v1/context/catalog/children`、`item`、`search`、`reindex` |
| Resource | `/api/v1/context/resources`、`/api/v1/context/resources/{resource_id}/delete` |
| Skill | `/api/v1/context/skills`、`/api/v1/context/skills/{skill_id}/delete` |
| Session | `/api/v1/sessions/messages`、`current`、`commit`、`consolidate` |
| Recovery | `/api/v1/projections/reconcile` |
| Demo/Observability | `/api/status`、`/api/flows`、`/api/schedules`、`/api/run-smoke` |

HTTP 层统一返回可识别的 Invalid Argument、Not Found、Busy、Timeout、Unavailable 和 Internal
错误语义。API 不暴露 Provider 凭据或 Redis/Milvus/P2 Client。

## 验证

```bash
python -m pytest -q
python -m ruff check src tests scripts benchmarks
python -m mypy src
python -m compileall -q src
```

当前提交在本地验证结果：

```text
pytest       390 passed, 3 skipped
ruff         All checks passed
mypy src     200 source files, no issues
compileall   passed
```

真实模型和性能实验应回到安装了 `aether` Conda 环境、B1 模型及生产依赖的服务器执行；本地测试
主要用于功能、契约和架构回归，不能替代正式性能证据。服务器回归记录见
[docs/SERVER_REGRESSION_20260825.md](docs/SERVER_REGRESSION_20260825.md)。

## 当前边界

下列项目未被包装为“已完成”：

1. **2000 Effective QPS 尚需当前代码正式复验**：历史 OpenVINO INT8 结果证明方案具备潜力，
   但必须在 CPU 独占窗口按冻结 Acceptance Profile 重跑并归档证据。
2. **B3 物理迁移未完成**：现阶段是 Control-plane 决策、逻辑路由与状态回写，不宣称完成
   Milvus/P2/Redis 之间的真实字节搬运。
3. **Prediction Model 未部署**：当前 B3 使用 `heuristic-v1`，生产默认 Shadow Mode。
4. **语义 Memory Extraction Provider 未部署验收**：框架已有 typed Port 和可选 HTTP Adapter，
   默认仍使用确定性兼容策略。
5. **跨存储 Domain Event Outbox 未实现**：现有 Projection Queue、租约、重试和 Reconcile
   解决派生任务恢复，但不等同于完整事务 Outbox。
6. **索引物理回收仍需完善**：逻辑 tombstone 和查询过滤已实现，P2 stale vector 的物理删除、
   批量回收和服务器 reindex 压测仍待完成。
7. **原文所有权仍需收口**：长文本全文在 Memory 与 P2 E2 之间存在过渡期双写，需要按
   ADR-0003 统一引用、保留和删除策略。
8. **真实故障注入不足**：需要补充 Redis/P2/B1/Celery 中断、重复投递、租约过期和恢复演练。

## 后续优先级

1. 在服务器 CPU 独占窗口完成 B1 OpenVINO INT8 正式验收，冻结数据集、模型哈希、并发、
   CPU 亲和性及 Effective Item QPS 计数口径。
2. 完成 Context/Memory 派生索引物理回收与批量 Reindex 的生产验证。
3. 接入公司真实 Memory Extraction Provider，验证 Session Archive → Formation → Memory 闭环。
4. 建立持久 Domain Event Outbox，补齐跨存储对账、重复投递和断点恢复。
5. 冻结降级矩阵、数据保留/删除、部署拓扑、监控与容量规划等公司落地 ADR。

P2 与 P4 暂不做架构扩张；后续优化继续围绕 P3 的产品化、可靠性、性能与企业接入边界展开。

## 文档索引

- [P3 Runtime 架构](docs/p3_runtime_architecture.md)
- [P3 Northbound API v1](docs/P3_NORTHBOUND_API_V1.md)
- [架构决策记录](docs/adr/README.md)
- [工程审计](docs/ENGINEERING_AUDIT.md)
- [工程交付报告](docs/ENGINEERING_DELIVERY.md)
- [服务器回归](docs/SERVER_REGRESSION_20260825.md)
- [Web 展示前端](web/README.md)
- [手动场景测试](docs/MANUAL_SCENARIO_TEST.md)
