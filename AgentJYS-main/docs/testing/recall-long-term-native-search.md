# AET-35：长期来源独立检索与过滤边界

严格验收入口为 `tests/integration/test_recall_long_term_native_search.py`，对应
[AET-35](https://linear.app/yu-xiao/issue/AET-35) 与 RC-SRC-02。
测试复用 [AET-34 的真实运行配置与观测边界](recall-working-native-search.md)。

## 场景与证据

六个场景由 U01（READ）、U06（READ+DIAGNOSE）与三种高分干扰项交叉组成。
夹具维护者通过真实 Remember 准备 F1 Working 咖啡和乌龙茶，以及 F2 长期条目：
“用户每周二上午参加项目评审；遇节假日顺延。”所有条目均等待 Ready，核验权威正文、
确切 Ref、版本、hash、模型空间和来源。

显式 `long_term` 查询“项目评审通常安排在什么时候？”，服务端 `candidate_limit=1`。
要求最终完整返回 F2 原文及 provenance，包含周二上午与节假日顺延，只有长期来源贡献，
Working 搜索调用为零。原 operation/result 与 recall/result 必须一致，U01 不能读取维护
诊断，U06 可以读取自身原任务及 trace 日志。

服务端候选 K 与 Milvus 生成检索的发现上限是不同限制，两者分别记录。为直接证明
后端过滤先于截断，使用同一真实 Native Query 向量和同一独占 collection，增加实际
VectorSearchPort 的 `limit=1` 对照，不把 HTTP 的发现请求描述为 SDK K=1：

1. 从真实 Remember 已发布的单 chunk 中读取合法 target；干扰行使用实际查询向量，
   作为明确的测试索引污染写入该测试独占 collection，不覆盖原投影或权威 body。
2. 三类干扰分别为同 home 的其他 session、错误 model_space、同 session 的 Working。
   每类只改变被检验的过滤维度，保留真实权威 Ref/body hash/generation。
3. 实际 SDK 对合法行与干扰行作双行对照：干扰得分必须严格高于合法得分，
   未作业务过滤的 Top-1 必须返回干扰行。
4. 实际 VectorSearchPort 以 `limit=1` 搜索；观测 SDK 请求中的全局 scope、模型空间和
   来源过滤，以及 SDK 原始返回，要求只命中合法行。仅在 Python 层事后过滤不能通过。
5. 随后重新执行真实 HTTP Recall，完整 F2 仍必须命中；其实际 Query 向量须与对照一致。

所有 SDK 与 Native 调用均转发给原提供方，未替换计算结果或搜索命中。
压力行只写入 `azure_test_runtime` 登记的 test-owned provider，完成后删除；整个独占
collection 仍由运行时夹具清理。若真实得分没有形成严格压力，该场景失败，不能当作通过。

## 执行与限制

配置要求见 AET-34 运行说明，包括 `P3_TEST_NATIVE_CONFIG`、真实 Azure 后端、专用
PostgreSQL 数据库及 test-owned Temporal。所有模型、临时目录与证据放项目外。

```bash
python -B -m pytest tests/integration/test_recall_long_term_native_search.py \
  --basetemp=/absolute/external/workspace/AET-35/run-unique \
  -o cache_dir=/absolute/external/workspace/AET-35/pytest-cache
```

`P3_RECALL_EVIDENCE_DIR` 可指定外部目录，默认系统临时目录下
`aether-workspace-support/AET-35`，每次执行独立子目录。证据记录模型/后端绑定、
Ref/hash、operation/job/recall/trace、实际过滤表达式、发现上限、调用数、SDK 原始得分
摘要及向量 hash，不保存凭据、正文或完整向量。

缺少模型或真实环境时以 `blocked_fixture` 失败，skip 不能算通过。只有六个真实场景
全部通过才能确认完整链路验收。单测 `tests/unit/test_recall_long_term_search_support.py`
仅验证断言能拒绝伪压力、错误 K、过滤绕过或其他向量/collection，不能替代真实验收。
