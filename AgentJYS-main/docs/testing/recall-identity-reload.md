# AET-30：组包后撤销 READ，阻断原请求最终提交

RC-AUTH-12 的独立实现为 `tests/integration/test_recall_identity_reload.py`，
辅助证据契约为 `tests/recall_identity_reload_support.py`。这是测试用例实现，不声明真实后端通过。
不修改 AET-11/AET-12、产品认证/RBAC/grant 存储或父 issue。

`P3_AUTH12_FIXTURE_FILE` 指向项目外 JSON，顶层为 `base_url` 和 `case`。
`case` 按 `IdentityReloadCase.model_json_schema()` 准备：两个 actors 均为 U01，
同一 READ 身份、精确 scope/session、独立 memory_id，正文分别为 F1 咖啡/乌龙茶。
actors 的来源 Ref、版本、正文 hash、body/projection generation 与 model_space 均来自真实维护观察。
每次执行使用独立 tenant/scope、数据副本、run_id 和全新 IPC/export 路径；不读取其他测试的 Pack。
普通和合法维护凭据分离，夹具只存环境变量名，不存凭据。

1. 通过真实 `/p3/readyz` 与维护接口核对配置、身份、两份 active Working Ready 记忆及精确正文。
2. 使用原 U01 凭据完成合法非空对照，关联原 operation/job/recall/trace，核对真实组包、
   final_guard、成功条件提交、read/packed 事件与用户返回 Pack。
3. 向外部控制目录原子发布 `arm.json`，等待绑定本次 operation 的 armed 回执。
   提交第二个 Recall 后，等待实际 B5 held 观察：位置严格为组包完成、final_guard 前，
   同时取得实际 ContextAssemblyPlan、真实 read 事件、零 final_guard/成功提交次数及原 TrustedContext。
   此时尚无 ContextPack；逐一核对计划中的完整正文、来源、Ref/hash、generation 和旧授权 GuardStamp。
   POST 接纳或超时不能替代屏障证据。
4. 发布 `revoke.json`，仅移除 U01 READ、推进 auth_epoch 和身份文件 revision，保留凭据、scope、
   其他权限及其他身份/租户/grants/JWT 配置。等待 Q18 权威提交和全部 HTTP/worker 实例重载回执。
   回执必须绑定 B5/原请求、当前 Principal、同一 credential hash、文档 hash、新 revision、
   committed/effective/applied/observed 时间，且早于原请求 deadline。
5. 只有验证重载回执后才发布 `release.json`。通过合法 operator 轮询原 job 终态，
   使用原 U01 凭据读取原 operation/result 和 recall/result，均要求 FORBIDDEN 403 且无数据泄漏。
   Q06 最终维护观察须证明原请求 final_guard/RF 读取新身份、FORBIDDEN 失败、原 Record 失败、
   无结果引用、无持久化 Pack、零成功提交，完整核对条件提交计数及 Outbox/事件。
   组包前真实成功的 read 必须保留，撤权材料不得有成功 packed/delivered。
6. `finally` 无论断言、HTTP 或回执是否失败均发布 `cleanup.json`，要求释放所有本次屏障、
   drain/cancel 原请求、恢复隔离测试身份，并等待 cleanup 回执；恢复也必须使用更高 revision/epoch。
   控制器必须提供 120 秒自动释放兜底，不能由测试进程存活保证释放。

控制目录文件是测试侧 IPC，不是产品 API。可信部署控制器只能操作本次 test-owned 部署；
本任务不实现控制器或伪造 exporter。三个 export 文件均为原子发布的 JSON 数组，
按 job_id 或 control_operation_id 查找唯一回执。`maintenance_export` 使用 AssemblyObservation，
`receipt_export` 使用 ArmedReceipt / ReadRevocationReceipt / CleanupReceipt，
`final_export` 使用 FinalObservation。完整 schema 可从对应 Pydantic 模型导出到项目外工作区。
AssemblyObservation 的 control 阶段提供实际成功 pack（plan 可为 null）；B5 阶段提供实际 plan、
pack 必须为 null。维护视图中的计划/Pack/正文/身份回执应存项目外受控目录；控制命令使用 0600 文件。

`unchanged_identity_state_hash` 为排除目标 U01 principal 的 permissions/auth_epoch 后，
完整身份配置（包括凭据、其他身份、tenants、grants、JWT 配置）的规范化 JSON SHA-256。
exporter 必须对真实变更前后文档计算，而非回显控制命令。
`identity_instances` 必须列出实际接纳、执行和最终提交涉及的全部实例。
Q06 的 `events` 与 `outbox_events` 是本次原请求的完整事件集；不得筛掉失败或泄漏事件。
`persisted_pack` 必须来自实际持久化观察；`conditional_commit_attempts` 记录真实次数，
允许在 final_guard 已失败时为零，`successful_commits` 必须为零。

Ready 等待最长 60 秒；回执最长 30 秒；原 job 终态最长 120 秒；HTTP 超时 30 秒。
均为有界条件等待，不固定 sleep 猜测阶段。缺 B5/Q18/实例重载夹具记 `blocked_fixture`；
缺 Q06 契约记 `blocked_requirement`；受阻完整用例失败，不记 passed。
脱敏摘要在外部 `P3_RECALL_EVIDENCE_DIR`（默认系统临时工作区的 AET-30 UUID 目录），
只保留部署绑定、精确 Ref/hash、关联 ID、时间和证据编号，不保存正文或凭据。

```bash
python -B -m pytest tests/unit/test_recall_identity_reload_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_identity_reload.py -p no:cacheprovider --basetemp=<external-temp>
```

严格契约辅助校验使用构造证据与错误变体，单独统计，不能替代真实后端验收。
