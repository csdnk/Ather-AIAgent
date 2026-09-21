# P3 总体设计与协作契约

版本：1.0；日期：2026-09-19。当前产品依据：**AetherBrain_P3_PRD_租户补全版_V1.3.docx**（2026-09-19），2026-09-20按用户指示切换。本目录维护仓库内设计与机制；团队当前协作入口为[CONTRIBUTING](../../CONTRIBUTING.md)，面向用户的完整协作基线在交付包 `交付成果/开发协作/`。

**当前进度：公共底座与三个流程的本地基础实现已交付。** 最新入口是[三流程基础实现与联调](development/05_三个流程基础实现与联调.md)及[三流程验收说明](acceptance/reports/flows-delivery.md)。底座说明见[首批实现与运行](development/04_公共底座首批实现与运行.md)。真实记忆提取模型、生产分层及云部署尚未接通；Milvus适配已交付但真实服务器联调待环境，完整PRD验收仍保持`not_run`；各批次旧报告保留为历史证据。

**日志与健康检测增量：** 已接通节点输入输出摘要、失败堆栈位置、跨任务/事件的 trace_id、独立日志库、授权查询，以及实际本地依赖探测。使用与存储规范见[日志追踪与健康检测](development/06_日志追踪与健康检测.md)，本批证据见[验收报告](acceptance/reports/observability-delivery.md)。这不代表 OTLP/Azure 采集或 HTTP 服务已接通。

**当前优先级补充：先把系统搭建起来。** 按用户最新要求，吞吐、延迟、压缩比、算法收益及长稳时长只保留PRD来源追踪，暂不进入本轮或F1基座搭建门禁。身份隔离、状态真实性、故障可定位及恢复可接入仍需保证；健康、积压和失败记录属于运行检测，不是性能达标要求。

**真实 Embedding 增量：** 默认已切换为原生 BGE 中文 CPU 模型，Remember Passage 与 Recall Query 共用已绑定模型空间；SQLite 向量检索已支持 512 维。见[接入与清理说明](development/07_真实Embedding接入与旧实现清理.md)。此前报告中的“Embedding 未接入”属于历史批次状态。

**2026-09-20 Recall 增量：** 已接通真实 CrossEncoder（显式启用）、实际 tokenizer 预算和 Milvus 适配器；本地真实模型链路通过，Milvus只完成协议故障测试。不要用旧批次报告推断当前缺少 Embedding 或仍按字节预算。

## 从哪里开始

**旧代码清理：** 经引用、入口、配置及测试复核，删除 4 个闲置文件；仍有消费者的旧入口和兼容路径保留。见[清理与保留边界](development/08_旧代码清理与保留边界.md)及[本批验收](acceptance/reports/cleanup-delivery.md)。

| 阅读顺序 | 文件 | 读者与用途 |
|---|---|---|
| 1 | [需求基线与阶段](architecture/01_需求基线与阶段划分.md) | 全员：产品要求、当前交付、历史差异 |
| 2 | [总体架构与职责](architecture/02_总体架构与模块职责.md) | 全员：边界、所有权、依赖 |
| 3 | [流程及异常时序](architecture/03_三流程与异常时序.md) | 全员：请求、交接、失败、恢复 |
| 4 | [数据与一致性](architecture/04_数据归属与一致性.md) | 全员：事务、版本、删除、授权 |
| 5 | [部署与演进](architecture/05_部署安全与演进.md) | RF：Azure VM、AKS、备份、发布 |
| 6 | [接口与事件](contracts/01_协作接口与事件目录.md) | 各接口提供方和消费方共同阅读 |
| 7 | [状态与兼容](contracts/02_状态错误与兼容规则.md) | 全员：错误、状态推进、旧接口隔离 |
| 8 | [追溯检测与恢复](contracts/03_追溯检测与恢复接入.md) | 全员：必须接入的运行机制 |
| 9 | [底座实现清单](development/01_公共底座实现清单.md) | RF：下一阶段逐模块开发 |
| 10 | [三流程接入](development/02_三流程接入与责任表.md) | B/A/C：接口接入、完成标准 |
| 11 | [代码复用清单](development/03_现有代码复用与改造清单.md) | 全员：现有实现如何处理 |
| 12 | [验收说明](acceptance/01_验收场景与证据要求.md) | 全员：PRD、测试规格、运行证据 |
| 13 | [选型修订 ADR](adr/ADR-P3-001_V1.2产品基线与框架采用.md) | 全员：框架、基座和产品边界 |

## 当前需求基线

[基线登记及V1.3增量](../../contracts/p3/prd-baseline.yaml)为当前版本入口。下列requirements和source-manifest仍保留V1.2原文位置，是历史追踪索引；使用时须合并V13-TEN增量，不能把旧检查通过当作V1.3全量覆盖。

## 唯一维护来源

- Python 类型：`src/aether_agent_memory/{runtime,remember,recall,operate}/contracts/`。字段和枚举以这些模型为准；Protocol 定义接口，不表示对应实现尚未开发；实际接入状态看当前流程说明与验收。
- [机器契约入口](../../contracts/p3/README.md)：生成 Schema、接口目录、HTTP 映射、事件类型、正反样例及阶段参数。
- [需求追踪表](acceptance/requirements.yaml)：每个编号小节和 FR 的出处、Owner、阶段、设计及验收场景。
- [验收场景目录](acceptance/scenarios.yaml)：运行测试的步骤、断言和证据。不得将文档覆盖状态改写为运行通过。

本轮沿用四位责任人：B=Remember，A=Recall及共享语义计算／向量机制，C=Operate，RF=公共底座和集成。历史编号B1/B2/B3仅用于代码映射，不改变结果责任。

## 统一协作门禁

```text
python -m pip install -r scripts/p3/requirements-collaboration.txt
python scripts/p3/validate_collaboration.py
```

涵盖流程、底座、契约、原 Recall 和原生适配器回归。远端分支保护需要仓库维护者另行配置，YAML 文件不等于已经启用。

## 单独运行契约检查

在独立 Python 3.13 环境中，从仓库根目录执行：

```text
python -m pip install -r scripts/p3/requirements.txt
python scripts/p3/validate_contracts.py
```

修改模型后执行 `python scripts/p3/generate_schemas.py` 并提交生成变更。日常校验使用 `--check` 模式，不自动改写 Schema。CI 使用同一入口。检查不需要模型密钥、Milvus、Docker或Azure账号，不启动旧应用。

## 交付与工作材料分开

业务代码、契约、测试、运行配置与贡献入口留仓库；新用户文档和验收摘要放项目 `交付成果/`。助手草稿、原始日志、下载与环境放项目外 `E:/projects/codex/.agent-work/aether/workspace-support/`。仓库既有 [reports](acceptance/reports/README.md)保留历史证据，不回写为当前批次结果。

已有外置开发基线和 ADR 是历史输入，本目录纠正与 V1.2 不一致的条款。不得再引用旧“仅文本、无归档、无共享、模拟调度即交付、必须运维页”的限制缩减产品。
