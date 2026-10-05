# Aether / AetherBrain P3

Aether P3 是面向 AI Agent 的持久记忆服务：接收对话、任务信息与文档，形成可追溯记忆，并在后续请求中返回经过权限、版本和有效性检查的上下文。

对外提供两个业务能力：**存储记忆（Remember）**与**召回记忆（Recall）**。内部 Operate 管理热度、缓存准备和动作核对；Temporal 编排持久任务、事件、周期与维护，业务底座保留事务、幂等及权限校验。

## 当前状态与访问

2026-10-05，Agent 用户平台、Budibase 管理后台、Builder、Keycloak 认证与平台数据库已部署到 Azure。公网首页进入 Agent，P3 由 Agent 经集群内部调用；Temporal 与只读 API 文档通过平台管理员登录校验。

| 入口 | 用途 |
|---|---|
| [Agent 用户平台](https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com/agent/) | 普通用户登录、对话、历史会话、个人记忆与来源 |
| [Budibase 管理后台](https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com/app/default%20workspace/aether-admin) | 平台／租户管理员管理用户、租户目录与变更记录 |
| [Builder](https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com/builder) | 设计器账号构建和发布管理界面 |
| [P3 API 文档](https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com/docs) / [OpenAPI JSON](https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com/openapi.json) | 平台管理员登录后查看，不提供在线执行 |

全部入口、Temporal 页面与账号角色见[页面和账号说明](交付成果/部署运行/项目Web页面与测试账号_20261005.md)。密码由项目负责人另行提供，不提交 Git。普通用户和管理员使用独立业务入口，租户由认证身份在服务端确定。

本次发布的范围与现场验证见[Azure 发布记录](交付成果/部署运行/Agent平台Azure发布记录_20261005.md)。云端服务独立于本机运行；当前仍为单实例测试部署，尚未完成 HA、24/72 小时长稳和完整灾备验收。云端使用基础镜像及校验后的 PVC 应用文件，自建镜像发布仍需 ACR 推送权限。

P3 当前源码要求 PostgreSQL、Redis、Milvus、Ceph 及独立 Temporal。旧 SQLite、Milvus Lite 和旧 Web 的验证材料属于历史记录，不能用作当前启动指引。真实依赖全量回归与业务验收仍需按目标环境单独执行；CI 成功不等于生产验收。

## 开发与部署

业务源码在 `AgentJYS-main/`，Python 要求 3.13。P3 安装、配置、鉴权及真实依赖测试见[业务代码指南](AgentJYS-main/README.md)。Agent、Budibase、云端网关、配置示例、Dockerfile 与发布脚本见[平台部署指南](AgentJYS-main/deploy/platform/README.md)。该部署目录依赖已有 AKS 和基础服务，不是空集群的一键安装器。

平台测试使用隔离的 PostgreSQL/Keycloak/Budibase 环境。未提供私有测试配置时，外部集成用例明确跳过，组件用例仍运行；不得将测试配置指向云端业务数据库。前端依赖、构建输出和原始测试记录放仓库外。

## 项目导航

| 位置 | 内容 |
|---|---|
| [业务代码与使用指南](AgentJYS-main/README.md) | 安装、鉴权、接口示例、模型接入、持续运行和开发验证 |
| [Temporal 部署运行指南](交付成果/部署运行/P3_Temporal本地运行与迁移指南_20260930.md) | 新启动方式、已有任务迁移、故障恢复与单实例约束 |
| [Temporal 工程验收](交付成果/测试与验收/P3_Temporal接入工程验收_20260930.md) | 测试结果、18 项场景证据和部署限制 |
| [交付成果](交付成果/README.md) | PRD、ADR、Epic、架构、Mock、验收报告和评审材料 |
| [PRD V1.3](交付成果/PRD版本/AetherBrain_P3_PRD_租户补全版_V1.3.docx) / [总体架构与流程](交付成果/架构设计/总体架构与流程.md) | 需求基线与流程设计 |
| [项目资料](项目资料/README.md) | 用户提供的原始要求、合同和历史参考 |
| [框架研读项目](P3_框架研读与工程验证_V1.0/P3%20框架研读与工程能力验证计划.md) | 已有研读项目，保留原位置 |

Mock 是独立演示成果，其展示效果不代表后端或外部集成已经验收。使用者应以统一服务接口和对应日期的工程证据判断可用范围。

## 仓库维护

项目关联 [csdnk/Ather-AIAgent](https://github.com/csdnk/Ather-AIAgent)。Git 根目录覆盖业务代码、交付成果、项目资料及研读项目；安装与测试命令在 `AgentJYS-main/` 执行。GitHub 实际工作流位于根目录 `.github/workflows/`，业务目录内的同名工作流是独立代码包参考，检查规则变更时应同步。

仓库保留可移植文档、历史交付及 `AgentJYS-main/` 业务源码和必要运行资源。本项目成品按分类放 `交付成果/`，原件放 `项目资料/`。环境、缓存、凭据、运行数据及助手处理记录放项目外 `E:/projects/codex/.agent-work/aether/workspace-support/`，不提交 Git。其他开发者可选择自己的外部工作目录。

历史代码提交仍在同一 Git 历史中；旧版本代码位于当时的仓库根目录，当前位于 `AgentJYS-main/`。
