# Agent 平台 Azure 发布实施计划

日期：2026-10-05。用户已要求修改代码并将最新 Agent 与管理后台发布到 Azure。本次复用现有 AKS 和 HTTPS 网关，保留现有数据，不新建 VM。

## 目标与部署结构

同一 HTTPS 域名下，`/agent/` 提供 Agent，`/builder` 提供 Budibase 设计器，`/app/default%20workspace/aether-admin` 提供业务后台，`/identity` 提供 Keycloak。首页跳转 Agent。使用明确的 URL 前缀避免两套应用的认证及静态资源路径冲突。

新增云端平台数据库和身份数据库，保留用户 ID、业务归属、会话、Budibase 应用及账号。Agent 单实例运行并使用 Secure/HttpOnly Cookie、固定可信 Origin；内存登录会话重启后需重新登录，此次不宣称多实例或 HA 已验收。Keycloak 使用正式启动模式和 PostgreSQL。P3 继续使用现有 Azure 数据及 Temporal 工作流，避免变更运行中任务的持久化底座。

## 执行步骤

- [x] 修复现有 Temporal 转发并验证页面与工作流接口。
- [x] 在现有 agent-platform 工作树实现独立云端配置入口、内部认证地址、HTTPS Cookie 和 Agent URL 前缀，保留本地入口校验。先添加失败测试，再实现并回归。
- [x] 创建版本化发布制品、SHA-256 清单、部署配置和回退记录。依赖使用固定版本；凭据保存在工作区及 Kubernetes Secret，不写入源码。
- [x] 备份并迁移平台 PostgreSQL、Keycloak 用户主体、Budibase 持久数据。新增独立 PVC，不覆盖旧数据卷。迁移前检查运行中写入，切换阶段暂停本机写入。
- [x] 部署云端数据库、认证服务、Budibase、Agent 和 P3 云端身份投影同步；替换本机隧道依赖为 Kubernetes Service。
- [x] 验证云端 HTTPS 登录、普通用户与管理员边界、真实模型回复、P3 保存及召回、历史会话与后台查询。
- [x] 新版验证通过后切换网关并停止旧 Web；保留数据和回退配置。更新用户入口清单，移除旧文档及旧监测入口。

## 必须检查的边界

1. 错误 Origin、伪造租户和管理员登录 Agent 均拒绝。
2. 公开 issuer 与内部传输地址分离，JWT 仍严格验证公开 issuer 和 audience。
3. 数据迁移保留全部账号、租户、会话和消息；不得把空数据库当成迁移成功。
4. Budibase 数据查询通过当前用户签名令牌，不能用共享管理员密钥替代用户身份。
5. 云端组件不引用 localhost、Docker Desktop 地址或 Windows 文件路径；本机关闭后云端无需本机同步。

## 已知范围

当前 Azure 身份无 ACR 推送角色。使用允许拉取的基础镜像和带校验值的版本化应用制品发布，同时提供容器构建配置。正式镜像流水线、Temporal 从开发模式迁移到生产集群、HA、长稳和灾备演练不因本次 URL 发布自动完成；最终报告按实际验证结果分别说明。

最终执行结果、故障修复与保留限制见[Azure 发布记录](Agent平台Azure发布记录_20261005.md)。
