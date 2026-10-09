# AET-27：显式共享精确当前版本

RC-AUTH-09 入口为 `tests/integration/test_recall_shared_read.py`。本交付是测试实现，不声明真实安全验收通过。

| 变体 | 核对重点 |
| --- | --- |
| delivery | U08 的真实 READ grant 生效后，精确 owner Ref/version/generation/hash 被实际交付 |
| no-grant | 相同 query 的 U09 在共享前后都有自己的非空成功对照，但不能得到共享材料 |
| isolation | 未共享同 scope 邻近记忆、其他 scope、其他 tenant 不随 grant 放开 |
| read-not-write | U08 有全局 WRITE/CORRECT 能力，但 READ grant 不允许 owner scope 写入或修改共享对象 |
| read-not-delete | U08 有全局 DELETE 能力，但 READ grant 不允许删除共享对象 |
| remember-preparation | 明确记录当前缺少 Remember 共享控制入口，不能将部署 grant 入口冒充 Remember 入口 |

每例独立 scope/数据、run_id 和控制/证据文件。owner U01、recipient U08 和无 grant 对照 U09
为三个独立主体，U08/U09 与 owner 同 tenant、application/user/agent 均不同。
六份记忆分别为 shared、neighbor、other-scope、other-tenant、u08-local、u09-local。
shared/neighbor 使用既有 F1 原文（咖啡不加糖、喜欢乌龙茶），由真实 Remember 预先保存并达到 Ready。
query 为同一个饮品偏好主题，两个 reader 的本地记忆提供独立非空成功对照，shared 的实际分数应能在
grant 生效后成为 U08 的目标。维护者确认六份精确 Ref、来源、版本、body/projection generation/hash。
不能只根据 remember 接纳、共享意图、POST 接纳或空结果判定成功。

## 当前共享提供方和边界

源码当前通过 `runtime/foundation/identity.py::Identity.provision` 的可信部署控制面配置
`AuthorizationGrant`，没有 Remember 共享 route/port。`remember-preparation` 变体在合法对照后
明确记 blocked_fixture，不将当前部署控制入口宣称为缺失的 Remember 入口。
其他变体单独验证当前真实部署 grant 的消费和交付，不代表该入口缺口已解决。

Remember 自己的 `remember/http.py` 已提供 `/p3/remember/{id}/correct` 与 `/delete`。
WRITE 变体真实发送 typed CorrectionRequest，并用 RememberRequest 尝试 owner selection 写入；
DELETE 变体使用资格守卫中的真实 object_revision 发送 DeleteRequest。
均期待 FORBIDDEN、无 job 接纳，再以 owner 维护视图复核原 Ref/version/scope、正文/hash、Ready 未改变。
路由在目标部署缺失时只将对应变体记 blocked_fixture。没有猜测 `/share`、`/grants` 或删除 URL。

当前发现过程使用逻辑 memory grant（resource.version=None）并由 Remember 核对精确当前版本。
测试允许 grant 资源为逻辑版本或指定当前版本，实际当前可发现能力以 Recall 和 qualify 为准；
不把逻辑 grant 宣称为“以后新版本永远不可读”的版本锁定政策。
共享成功的 Pack scope 是 U08 的 home scope，item.memory.scope 仍为 owner scope；两者不能错误地要求相等。
owner 原归属和 U08 的主体/home/permissions 均保持原样，不发生所有权转移。

## Q18 grant 准备与权威回执

可复用能力位于 `tests/recall_shared_read_support.py`：

- `prepare_shared_grant`：向项目外控制请求文件原子发布一个 READ grant 请求（不覆盖已有请求），
  等待合法维护者提供实际权威回执，不能凭意图返回成功。
- `assert_active_grant`：绑定 U08/U09 的实际资格主体、单一 grant/revision、目标来源和 observer，
  检查精确 shared allowed、U09 对 shared excluded、U08 对 neighbor excluded，及对象级 READ true、
  WRITE/CORRECT/DELETE false。
- `SharedRecallHarness.prepare/recall_and_save/verify_original`：调用现有 HTTP，轮询原 job，
  检查原 Record/两条 result、候选/正文/模型/事件，保存和复读原成功 Pack。
- `SavedSharedPack`：保留 operation/job/trace、grant 证据编号及原 ContextPack，供撤销和模型前撤权
  用例复用。原文仅存在外部受控工作目录的 0600 文件中，默认不展示文件链接。

控制文件是测试侧 IPC，不是产品 API。本次不实现外部 Q18 控制器或维护 exporter，也不直接写库或
调用 Identity.provision。维护者的控制器必须仅操作独立 test-owned 部署；Identity.provision 是完整
配置替换，控制器必须保留已配置主体与合法配置，不能误删共享环境身份、改 epoch 或批量授予其他对象。
它通过当前受控提供方应用一个 grant 后，读取实际授权及 Remember qualify 结果，再原子发布 JSON 数组。
没有 Q18 或没有实际回执时记 blocked_fixture，不生成自证回执、不禁用授权、不固定 sleep 等待生效。

## 外部夹具输入

`P3_AUTH09_FIXTURE_FILE` 指向项目外 JSON：base_url + cases，cases 按上表六个变体索引。
每个 value 符合 SharedReadCase，包含：

- 六份 SharedReadyMemory、U01/U08/U09 Principal 与凭据环境变量名，所有普通/维护 token 分离。
- 真实 AuthorizationGrant，仅 grantee U08、单一 shared memory、permissions=[memory:read]，不包含所有权或 mutation grant。
- 同 query、实际配置快照和 RecallSettings（K=1）、source SHA/image digest、backend/model binding/model space、operator_env。
- q18_capability_id、control_request、control_receipts、maintenance_export、success_directory。
  所有路径在项目外，每变体独立；run_id 和请求路径每次重跑必须新建。

GrantReceipt 使用实际 AuthorizationGrant、CandidateQualificationResult 和 Principal。
qualification_principals 分别绑定 shared→U08、no-grant→U09、unshared→U08，不能用 owner 资格冒充共享资格。
回执须包括受控 operation、配置 revision、时间、observer、部署/模型绑定、U08/U09 的实际 grant 集合，
allowed 必须有完整 manifest/guard，excluded 不携带可复用凭据。所有正文只通过合法维护者确认。

每条成功 Recall 的 SharedStageObservation 绑定原 run/operation/job/recall/trace、Principal、consumed_grants、
实际 qualification、query/embedding hash、部署与模型。候选/正文/模型/结果精确匹配目标 Ref；
真实 AccessObserved 的主体归属、initiator、trace、read/packed 和跨操作事件身份均核对。
U08 共享成功必须消费原 grant；本地成功对照不消费 grant。无 grant reader 的相同 query 只交付自己的合法记忆。
公共响应与头部检查未授权对象 ID/正文和原始索引诊断；受限信息只在合法维护视图取证。

## 分开执行

```bash
python -B -m pytest tests/unit/test_recall_shared_read_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_shared_read.py -p no:cacheprovider --basetemp=<external-temp>
```

辅助 fixture/IPC/断言验证使用构造的严格契约回执，不能证明产品真实共享通过；与 live HTTP 分别统计。
Schema 可由 SharedReadCase/GrantReceipt/SharedStageObservation.model_json_schema() 输出到项目外。
Ready/回执均使用有界条件轮询。脱敏摘要输出项目外 P3_RECALL_EVIDENCE_DIR，默认系统临时目录下
aether-workspace-support/AET-27，每次 UUID 子目录；只记录结论状态、Ref/hash、操作和证据编号。
缺政策/契约记 blocked_requirement，缺控制面/接口/夹具记 blocked_fixture，完整受阻变体不记 passed。
未修改产品身份/RBAC/grant 存储、AET-11/AET-12 或父 issue。
