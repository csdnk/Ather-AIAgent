# AET-28：共享撤销后的新旧读取

RC-AUTH-10 位于 `tests/integration/test_recall_shared_revoke.py`；这是测试用例实现，
不声明真实后端验收通过。沿用已确认的 Recall HTTP、原 job/Record/result 与合法维护视图边界。

| 独立变体 | 新 Recall | O5 原 Record 与原结果 |
| --- | --- | --- |
| recall-result | 完成原任务，只交付 U08 本地合法非空对照 | 原 Recall result 连续重取两次 |
| operation-result | 同上 | 原 operation result 连续重取两次 |
| http-cache | 同上 | 复用预热连接和目标实际返回的 ETag/Last-Modified，两个原 result 各重取两次 |

每个变体独立 tenant/scope、六份记忆、run_id、IPC/证据路径。每次重跑使用全新的数据和路径。
不依赖 AET-27 或其他变体的执行顺序。通过 AET-27 的 SharedRecallHarness 重新执行 owner、
U08 本地、U09 无 grant 非空对照，真实设置单一 READ grant，再让 U08 实际交付精确共享版本。
先验证原 Record、原 job 与两条结果，并保存原 Pack、正文/Ref/version/hash/来源、资格 guard
中的关系 revision/authorization epoch。原 Pack 存外部 0600 文件；原 read/packed 事件完整保留。

## O4 权威撤权与残留副本

当前提供方是可信部署侧 `Identity.provision`，没有公开共享撤销 HTTP API。
测试向项目外 `revoke_request` 原子提交单一 remove_grant 意图，再轮询 `revoke_export` 的权威回执；
不写 grant 表、不直接调用 provision、不猜 `/revoke` 路由。外部 Q18 控制器必须仅操作 test-owned
部署，保留完整身份/租户/JWT 配置，仅去掉目标 grant，推进 configuration_revision，不能通过撤销
凭据、停用 tenant 或改 home/permissions 代替撤销共享。没有控制器或回执记 blocked_fixture。
该部署路径不宣称补齐 AET-27 中缺失的 Remember 共享入口。

RevokeReceipt 绑定 run/control operation、Q18、observer、目标源码 SHA/镜像/config/backend/model，
实际撤销的完整 grant、新 revision、UTC submitted/committed/effective/observed 时刻、未改变的
Principals、实际空 grant 集合，以及真实 U08 对原精确 target 的 excluded qualification 和检查时刻。
拒绝项没有可复用 manifest/guard。只有实际生效回执后，普通 HTTP 响应检查才禁止 shared ID/正文。

RetainedReplicas 由合法维护 exporter 实际观察 Milvus 和 Redis，不由测试制造：
vector_present/redis_present 均为 true；绑定原 memory/version/projection generation/model/hash、
Redis body generation/原文/hash、实际 provider/instance 和时间。撤权回执与最终重取后均检查副本存在。
Redis 内容只出现在外部受控维护证据中。若夹具不能保留副本，不能把清理成功当撤权成功。

## 新召回、O5 重取及事件证据

撤权后仍使用相同 query、普通 U08 凭据和 session；POST 新 Recall，轮询其原 job，并读取实际结果。
需要精确本地非空 Pack，检查正文读取/模型输入/结果 Ref、真实阶段与 read/packed 事件。
新任务还要有实际 U08 对共享 target 的 excluded qualification、检查时间及完整事件。
不能用空结果、202 接纳、后端降级或身份已失效证明隔离。

每条旧 result 请求前先 GET 原 Record；Record 是保留的历史元数据，可以仍标 completed/result_available，
不等于当前正文可交付。原 result 必须返回现有 FORBIDDEN(403) 或 RESULT_INVALIDATED(410)，
不允许 200 裁剪/空包/替换结果、304 授权旧 HTTP 缓存回放，或 404/503 等无关错误伪装通过。
若目标没返回缓存校验头，http-cache 变体仍在相同路径重复普通 GET；不伪造 ETag，不声称测试了不存在的
缓存代理。测试不读取本地旧 Pack 冒充服务器响应；正常应用自己已持有的旧字节不属于可远程撤回范围。

最终维护证据 RevokeFinalObservation 按 revoke control_operation_id 关联：原 Record/job/guard、
原 Pack 摘要、原完整事件、新 operation/job/recall/trace、实际 excluded qualification、新完整事件，
及每次原 result GET 的实际 X-Request-ID/path/Principal/检查时刻/资格/搜索次数/前后原 Pack 摘要/事件。
维护 exporter 在本变体全部 2 次（http-cache 为 4 次）结果 GET 结束后原子发布最终 JSON 数组。
缺观察接口或原请求绑定记 blocked_fixture，不使用数据库侧读取代替用户响应。

Pack 摘要为 `sha256(ContextPack.model_dump_json().encode())`，exporter 需按同一契约序列化持久化原 Pack，
用于核对不可替换/裁剪的保存内容，而不是 HTTP JSON 空白差异。before/after 摘要必须等于独立保存的原包；
旧 GET 搜索次数为零。原事件整体等于撤权前捕获的真实事件，不能将成功 read 改为失败或删除。
新召回/受阻重取不能记录撤权材料 packed/delivered succeeded。原 job 终态与原结果引用保持不变。
末尾复核身份及 owner 精确正文；普通响应正文与头部全程检查未授权 ID/正文及索引诊断泄露。

## 外部夹具与执行

`P3_AUTH10_FIXTURE_FILE` 指向项目外 JSON：`base_url` + `cases`，cases 按三个变体索引。
每个 value 是 RevokeCase：包含 [AET-27 夹具字段](recall-shared-read.md) 和独立
`revoke_request`、`revoke_export`、`final_export` 路径。所有普通与维护凭据分离，仅传环境变量名。
可以通过 RevokeCase/RevokeReceipt/RevokeFinalObservation.model_json_schema() 在项目外导出完整 schema。
控制文件和证据 schema 是测试侧 IPC 协议，不是新增产品 API；本任务不实现控制器/exporter。

```bash
python -B -m pytest tests/unit/test_recall_shared_revoke_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_shared_revoke.py -p no:cacheprovider --basetemp=<external-temp>
```

严格辅助断言测试使用构造契约回执，单独统计，不能替代 live 后端验收。Ready/回执采用有界条件轮询，
不固定 sleep 代替屏障。执行摘要在外部 P3_RECALL_EVIDENCE_DIR（默认系统临时目录下 AET-28 UUID 目录），
仅包含操作/证据编号、时间、Ref/hash 与脱敏状态，不写正文/凭据。缺政策/契约记 blocked_requirement；
缺 Q18/夹具/维护能力记 blocked_fixture；完整受阻变体明确失败，不记 passed。
不修改产品授权/RBAC/grant 存储、AET-11/AET-12 或父 issue。
