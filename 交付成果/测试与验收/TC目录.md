# TC 目录与执行方法

版本：2026-09-19 评审稿。测试计划和运行结果分开维护。自动化用例使用现有夹具数据；下述业务例子便于走查，实际自动脚本的文字样本以源码为准。

新增专题：[租户与身份接入测试及回归方案](租户与身份接入测试及回归方案.md)，包含 TC-TEN-01～08。以下原有 TC 编号和结果保留，新增方案均为 not_run。

所有自动化项前置：Python 3.13 测试环境、当前工作树、独立临时库和目录。原生模型项使用独立模型验证，不把 lexical 测试当真实向量验证。P2/集成项必须使用真实服务。

## TC-P3-01 可靠保存与跨会话

- 数据/前置：预算20万元，每周五汇报。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：保存，当前会话读取，运行后台，再跨会话召回。
- 预期：saved 与 ready 分开，持久正文可读，长期召回只用就绪条目。
- 证据入口：tests/runtime/flows/test_flows.py::test_write_recall_and_automatic_real_cache。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-02 保存幂等

- 数据/前置：相同 operation ID，两次同文一次异文。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：重复保存，再以原 ID 提交预算15万元。
- 预期：同文不重复建事实，异文拒绝且原事实不变。
- 证据入口：tests/runtime/flows/test_flows.py::test_save_idempotency_same_operation_rejects_different_input。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-03 伪造向量不能就绪

- 数据/前置：待投影记忆及被篡改 embedding。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：篡改输入绑定后运行投影。
- 预期：不得标 ready，失败证据可定位。
- 证据入口：tests/runtime/flows/test_flows.py::test_tampered_embedding_never_marks_projection_ready。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-04 更正与历史结果失效

- 数据/前置：先保存预算20万元并生成旧 Context。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：更正为15万元，召回并重放旧 Context。
- 预期：仅有效新版可交付，旧结果重放被阻断。
- 证据入口：tests/runtime/flows/test_flows.py::test_correct_filters_old_vectors_and_invalidates_old_pack。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-05 删除与清理

- 数据/前置：已索引记忆及已生成 Context。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：删除，召回，重放并执行缓存清理。
- 预期：不再交付旧正文，旧结果失效，缓存清理有结果。
- 证据入口：tests/runtime/flows/test_flows.py::test_delete_blocks_old_pack_and_cleans_cache。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-06 依赖故障状态

- 数据/前置：存在长期记忆，向量服务故障。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：在依赖故障下执行统一 Recall。
- 预期：返回降级或不可用，不能伪装正常空。
- 证据入口：tests/runtime/flows/test_flows.py::test_vector_outage_is_degraded_or_failed_not_fake_empty。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-07 当前 tokenizer 预算（2026-09-20修订）

- 数据/前置：含中文的完整记忆，充足与极小预算。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：分别请求两档预算。
- 预期：使用配置的tokenizer计算完整rendered_context，记录tokenizer_id与tokens_used；包括引用和换行，放不下完整条目时明确失败。旧字节预算断言不再用于当前实现。
- 证据入口：tests/runtime/flows/test_flows.py::test_context_budget_is_measured_and_too_small_fails，以及test_recall_pipeline.py的真实token计数用例。
- 当前：本地测试通过，见[2026-09-20验收RE-01/RE-06](Recall流程实现验收_20260920.md)；下游目标模型专门校准仍属于TC-P3-22。

## TC-P3-08 三流程范围隔离

- 数据/前置：不同用户、租户和授权资源。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：跨范围保存、读取、动作访问。
- 预期：未授权行为拒绝，不交付外域正文。
- 证据入口：tests/runtime/flows/test_flows.py::test_scope_isolation_across_all_flows。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-09 迟到加工不复活

- 数据/前置：已保存 Working 且提取任务在途。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：删除来源后恢复旧加工。
- 预期：派生结果不能恢复已删除事实。
- 证据入口：tests/runtime/flows/test_flows.py::test_inflight_extraction_cannot_resurrect_deleted_working。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-10 读取事件去重

- 数据/前置：同一读取事件投递两次。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：消费事件并检查 hotness/动作。
- 预期：重复事件不重复累计热度。
- 证据入口：tests/runtime/flows/test_flows.py::test_duplicate_read_events_do_not_double_heat。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-11 Unknown 原动作核对

- 数据/前置：执行完成但响应丢失。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：触发动作，按原 action ID reconcile。
- 预期：查询原动作，不新增重复动作，核验效果后确认。
- 证据入口：tests/runtime/flows/test_flows.py::test_lost_execution_response_queries_original_action。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-12 无执行证据保持未知

- 数据/前置：Unknown 动作且执行历史丢失。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：再次核对动作。
- 预期：维持 Unknown，不假报成功。
- 证据入口：tests/runtime/flows/test_flows.py::test_lost_executor_history_stays_unknown。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-13 重启恢复

- 数据/前置：已经形成 Context 与执行收据。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：关闭并重建 Host，查询旧记录。
- 预期：Context 和执行证据重启后仍可复核。
- 证据入口：tests/runtime/flows/test_flows.py::test_restart_preserves_context_and_executor_evidence。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-14 精确正文校验

- 数据/前置：包含 CRLF 的原始正文。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：本地缓存复制并核验。
- 预期：按准确字节保留正文，不把换行变化忽略。
- 证据入口：tests/runtime/flows/test_flows.py::test_cache_verification_preserves_exact_crlf_bytes。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-15 归档恢复

- 数据/前置：有效记忆。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：归档，查询，再恢复查询。
- 预期：归档与恢复符合生命周期资格，状态可追踪。
- 证据入口：tests/runtime/flows/test_flows.py::test_archive_and_reactivate。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-17 提取候选有来源

- 数据/前置：无来源支撑的候选事实。
- 环境：新 ThreeFlows 测试夹具，临时 SQLite/目录，lexical 测试适配；非真实 Provider。
- 操作：通过 LangMem 适配器提交候选。
- 预期：拒绝无来源支撑候选，不代表真实模型质量已测。
- 证据入口：tests/runtime/flows/test_flows.py::test_langmem_adapter_rejects_unsubstantiated_candidate。
- 当前：passed / 本地测试，见 TCR-20260919-CORE；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-16 真实向量空间

- 数据/前置：BGE-small-zh-v1.5 本机缓存，同文 Query/Passage。
- 环境：TCR-20260919-NATIVE，原始记录已独立归档。
- 操作：运行真实 native 验证脚本并重启绑定模型。
- 预期：512 维真实推理、模式差异、输入与模型绑定、完整流程可查。
- 证据入口：TCR-20260919-NATIVE，原始记录已独立归档。
- 当前：已有独立真实模型验证；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-18 真实语义提取

- 数据/前置：含20→15更正、多来源与否定句的人工标注集。
- 环境：真实 LangMem/模型接入后。
- 操作：用真实提取模型生成事实并核对出处。
- 预期：不编造事实、更正关系正确；质量阈值先共同确认。
- 证据入口：真实 LangMem/模型接入后。
- 当前：not_run；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-19 文档与长文本

- 数据/前置：带章节、页码、来源版本的长文档。
- 环境：文档加工路径补齐后。
- 操作：上传、拆分、加工、跨会话召回来源片段。
- 预期：来源可追踪，失败可重试，未完成加工不误报全就绪。
- 证据入口：文档加工路径补齐后。
- 当前：not_run；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-20 压缩保真

- 数据/前置：含数值、否定、时间和冲突的长记忆集。
- 环境：压缩实现及标注集完成后。
- 操作：压缩后与原文逐项核对并检索。
- 预期：关键约束不丢，压缩率和质量按确认口径评估。
- 证据入口：压缩实现及标注集完成后。
- 当前：not_run；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-21 多来源冲突整组

- 数据/前置：来源A预算20万，B预算15万，C每周五汇报。
- 环境：真实冲突组实现后。
- 操作：充足预算与不足以容纳冲突组两档召回。
- 预期：冲突作为整体保留或跳过，不能只留一方；可继续选择后组。
- 证据入口：真实冲突组实现后。
- 当前：仅 Mock S08；后端 not_run；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-22 目标 tokenizer 预算

- 数据/前置：中文、英文、表情混合上下文，指定目标模型。
- 环境：目标模型/计量口径确认后。
- 操作：用目标 tokenizer 测量最终 Context。
- 预期：实际 token 不超 budget，完整组策略与截断状态可解释。
- 证据入口：目标模型/计量口径确认后。
- 当前：not_run；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-23 HTTP 真实演示

- 数据/前置：两个会话、同一后端持久库。
- 环境：薄 HTTP 入口及真实演示适配完成后。
- 操作：通过页面或 HTTP 保存更正删除，刷新后查询 Trace。
- 预期：页面结果来自新 Host，刷新仍持久，显示真实失败。
- 证据入口：薄 HTTP 入口及真实演示适配完成后。
- 当前：not_run；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P3-24 预测策略后续阶段

- 数据/前置：独立训练/验证时段及启发式基线。
- 环境：预测阶段，当前不列 G3 阻断。
- 操作：离线回放，再 shadow 验证和故障回退。
- 预期：无时间泄漏，比较真实收益；阈值按后续阶段确认。
- 证据入口：预测阶段，当前不列 G3 阻断。
- 当前：not_run / P1；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P2-01 对象准确读写与恢复

- 数据/前置：tenant-a 的 object-v1 与 object-v2，已知正文摘要。
- 环境：真实 P2 E2 服务及约定 P1。
- 操作：写入、指定版本读取、重复请求、崩溃重启、跨域读取。
- 预期：摘要与版本准确；重复不破坏正文；恢复后可读；跨域拒绝。
- 证据入口：真实 P2 E2 服务及约定 P1。
- 当前：not_run / 源码核查；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P2-02 向量候选与版本

- 数据/前置：两个租户的512维向量与对象引用；一条错维向量。
- 环境：真实 P2 E1 服务。
- 操作：建集合、写入、等索引、过滤检索、更正删除后重查。
- 预期：维度错误拒绝，候选带稳定版本引用，过滤与可见性符合契约。
- 证据入口：真实 P2 E1 服务。
- 当前：not_run / 源码核查；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P2-03 图关系及融合接口

- 数据/前置：会议来源节点、对象版本节点、关系边。
- 环境：E3 Graph/Fusion 远程入口补齐。
- 操作：创建、遍历、向量图关联，再删除来源并查询。
- 预期：关联同一版本；缺失引用显式处理；远程服务可调用。
- 证据入口：E3 Graph/Fusion 远程入口补齐。
- 当前：not_run / 源码核查；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-P2-04 目标与动作恢复

- 数据/前置：目标版本、路由版本及唯一 action ID。
- 环境：正式 Executor/P1，范围待决。
- 操作：提交、丢失回执、查询原动作、重启、注入旧回执。
- 预期：受理不等于成功；目标可读和版本匹配才确认；不重复迁移。
- 证据入口：正式 Executor/P1，范围待决。
- 当前：not_run / 源码核查；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-INT-01 P2/P3 真实联调

- 数据/前置：跨会话记忆、相同来源对象/向量/图版本。
- 环境：P2/P3 固定版本部署与追踪。
- 操作：真实写入投影召回；依次注入写超时、向量失效、删除、重启。
- 预期：端到端引用/版本/授权一致，Unknown 可核对，故障不假报完整。
- 证据入口：P2/P3 固定版本部署与追踪。
- 当前：not_run；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-PERF-01 容量、检索与隔离

- 数据/前置：经确认的规模、并发、文档长度及冷热分布。
- 环境：目标机器与可复现负载，G4 必须补。
- 操作：分别测前台/后台/混合负载，再注入积压与恢复。
- 预期：记录 p50/p95/p99、吞吐、错误率、资源、召回质量；阈值冻结后判断。
- 证据入口：目标机器与可复现负载，G4 必须补。
- 当前：not_run / 当前建设阶段后置；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。

## TC-OPS-01 备份、回滚与运行

- 数据/前置：候选发布版本、备份集、故障脚本。
- 环境：目标部署；RPO/RTO 待确认。
- 操作：备份恢复、发布失败回滚、告警到故障定位。
- 预期：恢复目标内保持数据/权限正确，回滚有执行记录和责任接手。
- 证据入口：目标部署；RPO/RTO 待确认。
- 当前：not_run；最终以 [TCR](../../项目资料/历史版本/交付成果/测试与验收/TCR当前证据.md) 与运行附件为准。
