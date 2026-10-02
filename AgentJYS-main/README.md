# Aether P3 — Agent 持久记忆服务

2026-10-02 租户、日志和 Trace 接入复用项目已有 PostgreSQL：[接入说明与边界](docs/p3/development/16_PostgreSQL接入与数据迁移.md)。状态与节点日志使用上游存储；身份、P2、Temporal 的原有部署配置保持独立。

2026-10-01 Working / Milvus 增量：[本地开发与 PyCharm 配置](docs/p3/development/Working与Milvus_本地开发指南.md)、[当前流程图](docs/p3/architecture/Working与Milvus_当前流程.svg)、[真实功能验收](docs/p3/development/Working与Milvus_验收报告.md)。Working 的语义召回现使用原生 Query 编码与来源过滤后的向量搜索；官方 Lite 已通过本地持久化验证，生产集群仍待验收。

P3 将对话、任务信息和文档保存为可追溯记忆，并按请求身份、来源、版本、有效性及 token 预算返回上下文，供上层 Agent 使用。对外业务是 **Remember（存储记忆）**和 **Recall（召回记忆）**；Operate 是内部持续运行的热度、缓存与恢复机制。

**状态更新：2026-09-30。统一服务已接入 Temporal，启动时必须连接独立 Temporal Server。** 本地验证结果与未验收项见 [Temporal 工程验收](../交付成果/测试与验收/P3_Temporal接入工程验收_20260930.md)，新建与已有目录操作见 [运行和迁移指南](../交付成果/部署运行/P3_Temporal本地运行与迁移指南_20260930.md)。

租户入口与权限配置见 [租户与可观测性接入](docs/p3/development/12_租户入口与可观测性接入.md) 和 [Keycloak 组织成员权限](docs/p3/development/14_Keycloak原生组织成员与权限复用.md)。

## 服务如何工作

```text
对话 / 任务 / 文档 → Remember → 当前记忆 → 后台抽取、核验与长期化 → 正文和索引
查询 + 当前身份   → Recall   → 候选资格检查 → 精确版本正文 → 最终复核 → 上下文与来源
存储与成功读取事件 → 内部 Operate → 热度与定时衰减 → 缓存准备、读回确认及恢复
                         Temporal 编排持久任务；业务底座保留事务、幂等、事件与权限校验
```

默认在一个 Python 进程内装配 HTTP 与 Temporal SDK Workers，连接独立 Temporal Server。任务不需要调用方手动推进；默认正文与向量使用本地 SQLite，缓存使用文件系统。可通过配置接入原生 BGE、LLM、P2 及 Milvus。Working/Episodic/Semantic 是业务记忆类型；hot/warm/cold 是内部访问准备层级。

## 快速启动

以下命令均在 **`AgentJYS-main/`** 中执行。从仓库根目录开始时先运行 `cd AgentJYS-main`。使用 Python **3.13**，先用 `lexical` 模式跑通保存与查询；需要 Temporal Server，无需下载 BGE 模型。当前 SDK 固定为 1.33.0，开发 CLI 固定为 1.9.1。

### Windows PowerShell

虚拟环境和部署数据保存在仓库外；`$P3Home` 可改为自己的外部工作目录。直接调用虚拟环境解释器，无需修改 PowerShell 激活策略。

```powershell
$P3Home = Join-Path (Resolve-Path ../..).Path '.agent-work/aether/workspace-support/p3-local'
$P3Python = Join-Path $P3Home 'venv/Scripts/python.exe'
$P3Deploy = Join-Path $P3Home 'deployment'
python --version
python -m venv (Join-Path $P3Home 'venv')
& $P3Python -m pip install -e .
& $P3Python scripts/p3/install_temporal_cli.py --directory (Join-Path $P3Home 'tools')
$P3TemporalState = & $P3Python scripts/p3/temporal_dev.py start --directory (Join-Path $P3Home 'temporal') --binary (Join-Path $P3Home 'tools/temporal.exe') | ConvertFrom-Json
& $P3Python -m aether_agent_memory init --directory $P3Deploy --embedding-profile lexical --temporal-endpoint $P3TemporalState.endpoint
& $P3Python -m aether_agent_memory check-config --config (Join-Path $P3Deploy 'service.yaml')
& $P3Python -m aether_agent_memory serve --config (Join-Path $P3Deploy 'service.yaml')
```

### Linux AMD64

```bash
P3_HOME="$HOME/.local/share/aether/p3-local"
P3_PYTHON="$P3_HOME/venv/bin/python"
P3_DEPLOY="$P3_HOME/deployment"
python3.13 -m venv "$P3_HOME/venv"
"$P3_PYTHON" -m pip install -e .
"$P3_PYTHON" scripts/p3/install_temporal_cli.py --directory "$P3_HOME/tools"
"$P3_PYTHON" scripts/p3/temporal_dev.py start --directory "$P3_HOME/temporal" --binary "$P3_HOME/tools/temporal"
# 将上一条输出的 endpoint 填入此变量。
P3_TEMPORAL_ENDPOINT='127.0.0.1:替换为实际端口'
"$P3_PYTHON" -m aether_agent_memory init --directory "$P3_DEPLOY" --embedding-profile lexical --temporal-endpoint "$P3_TEMPORAL_ENDPOINT"
"$P3_PYTHON" -m aether_agent_memory check-config --config "$P3_DEPLOY/service.yaml"
"$P3_PYTHON" -m aether_agent_memory serve --config "$P3_DEPLOY/service.yaml"
```

`check-config` 成功输出 `{"valid": true, "profile": "local"}`；它只检查配置，不证明模型或外部服务可用。`serve` 持续占用当前终端，默认监听 `127.0.0.1:8080`。打开 [接口文档](http://127.0.0.1:8080/docs) 或 [存活检查](http://127.0.0.1:8080/p3/live)。安装后也可用 `aether-p3` 代替 `python -m aether_agent_memory`。

初始化产生以下文件：

| 文件 | 用途 |
|---|---|
| `service.yaml` | 服务、后台任务和 Provider 配置 |
| `identities.yaml` | 租户、身份权限、凭据 SHA256 与共享授权 |
| `credential` | 本地管理员的 Bearer 凭据；不要提交或公开 |
| `embedding.json` | 仅 native 初始化时生成，含部署目录中的模型缓存位置 |
| `state/` | 服务运行后生成的持久业务数据与缓存 |

`init` 遇到已有配置或凭据会拒绝覆盖。后续启动只需执行 `serve`，不要反复初始化。配置中的 `data_dir`、`identity_file`、`embedding_config` 和 `recall_config` 相对 **配置文件所在目录**解析。

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

若要在 Web 中观察三流程运行，将命令中的 `smoke_service.py` 换为 `monitor_demo.py`。它额外验证 Working 召回、连续读取触发 Operate 自动 hot 放置、放置后的召回和三流程诊断记录，输出可在监控页查询的任务 ID 与 Trace ID。详见 [边测试边监测](web/README.md#一边运行三流程用例一边查看真实轨迹)。

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

旧 `library-basic` 仅保留 API 兼容，不再列在新场景选择器。当前 Native 环境使用 `lexical / literal_baseline`；未配置提炼模型时，S4 在真实任务和 `provider_unavailable` 证据确认后显示**条件不足**，不填入虚构总结。

覆盖面板列出当前源码的 **50 个方法＋路径组合**，分别统计“已调用”和“检查通过”。每次只统计当前运行，不把一次故事或 HTTP 200 当作全接口验收。诊断只抽样本轮可关联元数据；运维写操作留给专用隔离环境，不随演示修改共享服务配置或执行恢复。

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

浏览器打开 `http://127.0.0.1:5173`，点击“开始演示”。演示凭据只从 **P4 服务端文件**读取，不填写到页面或 `VITE_` 变量。默认仅允许本机 5173 的 Origin；本阶段仅验证 Native Vite 代理，容器/Nginx 尚未配置演示代理。

同一时间只运行一轮，完成后手动新开独立范围。HTTP 400 `REQUEST_IN_PROGRESS` 只观察原 job，不重发原 POST；观察超时/结果未知会停住依赖步骤，不能当作失败后立即重跑。“重新查询”只 GET。刷新或切页保留本标签页的开始标识；P4 最多保存 10 轮，重启会丢失内存记录，旧 ID 的 404 **不证明旧写入未执行**。未知结果必须先核对原 job 和专属实例，再决定是否重启和手动清除页面旧标识，不能自动重放。

关闭顺序：Web、P4、P3 各自终端 `Ctrl+C`，最后用 `scripts/p3/temporal_dev.py stop --directory <原专属Temporal目录>` 停止本轮拥有的 Temporal；保留部署数据，不删除数据库、不停止共享实例。测试与当前环境限制应与代码一同评审，不以一次页面通过代替全项目验收。

演示回归在业务目录执行：

```powershell
python -m pytest tests/unit/p4_validation tests/unit/p4_demo tests/integration/test_p4_demo_stories.py tests/integration/test_p4_demo_temporal.py tests/integration/test_p4_demo_handoff.py -q
npm --prefix web test
npm --prefix web run build
npm --prefix web run lint
```

真实 Temporal 集成需要已安装的固定版本 CLI（按测试约定设置 `P3_TEMPORAL_CLI`），开发依赖按 `python -m pip install -e . -r scripts/p3/requirements-ci.txt` 安装。

**50 项接口验收**另有可复现入口，复用五故事及已有隔离运维测试，不向正在演示的服务发送请求。以下沿用前文的 `$P3Home` 和 `$P3Python`，每次使用新的仓库外目录：

```powershell
$P3Evidence = Join-Path $P3Home ('checks/demo-' + [guid]::NewGuid().ToString('N'))
& $P3Python scripts/p3/validate_demo_interfaces.py --directory $P3Evidence
```

默认执行全接口所需的测试；追加 `--full-suite` 可同时运行全仓 `pytest`。不支持 `pytest-xdist` 并行归因。报告 `report.json` 按方法＋实际注册的路径模板记录完整 HTTP 响应状态和对应通过的测试；仅枚举接口、Mock、跳过/失败的测试及单纯收集用例不会计入通过。测试还会对照实际路由注册集合，防止清单漏增或漏删。

新增的两个身份接口由登录/身份测试验证，在未调用它们的故事覆盖面板中保持“未执行”。五场景与只读诊断最多涉及 40 项；另 8 项任务/周期控制、配置、备份、恢复演练及退役维护契约在独立临时数据和 test-owned Temporal 中验证。退役维护接口的 410 只代表正确拒绝，普通权限不足的 403 不代表正常功能通过。提炼成功路径使用确定性测试 provider 检查原任务产物引用；默认 S4 的 `provider_unavailable` 仍是条件不足。

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

在相同虚拟环境中安装可选依赖（Linux/macOS 将 `& $P3Python` 换为 `"$P3_PYTHON"`）：

```powershell
& $P3Python -m pip install -e '.[embedding-onnx,resource-documents]'
# 使用新的部署目录，首次启动需要下载或提供 BGE 模型。
$P3NativeDeploy = Join-Path $P3Home 'deployment-native'
& $P3Python -m aether_agent_memory init --directory $P3NativeDeploy --embedding-profile native --temporal-endpoint $P3TemporalState.endpoint
```

编辑新目录的 `service.yaml` 后，对它执行 `check-config` 和 `serve`。`init` 默认就是 native；首跑步骤显式选择 lexical 是为了先验证本地链路。不要直接把 lexical 数据库改为 native，也不要直接替换已绑定的正文 Provider；模型空间与存储绑定会拒绝隐式迁移，应使用新数据目录并单独设计数据及索引迁移。

| 配置 | 作用及接入要求 |
|---|---|
| `embedding_profile: native`、`embedding_config` | BGE 中文 512 维编码；可在 `embedding.json` 指定已有 `model_path` |
| `language_model` | 配置模型 `endpoint`、`model`，用于抽取、比较、压缩及摘要 |
| `verifier_model` | 可选独立审核模型；省略时由同一模型另行审核 |
| `p2_endpoint`、`p2_bucket` | P2 gRPC 正文/原件 Provider；接入前运行 `python scripts/generate_proto.py`（使用同一虚拟环境） |
| `recall_config` | Recall 预算、可选 CrossEncoder、Milvus 等参数 |
| `redis_url_env` | 可选 Redis URL 的环境变量名 |

模型服务需兼容 `/chat/completions` 的 JSON object 输出和 `/models` 模型查询。初始化时可附加 `--model 实际模型名 --model-endpoint http://模型服务/v1`，密钥由环境变量 `AETHER_LLM_API_KEY` 提供（可用 `api_key_env` 改名）。真实模型的抽取、冲突判断、压缩质量需单独评测。

[生产配置样例](configs/p3.production.example.yaml) 给出完整字段；其中占位模型名和端点必须替换。`profile: production` 强制要求 native、LLM 与 P2 配置，它是配置门槛，不是生产认证。完整配置与身份变更说明见 [运行指南](../交付成果/部署运行/P3_统一服务运行指南_20260926.md)。

## 持续运行与排障

`serve` 启动 HTTP、Temporal SDK Workers、事务意图转交、身份重载与依赖探测。业务事件、周期和维护由 Temporal Workflow 编排。使用单进程宿主 `workers=1`，同一个部署目录只启动一个宿主；目录锁拒绝第二个所有者。需要进程退出后自动重启时，使用进程管理器或下方 Compose。

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
- **保存后长期 Recall 为空**：检查 processing，确认批次已结束、任务成功且索引发布完成；lexical 查询应包含相近词项。
- **native 启动失败**：检查扩展依赖、模型下载/目录、空间绑定；不要用更换已有数据库模式的方式绕过校验。
- **live 正常但 ready 不可用**：查看授权 `/p3/health` 中失败的依赖，配置校验通过不能替代实际连接检查。

部署目录应持续保存。备份需覆盖 RF、正文 Provider、配置、索引及模型绑定；仅备份 RF 数据库不足以完成灾备。业务历史和动作证据仍需容量规划、归档与备份周期；日志保留上限不代表所有业务数据已自动回收。

## Docker 部署

统一宿主使用 [compose.p3.yaml](compose.p3.yaml) 和 [Dockerfile.p3](Dockerfile.p3)。以下 PowerShell 示例使用单独的容器部署目录；需先具备可用的 Docker Engine 与 Compose：

```powershell
$env:AETHER_DEPLOYMENT_DIR = Join-Path (Resolve-Path ../..).Path '.agent-work/aether/workspace-support/p3-container'
docker compose -f compose.p3.yaml run --build --rm --no-deps p3 python -m aether_agent_memory init --directory /deployment --embedding-profile lexical --host 0.0.0.0 --temporal-endpoint temporal-dev:7233
docker compose -f compose.p3.yaml --profile temporal-local up -d --build
docker compose -f compose.p3.yaml logs -f p3
# 停止服务；部署目录保留，后续用 up 再次启动。
docker compose -f compose.p3.yaml --profile temporal-local down
```

Linux/macOS 使用 `export AETHER_DEPLOYMENT_DIR="$HOME/.local/share/aether/p3-container"`，随后执行相同 Docker 命令。初始化只执行一次；容器内配置用 `/deployment` 路径，监听地址必须为 `0.0.0.0`。端口默认仅发布到宿主机 `127.0.0.1:8080`，可用 `AETHER_HOST_P3_PORT` 改宿主端口。

接内置 P2 时，在新部署配置中设置 `p2_endpoint: engine:50052`，再用 `docker compose -f compose.p3.yaml --profile temporal-local --profile p2 up -d --build`。容器访问宿主模型可使用 `host.docker.internal`。`restart: unless-stopped` 负责进程重启，P3 healthcheck 检查 `/p3/readyz`。`temporal-local` 用持久 volume 保存开发历史；连接已有 Server 时省略该 profile 并填写相应 endpoint。

**本轮构建在获取 `python:3.13-slim` 鉴权令牌时网络连接失败；新镜像、容器启动和持久恢复尚未通过验收。**

## 当前状态

| 能力 | 当前实现与验证范围 |
|---|---|
| Remember → Recall 统一链路 | 保存、后台长期化、多块完整索引发布、资格检查、精确正文、最终复核、结果重取已接通并本地验证 |
| Temporal 与业务底座 | Temporal 编排任务、事件和周期；保留 SQLite 事务、Outbox/Inbox、权限、幂等和完成证据。旧 RF 调度入口已退役 |
| 原生 Embedding | 真 BGE 保存与召回已测；业务检索质量和生产吞吐仍需独立验收 |
| 租户与共享 | 本地已测隔离、共享发现、撤权、停用及重新启用 epoch 栅栏；P4 真实身份平台待集成 |
| 监测 Web | 已接统一 `/p3` 接口，提供健康、任务、异常和 Trace 瀑布图；尚无跨实例聚合、持久历史指标和云端 APM 接入 |
| 内部 Operate | 持久热度、定时衰减、缓存准备、读回确认、回收再唤醒已测；当前执行本地文件缓存，生产物理存储迁移待验收 |
| 文档与模型加工 | 原件上传和解析入口、JSON 模型适配已接通；PDF/DOCX 需可选依赖，真实 LLM 质量及 5 倍压缩指标未验收 |
| P2 / Milvus | 官方 Milvus Lite 已通过真实本地验证；P2 与生产 Milvus 集群联调待验证 |
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

本次 README 校核（2026-09-28）：在 Windows 已有 Python 3.13 环境中，使用新部署目录与 lexical 模式，通过初始化、配置校验、真实 TCP 就绪检查、长期召回冒烟及本文 PowerShell 保存/召回示例；临时服务已停止。未重新验证全新环境安装、Linux/macOS 实跑或 Docker 部署。

## 开发与文档导航

| 目录 / 文件 | 用途 |
|---|---|
| `src/aether_agent_memory/runtime/flows/` | 统一 CLI、配置、装配、HTTP 与持续执行器 |
| `src/aether_agent_memory/runtime/foundation/` | RF 事务、任务、事件、身份与诊断 |
| `src/aether_agent_memory/runtime/temporal/` | Workflow、Activity、接纳绑定、幂等重放与迁移 |
| `src/aether_agent_memory/remember/` | 保存、长期化、文档与模型加工 |
| `src/aether_agent_memory/recall/` | 原生编码、候选、正文、复核与上下文组装 |
| `src/aether_agent_memory/operate/` | 热度算法、持续调度和文件缓存执行 |
| `contracts/p3/`、`docs/p3/` | 需求基线、接口契约、架构与开发约束 |
| `tests/`、`scripts/p3/` | 回归、协作门禁与公开 HTTP 冒烟 |
| `configs/p3.production.example.yaml` | 统一服务配置样例 |
| `engine/` | P2 引擎与协议 |

在虚拟环境中安装工程测试依赖后运行静态检查和统一服务回归（Linux/macOS 同样替换解释器变量）：

```powershell
& $P3Python -m pip install -r scripts/p3/requirements-ci.txt
& $P3Python -m ruff check src tests scripts benchmarks
& $P3Python -m mypy src
$env:P3_TEMPORAL_CLI = Join-Path $P3Home 'tools/temporal.exe'
& $P3Python -m pytest -q tests/integration/test_continuous_service.py
```

完整回归和真模型测试需要相应扩展依赖与环境，执行范围及证据要求参见 [协作门禁脚本](scripts/p3/validate_collaboration.py) 和 [验收报告](../交付成果/测试与验收/P3_统一运行整合与验收_20260926.md)。

- [完整部署运行指南](../交付成果/部署运行/P3_统一服务运行指南_20260926.md)
- [PRD V1.3 基线](contracts/p3/prd-baseline.yaml) / [总体架构与流程](../交付成果/架构设计/总体架构与流程.md)
- [项目与交付成果入口](../README.md)
- [真实监测 Web](web/README.md)
- 旧兼容栈参考：[Runtime 架构](docs/p3_runtime_architecture.md)、[旧 Northbound API v1](docs/P3_NORTHBOUND_API_V1.md)、[旧 ADR](docs/adr/README.md)、[历史服务器回归](docs/SERVER_REGRESSION_20260825.md)。这些文档保留原日期及背景，不作为统一宿主的默认启动说明。

旧 `python -m aether_agent_memory.app`、旧 `compose.yaml`、B1/B2/B3 演示和 `/api/v1/...` 继续保留兼容用途。新用户从本 README 的统一入口开始；可运行 Mock 是独立产品演示，不代表已经连接此服务。
