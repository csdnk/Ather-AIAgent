# Aether P3 运行监测 Web

这是统一 P3 服务的真实监测前端，使用 React、TypeScript、Vite 和 Ant Design。默认页面已经切换到新的监测工作空间；不读取旧 `/api/v1/...` 数据，也不回退到 Mock。

界面借鉴 Grafana、Datadog 等监测平台的常见布局：深色侧边导航、紧凑指标卡、可扫描的明细表、右侧详情抽屉和 Trace 瀑布图。所有业务数据来自当前登录身份有权查询的统一服务。

## 能看什么

| 页面 | 实际数据与操作 |
|---|---|
| 运行总览 | 就绪状态、待处理任务、未确认事件、异常数、Worker 心跳、运行配置、待关注任务及未知动作 |
| 服务与依赖 | 能力与依赖探测状态、探针耗时、健康证据有效期 |
| 后台任务 | 按状态筛选、分页、按 ID 查询、持久任务与进度/恢复证据、关联 Trace |
| 链路追踪 | 业务/内部运行/流程筛选、分页、按 ID 查询、父子节点瀑布图、节点阶段与关联 ID |
| 异常事件 | 处置状态、业务复验状态、证据引用与原操作 ID |

趋势图只保存**当前页面连接后的最近 60 次采样**，并按实际采样时间绘制；失败采样留空，不补零。暂停采样、断连、无权限、证据过期和日志不完整都有明确提示。页面刷新或切换身份会清除本页数据。

各数据区域在对应接口返回后立即显示，不再等待本轮所有接口完成。任务详情在任务执行期间每 2 秒更新，显示压缩、提取、向量编码、摘要等阶段已提交的分片数和真实依赖等待；这些数字不是估算百分比。重启后复用已有分片时也会补登记进度。

## 本地启动

当前 Windows 演示环境从启动 P3、启动 Web、复制凭据到运行中文对话测试的整套命令，见 [完整启动命令](../../交付成果/部署运行/P3_监测与中文对话演示_完整启动命令_20260928.md)。

需要 Node.js 22 和 npm，以及按 [业务 README](../README.md#快速启动) 启动的统一 P3 服务（默认 `http://127.0.0.1:8080`）。

在业务目录下进入 Web：

```powershell
cd web
npm ci
npm run dev
```

打开 [http://127.0.0.1:5173](http://127.0.0.1:5173)，点击“配置连接”，输入部署目录 `credential` 文件内容。身份需要 `maintenance:diagnose` 权限；初始化生成的本地管理员已具备该权限。

如果后端使用其他地址，在启动 Vite 前设置：

```powershell
$env:VITE_AETHER_PROXY_TARGET='http://127.0.0.1:8081'
npm run dev
```

Linux/macOS 等价命令为 `VITE_AETHER_PROXY_TARGET=http://127.0.0.1:8081 npm run dev`。

Web 始终通过**同源 `/p3`** 访问后端。开发阶段由 Vite 转发，部署阶段由 Nginx 转发，因此无需给后端增加宽泛 CORS。Bearer 凭据仅保留在页面内存，不写入 localStorage、sessionStorage、URL 或构建产物；刷新或断开连接后需重新输入。

依赖实际安装在当前项目外时，可将 `web/node_modules` 链接到外部依赖目录。此工作区的依赖、构建检查结果和缓存放在 `E:/projects/codex/.agent-work/aether/workspace-support/`。`AETHER_WEB_CACHE_DIR` 与 `AETHER_WEB_BUILD_DIR` 可分别指定 Vite 缓存和构建输出路径。

## 容器部署

业务目录的 [compose.p3.yaml](../compose.p3.yaml) 现在包含 `web` 服务。先按 [统一服务运行指南](../../交付成果/部署运行/P3_统一服务运行指南_20260926.md) 初始化容器部署目录，再执行：

```powershell
# AETHER_DEPLOYMENT_DIR 必须指向已经初始化的外部部署目录。
docker compose -f compose.p3.yaml up -d --build
```

- Web：[http://127.0.0.1:3000](http://127.0.0.1:3000)，可用 `AETHER_HOST_WEB_PORT` 改宿主端口。
- API：默认 `http://127.0.0.1:8080`。
- Nginx 将 `/p3/` 转发给 `p3:8080`，页面刷新由 SPA 回退处理。
- 单独部署时先执行 `npm run build`，用同源 Web 服务器托管 `dist/` 并配置 `/p3` 代理；不能仅双击 index.html。
- 镜像构建使用 [Dockerfile](Dockerfile) 和 [nginx.conf](nginx.conf)。容器配置与本地浏览器验证是不同证据，当前容器实跑情况见验收记录。

## 接口与权限

| 接口 | 用途 |
|---|---|
| `GET /p3/health`、`/p3/runtime`、`/p3/capabilities` | 能力、健康与后台执行状态 |
| `GET /p3/tasks?limit=30&state=running&cursor=...` | 权限过滤后分页的任务元数据 |
| `GET /p3/tasks/{id}`、`/p3/tasks/{id}/progress` | 单任务及其进度证据 |
| `GET /p3/traces?limit=30&flow=business&before=...` | 当前身份及作用域内的保留 Trace |
| `GET /p3/logs/{trace_id}?limit=200&after=...` | 分页读取规范化节点日志 |
| `GET /p3/incidents` | 授权可见异常事件 |

任务列表使用绑定身份和筛选条件的签名游标。Trace 目录按首次保留记录倒序；`flow` 可取 `business`、`remember`、`recall`、`operate`、`runtime`，省略表示全部。默认选择业务链路，避免健康探测与维护采样淹没业务请求。

列表不返回记忆正文、模型输入或凭据。资源共享不意味着获得对方全部 Trace；Trace 目录和日志仍按原发起身份及其 home scope 限制。节点记录只能用来诊断，业务完成与恢复仍由持久任务、版本及副作用证据决定。

## 验证

### 一边运行三流程用例，一边查看真实轨迹

要用自然中文对话验证偏好记忆、出差安排、跨会话召回和明确纠错，请运行 `scripts/p3/dialogue_demo.py`。参数与下面的三流程脚本相同，输出逐轮用户话语、P3 原始召回和中文步骤对应的 Trace。当前工作区可直接复制的命令与凭据获取方式见 [中文多轮对话验证说明](../../交付成果/测试与验收/P3_中文多轮对话验证说明_20260928.md)。这是记忆服务测试，不生成模拟聊天回复。

保持统一服务和 Web 运行，用与 Web 相同的部署凭据，在业务目录执行：

```powershell
& $P3Python scripts/p3/monitor_demo.py --url http://127.0.0.1:8080 --credential-file (Join-Path $P3Deploy 'credential') --step-delay 1
```

`$P3Python` 和 `$P3Deploy` 沿用业务 README 的启动变量；若连接其他实例，修改 URL 和凭据文件，Web 的代理也必须指向同一个实例。该脚本不会启动另一个服务或建立单独测试数据库。每次创建唯一 `monitor_...` 会话并保留测试记忆。

用例验证：保存成功 → Working 召回 → 后台批次长期化/索引 → 长期召回及结果重取复核 → 16 次真实读取触发 Operate 热度计算与 hot 放置成功 → 放置后仍能召回 → 任务进度和 Remember/Recall/Operate 三类日志均可读取。默认各阶段/读取间隔 1 秒，异步阶段最多各等待 120 秒；输出 7 项 PASS、`"passed": true`，进程退出码为 0 才表示通过。失败时返回非零退出码。当前验证基线是本地 lexical 与默认 continuous_heat_v1 策略；改变策略后可能不再触发相同升温结果。

查看方式：

1. Web 自动刷新选择“每 5 秒”，在“后台任务”粘贴命令输出的完整 `task_id`，查看状态、进度和关联 Trace。
2. 在“链路追踪”粘贴输出 `traces` 列表中的完整 `trace_id`，点击“查看”，展开瀑布图和节点明细。日志较多时继续加载后续记录。
3. 一次用例包含多个 HTTP 请求和后台任务，因此会产生多条 Trace；脚本列出本次可见业务 Trace，而不是只提供一条保存请求链路。任务继承的 Trace 可能同时含 Remember 与 Operate 节点，默认“业务链路”能一起查看。

快速任务可能在两次采样间完成，总览待处理数仍可能为 0；请以持久任务和 Trace 明细核实执行。正常用例不故意制造异常，异常列表为空是预期行为。日志受保留策略影响；这里只验证当前三流程正常路径，不覆盖纠错/删除、全部故障恢复、长时间冷却或生产验收。原有 `smoke_service.py` 是更短的首次保存/长期召回检查；`pytest` 和 `demo_flows.py` 的独立数据库不会自动显示在此 Web。

```powershell
npm run lint
npm test
npm run build
```

后端目录及权限回归在业务目录执行：

```powershell
python -m pytest -q tests/integration/test_monitor_catalogs.py tests/integration/test_continuous_service.py
```

新增前端测试覆盖认证请求、错误不回退、采样断连/恢复、慢接口不阻塞其他区域、Trace 父子关系、日志缺失与未闭合节点。2026-09-29 已删除经应用入口和全部测试引用图确认不可达的 13 个旧页面、接口封装和样式文件；仍被测试或运行代码使用的组件继续保留。

本轮结果及环境边界见 [真实监测 Web 改造与验收](../../交付成果/测试与验收/P3_真实监测Web改造与验收_20260928.md)。

## 当前边界

这是本地统一服务的监测界面，不是完整云端 APM 平台。尚未提供跨实例聚合、持久历史指标查询、告警通知、OTLP/Grafana/Jaeger 接入、租户管理或自动恢复控制台。未采集 CPU/内存、业务 QPS 或 P95 时，页面不会生成这些指标。任务目录仍基于当前本地 RF 存储扫描，不能据此声称大规模集群监控性能已验收。
