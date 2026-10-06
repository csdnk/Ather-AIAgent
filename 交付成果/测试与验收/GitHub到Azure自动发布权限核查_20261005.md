# GitHub 到 Azure 自动发布权限核查

核查时间：2026-10-05 19:43—19:49（北京时间）。

目标：判断发布 GitHub Release 后，自动构建镜像、推送 ACR、更新现有 AKS 服务所需的权限与配置是否齐备。

## 结论

**目前尚不足以完成 GitHub Release 自动部署闭环。** GitHub 登录账号具有仓库管理员权限，本机已有 Kubernetes 凭据也能更新应用；但当前 Azure 用户没有 ACR 镜像推送权限，远端默认分支没有 Release 部署工作流，GitHub Actions 的独立 Azure 部署身份尚未核实。不能将本机 Kubernetes 管理凭据的能力视作 GitHub Actions 已获授权。

本轮只读查询云资源、仓库元数据及授权评估接口；未推送镜像、发布 Release、运行工作流、修改权限或更新服务。新增本报告，原始记录保存在项目外独立工作区。权限检查不等于部署或业务验收。

## 实测结果

| 项目 | 当前证据 | 判定 |
|---|---|---|
| GitHub 仓库身份 | `csdnk/Ather-AIAgent`；当前登录 `csdnk`；API 返回 `admin=true`、`push=true` | 仓库账号层面具备管理权限 |
| 本机 GitHub 令牌 | 读取 Actions 设置、仓库 Secrets 元数据、Variables 元数据和自托管 Runner 列表均返回 HTTP 403，`Resource not accessible by personal access token` | 当前令牌对这些接口权限不足；不能推断账号本身不是管理员，也不能证明 Secrets 不存在 |
| GitHub 自动化现状 | 默认分支为 `codex/project-workspace`；三个远端工作流为 `ci.yml`、`p3-contracts.yml`、`p3-azure-tests.yml`；前两者处理 push/PR，后者为手动 Azure 测试 | 默认分支未配置 Release 自动构建、上传镜像及部署工作流 |
| Release 与环境 | Release 列表为 0；Environments 列表为 0。远端测试工作流引用 `azure-tests` 环境及三项 Secret | 尚无已发布 Release；引用的环境未配置；Secret 是否存在未能核实 |
| Azure 用户 | `ea-nuist-cgao@jumborca.net`；订阅 `NUIST-RnD`；目标资源组 `aetherstore-p3` | 当前身份已核实 |
| ACR 上传权限 | 仓库 `aetherp3acr` 使用 `LegacyRegistryPermissions`；当前用户继承 Reader、Foundry User、AcrPull、Storage Blob Data Reader、Log Analytics Reader；有效权限缺少 `Microsoft.ContainerRegistry/registries/push/write` | 当前 Azure 用户不能通过其现有 Azure RBAC 权限推送新镜像；有下载权限 |
| AKS 更新权限 | 现有 kubeconfig 在服务端识别为 `masterclient`，属于 `system:masters`；目标 namespace 的 Deployment patch/update/create、Service create、ConfigMap patch、Pod/日志读取均返回 yes | 这份本机 Kubernetes 凭据足以修改目标应用，权限范围较宽；并非当前 Azure 用户身份的等价证明 |
| GitHub 的部署身份 | 远端三个工作流均未包含 Azure OIDC 登录和部署流程；资源组内用户分配托管身份列表为空；Secrets 元数据不可读 | 未确认已经存在并接通 GitHub 的发布身份；不能据此断言整个租户没有服务主体或其他身份 |
| AKS 拉取镜像 | 现有 Deployment 引用 `acr-pull`，主要服务 Pod 当前为 Running；查询 kubelet 身份在 ACR 及上级范围未见角色分配 | 已有运行实例使用 ACR 镜像，但本轮未读取拉取 Secret、未启动新实例，不能证明新镜像的拉取或凭据续期已经通过 |
| 网络 | 本机通过既有管理代理可以访问 AKS；ACR 公共网络访问为 Enabled | 本机管理路径可用；GitHub Runner 到 ACR/AKS 的真实网络路径未执行验证 |

## 实施自动发布前需要补齐的事项

1. **指定 GitHub 发布身份。** 优先为该仓库建立 OIDC 联合身份，限定信任的仓库及发布环境或标签规则。OIDC 指 GitHub 工作流在运行时换取短期 Azure 凭据。本机已登录 Azure 不代表 GitHub Runner 可以登录。
2. **给发布身份镜像上传权限。** 当前 ACR 为传统仓库权限模式，可在 `aetherp3acr` 资源范围授予 `AcrPush`。管理员可直接配置，无需给普通开发人员整个订阅 Owner。本机若也需要手动发布镜像，再单独决定是否给个人账号同类权限。
3. **给发布身份目标 AKS 应用的部署权限。** 如果采用 Azure 登录并获取 AKS 用户凭据，需要相应的 Cluster User 管理面权限；还需要目标 namespace 内更新 Deployment、读取发布状态等 Kubernetes 数据面权限。两层权限分别核对。不要把现有 `system:masters` 凭据直接作为流水线默认凭据。
4. **配置 GitHub Release 工作流及发布环境。** 检出 Release 对应的确定提交，执行测试和镜像构建，推送 ACR，按镜像摘要更新应用并验证。若工作流声明某个 GitHub Environment，需同步创建并配置该环境。
5. **解决配置渠道的访问权限。** 仓库管理员可在网页配置必要环境和凭据；若继续使用当前 CLI/API 配置，需要给令牌相应仓库范围的最小权限。403 涉及的管理 API 不影响已经实测通过的普通 CI，且不能证明所有写接口都被拒绝。
6. **核对拉取身份与 Runner 网络。** 优先使用持久的受控拉取身份；若继续使用 `acr-pull`，需由维护者核对凭据有效性及续期方案。后续在明确的测试发布中验证新镜像拉取、滚动更新或实际更新策略、失败处置和业务接口。

本次目标是复用现有 ACR/AKS 完成应用发布，不需要以新建 VM 或取得全订阅管理权限作为前提。数据库迁移、生产可用性、完整平台云端迁移属于额外实施与验收范围。

## 可转发给管理员的需求

> 我们使用 GitHub 仓库 csdnk/Ather-AIAgent，希望在发布正式 Release 后，由 GitHub Actions 自动构建镜像并部署到现有 Azure AKS。当前个人 Azure 账号只有 ACR 拉取权限，缺少镜像推送权限；本机虽有可更新 Kubernetes 应用的凭据，但尚未接通专用 GitHub 发布身份。请协助配置受仓库及发布环境约束的 OIDC 联合身份，在 aetherp3acr 范围授予 AcrPush，并提供目标 AKS namespace 的应用部署权限及必要的用户凭据获取权限，同时确认 AKS 镜像拉取身份和 Runner 网络通路。GitHub 工作流、发布环境及验证流程由我们补齐，无需开放整个订阅的 Owner 权限。

## 证据索引与限制

- E01：GitHub 仓库身份、默认分支与 permissions API 读回。
- E02：默认分支三个工作流源码及已登记工作流列表；近期普通 CI 成功记录。
- E03：GitHub Release、Environments 列表，以及 Actions 设置、Secrets、Variables、Runner 元数据接口的 403。
- E04：Azure 当前身份、ACR 配置、包含继承和组成员关系的角色分配、ACR 有效 permissions、AcrPush 角色定义。
- E05：Kubernetes `auth whoami`、逐项 `auth can-i`、Deployment 镜像及 imagePullSecrets 引用、Pod 状态。
- E06：AKS 配置、kubelet 身份的 ACR 角色查询、资源组托管身份列表。

原始记录保存在项目外工作区，不含令牌、密码、kubeconfig 内容或 Kubernetes Secret 值。没有执行镜像推送或真实部署，因此不存在本轮自动发布成功的结论。远端分支仍可能有未合并配置，外部发布系统也未纳入本次证据；本报告的缺失判断限定于当前默认分支及本轮可见配置。
