# AET-29：租户生命周期与旧 epoch 失效

RC-AUTH-11 的 live 入口是 `tests/integration/test_recall_tenant_lifecycle.py`，辅助断言入口为
`tests/unit/test_recall_tenant_lifecycle_support.py`。本交付实现测试，不声明真实后端安全验收通过。
Owner 为 A（Recall 消费者测试）；不修改身份认证、RBAC、grant 存储或产品策略。

| 独立变体 | 停用与重启用后的旧凭据检查 |
| --- | --- |
| recall-result | POST Recall、GET auth/me、原 Record、原 Recall result、原 job |
| operation-result | POST Recall、GET auth/me、原 Record、原 operation result、原 job |

每例独立两个 tenant/scope、数据、run_id、控制/证据文件，不能读取另一个测试的 Pack。
U01 是生命周期主体；U04 在另一 tenant，作为各阶段不受影响的非空成功对照。
两个主体均只有真实 READ 权限，具有同一请求 session、不同私有正文/ID canary。
使用真实 Remember 预置 Working 记忆，核对精确 Ref/version/hash/来源、Ready、body/projection generation。

## F12 原成功包

先用 U01 旧凭据实际 POST Recall，完成原 job，检查原 Record 与两条结果，保存精确原 Pack。
检查候选、正文读取、模型输入、结果 Ref、query/embedding hash、read/packed 事件及实际 epoch。
LifecycleStage 还包含真实 Principal、原 TrustedContext、CandidateQualificationResult、identity_revision。
guard 记录 object/relations revision、authorization_epoch、正文 hash 与检查时间；manifest 记录精确发布版本。
epoch 不从 `/p3/auth/me` 猜测：当前接口只返回 principal_id/scope/permissions，实际 epoch 来自维护证据和原 job。
原成功 Pack 以 0600 保存至外部 success_directory，正式摘要只保留 Ref/hash、操作与证据编号。

## 真实控制面与重载屏障

现有 `runtime/flows/application.py::Service.reload_identity()` 读取版本化身份文件并调用
Identity.provision。租户停用会建立 tenant_epoch_fence；重新启用要求同一 principal 的 epoch 超过 fence。
静态凭据轮换同样要求 epoch 递增。测试不直接写身份文件/数据库、不调用 provision、不猜租户管理 HTTP API。

外部 Q18 控制器只操作本例独占部署，通过现有 reload_identity 控制面完成以下有序动作：

1. enabled：观察当前已启用、旧凭据绑定的实际身份及已有初始重载，不重新建立另一套静态身份。
2. disabled：递增 identity configuration revision，设置目标 tenant.enabled=false，保留旧身份/凭据，实际重载。
3. reenabled：再递增 revision，恢复同一 tenant，给同一 U01 合法递增 epoch 并绑定新凭据，实际重载。

控制器须保留完整合法身份/租户/JWT 配置。Identity.provision 是完整替换；同 tenant 的维护主体也必须
按提供方要求更新 epoch，不能误删共享环境身份。平台观察者和 U04 位于独立有效 tenant，生命周期过程中持续可用。
测试要求新 token 在开始时实际为 UNAUTHENTICATED，重启用后实际可用；不预先激活两个 epoch 身份。

请求按 phase 发布到外部 control_directory，使用排他原子文件，不重发已有操作。每次请求携带前一步
evidence_id，回执绑定 run/control operation、Q18、source SHA/image/config/backend/model、目标 tenant、
实际 IdentityEntry（Principal + credential_sha256）、identity revision、实际身份文件摘要、时间和 epoch fence。
disabled 维持旧凭据绑定且 tenant 确实停用；reenabled 维持同一主体/home/READ 资格，只更新 epoch 与凭据。
当前测试针对现有静态凭据配置，不扩展 JWT/登录平台策略。

identity_instances 必须列出实际处理本例请求/任务的所有 HTTP/worker 实例。每个实例提供同 revision、
身份文件摘要、tenant/identity 和 applied_at 的重载证明；只收到配置意图、只重载一个实例或沿用旧文档均不够。
初始 enabled 回执的提交/生效时间为原配置的真实激活时间；变更回执记录本次实际提交/提交事务/生效 UTC 时间。
只有收到对应权威状态和完整重载回执，测试才进入该阶段的 HTTP 检查。缺 Q18/实例重载证明明确 blocked_fixture。

回执还使用原成功请求保留的真实 TrustedContext 执行现有身份 revalidate 观察：enabled 时允许，disabled
和 reenabled 时 FORBIDDEN。检查发生在原 context.deadline_at 之前，避免超时冒充 epoch 失效。
该维护探针观察既有旧快照，不向公共 HTTP 注入伪造 epoch；若执行窗口太短，应由夹具准备足够的合法请求窗口，
不能伪造新的截止时间或省略旧上下文复核。

## 拒绝与当前成功的证据

disabled 阶段旧静态凭据期待现有 FORBIDDEN(403)；reenabled 阶段旧凭据已被替换，期待
UNAUTHENTICATED(401)。每次实际响应正文和头部检查受限材料、索引诊断，拒绝 200/304、裁剪包及无关错误。
POST 不仅检查 HTTP，还比对完整合法维护 task 视图；意外创建的任务必须绑定原 denied operation，
终态 failed/cancelled、无 result_ref、错误与拒绝一致，不能持久化成功结果。

每个拒绝阶段还等待 LifecyclePhaseObservation，绑定该阶段 receipt_evidence_id 和全部五次实际
X-Request-ID/method/path/operation/credential digest。观察必须发生在屏障之后，且没有正文读取、模型正文输入、
结果 Ref、搜索或成功 read/packed/delivered。原 Record/job/guard、Pack 契约摘要与原事件保持原样；
不能删除历史成功事实、改写持久终态、清空包或重新搜索来伪装拒绝。摘要算法与 AET-28 的 pack_hash 相同。
维护 exporter 在该阶段全部五个 HTTP 请求结束、相关任务终态可见后原子发布 JSON 数组。

U04 在原始、停用、重启用三个阶段均完成真实非空 Recall。最后 U01 换新 token，实际 auth/me 和新 Recall
按当前 scope/READ 资格成功，资格 guard、原 job 和真实事件均绑定新 epoch/current revision；不能重放旧成功包。
另以新 token 读取 U04 的记忆必须 FORBIDDEN，证明 tenant 名称或新 epoch 不扩大当前授权。

## 外部输入与分开执行

`P3_AUTH11_FIXTURE_FILE` 指向项目外 JSON，包含 base_url 和 cases（上述两个变体）。value 遵循
TenantLifecycleCase：F4CaseData 公共部署/模型/请求字段、两个 F4Actor、old/new/control Principal、
new_credential_env、initial_identity_revision、identity_instances、q18_capability_id、control_directory、
receipt_export、phase_export、success_directory、maintenance_export。所有路径和重跑 run_id 独立，普通/维护 token 分离。
schema 可由相关 Pydantic 模型导出到项目外；这些文件是测试 IPC/维护观察格式，不是新增产品接口。
本次不实现外部控制器或维护 exporter。

```bash
python -B -m pytest tests/unit/test_recall_tenant_lifecycle_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_tenant_lifecycle.py -p no:cacheprovider --basetemp=<external-temp>
```

严格辅助测试验证证据断言、生命周期关联和 IPC，不能替代真实后端验收；与 live 单独统计。
Ready/回执均使用有界条件轮询，不固定 sleep 代替屏障。脱敏摘要输出到项目外 P3_RECALL_EVIDENCE_DIR，
默认系统临时目录下 aether-workspace-support/AET-29/UUID，不默认展示原始记录链接。
缺政策/契约记 blocked_requirement；缺控制面/夹具/重载或维护证据记 blocked_fixture；受阻完整用例不记 passed。
不改写 AET-11/AET-12 或父 issue。
