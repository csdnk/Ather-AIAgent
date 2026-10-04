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

若要在 Web 中观察三流程运行，将命令中的 `scripts/p3/smoke_service.py` 换为 `scripts/p3/monitor_demo.py`。它额外验证 Working 召回、连续读取触发 Operate 自动 hot 放置、放置后的召回和三流程诊断记录，输出可在监控页查询的任务 ID 与 Trace ID。详见 [边测试边监测](web/README.md#一边运行三流程用例一边查看真实轨迹)。

## 预设对话 Web 演示

默认 Web 入口提供 **五个固定故事**。选择场景后点击“开始演示”，由 P4 按预设步骤调用真实 P3；页面显示实际回复、进度、任务和可折叠证据。不是自由聊天模型，不需要在浏览器填写凭据。“监测控制台”入口保留，可直接打开 `http://127.0.0.1:5173/?view=monitor`。Keycloak 登录返回后自动进入监测台；在两页间切换会保留本次页面会话中选中的组织。

**身份边界：**监测控制台使用当前登录人的组织与权限；预设场景使用 P4 服务端配置的演示凭据，切换监测台组织不会改变演示凭据对应的租户。此入口仅用于本地演示，不能据此宣称外部 P4 已接入组织身份。

| 场景 ID | 步骤 | 验证内容 |
|---|---:|---|
| `library-full` | 9 | 文档上传、摘要发布、规则与个人记录召回、正文及原文片段、干扰后查询 |
| `weather-weekend` | 8 | 虚构天气设定、周末安排、长期化、仅共享本轮 task 范围的新会话召回 |
| `preference-update` | 8 | 版本更正、旧结果失效、归档/恢复、保留规则、重新处理 |
| `learning-review` | 8 | 学习记录长期化、反思设置、提炼任务与实际生成结果 |
| `forget-sources` | 7 | 本轮记忆删除、两个独立来源的删除/撤销、读屏障与清理状态 |

旧 `library-basic` 仅保留 API 兼容，不再列在新场景选择器。当前公开 Azure 服务使用 native BGE 和获批的结构化 LLM。组件测试中的固定模型只用于确定性故障断言；未配置提炼能力时，页面在实际任务和 `provider_unavailable` 证据确认后显示**条件不足**，不填入虚构总结。

覆盖面板列出当前源码的 **64 个方法＋路径组合**，分别统计“已调用”和“检查通过”。其中 11 项 `/p3/client-runs/…` 登记、定义、原输入、执行状态与执行权转移接口不计入故事业务覆盖，原操作与变更回执/原结果查询也单独验证恢复行为。每次只统计当前运行，不把一次故事或 HTTP 200 当作全接口验收。诊断只抽样本轮可关联元数据；运维写操作留给专用隔离环境，不随演示修改共享服务配置或执行恢复。

先按上文启动**独立 Temporal + P3**，确认 `/p3/readyz` 返回 200。在第二个 PowerShell 终端进入 `AgentJYS-main`，设置同一部署的 `$P3Python` 和 `$P3Deploy` 后启动 P4：

```powershell
$env:AETHER_P4_DEMO_ENABLED = '1'
$env:AETHER_P4_DEMO_P3_URL = 'http://127.0.0.1:8080'
$env:AETHER_P4_DEMO_CREDENTIAL_FILE = (Resolve-Path (Join-Path $P3Deploy 'credential')).Path
$env:AETHER_P4_BIND = '127.0.0.1'
$env:AETHER_P4_PORT = '8090'
& $P3Python -m aether_p4_simulator.server
```

第三个终端进入 `AgentJYS-main/web`，已安装依赖则无需重复 `npm ci`：

```powershell
$env:VITE_P4_PROXY_TARGET = 'http://127.0.0.1:8090'
$env:VITE_AETHER_PROXY_TARGET = 'http://127.0.0.1:8080'
npm run dev -- --strictPort
```

浏览器打开 `http://127.0.0.1:5173`，点击“开始演示”。演示凭据只从 **P4 服务端文件**读取，不填写到页面或 `VITE_` 变量。默认仅允许本机 5173 的 Origin。修改 Web 端口时，可用 `AETHER_P4_DEMO_ORIGINS` 配置精确 loopback Origin 的 JSON 数组；外部域名、通配符、路径和用户信息会在监听前拒绝。

本地容器联调可在已经初始化的 **development/test 专用部署**上使用 [compose.p4-demo.yaml](compose.p4-demo.yaml)：

```powershell
docker compose -f compose.p3.yaml -f compose.p4-demo.yaml up -d --build
```

Web 默认访问 `http://127.0.0.1:3000`，`AETHER_HOST_WEB_PORT` 同时调整发布端口和 P4 Origin。P4 与 Web 共享网络命名空间，仍只监听 `127.0.0.1:8090`；服务端凭据只读挂载，Nginx 仅代理演示接口。此覆盖文件只用于本机联调。默认 Web 镜像及生产 Compose 显示“演示未启用”；正式身份和 P4 持久运行尚未验收。Nginx/P4/P3/Temporal/当前 P2 的本机 socket 测试通过不等于 Docker 镜像或 AKS 发布通过。

同一调用方同时只运行一轮，完成后手动新开独立范围。HTTP 400 `REQUEST_IN_PROGRESS` 只观察原 job，不重发原 POST；观察超时/结果未知会停住依赖步骤。“重新查询”只 GET。P4 通过受认证的 P3 `POST/GET/PUT /p3/client-runs/{run_id}` 保存原 UUID、场景、固定 scope、所有者和进度；步骤操作先保存意图，再保存原 operation/job 回执。P3 消费事务与身份接口；新 Azure 配置使用 PG 元数据与 Ceph 原始输入，P2 接管适配和完整 P4 恢复仍需验收。

新版本登记的已完成运行经过实际 P4 进程重启后，可以读取原步骤、记忆和任务结果；同 UUID 再次开始返回原登记，不执行业务。进度 CAS 或依赖异常会停止后续动作。中断运行保留占用，新 P4 所有者不会自动接管；原所有者超过 120 秒未更新时显示 `unconfirmed`，需要核对原操作，不能以新 UUID、清库或重启解除。每个调用方持久保留最多 10 轮，重启不会重置上限；尚未提供清退或人工恢复入口。

升级前未登记的旧 UUID 仍缺少 UUID→scope 迁移证明，404 **不证明旧写入未执行**，不得重新提交。原版本进程重启后重复生成 3 条记忆的失败证据保留。中断安全续跑、旧记录迁移、正式身份和正式 P2 灾备仍是上线未完成项。

原 HTTP 响应丢失而只有 operation ID 时，可调用受认证的 `GET /p3/operation-requests/{operation_id}?kind=remember.save` 找回原 job。`kind` 支持 `remember.save`、`recall.execute`、`remember.correct`、`remember.document`。返回 `found` 时包含原 job、任务状态、持久输入描述的 hash 和 workflow ID，再通过原 `/p3/operations/{job_id}` 与 `/result` 核对；`found` 不等同业务成功。返回 `unconfirmed` 不证明没有写入，不允许据此重发。接口不重新准入、不修改登记、不扫描全部任务，也不释放 P4 的 active 占用。

其余 Remember/Source 写入口使用 `GET /p3/mutation-receipts/{operation_id}?kind=remember.delete` 查询原事务回执。支持 `remember.consolidate`、`remember.distill`、`remember.reprocess`、`remember.reindex`、`remember.lifecycle`、`remember.retention`、`remember.reflection`、`remember.delete`、`source.delete`、`source.revoke`。`committed` 表示同步变更和原任务准入已提交；后台投影、清理或提炼结果仍通过返回的原 task IDs 核对。缺少回执返回 `unconfirmed`，包括缺少身份来源信息的旧操作记录，不能将它解释为未执行。

同类写操作的 operation ID 固定绑定调用方、home scope、auth epoch、目标和规范化请求摘要；复用 ID 改变目标或请求返回冲突。空整合结果也持久化，重复请求不会处理后来到达的输入。查询仅返回引用和摘要，不泄露历史正文。生命周期回执的 `result_basis=metadata_without_content`，hash 对应去掉 `content` 的响应元数据（保留原 `content_hash`）；其他类型为完整响应 JSON 的规范化 hash。新的生命周期回执不存正文副本，也不再写入旧操作表；POST 重放通过 P2 读取并验证原版本正文。P4 已提供原结果查询、整轮只读核对和恢复占用转移；转移后的恢复执行及安全续跑仍未完成。

`GET /p3/mutation-receipts/{operation_id}/result?kind=…` 返回上述十类同步变更的原 `receipt` 和原 `response`，P3/P4 校验 RFC 8785 JSON 摘要及原 operation/kind；GET 不触发业务重放。读取要求当前身份、原 auth epoch 和每个原目标的现行 READ 权限。生命周期返回原来去掉正文的元数据，即使后来恢复 active，也不会把原 archived 结果替换为今天的状态；空合并也保留原空结果。缺少可验证原结果返回 `COMMIT_UNCONFIRMED`，不能推断未执行。成功响应设置 no-store。

P4 的 prepared 操作现在包含 `binding`：版本 1、实际编码路径和查询串、Content-Type，以及方法/模板/operation ID/正文 SHA256/目标/内容类型的规范化摘要。登记发生在网络发送前，取得响应时保持绑定不变。P3 拒绝新增无绑定操作和改写原绑定；旧无绑定记录仍可读取，中断记录不能补造目标或据此解锁。日志不保存正文或凭据请求头。该绑定证明 P4 准备发送的请求，尚不等同 P3 已接纳，也不授权重发或运行接管。

P4 的发送顺序为：保存 prepared 意图 → 保存并核对原始 HTTP 字节 → 发送业务请求 → 保存 observed 回执。原始字节通过 `PUT /p3/client-runs/{run_id}/inputs/{operation_id}` 交给 P3，再经现有 P2 对象接口保存；请求必须携带 `X-P3-Run-Owner` 和正整数 `X-P3-Run-Revision`。元数据先记录 pending，事务外写入并读回 P2 字节，再检查当前身份、原 owner、revision 和 active 占用，最后确认 ready。原始 JSON、二进制文档和空正文均保持原样，正文不进入浏览器 snapshot。

`GET` 同一路径在当前身份下读取 ready 的原始字节，并返回 `X-P3-Request-Hash` 和 `Cache-Control: no-store`。P3/P4 都核对原字节摘要；缺失、损坏、pending 或无历史绑定均明确失败。保存回执丢失时，P4 保留原 prepared 操作并停止业务发送；ready 回执重试仍须读取 P2 验证内容及当前 owner，不会修补缺失对象。本能力保存已构造的请求；固定的后续输入另由下述运行定义保存，动态结果及执行权恢复仍未完成。

新 prepared 操作随 checkpoint 原子登记“调用方＋operation ID → 原运行＋请求绑定”索引。该操作进入四类 Temporal 命令或十类同步变更时，必须携带 `X-P3-Run-ID`、`X-P3-Run-Owner`、`X-P3-Run-Revision`；P3 在业务事务中核对当前身份、owner/revision、running/active、prepared 和已确认原字节及请求目标。省略这些请求头不能绕过已登记操作的校验。P4 在原输入保存确认后读取当前登记修订号并发送；命令输入暂存前还有前置校验，P2 IO 后仍须通过最终事务检查。

原操作查询的 `http_request.client_run` 保存首次准入所验证的执行者声明；普通调用和历史证据为 `null`。已接纳的原 Temporal 任务继续使用原 job/workflow ID 与执行 fence；登记变化不取消原任务，已 observed 或旧 owner 的请求应通过 GET 查询原结果。升级前的旧 checkpoint 不自动补造索引，也不因此取得本项隔离保证；旧 P4 在新 P3 上新增登记的操作会建立索引，后续若缺少执行者声明则被拒绝，应配套升级同一版本 P3/P4。不提供自动接管或安全续跑，也不取代正式身份和 P2 事务验收。

新版 P4 登记要求 `scope_policy=p4_task_v1`。P3 在登记事务内分配并保留 `p4r_<32位小写十六进制>` 命名空间，关联原 run 引用；task 等于该标识，session 为空或该标识加 `_session`。该保留语法同时用于文档 ID 本身及其下划线后缀，普通请求不能抢占未登记的保留名称。P3 根据实际记忆、来源、明确选择的 scope 或 document ID 检查目标，换一个未登记 operation ID、去掉运行请求头或只保留 session 也不能写入保护范围。托管声明也不能访问另一运行的写入目标。

普通授权读取及其他范围的写入保持既有权限规则；未指定任务的 Recall 可以按原规则查询，明确查询托管 task/session 的 Recall 需通过原运行准入。合并和反思策略继续按完整 scope 精确匹配。任务绑定的 home scope 不能登记此策略。保护保留到运行结束之后，历史数据不会自动获得本项保证；终止运行的数据清退、容量、显式迁移和转移后的恢复执行仍待实现。P4 可 GET 读取旧记录，但不会接受旧策略用于新执行或 checkpoint。

新 P4 运行在首次登记中绑定不可替换的定义摘要、大小和格式。`PUT/GET /p3/client-runs/{run_id}/definition` 经 P2 保存和读取版本化定义；保存要求相同 owner/revision、运行仍 queued 且占有 active，P2 写入前后均鉴权。带定义绑定的运行在定义 ready 前不能进入 running 或登记业务操作。GET 返回原字节、`X-P3-Definition-Hash` 和 no-store；旧无定义运行保持可读，不补造定义。

定义保存固定步骤文本、上传原文、额外业务文本、策略参数和每步事件时间。当前六个固定演示使用定义生成时的 UTC 基点加步骤毫秒序号，恢复时不重新取事件时钟。P4 读回并核对定义后才执行；实际 HTTP 字节仍按逐次请求保存。定义还绑定执行及请求契约代码、序列化库的摘要；版本不兼容时须保留旧执行版本或明确迁移，不能自动使用当前代码续跑。仅记录了摘要但尚未完成定义存储的运行保持未确认，不从当前场景还原正文。

P3 新增可选的 `state_policy=p4_state_v1`，首次登记必须同时绑定 `p4_task_v1` 和原定义；旧登记不能补写策略。`PUT/GET /p3/client-runs/{run_id}/states/{sequence}` 保存与读取原 JSON 字节，序号从 1 连续增长至 1024，每份不超过 256 KiB 且受配置的对象输入上限约束。请求包含原 run/scenario/scope、definition hash、父 hash、完整原操作列表和调用方数据；操作列表必须与当前登记一致，已有操作顺序不得重排。凭据、请求头、重复 JSON 键及过深结构被拒绝。

保存使用与原输入相同的 owner/revision 请求头，先保留 pending，P2 写入并读回验证后，在同一事务内确认 ready、发布 head 并将 run revision 加一。P2 IO 前后检查当前身份、owner、revision 和 active；错父链、同序号换内容和过期执行者均拒绝。只有相同原字节、原 writer/revision、相同当前 head 且尚无后续修订时，PUT 才确认丢失的保存回执；历史状态通过 GET 读取。已 ready 的对象丢失或损坏不会由重试重建。GET 在读取前后检查权限与发布绑定，返回 `X-P3-State-Hash` 和 no-store，终态也可读取历史状态。

新版 P4 的六个场景首次登记时请求并核对 `state_policy=p4_state_v1`。每次业务调用前先保存原输入和执行状态，再使用状态保存返回的新 run revision 发送请求；ready head 的操作摘要必须与当前 prepared 日志一致。日志新增或更新后，旧 head 不足以支持下一次业务请求。状态保存失败或回执丢失时停止后续效果，保留原登记和不确定性。

`demo/state.py` 保存记忆/来源引用、原 Remember 回执、episode 分组、轻量 Recall 标识、上传文档元数据、提炼前目录基线、原任务标识及清理任务列表。阶段明确区分调用前、HTTP 已观察、结果已解析、结果已消费和步骤完成；消费回执不等于异步任务或投影完成。状态不保存 Recall 正文或完整 ContextPack。`parsed_hash` 是解析后模型表示的摘要，生命周期去除正文；它与服务端原变更回执的权威 `result_hash` 含义不同。

`DemoService.restore_execution(run_id)` 经 GET 读取原登记、定义和状态，核对代码版本、字节/父链绑定、原引用与操作前缀，并在读取后再次核对登记没有变化。它只复原数据，不申请所有权或启动故事。状态落后于当前日志时返回 `journal_matches=False`，保留尚未消费的操作尾部；缺失、损坏或不兼容状态明确拒绝，不从当前目录或场景文本补造。

`DemoService.reconcile_execution(run_id, timeout_seconds=60)` 在复原后按当前完整日志顺序读取原输入、原 HTTP 准入、原 job 和原结果，覆盖四类 Temporal 命令及十类同步变更。报告保留原操作/查询证据、身份与请求匹配结论、任务状态、原结果描述及与已保存解析摘要的比较；不会保存新读取的输入或结果正文，也不会改写日志、消费标记、head 或 active。生命周期只核对原提交的 `metadata_without_content`，不构造带正文的快照；后台任务创建成功和任务完成仍分别表示。未找到原准入、任务处理中/失败/效果未知、旧 Recall 失效以及摘要不一致都有明确结果。

核对中的所有 HTTP 调用均为 GET，共享一个剩余时间预算；每次网络读取和返回报告时检查预算，超时或最终原登记变化会拒绝整份报告。网络使用 HTTPX 分阶段超时，不能据此声称底层网络调用在绝对时间点被强制取消。有所有权历史时，原准入 owner 必须匹配其准入 revision 所属区间，且不能超过当前修订；无历史记录继续严格比较当前 owner。报告只证明读取时的逐项证据，不授予继续执行权限。

`POST /p3/client-runs/{run_id}/transfers/{transfer_id}` 原子取得恢复占用：请求固定原 owner、revision、完整记录的 RFC 8785 SHA256 和新 owner；P3 在原事务中检查当前身份、RECOVER 权限及 active，更新 owner/revision/所有权历史，并保存不可变的转移前后回执。`GET` 同路径查询原回执；丢回执保持 unconfirmed，必须用原 transfer ID 核对。相同 ID/意图返回原结果；不同意图、旧记录、无所有权历史或重复 owner 均拒绝。历史最多 16 个 owner 区间，达到上限仍可查询和确认原转移，不能再增加新区间。

转移后 `recovery_transfer_id` 标记运行处于待恢复状态，原 snapshot、错误、日志、定义和执行状态 head 保留，active 不释放。旧执行者不能继续写入，新 owner 的普通业务、进度和状态/输入/定义写入也被拒绝；已 ready 的原输入和定义可以核对原字节后返回原回执。未开始的初始化另有显式原定义确认接口，见下文。已经准入的 Temporal 任务仍按原 job/workflow ID 和 fence 执行。`DemoService.transfer_execution(report, transfer_id)` 只取得该占用，不创建本地运行或启动故事。

`POST/GET /p3/client-runs/{run_id}/recoveries/{transfer_id}` 实现显式激活及原结果查询。激活固定当前 owner/revision/完整记录摘要，先经 P2 保存新状态，再原子提交状态 head、解除 hold 和原回执；原 snapshot 只改 state，错误、日志和已受理任务保持原样。未开始的 queued 运行可以继续原定义初始化；running 恢复必须保存完整原日志并引用已确认父状态。

状态的 `stream_id`/`parent_stream_id` 在接管后使用 transfer ID，旧状态链及 pending 预约保留；初始空标识不改变旧 key 或哈希。P4 的 `activate_execution(report)` 和客户端已支持该契约，激活丢响应后用 `lookup_client_recovery` 查原结果，当前 head 的读写会携带状态链标识。P3/P4 需要同步使用此契约版本。

单独激活不会启动本地故事工作线程。P4 已支持未进入业务的初始化恢复；已有业务日志或已发布状态的逐效果续跑、历史迁移和容量清退仍未完成。不能通过普通 checkpoint 把 unconfirmed 改回 running；已有激活与接管能力不代表已完成安全续跑或上线验收。

同一所有者原样重试已提交的旧 checkpoint 时，当前身份校验通过后返回原记录，保留原 revision 和 active 占用；该确认不补造绑定或改写回执。P4 的实际 HTTP 字节摘要、P3 任务输入描述摘要和业务意图摘要含义不同，不能直接互相比对；请求核对使用下面的独立 HTTP 证据，安全续跑仍待完成。

现有原操作/变更回执查询新增可选的 `http_request`：由 P3 在 HTTP 入口独立计算请求字节摘要、业务路径/有效查询参数和内容类型，与首次任务准入或同步变更同事务保存。重复请求保留原证据，旧任务/回执不补写此字段。P4 当前客户端的 `confirm_operation(original_operation)` 返回 `matched / mismatch / unconfirmed` 并保留原查询结果；它只发 GET，不修改日志或接管运行。`matched` 表示请求与原准入/事务相符，原任务仍可能失败；后台完成、原输入可恢复性和安全续跑需要分别核对。P3 与 P4 应使用包含该可选响应字段的同一版本契约。

调用方日志可能保留被 HTTP 校验拒绝的请求。文档请求缺少 `version` 时，如果查到带版本的原服务端证据，核对返回 `mismatch`；缺少任一端证据时仍为 `unconfirmed`。核对不根据服务端记录补写调用方版本，也不重新提交原请求。

关闭顺序：Web、P4、P3 各自终端 `Ctrl+C`，最后用 `scripts/p3/temporal_dev.py stop --directory <原专属Temporal目录>` 停止本轮拥有的 Temporal；保留部署数据，不删除数据库、不停止共享实例。测试与当前环境限制应与代码一同评审，不以一次页面通过代替全项目验收。

演示回归在业务目录执行：

```powershell
python -m pytest tests/unit/p4_validation tests/unit/p4_demo tests/integration/test_p4_demo_stories.py tests/integration/test_p4_demo_temporal.py tests/integration/test_p4_demo_handoff.py -q
npm --prefix web test
npm --prefix web run build
npm --prefix web run lint
```

真实 Temporal 集成需要已安装的固定版本 CLI（按测试约定设置 `P3_TEMPORAL_CLI`），开发依赖按 `python -m pip install -e . --group dev` 安装。上述 Python 业务测试需完整 Azure 测试配置；推荐通过开发者指南的 `run_aks_tests.py` 在 AKS 执行相同文件。

**接口专项验收**另有可复现入口，复用五故事、恢复契约及已有隔离运维测试，不向正在演示的服务发送请求；以该次生成报告的 total_routes、verified_routes、status 和失败项为准，不能仅凭调用过脚本认定全部验收。以下沿用前文的 `$P3Home` 和 `$P3Python`，每次使用新的仓库外目录：

```powershell
$P3Evidence = Join-Path $P3Home ('checks/demo-' + [guid]::NewGuid().ToString('N'))
& $P3Python scripts/p3/validate_demo_interfaces.py --directory $P3Evidence
```

默认执行全接口所需的测试；追加 `--full-suite` 可同时运行全仓 `pytest`。不支持 `pytest-xdist` 并行归因。报告 `report.json` 按方法＋实际注册的路径模板记录完整 HTTP 响应状态和对应通过的测试；仅枚举接口、Mock、跳过/失败的测试及单纯收集用例不会计入通过。测试还会对照实际路由注册集合，防止清单漏增或漏删。

激活阶段的默认选择包含六个原故事日志核对、转移和激活回执测试。新增激活后的 42 项定向验证通过；组合回归为 750 通过/3 次就绪门禁 503，带诊断复查 3 项通过但原根因仍开放。默认入口首次 36 通过/1 失败，暴露提炼测试忽略周期反思先完成的竞态；按原任务和目录基线纠正测试并增加确定性复用场景后，最终实际默认入口 **38 项通过、66/66 条接口有通过证据**。该证据覆盖 P3 ASGI、测试 Temporal 和配置后的当前 P2 对象调用，元数据/缓存及部分模型仍为测试参考组件。不同批次不累计，最终接口通过不抵销组合回归的启动稳定性问题、P2 同键并发输入写入失败和全仓 Docker/跳过项，不能当作整体上线验收。

两个身份接口由登录/身份测试验证，在未调用它们的故事覆盖面板中保持“未执行”。五场景与只读诊断最多涉及 40 项；另 8 项任务/周期控制、配置、备份、恢复演练及退役维护契约在独立临时数据和 test-owned Temporal 中验证。3 项运行登记接口随故事真实调用，但其持久、幂等、所有权和回执丢失语义另见 `tests/integration/test_client_run_registry.py`、`test_p4_demo_recovery.py`、`test_p4_demo_reply_loss.py`。退役维护接口的 410 只代表正确拒绝，普通权限不足的 403 不代表正常功能通过。提炼成功路径使用确定性测试 provider 检查原任务产物引用；默认 S4 的 `provider_unavailable` 仍是条件不足。

这些证据的边界是 **P3 真实 ASGI 路由与测试专属 Temporal，五故事还经过 P4 TCP**，不是所有接口均经过 Native TCP 的验收，也不证明 Docker、真实 P2、真实 Embedding/Milvus 或真实模型质量。全仓测试的失败和跳过仍需单独记录，不因接口有调用记录就忽略。

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

## 打开真实监测 Web

后端启动后，在另一个终端进入 `AgentJYS-main/web`，使用 Node.js 22 执行：

```sh
npm ci
npm run dev
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)，点击“配置连接”，输入部署目录 `credential` 文件内容。页面通过同源 `/p3` 代理读取真实的健康、Worker、任务、异常与 Trace；后台地址默认是 `127.0.0.1:8080`。权限、端口修改、容器部署及限制见 [Web 使用指南](web/README.md)。

监测界面提供运行总览、依赖探测、任务分页与详情、业务链路筛选、Trace 瀑布图和异常记录。趋势仅来自连接后的实际采样；凭据只保留在页面内存。统一 Compose 同时启动 Web，默认访问 `http://127.0.0.1:3000`。

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

[compose.production.yaml](compose.production.yaml) 使用指定摘要的 P3/Web 镜像，强制 `--require-profile production`。设置 `AETHER_P3_IMAGE`、`AETHER_WEB_IMAGE`、只读 `AETHER_DEPLOYMENT_DIR`、可写 `AETHER_RUNTIME_DIR` 和只读 `AETHER_MODELS_DIR`。容器配置使用 `host: 0.0.0.0`、`port: 8080`、`data_dir: /runtime`；模型权重位于 `/models`，模型缓存指向 `/runtime` 的可写目录。

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
- [真实监测 Web](web/README.md)
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
