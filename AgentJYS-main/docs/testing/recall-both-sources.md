# AET-36：both 双路执行、来源贡献与精确 Ref 去重

对应 [AET-36](https://linear.app/yu-xiao/issue/AET-36) 与 RC-SRC-03。
本次只实现用例，不以运行通过为交付条件，未执行用例或认定真实验收通过。

## 真实双路入口

`tests/integration/test_recall_both_native_search.py` 通过真实 Remember 准备 F1 咖啡、
乌龙茶及 F2 项目评审完整正文，等待 Working 和长期投影均 Ready。分别以 U01 READ、
U06 READ+DIAGNOSE 请求 `sources=both`，禁重排、预算充足。

逐路记录实际 Query compute、EmbeddingPort、VectorSearchPort 及 Milvus SDK 的请求、
来源过滤、模型绑定、向量摘要和候选 Ref，要求两路都成功执行，最终包只能包含这些
确切权威 Ref 和完整原文。selected_sources 与双路 complete coverage 必须一致。
每条结果的原 SourceRef/provenance 保持不变，不将文档 SourceRef 当作检索路线标签。
原 operation/result 与 recall/result 必须一致；U01 诊断应被拒绝，U06 可读自身任务及日志。
真实 ANN 用例不要求 F3 固定排序。

沿用 [原生 Working 测试运行配置](recall-working-native-search.md)，证据默认在项目外
系统临时目录的 `aether-workspace-support/AET-36`，可用 `P3_RECALL_EVIDENCE_DIR` 指定。
缺环境记录 blocked_fixture；skip 不能当作通过。

## 受控融合入口

`tests/runtime/flows/test_recall_both_fusion.py` 在已确认的 MemoryCandidatePort 与
ContextAssemblyPort 边界测试，复用真实 RF 身份、事务及组包器。
受控场景也分别使用 U01 READ 与 U06 READ+DIAGNOSE。
`tests/recall_both_sources_support.py` 的 `FixedCandidateRoutes` 是可复用受控基线：
Working=[A v2,B v1]、长期=[C v1,A v2]，同 scope、合法完整候选，独立记录实际调用。
`tests/recall_fusion_fixture.py` 明确提供 B 契约替身，以精确 Ref 保存独立正文、来源、
发布清单和 guard；它不能证明真实 Remember 的发布或授权行为。

- A v2 只入包一次，来源排名 Working=1、长期=2 均保留；B、C 保留各自来源贡献。
- 检查独立给定的 RRF 单路贡献常量、完整正文、原始 provenance 和充足预算。
- B、C 正文故意相同，仍必须保留两个不同 Ref 及各自来源。
- A v1 与 A v2 在不同路线作为合法候选输入时，要求按精确 Ref 区分，不错误融合。
  当前 `ContextAssemblyPlan` 契约还限制同 scope/memory_id 只能出现一次；此用例保留
  ticket 的精确 Ref 期望，用于暴露该语义冲突，本次不改产品契约或放宽断言。
- 分别让任一路 unavailable，只有一路成功不得宣称完整 both；计划必须有相应退化原因，
  unavailable 路线不得产生贡献。

单测 `tests/unit/test_recall_both_sources_support.py` 检查证据断言能拒绝缺路、失败、错误
trace、缺 SDK、过滤绕过、错误版本/来源及 coverage。单测与 F3 受控用例均不替代真实链路。

```bash
python -B -m pytest tests/integration/test_recall_both_native_search.py \
  tests/runtime/flows/test_recall_both_fusion.py \
  tests/unit/test_recall_both_sources_support.py \
  --basetemp=/absolute/external/workspace/AET-36/run-unique \
  -o cache_dir=/absolute/external/workspace/AET-36/pytest-cache
```
