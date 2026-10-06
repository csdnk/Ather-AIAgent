# 查看 AKS 服务、API 与 Temporal

当前服务运行在 Azure 全球版 Southeast Asia：订阅 `NUIST-RnD` → 资源组 `aetherstore-p3` → AKS 集群 `aks` → 命名空间 `aether-p3-demo`。2026-10-01 已核对 P3、Web、Temporal、Temporal UI 四项 Deployment 均为 1/1 Ready。

浏览器中的 127.0.0.1 是本机端口转发入口，实际 API、Worker、Workflow 和数据仍运行在 Azure。关闭本机 PowerShell 转发窗口不会停止云端服务。

**公网监测网页现已开放：[HTTPS 入口](https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com)。** 直接浏览器访问即可，无需转发；令牌连接方式见[公网访问说明](P3_公网访问说明_20261001.md)。下面保留 API 文档、Temporal 与本机诊断入口。

## 三个查看入口

| 入口 | 地址 | 适合查看 |
|---|---|---|
| P3 监测网页 | http://127.0.0.1:3000 | 依赖健康、后台任务、业务结果、调用链路和节点日志 |
| P3 API 文档 | http://127.0.0.1:18080/docs | 请求字段、响应模型和接口列表 |
| Temporal UI | http://127.0.0.1:8233 | Workflow、Activity、运行状态和执行历史 |

目前三个转发入口均已验证可用。日后转发未启动时，在独立的 PowerShell 窗口执行对应脚本，保留正在转发的窗口，看到 Forwarding 后刷新浏览器：

```powershell
& 'E:/projects/codex/aether/交付成果/部署运行/打开P3监测网页.ps1'
```

```powershell
& 'E:/projects/codex/aether/交付成果/部署运行/打开P3接口文档.ps1'
```

```powershell
& 'E:/projects/codex/aether/交付成果/部署运行/打开Temporal运行页面.ps1'
```

P3 监测脚本复制本机测试令牌到剪贴板，在网页凭据框粘贴。API 文档与 Temporal 页面无需 P3 令牌。当前 Swagger 未配置标准 Bearer Authorize，实际鉴权 API 调用使用监测网页、PowerShell 或 HTTP 客户端。

## 在 Azure Portal 看运行位置

1. 打开资源组 `aetherstore-p3`，进入 Kubernetes 服务 `aks`。
2. 在 Kubernetes 资源的工作负载（Workloads）中，选择命名空间 `aether-p3-demo`。
3. 查看部署（Deployments）或 Pod：`p3`、`web`、`temporal`、`temporal-ui`。Pod 信息包含运行节点、状态、重启次数和事件。
4. 在服务和入口（Services and ingresses）中查看原四个 ClusterIP 服务，以及新增的 `public-web` LoadBalancer，外部 IP 为 `4.194.21.201`。公网入口经独立 Caddy 代理访问现有 Web；原服务类型不变。

也可以通过本机 kubectl 直接查看，命令明确指定本次集群配置：

```powershell
kubectl --kubeconfig 'E:/projects/codex/.agent-work/aether/workspace-support/aks-demo-20261001/kubeconfig' -n aether-p3-demo get pods -o wide
```

持续观察 Pod 状态，在上面命令末尾加 `--watch`；按 Ctrl+C 停止观察。

读取 P3 实时容器日志：

```powershell
kubectl --kubeconfig 'E:/projects/codex/.agent-work/aether/workspace-support/aks-demo-20261001/kubeconfig' -n aether-p3-demo logs deployment/p3 --tail=100 --follow
```

## 用 API 观察业务

以下接口可经 Web 转发使用 `http://127.0.0.1:3000`，也可经 API 转发使用 `http://127.0.0.1:18080`。除 live/readyz 外，需 `Authorization: Bearer <P3 令牌>`，诊断接口需对应诊断权限。

| GET 接口 | 用途 |
|---|---|
| /p3/readyz | 是否能够接纳执行请求；当前为 ready |
| /p3/health | 依赖与能力的健康状态 |
| /p3/runtime | Worker、队列和需要关注的任务 |
| /p3/tasks?limit=50 | 业务任务列表；加 state=failed 筛选失败 |
| /p3/operations/{job_id} | 某任务的业务状态、effect_status、Temporal 绑定和诊断 |
| /p3/traces?limit=50 | 获取调用链路及 trace ID |
| /p3/logs/{trace_id}?limit=100 | 某条调用链路的节点日志 |

PowerShell 示例从本机文件读取令牌，不输出秘密值：

```powershell
$P3Headers = @{ Authorization = 'Bearer ' + [IO.File]::ReadAllText('E:/projects/codex/.agent-work/aether/workspace-support/aks-demo-20261001/credential').Trim() }
Invoke-RestMethod 'http://127.0.0.1:3000/p3/readyz'
Invoke-RestMethod 'http://127.0.0.1:3000/p3/runtime' -Headers $P3Headers
Invoke-RestMethod 'http://127.0.0.1:3000/p3/tasks?limit=10' -Headers $P3Headers | ConvertTo-Json -Depth 12
```

不要向聊天发送令牌或包含 Authorization 的请求信息。Swagger 地址在 API 根 `/docs`；Web 的 `3000/docs` 会返回前端页面，不能当作 API 文档。

## 在 Temporal 页面看什么

打开 Temporal UI 后选择 namespace `default`，进入 Workflows。已验证页面能读取现有执行列表。

- `p3/aks-p3-demo/periodic` 是持续运行的周期 Workflow，负责周期检查与维护；Continue-As-New 会更换 run ID，执行链继续保留。
- 业务 Workflow ID 形如 `p3/aks-p3-demo/recall.execute/{job_id}`、`p3/aks-p3-demo/remember.extract/{job_id}`。按 P3 任务 ID 查找，打开执行详情，查看 History、Activity 与失败或重试事件。
- 可用已验证的 Recall 任务 `d790574f3ad33d0dcf39618b02d654239cd3cf8e6bbd13cf7abc172e3e39bede` 搜索对应执行。

Temporal Completed 表示技术执行结束，P3 业务成功应同时查看 `/p3/operations/{job_id}` 顶层 `state=succeeded` 及 `effect_status`。之前因重试耗尽的 Recall 可能表现为 Temporal Completed、P3 failed；两者描述不同层面的结果。

UI 为独立 Deployment，2.54.1 版，通过 `temporal:7233` 连接现有 Temporal。已启用只读配置，禁用终止、取消等写操作；没有新增数据卷，也没有重启 Temporal/P3。仅本机转发访问，没有公网入口或独立登录。开发 Temporal 的完成历史保留 24 小时，因此较早记录会按保留期清理。

相关说明：[实际部署进度与验证](P3_AKS部署进度_20261001.md)、[部署指南](P3_AKS联调演示部署指南_20261001.md)。
