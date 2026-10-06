# Recall 与 Embedding A 侧 PR 申请

日期：2026-09-23。

- 仓库：https://github.com/csdnk/Ather-AIAgent
- 开发分支：codex/p3-recall-embedding-a
- 目标分支：codex/project-workspace（仓库默认基线）
- 已推送提交：ba7c215f0460119e7ffbc9d1574bb44735c155f9
- 范围：28 个代码、契约、测试及交付说明文件；其他任务改动保留在原工作区。
- 独立工作区完整门禁：664 项通过，无跳过；静态检查、严格类型及 122 个 Schema 检查通过。
- 状态：PR #4 已创建并打开，非草稿；已核对分支、提交号及可合并状态，尚未合并。
- 后续：等待非作者评审及 B/RF 消费者确认。当前凭证仍无法读取 Actions，远端 CI 状态尚未核验；本地门禁结果不等同于远端 CI 通过。

[查看 PR #4](https://github.com/csdnk/Ather-AIAgent/pull/4)

标题：feat(p3): implement guarded recall and embedding A workflow

下面为按仓库模板准备的 PR 正文。人工消费者确认及非作者评审留待 PR 流程完成。

---
## 问题与变化

实现已确认的 Recall / Embedding A 侧流程：Query 编码、块检索、B 资格审查、记忆级 Top K、完整正文组包、最终复核与事务提交。多块命中不再重复占据记忆名额，被排除块的高分不会抬高记忆排名；正文、关系或发布批次失效时拒绝提交及结果重取。

依据：2026-09-23 Recall 与 Embedding A 侧逐项实施计划，未绑定额外 Issue。

## 责任与接口

- Owner：A；RF 负责装配、事务、身份及事件；新增 B 契约由 B 提供方实现。
- 新增 CandidateQualificationTarget、CandidateQualificationResult、ContextGuardRequest、MemoryRelationSnapshot，以及 qualify、relations、revalidate_context 接口。同步 Python、122 个 Schema、样例及接口目录。
- Query / Passage 绑定完整 EmbeddingSpace，校验实际 tokenizer 上限、维度、摘要和有限值。保留默认 BGE 与既有空间标识；配置不一致时拒绝混用，不自动改标或重建向量。
- 新流程通过 enable_generation_recall 显式注入完整 B 提供方；默认保留现有真实链路。原 POST /p3/recall 契约保持兼容，补充受控状态及结果查询 GET 路由。
- 受影响消费者：A、B、RF。独立子代理已逐阶段审核并复审中文注释；人工 B/RF 消费者确认仍待完成。

## 正确性与运行

- [x] 复用 RF 身份、事务、任务和 Outbox；未新增流程私有队列。
- [x] 校验正文、对象、关系、授权和发布批次；撤权、删除、失配及并发修订阻止交付。
- [x] 新节点接入日志与 Trace；未提供真实 B 健康探针时报告 unknown。
- [x] 分页、重验、输入长度和期限有界；覆盖超时、取消及实际推理仍占用资源的场景。
- [x] 正文及凭证不进入日志；提交内容不含运行数据库、模型权重或凭证。测试中的示例文本用于可复现断言。

Working-only 不调用 Embedding、向量检索或 RRF；混合来源按记忆级 RRF 排序。冲突组完整装入或整组跳过；实际 tokenizer 计算含编号和分隔符的完整渲染串，不截断正文。read 与 packed 分别记录事实；结果与 packed 事件在同一事务内提交和回滚。

## 验证与交付

- [x] python scripts/p3/validate_collaboration.py：664 项通过，无跳过（122 流程 + 46 RF + 352 契约 + 144 Recall/原生适配）。
- [x] Ruff、严格类型检查、122 个 Schema、示例及协作入口检查通过。
- [x] 契约变更同步消费者正反例；覆盖越权、响应错配、非法向量、跨页补取、稳定排序、冲突组、精确预算、撤权、关系/发布批次变化、幂等和事务末尾回滚。
- [x] 新增接入说明与正式验收报告。18 个文件补充中文注释；修改前后 AST 及非注释 token 一致。

真实依赖验证（与替身测试分开）：

1. 新流程：真实 A + RF + SQLite，B 使用严格契约替身；不是新 B 真实联调。
2. 现有链路：真实 BGE-small-zh-v1.5 + 现有 B + SQLite + 本机 TCP HTTP，覆盖 Query/Passage、保存与投影、Recall、状态/结果查询、幂等及重启绑定。Windows 11 / Python 3.13.15 / CPU；仅为单机功能验证。

交付说明：

- AgentJYS-main/docs/p3/development/10_Recall与Embedding_A侧接入.md
- 交付成果/开发协作/13_Recall与Embedding_A侧实现与接入_20260923.md
- 交付成果/测试与验收/Recall与Embedding_A侧工程验收_20260923.md（保留提交前验收快照与范围说明）

尚未验证或实现：真实新 B 资格/正文/关系/事务守卫提供方及多块发布、Milvus、生产容量/多副本、人工消费者签认。新流程仅在完整接口齐备时显式启用；可继续运行默认现有链路，但旧服务不会降低标准读取新流程历史结果。

跨流程变更的消费者确认：

- [ ] B 提供方人工确认新增契约。
- [ ] RF / A 消费者人工确认事务、权限与装配边界。
- [ ] GitHub CI 结果与非作者代码评审通过后再合并。
