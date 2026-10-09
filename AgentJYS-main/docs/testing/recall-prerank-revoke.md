# AET-31：重排前撤权阻断实际模型输入

RC-AUTH-13 位于 `tests/integration/test_recall_prerank_revoke.py`；测试侧契约位于
`tests/recall_prerank_revoke_support.py`。本任务仅实现测试，不宣称真实后端验收通过，
不修改 AET-11/AET-12 或产品授权、RBAC、grant 存储。模型执行中的撤权及最终 guard 竞态留给 CON 组。

## 独立夹具及合法对照

`P3_AUTH13_FIXTURE_FILE` 指向项目外 JSON，顶层为 `base_url` 与 `case`。
case 使用 `PrerankRevokeCase.model_json_schema()`：继承 [RC-AUTH-09 夹具](recall-shared-read.md)，
包括六份独立 Ready 记忆、F1 咖啡/乌龙茶、单对象单 READ 非过期 grant，以及 U01/U08/U09
与隔离维护凭据环境变量名。每次使用全新 scope/tenant、数据副本、run_id 和互不嵌套的外部路径。
`initial_configuration_revision` 为真实身份/grant 配置修订；与 ConfigurationSnapshot.version 区分。
服务设置必须 candidate_limit=1、rerank_policy=required，且实际 reranker_model/revision 已绑定。
后端与模型绑定、源码完整 SHA、镜像 digest、config hash 和精确 Ref/hash 均来自实际部署。

通过现有 HTTP 接口核对 Ready、身份权限枚举、配置、正文和来源/generation；独立重建 owner、
U08 本地和 U09 无 grant 非空对照。Q18 `Identity.provision` 权威回执确认共享 READ 生效后，
U08 必须实际交付精确共享正文。还须取得模型探针的至少一次实际成功调用，核对共享正文的
输入摘要、query hash、模型版本和原 operation/job/recall/trace；不能只检查开启 rerank 的配置。

## B4 控制与模型证据

barrier_directory 为测试侧 IPC：原子发布 `arm.json`，等待 BarrierReceipt 确认按 operation_id
布防且实际模型探针已开启；控制器同时承诺 120 秒自动释放。随后仅提交一次原 Recall。
`b4_export` 原子发布 B4Observation JSON 数组：实际位置必须是完整正文读取后、prerank 授权
复验前，held=true、model_invocations=0；绑定实际 TrustedContext、授权 grant、qualification、
精确 FullBodyReadResult（正文、来源、Ref/hash、body generation/guard）和真实成功 read 事件。
阶段暂停不能由 POST 接纳、固定 sleep 或最后结果推断。

`revoke.json` 仅要求移除本次对象 grant，不禁用用户/tenant、不更换权限/凭据、不改 home scope。
`revoke_export` 的 GrantRevocation 必须绑定 B4 和原请求：真实新 configuration_revision、
submitted/committed/effective/observed 时刻、实际空 grant 集、未变 Principals，以及真实 U08
对同一精确 target 的 excluded qualification。只有回执有效且原 deadline 未到才发布 `release.json`。

`model_export` 为实际模型调用入口的 ModelObservation 数组，按原 job_id 唯一查找。
模型边界的规范化名称为 `reranker.model.invoke`，须由 q06_mapping_id 明确映射到真实 provider
执行入口（当前 CrossEncoder 的实际 predict 调用），不能只观测候选列表、预期输入或过滤后的日志。
探针按原 operation/job/recall/trace 包含所有批次、重试及失败调用；在调用前捕获实际完整文档字符串
（模型入口、tokenization 前），仅导出 SHA-256，不输出正文。ModelDocument.memory_keys 为对应
`MemoryRef.model_dump_json()` 的完整序列化字符串，body_hashes 为源正文摘要，document_hash 为
实际传入字符串的 SHA-256；不能从 Ref 反推实际输入。query_hash 为实际调用 query 的摘要。
探针必须覆盖请求 admitted_at 之前至原任务 terminal_at，终态后封存 complete/task_terminal
窗口，不能把缺探针或半份空记录当作零调用。未撤权对照使用同一探针/模型映射，证明探针可见性。

受阻原请求的全部模型调用禁止共享 Ref、共享 body hash 或共享正文字符串摘要，其他文档也必须
精确匹配合法 U08 本地正文。零调用只有在完整封存窗口、真实当前授权复验和原任务终态齐全时有效。
这样即使最后结果为空，也能检测已撤权正文实际进入模型的错误。

## 原请求结果、事件与清理

`final_export` 的 PrerankFinal 绑定同一 job/recall/trace、B4 与 revoke evidence，证明释放发生在
撤权回执之后，prerank 当前权限检查在原 deadline 之前并明确 excluded 共享 target。
检查原 job/Record、两条原 result HTTP 路径及完整 Outbox/事件，不用新 Recall 替代原结果。
允许原任务 FORBIDDEN/RESULT_INVALIDATED 失败而无 Pack/result_ref，或完成不含撤权对象的
合法完整结果；后者还要求非降级、Working coverage complete。普通响应与头部不得泄漏受限 ID/正文。
最终 Pack 不作为模型安全的唯一证据。已真实发生的 read 事件须原样保留，撤权对象不能出现
成功 packed/delivered。模型窗口和最终事件不能由 exporter 筛掉泄漏调用或事件。

`finally` 在断言/HTTP/回执失败时也原子发布 `cleanup.json`，释放所有本次屏障、drain/cancel
原请求并移除测试 grant。PrerankCleanup 回执必须证明 released/request_drained、配置修订不倒退、
身份未改变且实际 grant 集为空。barrier_receipts 为 BarrierReceipt/PrerankCleanup JSON 数组。
控制器仅操作本次 test-owned 部署；本切片不实现控制器、模型探针或维护 exporter，不猜产品 API。

Ready 等待 60 秒、HTTP/每份回执 30 秒、原 job 终态 120 秒，均有界条件轮询。
缺 B4/Q18/模型探针或回执记 blocked_fixture，缺 Q06 政策/证据映射及最终视图记 blocked_requirement；
受阻完整用例失败，不记 passed。原始受限证据仅在项目外合法受控维护目录保存。
脱敏摘要在外部 P3_RECALL_EVIDENCE_DIR（默认系统临时工作区 AET-31 UUID 目录），只写部署绑定、
Ref/hash、证据编号、关联 ID 和时间，不写凭据/正文。控制文件使用 0600，拒绝覆盖旧文件。

```bash
python -B -m pytest tests/unit/test_recall_prerank_revoke_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_prerank_revoke.py -p no:cacheprovider --basetemp=<external-temp>
```

严格辅助校验使用构造证据/故障变体，和真实后端执行分别统计，不能代替 live 安全验收。
