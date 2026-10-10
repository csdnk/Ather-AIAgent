# AET-37：auto 按会话和任务上下文确定来源

对应 [AET-37](https://linear.app/yu-xiao/issue/AET-37) 与 RC-SRC-04、RC-SRC-05。
本次只实现测试及说明，未运行用例，不认定真实验收通过。

## 用例入口

`tests/integration/test_recall_auto_native_sources.py` 复用
[原生 Working 测试的配置及隔离机制](recall-working-native-search.md)，使用真实 Remember、
Native Query 编码、Azure 后端和独占 Temporal。夹具通过公开 Remember 接口在相同
session/task scope 创建咖啡与乌龙茶，等待 Working 和长期条目均 Ready，核验确切
Ref、版本、模型空间、完整权威正文和来源。即使请求不带上下文，这些近期 Working
数据仍真实存在，不能将不存在 Working 误当作来源选择正确。

U01（READ）与 U06（READ+DIAGNOSE）分别覆盖四种独立请求：

| 请求上下文 | 精确 selected_sources | Working coverage |
| --- | --- | --- |
| 仅 session | `[working, long_term]` | complete |
| 仅 task | `[working, long_term]` | complete |
| session 与 task | `[working, long_term]` | complete |
| 两者皆无 | `[long_term]` | not_requested |

不接受未解析的 `auto`。逐路核验原操作下的 Native compute、EmbeddingPort、
VectorSearchPort 和 SDK 实际执行及 scope/source/model 过滤；无上下文的 Working
端口尝试必须为零。比较原 operation/result 与 recall/result，核验最终 Ref、原文和
provenance，U01 的任务及 trace 诊断必须被拒绝。

## 选择原因与策略的追溯

U06 通过公开 `Diagnostics.trace` 读取原请求的 durable_facts，而非直接查数据库。
当前夹具使用 GenerationRecall/GenerationStages，discover 的 details 为空，没有旧
Recall 流程的 load 阶段。基础用例只按真实契约核验 discover/assemble 的原记录关联、
发生顺序与 `subject.scope.session_id/task_id`，并交叉核验原结果与真实执行。

当前契约没有单独的 auto 原因码，也未在阶段 details 中保存来源计划或策略版本。测试用
持久化 scope 作为选择的可追溯输入，以 `subject.object_id/recall_id` 关联原结果中的
`policy_version`，并精确比较服务实际策略版本。证据中的 variant/basis 是测试场景标签，
不是产品原因码。这种关联追溯不能代替 ticket 所需的持久化来源计划。

单独的 `test_auto_diagnostics_persist_resolved_plan_with_causal_scope_and_policy` 保留该
需求回归：要求 discover.details 持久化与公开 RecallPlanRequest 对齐的 `sources` 和
`policy_version`，配合已记录的 scope 解释选择原因。这两个诊断字段是待实现能力，
不是当前生成流程已有字段；缺失时显式失败并记录 blocked_requirement，不能 skip 或
以基础执行用例代替。本次不修改产品来实现该扩展，也不虚构原因枚举。

## 命中数量与故障对照

有上下文的三个变体分别控制 Working 或长期路线，并为每次对照创建新的 operation：

- empty：先转发真实 ANN，再清空指定路线的候选；要求该路线原本确有命中。
  最终只交付另一来源，但 selected_sources 保持双路，两路 coverage 仍为 complete。
- unavailable：在指定操作的 VectorSearchPort 抛出实际契约中的
  `DEPENDENCY_UNAVAILABLE`。失败路线只记录端口尝试，SDK 成功调用数为零；另一条路线
  保持真实执行。结果必须保留双路选择，并显示对应 unavailable coverage、degraded
  outcome 及生成流程的精确 `working_dependency` / `long_term_dependency` 退化原因，
  不能以改写为单来源掩盖故障。

`AutoSearchControl` 不控制来源解析、Remember、授权、组包或结果持久化。单测
`tests/unit/test_recall_auto_sources_support.py` 验证证据断言拒绝未解析 auto、错误 scope、
策略、来源计划、原记录关联和 Working 越界调用；这些不替代真实链路验收。

```bash
python -B -m pytest tests/integration/test_recall_auto_native_sources.py \
  tests/unit/test_recall_auto_sources_support.py \
  --basetemp=/absolute/external/workspace/AET-37/run-unique \
  -o cache_dir=/absolute/external/workspace/AET-37/pytest-cache
```

执行证据默认保存在项目外系统临时目录的 `aether-workspace-support/AET-37`，可由
`P3_RECALL_EVIDENCE_DIR` 指定。只持久化 Ref/hash、上下文、来源/coverage、原操作关联、
模型/策略绑定及调用摘要，不保存正文、完整向量或凭据。缺环境记录 blocked_fixture，
skip 不能计为通过。
