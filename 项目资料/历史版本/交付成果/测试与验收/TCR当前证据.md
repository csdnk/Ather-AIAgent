# TCR 验证证据导航与历史结论

最新运行增量见[2026-09-20 Recall验收](../../../../交付成果/测试与验收/Recall流程实现验收_20260920.md)；协作交付见[协作基线验收](../../../../交付成果/测试与验收/协作基线与旧状态修订验收_20260920.md)。下文保持2026-09-19证据快照，不代表当前最新状态，不将新增实现倒填为当时已通过。

验证日期：2026-09-19。对象为本机 AgentJYS-main 工作副本，包含当时未提交与未跟踪代码。本次目录整理不改变已有业务实现和测试结论。

## 已取得的结果

- 264 项测试通过，耗时26.88秒：189项契约测试和75项公共基础、三流程、日志及原生适配测试。
- 72 项 Schema 一致性检查通过。
- P3 核心 Mock 的12项行为检查、9场景37步通过；P2的4场景16步、P3补充2场景8步及页面导航检查通过。
- 同日独立真实 BGE ONNX 验证通过：512维向量、Query/Passage差异、10条推理尝试及对应底层证据、重启后模型绑定。

Mock结果只证明模拟行为。三流程自动测试包含lexical测试适配，真实向量推理另有独立验证。测试数量不等于PRD完成比例；独立验证中的重复适配用例未重复累计。

## 证据与可追溯性

证据编号：TCR-20260919-CORE（契约与本地运行）、TCR-20260919-NATIVE（真实向量）、TCR-20260919-MOCK（页面与模拟场景）。完整用例结果、首次失败、环境配置、文件摘要和截图已保存在独立工作区，可按证据编号复核。

代码实现依据见 [当前实现程度](代码核查_2026-09-19/当前实现程度.md)，业务预期见 [TC目录](../../../../交付成果/测试与验收/TC目录.md)。

## 对应 TC 状态

| TC | 当前结果 | 执行入口/前置 |
|---|---|---|
| TC-P3-01 可靠保存与跨会话 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_write_recall_and_automatic_real_cache` |
| TC-P3-02 保存幂等 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_save_idempotency_same_operation_rejects_different_input` |
| TC-P3-03 伪造向量不能就绪 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_tampered_embedding_never_marks_projection_ready` |
| TC-P3-04 更正与历史结果失效 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_correct_filters_old_vectors_and_invalidates_old_pack` |
| TC-P3-05 删除与清理 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_delete_blocks_old_pack_and_cleans_cache` |
| TC-P3-06 依赖故障状态 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_vector_outage_is_degraded_or_failed_not_fake_empty` |
| TC-P3-07 当前字节预算 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_context_budget_is_measured_and_too_small_fails` |
| TC-P3-08 三流程范围隔离 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_scope_isolation_across_all_flows` |
| TC-P3-09 迟到加工不复活 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_inflight_extraction_cannot_resurrect_deleted_working` |
| TC-P3-10 读取事件去重 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_duplicate_read_events_do_not_double_heat` |
| TC-P3-11 Unknown 原动作核对 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_lost_execution_response_queries_original_action` |
| TC-P3-12 无执行证据保持未知 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_lost_executor_history_stays_unknown` |
| TC-P3-13 重启恢复 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_restart_preserves_context_and_executor_evidence` |
| TC-P3-14 精确正文校验 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_cache_verification_preserves_exact_crlf_bytes` |
| TC-P3-15 归档恢复 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_archive_and_reactivate` |
| TC-P3-17 提取候选有来源 | passed / 本地测试 | `tests/runtime/flows/test_flows.py::test_langmem_adapter_rejects_unsubstantiated_candidate` |
| TC-P3-16 真实向量空间 | 已有独立真实模型验证 | 同日真实模型验证记录 |
| TC-P3-18 真实语义提取 | not_run | 真实 LangMem/模型接入后 |
| TC-P3-19 文档与长文本 | not_run | 文档加工路径补齐后 |
| TC-P3-20 压缩保真 | not_run | 压缩实现及标注集完成后 |
| TC-P3-21 多来源冲突整组 | 仅 Mock S08；后端 not_run | 真实冲突组实现后 |
| TC-P3-22 目标 tokenizer 预算 | not_run | 目标模型/计量口径确认后 |
| TC-P3-23 HTTP 真实演示 | not_run | 薄 HTTP 入口及真实演示适配完成后 |
| TC-P3-24 预测策略后续阶段 | not_run / P1 | 预测阶段，当前不列 G3 阻断 |
| TC-P2-01 对象准确读写与恢复 | not_run / 源码核查 | 真实 P2 E2 服务及约定 P1 |
| TC-P2-02 向量候选与版本 | not_run / 源码核查 | 真实 P2 E1 服务 |
| TC-P2-03 图关系及融合接口 | not_run / 源码核查 | E3 Graph/Fusion 远程入口补齐 |
| TC-P2-04 目标与动作恢复 | not_run / 源码核查 | 正式 Executor/P1，范围待决 |
| TC-INT-01 P2/P3 真实联调 | not_run | P2/P3 固定版本部署与追踪 |
| TC-PERF-01 容量、检索与隔离 | not_run / 当前建设阶段后置 | 目标机器与可复现负载，G4 必须补 |
| TC-OPS-01 备份、回滚与运行 | not_run | 目标部署；RPO/RTO 待确认 |

## 2026-09-19 租户专题补充

见[租户功能实现核查](代码核查_2026-09-19/租户功能实现核查.md)：TEN-CODE-20260919 的 10 项本地专题测试通过；TEN-SHARE-20260919 确认按编号共享读取成功，但默认长期 Recall 返回空，作为待修复缺口保留。TEN-MOCK-20260919 的 33 项检查、11 步租户走查及原有 9 个场景回归通过，仅证明前端模拟。真实 Entra、租户生命周期及 P2 隔离验收仍未完成，TC-TEN-01～08 保持 not_run。

## 结论边界

本地正确性和契约证据已补齐，不代表真实P2/P1联调、真实语义提取/压缩、完整冲突组、目标tokenizer、生产性能、长期恢复或UAT已经通过。P2当前只有源码核查，G1/G2需Review，G3整体未通过，G4/G5未完成。
