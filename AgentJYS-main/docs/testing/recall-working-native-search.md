# AET-34：Working 原生向量检索与禁止跨来源兜底

严格验收入口为 `tests/integration/test_recall_working_native_search.py`，对应
[AET-34](https://linear.app/yu-xiao/issue/AET-34) 与 RC-SRC-01、RC-SRC-06。
旧 AET-13 来源示例和 `test_working_native_vector.py` 的跳过或宽松断言不能替代此入口。

## 已确认的测试边界

1. HTTP Remember、Recall、原 operation/result、recall/result、任务和日志诊断。
2. 实际 Native Query compute、EmbeddingPort、VectorSearchPort 和 Milvus SDK search。

第二个边界由测试进程中的转发包装器观测。每次调用都交给原来的提供方执行，
不构造向量、候选或后端响应，不查询数据库旁路证明结果，也不增加产品测试路由。
ContextVar 将原生计算及 SDK 线程的观测关联到原请求，排除健康检查与后台 Passage 编码。

## 场景与判定

四个场景由 Working Ready / 仅长期 Ready 与 U01 / U06 交叉组成。
U01 只有 READ；U06 只有 READ+DIAGNOSE；夹具维护者另有完整权限。
所有身份属于同一个授权 home scope，避免将权限差异误判为来源差异。

- F1：通过真实 Remember API 准备咖啡、乌龙茶完整正文，等待 Ready，核验确切 Ref、
  version、正文 hash、模型空间和权威 body。先执行茶查询阳性对照，再执行
  “用户的咖啡加糖习惯是什么？”。
- F2：通过真实 Remember 管线产生长期 episode，核验其 Ready、权威 Ref、版本、
  hash、正文和阳性 long_term 查询。通过公开 lifecycle API 归档其 Working 输入，
  不直接改类型或投影数据。再次确认长期条目仍然可用、该 session 没有 active Working。
- Working Ready：结果必须是 F1 的完整原文、确切 Ref 和原始 provenance；coverage
  必须为 working=complete、long_term=not_requested；所有查询搜索均只能调用 Working。
- Working 正常空：选用 F2 的 session，要求正常 empty、完整 Working coverage、零正文、
  零交付条目且长期调用计数为零。允许原始向量搜索命中明确归档的 F2 Working 输入，
  但它不得进入最终结果；其他非预期 Ref 一律失败。
  随后用完全相同 selection 显式 long_term 命中 F2。
- 实际执行：要求 Native Query compute 的模型绑定、输入 hash、输出向量和证据引用，
  与 EmbeddingPort 返回及 Milvus SDK 使用的向量一致；SDK filter 必须在搜索前包含
  Working 来源、模型空间和请求 selection。失败的跨来源调用也计数。
- 结果恢复：读取并比较原 operation/result 与 recall/result。U01 读取维护任务应被拒绝；
  U06 可读取自己原任务与对应 trace 日志。

## 执行

沿用 `tests/azure_test_runtime.py` 的真实 Azure 配置及独占清理机制，使用专用
`p3_test_` PostgreSQL 数据库、独有 schema、Milvus collection 和对象/cache namespace。
必需项包括 `P3_TEST_STATE_DSN`、Redis、Milvus、Ceph 的 `P3_TEST_*` 配置，以及
`P3_TEMPORAL_CLI`（或 PATH 中已固定版本的 Temporal CLI）。

另需 `P3_TEST_NATIVE_CONFIG` 指向实际可加载的模型配置与本地权重，不能使用 injected
embedding。模型、身份文件、pytest 临时目录和执行证据均应位于项目外。

```bash
python -B -m pytest tests/integration/test_recall_working_native_search.py \
  --basetemp=/absolute/external/workspace/AET-34/run-unique \
  -o cache_dir=/absolute/external/workspace/AET-34/pytest-cache
```

AKS 执行可使用既有 `scripts/p3/run_aks_tests.py`，将上述测试路径作为 pytest 参数，
并按 Azure 开发指南配置模型及测试资产。不要向正在使用的部署或共享数据写入夹具。

`P3_RECALL_EVIDENCE_DIR` 可设置外部证据目录，默认使用系统临时目录下的
`aether-workspace-support/AET-34`，每次执行使用独立子目录。证据只保存模型/后端绑定、
原 operation/job/recall/trace 关联、夹具 Ref/hash、输入/向量摘要、实际搜索请求过滤条件、
调用计数和计算证据引用，
不保存凭据、正文或完整向量。

缺少模型、真实后端或独占 Temporal 时报告 `blocked_fixture`，不得以 skip 记为通过。
只有四个真实场景全部通过，才能确认本 ticket 的完整链路验收。
`tests/unit/test_recall_working_search_support.py` 仅验证断言能拒绝伪证据、错误来源、
截断正文和错误 provenance；其通过不能代替真实模型及后端验收。
