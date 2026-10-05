# Azure Agent 平台部署

本目录保存 2026-10-05 Azure 发布对应的配置与后续更新入口。运行源代码位于本仓库 `AgentJYS-main/`。更新前核对当前提交与部署版本，不要从旧 checkout 重新发布。

## 当前结构

- `aether-platform`：Agent BFF + Web，`/agent/`，单实例。
- `aether-budibase`：业务管理应用与 `/builder`，保留 Budibase 原生 OIDC 和设计器账号。
- `aether-keycloak`：生产 `start` 模式，`/identity`，PostgreSQL 持久化。
- `aether-platform-db`：平台及 Keycloak 数据库，独立 PVC。
- `aether-agent-p3`：现有云端 P3；`identity-projection` sidecar 从云端目录同步身份与 JWKS。
- `public-web`：现有 HTTPS Caddy 网关；`Caddyfile` 是当前路由。
- Temporal 沿用现有测试部署；`/temporal/` 通过 Agent 的后台身份校验，仅允许平台管理员。

`azure-resources.yaml` 不含 Secret，包含当前平台资源、探针、挂载、Service 和 PVC 声明。它依赖既有 AKS、网关、P3 数据卷、Temporal 和 Azure 业务依赖；不是空集群的一键安装器。已有 PVC 与数据库绝不能在发布时重建或覆盖。`p3-projection-patch.json` 使用 strategic merge，按容器名更新同步 sidecar，不重复追加。

## 私有配置

将 `cloud.example.json` 与 `identity.example.json` 复制到项目外私有工作目录再填写。LLM key、客户端 secret、DSN 和账号密码不提交 Git。

现有 Secret：`aether-platform-config`（`private.json`）、`aether-identity-config`（`private.json`、首次导入的 `realm.json`）、`aether-platform-database`、`aether-budibase-config`。本目录仅提供前两者的应用配置示例，数据库及 Budibase 的私有环境字段由 YAML 中的 `secretKeyRef` 明确列出。首次迁移需备份并恢复真实数据库及 Budibase `/data`；不得以空数据启动冒充迁移。

本次 issuer 切换已在平台库 `cloud_migrations` 记账：`azure-issuer-20261005`。8 个用户的版本只递增一次，避免 P3 接受旧 issuer 的同版本投影。后续发布不得再次执行或回滚这次版本迁移。

## 后续更新应用代码

在业务代码目录安装固定前端依赖并构建，缓存和输出放项目外：

```powershell
$env:AETHER_WEB_DEPENDENCIES = 'E:/projects/codex/.agent-work/aether/workspace-support/azure-platform-release-20261005/web-deps'
$env:AETHER_WEB_DIST = 'E:/projects/codex/.agent-work/aether/workspace-support/azure-platform-release-20261005/web'
$env:AETHER_PUBLIC_PATH = '/agent'
node scripts/platform/build_web.mjs
python deploy/platform/update_release.py --kubeconfig '<私有 kubeconfig 绝对路径>' --web $env:AETHER_WEB_DIST --work-dir 'E:/projects/codex/.agent-work/aether/workspace-support/azure-platform-next-release'
```

`update_release.py` 只支持已有发布 PVC 与单副本 Agent。它先在 PVC 的全新目录暂存两个源包、Web 和依赖，验证上传包 SHA 并检查导入；旧代码及旧依赖不会混入新版本。切换前检查 PVC 的全部 Pod 消费者：只有当前 Agent Deployment 的 Pod 才允许继续，其他消费者（包括使用同一 PVC 的 P3 sidecar）会使脚本停止，需要先单独制定这些消费者的协调发布方案，不能绕过检查。脚本不会修改其他 Deployment。

暂存成功后，脚本缩容 Agent 至零并等待 Pod 删除，再切换应用、依赖和校验清单，恢复单副本。旧文件保留在 PVC 的 `releases/<本次编号>/previous` 中；切换或新版本启动失败时尝试恢复旧文件和副本数。恢复未完成会保留更新 Pod 并明确报错，需人工检查，不能当作发布成功。该流程存在明确停机窗口，会使内存登录会话失效；发布前应确认没有正在生成的对话，且没有其他发布工具同时修改该 Deployment。

脚本不迁移数据库、不重导身份、不替换网关；发布包、SHA 与恢复目录记录留在指定外部目录。已验证本地切换、部分切换失败恢复和 PVC 消费者拒绝测试，尚未在云端执行新版发布脚本；本次实际迁移用的分阶段脚本和私有备份保存在项目外工作区。

`Dockerfile` 提供标准镜像构建路径。当前 Azure 身份没有 ACR 推送权限，因此此次运行的是基础镜像 + 校验后的 PVC 应用文件；没有声称已发布自建不可变镜像。完整镜像流水线仍需具备镜像仓库推送权限。

## 网关及验证

更新 `Caddyfile` 前先通过现有容器的 `caddy validate`，再更新 `public-web-caddy` ConfigMap 并重启 `public-web`。相对地址跳转显式写 `redir * /agent/ 302`，避免 Caddy 将目标路径识别为请求匹配器。Budibase 要求同源 Referer，网关使用 `Referrer-Policy same-origin`。OIDC callback 只把返回首页的 Location 重写到管理应用；Builder 邮箱登录保持原流程。

检查 `/` 跳到 `/agent/`，`/agent` 和 `/temporal` 补齐尾部斜杠；`/p3/*`、`/identity/admin/*` 返回 404；`/docs` 和 `/openapi.json` 仅平台管理员登录后可读。未登录访问 Temporal 应跳到管理后台，租户管理员和普通用户应返回 403。验证管理员的后台页面能实际显示数据，不能只检查 HTML 返回 200。

平台测试：`python -B -m pytest tests/platform -q -p no:cacheprovider`。数据库/身份测试需要原本的隔离测试配置，不能指向云端业务数据库。前端测试：设置包含 esbuild、React、jsdom 的 `AETHER_WEB_DEPENDENCIES`，执行 `node --test platform-web/tests/*.test.mjs`。

## 数据与运行边界

云端迁移后独立保存数据，不与本机双向同步。回退应用时复用当前数据库，禁止把迁移前 dump 直接覆盖云端新增数据。旧 `web` 可保持 0 副本，旧 P3 和既有数据卷作为保留资源；删除须另做依赖与备份审查。Temporal 仍是原测试持久化、平台登录会话仍在进程内，本次未完成 HA、长稳或灾备验收。

## P3 接口文档（2026-10-05 后续发布）

`docs/index.html` 是只读 Swagger UI，动态读取当前 P3 的 `/openapi.json`，禁用在线执行。两条公网路由复用现有平台管理员运维访问校验。文档通过 `aether-p3-documentation` ConfigMap 挂载至网关 `/srv/p3-docs`，无需本机文件服务。Swagger UI 使用固定版本 5.17.14 的 jsDelivr 资源；资源加载失败时页面提供 OpenAPI JSON 入口。

重建文档挂载时，在本业务代码目录执行（替换 kubeconfig 路径）：

```powershell
kubectl --kubeconfig '<私有配置路径>' -n aether-p3-demo create configmap aether-p3-documentation --from-file=index.html=deploy/platform/docs/index.html --dry-run=client -o yaml | kubectl --kubeconfig '<私有配置路径>' -n aether-p3-demo apply -f -
kubectl --kubeconfig '<私有配置路径>' -n aether-p3-demo patch deployment public-web --type=strategic --patch-file deploy/platform/docs/gateway-mount.json
```

网关 Caddyfile 另按前述方式验证和部署。仅公开文档不等于放开 `/p3/*` API 调用。更新文档源文件不会自动更新 ConfigMap，需重新执行创建／apply 步骤。
