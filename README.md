# Aether / AetherBrain P3

Aether P3 是面向 AI Agent 的持久记忆服务：接收对话、任务信息与文档，形成可追溯记忆，并在后续请求中返回经过权限、版本和有效性检查的上下文。

对外提供两个业务能力：**存储记忆（Remember）**与**召回记忆（Recall）**。内部 Operate 管理热度、缓存准备和动作核对；Temporal 编排持久任务、事件、周期与维护，业务底座保留事务、幂等及权限校验。

## 当前状态

2026-10-01：Working 已使用真实 BGE 向量投影与官方本地 Milvus Lite；五项必选真实功能验证通过，涵盖来源/租户隔离、持久重启和生命周期。见[本地开发指南](AgentJYS-main/docs/p3/development/Working与Milvus_本地开发指南.md)、[当前流程图](AgentJYS-main/docs/p3/architecture/Working与Milvus_当前流程.svg)和[验收报告](AgentJYS-main/docs/p3/development/Working与Milvus_验收报告.md)。本地 Lite 通过不代表生产集群验收。

**截至 2026-09-30，统一服务已接入 Temporal；外部集成与生产验收待完成。** 当前代码将 Remember、Recall 与内部 Operate 装配到一个 P3 进程，连接独立 Temporal Server；本次验证证据见 [Temporal 工程验收](交付成果/测试与验收/P3_Temporal接入工程验收_20260930.md)。

| 范围 | 状态 |
|---|---|
| 保存 → 后台长期化 → 索引发布 → 授权召回 → 结果再次获取 | 已实现并通过本地 HTTP/SQLite 验证 |
| 原生 BGE 编码、共享授权与撤权、本地文件缓存调度 | 已有实现和本地验证 |
| 真实监测 Web | 已接统一接口，提供健康、任务、异常和 Trace 瀑布图；云端 APM 接入待完成 |
| LLM、P2、Milvus 接口 | 官方本地 Milvus Lite 已真实验证；生产 Milvus 集群、LLM/P2 服务集成及业务质量待验收 |
| Docker、24/72 小时长稳、性能、多实例、完整灾备、云端部署 | 待实际环境验证；Docker 当前仅验证配置解析 |
| PRD FR16（P1）预测预热 | 未实现 |

最近的完整工程记录为 [2026-09-26 统一运行整合与验收](交付成果/测试与验收/P3_统一运行整合与验收_20260926.md)：Python 回归 **1305 passed**（含真实 BGE，排除 Docker 环境项），Ruff 通过，Mypy **356 个源码文件**通过。上述为该次工作区验收记录，不代表当前远端 CI、生产验收或 PRD 全部完成。

## 如何启动

需要 Python **3.13**。从仓库根目录进入业务代码目录：

```sh
cd AgentJYS-main
```

然后按 [代码 README 的快速启动](AgentJYS-main/README.md#快速启动) 创建外部虚拟环境、安装依赖、启动独立 Temporal Server、初始化部署目录并启动 P3。首跑推荐 `lexical` 模式，无需运行 LLM、P2、Milvus 或 Redis 服务，也无需下载 BGE 模型。该模式用于功能联通，语义效果需另行配置真实模型。

标准入口为 `python -m aether_agent_memory`（安装后也可用 `aether-p3`），使用 `init`、`check-config`、`serve` 三个命令，`init` 要求显式 Temporal endpoint。启动后等待 `/p3/readyz` 就绪，再访问 [接口文档](http://127.0.0.1:8080/docs)。一个 P3 进程承载 API 与 SDK Workers，独占业务目录；重启后按 Temporal 历史和业务完成证据恢复。已有业务目录须先执行离线迁移，见 [Temporal 运行与迁移指南](交付成果/部署运行/P3_Temporal本地运行与迁移指南_20260930.md)。

容器使用业务目录内的 `compose.p3.yaml`。旧 `compose.yaml`、`aether_agent_memory.app` 和 `/api/v1/...` 属于兼容栈，其配置与 API 不适用于新统一入口。

要打开监测页面，后端启动后在 `AgentJYS-main/web` 执行 `npm ci`、`npm run dev`（Node.js 22），访问 `http://127.0.0.1:5173` 并输入部署凭据。统一 Compose 的 Web 默认端口为 `3000`。详见 [真实监测 Web 使用指南](AgentJYS-main/web/README.md)。

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

仓库保留可移植文档、历史交付及 `AgentJYS-main/` 业务源码和必要运行资源。本机新成品按分类放外部 `D:/AStore项目/codex-aether/交付成果/`；原件放同级 `项目资料/`。环境、缓存、凭据、运行数据及助手处理记录放同级 `.agent-work/workspace-support/`，不提交 Git。其他开发者可选择自己的外部目录。

历史代码提交仍在同一 Git 历史中；旧版本代码位于当时的仓库根目录，当前位于 `AgentJYS-main/`。
