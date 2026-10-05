# Aether P3 — Agent 持久记忆服务

2026-10-04 当前运行路线：同一套 P3 源码通过配置连接 Azure PostgreSQL、Redis、Milvus、Ceph 和独立 Temporal。旧 SQLite 存储、RF 调度入口、旧迁移与重复测试已退役；有效测试迁移到独立 AKS Pod 和真实后端。旧数据迁移已取消，当前持久数据保留。开发者及 AI 的完整环境步骤见 [Azure 开发环境与 AI 配置指南](../交付成果/部署运行/P3_Azure开发环境与AI配置指南_20261004.md)。当前常驻镜像未被本次源码替换，测试和正式部署的边界见 [源码清理与部署就绪验收](../交付成果/测试与验收/P3_源码清理与部署就绪验收_20261004.md)。

历史组件接入记录（不作为当前部署方式）：2026-10-02 租户、日志和 Trace PostgreSQL：[接入说明与边界](docs/p3/development/16_PostgreSQL接入与数据迁移.md)。状态与节点日志使用上游存储；身份、P2、Temporal 的原有部署配置保持独立。

2026-10-01 Working / Milvus 历史增量：[当时的本地开发记录](docs/p3/development/Working与Milvus_本地开发指南.md)、[流程图](docs/p3/architecture/Working与Milvus_当前流程.svg)、[当时验收](docs/p3/development/Working与Milvus_验收报告.md)。这些记录保留原验证范围；当前开发使用真实 Azure Milvus，旧 Lite 启动器与配置已退役。

P3 将对话、任务信息和文档保存为可追溯记忆，并按请求身份、来源、版本、有效性及 token 预算返回上下文，供上层 Agent 使用。对外业务是 **Remember（存储记忆）**和 **Recall（召回记忆）**；Operate 是内部持续运行的热度、缓存与恢复机制。

**当前服务必须连接独立 Temporal Server。** 新环境不迁移旧 SQLite 数据；统一使用完整 Azure 配置及个人数据空间。启动、测试和恢复按上面的开发指南操作；2026-09-30 的 Temporal 交付记录只代表当时的验证范围。

租户入口与权限配置见 [租户与可观测性接入](docs/p3/development/12_租户入口与可观测性接入.md) 和 [Keycloak 组织成员权限](docs/p3/development/14_Keycloak原生组织成员与权限复用.md)。

## 服务如何工作

```text
对话 / 任务 / 文档 → Remember → 当前记忆 → 后台抽取、核验与长期化 → 正文和索引
查询 + 当前身份   → Recall   → 候选资格检查 → 精确版本正文 → 最终复核 → 上下文与来源
存储与成功读取事件 → 内部 Operate → 热度与定时衰减 → 缓存准备、读回确认及恢复
                         Temporal 编排持久任务；业务底座保留事务、幂等、事件与权限校验
```

默认在一个 Python 进程内装配 HTTP 与 Temporal SDK Workers，连接独立 Temporal Server。正文由 Ceph 保存，权威元数据与回执在 PostgreSQL，向量投影在 Milvus，热副本在 Redis；原生 BGE 负责向量编码，LLM 负责语义加工。Working/Episodic/Semantic 是业务记忆类型；当前 Azure 执行层仅声明经过配置的热副本，未配置的温/冷层迁移明确不支持。

## 快速启动

以下命令在 `AgentJYS-main/` 执行，使用 Python 3.13。先准备可访问的四种存储、Temporal、BGE 模型、结构化 LLM 和身份配置。公开服务配置要求 native embedding；词法实现仅用于明确的组件测试。

1. 复制 [Azure 测试模板](configs/p3.azure.example.yaml) 到仓库外的部署目录，命名为 `service.yaml`。开发、测试、预发、生产模板使用同一结构，分别选择独立的数据空间。
2. 同时准备 [Embedding 示例](configs/embedding.azure.example.json)、[Recall 策略](configs/recall.azure.json) 和实际 `identities.yaml`；按目标环境调整模型路径、证书路径、Ceph/Temporal/LLM 地址及权限。身份可参考 `configs/identities.jwt.example.yaml` 或 Keycloak 组织配置。
3. 通过受控环境变量/Secret 注入 `AETHER_POSTGRES_DSN`、`AETHER_REDIS_PASSWORD`、`AETHER_MILVUS_TOKEN`、`AETHER_CEPH_ACCESS_KEY`、`AETHER_CEPH_SECRET_KEY` 与模型密钥。PG 必须 verify-full，Redis/Milvus/Ceph 也验证 TLS。

```powershell
python -m pip install -e '.[embedding-onnx,resource-documents,recall-rerank,remember-ceph]'
python -m aether_agent_memory check-config --config E:/deployment/p3-test/service.yaml
python -m aether_agent_memory serve --config E:/deployment/p3-test/service.yaml --require-profile test
```

Linux 使用相同模块命令，并换为实际配置绝对路径。`check-config` 只验证配置和身份结构；不证明存储、模型或 Temporal 已可用。Azure Service 缺少必需后端或凭据会明确拒绝启动。

`data_dir`、`identity_file`、`embedding_config`、`recall_config` 和 CA 文件相对于服务配置文件解析。Embedding JSON 内的模型路径由其后端读取，需显式设置为正确路径。跨环境切换必须配套数据库/schema、缓存 namespace、向量 database、对象 bucket 及 Temporal namespace/deployment，不能只改 profile 或重建目录。

`init --directory <新的空目录> --template <完整Azure模板>` 现在只接受明确的 Azure 模板，不生成旧兼容栈、默认身份或凭据。先准备身份、模型及完整模板，再初始化；已有部署直接使用 `check-config` 和 `serve`。详细步骤见开发者指南。

### 验证首次保存与长期召回

保持服务运行，另开终端，进入 `AgentJYS-main`，重新设置相同变量后执行：

```powershell
$P3Home = Join-Path (Resolve-Path ../..).Path '.agent-work/aether/workspace-support/p3-local'
$P3Python = Join-Path $P3Home 'venv/Scripts/python.exe'
$P3Deploy = Join-Path $P3Home 'deployment'
& $P3Python scripts/p3/smoke_service.py --url http://127.0.0.1:8080 --credential-file (Join-Path $P3Deploy 'credential')
```

Linux/macOS 在第二个终端重新设置前述变量，再执行：

```bash
"$P3_PYTHON" scripts/p3/smoke_service.py --url http://127.0.0.1:8080 --credential-file "$P3_DEPLOY/credential"
```

脚本会写入一条带唯一标识的测试记忆、主动结束批次、等待长期索引、查询并再次获取结果。成功输出含 `"passed": true`、`operation_id` 和 `recall_id`；不会输出凭据。测试记忆会保留在部署数据中。

三流程运行可通过 `scripts/p3/monitor_demo.py` 验证，通过新版 Agent 的 P3 运维入口查看授权范围内的任务与诊断。

## Agent 与管理后台

旧 P4 故事页面与监测 Web 已于 2026-10-05 退役。使用 [Agent 平台](http://localhost:19010/)；登录后进入“我的记忆”，平台管理员可进入“P3 运维控制台”。账号与租户管理使用 [Budibase](http://localhost:19000/app/default%20workspace/aether-admin)。

新版实现位于既有 agent-platform 工作树的 `platform-web/` 与 `src/aether_platform/`，启动方式见交付成果中的 Agent 平台测试包。主工作区不自动复制该工作树的未提交实现。P4 Python 模拟器及其契约测试保留用于 P3 回归，不再提供旧 Web。

## 调用业务接口

受保护接口使用 `Authorization: Bearer <credential 文件内容>`。初始化身份拥有全部权限，仅用于本地接入起点；正式部署应配置实际身份和权限。请求的 `selection` 用于缩小业务范围，不能覆盖认证身份或租户。

下面是在 PowerShell 中保存一条偏好的示例，需先设置前述 `$P3Deploy`：

```powershell
$P3Headers = @{
    Authorization = 'Bearer ' + (Get-Content -Raw -Encoding UTF8 (Join-Path $P3Deploy 'credential')).Trim()
    'X-Operation-ID' = [guid]::NewGuid().ToString('N')
}
$P3Session = 'example-' + [guid]::NewGuid().ToString('N')
$P3Body = @{
    source = @{
        kind = 'conversation'
        external_id = $P3Session
        external_version = '1'
        occurred_at = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffZ")
    }
    selection = @{ session_id = $P3Session }
    content = @{ kind = 'text'; text = '用户喝咖啡不加糖。' }
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/p3/remember -Headers $P3Headers -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($P3Body))

# 新业务操作使用新 ID；重试同一次操作时保留原 ID 和请求体。
$P3Headers['X-Operation-ID'] = [guid]::NewGuid().ToString('N')
$P3Query = @{
    query = '用户喝咖啡不加糖'
    selection = @{ session_id = $P3Session }
    sources = 'both'
    token_budget = 1000
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8080/p3/recall -Headers $P3Headers -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($P3Query))
```

词法模式依赖词项相似度，示例使用相近措辞。未配置 LLM 时采用保守原文事件基线，不代表具备模型抽取、推理或压缩质量。Recall 的 `sources` 支持 `working`、`long_term`、`both`、`auto`。

**保存成功不等于长期索引已就绪。** 默认累计 32 条、8000 token 或等待 600 秒触发长期化。要主动结束会话批次，可向 `POST /p3/remember/consolidate` 发送 `{"session_id":"实际会话 ID"}`，再查处理状态或等待长期 Recall；上面的冒烟脚本已包含这一步。

| 接口 | 用途 |
|---|---|
| `POST /p3/remember` / `POST /p3/recall` | 保存记忆 / 返回上下文及来源 |
| `PUT /p3/documents/{id}?version=1` | 上传不可变版本原件，返回可交给 Remember 的 `content` |
| `GET /p3/memories` | 授权范围内的记忆目录 |
| `GET /p3/remember/{id}/processing` | 后台处理状态与任务 |
| `POST /p3/remember/{id}/correct`、`.../lifecycle`、`.../delete` | 更正、归档/激活、先阻断再清理 |
| `POST /p3/sources/{id}/revoke` | 撤销来源并传播使用阻断 |
| `/p3/remember/{id}/retention` | 保留策略 |
| `POST /p3/remember/reflection`、`/p3/remember/distill` | 条件化反思与显式提炼 |
| `GET /p3/recalls/{id}`、`.../result` | 查询进度、再次取结果；仍按当前版本和权限复核 |

字段、版本条件和错误响应以当前运行服务的 `/docs` 为准。TXT 可直接解析；PDF/DOCX 需安装 `resource-documents` 扩展。旧 `/api/v1/...` 文档对应兼容栈。

## 接入真实模型与存储

| 配置 | 作用及接入要求 |
|---|---|
| `profile` | development/test/staging/production，与存储实现选择独立 |
| `storage_mode: azure`、`metadata_backend: postgresql` | 完整 Azure 后端装配，不回退参考存储 |
| `azure_storage.postgres` | DSN 环境变量名及提前授权的专用 schema |
| `azure_storage.redis` | TLS 主机、端口、CA、密钥变量名；环境 namespace 隔离 |
| `azure_storage.milvus` | TLS 地址、CA/服务名、database/逻辑 collection 与 token 变量名 |
| `azure_storage.ceph` | HTTPS RGW、bucket、S3 凭据变量名和可选私有 CA |
| `embedding_profile: native`、`embedding_config` | BGE 中文 512 维编码；预置模型与一致的推理配置 |
| `language_model`、`verifier_model` | 语义加工及可选独立审核模型 |
| `recall_config` | 预算与可选 reranker；Azure 模式拒绝第二套 Milvus 连接配置 |
| `temporal` | endpoint、已存在的 namespace、deployment_id、task_queue_prefix 与可选 TLS |

模型服务需支持结构化输出与健康查询。模型空间、向量、正文提供方与历史数据持久绑定；更换时需显式迁移和验证，不得改标签或清库绕过。当前新配置模板支持 production profile，不代表生产验收通过。

## 持续运行与排障

`serve` 启动 HTTP、Temporal SDK Workers、事务意图转交、身份重载与依赖探测。业务事件、周期和维护由 Temporal Workflow 编排。每个宿主使用 `workers=1`。参考本地模式有目录锁；Azure 模式依赖 PG 原子性与持久绑定，多实例滚动和故障恢复仍需专项验收。需要进程退出后自动重启时，使用进程管理器或下方 Compose。

按 `Ctrl+C` 正常停止，服务先等待在途 Activity；下次使用同一配置、业务目录与 Temporal 持久历史启动后继续执行。HTTP 等待断开不取消 Workflow。结果未知（Unknown）的动作查询原动作 ID，不能换 ID 重做副作用。

| 检查入口 | 判断内容 |
|---|---|
| `GET /p3/live` | 无需鉴权；仅表示进程可响应 |
| `GET /p3/readyz` | 就绪探针；Temporal 不可用时返回 503，并暂停新接纳 |
| `GET /p3/ready`、`/p3/health` | 需要 DIAGNOSE 权限；检查必需依赖及能力证据 |
| `GET /p3/runtime` | worker、队列、过期租约和 Unknown 动作 |
| `GET /p3/tasks/{id}`、`.../progress` | 任务进度与恢复证据 |
| `GET /p3/operations/{job_id}`、`.../result` | 持久前台操作与重新鉴权后的结果 |
| `GET /p3/operate/memories/{id}` | 热度、输入水位和缓存动作 |
| `GET /p3/incidents`、`/p3/logs/{trace_id}` | 维护事件及受控诊断日志 |

常见问题：

- **端口占用**：初始化时增加 `--port 8081`，或修改现有 `service.yaml` 的 `port`，同时调整请求地址。
- **401/403**：检查 Bearer 凭据、租户启用状态、身份 epoch 和接口权限。修改身份配置需增加 `revision`，身份权限变更需增加 `auth_epoch`。
- **保存后长期 Recall 为空**：检查 processing，确认批次已结束、任务成功且索引发布完成，并核对当前模型空间与 P2 投影证据。
- **native 启动失败**：检查扩展依赖、模型下载/目录、空间绑定；不要用更换已有数据库模式的方式绕过校验。
- **live 正常但 ready 不可用**：查看授权 `/p3/health` 中失败的依赖，配置校验通过不能替代实际连接检查。

部署目录应持续保存。备份需覆盖 PostgreSQL 元数据与回执、Ceph 正文、Temporal 状态、身份配置、索引及模型绑定；只备份 PostgreSQL 不能完成全依赖灾备。业务历史和动作证据仍需容量规划、归档与备份周期；日志保留上限不代表所有业务数据已自动回收。

## Docker 部署

[Dockerfile.p3](Dockerfile.p3) 安装原生 embedding、文档处理、reranker 和 Ceph 所需依赖。先构建候选镜像，再验证实际依赖与业务：

```powershell
docker build -f Dockerfile.p3 -t aether-p3:azure-candidate .
```

[compose.p3.yaml](compose.p3.yaml) 可用于配置好网络的本机联调。先设置仓库外的 `AETHER_DEPLOYMENT_DIR`、`AETHER_RUNTIME_DIR`、`AETHER_MODELS_DIR`，分别挂载为 `/deployment`、`/runtime`、`/models`，配置使用对应的容器路径。Temporal endpoint 必须从容器实际可达；宿主 loopback 的开发 CLI 不会自动成为容器可访问的服务。可选 `p2` profile 仅保留 Rust P2 提供方的独立联调，不参与 Azure Service 装配。

[compose.production.yaml](compose.production.yaml) 使用指定摘要的 P3 镜像，强制 `--require-profile production`。设置 `AETHER_P3_IMAGE`、只读 `AETHER_DEPLOYMENT_DIR`、可写 `AETHER_RUNTIME_DIR` 和只读 `AETHER_MODELS_DIR`。容器配置使用 `host: 0.0.0.0`、`port: 8080`、`data_dir: /runtime`；模型权重位于 `/models`，模型缓存指向 `/runtime` 的可写目录。

Compose 传递上述四后端与模型的默认密钥变量名；若配置使用其他名字，应同步修改环境注入。容器必须实际可达私网 PG/Redis/Milvus 及 Ceph HTTPS，并具备授权 namespace/数据库/bucket。模板、镜像构建和 Pod Ready 都不能单独证明已达到上线标准。

现有演示服务未被本批变更替换。候选镜像、独立 AKS 测试与常驻正式升级分别取证；灾备和长稳须单独验收。旧 B1 OpenVINO Compose 入口已退役；仍有效的原生计算适配器按明确的依赖和配置启用。

## 当前状态

| 能力 | 当前实现与验证范围 |
|---|---|
| Remember → Recall 统一链路 | 保存、后台长期化、多块完整索引发布、资格检查、精确正文、最终复核、结果重取已接通并本地验证 |
| Temporal 与业务底座 | Temporal 编排任务、事件和周期；保留 PG 事务、Outbox/Inbox、权限、幂等和完成证据；正式 Temporal PG 依赖管理员补齐扩展和权限。旧数据迁移取消，当前任务恢复保留 |
| 原生 Embedding | 真 BGE 保存与召回已测；业务检索质量和生产吞吐仍需独立验收 |
| 租户与共享 | 本地已测隔离、共享发现、撤权、停用及重新启用 epoch 栅栏；P4 真实身份平台待集成 |
| 监测 Web | 已接统一 `/p3` 接口，提供健康、任务、异常和 Trace 瀑布图；尚无跨实例聚合、持久历史指标和云端 APM 接入 |
| 内部 Operate | 持久热度、定时衰减、缓存准备、读回确认、回收再唤醒已测；Azure 路线使用 Redis 热副本与 PG 回执，不支持未配置的温/冷层迁移 |
| 文档与模型加工 | 原件上传和解析入口、JSON 模型适配已接通；PDF/DOCX 需可选依赖，真实 LLM 质量及 5 倍压缩指标未验收 |
| 存储 / P2 接管 | PG/Redis/Milvus/Ceph 通过明确的 Azure 接口装配；当前 Ceph HTTP 联调需 opt-in，正式 HTTPS 待补齐；P2 后续按保留接口适配和联调 |
| 部署与运营 | Docker 实跑、24/72 小时长稳、性能、多实例、完整灾备、Azure/AKS/P4 真实部署待验证 |
| FR16（P1）预测预热 | 未实现，基础热度调度不能替代预测预热 |

2026-09-26 工程记录：

| 检查 | 结果 |
|---|---|
| 全仓 Python 回归（启用真 BGE，排除 Docker 环境项） | **1305 passed** |
| 收尾统一 HTTP 回归 | **16 passed**；与上项重叠，不相加 |
| 真实 TCP 保存、长期化、召回、重启后原结果重取 | 通过 |
| Ruff / 严格 Mypy | 通过 / **356 source files** 通过 |
| 合约、RF、三流程协作门禁 | 通过，具体分组与跳过项见验收报告 |
| Compose 配置 | 解析通过；无新容器实跑证据 |

上述数字引用 [该次工作区验收报告](../交付成果/测试与验收/P3_统一运行整合与验收_20260926.md)，不是本次 README 更新重新执行的全仓结果，也不代表新的远端 CI 或产品验收。短时回归不能替代长期运行及产品指标验收。

历史 README 校核（2026-09-28，以下为旧配置证据，不适用于当前部署命令）：在 Windows 已有 Python 3.13 环境中，使用新部署目录与 lexical 模式，通过初始化、配置校验、真实 TCP 就绪检查、长期召回冒烟及本文 PowerShell 保存/召回示例；临时服务已停止。未重新验证全新环境安装、Linux/macOS 实跑或 Docker 部署。

## 开发与文档导航

| 目录 / 文件 | 用途 |
|---|---|
| `src/aether_agent_memory/runtime/flows/` | 统一 CLI、配置、装配、HTTP 与持续执行器 |
| `src/aether_agent_memory/runtime/foundation/` | RF 事务、任务、事件、身份与诊断 |
| `src/aether_agent_memory/runtime/temporal/` | Workflow、Activity、接纳绑定、幂等重放与当前任务恢复 |
| `src/aether_agent_memory/remember/` | 保存、长期化、文档与模型加工 |
| `src/aether_agent_memory/recall/` | 原生编码、候选、正文、复核与上下文组装 |
| `src/aether_agent_memory/operate/` | 热度算法、持续调度和文件缓存执行 |
| `contracts/p3/`、`docs/p3/` | 需求基线、接口契约、架构与开发约束 |
| `tests/`、`scripts/p3/` | AKS 真实后端回归、契约检查与公开 HTTP 冒烟 |
| `configs/p3.production.example.yaml` | 统一服务配置样例 |
| `engine/` | P2 引擎与协议 |

在虚拟环境中安装工程测试依赖后运行静态检查和统一服务回归（Linux/macOS 同样替换解释器变量）：

```powershell
& $P3Python -m pip install -e '.[embedding-onnx,resource-documents,remember-ceph]' --group dev
& $P3Python -m ruff check src tests scripts
& $P3Python -m mypy src
& $P3Python scripts/p3/generate_schemas.py --check
```

完整回归通过 [AKS 测试入口](scripts/p3/run_aks_tests.py) 连接真实 Azure 后端；环境、资产、零跳过门禁和明确排除的 P2/Nginx 范围见 [当前开发者指南](../交付成果/部署运行/P3_Azure开发环境与AI配置指南_20261004.md)。历史验收数字保留原日期，不作为当前提交通过证据。

- [当前 Azure 开发、AI 配置与部署指南](../交付成果/部署运行/P3_Azure开发环境与AI配置指南_20261004.md)
- [2026-09-26 部署历史记录](../交付成果/部署运行/P3_统一服务运行指南_20260926.md)
- [PRD V1.3 基线](contracts/p3/prd-baseline.yaml) / [总体架构与流程](../交付成果/架构设计/总体架构与流程.md)
- [项目与交付成果入口](../README.md)
- [Agent 平台](http://localhost:19010/) / [管理后台](http://localhost:19000/app/default%20workspace/aether-admin)
- 旧兼容栈参考：[Runtime 架构](docs/p3_runtime_architecture.md)、[旧 Northbound API v1](docs/P3_NORTHBOUND_API_V1.md)、[旧 ADR](docs/adr/README.md)、[历史服务器回归](docs/SERVER_REGRESSION_20260825.md)。这些文档保留原日期及背景，不作为统一宿主的默认启动说明。

旧 `python -m aether_agent_memory.app`、旧 `compose.yaml`、B1/B2/B3 演示和 `/api/v1/...` 仅保留 demo/integration 组件兼容用途；旧 production 明确拒绝启动。新用户从本 README 的统一入口开始；可运行 Mock 是独立产品演示，不代表已经连接此服务。


### 待确认对象的显式恢复读取

`GET /p3/client-runs/{run_id}/recoveries/{transfer_id}/definition`、`.../inputs/{operation_id}` 和 `.../states/{sequence}` 要求 expected_owner_id、expected_revision、expected_record_hash；状态可带 stream_id。P3 读前后复验身份、RECOVER、active、完整接管记录及对象预约，返回原字节和 X-P3-Recovery-Object 元数据。pending 与 ready 分开，普通 GET 不放宽，读取不会发布或激活。缺失原字节不可由 hash 还原。P4 通过 read_recovery_definition/input/state 消费；ready 状态还必须与已知 head 相容。

本批最终定向 337 项通过；组合回归 572 通过/1 启动 RPCError 503，问题仍开放。实际默认入口：**44 项通过，326.27 秒；69/69 条方法＋路径有通过证据，report 状态 passed**。严格 Mypy 448 源文件、Ruff 全范围、11 文件格式及 760 个 Python 文件摘要核对通过。当前 P2 对象调用为真，参考元数据/缓存及测试 Temporal 不代表正式上线；待确认数据的恢复编排与逐效果安全续跑仍未完成。

### 恢复占用期间确认原定义

`PUT /p3/client-runs/{run_id}/recoveries/{transfer_id}/definition` 使用同一组预期 owner/revision/完整记录 hash。只有无业务日志且无已发布状态 head 的原运行可确认；P2 IO 前后复验完整恢复占用与原预约。空正文核对原对象；原 pending 缺字节时，须显式提供匹配原登记 hash/长度的原文。queued 尚未创建预约时记录实际恢复写入者，已有 pending 保留原 writer_id。仅将原定义标记 ready，不改变运行、head、hold 或 Temporal 任务；ready 缺失/损坏及 running 缺确认历史均拒绝修复。

P4 的 `confirm_recovery_definition(record, payload=None)` 校验原回执。丢失响应保持 unconfirmed，可通过原恢复 GET 区分 pending/ready；没有自动重发或启动故事。初始化恢复执行已接入下文的新方法；定义确认接口本身不派发故事，已有业务效果仍须逐效果恢复。

原定义确认本批相关回归：476 通过、0 失败、0 报错、0 跳过，274.19 秒。实际默认入口：48 项通过，330.70 秒；70/70 条方法＋路径有通过证据，report 状态 passed。764 个 Python 文件摘要一致；Ruff、严格 Mypy 450 源文件及 10 文件格式通过。旧启动故障、P2 同键并发失败和正式生产门禁仍开放。


### P4 原初始化恢复执行

`demo.recover_initialization(run_id, transfer_id, original_definition=None)` 是内部服务入口，需已有 P3 客户端配置。传入原 run ID 和明确的 transfer ID；原定义缺失时只允许提供匹配原登记的原始字节。运行必须没有业务日志、没有已发布 head、没有步骤结果，且冻结定义与当前执行器版本一致。已有业务效果不能走此入口从头执行。

入口在恢复占用内确认原定义，核对原 transfer/activation，读回原对象并复验当前所有权后才派发。当前实例重复调用沿用原 transfer ID；新实例必须通过新的显式转移取得所有权。响应或回执不可用保持未确认；线程提交失败后可继续核对原 transfer 重试，已排队的失效本地令牌不会启动第二次执行。原错误、输入、事件时间和运行/业务 ID 保留，旧 pending 初始状态不覆盖。

本批相关回归：571 通过、0 失败、0 报错、0 跳过，576.18 秒（JUnit）；实际默认入口：49 项通过、3 项失败，582.04 秒；70/70 条方法＋路径有通过证据，report 状态 incomplete，整体门禁未通过。27 项新增测试包含旧解释器终止后的新进程完整执行。Ruff、451 源文件严格 Mypy、6 文件格式及 768 个 Python 摘要通过。旧启动/P2 故障、逐效果续跑和正式上线验收仍开放；此方法尚未接入 P4 网页恢复操作。
