# A 组文档字段与代码类核对明细 V0.1

核对日期：2026-09-14。此表以现有文档字段为目标；新增路径/属性均为修改建议，尚未实施。

[返回核对结论与修改清单](A组_文档字段与现有代码逐项核对_V0.1.md)。

每个文档属性列一行。公共头单列，不重复展开到每个继承对象；复合字段按其类型另表展开。语义相同的公共类型共用一份定义。未命中显式映射的字段标为“新增契约字段”，表示缺少文档要求的强类型承接，不表示现有自由字典完全没有相近数据。

文档 `T?` 表示必须出现但允许 null；新 Python 契约中用 `T | None` 且不设置省略默认值。`uint` 需做非负整数校验，正数/范围/状态条件仍按“文档定义”执行。

## RecallRequest

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallRequest`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallRequest.request_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地标准请求的受控引用；与 RequestIndex、Execution 指向一致；[召回数据定义_V0.1 L104](运行时详细设计_V0.1/召回数据定义_V0.1.md:104) |
| RecallRequest.query | string / 是 | [ContextRequest.query](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:18) | 可复用值 | 新增 RecallRequest.query；复用原文本并校验非空和长度，保持文档的标准化规则。 | 校验后的非空 Query；不超过 P-01；不做隐式语义改写，规范化方式固定在 policy；[召回数据定义_V0.1 L105](运行时详细设计_V0.1/召回数据定义_V0.1.md:105) |
| RecallRequest.retrieval_mode | RetrievalMode / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.retrieval_mode`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 内部计算，外部不传；对已授权且符合范围、类型约束的适用来源取并集后生成；与 source_selection 的 eligible 来源一一对应；执行中不变；客户端传入则拒绝；[召回数据定义_V0.1 L106](运行时详细设计_V0.1/召回数据定义_V0.1.md:106) |
| RecallRequest.source_selection | SourceSelection / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.source_selection`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 内部生成，外部不传；保存本次来源选择理由；同时绑定授权证据、scope、筛选与策略；禁止客户端赋值；[召回数据定义_V0.1 L107](运行时详细设计_V0.1/召回数据定义_V0.1.md:107) |
| RecallRequest.scope | Scope / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.scope`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 认证/授权求交后的范围；不能因来源不可用扩大范围；[召回数据定义_V0.1 L108](运行时详细设计_V0.1/召回数据定义_V0.1.md:108) |
| RecallRequest.principal_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.principal_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可重验的调用身份引用；不保存认证 token；[召回数据定义_V0.1 L109](运行时详细设计_V0.1/召回数据定义_V0.1.md:109) |
| RecallRequest.authorization_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.authorization_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次已验证授权的受控证明；重放/发出前仍需复核，不等于永久授权；[召回数据定义_V0.1 L110](运行时详细设计_V0.1/召回数据定义_V0.1.md:110) |
| RecallRequest.idempotency_key | Id / 是 | [RequestContext.idempotency_key](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:20) | 需收紧约束 | 新增 RecallRequest.idempotency_key，要求有效值，并绑定 tenant、principal、scope、request_fingerprint。 | 上游幂等键；只在 tenant + principal + scope 内唯一；[召回数据定义_V0.1 L111](运行时详细设计_V0.1/召回数据定义_V0.1.md:111) |
| RecallRequest.request_fingerprint | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 对执行语义的规范化摘要；输入集合见本节下文；不包含自身；[召回数据定义_V0.1 L112](运行时详细设计_V0.1/召回数据定义_V0.1.md:112) |
| RecallRequest.deadline_at | Timestamp / 是 | [RequestContext.deadline](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:19) | 需适配 | 新增 RecallRequest.deadline_at；与原请求期限、策略上限取较早值并冻结；deadline_ms 不能作为每次重试重新计算的完整新期限。 | 上游期限与 P-02 上限取较早值；已过期拒绝；重启和重试不延长；[召回数据定义_V0.1 L113](运行时详细设计_V0.1/召回数据定义_V0.1.md:113) |
| RecallRequest.token_budget | uint / 是 | [ContextRequest.max_tokens](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:22) | 需适配 | 新增 RecallRequest.token_budget；从明确预算映射并校验正数/上限；不以旧默认 4096 证明上游明确给出预算。 | 上游明确给出的预算；0 < token_budget <= P-03；[召回数据定义_V0.1 L114](运行时详细设计_V0.1/召回数据定义_V0.1.md:114) |
| RecallRequest.tokenizer_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.tokenizer_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已认可的精确计数器；与实际注入方式匹配，未知组合拒绝；[召回数据定义_V0.1 L115](运行时详细设计_V0.1/召回数据定义_V0.1.md:115) |
| RecallRequest.tokenizer_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.tokenizer_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定 tokenizer 版本；不随默认模型更新漂移；[召回数据定义_V0.1 L116](运行时详细设计_V0.1/召回数据定义_V0.1.md:116) |
| RecallRequest.template_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.template_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定的上下文注入模板版本；AL-P403 需确认实际模板；[召回数据定义_V0.1 L117](运行时详细设计_V0.1/召回数据定义_V0.1.md:117) |
| RecallRequest.retrieval_constraints | RetrievalConstraints / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.retrieval_constraints`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 规范化外部类型/时间筛选，选路后内部派生 allowed_sources；外部不传 allowed_sources；类型保存筛选意图，各分支只用其适用部分；不能扩大 scope 或在执行中改来源；[召回数据定义_V0.1 L118](运行时详细设计_V0.1/召回数据定义_V0.1.md:118) |
| RecallRequest.retrieval_space_ref | ContractRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.retrieval_space_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次长期检索空间及模型绑定的只读引用；working_only 为 null；其他模式必须有值，消费既有批准配置；[召回数据定义_V0.1 L119](运行时详细设计_V0.1/召回数据定义_V0.1.md:119) |
| RecallRequest.policy_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequest.policy_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 服务固定的 Recall 参数、类型展开及选路算法版本；本次示例为 design-v0.1-source-union-v1；整次执行及同键重放沿用该版；[召回数据定义_V0.1 L120](运行时详细设计_V0.1/召回数据定义_V0.1.md:120) |

## SourceSelection

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`SourceSelection`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| SourceSelection.strategy | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceSelection.strategy`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 固定的选路算法标识，本版 scope_union_v1；与父请求 policy_version 绑定；调用方不能选择或覆盖；[召回数据定义_V0.1 L135](运行时详细设计_V0.1/召回数据定义_V0.1.md:135) |
| SourceSelection.working_eligibility | SourceEligibility / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceSelection.working_eligibility`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前会话/任务、授权及类型约束的本地判定；按无当前范围 → 未授权 → 类型排除 → eligible 的优先级；no_current_scope 仅在两个 ID 均 null 时成立；[召回数据定义_V0.1 L136](运行时详细设计_V0.1/召回数据定义_V0.1.md:136) |
| SourceSelection.long_term_eligibility | SourceEligibility / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceSelection.long_term_eligibility`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 长期范围读取授权及类型约束的本地判定；按未授权 → 类型排除 → eligible 的优先级；不能使用 no_current_scope；[召回数据定义_V0.1 L137](运行时详细设计_V0.1/召回数据定义_V0.1.md:137) |

## RecallRequestIndex

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallRequestIndex`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallRequestIndex.principal_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequestIndex.principal_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次受理的身份引用；与请求一致；[召回数据定义_V0.1 L235](运行时详细设计_V0.1/召回数据定义_V0.1.md:235) |
| RecallRequestIndex.scope_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequestIndex.scope_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对完整规范化 Scope 的摘要；唯一键比较完整摘要值及算法，不跨 scope 复用；[召回数据定义_V0.1 L236](运行时详细设计_V0.1/召回数据定义_V0.1.md:236) |
| RecallRequestIndex.idempotency_key | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequestIndex.idempotency_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次受理的幂等键；不覆盖旧绑定；[召回数据定义_V0.1 L237](运行时详细设计_V0.1/召回数据定义_V0.1.md:237) |
| RecallRequestIndex.request_fingerprint | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequestIndex.request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 请求语义摘要；与 RecallRequest 相等；[召回数据定义_V0.1 L238](运行时详细设计_V0.1/召回数据定义_V0.1.md:238) |
| RecallRequestIndex.recall_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequestIndex.recall_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次受理创建的执行；与请求/执行一致；[召回数据定义_V0.1 L239](运行时详细设计_V0.1/召回数据定义_V0.1.md:239) |
| RecallRequestIndex.request_ref | Ref<RecallRequest> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRequestIndex.request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 不可变标准请求引用；与执行建立同一可靠提交边界；[召回数据定义_V0.1 L240](运行时详细设计_V0.1/召回数据定义_V0.1.md:240) |

## RecallExecution

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallExecution`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallExecution.state | ExecutionState / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.state`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 唯一总流程中的当前状态；四终态不得回退；[召回数据定义_V0.1 L275](运行时详细设计_V0.1/召回数据定义_V0.1.md:275) |
| RecallExecution.request_ref | Ref<RecallRequest> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定的标准请求；不以重试请求替换；[召回数据定义_V0.1 L276](运行时详细设计_V0.1/召回数据定义_V0.1.md:276) |
| RecallExecution.policy_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.policy_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次执行固定策略；等于请求；[召回数据定义_V0.1 L277](运行时详细设计_V0.1/召回数据定义_V0.1.md:277) |
| RecallExecution.execution_deadline_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.execution_deadline_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次执行的 deadline；等于请求 deadline_at，恢复不延期；[召回数据定义_V0.1 L278](运行时详细设计_V0.1/召回数据定义_V0.1.md:278) |
| RecallExecution.checkpoint_refs | Map<Stage,Ref<RecallCheckpoint>> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.checkpoint_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 各阶段已可靠确认的输出引用，初始 {}；先落地后推进；键必须等于目标 checkpoint.stage；[召回数据定义_V0.1 L279](运行时详细设计_V0.1/召回数据定义_V0.1.md:279) |
| RecallExecution.lease_owner | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.lease_owner`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前推进者；未领取时 null；与 lease_until、lease_token 同有值或同 null；[召回数据定义_V0.1 L280](运行时详细设计_V0.1/召回数据定义_V0.1.md:280) |
| RecallExecution.lease_until | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.lease_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 推进租约到期时间；过期不能写状态，不修改业务 deadline；[召回数据定义_V0.1 L281](运行时详细设计_V0.1/召回数据定义_V0.1.md:281) |
| RecallExecution.lease_token | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.lease_token`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次领取的防旧写令牌；每次重新领取不同；写入同时检查 token、租约及 CAS；[召回数据定义_V0.1 L282](运行时详细设计_V0.1/召回数据定义_V0.1.md:282) |
| RecallExecution.source_coverage | SourceCoverage / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.source_coverage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前两来源覆盖；最终值与来源输出、Pack、Trace 一致；[召回数据定义_V0.1 L283](运行时详细设计_V0.1/召回数据定义_V0.1.md:283) |
| RecallExecution.excluded_candidates | List<CandidateExclusion> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.excluded_candidates`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已知排除，初始 []；与尚未确认的缺口分开；[召回数据定义_V0.1 L284](运行时详细设计_V0.1/召回数据定义_V0.1.md:284) |
| RecallExecution.degradation_reasons | List<RecallReason> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.degradation_reasons`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 尚未被合法回退消除的缺口；不能把已成功回退的 cache miss 继续算缺口；[召回数据定义_V0.1 L285](运行时详细设计_V0.1/召回数据定义_V0.1.md:285) |
| RecallExecution.confirmed_empty | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.confirmed_empty`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 初始 false；只在最终证据满足正常空判定时为 true；[召回数据定义_V0.1 L286](运行时详细设计_V0.1/召回数据定义_V0.1.md:286) |
| RecallExecution.fatal_reason | ReasonCode? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.fatal_reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 请求级失败的最终原因；非失败终态为 null；失败必须能解释；[召回数据定义_V0.1 L287](运行时详细设计_V0.1/召回数据定义_V0.1.md:287) |
| RecallExecution.result_ref | Ref<ContextPack>? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次不可变结果；未终态 null；每个已可靠提交终态都有结果，失败也用空包表达；[召回数据定义_V0.1 L288](运行时详细设计_V0.1/召回数据定义_V0.1.md:288) |
| RecallExecution.result_digest | Hash? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.result_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 冻结 Pack 的完整性摘要；与 result_ref 同有值；不对清除后的内容声称仍可读；[召回数据定义_V0.1 L289](运行时详细设计_V0.1/召回数据定义_V0.1.md:289) |
| RecallExecution.final_validation_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.final_validation_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最后一次完成必要授权/事实复核的时间；未执行或未完成则 null，不填收尾时间代替；[召回数据定义_V0.1 L290](运行时详细设计_V0.1/召回数据定义_V0.1.md:290) |
| RecallExecution.completed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.completed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 终态可靠收尾记录的时间；未终态 null；终态后固定；[召回数据定义_V0.1 L291](运行时详细设计_V0.1/召回数据定义_V0.1.md:291) |
| RecallExecution.last_event_sequence | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.last_event_sequence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本执行已可靠分配的最大事件序号，初始 0；单执行唯一递增；含终态后的真实交付事件；[召回数据定义_V0.1 L292](运行时详细设计_V0.1/召回数据定义_V0.1.md:292) |
| RecallExecution.source_result_refs | Map<LogicalSource,Ref<SourceReadResult>> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.source_result_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 初始 {}；发现或提前失败收尾后保存两来源引用；最终必须恰含 working、long_term；与 source_coverage 一致；[召回数据定义_V0.1 L293](运行时详细设计_V0.1/召回数据定义_V0.1.md:293) |
| RecallExecution.query_embedding_call | EmbeddingCallBinding? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.query_embedding_call`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Query 共享调用的稳定绑定；working_only 为 null；其他模式首次调用前可靠保存；[召回数据定义_V0.1 L294](运行时详细设计_V0.1/召回数据定义_V0.1.md:294) |
| RecallExecution.read_ledger | RecallReadLedger / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.read_ledger`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 在线调用次数、字节预占与结算账本；受理时初始化；调用前原子更新，重启不清零；[召回数据定义_V0.1 L295](运行时详细设计_V0.1/召回数据定义_V0.1.md:295) |
| RecallExecution.final_validation | FinalValidationBatch? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.final_validation`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 全部已加载可组装候选的最终复核；未到阶段为 null；不得只包含试装中的首选项；[召回数据定义_V0.1 L296](运行时详细设计_V0.1/召回数据定义_V0.1.md:296) |
| RecallExecution.finalization | RecallFinalization / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.finalization`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 唯一最终提交身份及独立恢复控制；受理时固定身份与恢复截止，见主设计 3.8.1；[召回数据定义_V0.1 L297](运行时详细设计_V0.1/召回数据定义_V0.1.md:297) |
| RecallExecution.retention | RecallRetention / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallExecution.retention`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 载荷与最小元数据分期清除；不修改 B 生命周期；未知提交及未解决事项有清理保护；[召回数据定义_V0.1 L298](运行时详细设计_V0.1/召回数据定义_V0.1.md:298) |

## RecallReason

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallReason`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallReason.reason | ReasonCode / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReason.reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地失败/降级原因；必须属于主文档原因码目录；[召回数据定义_V0.1 L313](运行时详细设计_V0.1/召回数据定义_V0.1.md:313) |
| RecallReason.affected_ref | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReason.affected_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 受影响的本地候选、来源结果或执行引用；请求级问题可 null；不直接公开内部引用；[召回数据定义_V0.1 L314](运行时详细设计_V0.1/召回数据定义_V0.1.md:314) |

## EmbeddingCallBinding

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`EmbeddingCallBinding`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| EmbeddingCallBinding.caller_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `EmbeddingCallBinding.caller_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 的固定共享能力调用身份；不随网络重试或 Worker 改变；[召回数据定义_V0.1 L320](运行时详细设计_V0.1/召回数据定义_V0.1.md:320) |
| EmbeddingCallBinding.caller_request_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `EmbeddingCallBinding.caller_request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次 recall_id；同租户/调用方唯一绑定；不得换键重获推理次数；[召回数据定义_V0.1 L321](运行时详细设计_V0.1/召回数据定义_V0.1.md:321) |
| EmbeddingCallBinding.embedding_request_ref | Ref<SemanticEmbeddingRequest>? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `EmbeddingCallBinding.embedding_request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 获知并验证共享请求后关联；首次回包丢失可 null；仍能按前两字段找回原执行；[召回数据定义_V0.1 L322](运行时详细设计_V0.1/召回数据定义_V0.1.md:322) |

## RecallReadLedger

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallReadLedger`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallReadLedger.attempts | List<RecallReadAttempt> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadLedger.attempts`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按可靠分配次序保存的调用尝试，初始 []；attempt_id 唯一；只追加及按许可状态结算，不删除重置额度；[召回数据定义_V0.1 L328](运行时详细设计_V0.1/召回数据定义_V0.1.md:328) |
| RecallReadLedger.bytes_charged | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadLedger.bytes_charged`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已结算实际字节加未知全额扣费，初始 0；等于所有非 reserved 尝试的 charged_bytes 之和；[召回数据定义_V0.1 L329](运行时详细设计_V0.1/召回数据定义_V0.1.md:329) |
| RecallReadLedger.bytes_reserved | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadLedger.bytes_reserved`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 所有在途正文预留之和，初始 0；等于 reserved 尝试的 reserved_bytes 之和；[召回数据定义_V0.1 L330](运行时详细设计_V0.1/召回数据定义_V0.1.md:330) |
| RecallReadLedger.admission_cursors | Map<Stage,uint> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadLedger.admission_cursors`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 各阶段稳定调用计划的准入位置，初始 {}；重启先恢复原计划；不能改变候选/批次顺序抢额度；[召回数据定义_V0.1 L331](运行时详细设计_V0.1/召回数据定义_V0.1.md:331) |

## RecallReadAttempt

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallReadAttempt`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallReadAttempt.attempt_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.attempt_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地稳定调用意图 ID；同执行唯一；ContentLoadResult 可引用；[召回数据定义_V0.1 L339](运行时详细设计_V0.1/召回数据定义_V0.1.md:339) |
| RecallReadAttempt.call_key | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.call_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对 tenant_id、recall_id、stage、capability、logical_target、input_digest 的 JCS 摘要；重试保持不变；同一 call_key 的 attempt_no 不超过 P-08；[召回数据定义_V0.1 L340](运行时详细设计_V0.1/召回数据定义_V0.1.md:340) |
| RecallReadAttempt.stage | Stage / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.stage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 发起调用的在线阶段；初查与最终复核使用不同阶段身份；[召回数据定义_V0.1 L341](运行时详细设计_V0.1/召回数据定义_V0.1.md:341) |
| RecallReadAttempt.capability | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.capability`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | authorization_read、query_embedding_attach、working_read、vector_search、memory_read、canonical_read、prewarm_read、final_revalidate 之一；authorization_read 是已受理执行的身份/范围复核；本地身份不冒充远端方法名；[召回数据定义_V0.1 L342](运行时详细设计_V0.1/召回数据定义_V0.1.md:342) |
| RecallReadAttempt.logical_target | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.logical_target`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 准确来源、内容版本/范围或固定批次计划的本地 ID；不能随机换目标 ID 绕过重试额度；[召回数据定义_V0.1 L343](运行时详细设计_V0.1/召回数据定义_V0.1.md:343) |
| RecallReadAttempt.input_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.input_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定语义输入及有序批次成员摘要；排除 trace/瞬时授权引用，重试仍重新验证权限；[召回数据定义_V0.1 L344](运行时详细设计_V0.1/召回数据定义_V0.1.md:344) |
| RecallReadAttempt.attempt_no | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.attempt_no`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 同 call_key 从 1 递增的已占次数；保存后即占额度，即使尚未确认发出；[召回数据定义_V0.1 L345](运行时详细设计_V0.1/召回数据定义_V0.1.md:345) |
| RecallReadAttempt.state | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.state`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | reserved、settled、uncertain 之一；reserved 可转 settled/uncertain，后两者不可重新打开；[召回数据定义_V0.1 L346](运行时详细设计_V0.1/召回数据定义_V0.1.md:346) |
| RecallReadAttempt.reserved_bytes | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.reserved_bytes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 调用前预留的最大正文 byte；纯元数据/向量/推理附着为 0；正文须覆盖完整批准片段且有接收硬上限；[召回数据定义_V0.1 L347](运行时详细设计_V0.1/召回数据定义_V0.1.md:347) |
| RecallReadAttempt.received_bytes | uint? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.received_bytes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已可靠结算的实际接收正文 byte；reserved/uncertain 为 null；settled 为真实值且不超过预留；[召回数据定义_V0.1 L348](运行时详细设计_V0.1/召回数据定义_V0.1.md:348) |
| RecallReadAttempt.charged_bytes | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.charged_bytes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此尝试已扣正文额度；reserved 为 0；settled 等于 received_bytes；uncertain 等于 reserved_bytes；[召回数据定义_V0.1 L349](运行时详细设计_V0.1/召回数据定义_V0.1.md:349) |
| RecallReadAttempt.reserved_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.reserved_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 调用意图可靠保存时间；不冒充远端开始时间；[召回数据定义_V0.1 L350](运行时详细设计_V0.1/召回数据定义_V0.1.md:350) |
| RecallReadAttempt.call_deadline_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.call_deadline_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此尝试原始调用截止；不晚于原在线 deadline，重启不改；[召回数据定义_V0.1 L351](运行时详细设计_V0.1/召回数据定义_V0.1.md:351) |
| RecallReadAttempt.closed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.closed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 结算或判为 uncertain 的本地时间；reserved 为 null；其余必有值；[召回数据定义_V0.1 L352](运行时详细设计_V0.1/召回数据定义_V0.1.md:352) |
| RecallReadAttempt.output_ref | ProtectedRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallReadAttempt.output_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已可靠保存的本次结果或错误证据；uncertain 不伪造成功结果；失租回调不替换；[召回数据定义_V0.1 L353](运行时详细设计_V0.1/召回数据定义_V0.1.md:353) |

## FinalValidationBatch

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`FinalValidationBatch`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| FinalValidationBatch.candidate_ids | List<Id> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationBatch.candidate_ids`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 排序阶段冻结的全部已加载可组装候选，含替补；去重、固定顺序；批量拆分的并集必须等于本集合；[召回数据定义_V0.1 L361](运行时详细设计_V0.1/召回数据定义_V0.1.md:361) |
| FinalValidationBatch.items | List<FinalValidationItem> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationBatch.items`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 逐候选最终判断；与 candidate_ids 一一对应；缺响应也创建 unverifiable 项；[召回数据定义_V0.1 L362](运行时详细设计_V0.1/召回数据定义_V0.1.md:362) |
| FinalValidationBatch.request_authorization_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationBatch.request_authorization_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本轮请求级授权复核依据；可用或正常空结果须有当前有效依据；失败不伪造；[召回数据定义_V0.1 L363](运行时详细设计_V0.1/召回数据定义_V0.1.md:363) |
| FinalValidationBatch.request_authorization_valid_until | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationBatch.request_authorization_valid_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 上述证据按已确认契约可采用的截止；与证据同有值；不能自行用无限期替代未知窗口；[召回数据定义_V0.1 L364](运行时详细设计_V0.1/召回数据定义_V0.1.md:364) |
| FinalValidationBatch.started_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationBatch.started_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本轮开始时点；排序后固定范围，再发起分批复核；[召回数据定义_V0.1 L365](运行时详细设计_V0.1/召回数据定义_V0.1.md:365) |
| FinalValidationBatch.completed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationBatch.completed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本轮全部项已归类的时间；分批超时后可将缺项记未知而完成归类；不表示全部通过；[召回数据定义_V0.1 L366](运行时详细设计_V0.1/召回数据定义_V0.1.md:366) |

## FinalValidationItem

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`FinalValidationItem`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| FinalValidationItem.candidate_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationItem.candidate_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原已加载候选；必须属于本轮冻结集合；[召回数据定义_V0.1 L372](运行时详细设计_V0.1/召回数据定义_V0.1.md:372) |
| FinalValidationItem.decision | CandidateDecision / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationItem.decision`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | accepted、excluded、unverifiable；不改写初次资格记录；[召回数据定义_V0.1 L373](运行时详细设计_V0.1/召回数据定义_V0.1.md:373) |
| FinalValidationItem.evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationItem.evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 当前授权/版本/状态及 Working TTL 证据；accepted 必填；未知不补造；[召回数据定义_V0.1 L374](运行时详细设计_V0.1/召回数据定义_V0.1.md:374) |
| FinalValidationItem.validated_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationItem.validated_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前事实得到证明的时间；accepted 必填；不以本地组装结束时间替代；[召回数据定义_V0.1 L375](运行时详细设计_V0.1/召回数据定义_V0.1.md:375) |
| FinalValidationItem.valid_until | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationItem.valid_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按 B/RF 已确认窗口及实际 TTL 取更早截止；accepted 必填；不超过请求级授权可用窗口；采用时须 now < valid_until；[召回数据定义_V0.1 L376](运行时详细设计_V0.1/召回数据定义_V0.1.md:376) |
| FinalValidationItem.reason_codes | List<ReasonCode> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalValidationItem.reason_codes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 排除或无法确认的原因；非 accepted 必须有可解释原因；[召回数据定义_V0.1 L377](运行时详细设计_V0.1/召回数据定义_V0.1.md:377) |

## RecallFinalization

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallFinalization`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallFinalization.commit_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.commit_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次受理时生成的稳定最终提交 ID；同 recall_id 绑定，任何重试不换键；[召回数据定义_V0.1 L385](运行时详细设计_V0.1/召回数据定义_V0.1.md:385) |
| RecallFinalization.generation | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.generation`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 防迟到旧准备稿提交的代际，初始 0；只在原子隔离/废弃旧准备稿时递增，不改变 commit_id；[召回数据定义_V0.1 L386](运行时详细设计_V0.1/召回数据定义_V0.1.md:386) |
| RecallFinalization.phase | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.phase`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | idle、prepared、commit_unknown、committed、attention_required；协调进度，不是新增 Recall 业务状态；[召回数据定义_V0.1 L387](运行时详细设计_V0.1/召回数据定义_V0.1.md:387) |
| RecallFinalization.draft_ref | ProtectedRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.draft_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前准备稿及最小 Trace/已发生事件的受控集合；与 draft_digest/proposed_state 同有值；敏感清除后解析显式失败；[召回数据定义_V0.1 L388](运行时详细设计_V0.1/召回数据定义_V0.1.md:388) |
| RecallFinalization.draft_digest | Hash? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.draft_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前准备稿的完整输入摘要；提交须匹配 generation、摘要及执行 CAS；不覆盖已提交结果；[召回数据定义_V0.1 L389](运行时详细设计_V0.1/召回数据定义_V0.1.md:389) |
| RecallFinalization.proposed_state | TerminalState? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.proposed_state`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 待原子提交的四终态之一；prepared/commit_unknown 必填，不等于已经终态；[召回数据定义_V0.1 L390](运行时详细设计_V0.1/召回数据定义_V0.1.md:390) |
| RecallFinalization.publish_before | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.publish_before`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可用/正常空准备稿最晚提交时点；取原 deadline、请求级及所采用最终证据有效截止最小值；失败准备稿可 null；[召回数据定义_V0.1 L391](运行时详细设计_V0.1/召回数据定义_V0.1.md:391) |
| RecallFinalization.recovery_deadline_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.recovery_deadline_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次受理时间加固定 P-22；不因发现故障、重启或换 Worker 延长；[召回数据定义_V0.1 L392](运行时详细设计_V0.1/召回数据定义_V0.1.md:392) |
| RecallFinalization.attempts_reserved | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.attempts_reserved`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次提交、同稿重交、查证及隔离收尾已占次数，初始 0；不超过 P-23；每次先保存，崩溃不退还；[召回数据定义_V0.1 L393](运行时详细设计_V0.1/召回数据定义_V0.1.md:393) |
| RecallFinalization.last_call | FinalizationCallIntent? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.last_call`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最近一次已占用的提交/恢复调用；与计数原子更新；未知回包先查原 commit_id；[召回数据定义_V0.1 L394](运行时详细设计_V0.1/召回数据定义_V0.1.md:394) |
| RecallFinalization.recovery_not_before | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.recovery_not_before`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按 P-24 计算的下次恢复调用最早时间；不能绕过退避连续查证；超过恢复截止即停止自动调用；[召回数据定义_V0.1 L395](运行时详细设计_V0.1/召回数据定义_V0.1.md:395) |
| RecallFinalization.commit_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallFinalization.commit_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 唯一最终结果的可靠提交证明；committed 必填，不能由准备稿自行生成；[召回数据定义_V0.1 L396](运行时详细设计_V0.1/召回数据定义_V0.1.md:396) |

## FinalizationCallIntent

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`FinalizationCallIntent`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| FinalizationCallIntent.sequence | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalizationCallIntent.sequence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次已占最终提交/恢复次数；等于父记录保存该意图时的 attempts_reserved；[召回数据定义_V0.1 L402](运行时详细设计_V0.1/召回数据定义_V0.1.md:402) |
| FinalizationCallIntent.kind | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalizationCallIntent.kind`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | commit_draft、query_commit 或 fence_and_finalize_failure；commit_draft 只在原 deadline/证据有效时允许；其他调用不读取业务内容；[召回数据定义_V0.1 L403](运行时详细设计_V0.1/召回数据定义_V0.1.md:403) |
| FinalizationCallIntent.generation | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalizationCallIntent.generation`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次调用预期的提交代际；服务端原子比较；旧代不能覆盖最终结果；[召回数据定义_V0.1 L404](运行时详细设计_V0.1/召回数据定义_V0.1.md:404) |
| FinalizationCallIntent.reserved_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalizationCallIntent.reserved_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可靠保存意图的时间；保存未知则先查本地意图，不先调用；[召回数据定义_V0.1 L405](运行时详细设计_V0.1/召回数据定义_V0.1.md:405) |
| FinalizationCallIntent.deadline_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `FinalizationCallIntent.deadline_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次提交/恢复调用截止；不超过 recovery_deadline_at；commit_draft 另不超过 publish_before；时长不超 P-02；[召回数据定义_V0.1 L406](运行时详细设计_V0.1/召回数据定义_V0.1.md:406) |

## RecallRetention

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallRetention`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallRetention.payload_retain_until | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRetention.payload_retain_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 终态提交时间加 P-16；未终态为 null；P-16=0 立即禁止后续正文重放；[召回数据定义_V0.1 L414](运行时详细设计_V0.1/召回数据定义_V0.1.md:414) |
| RecallRetention.metadata_retain_until | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRetention.metadata_retain_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 至少覆盖原恢复窗口与已确定载荷窗口的较晚结束点；初始等于 recovery_deadline_at；只延长不因正文清除缩短；[召回数据定义_V0.1 L415](运行时详细设计_V0.1/召回数据定义_V0.1.md:415) |
| RecallRetention.payload_state | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRetention.payload_state`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | retained 或 cleared；清除后不得把同键当新执行；[召回数据定义_V0.1 L416](运行时详细设计_V0.1/召回数据定义_V0.1.md:416) |
| RecallRetention.payload_cleared_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallRetention.payload_cleared_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 敏感载荷实际清除时间；cleared 必填；retained 为 null；[召回数据定义_V0.1 L417](运行时详细设计_V0.1/召回数据定义_V0.1.md:417) |

## RecallCheckpoint

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallCheckpoint`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallCheckpoint.checkpoint_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.checkpoint_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地稳定检查点引用；绑定本表唯一键；[召回数据定义_V0.1 L448](运行时详细设计_V0.1/召回数据定义_V0.1.md:448) |
| RecallCheckpoint.stage | Stage / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.stage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 产生输出的八个 RUNNING 阶段之一；与 Execution 中映射键一致；[召回数据定义_V0.1 L449](运行时详细设计_V0.1/召回数据定义_V0.1.md:449) |
| RecallCheckpoint.input_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.input_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已固定输入/版本/受控引用的摘要；不能只存可变外部对象 ID 而忽略版本；[召回数据定义_V0.1 L450](运行时详细设计_V0.1/召回数据定义_V0.1.md:450) |
| RecallCheckpoint.output_ref | ProtectedRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.output_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已可靠保存的阶段输出；可包含本文件多种执行期记录；受控且可验证；[召回数据定义_V0.1 L451](运行时详细设计_V0.1/召回数据定义_V0.1.md:451) |
| RecallCheckpoint.output_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.output_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 不可变输出摘要；实际载荷匹配后才可推进；[召回数据定义_V0.1 L452](运行时详细设计_V0.1/召回数据定义_V0.1.md:452) |
| RecallCheckpoint.completed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.completed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次阶段输出确认时间；不声称外部事实永不过期；[召回数据定义_V0.1 L453](运行时详细设计_V0.1/召回数据定义_V0.1.md:453) |
| RecallCheckpoint.source_schema_versions | Map<ContractRef,Version> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.source_schema_versions`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际采用的外部契约版本，未调用时 {}；外部版本原样绑定，不用 Recall schema 代替；[召回数据定义_V0.1 L454](运行时详细设计_V0.1/召回数据定义_V0.1.md:454) |
| RecallCheckpoint.policy_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.policy_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定 Recall 策略；与请求一致；[召回数据定义_V0.1 L455](运行时详细设计_V0.1/召回数据定义_V0.1.md:455) |
| RecallCheckpoint.attempt | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.attempt`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地阶段尝试序号，从 1 开始；不是 P-08 的远程调用重试次数；[召回数据定义_V0.1 L456](运行时详细设计_V0.1/召回数据定义_V0.1.md:456) |
| RecallCheckpoint.sensitivity | Sensitivity / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallCheckpoint.sensitivity`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 输出最高敏感级别；含正文、向量或 Query 时为 restricted_payload；[召回数据定义_V0.1 L457](运行时详细设计_V0.1/召回数据定义_V0.1.md:457) |

## QueryEmbeddingResult

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`QueryEmbeddingResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| QueryEmbeddingResult.embedding_result_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.embedding_result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地结果标识；检查点和搜索结果据此关联；[召回数据定义_V0.1 L511](运行时详细设计_V0.1/召回数据定义_V0.1.md:511) |
| QueryEmbeddingResult.vector_ref | ProtectedRef / 是 | [EmbeddingRecord.vector](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py:44) | 需转换 | 新增 vector_ref；把已验证的真实向量保存为受控载荷再记录引用；不是将 list[float] 强行改成字符串。 | 真实向量的受控引用；校验有限数值、实际维度和 dtype；不允许伪向量替代真实验收；[召回数据定义_V0.1 L512](运行时详细设计_V0.1/召回数据定义_V0.1.md:512) |
| QueryEmbeddingResult.usage | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.usage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次调用用途；固定 Query，不定义 Passage 请求；[召回数据定义_V0.1 L513](运行时详细设计_V0.1/召回数据定义_V0.1.md:513) |
| QueryEmbeddingResult.model_id | ExternalId / 是 | [VectorQueryResult.model](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:186) | 需校验后映射 | 新增目标字段；模型名须匹配固定模型目录，维度须核对实际向量长度。Sidecar 的 embedding_model/embedding_dim 可作实际运行来源。 | 实际模型标识；与检索空间批准绑定一致；[召回数据定义_V0.1 L514](运行时详细设计_V0.1/召回数据定义_V0.1.md:514) |
| QueryEmbeddingResult.model_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.model_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际模型版本；不临时切换不兼容版本；[召回数据定义_V0.1 L515](运行时详细设计_V0.1/召回数据定义_V0.1.md:515) |
| QueryEmbeddingResult.dimension | uint / 是 | [VectorQueryResult.dimension](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:185) | 需校验后映射 | 新增目标字段；模型名须匹配固定模型目录，维度须核对实际向量长度。Sidecar 的 embedding_model/embedding_dim 可作实际运行来源。 | 实际向量维度；大于 0 且与实际向量长度一致；[召回数据定义_V0.1 L516](运行时详细设计_V0.1/召回数据定义_V0.1.md:516) |
| QueryEmbeddingResult.dtype | ExternalLabel / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.dtype`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原模型契约的数据类型；只能使用已批准兼容类型；[召回数据定义_V0.1 L517](运行时详细设计_V0.1/召回数据定义_V0.1.md:517) |
| QueryEmbeddingResult.embedding_schema_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.embedding_schema_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际嵌入输出契约版本；不等于 Recall schema_version；[召回数据定义_V0.1 L518](运行时详细设计_V0.1/召回数据定义_V0.1.md:518) |
| QueryEmbeddingResult.source_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.source_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地绑定本次 query 输入的摘要；若共享能力有额外预处理，须保留原契约映射证据；[召回数据定义_V0.1 L519](运行时详细设计_V0.1/召回数据定义_V0.1.md:519) |
| QueryEmbeddingResult.retrieval_space_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.retrieval_space_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 目标检索空间及兼容规则；与请求一致；[召回数据定义_V0.1 L520](运行时详细设计_V0.1/召回数据定义_V0.1.md:520) |
| QueryEmbeddingResult.external_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.external_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次共享能力原契约；只引用，不定义其输入 schema；[召回数据定义_V0.1 L521](运行时详细设计_V0.1/召回数据定义_V0.1.md:521) |
| QueryEmbeddingResult.source_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.source_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际调用、输入绑定和向量验证证据；不凭配置声称已产生向量；[召回数据定义_V0.1 L522](运行时详细设计_V0.1/召回数据定义_V0.1.md:522) |
| QueryEmbeddingResult.validated_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `QueryEmbeddingResult.validated_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地兼容校验完成时间；搜索前形成；[召回数据定义_V0.1 L523](运行时详细设计_V0.1/召回数据定义_V0.1.md:523) |

## VectorCandidateSet

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`VectorCandidateSet`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| VectorCandidateSet.set_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.set_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地候选集合标识；绑定不可变搜索输出；[召回数据定义_V0.1 L558](运行时详细设计_V0.1/召回数据定义_V0.1.md:558) |
| VectorCandidateSet.embedding_result_ref | Ref<QueryEmbeddingResult> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.embedding_result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次真实 Query 向量结果；同一执行及检索空间；[召回数据定义_V0.1 L559](运行时详细设计_V0.1/召回数据定义_V0.1.md:559) |
| VectorCandidateSet.search_operation_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.search_operation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 稳定的本地搜索调用关联；不要求 P2 为读请求新增操作查询接口；[召回数据定义_V0.1 L560](运行时详细设计_V0.1/召回数据定义_V0.1.md:560) |
| VectorCandidateSet.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际搜索能力的 Provider 关联；按原契约保留；[召回数据定义_V0.1 L561](运行时详细设计_V0.1/召回数据定义_V0.1.md:561) |
| VectorCandidateSet.external_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.external_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已使用的 VEC-002 契约版本；不视为本地模型字段定义权；[召回数据定义_V0.1 L562](运行时详细设计_V0.1/召回数据定义_V0.1.md:562) |
| VectorCandidateSet.source_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.source_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 有界搜索的响应及完成证据；未取得可信响应时不伪造一个 complete 空集合；[召回数据定义_V0.1 L563](运行时详细设计_V0.1/召回数据定义_V0.1.md:563) |
| VectorCandidateSet.requested_k | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.requested_k`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际请求的 K；大于 0，受 P-05 及固定 policy 限制；[召回数据定义_V0.1 L564](运行时详细设计_V0.1/召回数据定义_V0.1.md:564) |
| VectorCandidateSet.returned_count | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.returned_count`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前保留的原始候选条数；等于 candidates.length，不凭它判断完成；[召回数据定义_V0.1 L565](运行时详细设计_V0.1/召回数据定义_V0.1.md:565) |
| VectorCandidateSet.complete | bool / 是 | [RecallSourceResult.complete](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:53) | 语义不等价 | 新增 complete 并从可信 P2 完成证据判定；不能直接复制旧来源对象的默认值。 | 本次约定 TopK 范围已完整完成；不代表全库扫描完成；[召回数据定义_V0.1 L566](运行时详细设计_V0.1/召回数据定义_V0.1.md:566) |
| VectorCandidateSet.candidates | List<VectorCandidate> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.candidates`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 搜索返回的候选，允许 []；candidate_id 在执行内唯一；条数不大于 requested_k；[召回数据定义_V0.1 L567](运行时详细设计_V0.1/召回数据定义_V0.1.md:567) |
| VectorCandidateSet.partial_reason | ReasonCode? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.partial_reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 有界响应未完成的本地原因；complete=true 时 null；否则必须解释；[召回数据定义_V0.1 L568](运行时详细设计_V0.1/召回数据定义_V0.1.md:568) |
| VectorCandidateSet.observed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidateSet.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地观测到搜索结果的时间；来源时间另通过证据保存；[召回数据定义_V0.1 L569](运行时详细设计_V0.1/召回数据定义_V0.1.md:569) |

## VectorCandidate

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`VectorCandidate`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| VectorCandidate.candidate_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidate.candidate_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 分配的本地发现标识；同阶段重放复用，不等于 memory_id；[召回数据定义_V0.1 L584](运行时详细设计_V0.1/召回数据定义_V0.1.md:584) |
| VectorCandidate.projection_ref | ExternalId / 是 | [P2VectorHit.id](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:33) | 需契约映射 | 新增 projection_ref；只有确认 id 是 B 可解析的投影引用才赋值。不得直接推导 memory_id。 | 原搜索结果的稳定 Projection 引用；必须能交由 B 解析；不能自行拼 Memory ID；[召回数据定义_V0.1 L585](运行时详细设计_V0.1/召回数据定义_V0.1.md:585) |
| VectorCandidate.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidate.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原始搜索 Provider；与所属结果证据一致；[召回数据定义_V0.1 L586](运行时详细设计_V0.1/召回数据定义_V0.1.md:586) |
| VectorCandidate.provider_rank | uint / 是 | [P2GrpcClient](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:63) | 需契约映射 | 新增 provider_rank；只有确认 P2 返回顺序即排名才从 1 编号，不能靠 raw_score 猜测。 | 来源契约排名或已确认的返回顺序；从 1 开始，不用 raw_score 猜排名；[召回数据定义_V0.1 L587](运行时详细设计_V0.1/召回数据定义_V0.1.md:587) |
| VectorCandidate.raw_score | number? / 是 | [P2VectorHit.score](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:34) | 需保留未知 | 新增 raw_score: float \| None（必填可空）；原分数存在时保留，无来源分数时显式 null，不能复用 MemorySearchHit.score 的默认 0。 | 原分数；无分数时 null；不直接与 Working 或其他空间分数比较；[召回数据定义_V0.1 L588](运行时详细设计_V0.1/召回数据定义_V0.1.md:588) |
| VectorCandidate.model_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidate.model_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 候选所在模型空间版本；与已验证 Query 空间兼容；[召回数据定义_V0.1 L589](运行时详细设计_V0.1/召回数据定义_V0.1.md:589) |
| VectorCandidate.projection_schema_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidate.projection_schema_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 候选 Projection 的原契约版本；仍需 B 确认可读性；[召回数据定义_V0.1 L590](运行时详细设计_V0.1/召回数据定义_V0.1.md:590) |
| VectorCandidate.observed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidate.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地观测该候选的时间；可继承集合观察时间；[召回数据定义_V0.1 L591](运行时详细设计_V0.1/召回数据定义_V0.1.md:591) |
| VectorCandidate.external_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidate.external_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该条来源契约版本；保留精确解释依据；[召回数据定义_V0.1 L592](运行时详细设计_V0.1/召回数据定义_V0.1.md:592) |
| VectorCandidate.source_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VectorCandidate.source_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该条原始引用/排序证据；不把向量命中当正文可用；[召回数据定义_V0.1 L593](运行时详细设计_V0.1/召回数据定义_V0.1.md:593) |

## SourceReadResult

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`SourceReadResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| SourceReadResult.source_result_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.source_result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地来源结果标识；在 producer_stage 对应受控输出中可解析；[召回数据定义_V0.1 L620](运行时详细设计_V0.1/召回数据定义_V0.1.md:620) |
| SourceReadResult.source_id | LogicalSource / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.source_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | working 或 long_term；不允许 prewarm；[召回数据定义_V0.1 L621](运行时详细设计_V0.1/召回数据定义_V0.1.md:621) |
| SourceReadResult.producer_stage | Stage / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.producer_stage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际创建本记录的阶段；仅 RUNNING_VECTOR_SEARCH 或 RUNNING_TRACE_FINALIZATION；不伪造发现检查点；[召回数据定义_V0.1 L622](运行时详细设计_V0.1/召回数据定义_V0.1.md:622) |
| SourceReadResult.read_attempted | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.read_attempted`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否存在该来源的远程发现调用意图；有可靠 reserved 意图即视为可能调用；无意图为 false；不包括 Query 推理；[召回数据定义_V0.1 L623](运行时详细设计_V0.1/召回数据定义_V0.1.md:623) |
| SourceReadResult.coverage | CoverageStatus / 是 | [RecallSourceResult.complete](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:53) | 需新增多状态字段 | 新增 coverage，以及同对象 bounds、completion_evidence_ref 等字段；旧布尔值不足以区分未请求、部分返回、不可用和完成。 | 本次有界读取的覆盖结论；与执行对应来源一致；[召回数据定义_V0.1 L624](运行时详细设计_V0.1/召回数据定义_V0.1.md:624) |
| SourceReadResult.candidate_refs | List<SourceReference> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.candidate_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 发现时的复合引用，允许 []；不指向尚未生成的 RecallCandidate；后者沿 candidate_id 关联；[召回数据定义_V0.1 L625](运行时详细设计_V0.1/召回数据定义_V0.1.md:625) |
| SourceReadResult.bounds | ReadBounds? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.bounds`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次来源拟请求/实际请求范围及可证明快照条件；not_requested 时 null；必需来源即使未调用也保留计划范围，snapshot_at 未知为 null；[召回数据定义_V0.1 L626](运行时详细设计_V0.1/召回数据定义_V0.1.md:626) |
| SourceReadResult.reason_codes | List<ReasonCode> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.reason_codes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 未完成/不可用原因；partial、unavailable 不能无解释地冒充空结果；[召回数据定义_V0.1 L627](运行时详细设计_V0.1/召回数据定义_V0.1.md:627) |
| SourceReadResult.vector_set_ref | Ref<VectorCandidateSet>? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.vector_set_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 长期分支的可信搜索响应；Working 和无可信搜索响应时 null；[召回数据定义_V0.1 L628](运行时详细设计_V0.1/召回数据定义_V0.1.md:628) |
| SourceReadResult.completion_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.completion_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 来源范围完成的证明；complete 必须有值；null 不能推断 complete；[召回数据定义_V0.1 L629](运行时详细设计_V0.1/召回数据定义_V0.1.md:629) |
| SourceReadResult.observed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `SourceReadResult.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地形成此覆盖判断的时间；不冒充外部快照时间；[召回数据定义_V0.1 L630](运行时详细设计_V0.1/召回数据定义_V0.1.md:630) |

## ReadBounds

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`ReadBounds`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ReadBounds.limit | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ReadBounds.limit`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际条数上限；Working 按 P-04，长期按 P-05；大于 0；[召回数据定义_V0.1 L645](运行时详细设计_V0.1/召回数据定义_V0.1.md:645) |
| ReadBounds.filter | RetrievalConstraints / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ReadBounds.filter`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 从标准请求投影出的本来源实际读取约束；memory_types 只保留该来源适用子集、allowed_sources 仅本来源；时间不变，不扩大 Scope，不改变父请求模式；[召回数据定义_V0.1 L646](运行时详细设计_V0.1/召回数据定义_V0.1.md:646) |
| ReadBounds.scope_ref | Ref<RecallRequest> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ReadBounds.scope_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 指向标准请求中的授权 Scope；同一执行；并非要求 B 暴露本地 Scope 对象；[召回数据定义_V0.1 L647](运行时详细设计_V0.1/召回数据定义_V0.1.md:647) |
| ReadBounds.snapshot_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ReadBounds.snapshot_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原能力明确提供的快照时点；未承诺统一快照时 null，不填本地 now 假充快照；[召回数据定义_V0.1 L648](运行时详细设计_V0.1/召回数据定义_V0.1.md:648) |
| ReadBounds.retrieval_space_ref | ContractRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ReadBounds.retrieval_space_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 长期检索空间；Working 为 null，长期与请求一致；[召回数据定义_V0.1 L649](运行时详细设计_V0.1/召回数据定义_V0.1.md:649) |

## CandidateValidationResult

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`CandidateValidationResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| CandidateValidationResult.validation_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.validation_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 本地资格校验记录标识；进入候选校验阶段检查点，不是 B 的快照对象；[召回数据定义_V0.1 L724](运行时详细设计_V0.1/召回数据定义_V0.1.md:724) |
| CandidateValidationResult.candidate_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.candidate_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次被检查的发现候选；必须存在于对应 SourceReadResult；[召回数据定义_V0.1 L725](运行时详细设计_V0.1/召回数据定义_V0.1.md:725) |
| CandidateValidationResult.source_result_ref | Ref<SourceReadResult> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.source_result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该候选的来源结果；同租户、同执行；[召回数据定义_V0.1 L726](运行时详细设计_V0.1/召回数据定义_V0.1.md:726) |
| CandidateValidationResult.source_id | LogicalSource / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.source_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次候选所属逻辑来源；与来源记录一致；用于区分 Working 与长期的附加条件；[召回数据定义_V0.1 L727](运行时详细设计_V0.1/召回数据定义_V0.1.md:727) |
| CandidateValidationResult.owner_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.owner_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次使用的 B 原事实读取契约；只消费，不定义 B 响应结构；[召回数据定义_V0.1 L728](运行时详细设计_V0.1/召回数据定义_V0.1.md:728) |
| CandidateValidationResult.owner_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.owner_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 返回的当前版本/资格证据；accepted、excluded 必须有；调用失败不伪造证据；[召回数据定义_V0.1 L729](运行时详细设计_V0.1/召回数据定义_V0.1.md:729) |
| CandidateValidationResult.authorization_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.authorization_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 候选在当前请求 Scope 内可访问的证明；accepted 必须有；不得以检索 filter 代替授权；[召回数据定义_V0.1 L730](运行时详细设计_V0.1/召回数据定义_V0.1.md:730) |
| CandidateValidationResult.projection_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.projection_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 对长期候选 Ready、模型/Schema 对应的证明；长期 accepted 必须有；Working 不要求 Projection；[召回数据定义_V0.1 L731](运行时详细设计_V0.1/召回数据定义_V0.1.md:731) |
| CandidateValidationResult.memory_id | ExternalId? / 是 | [RecallCandidate.memory_id](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:31) | 值可能已有，缺权威证明 | 新增目标字段；须由 B 本次资格响应确认后填写，候选自带值只作查找线索。 | 解析得到的 Memory 引用；accepted 必须有；未解析时 null；[召回数据定义_V0.1 L732](运行时详细设计_V0.1/召回数据定义_V0.1.md:732) |
| CandidateValidationResult.memory_version | Version? / 是 | [MemorySearchHit.source_revision](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:145) | 需 B 版本映射 | 新增 memory_version；source_revision 是 int 的发现信息，不能转 str 后即当 B 当前确认版本。 | B 本次确认的 Memory 版本；accepted 必须有；不补写后来的当前版本；[召回数据定义_V0.1 L733](运行时详细设计_V0.1/召回数据定义_V0.1.md:733) |
| CandidateValidationResult.memory_type | ExternalLabel? / 是 | [RecallCandidate.memory_type](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:43) | 值可能已有，缺权威证明 | 新增目标字段；须由 B 本次资格响应确认后填写，候选自带值只作查找线索。 | B 本次确认的 Memory Type；accepted 时符合本模式适用类型；[召回数据定义_V0.1 L734](运行时详细设计_V0.1/召回数据定义_V0.1.md:734) |
| CandidateValidationResult.decision | CandidateDecision / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.decision`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | accepted、excluded 或 unverifiable 的本地校验结论；accepted 只证明此时资格通过，不证明正文已加载；[召回数据定义_V0.1 L735](运行时详细设计_V0.1/召回数据定义_V0.1.md:735) |
| CandidateValidationResult.reason_codes | List<ReasonCode> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.reason_codes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 排除或无法确认的本地原因；accepted 为 []；excluded/unverifiable 必须解释；[召回数据定义_V0.1 L736](运行时详细设计_V0.1/召回数据定义_V0.1.md:736) |
| CandidateValidationResult.checked_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CandidateValidationResult.checked_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地结束这次检查的时间；Working 当前性/未过期及权限均以该时点的 B 证据判断；[召回数据定义_V0.1 L737](运行时详细设计_V0.1/召回数据定义_V0.1.md:737) |

## ContentLoadResult

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`ContentLoadResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ContentLoadResult.content_result_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.content_result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地加载链结果标识；候选、Trace、Provenance 通过它关联；[召回数据定义_V0.1 L774](运行时详细设计_V0.1/召回数据定义_V0.1.md:774) |
| ContentLoadResult.validation_ref | Ref<CandidateValidationResult> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.validation_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 正文读取前的本地资格结论；目标必须 accepted，且与 candidate_id、Memory 版本一致；[召回数据定义_V0.1 L775](运行时详细设计_V0.1/召回数据定义_V0.1.md:775) |
| ContentLoadResult.candidate_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.candidate_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此次加载对应的发现候选；先有 B 的授权和正式映射，再尝试加载；[召回数据定义_V0.1 L776](运行时详细设计_V0.1/召回数据定义_V0.1.md:776) |
| ContentLoadResult.expected_identity | ContentIdentity / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.expected_identity`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 已批准的目标内容身份与范围；不根据缓存自报内容修改期望身份；[召回数据定义_V0.1 L777](运行时详细设计_V0.1/召回数据定义_V0.1.md:777) |
| ContentLoadResult.representation_id | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准加载的内容表示；不把 Artifact 当 Original；[召回数据定义_V0.1 L778](运行时详细设计_V0.1/召回数据定义_V0.1.md:778) |
| ContentLoadResult.representation_type | ExternalLabel / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.representation_type`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原契约中的正式表示类别；不自定义 B 的类别；[召回数据定义_V0.1 L779](运行时详细设计_V0.1/召回数据定义_V0.1.md:779) |
| ContentLoadResult.external_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.external_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际使用的正式读取契约；不是 Recall 自建的 B/P2 API；[召回数据定义_V0.1 L780](运行时详细设计_V0.1/召回数据定义_V0.1.md:780) |
| ContentLoadResult.source_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.source_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已批准映射、版本、范围和期望 hash 的证据；不能只由缓存自身证明自身可信；[召回数据定义_V0.1 L781](运行时详细设计_V0.1/召回数据定义_V0.1.md:781) |
| ContentLoadResult.expected_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.expected_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按来源契约取得的可信期望摘要；适用于完整批准范围；[召回数据定义_V0.1 L782](运行时详细设计_V0.1/召回数据定义_V0.1.md:782) |
| ContentLoadResult.payload_ref | ProtectedRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.payload_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地已验证内容引用；validated=true 时必须有；失败时 null，不公开不可信字节；[召回数据定义_V0.1 L783](运行时详细设计_V0.1/召回数据定义_V0.1.md:783) |
| ContentLoadResult.actual_provider | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.actual_provider`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 有实际证据的内容读取来源；未证明实际后端时 null，不用配置的期望位置代填；[召回数据定义_V0.1 L784](运行时详细设计_V0.1/召回数据定义_V0.1.md:784) |
| ContentLoadResult.load_path | LoadPath / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.load_path`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次加载链最后采用/尝试的路径；回退成功记 canonical，保留缓存诊断；[召回数据定义_V0.1 L785](运行时详细设计_V0.1/召回数据定义_V0.1.md:785) |
| ContentLoadResult.content_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际可证明的读取版本；成功时等于 expected_identity.content_version；失败未知则 null；[召回数据定义_V0.1 L786](运行时详细设计_V0.1/召回数据定义_V0.1.md:786) |
| ContentLoadResult.actual_hash | Hash? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.actual_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按批准算法/范围验证的实际摘要；成功时与 expected_hash 的算法、值、编码及范围相符；[召回数据定义_V0.1 L787](运行时详细设计_V0.1/召回数据定义_V0.1.md:787) |
| ContentLoadResult.loaded_bytes | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.loaded_bytes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终一次采用/尝试的响应字节数；成功时等于批准片段长度；失败可为 0 或部分字节；[召回数据定义_V0.1 L788](运行时详细设计_V0.1/召回数据定义_V0.1.md:788) |
| ContentLoadResult.bytes_read_total | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.bytes_read_total`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本链关联调用已可靠结算的正文 byte 之和，含丢弃、重试和回退；>= loaded_bytes；有未知尝试时只是已知下界；共享 Working 批次不可跨结果累加；[召回数据定义_V0.1 L789](运行时详细设计_V0.1/召回数据定义_V0.1.md:789) |
| ContentLoadResult.read_attempt_ids | List<Id> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.read_attempt_ids`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本链及所采用 Working 内联读取的 RecallReadAttempt 身份；按发生顺序去重；引用同一 Execution.read_ledger，不重复扣内联正文；[召回数据定义_V0.1 L790](运行时详细设计_V0.1/召回数据定义_V0.1.md:790) |
| ContentLoadResult.budget_charged_bytes | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.budget_charged_bytes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 上述尝试的 charged_bytes 之和，含未知全额扣费；>= bytes_read_total；P-20 由账本原子控制，不靠本字段事后准入；[召回数据定义_V0.1 L791](运行时详细设计_V0.1/召回数据定义_V0.1.md:791) |
| ContentLoadResult.byte_accounting_complete | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.byte_accounting_complete`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否所有关联尝试均已知实际接收量；有 uncertain 则 false；保守扣费不填充实际统计；[召回数据定义_V0.1 L792](运行时详细设计_V0.1/召回数据定义_V0.1.md:792) |
| ContentLoadResult.validated | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.validated`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 版本、权限关联、范围和完整性均通过；true 才能进入可用候选；[召回数据定义_V0.1 L793](运行时详细设计_V0.1/召回数据定义_V0.1.md:793) |
| ContentLoadResult.failure_reason | ReasonCode? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.failure_reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终加载失败的本地原因；validated=true 时 null；false 时必须有原因；[召回数据定义_V0.1 L794](运行时详细设计_V0.1/召回数据定义_V0.1.md:794) |
| ContentLoadResult.observed_tier | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.observed_tier`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 证明的实际层级；无证据 null；不等于 desired_tier；[召回数据定义_V0.1 L795](运行时详细设计_V0.1/召回数据定义_V0.1.md:795) |
| ContentLoadResult.placement_generation | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.placement_generation`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际放置观察版本；不自行比较大小，也不要求等于动作提交前版本；[召回数据定义_V0.1 L796](运行时详细设计_V0.1/召回数据定义_V0.1.md:796) |
| ContentLoadResult.observed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该放置事实的来源观察时间；明确为 Timestamp，不沿用旧合并行的笼统 string；[召回数据定义_V0.1 L797](运行时详细设计_V0.1/召回数据定义_V0.1.md:797) |
| ContentLoadResult.producing_action_id | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.producing_action_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 有唯一关联证明的 C 动作；没有证明则 null，不能按时间接近推断；[召回数据定义_V0.1 L798](运行时详细设计_V0.1/召回数据定义_V0.1.md:798) |
| ContentLoadResult.action_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.action_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 动作与实际读取表示的关联证据；有 producing_action_id 时必须能解释关联；[召回数据定义_V0.1 L799](运行时详细设计_V0.1/召回数据定义_V0.1.md:799) |
| ContentLoadResult.latency_ms | uint? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.latency_ms`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 实际观测的整条加载链耗时；使用可验证的单调时钟；跨重启无法还原则 null，不从跨服务时间相减；[召回数据定义_V0.1 L800](运行时详细设计_V0.1/召回数据定义_V0.1.md:800) |
| ContentLoadResult.cache_diagnostics | List<CacheDiagnostic> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.cache_diagnostics`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 缓存尝试/内部回退的诊断，未使用则 []；已由 Provider 回退时不再重复触发同一次回退；[召回数据定义_V0.1 L801](运行时详细设计_V0.1/召回数据定义_V0.1.md:801) |
| ContentLoadResult.completed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContentLoadResult.completed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地加载链结束时间；与可空的放置 observed_at 分开；[召回数据定义_V0.1 L802](运行时详细设计_V0.1/召回数据定义_V0.1.md:802) |

## CacheDiagnostic

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`CacheDiagnostic`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| CacheDiagnostic.reason | ReasonCode / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CacheDiagnostic.reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 使用 PREWARM_FALLBACK 记录缓存路径问题；最终是否降级由权威回退结果决定；[召回数据定义_V0.1 L819](运行时详细设计_V0.1/召回数据定义_V0.1.md:819) |
| CacheDiagnostic.detail | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CacheDiagnostic.detail`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 经脱敏的 miss/过期/校验失败/释放等说明；不存正文、内部 key、token 或未授权 ID；[召回数据定义_V0.1 L820](运行时详细设计_V0.1/召回数据定义_V0.1.md:820) |
| CacheDiagnostic.evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CacheDiagnostic.evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已得到的调用/回退证据；证据未取得时 null，不编造；[召回数据定义_V0.1 L821](运行时详细设计_V0.1/召回数据定义_V0.1.md:821) |
| CacheDiagnostic.observed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `CacheDiagnostic.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地诊断观察时间；不是调层生效时间；[召回数据定义_V0.1 L822](运行时详细设计_V0.1/召回数据定义_V0.1.md:822) |

## RecallCandidate

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallCandidate`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallCandidate.candidate_id | Id / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.candidate_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前候选的本地标识；去重时选确定的代表 ID，并保留所有 source_refs；[召回数据定义_V0.1 L851](运行时详细设计_V0.1/召回数据定义_V0.1.md:851) |
| RecallCandidate.validation_ref | Ref<CandidateValidationResult> / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.validation_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 初次资格通过的本地记录；同租户同执行；正文已验证不替代 B 资格证明；[召回数据定义_V0.1 L852](运行时详细设计_V0.1/召回数据定义_V0.1.md:852) |
| RecallCandidate.owner_evidence_ref | EvidenceRef / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.owner_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 的版本、资格与权限事实；不是 Recall 新建的 Memory 权威；[召回数据定义_V0.1 L853](运行时详细设计_V0.1/召回数据定义_V0.1.md:853) |
| RecallCandidate.owner_contract_ref | ContractRef / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.owner_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 事实原契约；保留读取版本；[召回数据定义_V0.1 L854](运行时详细设计_V0.1/召回数据定义_V0.1.md:854) |
| RecallCandidate.content_result_ref | Ref<ContentLoadResult> / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.content_result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已验证的内容结果；目标必须 validated=true，身份与本候选一致；[召回数据定义_V0.1 L855](运行时详细设计_V0.1/召回数据定义_V0.1.md:855) |
| RecallCandidate.logical_sources | List<LogicalSource> / 是 | [RecallCandidate.source](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:39) | 需结构转换 | 新增 logical_sources:list，source_ranks:map、source_refs:list 配套；旧单个 source 不能表达去重后多来源证据。 | 来源集合；非空、去重；与 source_ranks 的键一致；[召回数据定义_V0.1 L856](运行时详细设计_V0.1/召回数据定义_V0.1.md:856) |
| RecallCandidate.source_ranks | Map<LogicalSource,uint> / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.source_ranks`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 校验后来源内重新编号的 rank；从 1 开始；同来源重复身份只取最佳 rank；[召回数据定义_V0.1 L857](运行时详细设计_V0.1/召回数据定义_V0.1.md:857) |
| RecallCandidate.source_refs | List<SourceReference> / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.source_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 保留去重前真实发现证据；非空；被合并候选不得丢失原引用；[召回数据定义_V0.1 L858](运行时详细设计_V0.1/召回数据定义_V0.1.md:858) |
| RecallCandidate.identity_key | ContentIdentity / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.identity_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地去重身份；与已验证内容 expected_identity 一致；[召回数据定义_V0.1 L859](运行时详细设计_V0.1/召回数据定义_V0.1.md:859) |
| RecallCandidate.memory_type | ExternalLabel / 是 | [RecallCandidate.memory_type](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:43) | 同名字段，需确认 | 新契约 RecallCandidate.memory_type 必填，由 B 确认；旧兼容 DTO 可空，不能直接作为已验证候选。 | B 已确认的 Memory Type；不根据读取位置推断 Working/Semantic；[召回数据定义_V0.1 L860](运行时详细设计_V0.1/召回数据定义_V0.1.md:860) |
| RecallCandidate.conflict_check | ConflictCheck / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.conflict_check`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地冲突证据充分性；unknown 不能当 known_none；[召回数据定义_V0.1 L861](运行时详细设计_V0.1/召回数据定义_V0.1.md:861) |
| RecallCandidate.conflict_group_ref | ExternalId? / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.conflict_group_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 已有冲突事实的只读引用；known_group 必须有；known_none 为 null；[召回数据定义_V0.1 L862](运行时详细设计_V0.1/召回数据定义_V0.1.md:862) |
| RecallCandidate.evidence_refs | List<EvidenceRef> / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.evidence_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 定义的证据/语义策略输入引用；不自造 confidence、衰减系数或“默认无冲突”；[召回数据定义_V0.1 L863](运行时详细设计_V0.1/召回数据定义_V0.1.md:863) |
| RecallCandidate.validated_at | Timestamp / 是 | [RecallCandidate](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `RecallCandidate.validated_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 候选初次事实和内容校验完成时间；不等于最终发出前复核时间；[召回数据定义_V0.1 L864](运行时详细设计_V0.1/召回数据定义_V0.1.md:864) |

## RankedRecallCandidates

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RankedRecallCandidates`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RankedRecallCandidates.ranked_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedRecallCandidates.ranked_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地排序输出标识；进入排序检查点；[召回数据定义_V0.1 L899](运行时详细设计_V0.1/召回数据定义_V0.1.md:899) |
| RankedRecallCandidates.entries | List<RankedEntry> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedRecallCandidates.entries`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 去重后稳定顺序，允许 []；candidate_id 唯一；final_rank 连续从 1 开始；[召回数据定义_V0.1 L900](运行时详细设计_V0.1/召回数据定义_V0.1.md:900) |
| RankedRecallCandidates.policy_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedRecallCandidates.policy_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定 Recall 融合策略；与请求一致；[召回数据定义_V0.1 L901](运行时详细设计_V0.1/召回数据定义_V0.1.md:901) |
| RankedRecallCandidates.excluded_refs | List<CandidateExclusion> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedRecallCandidates.excluded_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此阶段已知规则排除；缺失/冲突不安全另记 gap，不混作正常排除；[召回数据定义_V0.1 L902](运行时详细设计_V0.1/召回数据定义_V0.1.md:902) |

## RankedEntry

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RankedEntry`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RankedEntry.candidate_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedEntry.candidate_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已验证候选标识；不得新增来源中不存在或内容未验证的候选；[召回数据定义_V0.1 L917](运行时详细设计_V0.1/召回数据定义_V0.1.md:917) |
| RankedEntry.base_score | number / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedEntry.base_score`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 来源 rank 融合所得的 Recall 基础分；有限且非负；公式仍只在主文档定义；[召回数据定义_V0.1 L918](运行时详细设计_V0.1/召回数据定义_V0.1.md:918) |
| RankedEntry.final_rank | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedEntry.final_rank`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 消费 B 已批准语义策略后的最终名次；从 1 开始；[召回数据定义_V0.1 L919](运行时详细设计_V0.1/召回数据定义_V0.1.md:919) |
| RankedEntry.semantic_basis_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedEntry.semantic_basis_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 策略应用或明确无需调整的证据；未确认回退时不能自行造默认语义；[召回数据定义_V0.1 L920](运行时详细设计_V0.1/召回数据定义_V0.1.md:920) |
| RankedEntry.tie_break_key | TieBreakKey / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RankedEntry.tie_break_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 确定性并列排序依据；相同输入/策略输出可复现；[召回数据定义_V0.1 L921](运行时详细设计_V0.1/召回数据定义_V0.1.md:921) |

## TieBreakKey

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`TieBreakKey`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| TieBreakKey.source_priority | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `TieBreakKey.source_priority`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Working 优先记 0，否则长期记 1；多来源候选取最佳来源优先级；[召回数据定义_V0.1 L930](运行时详细设计_V0.1/召回数据定义_V0.1.md:930) |
| TieBreakKey.identity | ContentIdentity / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `TieBreakKey.identity`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 候选内容身份；对 memory_id、memory_version、content_ref、range 按主文档顺序稳定比较；[召回数据定义_V0.1 L931](运行时详细设计_V0.1/召回数据定义_V0.1.md:931) |

## RecallBudgetGroup

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallBudgetGroup`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallBudgetGroup.group_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallBudgetGroup.group_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地组标识；同输入、成员与策略稳定；不等于 B 冲突 ID；[召回数据定义_V0.1 L960](运行时详细设计_V0.1/召回数据定义_V0.1.md:960) |
| RecallBudgetGroup.candidate_ids | List<Id> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallBudgetGroup.candidate_ids`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 参与装配的已验证候选；非空去重；无冲突时单项成组；[召回数据定义_V0.1 L961](运行时详细设计_V0.1/召回数据定义_V0.1.md:961) |
| RecallBudgetGroup.conflict_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallBudgetGroup.conflict_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 完整冲突成员和提示依据；冲突组必须有；成员不全则整组隔离；[召回数据定义_V0.1 L962](运行时详细设计_V0.1/召回数据定义_V0.1.md:962) |
| RecallBudgetGroup.order_key | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallBudgetGroup.order_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 组内最前的 final_rank；从 1 开始，同键按稳定 group_id；[召回数据定义_V0.1 L963](运行时详细设计_V0.1/召回数据定义_V0.1.md:963) |
| RecallBudgetGroup.rendered_text_ref | ProtectedRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallBudgetGroup.rendered_text_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 同一模板渲染后的组内容，包含必要提示与证据；内容变化必须重算，不只保存裸正文；[召回数据定义_V0.1 L964](运行时详细设计_V0.1/召回数据定义_V0.1.md:964) |
| RecallBudgetGroup.token_count | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallBudgetGroup.token_count`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此组在固定 tokenizer/template 下的独立计数；仅作试装依据，不能将组计数简单相加当最终计数；[召回数据定义_V0.1 L965](运行时详细设计_V0.1/召回数据定义_V0.1.md:965) |

## ContextPack

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`ContextPack`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ContextPack.pack_id | Id / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.pack_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 分配的结果 ID；一个执行只冻结一个最终包；[召回数据定义_V0.1 L1002](运行时详细设计_V0.1/召回数据定义_V0.1.md:1002) |
| ContextPack.pack_version | Version / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.pack_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地不可变结果版本；重放不更新，内容变化应新执行；[召回数据定义_V0.1 L1003](运行时详细设计_V0.1/召回数据定义_V0.1.md:1003) |
| ContextPack.scope_ref | Ref<RecallRequest> / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.scope_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此结果的授权 Scope；只向当前授权上游暴露允许信息；[召回数据定义_V0.1 L1004](运行时详细设计_V0.1/召回数据定义_V0.1.md:1004) |
| ContextPack.retrieval_mode | RetrievalMode / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.retrieval_mode`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 继承 RecallRequest 中由 Recall 自己决定的实际执行模式；与内部标准请求一致；这是执行结果说明，不是外部入口参数；[召回数据定义_V0.1 L1005](运行时详细设计_V0.1/召回数据定义_V0.1.md:1005) |
| ContextPack.policy_version | Version / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.policy_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定 Recall 策略；与执行一致；[召回数据定义_V0.1 L1006](运行时详细设计_V0.1/召回数据定义_V0.1.md:1006) |
| ContextPack.source_coverage | SourceCoverage / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.source_coverage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终来源覆盖；与执行/Trace 一致；[召回数据定义_V0.1 L1007](运行时详细设计_V0.1/召回数据定义_V0.1.md:1007) |
| ContextPack.execution_state | TerminalState / 是 | [ContextPack.status](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:40) | 需结构/语义转换 | 新增正式四终态；原 status=ok/complete 默认值不能直接决定正式结果分类和可用性。 | 四种正式终态之一；按 A-02 判定；未可靠提交不发布；[召回数据定义_V0.1 L1008](运行时详细设计_V0.1/召回数据定义_V0.1.md:1008) |
| ContextPack.result_class | ResultClass / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.result_class`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 终态对应的结果分类；不能与 execution_state 矛盾；[召回数据定义_V0.1 L1009](运行时详细设计_V0.1/召回数据定义_V0.1.md:1009) |
| ContextPack.recall_availability | RecallAvailability / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.recall_availability`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可用、正常空或不可用；失败与正常空不可混淆；[召回数据定义_V0.1 L1010](运行时详细设计_V0.1/召回数据定义_V0.1.md:1010) |
| ContextPack.items | List<ContextItem> / 是 | [ContextPack.memories](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:30) | 需结构/语义转换 | 新增 list[ContextItem]；旧 memories:list[Memory] 需逐条转换成获准片段、版本、正文证明和最终复核证明，不能只重命名。 | 最终安全条目，按 rank_position 排列；available 时非空；empty/unavailable 时 []；[召回数据定义_V0.1 L1011](运行时详细设计_V0.1/召回数据定义_V0.1.md:1011) |
| ContextPack.groups | List<ContextGroup> / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.groups`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终预算原子组与 item 的对应；每个 item 只属于一组；冲突组不得拆散；[召回数据定义_V0.1 L1012](运行时详细设计_V0.1/召回数据定义_V0.1.md:1012) |
| ContextPack.rendered_context | string / 是 | [ContextPack.assembled_text](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:34) | 需结构/语义转换 | 新增 rendered_context；固定 template_version 后渲染。assembled_text 可作旧文本来源，不能无校验认定模板一致。 | 固定模板形成的最终实际注入文本；包含证据、冲突提示和分隔符；不是诊断元数据；[召回数据定义_V0.1 L1013](运行时详细设计_V0.1/召回数据定义_V0.1.md:1013) |
| ContextPack.token_budget | uint / 是 | [ContextPack.budget_tokens](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:32) | 需结构/语义转换 | 新增 token_budget；从已标准化请求取得，正数及上限校验后可沿用 budget_tokens 的值。 | 本次明确预算；等于请求值，>0 且不超 P-03；[召回数据定义_V0.1 L1014](运行时详细设计_V0.1/召回数据定义_V0.1.md:1014) |
| ContextPack.used_tokens | uint / 是 | [ContextPack.total_tokens](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:31) | 需结构/语义转换 | 新增 used_tokens；按固定 tokenizer 对最终 rendered_context 精确计数。已检查 MockContextPackBuilder 使用字符估算，不能直接复制 total_tokens。 | 对 rendered_context 的最终精确计数；<= token_budget，不用 items.token_count 之和替代；[召回数据定义_V0.1 L1015](运行时详细设计_V0.1/召回数据定义_V0.1.md:1015) |
| ContextPack.tokenizer_id | Id / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.tokenizer_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终计数器标识；与请求绑定一致；[召回数据定义_V0.1 L1016](运行时详细设计_V0.1/召回数据定义_V0.1.md:1016) |
| ContextPack.tokenizer_version | Version / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.tokenizer_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终计数器版本；与请求绑定一致；[召回数据定义_V0.1 L1017](运行时详细设计_V0.1/召回数据定义_V0.1.md:1017) |
| ContextPack.template_version | Version / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.template_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际渲染模板版本；与请求绑定一致；[召回数据定义_V0.1 L1018](运行时详细设计_V0.1/召回数据定义_V0.1.md:1018) |
| ContextPack.truncated | bool / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.truncated`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否发生合法预算裁剪；不表示 TopK 有界搜索不完整；[召回数据定义_V0.1 L1019](运行时详细设计_V0.1/召回数据定义_V0.1.md:1019) |
| ContextPack.truncation_reasons | List<ReasonCode> / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.truncation_reasons`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地裁剪说明；无裁剪为 []；正常裁剪用 BUDGET_TRUNCATED；[召回数据定义_V0.1 L1020](运行时详细设计_V0.1/召回数据定义_V0.1.md:1020) |
| ContextPack.excluded_summary | ExcludedSummary / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.excluded_summary`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可安全对外解释的排除摘要；不泄露未授权对象是否存在；[召回数据定义_V0.1 L1021](运行时详细设计_V0.1/召回数据定义_V0.1.md:1021) |
| ContextPack.missing_sources | List<LogicalSource> / 是 | [ContextPack.missing_sources](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:43) | 需结构/语义转换 | 新增 missing_sources；把旧来源标签映射为 working/long_term，保留原失败原因且不包括未请求来源。 | 必需且最终 partial/unavailable 的来源；去重，不包含未请求来源或 prewarm；[召回数据定义_V0.1 L1022](运行时详细设计_V0.1/召回数据定义_V0.1.md:1022) |
| ContextPack.degradation_reasons | List<ReasonCode> / 是 | [ContextPack.degradation_reasons](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:44) | 需结构/语义转换 | 新增 list[ReasonCode]；旧 dict[str,str] 要规范化、去重和脱敏，不能原样赋值。 | 脱敏后的未消除缺口代码；不携带内部 affected_ref；[召回数据定义_V0.1 L1023](运行时详细设计_V0.1/召回数据定义_V0.1.md:1023) |
| ContextPack.fatal_reason | ReasonCode? / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.fatal_reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 请求失败的最终原因；failed 必须有；其他终态 null；[召回数据定义_V0.1 L1024](运行时详细设计_V0.1/召回数据定义_V0.1.md:1024) |
| ContextPack.validated_at | Timestamp? / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.validated_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 必要授权/事实复核完成的快照时间；可用/正常空结果必须有；失败前未完成复核则 null；[召回数据定义_V0.1 L1025](运行时详细设计_V0.1/召回数据定义_V0.1.md:1025) |
| ContextPack.finalized_at | Timestamp / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.finalized_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 不可变收尾记录的时间；不凭此字段自身证明持久化成功；[召回数据定义_V0.1 L1026](运行时详细设计_V0.1/召回数据定义_V0.1.md:1026) |
| ContextPack.trace_ref | Ref<RecallTrace> / 是 | [ContextPack](../../../AgentJYS-main/src/aether_agent_memory/context/models.py:28) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextPack.trace_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次最小安全 Trace；与执行、结果和本地事件一致提交；[召回数据定义_V0.1 L1027](运行时详细设计_V0.1/召回数据定义_V0.1.md:1027) |

## ContextGroup

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`ContextGroup`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ContextGroup.group_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextGroup.group_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应本地 RecallBudgetGroup；不能根据 B conflict_group_ref 直接当本地组 ID；[召回数据定义_V0.1 L1042](运行时详细设计_V0.1/召回数据定义_V0.1.md:1042) |
| ContextGroup.item_ids | List<Id> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextGroup.item_ids`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本组所有输出条目；非空去重，均属于同一 Pack；[召回数据定义_V0.1 L1043](运行时详细设计_V0.1/召回数据定义_V0.1.md:1043) |
| ContextGroup.conflict_notice | string? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextGroup.conflict_notice`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按 B 证据呈现的必要冲突提示；冲突组必须有非空提示；无冲突为 null；[召回数据定义_V0.1 L1044](运行时详细设计_V0.1/召回数据定义_V0.1.md:1044) |
| ContextGroup.conflict_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextGroup.conflict_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 完整成员与提示的依据；与 BudgetGroup 一致；[召回数据定义_V0.1 L1045](运行时详细设计_V0.1/召回数据定义_V0.1.md:1045) |

## ContextItem

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`ContextItem`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ContextItem.item_id | Id / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.item_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Pack 内稳定条目标识；在同一 Pack 唯一；[召回数据定义_V0.1 L1054](运行时详细设计_V0.1/召回数据定义_V0.1.md:1054) |
| ContextItem.memory_id | ExternalId / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.memory_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 已确认的 Memory 引用；只读引用，不创建新 Memory；[召回数据定义_V0.1 L1055](运行时详细设计_V0.1/召回数据定义_V0.1.md:1055) |
| ContextItem.memory_version | Version / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.memory_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终复核通过的 Memory 版本；不能把旧候选悄悄替换成新版本正文；[召回数据定义_V0.1 L1056](运行时详细设计_V0.1/召回数据定义_V0.1.md:1056) |
| ContextItem.memory_type | ExternalLabel / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.memory_type`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 已确认的 Memory Type；不从存储层级推导；[召回数据定义_V0.1 L1057](运行时详细设计_V0.1/召回数据定义_V0.1.md:1057) |
| ContextItem.content_ref | ExternalId / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.content_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准的正式内容引用；可安全向当前调用方提供；[召回数据定义_V0.1 L1058](运行时详细设计_V0.1/召回数据定义_V0.1.md:1058) |
| ContextItem.content_version | Version / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次已验证内容版本；与 ContentLoadResult 一致；[召回数据定义_V0.1 L1059](运行时详细设计_V0.1/召回数据定义_V0.1.md:1059) |
| ContextItem.representation_id | ExternalId / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际采用的批准表示；保留 Original / Artifact 的原契约身份；[召回数据定义_V0.1 L1060](运行时详细设计_V0.1/召回数据定义_V0.1.md:1060) |
| ContextItem.passage_range | ByteRange? / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.passage_range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本条选择的有证明片段范围；null 表示完整获批片段，绝不泛指整份原始 Memory；[召回数据定义_V0.1 L1061](运行时详细设计_V0.1/召回数据定义_V0.1.md:1061) |
| ContextItem.text | string / 是 | [ContextContent.text](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:76) | 值可用，对象语义不同 | 新增输出 ContextItem.text；只能取获准且已验证的片段。context_store.ContextItem 是存储目录对象，不能因同名而直接作为输出项。 | 获准片段转换得到的输出文本；非空；转换和范围映射可追踪，不任意逐字硬截断；[召回数据定义_V0.1 L1062](运行时详细设计_V0.1/召回数据定义_V0.1.md:1062) |
| ContextItem.text_hash | Hash / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.text_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本条 text 的 UTF-8 全文摘要；本地 SHA-256、range=null；不冒充 Provider 原字节 hash；[召回数据定义_V0.1 L1063](运行时详细设计_V0.1/召回数据定义_V0.1.md:1063) |
| ContextItem.evidence_refs | List<EvidenceRef> / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.evidence_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按 B 证据策略保留的引用；只向授权上游暴露可访问证据；[召回数据定义_V0.1 L1064](运行时详细设计_V0.1/召回数据定义_V0.1.md:1064) |
| ContextItem.provenance | ContextProvenance / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.provenance`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 内容、批准范围和最终复核证明；缺安全关键证明不得输出；[召回数据定义_V0.1 L1065](运行时详细设计_V0.1/召回数据定义_V0.1.md:1065) |
| ContextItem.conflict_group_ref | ExternalId? / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.conflict_group_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 原有冲突事实引用；有冲突时与所在 ContextGroup 证据一致；[召回数据定义_V0.1 L1066](运行时详细设计_V0.1/召回数据定义_V0.1.md:1066) |
| ContextItem.source_refs | List<SourceReference> / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.source_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 一个或多个真实发现来源；不伪造“综合 representation”；内容加载事实由 provenance 关联；[召回数据定义_V0.1 L1067](运行时详细设计_V0.1/召回数据定义_V0.1.md:1067) |
| ContextItem.token_count | uint / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.token_count`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | text 在固定 tokenizer 下的独立计数；不含整包共用模板开销，不能与 used_tokens 混用；[召回数据定义_V0.1 L1068](运行时详细设计_V0.1/召回数据定义_V0.1.md:1068) |
| ContextItem.rank_position | uint / 是 | [ContextItem](../../../AgentJYS-main/src/aether_agent_memory/context_store/models.py:94) | 同名类不等于文档对象 | 在 `memory/recall_contracts.py` 新增 `ContextItem.rank_position`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终输出位置；从 1 连续编号；不是原 Provider rank；[召回数据定义_V0.1 L1069](运行时详细设计_V0.1/召回数据定义_V0.1.md:1069) |

## ContextProvenance

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`ContextProvenance`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ContextProvenance.owner_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextProvenance.owner_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 资格/映射原契约；不定义对方对象；[召回数据定义_V0.1 L1078](运行时详细设计_V0.1/召回数据定义_V0.1.md:1078) |
| ContextProvenance.owner_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextProvenance.owner_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 的内容资格与版本证据；与本条 Memory/版本对应；[召回数据定义_V0.1 L1079](运行时详细设计_V0.1/召回数据定义_V0.1.md:1079) |
| ContextProvenance.content_result_ref | Ref<ContentLoadResult> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextProvenance.content_result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已验证加载结果；同租户同执行，validated=true；[召回数据定义_V0.1 L1080](运行时详细设计_V0.1/召回数据定义_V0.1.md:1080) |
| ContextProvenance.approved_range | ByteRange / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextProvenance.approved_range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 确定的完整获批片段范围；passage_range=null 也必须能从这里获知范围；[召回数据定义_V0.1 L1081](运行时详细设计_V0.1/召回数据定义_V0.1.md:1081) |
| ContextProvenance.range_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextProvenance.range_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原字节到输出文本/范围的映射证明；不用字符数代替字节范围；[召回数据定义_V0.1 L1082](运行时详细设计_V0.1/召回数据定义_V0.1.md:1082) |
| ContextProvenance.final_validation_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextProvenance.final_validation_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 发出前的授权、版本与有效性复核证据；指向本次最终复核，不只复用初次 CandidateAccepted；[召回数据定义_V0.1 L1083](运行时详细设计_V0.1/召回数据定义_V0.1.md:1083) |
| ContextProvenance.validated_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextProvenance.validated_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此条最终复核时间；不晚于 Pack 的最终复核完成时间；[召回数据定义_V0.1 L1084](运行时详细设计_V0.1/召回数据定义_V0.1.md:1084) |

## ExcludedSummary

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`ExcludedSummary`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ExcludedSummary.count | uint? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ExcludedSummary.count`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 允许公开的已排除候选去重数量；可能泄露未授权对象存在性时 null，不伪装成 0；[召回数据定义_V0.1 L1093](运行时详细设计_V0.1/召回数据定义_V0.1.md:1093) |
| ExcludedSummary.reason_codes | List<ReasonCode> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ExcludedSummary.reason_codes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 允许公开的粗粒度原因集合；去重、脱敏；无内部对象引用或其他租户标识；[召回数据定义_V0.1 L1094](运行时详细设计_V0.1/召回数据定义_V0.1.md:1094) |

## RecallTrace

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallTrace`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallTrace.trace_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.trace_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地业务追踪标识；不等于共享 OTel trace_id 的新 schema；[召回数据定义_V0.1 L1179](运行时详细设计_V0.1/召回数据定义_V0.1.md:1179) |
| RecallTrace.stage_traces | List<StageTrace> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.stage_traces`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际阶段尝试及跳过说明；同阶段多次尝试可区分，不伪造未执行步骤；[召回数据定义_V0.1 L1180](运行时详细设计_V0.1/召回数据定义_V0.1.md:1180) |
| RecallTrace.checkpoint_refs | Map<Stage,Ref<RecallCheckpoint>> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.checkpoint_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 采用的可靠输出；与 Execution 最终快照一致；[召回数据定义_V0.1 L1181](运行时详细设计_V0.1/召回数据定义_V0.1.md:1181) |
| RecallTrace.version_bindings | List<VersionBinding> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.version_bindings`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 模型/Schema/语义策略/模板/Recall policy 的实际版本；只记录实际采用值，无调用则不制造模型版本；[召回数据定义_V0.1 L1182](运行时详细设计_V0.1/召回数据定义_V0.1.md:1182) |
| RecallTrace.candidate_decisions | List<CandidateExclusion> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.candidate_decisions`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已知保留边界及排除证据；不把失败未知写成已知正常排除；[召回数据定义_V0.1 L1183](运行时详细设计_V0.1/召回数据定义_V0.1.md:1183) |
| RecallTrace.validation_refs | List<Ref<CandidateValidationResult>> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.validation_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 候选校验阶段的历史结论；可含 accepted、excluded、unverifiable；不根据最终输出倒写初次事实；[召回数据定义_V0.1 L1184](运行时详细设计_V0.1/召回数据定义_V0.1.md:1184) |
| RecallTrace.content_result_refs | List<Ref<ContentLoadResult>> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.content_result_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 内容版本、范围和 hash 校验记录；不在 Trace 内复制正文；[召回数据定义_V0.1 L1185](运行时详细设计_V0.1/召回数据定义_V0.1.md:1185) |
| RecallTrace.source_coverage | SourceCoverage / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.source_coverage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终覆盖；与执行和 Pack 一致；[召回数据定义_V0.1 L1186](运行时详细设计_V0.1/召回数据定义_V0.1.md:1186) |
| RecallTrace.source_result_refs | Map<LogicalSource,Ref<SourceReadResult>> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.source_result_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终采用的两来源记录；恰含 working、long_term；与 Execution 一致，包含提前失败补齐记录；[召回数据定义_V0.1 L1187](运行时详细设计_V0.1/召回数据定义_V0.1.md:1187) |
| RecallTrace.decision_inputs | DecisionInputs / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.decision_inputs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A-02 归并所用的 U/G/D/E/P/B/T；与最终组数及提交事实一致；[召回数据定义_V0.1 L1188](运行时详细设计_V0.1/召回数据定义_V0.1.md:1188) |
| RecallTrace.result_ref | Ref<ContextPack> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 唯一结果；通过稳定引用关联，不递归嵌套 Pack；[召回数据定义_V0.1 L1189](运行时详细设计_V0.1/召回数据定义_V0.1.md:1189) |
| RecallTrace.result_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.result_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 结果完整性摘要；与 Execution 一致；[召回数据定义_V0.1 L1190](运行时详细设计_V0.1/召回数据定义_V0.1.md:1190) |
| RecallTrace.reason_codes | List<ReasonCode> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.reason_codes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次可解释的本地原因；区分诊断与真正缺口，不凭代码存在与否单独推导降级；[召回数据定义_V0.1 L1191](运行时详细设计_V0.1/召回数据定义_V0.1.md:1191) |
| RecallTrace.safety_evidence_refs | List<EvidenceRef> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.safety_evidence_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 授权、版本和可信来源的证明；可用结果必须足以证明安全；失败可记录已得证据与缺口；[召回数据定义_V0.1 L1192](运行时详细设计_V0.1/召回数据定义_V0.1.md:1192) |
| RecallTrace.completed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallTrace.completed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最小 Trace 的收尾记录时间；与终态一致提交，不伪造提交成功；[召回数据定义_V0.1 L1193](运行时详细设计_V0.1/召回数据定义_V0.1.md:1193) |

## StageTrace

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`StageTrace`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| StageTrace.stage | Stage / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `StageTrace.stage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 所属 RUNNING 阶段；不新增业务状态；[召回数据定义_V0.1 L1208](运行时详细设计_V0.1/召回数据定义_V0.1.md:1208) |
| StageTrace.attempt | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `StageTrace.attempt`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地阶段尝试序号；从 1 开始；[召回数据定义_V0.1 L1209](运行时详细设计_V0.1/召回数据定义_V0.1.md:1209) |
| StageTrace.outcome | StageOutcome / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `StageTrace.outcome`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次尝试的观察标签；skipped 必须有原因说明；[召回数据定义_V0.1 L1210](运行时详细设计_V0.1/召回数据定义_V0.1.md:1210) |
| StageTrace.started_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `StageTrace.started_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际开始时间；未执行的 skipped 可 null；[召回数据定义_V0.1 L1211](运行时详细设计_V0.1/召回数据定义_V0.1.md:1211) |
| StageTrace.ended_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `StageTrace.ended_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际结束时间；未结束时 null；不能虚构运行耗时；[召回数据定义_V0.1 L1212](运行时详细设计_V0.1/召回数据定义_V0.1.md:1212) |
| StageTrace.checkpoint_ref | Ref<RecallCheckpoint>? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `StageTrace.checkpoint_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此尝试确认的输出；没有可靠输出则 null；[召回数据定义_V0.1 L1213](运行时详细设计_V0.1/召回数据定义_V0.1.md:1213) |
| StageTrace.reason_codes | List<ReasonCode> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `StageTrace.reason_codes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可映射的本地原因；非故障的跳过可 []；[召回数据定义_V0.1 L1214](运行时详细设计_V0.1/召回数据定义_V0.1.md:1214) |
| StageTrace.explanation | string? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `StageTrace.explanation`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 无需调用、未达阶段等脱敏说明；skipped 时必须可解释，例如 working_only 不需向量；[召回数据定义_V0.1 L1215](运行时详细设计_V0.1/召回数据定义_V0.1.md:1215) |

## VersionBinding

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`VersionBinding`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| VersionBinding.role | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VersionBinding.role`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | model、embedding_schema、projection_schema、semantic_policy、tokenizer、template、recall_policy 或 external_contract；仅允许此处角色；不是外部新增枚举；[召回数据定义_V0.1 L1224](运行时详细设计_V0.1/召回数据定义_V0.1.md:1224) |
| VersionBinding.reference | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VersionBinding.reference`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 被绑定的对象/配置/契约引用；来源可追溯；[召回数据定义_V0.1 L1225](运行时详细设计_V0.1/召回数据定义_V0.1.md:1225) |
| VersionBinding.version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `VersionBinding.version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次实际采用的版本；不采用后来默认版本补写；[召回数据定义_V0.1 L1226](运行时详细设计_V0.1/召回数据定义_V0.1.md:1226) |

## DecisionInputs

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`DecisionInputs`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| DecisionInputs.safety_blocked | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `DecisionInputs.safety_blocked`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | U：请求级安全证明失败；true 时不能发布内容；[召回数据定义_V0.1 L1235](运行时详细设计_V0.1/召回数据定义_V0.1.md:1235) |
| DecisionInputs.unresolved_gap | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `DecisionInputs.unresolved_gap`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | G：仍有未消除的必需结果缺口；不由正常 TopK 或 cache miss 单独置 true；[召回数据定义_V0.1 L1236](运行时详细设计_V0.1/召回数据定义_V0.1.md:1236) |
| DecisionInputs.required_sources_confirmed | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `DecisionInputs.required_sources_confirmed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | D：本模式必需来源都已完成；由最终覆盖与证据计算；[召回数据定义_V0.1 L1237](运行时详细设计_V0.1/召回数据定义_V0.1.md:1237) |
| DecisionInputs.eligible_count | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `DecisionInputs.eligible_count`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | E：最终复核后、预算前的合格组数；单位是组，不是条目数；[召回数据定义_V0.1 L1238](运行时详细设计_V0.1/召回数据定义_V0.1.md:1238) |
| DecisionInputs.packed_count | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `DecisionInputs.packed_count`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P：最终实际装入的组数；<= eligible_count，等于 Pack.groups.length；[召回数据定义_V0.1 L1239](运行时详细设计_V0.1/召回数据定义_V0.1.md:1239) |
| DecisionInputs.budget_blocked | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `DecisionInputs.budget_blocked`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B：有合格组但全部装不下；true 必须 E>0 且 P=0；[召回数据定义_V0.1 L1240](运行时详细设计_V0.1/召回数据定义_V0.1.md:1240) |
| DecisionInputs.durable_finalization | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `DecisionInputs.durable_finalization`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | T：结果/终态/最小 Trace/本地 Outbox 已可靠一致提交；只能由实际提交证明确认，记录中的 true 不是自证；[召回数据定义_V0.1 L1241](运行时详细设计_V0.1/召回数据定义_V0.1.md:1241) |

## RecallAccessObservation

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`RecallAccessObservation`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecallAccessObservation.event_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.event_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地稳定事实 ID；由租户、执行、观察类型、主体、stage_observation_id 和适用 delivery_id 确定；[召回数据定义_V0.1 L1274](运行时详细设计_V0.1/召回数据定义_V0.1.md:1274) |
| RecallAccessObservation.observation_type | ObservationType / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.observation_type`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 七类 Recall 本地观察；不当作 RF 共享 event_type；[召回数据定义_V0.1 L1275](运行时详细设计_V0.1/召回数据定义_V0.1.md:1275) |
| RecallAccessObservation.external_contract_ref | ContractRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.external_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 形成观察时已知的共享映射契约；未确认可 null；真正投递以 Outbox 的已确认映射为准；[召回数据定义_V0.1 L1276](运行时详细设计_V0.1/召回数据定义_V0.1.md:1276) |
| RecallAccessObservation.external_evidence_refs | List<EvidenceRef> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.external_evidence_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本事件使用的外部事实证据；不复制原始凭证/完整响应进入事件；[召回数据定义_V0.1 L1277](运行时详细设计_V0.1/召回数据定义_V0.1.md:1277) |
| RecallAccessObservation.span_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.span_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可用技术 span 关联；缺失为 null；[召回数据定义_V0.1 L1278](运行时详细设计_V0.1/召回数据定义_V0.1.md:1278) |
| RecallAccessObservation.scope_ref | Ref<RecallRequest> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.scope_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 标准请求的授权 Scope；投递前按批准共享契约做必要脱敏；[召回数据定义_V0.1 L1279](运行时详细设计_V0.1/召回数据定义_V0.1.md:1279) |
| RecallAccessObservation.principal_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.principal_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次执行的身份引用；不是认证 token；[召回数据定义_V0.1 L1280](运行时详细设计_V0.1/召回数据定义_V0.1.md:1280) |
| RecallAccessObservation.memory_id | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.memory_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 已确认的 Memory 引用；CandidateAccepted/加载/Context 条目观察必须有；未解析 SearchHit 和 RecallCompleted 可 null；[召回数据定义_V0.1 L1281](运行时详细设计_V0.1/召回数据定义_V0.1.md:1281) |
| RecallAccessObservation.memory_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.memory_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应时点的 B 版本；与 memory_id 的证据成对，不补写最新版本；[召回数据定义_V0.1 L1282](运行时详细设计_V0.1/召回数据定义_V0.1.md:1282) |
| RecallAccessObservation.representation_id | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 真实来源或内容表示；加载事件必须有；SearchHit 尚无证据时 null，不假充物理归因；[召回数据定义_V0.1 L1283](运行时详细设计_V0.1/召回数据定义_V0.1.md:1283) |
| RecallAccessObservation.representation_type | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.representation_type`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原契约中的表示类别；不能按期望配置或 memory_id 生成；[召回数据定义_V0.1 L1284](运行时详细设计_V0.1/召回数据定义_V0.1.md:1284) |
| RecallAccessObservation.candidate_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.candidate_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地发现/候选标识；发现、初次校验和加载事件必须有；[召回数据定义_V0.1 L1285](运行时详细设计_V0.1/召回数据定义_V0.1.md:1285) |
| RecallAccessObservation.content_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.content_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 正式内容引用；加载与 Context 条目事件必须有；[召回数据定义_V0.1 L1286](运行时详细设计_V0.1/召回数据定义_V0.1.md:1286) |
| RecallAccessObservation.content_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际已验证内容版本；加载与 Context 条目事件必须有；[召回数据定义_V0.1 L1287](运行时详细设计_V0.1/召回数据定义_V0.1.md:1287) |
| RecallAccessObservation.pack_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.pack_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应已冻结 Pack；ContextSelected、ContextEmitted 必须有；[召回数据定义_V0.1 L1288](运行时详细设计_V0.1/召回数据定义_V0.1.md:1288) |
| RecallAccessObservation.pack_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.pack_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该 Pack 的不可变版本；与 pack_id、item_id 成组；[召回数据定义_V0.1 L1289](运行时详细设计_V0.1/召回数据定义_V0.1.md:1289) |
| RecallAccessObservation.item_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.item_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Context 条目标识；与实际 Pack.items 一致；[召回数据定义_V0.1 L1290](运行时详细设计_V0.1/召回数据定义_V0.1.md:1290) |
| RecallAccessObservation.source_id | LogicalSource? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.source_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该次发现/加载的逻辑来源；融合后的 Context 不伪造单一来源，可 null；[召回数据定义_V0.1 L1291](运行时详细设计_V0.1/召回数据定义_V0.1.md:1291) |
| RecallAccessObservation.source_provider | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.source_provider`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该次实际观测的来源 Provider；无实际来源证明则 null；不能用期望 tier 反推；[召回数据定义_V0.1 L1292](运行时详细设计_V0.1/召回数据定义_V0.1.md:1292) |
| RecallAccessObservation.load_path | LoadPath? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.load_path`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应真实加载的路径；非加载观察不猜测加载路径；[召回数据定义_V0.1 L1293](运行时详细设计_V0.1/召回数据定义_V0.1.md:1293) |
| RecallAccessObservation.observed_tier | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.observed_tier`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 实际层级；无证据 null；[召回数据定义_V0.1 L1294](运行时详细设计_V0.1/召回数据定义_V0.1.md:1294) |
| RecallAccessObservation.placement_generation | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.placement_generation`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应物理观察版本；不要求等于动作提交前的版本；[召回数据定义_V0.1 L1295](运行时详细设计_V0.1/召回数据定义_V0.1.md:1295) |
| RecallAccessObservation.placement_observed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.placement_observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该放置事实的来源时间；不以本地记录时间代替；[召回数据定义_V0.1 L1296](运行时详细设计_V0.1/召回数据定义_V0.1.md:1296) |
| RecallAccessObservation.action_id | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.action_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 精确关联到的 C 动作；仅有动作关联证明才填写；[召回数据定义_V0.1 L1297](运行时详细设计_V0.1/召回数据定义_V0.1.md:1297) |
| RecallAccessObservation.action_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.action_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该动作与本次实际读取的关联证据；action_id 有值时必须有；[召回数据定义_V0.1 L1298](运行时详细设计_V0.1/召回数据定义_V0.1.md:1298) |
| RecallAccessObservation.occurred_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.occurred_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次访问事实实际发生的时间；不是 Memory 业务发生时间；补证不能任意改成当前时间；[召回数据定义_V0.1 L1299](运行时详细设计_V0.1/召回数据定义_V0.1.md:1299) |
| RecallAccessObservation.observed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 观测到本次事实的时间；与来源 placement_observed_at 区分；[召回数据定义_V0.1 L1300](运行时详细设计_V0.1/召回数据定义_V0.1.md:1300) |
| RecallAccessObservation.recorded_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.recorded_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地记录该事实的时间；created_at 等于本次首次 recorded_at，重投不改变；[召回数据定义_V0.1 L1301](运行时详细设计_V0.1/召回数据定义_V0.1.md:1301) |
| RecallAccessObservation.event_sequence | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.event_sequence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 单执行内可靠分配的事件序号；从 1 开始唯一递增，不承诺跨执行全序；[召回数据定义_V0.1 L1302](运行时详细设计_V0.1/召回数据定义_V0.1.md:1302) |
| RecallAccessObservation.stage_observation_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.stage_observation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该阶段已确认事实的稳定关联；同事实重试复用，不每次重试重新生成；[召回数据定义_V0.1 L1303](运行时详细设计_V0.1/召回数据定义_V0.1.md:1303) |
| RecallAccessObservation.delivery_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.delivery_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 一次真实交付尝试的稳定关联；ContextEmitted 必须有；同次补证/重投复用，真实再次交付使用新 ID；[召回数据定义_V0.1 L1304](运行时详细设计_V0.1/召回数据定义_V0.1.md:1304) |
| RecallAccessObservation.retrieved | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.retrieved`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该主体此前已被来源发现；按本事件时点，不倒写未来事实；[召回数据定义_V0.1 L1305](运行时详细设计_V0.1/召回数据定义_V0.1.md:1305) |
| RecallAccessObservation.validated | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.validated`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该主体已通过对应时点 B 校验；历史 true 不表示以后永远有效；[召回数据定义_V0.1 L1306](运行时详细设计_V0.1/召回数据定义_V0.1.md:1306) |
| RecallAccessObservation.loaded | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.loaded`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已取得该版本可信正文；SearchHit/CandidateAccepted 不提前置 true；[召回数据定义_V0.1 L1307](运行时详细设计_V0.1/召回数据定义_V0.1.md:1307) |
| RecallAccessObservation.selected | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.selected`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终复核后已选入待提交 Pack；ContextSelected 首次可 true；[召回数据定义_V0.1 L1308](运行时详细设计_V0.1/召回数据定义_V0.1.md:1308) |
| RecallAccessObservation.used_in_context | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.used_in_context`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 属于 A 已交给传输层的 Context；ContextEmitted 才可 true；[召回数据定义_V0.1 L1309](运行时详细设计_V0.1/召回数据定义_V0.1.md:1309) |
| RecallAccessObservation.agent_received | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.agent_received`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已取得的实际调用方接收证据；无证据 null，不能默认 false；[召回数据定义_V0.1 L1310](运行时详细设计_V0.1/召回数据定义_V0.1.md:1310) |
| RecallAccessObservation.used_in_model_request | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.used_in_model_request`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已取得的实际调用方模型调用使用证据；无证据 null；不等于模型语义采纳；[召回数据定义_V0.1 L1311](运行时详细设计_V0.1/召回数据定义_V0.1.md:1311) |
| RecallAccessObservation.latency_ms | uint? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.latency_ms`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前明确操作口径的实测耗时；未测量为 null，不默认 0；[召回数据定义_V0.1 L1312](运行时详细设计_V0.1/召回数据定义_V0.1.md:1312) |
| RecallAccessObservation.bytes | uint? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.bytes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前明确操作口径的字节数；非字节操作为 null，不默认 0；[召回数据定义_V0.1 L1313](运行时详细设计_V0.1/召回数据定义_V0.1.md:1313) |
| RecallAccessObservation.policy_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.policy_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次采用的 Recall 策略；受理后的观察必须与请求一致；[召回数据定义_V0.1 L1314](运行时详细设计_V0.1/召回数据定义_V0.1.md:1314) |
| RecallAccessObservation.model_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.model_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 相关向量/模型版本；非向量观察可 null，不制造 Working 模型版本；[召回数据定义_V0.1 L1315](运行时详细设计_V0.1/召回数据定义_V0.1.md:1315) |
| RecallAccessObservation.source_schema_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.source_schema_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 相关来源契约版本；不用本地 schema_version 替代；[召回数据定义_V0.1 L1316](运行时详细设计_V0.1/召回数据定义_V0.1.md:1316) |
| RecallAccessObservation.execution_state | TerminalState? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.execution_state`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已提交最终结果状态；RecallCompleted 必填非 null；其他事件仅在确有结果时填写；[召回数据定义_V0.1 L1317](运行时详细设计_V0.1/召回数据定义_V0.1.md:1317) |
| RecallAccessObservation.source_coverage | SourceCoverage? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.source_coverage`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应最终来源汇总；RecallCompleted 必填非 null；不是按单条布尔推断；[召回数据定义_V0.1 L1318](运行时详细设计_V0.1/召回数据定义_V0.1.md:1318) |
| RecallAccessObservation.reason_codes | List<ReasonCode> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.reason_codes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地诊断/排除/终态原因；无原因 []，对外脱敏；[召回数据定义_V0.1 L1319](运行时详细设计_V0.1/召回数据定义_V0.1.md:1319) |
| RecallAccessObservation.parent_event_ids | List<Id> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.parent_event_ids`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际前序观察 ID；同租户可追溯，不构造不存在的链；[召回数据定义_V0.1 L1320](运行时详细设计_V0.1/召回数据定义_V0.1.md:1320) |
| RecallAccessObservation.evidence_refs | List<EvidenceRef> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.evidence_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 内容、Pack、传输等本地/外部证据；ContextEmitted 必须能证明实际交付；[召回数据定义_V0.1 L1321](运行时详细设计_V0.1/召回数据定义_V0.1.md:1321) |
| RecallAccessObservation.payload_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `RecallAccessObservation.payload_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 规范化本地记录摘要，排除自身；同 event_id 不同摘要拒绝并审计；[召回数据定义_V0.1 L1322](运行时详细设计_V0.1/召回数据定义_V0.1.md:1322) |

## OutboxEntry

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`OutboxEntry`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| OutboxEntry.event_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.event_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应本地事实 ID；与 event_payload_ref 指向的事件一致；[召回数据定义_V0.1 L1372](运行时详细设计_V0.1/召回数据定义_V0.1.md:1372) |
| OutboxEntry.event_payload_ref | Ref<RecallAccessObservation> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.event_payload_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 不可变本地事件；与本记录同 tenant/recall；[召回数据定义_V0.1 L1373](运行时详细设计_V0.1/召回数据定义_V0.1.md:1373) |
| OutboxEntry.payload_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.payload_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地事件摘要；与事件内摘要一致，不当作编码报文摘要；[召回数据定义_V0.1 L1374](运行时详细设计_V0.1/召回数据定义_V0.1.md:1374) |
| OutboxEntry.event_sequence | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.event_sequence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该事件在执行内的序号；与原事件一致，重投不增加；[召回数据定义_V0.1 L1375](运行时详细设计_V0.1/召回数据定义_V0.1.md:1375) |
| OutboxEntry.external_contract_ref | ContractRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.external_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次使用的已确认 RF 摄取契约；未确认不得开始投递；[召回数据定义_V0.1 L1376](运行时详细设计_V0.1/召回数据定义_V0.1.md:1376) |
| OutboxEntry.encoded_payload_ref | ProtectedRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.encoded_payload_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按原 RF 契约编码的报文；不是直接发送本地对象；[召回数据定义_V0.1 L1377](运行时详细设计_V0.1/召回数据定义_V0.1.md:1377) |
| OutboxEntry.encoded_payload_digest | Hash? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.encoded_payload_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际编码报文的摘要；与本地 payload_digest 分开，重投不改变；[召回数据定义_V0.1 L1378](运行时详细设计_V0.1/召回数据定义_V0.1.md:1378) |
| OutboxEntry.external_dedup_ref | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.external_dedup_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已确认映射下的外部去重关联；具体协议由 RF 决定；[召回数据定义_V0.1 L1379](运行时详细设计_V0.1/召回数据定义_V0.1.md:1379) |
| OutboxEntry.attempt_count | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.attempt_count`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际投递尝试计数，初始 0；不生成新业务事件；[召回数据定义_V0.1 L1380](运行时详细设计_V0.1/召回数据定义_V0.1.md:1380) |
| OutboxEntry.next_attempt_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.next_attempt_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 下次允许投递时间；已 Ack 或契约未就绪可 null；null 本身不是成功证明；[召回数据定义_V0.1 L1381](运行时详细设计_V0.1/召回数据定义_V0.1.md:1381) |
| OutboxEntry.last_error | string? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.last_error`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 经脱敏的最近投递错误；无错误 null，不保存原始带密钥响应；[召回数据定义_V0.1 L1382](运行时详细设计_V0.1/召回数据定义_V0.1.md:1382) |
| OutboxEntry.lease_owner | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.lease_owner`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前 dispatcher；与 lease_until、lease_token 成组；[召回数据定义_V0.1 L1383](运行时详细设计_V0.1/召回数据定义_V0.1.md:1383) |
| OutboxEntry.lease_until | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.lease_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次投递领取期限；失租者不能落 Ack/重试更新；[召回数据定义_V0.1 L1384](运行时详细设计_V0.1/召回数据定义_V0.1.md:1384) |
| OutboxEntry.lease_token | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.lease_token`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次领取的防旧写令牌；与 state_version 共同检查；[召回数据定义_V0.1 L1385](运行时详细设计_V0.1/召回数据定义_V0.1.md:1385) |
| OutboxEntry.ingest_ack_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.ingest_ack_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | RF 认可的可靠摄取证明；不是普通 HTTP 成功或 C 消费确认；[召回数据定义_V0.1 L1386](运行时详细设计_V0.1/召回数据定义_V0.1.md:1386) |
| OutboxEntry.acknowledged_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `OutboxEntry.acknowledged_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地确认可靠摄取的时间；与 ingest_ack_ref 同有值或同 null；[召回数据定义_V0.1 L1387](运行时详细设计_V0.1/召回数据定义_V0.1.md:1387) |

## ContextUseAssessment

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`ContextUseAssessment`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ContextUseAssessment.assessment_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.assessment_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对同一 pack/item 及适用 delivery 的本地评估标识；不新增实际调用方回执对象；[召回数据定义_V0.1 L1424](运行时详细设计_V0.1/召回数据定义_V0.1.md:1424) |
| ContextUseAssessment.assessment_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.assessment_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前输入证据下的不可变评估版本；相同证据与规则复用，新证据新版本；[召回数据定义_V0.1 L1425](运行时详细设计_V0.1/召回数据定义_V0.1.md:1425) |
| ContextUseAssessment.supersedes_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.supersedes_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 被后续证据修订的本地旧版；初版 null；不靠版本字符串大小选最新版；[召回数据定义_V0.1 L1426](运行时详细设计_V0.1/召回数据定义_V0.1.md:1426) |
| ContextUseAssessment.pack_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.pack_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已有 Pack；与本执行结果对应；[召回数据定义_V0.1 L1427](运行时详细设计_V0.1/召回数据定义_V0.1.md:1427) |
| ContextUseAssessment.pack_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.pack_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已有 Pack 版本；不修改原包；[召回数据定义_V0.1 L1428](运行时详细设计_V0.1/召回数据定义_V0.1.md:1428) |
| ContextUseAssessment.item_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.item_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 被评估条目；必须存在于该 Pack；[召回数据定义_V0.1 L1429](运行时详细设计_V0.1/召回数据定义_V0.1.md:1429) |
| ContextUseAssessment.delivery_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.delivery_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 回执能明确关联的一次交付；缺少唯一关联则 null，不能摊给所有重放交付；[召回数据定义_V0.1 L1430](运行时详细设计_V0.1/召回数据定义_V0.1.md:1430) |
| ContextUseAssessment.external_contract_refs | List<ContractRef> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.external_contract_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际使用的实际调用方/RF 原契约；不定义对方字段；[召回数据定义_V0.1 L1431](运行时详细设计_V0.1/召回数据定义_V0.1.md:1431) |
| ContextUseAssessment.receipt_evidence_refs | List<EvidenceRef> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.receipt_evidence_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原始回执的受控证明；没有证据时 []，观察值保持 null；[召回数据定义_V0.1 L1432](运行时详细设计_V0.1/召回数据定义_V0.1.md:1432) |
| ContextUseAssessment.agent_received | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.agent_received`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此范围内是否已被实际调用方接收；true/false 都需明确证据；超时只能为 null；[召回数据定义_V0.1 L1433](运行时详细设计_V0.1/召回数据定义_V0.1.md:1433) |
| ContextUseAssessment.used_in_model_request | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.used_in_model_request`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此范围内是否进入实际调用方的模型调用；true 需精确 pack/item 证据，并支持 agent_received=true；[召回数据定义_V0.1 L1434](运行时详细设计_V0.1/召回数据定义_V0.1.md:1434) |
| ContextUseAssessment.metric_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.metric_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 本地解释/去重口径版本；不定义实际调用方指标；[召回数据定义_V0.1 L1435](运行时详细设计_V0.1/召回数据定义_V0.1.md:1435) |
| ContextUseAssessment.source_evidence_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.source_evidence_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定证据、关联范围和规则版本的摘要；不只摘要可变引用的名称；[召回数据定义_V0.1 L1436](运行时详细设计_V0.1/召回数据定义_V0.1.md:1436) |
| ContextUseAssessment.assessed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.assessed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次本地评估时间；不改变原事件 occurred_at；[召回数据定义_V0.1 L1437](运行时详细设计_V0.1/召回数据定义_V0.1.md:1437) |
| ContextUseAssessment.reason | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `ContextUseAssessment.reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地判断/未知的简要原因；false 仅可表示原契约明确否定的范围，不把缺回执写成 false；[召回数据定义_V0.1 L1438](运行时详细设计_V0.1/召回数据定义_V0.1.md:1438) |

## PlacementVerificationRecord

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`PlacementVerificationRecord`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| PlacementVerificationRecord.verification_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.verification_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 同一动作/验证范围的本地标识；不替代 C action_id；[召回数据定义_V0.1 L1479](运行时详细设计_V0.1/召回数据定义_V0.1.md:1479) |
| PlacementVerificationRecord.evaluation_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.evaluation_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 不可变评估版本；相同输入/规则/窗口复用，新证据新版本；[召回数据定义_V0.1 L1480](运行时详细设计_V0.1/召回数据定义_V0.1.md:1480) |
| PlacementVerificationRecord.supersedes_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.supersedes_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 修订的旧本地评估版本；初版 null；保留历史，不覆盖；[召回数据定义_V0.1 L1481](运行时详细设计_V0.1/召回数据定义_V0.1.md:1481) |
| PlacementVerificationRecord.action_id | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.action_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次评估针对的 C 动作；只读，不改 TierAction 状态；[召回数据定义_V0.1 L1482](运行时详细设计_V0.1/召回数据定义_V0.1.md:1482) |
| PlacementVerificationRecord.action_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.action_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | C/Provider 原动作证据；未取得时 null，不能肯定归因；[召回数据定义_V0.1 L1483](运行时详细设计_V0.1/召回数据定义_V0.1.md:1483) |
| PlacementVerificationRecord.external_contract_refs | List<ContractRef> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.external_contract_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 所用外部证据契约；版本固定；[召回数据定义_V0.1 L1484](运行时详细设计_V0.1/召回数据定义_V0.1.md:1484) |
| PlacementVerificationRecord.action_kind | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.action_kind`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | C 原契约中的动作类别；仅消费；不能按 desired_tier 猜 Prefetch；[召回数据定义_V0.1 L1485](运行时详细设计_V0.1/召回数据定义_V0.1.md:1485) |
| PlacementVerificationRecord.eligible | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.eligible`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 当前核验范围是否适用且动作已成功；false 有已知不适用/未成功证据；未知为 null；[召回数据定义_V0.1 L1486](运行时详细设计_V0.1/召回数据定义_V0.1.md:1486) |
| PlacementVerificationRecord.window_start | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.window_start`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 外部生效证据限定的本地窗口起点；不自定 C 的生效时间；[召回数据定义_V0.1 L1487](运行时详细设计_V0.1/召回数据定义_V0.1.md:1487) |
| PlacementVerificationRecord.window_end | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.window_end`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 外部有效/失效证据限定的本地窗口终点；有完整窗口时 start < end，半开范围；[召回数据定义_V0.1 L1488](运行时详细设计_V0.1/召回数据定义_V0.1.md:1488) |
| PlacementVerificationRecord.window_closed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.window_closed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已证明窗口结束并满足截止条件；不只凭本地 now 就认定迟到事件到齐；[召回数据定义_V0.1 L1489](运行时详细设计_V0.1/召回数据定义_V0.1.md:1489) |
| PlacementVerificationRecord.coverage_complete | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.coverage_complete`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 所需遥测是否完整无空洞；必须有 coverage_ref；未知为 null；[召回数据定义_V0.1 L1490](运行时详细设计_V0.1/召回数据定义_V0.1.md:1490) |
| PlacementVerificationRecord.coverage_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.coverage_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | RF/C 的既有覆盖、水位和截止证明；无证明时不能作否定结论；[召回数据定义_V0.1 L1491](运行时详细设计_V0.1/召回数据定义_V0.1.md:1491) |
| PlacementVerificationRecord.evaluated_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.evaluated_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次评估时间；不是外部窗口的替代值；[召回数据定义_V0.1 L1492](运行时详细设计_V0.1/召回数据定义_V0.1.md:1492) |
| PlacementVerificationRecord.memory_id | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.memory_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 关联的 Memory；无法精确关联时 null；[召回数据定义_V0.1 L1493](运行时详细设计_V0.1/召回数据定义_V0.1.md:1493) |
| PlacementVerificationRecord.memory_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.memory_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 关联 Memory 版本；不仅按同 memory_id 粗匹配；[召回数据定义_V0.1 L1494](运行时详细设计_V0.1/召回数据定义_V0.1.md:1494) |
| PlacementVerificationRecord.representation_id | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际被验证的正文/Artifact 表示；向量搜索命中不混入正文 load_hit；[召回数据定义_V0.1 L1495](运行时详细设计_V0.1/召回数据定义_V0.1.md:1495) |
| PlacementVerificationRecord.content_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.content_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应正式内容引用；与匹配事件及 B 证据一致；[召回数据定义_V0.1 L1496](运行时详细设计_V0.1/召回数据定义_V0.1.md:1496) |
| PlacementVerificationRecord.content_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应内容版本；版本不符不能肯定归因；[召回数据定义_V0.1 L1497](运行时详细设计_V0.1/召回数据定义_V0.1.md:1497) |
| PlacementVerificationRecord.provider_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 精确关联的实际 Provider；不能用期望 Provider 代填；[召回数据定义_V0.1 L1498](运行时详细设计_V0.1/召回数据定义_V0.1.md:1498) |
| PlacementVerificationRecord.matched_event_ids | List<Id> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.matched_event_ids`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地匹配的真实访问事件；同租户可追溯，不把 ContextSelected 当 ContextEmitted；[召回数据定义_V0.1 L1499](运行时详细设计_V0.1/召回数据定义_V0.1.md:1499) |
| PlacementVerificationRecord.recall_ids | List<Id> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.recall_ids`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 关联执行集合；去重，不重复增加动作分子；[召回数据定义_V0.1 L1500](运行时详细设计_V0.1/召回数据定义_V0.1.md:1500) |
| PlacementVerificationRecord.pack_item_refs | List<PackItemRef> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.pack_item_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际发出内容的引用；context_hit=true 时有可验证关联；[召回数据定义_V0.1 L1501](运行时详细设计_V0.1/召回数据定义_V0.1.md:1501) |
| PlacementVerificationRecord.context_use_assessment_refs | List<Ref<ContextUseAssessment>> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.context_use_assessment_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可用实际调用方证据的本地评估引用；无回执可 []，不影响已有 context_hit；[召回数据定义_V0.1 L1502](运行时详细设计_V0.1/召回数据定义_V0.1.md:1502) |
| PlacementVerificationRecord.load_hit | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.load_hit`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否有唯一、精确的加载命中；true 需实际副本/动作证据，false 需完整闭窗；[召回数据定义_V0.1 L1503](运行时详细设计_V0.1/召回数据定义_V0.1.md:1503) |
| PlacementVerificationRecord.context_hit | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.context_hit`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否进一步关联到 ContextEmitted；true 必须 load_hit=true，false 需完整闭窗；[召回数据定义_V0.1 L1504](运行时详细设计_V0.1/召回数据定义_V0.1.md:1504) |
| PlacementVerificationRecord.model_use_hit | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.model_use_hit`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 同一内容是否有实际调用方使用证明；无证据 null；true 必须有可追踪的同 pack/item 证据；[召回数据定义_V0.1 L1505](运行时详细设计_V0.1/召回数据定义_V0.1.md:1505) |
| PlacementVerificationRecord.hit_after_prefetch | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.hit_after_prefetch`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Prefetch 的 context_hit 口径；仅合格 Prefetch 适用；Promote/不适用为 null；[召回数据定义_V0.1 L1506](运行时详细设计_V0.1/召回数据定义_V0.1.md:1506) |
| PlacementVerificationRecord.outcome | VerificationOutcome / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.outcome`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 五种互斥评估结论；按 A-04，不新增 C 动作状态；[召回数据定义_V0.1 L1507](运行时详细设计_V0.1/召回数据定义_V0.1.md:1507) |
| PlacementVerificationRecord.reason | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地结论/不可验证原因；如 window_open；不当作 P2/C 错误码；[召回数据定义_V0.1 L1508](运行时详细设计_V0.1/召回数据定义_V0.1.md:1508) |
| PlacementVerificationRecord.action_policy_version | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.action_policy_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际取得的 C 动作策略版本；缺失 null，不补写 C 默认版本；[召回数据定义_V0.1 L1509](运行时详细设计_V0.1/召回数据定义_V0.1.md:1509) |
| PlacementVerificationRecord.recall_policy_versions | List<Version> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.recall_policy_versions`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 匹配执行实际采用的 Recall 策略；去重，尚无匹配执行可 []；[召回数据定义_V0.1 L1510](运行时详细设计_V0.1/召回数据定义_V0.1.md:1510) |
| PlacementVerificationRecord.metric_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.metric_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 本地核验口径版本；不当作 C 热度/调度阈值；[召回数据定义_V0.1 L1511](运行时详细设计_V0.1/召回数据定义_V0.1.md:1511) |
| PlacementVerificationRecord.source_evidence_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PlacementVerificationRecord.source_evidence_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 输入证据、适用窗口及核验版本的摘要；能区分迟到证据导致的修订；[召回数据定义_V0.1 L1512](运行时详细设计_V0.1/召回数据定义_V0.1.md:1512) |

## PackItemRef

建议代码位置：`src/aether_agent_memory/memory/recall_contracts.py`；类名：`PackItemRef`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| PackItemRef.recall_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PackItemRef.recall_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原执行；与 Pack 归属一致；[召回数据定义_V0.1 L1527](运行时详细设计_V0.1/召回数据定义_V0.1.md:1527) |
| PackItemRef.pack_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PackItemRef.pack_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原 Pack；按父记录租户解析；[召回数据定义_V0.1 L1528](运行时详细设计_V0.1/召回数据定义_V0.1.md:1528) |
| PackItemRef.pack_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PackItemRef.pack_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 不可变结果版本；不能只按 pack_id 忽略版本；[召回数据定义_V0.1 L1529](运行时详细设计_V0.1/召回数据定义_V0.1.md:1529) |
| PackItemRef.item_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PackItemRef.item_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原条目；实际存在于对应 Pack；[召回数据定义_V0.1 L1530](运行时详细设计_V0.1/召回数据定义_V0.1.md:1530) |
| PackItemRef.delivery_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/recall_contracts.py` 新增 `PackItemRef.delivery_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已证明的一次传输交付；Context 归因须能追溯实际发送；不得把缺失当任意交付；[召回数据定义_V0.1 L1531](运行时详细设计_V0.1/召回数据定义_V0.1.md:1531) |

## SemanticEmbeddingRequest

建议代码位置：`src/aether_agent_memory/b1/semantic/models.py`；类名：`SemanticEmbeddingRequest`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| SemanticEmbeddingRequest.embedding_request_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.embedding_request_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 分配的本次向量化请求标识；同租户唯一；[召回数据定义_V0.1 L1561](运行时详细设计_V0.1/召回数据定义_V0.1.md:1561) |
| SemanticEmbeddingRequest.caller_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.caller_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已认证调用方；仅获准调用者可解析输入与结果；[召回数据定义_V0.1 L1562](运行时详细设计_V0.1/召回数据定义_V0.1.md:1562) |
| SemanticEmbeddingRequest.caller_request_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.caller_request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原共享调用的稳定关联；Query 使用 Recall 的 recall_id；Passage 使用 B 对该输入固定的调用身份，不随 task attempt/网络重试变化；[召回数据定义_V0.1 L1563](运行时详细设计_V0.1/召回数据定义_V0.1.md:1563) |
| SemanticEmbeddingRequest.trace_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.trace_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次调用追踪；普通日志不带文本/向量；[召回数据定义_V0.1 L1564](运行时详细设计_V0.1/召回数据定义_V0.1.md:1564) |
| SemanticEmbeddingRequest.authorization_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.authorization_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次输入访问和计算授权；复用结果时重新核验当前授权；[召回数据定义_V0.1 L1565](运行时详细设计_V0.1/召回数据定义_V0.1.md:1565) |
| SemanticEmbeddingRequest.usage | EmbeddingUsage / 是 | [InterceptItem.input_type](../../../AgentJYS-main/src/aether_agent_memory/b1/sidecar.py:297) | 需适配 | 新增目标 usage 字段；显式 Query↔query、Passage↔passage 映射。新请求不得依赖旧 input_type 默认 passage；结果从绑定执行回填。 | 调用方声明用途；Query 或 Passage；不可通过默认值猜测；[召回数据定义_V0.1 L1566](运行时详细设计_V0.1/召回数据定义_V0.1.md:1566) |
| SemanticEmbeddingRequest.input_ref | ProtectedRef / 是 | [EmbeddingRequest.text](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py:16) | 语义不同 | 新增 input_ref，保存固定输入的受控引用；解析后才传给原 text/chunk_text；原字符串不能当 ProtectedRef。 | 本次固定输入文本/片段；非空、受控；不随可变源文档漂移；[召回数据定义_V0.1 L1567](运行时详细设计_V0.1/召回数据定义_V0.1.md:1567) |
| SemanticEmbeddingRequest.source_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.source_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际原始输入 UTF-8 字节摘要；SHA-256、range=null；与读取输入相符；[召回数据定义_V0.1 L1568](运行时详细设计_V0.1/召回数据定义_V0.1.md:1568) |
| SemanticEmbeddingRequest.input_binding_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.input_binding_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 输入上下文的规范化摘要；Passage 含 B 的 chunk/来源版本绑定；Query 含原问题绑定；不能只摘要可变引用名；[召回数据定义_V0.1 L1569](运行时详细设计_V0.1/召回数据定义_V0.1.md:1569) |
| SemanticEmbeddingRequest.input_binding_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.input_binding_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 上述输入上下文快照；可验证与 source_hash 的对应关系；[召回数据定义_V0.1 L1570](运行时详细设计_V0.1/召回数据定义_V0.1.md:1570) |
| SemanticEmbeddingRequest.model_binding | EmbeddingModelBinding / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.model_binding`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 固定的模型与空间契约；请求受理后不随默认模型变化；[召回数据定义_V0.1 L1571](运行时详细设计_V0.1/召回数据定义_V0.1.md:1571) |
| SemanticEmbeddingRequest.deadline_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.deadline_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次最迟等待时间；与调用方 deadline 和 EM-02 取更早值；[召回数据定义_V0.1 L1572](运行时详细设计_V0.1/召回数据定义_V0.1.md:1572) |
| SemanticEmbeddingRequest.execution_policy_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.execution_policy_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定 EM-01～EM-04 配置快照；必须可恢复解析；不是生产验收证明；[召回数据定义_V0.1 L1573](运行时详细设计_V0.1/召回数据定义_V0.1.md:1573) |
| SemanticEmbeddingRequest.reuse_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingRequest.reuse_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地计算复用身份；计算范围见 19.4；[召回数据定义_V0.1 L1574](运行时详细设计_V0.1/召回数据定义_V0.1.md:1574) |

## EmbeddingModelBinding

建议代码位置：`src/aether_agent_memory/b1/semantic/models.py`；类名：`EmbeddingModelBinding`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| EmbeddingModelBinding.model_id | Id / 是 | [VectorQueryResult.model](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:186) | 需校验后映射 | 新增目标字段；模型名须匹配固定模型目录，维度须核对实际向量长度。Sidecar 的 embedding_model/embedding_dim 可作实际运行来源。 | A 模型目录中的标识；不以同名版本替代不同模型；[召回数据定义_V0.1 L1582](运行时详细设计_V0.1/召回数据定义_V0.1.md:1582) |
| EmbeddingModelBinding.model_version | Version / 是 | [B1Service](../../../AgentJYS-main/src/aether_agent_memory/b1/sidecar.py:550) | 部分运行信息已有 | 新增 model_version；Sidecar 已返回 model_hash，须通过固定模型目录建立 hash→版本对应；不能直接以模型名称或任意 hash 冒充版本契约。 | 固定模型版本；不用字符串大小判断新旧；[召回数据定义_V0.1 L1583](运行时详细设计_V0.1/召回数据定义_V0.1.md:1583) |
| EmbeddingModelBinding.dimension | uint / 是 | [VectorQueryResult.dimension](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:185) | 需校验后映射 | 新增目标字段；模型名须匹配固定模型目录，维度须核对实际向量长度。Sidecar 的 embedding_model/embedding_dim 可作实际运行来源。 | 模型输出维度；>0，等于实际向量长度；[召回数据定义_V0.1 L1584](运行时详细设计_V0.1/召回数据定义_V0.1.md:1584) |
| EmbeddingModelBinding.dtype | ExternalLabel / 是 | [BackendConfig.precision](../../../AgentJYS-main/src/aether_agent_memory/b1/backends.py:65) | 语义需区分 | 新增 dtype；precision/quantization_type 是推理精度信息，不自动等于输出向量编码类型；按实际向量和模型契约确定。 | 模型契约指定数值类型；规范字节编码由契约固定；[召回数据定义_V0.1 L1585](运行时详细设计_V0.1/召回数据定义_V0.1.md:1585) |
| EmbeddingModelBinding.embedding_schema_version | Version / 是 | [B1Service](../../../AgentJYS-main/src/aether_agent_memory/b1/sidecar.py:550) | 语义需区分 | 新增 embedding_schema_version；Sidecar 响应 schema_version=1.1 只有确认是同一输出契约才可映射，不能直接当投影 Schema 或所有版本。 | 向量输出格式版本；与 projection_schema_version 分开；[召回数据定义_V0.1 L1586](运行时详细设计_V0.1/召回数据定义_V0.1.md:1586) |
| EmbeddingModelBinding.preprocessing_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `EmbeddingModelBinding.preprocessing_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 文本处理及 usage 编码规则版本；不静默修改空白、前缀或归一化；[召回数据定义_V0.1 L1587](运行时详细设计_V0.1/召回数据定义_V0.1.md:1587) |
| EmbeddingModelBinding.retrieval_space_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `EmbeddingModelBinding.retrieval_space_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 兼容检索空间绑定；Query/Passage 可比较须有契约依据；[召回数据定义_V0.1 L1588](运行时详细设计_V0.1/召回数据定义_V0.1.md:1588) |
| EmbeddingModelBinding.model_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `EmbeddingModelBinding.model_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 模型完整契约；包含输入 token 上限、计数与向量编码规则；[召回数据定义_V0.1 L1589](运行时详细设计_V0.1/召回数据定义_V0.1.md:1589) |

## SemanticEmbeddingResult

建议代码位置：`src/aether_agent_memory/b1/semantic/models.py`；类名：`SemanticEmbeddingResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| SemanticEmbeddingResult.embedding_result_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingResult.embedding_result_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 的共享结果标识；不与 Recall 本地 embedding_result_ref 混用；[召回数据定义_V0.1 L1617](运行时详细设计_V0.1/召回数据定义_V0.1.md:1617) |
| SemanticEmbeddingResult.request_ref | Ref<SemanticEmbeddingRequest> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingResult.request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最初产生该结果的规范化请求；复用不改写原请求与生成时间；[召回数据定义_V0.1 L1618](运行时详细设计_V0.1/召回数据定义_V0.1.md:1618) |
| SemanticEmbeddingResult.usage | EmbeddingUsage / 是 | [InterceptItem.input_type](../../../AgentJYS-main/src/aether_agent_memory/b1/sidecar.py:297) | 需适配 | 新增目标 usage 字段；显式 Query↔query、Passage↔passage 映射。新请求不得依赖旧 input_type 默认 passage；结果从绑定执行回填。 | 实际用途；与原请求相同；写入仅接受 Passage；[召回数据定义_V0.1 L1619](运行时详细设计_V0.1/召回数据定义_V0.1.md:1619) |
| SemanticEmbeddingResult.model_binding | EmbeddingModelBinding / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingResult.model_binding`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际模型/版本/空间；与请求绑定完全兼容；[召回数据定义_V0.1 L1620](运行时详细设计_V0.1/召回数据定义_V0.1.md:1620) |
| SemanticEmbeddingResult.source_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingResult.source_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际输入摘要；与请求一致；[召回数据定义_V0.1 L1621](运行时详细设计_V0.1/召回数据定义_V0.1.md:1621) |
| SemanticEmbeddingResult.input_binding_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingResult.input_binding_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际输入上下文绑定；与请求一致；[召回数据定义_V0.1 L1622](运行时详细设计_V0.1/召回数据定义_V0.1.md:1622) |
| SemanticEmbeddingResult.vector_ref | ProtectedRef / 是 | [EmbeddingRecord.vector](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py:44) | 需转换 | 新增 vector_ref；把已验证的真实向量保存为受控载荷再记录引用；不是将 list[float] 强行改成字符串。 | 真实向量受控载荷；可恢复、非空、维度与 dtype 正确；[召回数据定义_V0.1 L1623](运行时详细设计_V0.1/召回数据定义_V0.1.md:1623) |
| SemanticEmbeddingResult.vector_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingResult.vector_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 规范向量字节摘要；SHA-256、byte_encoding=raw、range=null；具体 dtype 字节序来自 model_contract_ref；[召回数据定义_V0.1 L1624](运行时详细设计_V0.1/召回数据定义_V0.1.md:1624) |
| SemanticEmbeddingResult.validation_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingResult.validation_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 输入、模型、数值及推理/复用证据；不能以配置或零向量替代；[召回数据定义_V0.1 L1625](运行时详细设计_V0.1/召回数据定义_V0.1.md:1625) |
| SemanticEmbeddingResult.validated_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingResult.validated_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次结果首次验证时间；复用保留原时间，当前权限另核验；[召回数据定义_V0.1 L1626](运行时详细设计_V0.1/召回数据定义_V0.1.md:1626) |

## VectorProjectionRequest

建议代码位置：`src/aether_agent_memory/memory/vector_projection/models.py`；类名：`VectorProjectionRequest`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| VectorProjectionRequest.projection_request_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.projection_request_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 请求标识；受理后固定；[召回数据定义_V0.1 L1654](运行时详细设计_V0.1/召回数据定义_V0.1.md:1654) |
| VectorProjectionRequest.caller_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.caller_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 的获准调用身份；不能由普通查询调用隐式取得写权限；[召回数据定义_V0.1 L1655](运行时详细设计_V0.1/召回数据定义_V0.1.md:1655) |
| VectorProjectionRequest.caller_request_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.caller_request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 原任务/调用关联；不更改 B 任务状态；[召回数据定义_V0.1 L1656](运行时详细设计_V0.1/召回数据定义_V0.1.md:1656) |
| VectorProjectionRequest.trace_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.trace_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 写路径追踪；与在线 Recall trace 可以不同；[召回数据定义_V0.1 L1657](运行时详细设计_V0.1/召回数据定义_V0.1.md:1657) |
| VectorProjectionRequest.authorization_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.authorization_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对准确目标的当前写/删授权；upsert 与 delete 分别核验；[召回数据定义_V0.1 L1658](运行时详细设计_V0.1/召回数据定义_V0.1.md:1658) |
| VectorProjectionRequest.owner_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.owner_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 指定的片段、版本及构建/删除依据；不由 A 猜测当前 Memory 版本；[召回数据定义_V0.1 L1659](运行时详细设计_V0.1/召回数据定义_V0.1.md:1659) |
| VectorProjectionRequest.operation_kind | ProjectionOperationKind / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.operation_kind`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | upsert 或 delete；与实际远程变更一致；[召回数据定义_V0.1 L1660](运行时详细设计_V0.1/召回数据定义_V0.1.md:1660) |
| VectorProjectionRequest.build_intent | string? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.build_intent`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | upsert 为 initial_or_retry 或 rebuild；delete 为 null；rebuild 本版返回 PROJECTION_REBUILD_UNSUPPORTED，不创建新操作、不发 P2；B 明确意图不能靠新 attempt 推断；[召回数据定义_V0.1 L1661](运行时详细设计_V0.1/召回数据定义_V0.1.md:1661) |
| VectorProjectionRequest.identity | ProjectionIdentity / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.identity`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原契约五元组；保留全部字段，不能只用 memory_id；[召回数据定义_V0.1 L1662](运行时详细设计_V0.1/召回数据定义_V0.1.md:1662) |
| VectorProjectionRequest.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定目标 Provider；恢复不切换 Provider 盲写；[召回数据定义_V0.1 L1663](运行时详细设计_V0.1/召回数据定义_V0.1.md:1663) |
| VectorProjectionRequest.retrieval_space_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.retrieval_space_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定目标检索空间；必须与向量兼容并获授权；[召回数据定义_V0.1 L1664](运行时详细设计_V0.1/召回数据定义_V0.1.md:1664) |
| VectorProjectionRequest.payload | ProjectionPayload? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.payload`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | upsert 的固定向量及映射；upsert 非 null；delete 为 null；[召回数据定义_V0.1 L1665](运行时详细设计_V0.1/召回数据定义_V0.1.md:1665) |
| VectorProjectionRequest.wait_deadline_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.wait_deadline_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次同步等待截止；过期不代表 P2 操作已取消；[召回数据定义_V0.1 L1666](运行时详细设计_V0.1/召回数据定义_V0.1.md:1666) |
| VectorProjectionRequest.execution_policy_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.execution_policy_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | VP-01～VP-04 固定配置；不复用 Recall 的在线 token/读取预算；[召回数据定义_V0.1 L1667](运行时详细设计_V0.1/召回数据定义_V0.1.md:1667) |
| VectorProjectionRequest.request_fingerprint | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionRequest.request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定请求语义摘要；范围见 21.4；同键不同摘要拒绝；[召回数据定义_V0.1 L1668](运行时详细设计_V0.1/召回数据定义_V0.1.md:1668) |

## ProjectionIdentity

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`ProjectionIdentity`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ProjectionIdentity.memory_id | ExternalId / 是 | [Memory.id](../../../AgentJYS-main/src/aether_agent_memory/core/memory.py:100) | 可复用值但需绑定 | 新增 ProjectionIdentity.memory_id；B 批准后可使用 Memory.id，必须同时绑定其余四个身份字段。 | B 的 Memory 身份；不生成新 Memory；[召回数据定义_V0.1 L1676](运行时详细设计_V0.1/召回数据定义_V0.1.md:1676) |
| ProjectionIdentity.chunk_id | ExternalId / 是 | [EmbeddingRecord.chunk_id](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py:42) | 需确认片段来源 | 新增 ProjectionIdentity.chunk_id；必须来自 B 固定片段，不默认采用旧 A 切分生成 ID；现 P2VectorIndexAdapter 将 memory.id 填 chunk_id 不能普遍成立。 | B 的片段身份；A 不接管切分规则；[召回数据定义_V0.1 L1677](运行时详细设计_V0.1/召回数据定义_V0.1.md:1677) |
| ProjectionIdentity.memory_version | Version / 是 | [Memory.revision](../../../AgentJYS-main/src/aether_agent_memory/core/memory.py:121) | 需 B 版本映射 | 新增 memory_version；只有 B 确認版本编码才映射 revision，不能兼作 content_version/model_version。 | B 本次构建/删除版本；与内容映射相符；[召回数据定义_V0.1 L1678](运行时详细设计_V0.1/召回数据定义_V0.1.md:1678) |
| ProjectionIdentity.model_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `ProjectionIdentity.model_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次投影模型版本；upsert 与向量实际版本相符；[召回数据定义_V0.1 L1679](运行时详细设计_V0.1/召回数据定义_V0.1.md:1679) |
| ProjectionIdentity.projection_schema_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `ProjectionIdentity.projection_schema_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B/A 已对齐的投影 Schema；不等于 Embedding 输出 Schema 或本地记录 Schema；[召回数据定义_V0.1 L1680](运行时详细设计_V0.1/召回数据定义_V0.1.md:1680) |

## ProjectionPayload

建议代码位置：`src/aether_agent_memory/memory/vector_projection/models.py`；类名：`ProjectionPayload`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ProjectionPayload.embedding_result_ref | Ref<SemanticEmbeddingResult> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionPayload.embedding_result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 已验证的 Passage 结果；同租户、usage=Passage，模型/空间匹配；[召回数据定义_V0.1 L1686](运行时详细设计_V0.1/召回数据定义_V0.1.md:1686) |
| ProjectionPayload.representation_id | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionPayload.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准的投影表示关联；与 B 元数据契约一致；[召回数据定义_V0.1 L1687](运行时详细设计_V0.1/召回数据定义_V0.1.md:1687) |
| ProjectionPayload.content_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionPayload.content_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 的正式内容引用；不把向量存储地址当正文地址；[召回数据定义_V0.1 L1688](运行时详细设计_V0.1/召回数据定义_V0.1.md:1688) |
| ProjectionPayload.content_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionPayload.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次正文版本；不能用新正文解释旧向量；[召回数据定义_V0.1 L1689](运行时详细设计_V0.1/召回数据定义_V0.1.md:1689) |
| ProjectionPayload.approved_range | ByteRange / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionPayload.approved_range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 本次片段的准确原字节范围；有范围与输入转换证据；[召回数据定义_V0.1 L1690](运行时详细设计_V0.1/召回数据定义_V0.1.md:1690) |
| ProjectionPayload.content_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionPayload.content_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 正文、范围与 Embedding 输入对应证据；须关联 source_hash / input_binding_digest；[召回数据定义_V0.1 L1691](运行时详细设计_V0.1/召回数据定义_V0.1.md:1691) |
| ProjectionPayload.metadata | ProjectionMetadata / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionPayload.metadata`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准的固定检索属性快照；与本请求身份、模型、正文关联一起映射到 P2；原编码按 AL-P205 确认；[召回数据定义_V0.1 L1692](运行时详细设计_V0.1/召回数据定义_V0.1.md:1692) |
| ProjectionPayload.metadata_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionPayload.metadata_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 只对 scope、memory_type、occurred_at 的 JCS + UTF-8 字节做 SHA-256；排除 owner_metadata_evidence_ref、向量及原文；byte_encoding=jcs-utf8、range=null；[召回数据定义_V0.1 L1693](运行时详细设计_V0.1/召回数据定义_V0.1.md:1693) |

## ProjectionMetadata

建议代码位置：`src/aether_agent_memory/memory/vector_projection/models.py`；类名：`ProjectionMetadata`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ProjectionMetadata.scope | Scope / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionMetadata.scope`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 授权的该片段范围快照；tenant 与请求一致；不能仅靠调用者 tenant 猜测可检索范围；[召回数据定义_V0.1 L1699](运行时详细设计_V0.1/召回数据定义_V0.1.md:1699) |
| ProjectionMetadata.memory_type | ExternalLabel / 是 | [Memory.type](../../../AgentJYS-main/src/aether_agent_memory/core/memory.py:101) | 需契约映射 | 新增同名字段；working/episodic/semantic 与文档标签明确映射；长期投影仅接受获准 Episodic/Semantic。 | B 确认的长期 Memory 类型；本版仅 Episodic / Semantic；不把 Working 直接写为长期投影；[召回数据定义_V0.1 L1700](运行时详细设计_V0.1/召回数据定义_V0.1.md:1700) |
| ProjectionMetadata.occurred_at | Timestamp? / 是 | [Memory.created_at](../../../AgentJYS-main/src/aether_agent_memory/core/memory.py:115) | 语义不等价 | 新增 occurred_at: datetime \| None（必填可空），由 B 提供业务发生时间；不可拿 created_at/updated_at 代填。 | B 的业务发生时间；未知为 null，不能填本次写入时间；时间过滤缺事实时不得伪称支持；[召回数据定义_V0.1 L1701](运行时详细设计_V0.1/召回数据定义_V0.1.md:1701) |
| ProjectionMetadata.owner_metadata_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionMetadata.owner_metadata_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Scope、类型、时间等源事实依据；不由 A 推断生命周期、衰减或冲突事实；[召回数据定义_V0.1 L1702](运行时详细设计_V0.1/召回数据定义_V0.1.md:1702) |

## ProjectionTargetBinding

建议代码位置：`src/aether_agent_memory/memory/vector_projection/models.py`；类名：`ProjectionTargetBinding`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ProjectionTargetBinding.target_binding_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.target_binding_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 本地目标记录标识；不冒充 P2 物理对象 ID；[召回数据定义_V0.1 L1736](运行时详细设计_V0.1/召回数据定义_V0.1.md:1736) |
| ProjectionTargetBinding.target_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.target_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 第 21 章目标摘要；同目标唯一；[召回数据定义_V0.1 L1737](运行时详细设计_V0.1/召回数据定义_V0.1.md:1737) |
| ProjectionTargetBinding.identity | ProjectionIdentity / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.identity`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定五元组；与摘要相符；[召回数据定义_V0.1 L1738](运行时详细设计_V0.1/召回数据定义_V0.1.md:1738) |
| ProjectionTargetBinding.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定 Provider；不因故障改写；[召回数据定义_V0.1 L1739](运行时详细设计_V0.1/召回数据定义_V0.1.md:1739) |
| ProjectionTargetBinding.retrieval_space_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.retrieval_space_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定空间；不与其他空间共用操作；[召回数据定义_V0.1 L1740](运行时详细设计_V0.1/召回数据定义_V0.1.md:1740) |
| ProjectionTargetBinding.upsert_operation_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.upsert_operation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本目标唯一已绑定 upsert；先收到 delete 时可 null；[召回数据定义_V0.1 L1741](运行时详细设计_V0.1/召回数据定义_V0.1.md:1741) |
| ProjectionTargetBinding.delete_operation_id | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.delete_operation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本目标唯一已绑定 delete；删除受理后固定关联；[召回数据定义_V0.1 L1742](运行时详细设计_V0.1/召回数据定义_V0.1.md:1742) |
| ProjectionTargetBinding.retired | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.retired`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否收到获准删除并禁止本机制旧写；初始 false；删除受理时原子置 true，不自动回退；[召回数据定义_V0.1 L1743](运行时详细设计_V0.1/召回数据定义_V0.1.md:1743) |
| ProjectionTargetBinding.retired_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.retired_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 退役标记确认时间；retired=true 时非 null；[召回数据定义_V0.1 L1744](运行时详细设计_V0.1/召回数据定义_V0.1.md:1744) |
| ProjectionTargetBinding.retirement_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.retirement_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 删除依据；与 retired_at 同有值；[召回数据定义_V0.1 L1745](运行时详细设计_V0.1/召回数据定义_V0.1.md:1745) |
| ProjectionTargetBinding.state_version | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionTargetBinding.state_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | CAS 版本；每次成功改变绑定递增；[召回数据定义_V0.1 L1746](运行时详细设计_V0.1/召回数据定义_V0.1.md:1746) |

## VectorProjectionOperation

建议代码位置：`src/aether_agent_memory/memory/vector_projection/models.py`；类名：`VectorProjectionOperation`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| VectorProjectionOperation.operation_id | Id / 是 | [ProjectionWorkItem](../../../AgentJYS-main/src/aether_agent_memory/memory/projection.py:33) | 缺专用字段 | 新增 VectorProjectionOperation.operation_id；若沿用队列，给 ProjectionWorkItem 增加 operation_ref: str \| None 关联操作；work_id/attempts/revision 不代替该字段。 | A 分配的稳定机制操作标识；同键复用，不因重启或丢响应换 ID；[召回数据定义_V0.1 L1774](运行时详细设计_V0.1/召回数据定义_V0.1.md:1774) |
| VectorProjectionOperation.operation_key | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.operation_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 第 21 章按目标和动作计算的键；外部编码须保持全值与命名空间；[召回数据定义_V0.1 L1775](运行时详细设计_V0.1/召回数据定义_V0.1.md:1775) |
| VectorProjectionOperation.request_ref | Ref<VectorProjectionRequest> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已绑定的不可变请求；同租户，可恢复；[召回数据定义_V0.1 L1776](运行时详细设计_V0.1/召回数据定义_V0.1.md:1776) |
| VectorProjectionOperation.target_ref | Ref<ProjectionTargetBinding> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.target_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 准确目标和退役屏障；每次变更前复核；[召回数据定义_V0.1 L1777](运行时详细设计_V0.1/召回数据定义_V0.1.md:1777) |
| VectorProjectionOperation.request_fingerprint | Hash / 是 | [ProjectionWorkItem](../../../AgentJYS-main/src/aether_agent_memory/memory/projection.py:33) | 缺专用字段 | 新增 VectorProjectionOperation.request_fingerprint；若沿用队列，给 ProjectionWorkItem 增加 operation_ref: str \| None 关联操作；work_id/attempts/revision 不代替该字段。 | 原固定载荷语义；与请求一致；[召回数据定义_V0.1 L1778](运行时详细设计_V0.1/召回数据定义_V0.1.md:1778) |
| VectorProjectionOperation.provider_idempotency_key | Id / 是 | [ProjectionWorkItem](../../../AgentJYS-main/src/aether_agent_memory/memory/projection.py:33) | 缺专用字段 | 新增 VectorProjectionOperation.provider_idempotency_key；若沿用队列，给 ProjectionWorkItem 增加 operation_ref: str \| None 关联操作；work_id/attempts/revision 不代替该字段。 | 按已确认适配编码的稳定 P2 幂等键；发出前持久化，同次重发不变化；[召回数据定义_V0.1 L1779](运行时详细设计_V0.1/召回数据定义_V0.1.md:1779) |
| VectorProjectionOperation.provider_operation_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.provider_operation_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P2 实际返回的操作引用；首次响应丢失可 null，此时按原幂等键查证；[召回数据定义_V0.1 L1780](运行时详细设计_V0.1/召回数据定义_V0.1.md:1780) |
| VectorProjectionOperation.coordinator_state | ProjectionCoordinatorState / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.coordinator_state`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 的本地协调进度；不当作 ProviderResult 或 ProjectionState；[召回数据定义_V0.1 L1781](运行时详细设计_V0.1/召回数据定义_V0.1.md:1781) |
| VectorProjectionOperation.execution_policy_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.execution_policy_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原 VP 配置及 P2 限制绑定；重启不改默认值重新计数；[召回数据定义_V0.1 L1782](运行时详细设计_V0.1/召回数据定义_V0.1.md:1782) |
| VectorProjectionOperation.accepted_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.accepted_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次受理确认时间；重试不刷新；[召回数据定义_V0.1 L1783](运行时详细设计_V0.1/召回数据定义_V0.1.md:1783) |
| VectorProjectionOperation.reconcile_until | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.reconcile_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 首次受理计算的恢复截止；不随客户端重试延长；[召回数据定义_V0.1 L1784](运行时详细设计_V0.1/召回数据定义_V0.1.md:1784) |
| VectorProjectionOperation.mutation_attempts_reserved | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.mutation_attempts_reserved`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 发出前已可靠占用的变更次数；含可能未发出的崩溃尝试，不回退；[召回数据定义_V0.1 L1785](运行时详细设计_V0.1/召回数据定义_V0.1.md:1785) |
| VectorProjectionOperation.query_attempts_reserved | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.query_attempts_reserved`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 发出前已可靠占用的查询次数；operation/status/object 每次实际调用分别占用；[召回数据定义_V0.1 L1786](运行时详细设计_V0.1/召回数据定义_V0.1.md:1786) |
| VectorProjectionOperation.pending_call | ProjectionCallIntent? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.pending_call`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已保存但尚未可靠归并结果的调用意图；非 null 时重启先查询/恢复，不能当作未发送；[召回数据定义_V0.1 L1787](运行时详细设计_V0.1/召回数据定义_V0.1.md:1787) |
| VectorProjectionOperation.next_attempt_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.next_attempt_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 下次恢复调度时间；settled/attention_required 为 null；[召回数据定义_V0.1 L1788](运行时详细设计_V0.1/召回数据定义_V0.1.md:1788) |
| VectorProjectionOperation.latest_result_ref | Ref<ProviderResult>? / 是 | [ProjectionWorkItem](../../../AgentJYS-main/src/aether_agent_memory/memory/projection.py:33) | 缺专用字段 | 新增 VectorProjectionOperation.latest_result_ref；若沿用队列，给 ProjectionWorkItem 增加 operation_ref: str \| None 关联操作；work_id/attempts/revision 不代替该字段。 | 最近已可靠采用的观察；尚未形成结果可 null，不伪造 P2 ACCEPTED；[召回数据定义_V0.1 L1789](运行时详细设计_V0.1/召回数据定义_V0.1.md:1789) |
| VectorProjectionOperation.latest_result_version | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.latest_result_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地结果版本，初始 0；有结果时与其 observation_version 相同；[召回数据定义_V0.1 L1790](运行时详细设计_V0.1/召回数据定义_V0.1.md:1790) |
| VectorProjectionOperation.attention_reason | ProjectionErrorCode? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.attention_reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 无法自动推进的本地原因；不因此将 PENDING/UNKNOWN 改为 FAILED；[召回数据定义_V0.1 L1791](运行时详细设计_V0.1/召回数据定义_V0.1.md:1791) |
| VectorProjectionOperation.lease_owner | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.lease_owner`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前协调者；与 lease_until/lease_token 同有值；[召回数据定义_V0.1 L1792](运行时详细设计_V0.1/召回数据定义_V0.1.md:1792) |
| VectorProjectionOperation.lease_until | Timestamp? / 是 | [ProjectionWorkItem.lease_until](../../../AgentJYS-main/src/aether_agent_memory/memory/projection.py:43) | 字段同名但记录不同 | 新增机制对象自己的 lease_until，并配套 lease_owner/lease_token/state_version；队列租约不能直接证明机制记录更新权。 | 当前租约期限；过期后不能落地回调；[召回数据定义_V0.1 L1793](运行时详细设计_V0.1/召回数据定义_V0.1.md:1793) |
| VectorProjectionOperation.lease_token | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.lease_token`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次领取令牌；每次领取变化；与 CAS 同时校验；[召回数据定义_V0.1 L1794](运行时详细设计_V0.1/召回数据定义_V0.1.md:1794) |
| VectorProjectionOperation.state_version | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `VectorProjectionOperation.state_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地 CAS 版本；每次成功更新递增；[召回数据定义_V0.1 L1795](运行时详细设计_V0.1/召回数据定义_V0.1.md:1795) |

## ProjectionCallIntent

建议代码位置：`src/aether_agent_memory/memory/vector_projection/models.py`；类名：`ProjectionCallIntent`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ProjectionCallIntent.call_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionCallIntent.call_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次远程尝试标识；不代替固定的操作幂等键；[召回数据定义_V0.1 L1803](运行时详细设计_V0.1/召回数据定义_V0.1.md:1803) |
| ProjectionCallIntent.call_kind | ProjectionCallKind / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionCallIntent.call_kind`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | upsert、delete 或 query；原操作为 delete 时不允许发 upsert；[召回数据定义_V0.1 L1804](运行时详细设计_V0.1/召回数据定义_V0.1.md:1804) |
| ProjectionCallIntent.reserved_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionCallIntent.reserved_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 次数与调用意图提交时间；早于发出调用，不声称 P2 已接收；[召回数据定义_V0.1 L1805](运行时详细设计_V0.1/召回数据定义_V0.1.md:1805) |
| ProjectionCallIntent.lease_token | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProjectionCallIntent.lease_token`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 领取该次调用的令牌；迟到回调须校验当前租约；[召回数据定义_V0.1 L1806](运行时详细设计_V0.1/召回数据定义_V0.1.md:1806) |

## ProviderResult

建议代码位置：`src/aether_agent_memory/memory/vector_projection/models.py`；类名：`ProviderResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ProviderResult.result_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.result_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地结果稳定引用；同租户唯一；[召回数据定义_V0.1 L1832](运行时详细设计_V0.1/召回数据定义_V0.1.md:1832) |
| ProviderResult.operation_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.operation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原机制操作；与请求及目标一致；[召回数据定义_V0.1 L1833](运行时详细设计_V0.1/召回数据定义_V0.1.md:1833) |
| ProviderResult.observation_version | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.observation_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本操作的观察版本；从 1 递增，旧结果不覆盖；[召回数据定义_V0.1 L1834](运行时详细设计_V0.1/召回数据定义_V0.1.md:1834) |
| ProviderResult.operation_kind | ProjectionOperationKind / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.operation_kind`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原 upsert/delete；READY 必须结合动作解释；[召回数据定义_V0.1 L1835](运行时详细设计_V0.1/召回数据定义_V0.1.md:1835) |
| ProviderResult.identity | ProjectionIdentity / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.identity`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次准确五元组；不混用其他 chunk/版本；[召回数据定义_V0.1 L1836](运行时详细设计_V0.1/召回数据定义_V0.1.md:1836) |
| ProviderResult.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际查询/调用的 Provider；与操作固定 Provider 一致；[召回数据定义_V0.1 L1837](运行时详细设计_V0.1/召回数据定义_V0.1.md:1837) |
| ProviderResult.retrieval_space_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.retrieval_space_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际空间；与原目标相符；[召回数据定义_V0.1 L1838](运行时详细设计_V0.1/召回数据定义_V0.1.md:1838) |
| ProviderResult.state | ProjectionProviderState / 是 | [MemoryProjection.vector_projection_status](../../../AgentJYS-main/src/aether_agent_memory/core/memory.py:16) | 状态不等价 | 新增 ProviderResult.state 五态枚举；原 succeeded 和队列 SUCCEEDED 都不能直接映射 READY，须检查准确对象/完成证据。 | ACCEPTED/PENDING/READY/FAILED/UNKNOWN；判定只按主设计第 2.6 节；[召回数据定义_V0.1 L1839](运行时详细设计_V0.1/召回数据定义_V0.1.md:1839) |
| ProviderResult.raw_status | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.raw_status`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P2 原始状态；无响应时 null，不伪造 P2 枚举；[召回数据定义_V0.1 L1840](运行时详细设计_V0.1/召回数据定义_V0.1.md:1840) |
| ProviderResult.provider_operation_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.provider_operation_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原始操作引用；无法取得可 null；[召回数据定义_V0.1 L1841](运行时详细设计_V0.1/召回数据定义_V0.1.md:1841) |
| ProviderResult.physical_target_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.physical_target_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P2 可验证的准确对象引用；未确认时 null，不根据 memory_id 猜测；[召回数据定义_V0.1 L1842](运行时详细设计_V0.1/召回数据定义_V0.1.md:1842) |
| ProviderResult.object_present | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.object_present`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 准确对象存在性；未知 null；ANN 未命中不能置 false；[召回数据定义_V0.1 L1843](运行时详细设计_V0.1/召回数据定义_V0.1.md:1843) |
| ProviderResult.index_queryable | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.index_queryable`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按签收契约可被目标空间检索；接收 Ack 不能置 true；[召回数据定义_V0.1 L1844](运行时详细设计_V0.1/召回数据定义_V0.1.md:1844) |
| ProviderResult.binding_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.binding_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对象与预期向量/元数据/模型/Schema 的对应证明；upsert READY 必须有；不能只回显请求证明已存储；[召回数据定义_V0.1 L1845](运行时详细设计_V0.1/召回数据定义_V0.1.md:1845) |
| ProviderResult.completion_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.completion_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 写入或删除达到约定完成条件的证明；READY 必须有；[召回数据定义_V0.1 L1846](运行时详细设计_V0.1/召回数据定义_V0.1.md:1846) |
| ProviderResult.delete_confirmed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.delete_confirmed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 准确目标的删除完成事实；upsert 为 null；delete UNKNOWN 不置 false；[召回数据定义_V0.1 L1847](运行时详细设计_V0.1/召回数据定义_V0.1.md:1847) |
| ProviderResult.late_write_barrier_confirmed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.late_write_barrier_confirmed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 旧在途写不能在删除后生效的证明；delete READY 必须 true；[召回数据定义_V0.1 L1848](运行时详细设计_V0.1/召回数据定义_V0.1.md:1848) |
| ProviderResult.error_code | ProjectionErrorCode? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.error_code`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 机制错误/未知原因；READY 为 null；FAILED/UNKNOWN 应解释；[召回数据定义_V0.1 L1849](运行时详细设计_V0.1/召回数据定义_V0.1.md:1849) |
| ProviderResult.retry_advice | ProjectionRetryAdvice / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.retry_advice`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | none、query_only 或 safe_resubmit；不要求 B 盲目重试；[召回数据定义_V0.1 L1850](运行时详细设计_V0.1/召回数据定义_V0.1.md:1850) |
| ProviderResult.safe_resubmit_evidence_ref | EvidenceRef? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.safe_resubmit_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 前次无效果且不会迟到生效、可同键重发的证明；safe_resubmit 时必须有，其余 null；[召回数据定义_V0.1 L1851](运行时详细设计_V0.1/召回数据定义_V0.1.md:1851) |
| ProviderResult.evidence_refs | List<EvidenceRef> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.evidence_refs`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 脱敏受控的原始调用与查验证据；READY/FAILED 不得无依据；超时保留本地真实观察；[召回数据定义_V0.1 L1852](运行时详细设计_V0.1/召回数据定义_V0.1.md:1852) |
| ProviderResult.observed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该结果观察时间；不充当 Memory 当前性或永久存储保证；[召回数据定义_V0.1 L1853](运行时详细设计_V0.1/召回数据定义_V0.1.md:1853) |
| ProviderResult.result_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `memory/vector_projection/models.py` 新增 `ProviderResult.result_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地规范结果摘要，排除自身；不可变结果与载荷相符；[召回数据定义_V0.1 L1854](运行时详细设计_V0.1/召回数据定义_V0.1.md:1854) |

## SemanticEmbeddingExecution

建议代码位置：`src/aether_agent_memory/b1/semantic/models.py`；类名：`SemanticEmbeddingExecution`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| SemanticEmbeddingExecution.embedding_request_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.embedding_request_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原共享请求标识；与 request_ref 一致；[召回数据定义_V0.1 L1886](运行时详细设计_V0.1/召回数据定义_V0.1.md:1886) |
| SemanticEmbeddingExecution.request_ref | Ref<SemanticEmbeddingRequest> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定输入请求；同键换输入/usage/模型拒绝；[召回数据定义_V0.1 L1887](运行时详细设计_V0.1/召回数据定义_V0.1.md:1887) |
| SemanticEmbeddingExecution.caller_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.caller_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原调用身份；重取结果仍验证当前权限；[召回数据定义_V0.1 L1888](运行时详细设计_V0.1/召回数据定义_V0.1.md:1888) |
| SemanticEmbeddingExecution.caller_request_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.caller_request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原调用关联；不跨调用方复用幂等绑定；[召回数据定义_V0.1 L1889](运行时详细设计_V0.1/召回数据定义_V0.1.md:1889) |
| SemanticEmbeddingExecution.state | EmbeddingExecutionState / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.state`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | queued/running/succeeded/failed；不是 Recall 或 P2 机制状态；[召回数据定义_V0.1 L1890](运行时详细设计_V0.1/召回数据定义_V0.1.md:1890) |
| SemanticEmbeddingExecution.attempts_reserved | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.attempts_reserved`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 推理前可靠占用次数；初始 0；批内每条请求各占一次；重启不归零；[召回数据定义_V0.1 L1891](运行时详细设计_V0.1/召回数据定义_V0.1.md:1891) |
| SemanticEmbeddingExecution.deadline_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.deadline_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原执行截止；等于原请求 deadline_at，重放不延期；[召回数据定义_V0.1 L1892](运行时详细设计_V0.1/召回数据定义_V0.1.md:1892) |
| SemanticEmbeddingExecution.execution_policy_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.execution_policy_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原 EM 配置；沿用原重试/队列规则；[召回数据定义_V0.1 L1893](运行时详细设计_V0.1/召回数据定义_V0.1.md:1893) |
| SemanticEmbeddingExecution.result_ref | Ref<SemanticEmbeddingResult>? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.result_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已保存的可信结果；succeeded 必须非 null，其他状态 null；[召回数据定义_V0.1 L1894](运行时详细设计_V0.1/召回数据定义_V0.1.md:1894) |
| SemanticEmbeddingExecution.error_code | EmbeddingErrorCode? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.error_code`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终推理/输入错误；failed 必填；其他状态 null；[召回数据定义_V0.1 L1895](运行时详细设计_V0.1/召回数据定义_V0.1.md:1895) |
| SemanticEmbeddingExecution.lease_owner | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.lease_owner`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前推理协调者；与 lease_until、lease_token 同有值；[召回数据定义_V0.1 L1896](运行时详细设计_V0.1/召回数据定义_V0.1.md:1896) |
| SemanticEmbeddingExecution.lease_until | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.lease_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次推进租约；失租回调不能写结果；[召回数据定义_V0.1 L1897](运行时详细设计_V0.1/召回数据定义_V0.1.md:1897) |
| SemanticEmbeddingExecution.lease_token | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.lease_token`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 防旧推进者写入令牌；重新领取时变化；[召回数据定义_V0.1 L1898](运行时详细设计_V0.1/召回数据定义_V0.1.md:1898) |
| SemanticEmbeddingExecution.state_version | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `b1/semantic/models.py` 新增 `SemanticEmbeddingExecution.state_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | CAS 版本；更新时校验并递增；[召回数据定义_V0.1 L1899](运行时详细设计_V0.1/召回数据定义_V0.1.md:1899) |

## RecordHeader

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`RecordHeader`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RecordHeader.schema_version | SchemaVersion / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `RecordHeader.schema_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 本地结构版本；与记录类型一致，不冒充 RF 注册版本；[召回数据定义_V0.1 L1979](运行时详细设计_V0.1/召回数据定义_V0.1.md:1979) |
| RecordHeader.tenant_id | Id / 是 | [RequestContext.tenant_id](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:15) | 需收紧约束 | 新增 RecordHeader.tenant_id，要求可信非空租户；不能沿用允许缺失的默认值。 | 已验证的租户上下文；所有引用、查询和唯一键均限同租户；[召回数据定义_V0.1 L1980](运行时详细设计_V0.1/召回数据定义_V0.1.md:1980) |
| RecordHeader.created_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `RecordHeader.created_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地记录首次创建时间；创建后固定，不用 Memory 业务时间代替；[召回数据定义_V0.1 L1981](运行时详细设计_V0.1/召回数据定义_V0.1.md:1981) |

## OnlineHeader

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`OnlineHeader`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| OnlineHeader.recall_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `OnlineHeader.recall_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 分配的执行 ID；与受理后执行一一对应；不是 Memory ID；[召回数据定义_V0.1 L1990](运行时详细设计_V0.1/召回数据定义_V0.1.md:1990) |
| OnlineHeader.request_id | Id / 是 | [RequestContext.request_id](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:13) | 有数据来源 | 新增 OnlineHeader.request_id，绑定首次受理的关联；不能用当前重试请求覆盖。 | 首次受理时已验证的请求关联；重试不覆盖原值；重放交付用 delivery_id 区分；[召回数据定义_V0.1 L1991](运行时详细设计_V0.1/召回数据定义_V0.1.md:1991) |
| OnlineHeader.trace_id | Id / 是 | [RequestContext.trace_id](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:14) | 有数据来源 | 新增 OnlineHeader.trace_id，绑定首次受理的关联；不能用当前重试请求覆盖。 | 已验证的链路关联；保持首次执行的关联，格式消费 RF 约定；[召回数据定义_V0.1 L1992](运行时详细设计_V0.1/召回数据定义_V0.1.md:1992) |

## MutableOnlineHeader

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`MutableOnlineHeader`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| MutableOnlineHeader.state_version | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `MutableOnlineHeader.state_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 本地 CAS 版本，初始 0；每次成功更新递增；不能充当 Memory 或外部版本；[召回数据定义_V0.1 L2001](运行时详细设计_V0.1/召回数据定义_V0.1.md:2001) |

## ByteRange

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`ByteRange`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ByteRange.start | uint / 是 | [TextChunk.start_char](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py:33) | 语义不等价 | 新增 ByteRange.start，使用已批准内容的字节偏移；TextChunk 是字符偏移，须有原文编码/转换映射。Proto end 含上界，文档 end 不含上界，非空范围适配为 end-1。 | 已证明范围的起始字节，含下界；相对对应 content_ref 的已确认字节表示，不是字符/token 偏移；[召回数据定义_V0.1 L2018](运行时详细设计_V0.1/召回数据定义_V0.1.md:2018) |
| ByteRange.end | uint / 是 | [TextChunk.end_char](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py:34) | 语义不等价 | 新增 ByteRange.end，使用已批准内容的字节偏移；TextChunk 是字符偏移，须有原文编码/转换映射。Proto end 含上界，文档 end 不含上界，非空范围适配为 end-1。 | 结束字节，不含上界；start < end；不超已批准范围；[召回数据定义_V0.1 L2019](运行时详细设计_V0.1/召回数据定义_V0.1.md:2019) |

## Hash

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`Hash`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| Hash.algorithm | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `Hash.algorithm`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已选用的摘要算法标识；本地 JSON/文本摘要用 SHA-256；来源内容算法按原契约消费；[召回数据定义_V0.1 L2028](运行时详细设计_V0.1/召回数据定义_V0.1.md:2028) |
| Hash.value | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `Hash.value`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 摘要值的本地小写十六进制编码；必须与算法长度匹配；外部其他编码可无损转换并保留证据；[召回数据定义_V0.1 L2029](运行时详细设计_V0.1/召回数据定义_V0.1.md:2029) |
| Hash.byte_encoding | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `Hash.byte_encoding`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 被摘要字节的编码，局部使用 utf-8、jcs-utf8 或 raw；这是输入字节编码，不是 value 的编码；与摘要对象一致；[召回数据定义_V0.1 L2030](运行时详细设计_V0.1/召回数据定义_V0.1.md:2030) |
| Hash.range | ByteRange? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `Hash.range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 被摘要内容的已证明半开范围；null 仅表示本字段所指摘要对象的完整字节，不表示任意范围；[召回数据定义_V0.1 L2031](运行时详细设计_V0.1/召回数据定义_V0.1.md:2031) |

## Scope

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`Scope`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| Scope.tenant_id | Id / 是 | [Scope.tenant_id](../../../AgentJYS-main/src/aether_agent_memory/core/scope.py:14) | 需收紧约束 | 新增严格契约 Scope 的同名字段；从授权求交结果赋值。tenant_id 必须非空；其余字段必须出现但可为 null。旧可空维度不自动证明授权。 | 请求与认证身份求交后的租户；等于父记录 tenant_id；[召回数据定义_V0.1 L2042](运行时详细设计_V0.1/召回数据定义_V0.1.md:2042) |
| Scope.project_id | Id? / 是 | [Scope](../../../AgentJYS-main/src/aether_agent_memory/core/scope.py:7) | 现有类缺字段 | 给 core.Scope 和 RequestContext 增加 project_id: str \| None，并同步 as_dict、scope、from_values、from_mapping；新契约 Scope.project_id 必须出现但可空。不得由 user_id 改名得到。 | 已授权项目，没有该维度则 null；null 不等于允许任意项目；[召回数据定义_V0.1 L2043](运行时详细设计_V0.1/召回数据定义_V0.1.md:2043) |
| Scope.agent_id | Id? / 是 | [Scope.agent_id](../../../AgentJYS-main/src/aether_agent_memory/core/scope.py:16) | 需收紧约束 | 新增严格契约 Scope 的同名字段；从授权求交结果赋值。tenant_id 必须非空；其余字段必须出现但可为 null。旧可空维度不自动证明授权。 | 已授权 Agent，没有该维度则 null；不信任客户端自报；[召回数据定义_V0.1 L2044](运行时详细设计_V0.1/召回数据定义_V0.1.md:2044) |
| Scope.session_id | Id? / 是 | [Scope.session_id](../../../AgentJYS-main/src/aether_agent_memory/core/scope.py:17) | 需收紧约束 | 新增严格契约 Scope 的同名字段；从授权求交结果赋值。tenant_id 必须非空；其余字段必须出现但可为 null。旧可空维度不自动证明授权。 | 当前已授权会话；含 Working 的模式至少有 session_id 或 task_id；[召回数据定义_V0.1 L2045](运行时详细设计_V0.1/召回数据定义_V0.1.md:2045) |
| Scope.task_id | Id? / 是 | [Scope.task_id](../../../AgentJYS-main/src/aether_agent_memory/core/scope.py:18) | 需收紧约束 | 新增严格契约 Scope 的同名字段；从授权求交结果赋值。tenant_id 必须非空；其余字段必须出现但可为 null。旧可空维度不自动证明授权。 | 当前已授权任务；同上；不能把其他会话/任务扩大进来；[召回数据定义_V0.1 L2046](运行时详细设计_V0.1/召回数据定义_V0.1.md:2046) |

## RetrievalConstraints

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`RetrievalConstraints`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| RetrievalConstraints.memory_types | List<ExternalLabel> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `RetrievalConstraints.memory_types`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原始约束缺省时在选路前展开为本策略认可的 B 类型；本版按 Working、Episodic、Semantic 固定顺序去重；非空且不含未知类型；各分支只消费适用类型，不因选路删除原筛选意图；[召回数据定义_V0.1 L2055](运行时详细设计_V0.1/召回数据定义_V0.1.md:2055) |
| RetrievalConstraints.occurred_after | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `RetrievalConstraints.occurred_after`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原始约束缺省为 null；有值时含下界；Memory 业务时间仍由 B 定义；[召回数据定义_V0.1 L2056](运行时详细设计_V0.1/召回数据定义_V0.1.md:2056) |
| RetrievalConstraints.occurred_before | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `RetrievalConstraints.occurred_before`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原始约束缺省为 null；有值时不含上界；下界不得晚于上界；[召回数据定义_V0.1 L2057](运行时详细设计_V0.1/召回数据定义_V0.1.md:2057) |
| RetrievalConstraints.allowed_sources | List<LogicalSource> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `RetrievalConstraints.allowed_sources`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 根据内部模式派生；客户端禁止传入；请求级必须等于模式必需集合，按 working、long_term 排序；仅 ReadBounds.filter 的分支视图为其中的单来源子集，不反写父请求；[召回数据定义_V0.1 L2058](运行时详细设计_V0.1/召回数据定义_V0.1.md:2058) |

## SourceCoverage

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`SourceCoverage`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| SourceCoverage.working | CoverageStatus / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceCoverage.working`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Working 来源结果；未请求时固定 not_requested；[召回数据定义_V0.1 L2069](运行时详细设计_V0.1/召回数据定义_V0.1.md:2069) |
| SourceCoverage.long_term | CoverageStatus / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceCoverage.long_term`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 长期来源结果；未请求时固定 not_requested；[召回数据定义_V0.1 L2070](运行时详细设计_V0.1/召回数据定义_V0.1.md:2070) |

## ContentIdentity

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`ContentIdentity`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| ContentIdentity.tenant_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `ContentIdentity.tenant_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地授权租户；与父记录一致；[召回数据定义_V0.1 L2081](运行时详细设计_V0.1/召回数据定义_V0.1.md:2081) |
| ContentIdentity.memory_id | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `ContentIdentity.memory_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 确认的 Memory 引用；不从向量 ID 自行推断；[召回数据定义_V0.1 L2082](运行时详细设计_V0.1/召回数据定义_V0.1.md:2082) |
| ContentIdentity.memory_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `ContentIdentity.memory_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 确认的当前版本；不混合不同版本；[召回数据定义_V0.1 L2083](运行时详细设计_V0.1/召回数据定义_V0.1.md:2083) |
| ContentIdentity.content_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `ContentIdentity.content_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准的正式内容引用；不是内部数据库地址；[召回数据定义_V0.1 L2084](运行时详细设计_V0.1/召回数据定义_V0.1.md:2084) |
| ContentIdentity.content_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `ContentIdentity.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 被批准的内容版本；与实际校验版本一致；[召回数据定义_V0.1 L2085](运行时详细设计_V0.1/召回数据定义_V0.1.md:2085) |
| ContentIdentity.range | ByteRange / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `ContentIdentity.range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 完整、确定的批准片段范围；去重不使用模糊的 null 范围；原始范围映射须有证据；[召回数据定义_V0.1 L2086](运行时详细设计_V0.1/召回数据定义_V0.1.md:2086) |

## CandidateExclusion

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`CandidateExclusion`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| CandidateExclusion.candidate_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `CandidateExclusion.candidate_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 被排除的本地候选引用；在当前执行的发现/候选记录中可解析；[召回数据定义_V0.1 L2097](运行时详细设计_V0.1/召回数据定义_V0.1.md:2097) |
| CandidateExclusion.reason | ReasonCode? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `CandidateExclusion.reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 能映射的本地排除原因；与 rule_ref 至少一个有值；故障未知不得伪装权威排除；[召回数据定义_V0.1 L2098](运行时详细设计_V0.1/召回数据定义_V0.1.md:2098) |
| CandidateExclusion.rule_ref | Id? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `CandidateExclusion.rule_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地已版本化规则引用；例如 RULE-01；固定 policy_version，不能临时发明规则；[召回数据定义_V0.1 L2099](运行时详细设计_V0.1/召回数据定义_V0.1.md:2099) |
| CandidateExclusion.authority_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `CandidateExclusion.authority_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 规则归属方或原权威事实引用；不把 Recall 快照冒充 B 权威；[召回数据定义_V0.1 L2100](运行时详细设计_V0.1/召回数据定义_V0.1.md:2100) |
| CandidateExclusion.authority_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `CandidateExclusion.authority_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 做出排除时实际采用的规则/事实版本；可复现，不取后来发布的默认版；[召回数据定义_V0.1 L2101](运行时详细设计_V0.1/召回数据定义_V0.1.md:2101) |
| CandidateExclusion.evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `CandidateExclusion.evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 支持本次排除的证据；没有证据时应记缺口；[召回数据定义_V0.1 L2102](运行时详细设计_V0.1/召回数据定义_V0.1.md:2102) |
| CandidateExclusion.decided_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `CandidateExclusion.decided_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地做出判断的时间；不覆盖原证据时间；[召回数据定义_V0.1 L2103](运行时详细设计_V0.1/召回数据定义_V0.1.md:2103) |

## SourceReference

建议代码位置：`src/aether_agent_memory/runtime/contract_types.py`；类名：`SourceReference`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| SourceReference.candidate_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.candidate_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地发现候选标识；Working 由 Recall 分配；向量候选与 VectorCandidate 一致；[召回数据定义_V0.1 L2112](运行时详细设计_V0.1/召回数据定义_V0.1.md:2112) |
| SourceReference.source_id | LogicalSource / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.source_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 逻辑来源；与所属来源结果一致；[召回数据定义_V0.1 L2113](运行时详细设计_V0.1/召回数据定义_V0.1.md:2113) |
| SourceReference.source_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.source_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B Working 引用或 Projection 引用；保留原引用，不访问内部 Redis key；[召回数据定义_V0.1 L2114](运行时详细设计_V0.1/召回数据定义_V0.1.md:2114) |
| SourceReference.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 产生这次发现的原能力/Provider；不与后续实际正文 Provider 混为一谈；[召回数据定义_V0.1 L2115](运行时详细设计_V0.1/召回数据定义_V0.1.md:2115) |
| SourceReference.representation_id | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 来源能证明的真实表示标识；未解析时 null，不拿 memory_id 代填；[召回数据定义_V0.1 L2116](运行时详细设计_V0.1/召回数据定义_V0.1.md:2116) |
| SourceReference.representation_type | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.representation_type`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原契约中的表示类别；与 representation_id 的证据一致；[召回数据定义_V0.1 L2117](运行时详细设计_V0.1/召回数据定义_V0.1.md:2117) |
| SourceReference.source_rank | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.source_rank`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原来源次序，从 1 开始；校验后重排的融合 rank 另在 RecallCandidate 中保存；[召回数据定义_V0.1 L2118](运行时详细设计_V0.1/召回数据定义_V0.1.md:2118) |
| SourceReference.external_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.external_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 来源契约版本；必须可追溯；[召回数据定义_V0.1 L2119](运行时详细设计_V0.1/召回数据定义_V0.1.md:2119) |
| SourceReference.source_evidence_ref | EvidenceRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.source_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该次发现的实际证据；不能仅引用后来的新版本响应；[召回数据定义_V0.1 L2120](运行时详细设计_V0.1/召回数据定义_V0.1.md:2120) |
| SourceReference.observed_at | Timestamp / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `runtime/contract_types.py` 新增 `SourceReference.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本地发现观察时间；不替代 Working TTL 或 Memory 业务时间；[召回数据定义_V0.1 L2121](运行时详细设计_V0.1/召回数据定义_V0.1.md:2121) |

## P2SearchInput

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2SearchInput`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2SearchInput.query_vector | List<number> / 是 | [EmbeddingRecord.vector](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py:44) | 可复用值 | 新增目标字段并传真实数值；检索消费 Query，写入消费 Passage；核验 model_binding、维度、有限值及 Proto float 编码兼容性。 | QueryEmbeddingResult 中解析出的真实向量；有限数值且长度匹配；[召回与P2接口对接需求_V0.1 L115](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:115) |
| P2SearchInput.usage | Query / 是 | [InterceptItem.input_type](../../../AgentJYS-main/src/aether_agent_memory/b1/sidecar.py:297) | 需适配 | 新增目标 usage 字段；显式 Query↔query、Passage↔passage 映射。新请求不得依赖旧 input_type 默认 passage；结果从绑定执行回填。 | 本调用用途，不能隐式保存为长期投影；[召回与P2接口对接需求_V0.1 L116](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:116) |
| P2SearchInput.model_binding | EmbeddingModelBinding / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchInput.model_binding`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际模型、版本、维度/dtype、预处理和空间，见 1.2.2；[召回与P2接口对接需求_V0.1 L117](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:117) |
| P2SearchInput.retrieval_space_ref | ContractRef / 是 | [P2GrpcClient](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:63) | 需契约映射 | 新增 retrieval_space_ref；由固定空间映射解析到 collection。collection 字符串本身不证明模型兼容性或授权。 | 已批准的检索空间；与 Query/候选模型兼容；[召回与P2接口对接需求_V0.1 L118](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:118) |
| P2SearchInput.scope | Scope / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchInput.scope`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 认证后批准范围，见数据字典附录 A；不能静默放宽；[召回与P2接口对接需求_V0.1 L119](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:119) |
| P2SearchInput.memory_types | List<ExternalLabel> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchInput.memory_types`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Recall 筛选中适用于长期分支的 Episodic/Semantic，非空；不传 Working；[召回与P2接口对接需求_V0.1 L120](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:120) |
| P2SearchInput.occurred_after | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchInput.occurred_after`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 业务时间的含下界，未筛选为 null；[召回与P2接口对接需求_V0.1 L121](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:121) |
| P2SearchInput.occurred_before | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchInput.occurred_before`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 业务时间的不含上界，未筛选为 null；[召回与P2接口对接需求_V0.1 L122](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:122) |
| P2SearchInput.top_k | uint / 是 | [P2GrpcClient](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:63) | 底层已有参数 | 新增 P2SearchInput.top_k，传给 search_vectors(top_k) / SearchVectorRequest.top_k；实际发送上限须与 requested_k 一致。 | >0，受 Recall 固定策略及签收服务限额约束；不静默截小；[召回与P2接口对接需求_V0.1 L123](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:123) |

## P2SearchResult

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2SearchResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2SearchResult.retrieval_space_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchResult.retrieval_space_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际使用的空间及解释依据；[召回与P2接口对接需求_V0.1 L131](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:131) |
| P2SearchResult.requested_k | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchResult.requested_k`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次实际约定的 K，与请求一致；[召回与P2接口对接需求_V0.1 L132](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:132) |
| P2SearchResult.completion | complete / partial / failed / 是 | [RecallSourceResult.complete](../../../AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py:53) | 语义不等价 | 新增 completion 及 completion_evidence；旧 complete=True 默认值、NOT_FOUND→[] 或返回条数都不能证明 P2 完成。 | 本次有界搜索是否完成；不声称全库精确 TopK；[召回与P2接口对接需求_V0.1 L133](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:133) |
| P2SearchResult.hits | List<P2SearchHit> / 是 | [P2GrpcClient](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:63) | 列表已有，元素需转换 | 新增 P2SearchResult.hits: list[P2SearchHit]；转换原 list[P2VectorHit]，保留未裁剪的有界返回及来源事实。 | 同一次尝试返回的有序候选；条数 ≤ requested_k；[召回与P2接口对接需求_V0.1 L134](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:134) |
| P2SearchResult.partial_reason | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchResult.partial_reason`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | partial 时必须解释；complete 时 null；failed 的原因放公共 error；[召回与P2接口对接需求_V0.1 L135](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:135) |
| P2SearchResult.ranking_contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchResult.ranking_contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 分数含义、排序方向或返回顺序的明确契约；[召回与P2接口对接需求_V0.1 L136](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:136) |
| P2SearchResult.query_binding_evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchResult.query_binding_evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际空间、授权范围及筛选应用到本次搜索的依据；[召回与P2接口对接需求_V0.1 L137](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:137) |
| P2SearchResult.completion_evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchResult.completion_evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 有界搜索完成或部分返回的依据；不能从条数猜测；[召回与P2接口对接需求_V0.1 L138](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:138) |

## P2SearchHit

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2SearchHit`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2SearchHit.projection_ref | ExternalId / 是 | [P2VectorHit.id](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:33) | 需契约映射 | 新增 projection_ref；只有确认 id 是 B 可解析的投影引用才赋值。不得直接推导 memory_id。 | 可交 B 解析的稳定 Projection 引用；原 SearchHit.id 须有确认映射；[召回与P2接口对接需求_V0.1 L142](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:142) |
| P2SearchHit.provider_rank | uint / 是 | [P2GrpcClient](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:63) | 需契约映射 | 新增 provider_rank；只有确认 P2 返回顺序即排名才从 1 编号，不能靠 raw_score 猜测。 | 从 1 开始的原排名；可按已签收的返回顺序适配，不能按分数臆造；[召回与P2接口对接需求_V0.1 L143](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:143) |
| P2SearchHit.raw_score | number? / 是 | [P2VectorHit.score](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:34) | 需保留未知 | 新增 raw_score: float \| None（必填可空）；原分数存在时保留，无来源分数时显式 null，不能复用 MemorySearchHit.score 的默认 0。 | 原分数；缺失为 null，不直接跨空间或与 Working 比较；[召回与P2接口对接需求_V0.1 L144](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:144) |
| P2SearchHit.model_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchHit.model_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 候选对应模型版本，必须与 Query 空间兼容；[召回与P2接口对接需求_V0.1 L145](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:145) |
| P2SearchHit.projection_schema_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchHit.projection_schema_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 候选投影 Schema；不是 Memory 版本；[召回与P2接口对接需求_V0.1 L146](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:146) |
| P2SearchHit.identity | ProjectionIdentity? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchHit.identity`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 若返回完整五元组需有依据；未返回时通过稳定引用交 B 解析，不用当前版本补写；[召回与P2接口对接需求_V0.1 L147](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:147) |
| P2SearchHit.source_evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2SearchHit.source_evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原引用、版本和排序的来源依据，可关联该条原响应及空间契约；[召回与P2接口对接需求_V0.1 L148](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:148) |

## P2ContentTarget

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ContentTarget`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ContentTarget.content_ref | ExternalId / 是 | [ObjectReference.content_ref](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:16) | 需 B 批准绑定 | 新增同名字段；原引用可作候选值，正式读取须和 B 的版本、表示、范围绑定。 | B 的正式内容引用；[召回与P2接口对接需求_V0.1 L219](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:219) |
| P2ContentTarget.content_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentTarget.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准读取的正文版本；[召回与P2接口对接需求_V0.1 L220](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:220) |
| P2ContentTarget.representation_id | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentTarget.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原内容表示身份；[召回与P2接口对接需求_V0.1 L221](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:221) |
| P2ContentTarget.representation_type | ExternalLabel / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentTarget.representation_type`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 契约中的类别，不由 A 自建枚举；[召回与P2接口对接需求_V0.1 L222](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:222) |
| P2ContentTarget.bucket | ExternalId / 是 | [ObjectReference.namespace](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:14) | 需适配 | 新增 bucket；仅当前 p2:// 映射确认后由 namespace/已批准映射赋值。 | B/Provider 批准的实际对象空间映射；[召回与P2接口对接需求_V0.1 L223](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:223) |
| P2ContentTarget.key | ExternalId / 是 | [ObjectReference.object_key](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:15) | 需适配 | 新增 key；使用 B 批准的物理定位，不从未验证 metadata 自行替换。 | 对应准确版本的对象定位或条件读取目标，不能由 memory_id 猜测；[召回与P2接口对接需求_V0.1 L224](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:224) |

## P2ContentMetadataInput

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ContentMetadataInput`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ContentMetadataInput.content | P2ContentTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadataInput.content`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 同 1.7 节批准的正文表示与版本；[召回与P2接口对接需求_V0.1 L246](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:246) |
| P2ContentMetadataInput.approved_range | ByteRange / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadataInput.approved_range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 需要验证的字节范围；不能默认整对象摘要覆盖任意片段；[召回与P2接口对接需求_V0.1 L247](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:247) |
| P2ContentMetadataInput.version_condition_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadataInput.version_condition_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 与 get 同样的版本条件或不可变定位依据；[召回与P2接口对接需求_V0.1 L248](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:248) |

## P2ContentMetadataResult

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ContentMetadataResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ContentMetadataResult.status | ok / not_found / version_mismatch / failed / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadataResult.status`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对准确对象的元数据查询结果；[召回与P2接口对接需求_V0.1 L256](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:256) |
| P2ContentMetadataResult.meta | P2ContentMetadata? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadataResult.meta`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | ok 时必须有；其余可 null；[召回与P2接口对接需求_V0.1 L257](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:257) |
| P2ContentMetadataResult.evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadataResult.evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本次对象/版本/摘要范围的原始依据；[召回与P2接口对接需求_V0.1 L258](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:258) |

## P2ContentMetadata

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ContentMetadata`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ContentMetadata.content_ref | ExternalId / 是 | [ObjectReference.content_ref](../../../AgentJYS-main/src/aether_agent_memory/runtime/dtos.py:16) | 需 B 批准绑定 | 新增同名字段；原引用可作候选值，正式读取须和 B 的版本、表示、范围绑定。 | B 内容引用与实际对象的可验证映射；[召回与P2接口对接需求_V0.1 L264](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:264) |
| P2ContentMetadata.content_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadata.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际证实的正文版本；不能用 memory_version 替代；[召回与P2接口对接需求_V0.1 L265](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:265) |
| P2ContentMetadata.representation_id | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadata.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应 B 批准的内容表示；[召回与P2接口对接需求_V0.1 L266](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:266) |
| P2ContentMetadata.object_size_bytes | uint / 是 | [P2ObjectMeta.size](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:28) | 可复用值 | 新增 object_size_bytes；映射同一准确对象的 ObjectMeta.size，不能当作本次返回片段长度。 | 同一版本完整对象的字节长度，区别于本次 returned_bytes；[召回与P2接口对接需求_V0.1 L267](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:267) |
| P2ContentMetadata.content_encoding | ExternalLabel / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadata.content_encoding`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 字节表示/文本编码的契约标识，可由不可变内容契约证明；[召回与P2接口对接需求_V0.1 L268](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:268) |
| P2ContentMetadata.etag | string? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadata.etag`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 原 ETag；语义未经证明时只作不透明标识；[召回与P2接口对接需求_V0.1 L269](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:269) |
| P2ContentMetadata.checksum | Hash? / 是 | [P2ObjectMeta](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:24) | Proto 有字段，Python 丢失 | 给 P2ObjectMeta 增加 md5_hex/blake3_hex: str \| None 并保留响应；新增 checksum: Hash?，按算法、编码和覆盖范围转换，不把 etag 一律当内容 hash。 | 来源实际提供的算法、值、字节编码及覆盖范围；未提供可 null；[召回与P2接口对接需求_V0.1 L270](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:270) |
| P2ContentMetadata.version_evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentMetadata.version_evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 以上元数据与准确版本绑定的依据；[召回与P2接口对接需求_V0.1 L271](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:271) |

## P2CachedReadInput

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2CachedReadInput`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2CachedReadInput.content_read | P2ContentReadInput / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CachedReadInput.content_read`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 与正式读取完全一致的批准版本、范围和期望摘要；[召回与P2接口对接需求_V0.1 L293](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:293) |
| P2CachedReadInput.cache_read_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CachedReadInput.cache_read_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 经 B/Provider 认可的可读热副本引用；不是内部 Redis key；[召回与P2接口对接需求_V0.1 L294](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:294) |
| P2CachedReadInput.fallback_owner | caller / provider / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CachedReadInput.fallback_owner`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 签收的单次回退责任方；不能上下层各自重复回退；[召回与P2接口对接需求_V0.1 L295](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:295) |

## P2CacheTrace

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2CacheTrace`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2CacheTrace.cache_read_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CacheTrace.cache_read_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际尝试的已批准副本引用，不能根据 Memory ID 猜测；[召回与P2接口对接需求_V0.1 L303](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:303) |
| P2CacheTrace.cache_outcome | hit / miss / invalid / unavailable / unknown / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CacheTrace.cache_outcome`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 热路径实际结果；hit 仍须正文校验；[召回与P2接口对接需求_V0.1 L304](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:304) |
| P2CacheTrace.fallback_performed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CacheTrace.fallback_performed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 是否已执行正式回退；未知为 null；[召回与P2接口对接需求_V0.1 L305](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:305) |
| P2CacheTrace.final_read_path | prewarm / canonical / unknown / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CacheTrace.final_read_path`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 最终响应字节的路径，必须与 P2ContentReadResult.read_path 一致；[召回与P2接口对接需求_V0.1 L306](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:306) |
| P2CacheTrace.diagnostic_code | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CacheTrace.diagnostic_code`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 失效、释放、校验失败等原始分类，正常可 null；[召回与P2接口对接需求_V0.1 L307](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:307) |
| P2CacheTrace.evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CacheTrace.evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 缓存尝试与内部回退依据；[召回与P2接口对接需求_V0.1 L308](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:308) |

## P2ContentReadInput

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ContentReadInput`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ContentReadInput.content | P2ContentTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadInput.content`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准的正文、表示、版本及物理定位映射，见 1.4.2；[召回与P2接口对接需求_V0.1 L332](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:332) |
| P2ContentReadInput.approved_range | ByteRange / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadInput.approved_range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 批准的准确半开字节范围 [start,end)，相对 content_ref 的指定字节表示；[召回与P2接口对接需求_V0.1 L333](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:333) |
| P2ContentReadInput.expected_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadInput.expected_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B/原内容契约提供的可信期望摘要，覆盖本次批准范围；[召回与P2接口对接需求_V0.1 L334](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:334) |
| P2ContentReadInput.version_condition_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadInput.version_condition_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 支持时使用已确认条件读取标识；否则须有不可变版本定位等保证；[召回与P2接口对接需求_V0.1 L335](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:335) |
| P2ContentReadInput.max_response_bytes | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadInput.max_response_bytes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 已预留并允许接收的字节上限，不构成静默截断许可；[召回与P2接口对接需求_V0.1 L336](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:336) |

## P2ContentReadResult

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ContentReadResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ContentReadResult.status | ok / not_found / version_mismatch / failed / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadResult.status`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 字节读取事实；不等于 Memory 生命周期状态；[召回与P2接口对接需求_V0.1 L344](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:344) |
| P2ContentReadResult.content | P2ContentTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadResult.content`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 响应所关联的请求目标；ok 时实际版本必须由 meta 及证据证明，不能只靠回显；[召回与P2接口对接需求_V0.1 L345](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:345) |
| P2ContentReadResult.data | bytes? / 是 | [P2GrpcClient](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:63) | 有字节但返回对象缺失 | 新增 P2ContentReadResult.data；保留 GetObject 的 bytes，同时返回 meta/范围/证据；旧 get_object 仅 bytes/None 不足。 | 实际响应字节；ok 时非 null；失败残留字节只隔离处理，不输出为可信正文；[召回与P2接口对接需求_V0.1 L346](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:346) |
| P2ContentReadResult.returned_range | ByteRange? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadResult.returned_range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际返回的范围；ok 时与 approved_range 一致；[召回与P2接口对接需求_V0.1 L347](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:347) |
| P2ContentReadResult.returned_bytes | uint / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadResult.returned_bytes`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际响应字节数，ok 时等于 data 长度与批准范围长度；[召回与P2接口对接需求_V0.1 L348](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:348) |
| P2ContentReadResult.meta | P2ContentMetadata? / 是 | [P2GrpcClient](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:63) | Proto 有对象，Python 丢失 | 新增 meta: P2ContentMetadata?；读取时保留 ObjectBytes.meta，补齐版本依据后转换。 | 版本、编码、长度和摘要事实，见 1.5 节；ok 时必须有；[召回与P2接口对接需求_V0.1 L349](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:349) |
| P2ContentReadResult.read_path | canonical / prewarm / unknown / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadResult.read_path`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际采用路径的事实；无依据为 unknown，不用期望位置代填；[召回与P2接口对接需求_V0.1 L350](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:350) |
| P2ContentReadResult.placement | P2ReadPlacement? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadResult.placement`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可选物理来源/放置证据，见 1.6、1.8 节；缺失可 null；[召回与P2接口对接需求_V0.1 L351](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:351) |
| P2ContentReadResult.cache_trace | P2CacheTrace? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadResult.cache_trace`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可选热副本及内部回退事实，见 1.6、1.8 节；未使用可 null；[召回与P2接口对接需求_V0.1 L352](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:352) |
| P2ContentReadResult.evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ContentReadResult.evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 正文版本、范围及此次字节响应的关联证据；[召回与P2接口对接需求_V0.1 L353](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:353) |

## P2ReadPlacement

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ReadPlacement`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ReadPlacement.actual_provider | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ReadPlacement.actual_provider`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际读取字节的 Provider，无证据为 null；[召回与P2接口对接需求_V0.1 L424](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:424) |
| P2ReadPlacement.observed_tier | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ReadPlacement.observed_tier`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 该次读取的真实层级，不是 desired_tier；[召回与P2接口对接需求_V0.1 L425](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:425) |
| P2ReadPlacement.placement_generation | Version? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ReadPlacement.placement_generation`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原放置版本；不自行比较大小，不要求与 Memory 版本相等；[召回与P2接口对接需求_V0.1 L426](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:426) |
| P2ReadPlacement.observed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ReadPlacement.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 对该放置事实的观察时间；[召回与P2接口对接需求_V0.1 L427](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:427) |
| P2ReadPlacement.producing_action_id | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ReadPlacement.producing_action_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 能唯一关联该副本/读取的 C 动作；不能按时间接近猜测；[召回与P2接口对接需求_V0.1 L428](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:428) |
| P2ReadPlacement.evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ReadPlacement.evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 与父读取的内容、表示、版本、范围及动作绑定的证据；[召回与P2接口对接需求_V0.1 L429](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:429) |

## P2CallContext

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2CallContext`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2CallContext.request_ref | Id / 是 | [RequestContext.request_id](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:13) | 需适配 | 新增 P2CallContext.request_ref；从该旧字段按本次调用语义转换，核验空值和固定截止规则；不是整包 RequestContext 直接外发。 | 本次调用关联，区别于稳定变更幂等键；查询有自己的调用关联；[召回与P2接口对接需求_V0.1 L453](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:453) |
| P2CallContext.trace_id | Id / 是 | [RequestContext.trace_id](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:14) | 需适配 | 新增 P2CallContext.trace_id；从该旧字段按本次调用语义转换，核验空值和固定截止规则；不是整包 RequestContext 直接外发。 | 调用链追踪，不作为鉴权依据；[召回与P2接口对接需求_V0.1 L454](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:454) |
| P2CallContext.tenant_id | Id / 是 | [RequestContext.tenant_id](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:15) | 需适配 | 新增 P2CallContext.tenant_id；从该旧字段按本次调用语义转换，核验空值和固定截止规则；不是整包 RequestContext 直接外发。 | 已认证租户，与 target/scope 一致；[召回与P2接口对接需求_V0.1 L455](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:455) |
| P2CallContext.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CallContext.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际调用的固定 Provider；[召回与P2接口对接需求_V0.1 L456](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:456) |
| P2CallContext.contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2CallContext.contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 使用的 P2 原协议及适配版本；[召回与P2接口对接需求_V0.1 L457](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:457) |
| P2CallContext.deadline_at | Timestamp / 是 | [RequestContext.deadline](../../../AgentJYS-main/src/aether_agent_memory/runtime/request_context.py:19) | 需适配 | 新增 P2CallContext.deadline_at；从该旧字段按本次调用语义转换，核验空值和固定截止规则；不是整包 RequestContext 直接外发。 | 本次调用剩余期限；写操作超时不代表远端已取消；[召回与P2接口对接需求_V0.1 L458](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:458) |

## P2ResponseContext

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ResponseContext`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ResponseContext.request_ref | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResponseContext.request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 与本次请求关联，可由已确认 RPC 调用关联得到；[召回与P2接口对接需求_V0.1 L466](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:466) |
| P2ResponseContext.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResponseContext.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际响应 Provider，保留路由/服务契约依据；[召回与P2接口对接需求_V0.1 L467](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:467) |
| P2ResponseContext.contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResponseContext.contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 解释原响应所使用的契约版本；[召回与P2接口对接需求_V0.1 L468](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:468) |
| P2ResponseContext.provider_request_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResponseContext.provider_request_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 自有诊断请求 ID；无则 null；[召回与P2接口对接需求_V0.1 L469](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:469) |
| P2ResponseContext.observed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResponseContext.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 提供的观察时间；缺失不使用 A 时间冒充；[召回与P2接口对接需求_V0.1 L470](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:470) |
| P2ResponseContext.error | P2Error? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResponseContext.error`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 失败、部分结果原因或调用错误；正常无错误时 null；[召回与P2接口对接需求_V0.1 L471](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:471) |

## P2Evidence

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2Evidence`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2Evidence.kind | ExternalLabel / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Evidence.kind`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原证据所证明的事实类别，如持久化、存储绑定、索引可见、版本或屏障；示例标签不作为新冻结枚举；[召回与P2接口对接需求_V0.1 L493](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:493) |
| P2Evidence.contract_ref | ContractRef / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Evidence.contract_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 明确该证据证明什么、适用范围及窗口的签收契约；[召回与P2接口对接需求_V0.1 L494](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:494) |
| P2Evidence.provider_evidence_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Evidence.provider_evidence_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 如有可查询证据对象，返回其原引用；无独立对象可 null，此时原完整响应及契约本身须足够证明事实；[召回与P2接口对接需求_V0.1 L495](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:495) |
| P2Evidence.observed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Evidence.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 来源观察时间；无则 null，不伪造；[召回与P2接口对接需求_V0.1 L496](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:496) |

## P2Error

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2Error`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2Error.raw_code | ExternalLabel / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Error.raw_code`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原 P2 错误码或传输状态；[召回与P2接口对接需求_V0.1 L506](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:506) |
| P2Error.category | ExternalLabel / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Error.category`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 映射到下表分类；未知保留 provider_error，不能猜测；[召回与P2接口对接需求_V0.1 L507](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:507) |
| P2Error.message | string / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Error.message`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 脱敏诊断，不含文本、向量、凭证或内部 key；[召回与P2接口对接需求_V0.1 L508](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:508) |
| P2Error.retryable | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Error.retryable`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原协议是否建议重试；不能单独作为写重发许可；[召回与P2接口对接需求_V0.1 L509](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:509) |
| P2Error.retry_after_ms | uint? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Error.retry_after_ms`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | Provider 能提供的重试等待建议；仍受原 deadline/预算限制；[召回与P2接口对接需求_V0.1 L510](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:510) |
| P2Error.effect | no_effect / may_have_effect / unknown / not_applicable / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Error.effect`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 变更副作用是否确定；只读为 not_applicable；no_effect 仍不单独证明未来不会迟到生效；[召回与P2接口对接需求_V0.1 L511](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:511) |
| P2Error.evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2Error.evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 明确拒绝、失败及效果判断的来源依据；[召回与P2接口对接需求_V0.1 L512](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:512) |

## P2ProjectionTarget

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ProjectionTarget`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ProjectionTarget.tenant_id | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionTarget.tenant_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 所属租户，参与目标身份；[召回与P2接口对接需求_V0.1 L684](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:684) |
| P2ProjectionTarget.provider_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionTarget.provider_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 固定目标 Provider；[召回与P2接口对接需求_V0.1 L685](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:685) |
| P2ProjectionTarget.retrieval_space_ref | ContractRef / 是 | [P2GrpcClient](../../../AgentJYS-main/src/aether_agent_memory/p2/client.py:63) | 需契约映射 | 新增 retrieval_space_ref；由固定空间映射解析到 collection。collection 字符串本身不证明模型兼容性或授权。 | 固定检索空间，映射到 collection 等物理空间；[召回与P2接口对接需求_V0.1 L686](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:686) |
| P2ProjectionTarget.identity | ProjectionIdentity / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionTarget.identity`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 完整投影五元组，见下表；[召回与P2接口对接需求_V0.1 L687](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:687) |
| P2ProjectionTarget.physical_target_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionTarget.physical_target_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P2 准确向量对象 ID；尚未分配时可 null，但必须有稳定、唯一、可查证的映射方案；[召回与P2接口对接需求_V0.1 L688](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:688) |

## P2ProjectionPayload

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ProjectionPayload`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ProjectionPayload.usage | Passage / 是 | [InterceptItem.input_type](../../../AgentJYS-main/src/aether_agent_memory/b1/sidecar.py:297) | 需适配 | 新增目标 usage 字段；显式 Query↔query、Passage↔passage 映射。新请求不得依赖旧 input_type 默认 passage；结果从绑定执行回填。 | 从已验证 SemanticEmbeddingResult 取得；[召回与P2接口对接需求_V0.1 L706](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:706) |
| P2ProjectionPayload.vector | List<number> / 是 | [EmbeddingRecord.vector](../../../AgentJYS-main/src/aether_agent_memory/b1/models.py:44) | 可复用值 | 新增目标字段并传真实数值；检索消费 Query，写入消费 Passage；核验 model_binding、维度、有限值及 Proto float 编码兼容性。 | 从 vector_ref 解析的真实向量；具体传输 dtype/字节序按模型与 P2 契约；[召回与P2接口对接需求_V0.1 L707](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:707) |
| P2ProjectionPayload.model_binding | EmbeddingModelBinding / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.model_binding`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已固定实际模型、输出和兼容检索空间；[召回与P2接口对接需求_V0.1 L708](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:708) |
| P2ProjectionPayload.source_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.source_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原 Embedding 输入 UTF-8 字节摘要；不自动等于正文片段摘要；[召回与P2接口对接需求_V0.1 L709](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:709) |
| P2ProjectionPayload.input_binding_digest | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.input_binding_digest`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 片段、来源版本及输入上下文绑定摘要；[召回与P2接口对接需求_V0.1 L710](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:710) |
| P2ProjectionPayload.vector_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.vector_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 按固定 dtype/字节序编码后的向量字节摘要；[召回与P2接口对接需求_V0.1 L711](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:711) |
| P2ProjectionPayload.representation_id | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.representation_id`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准的投影表示关联；[召回与P2接口对接需求_V0.1 L712](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:712) |
| P2ProjectionPayload.content_ref | ExternalId / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.content_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 正式正文引用；[召回与P2接口对接需求_V0.1 L713](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:713) |
| P2ProjectionPayload.content_version | Version / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.content_version`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本投影对应的正文版本；[召回与P2接口对接需求_V0.1 L714](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:714) |
| P2ProjectionPayload.approved_range | ByteRange / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.approved_range`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准的原正文片段范围；转换为 Embedding 输入的证据由 A/B 保留；[召回与P2接口对接需求_V0.1 L715](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:715) |
| P2ProjectionPayload.metadata | P2ProjectionMetadata / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.metadata`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准、P2 需要存储用于检索的属性；[召回与P2接口对接需求_V0.1 L716](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:716) |
| P2ProjectionPayload.metadata_hash | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionPayload.metadata_hash`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对本次 metadata 的 scope、memory_type、occurred_at 规范语义视图重算；等于本地 ProjectionPayload.metadata_hash；[召回与P2接口对接需求_V0.1 L717](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:717) |

## P2ProjectionMetadata

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ProjectionMetadata`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ProjectionMetadata.scope | Scope / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ProjectionMetadata.scope`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准的租户/项目/Agent/会话/任务范围快照；[召回与P2接口对接需求_V0.1 L727](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:727) |
| P2ProjectionMetadata.memory_type | ExternalLabel / 是 | [Memory.type](../../../AgentJYS-main/src/aether_agent_memory/core/memory.py:101) | 需契约映射 | 新增同名字段；working/episodic/semantic 与文档标签明确映射；长期投影仅接受获准 Episodic/Semantic。 | 本版长期类型为 Episodic 或 Semantic；[召回与P2接口对接需求_V0.1 L728](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:728) |
| P2ProjectionMetadata.occurred_at | Timestamp? / 是 | [Memory.created_at](../../../AgentJYS-main/src/aether_agent_memory/core/memory.py:115) | 语义不等价 | 新增 occurred_at: datetime \| None（必填可空），由 B 提供业务发生时间；不可拿 created_at/updated_at 代填。 | B 业务发生时间，未知为 null；不能填本次写库时间；[召回与P2接口对接需求_V0.1 L729](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:729) |

## P2UpsertInput

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2UpsertInput`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2UpsertInput.target | P2ProjectionTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2UpsertInput.target`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 完整租户/Provider/空间/五元组，见 2.3.2；不能只传 memory_id；[召回与P2接口对接需求_V0.1 L747](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:747) |
| P2UpsertInput.provider_idempotency_key | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2UpsertInput.provider_idempotency_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 在调用前固定的 P2 幂等键；同操作重试沿用；[召回与P2接口对接需求_V0.1 L748](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:748) |
| P2UpsertInput.request_fingerprint | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2UpsertInput.request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 数据字典 21.4 的固定请求语义摘要；同键不同摘要拒绝；[召回与P2接口对接需求_V0.1 L749](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:749) |
| P2UpsertInput.payload | P2ProjectionPayload / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2UpsertInput.payload`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际向量、模型、正文映射、元数据及摘要，见 2.3.3；[召回与P2接口对接需求_V0.1 L750](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:750) |
| P2UpsertInput.precondition_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2UpsertInput.precondition_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 若采用条件写/代际屏障，传 P2 契约认可的条件；无该条件方案时为 null，不填写本地租约令牌；[召回与P2接口对接需求_V0.1 L751](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:751) |

## P2MutationResult

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2MutationResult`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2MutationResult.operation_kind | upsert / delete / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.operation_kind`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际变更类型，必须与原请求一致；[召回与P2接口对接需求_V0.1 L761](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:761) |
| P2MutationResult.target | P2ProjectionTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.target`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本操作对应的准确目标；[召回与P2接口对接需求_V0.1 L762](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:762) |
| P2MutationResult.provider_idempotency_key | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.provider_idempotency_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对应原请求的幂等键；仅回显键不能证明已经执行；[召回与P2接口对接需求_V0.1 L763](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:763) |
| P2MutationResult.request_fingerprint | Hash? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P2 已关联的请求语义；尚不能确认关联时为 null，不按本地输入补成已验证；[召回与P2接口对接需求_V0.1 L764](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:764) |
| P2MutationResult.provider_operation_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.provider_operation_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P2 的操作标识；可能尚未取得，此时必须具备按原键查询或等效核验路径；[召回与P2接口对接需求_V0.1 L765](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:765) |
| P2MutationResult.operation_status | P2OperationStatus / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.operation_status`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 本文的事实分类，见下表；与 A 的 ProviderResult 五态不同；[召回与P2接口对接需求_V0.1 L766](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:766) |
| P2MutationResult.raw_status | ExternalLabel? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.raw_status`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原 P2 状态或响应标签；若协议无此字段，保留原响应证据，不编造标签；[召回与P2接口对接需求_V0.1 L767](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:767) |
| P2MutationResult.target_state | P2TargetState? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.target_state`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 当前已取得的对象、载荷及完成性观察，见 2.6 节；受理阶段可 null；[召回与P2接口对接需求_V0.1 L768](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:768) |
| P2MutationResult.resubmit_assessment | P2ResubmitAssessment? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.resubmit_assessment`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 仅在能评估安全重发时返回，见 2.5 节；普通 not_found 不产生肯定结论；[召回与P2接口对接需求_V0.1 L769](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:769) |
| P2MutationResult.evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2MutationResult.evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 此次操作关联及进度的原始依据；见 1.9.2；明确成功/拒绝/失败须有依据；[召回与P2接口对接需求_V0.1 L770](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:770) |

## P2OperationQueryInput

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2OperationQueryInput`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2OperationQueryInput.target | P2ProjectionTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2OperationQueryInput.target`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原操作准确目标，不能换空间或版本；[召回与P2接口对接需求_V0.1 L867](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:867) |
| P2OperationQueryInput.operation_kind | upsert / delete / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2OperationQueryInput.operation_kind`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原动作，区分同目标上的写与删；[召回与P2接口对接需求_V0.1 L868](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:868) |
| P2OperationQueryInput.provider_operation_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2OperationQueryInput.provider_operation_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 已取得时优先使用；首次响应丢失可 null；[召回与P2接口对接需求_V0.1 L869](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:869) |
| P2OperationQueryInput.provider_idempotency_key | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2OperationQueryInput.provider_idempotency_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 原 P2 幂等键；没有 operation_ref 时仍须能够据此核验；[召回与P2接口对接需求_V0.1 L870](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:870) |
| P2OperationQueryInput.request_fingerprint | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2OperationQueryInput.request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 已固定的原载荷语义；防止关联到同键不同请求；[召回与P2接口对接需求_V0.1 L871](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:871) |

## P2ResubmitAssessment

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2ResubmitAssessment`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2ResubmitAssessment.no_effect_confirmed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResubmitAssessment.no_effect_confirmed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否已证明原请求没有产生效果；未知为 null；[召回与P2接口对接需求_V0.1 L883](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:883) |
| P2ResubmitAssessment.no_late_effect_confirmed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResubmitAssessment.no_late_effect_confirmed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否已证明原请求不会在稍后生效；[召回与P2接口对接需求_V0.1 L884](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:884) |
| P2ResubmitAssessment.same_key_resubmit_allowed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResubmitAssessment.same_key_resubmit_allowed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 契约是否允许沿用原键提交同载荷；[召回与P2接口对接需求_V0.1 L885](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:885) |
| P2ResubmitAssessment.idempotency_valid_until | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResubmitAssessment.idempotency_valid_until`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 可证明的原键有效截止；无时间型截止时为 null，由契约解释有效窗口；[召回与P2接口对接需求_V0.1 L886](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:886) |
| P2ResubmitAssessment.evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2ResubmitAssessment.evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 上述判断的权威依据及适用窗口；肯定判断不能只靠普通 not_found；[召回与P2接口对接需求_V0.1 L887](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:887) |

## P2TargetQueryInput

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2TargetQueryInput`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2TargetQueryInput.target | P2ProjectionTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetQueryInput.target`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 完整目标，物理 ID 已知时须与逻辑目标一致；[召回与P2接口对接需求_V0.1 L979](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:979) |
| P2TargetQueryInput.expected_request_fingerprint | Hash? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetQueryInput.expected_request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 要核对的原写入载荷指纹；delete 时可为原 upsert 指纹，不能误用无载荷的 delete 指纹；[召回与P2接口对接需求_V0.1 L980](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:980) |
| P2TargetQueryInput.related_operation_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetQueryInput.related_operation_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 关联待确认的 P2 操作；无引用时可 null；[召回与P2接口对接需求_V0.1 L981](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:981) |
| P2TargetQueryInput.include_payload | bool / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetQueryInput.include_payload`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否需要回读真实载荷；已有充分存储绑定证明时可 false，减少向量传输；[召回与P2接口对接需求_V0.1 L982](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:982) |

## P2TargetState

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2TargetState`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2TargetState.target | P2ProjectionTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.target`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际查验目标；已确定物理 ID 时必须带回对应关系；[召回与P2接口对接需求_V0.1 L988](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:988) |
| P2TargetState.object_present | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.object_present`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 准确对象存在性；权威查无才为 false，无法判断为 null；[召回与P2接口对接需求_V0.1 L989](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:989) |
| P2TargetState.stored_request_fingerprint | Hash? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.stored_request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际存储载荷对应的请求指纹；不是简单回显 expected_request_fingerprint；[召回与P2接口对接需求_V0.1 L990](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:990) |
| P2TargetState.stored_payload | P2ProjectionPayload? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.stored_payload`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 实际回读内容；未请求、对象不存在或无法取得时可 null；[召回与P2接口对接需求_V0.1 L991](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:991) |
| P2TargetState.durable | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.durable`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否满足签收的持久化条件；不等同于“收到请求”；[召回与P2接口对接需求_V0.1 L992](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:992) |
| P2TargetState.index_queryable | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.index_queryable`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否满足目标空间的查询可见性规则；不是保证某条任意 Query 必定命中；[召回与P2接口对接需求_V0.1 L993](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:993) |
| P2TargetState.delete_confirmed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.delete_confirmed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 是否已确认准确目标删除完成；普通写入观察不适用时 null；[召回与P2接口对接需求_V0.1 L994](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:994) |
| P2TargetState.late_write_barrier_confirmed | bool? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.late_write_barrier_confirmed`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 旧在途写是否已被排空或受有效屏障约束；仅本地停止重试不充分；[召回与P2接口对接需求_V0.1 L995](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:995) |
| P2TargetState.binding_evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.binding_evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 对实际向量、模型、Schema、正文映射及过滤属性的绑定证明；[召回与P2接口对接需求_V0.1 L996](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:996) |
| P2TargetState.completion_evidence | List<P2Evidence> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.completion_evidence`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 持久化/索引或删除/屏障达到约定条件的证明；[召回与P2接口对接需求_V0.1 L997](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:997) |
| P2TargetState.observed_at | Timestamp? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2TargetState.observed_at`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P2 能提供的本次状态观察时间；A 收到响应的时间另记；[召回与P2接口对接需求_V0.1 L998](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:998) |

## P2DeleteInput

建议代码位置：`src/aether_agent_memory/p2/contracts.py`；类名：`P2DeleteInput`。

| 文档字段 | 文档类型 / 必填 | 现有代码属性或依据 | 匹配结论 | 代码应增加或修改什么 | 文档定义与来源 |
|---|---|---|---|---|---|
| P2DeleteInput.target | P2ProjectionTarget / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2DeleteInput.target`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | B 批准删除的完整目标，与原 upsert 目标对应；[召回与P2接口对接需求_V0.1 L1069](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:1069) |
| P2DeleteInput.provider_idempotency_key | Id / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2DeleteInput.provider_idempotency_key`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 稳定 delete 幂等键，与 upsert 键区分；[召回与P2接口对接需求_V0.1 L1070](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:1070) |
| P2DeleteInput.request_fingerprint | Hash / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2DeleteInput.request_fingerprint`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | 删除请求语义摘要，不含向量载荷；[召回与P2接口对接需求_V0.1 L1071](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:1071) |
| P2DeleteInput.precondition_ref | ExternalId? / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2DeleteInput.precondition_ref`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | P2 条件删除/代际屏障条件；若采用排空后删除方案可 null，但仍须取得完成证据；[召回与P2接口对接需求_V0.1 L1072](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:1072) |
| P2DeleteInput.related_upsert_keys | List<Id> / 是 | 源码类索引中无同名契约类 | 需新增契约字段 | 在 `p2/contracts.py` 新增 `P2DeleteInput.related_upsert_keys`；按本行定义产生/取得值，原通用 metadata 不计为已实现此强类型契约。 | A 已知的该目标历史在途写关联，供核验；不能仅靠此列表断言不存在其他受影响写入；[召回与P2接口对接需求_V0.1 L1073](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md:1073) |

