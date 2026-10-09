# AET-24：应用、用户与 Agent 边界隔离

本实现补齐 RC-AUTH-04、RC-AUTH-05 的测试用例，不声明真实后端验收通过。
测试入口是 `tests/integration/test_recall_scope_isolation.py`。

| 编号 | 变化维度 | 高分受限侧变体 | 固定维度 |
| --- | --- | --- | --- |
| RC-AUTH-04 | application_id | U01-lower / U04-lower | tenant/user/agent/session/task |
| RC-AUTH-05 | user_id | U01-lower / U04-lower | tenant/application/agent/session/task |
| RC-AUTH-05 | agent_id | U01-lower / U04-lower | tenant/application/user/session/task |

每个变体拥有独立 tenant 和数据副本；变体内部两侧同 tenant，且只改变表中的一个 scope 字段。
两侧使用不同私有 memory_id、正文 canary 和精确来源 Ref。U01/U04 是凭据对应的
`principal_id`，不等同于 `scope.user_id`；agent 变体必须保持 user_id 一致。
两个干扰方向使用独立数据，避免要求同一对数据同时互相高于对方。

每例先完成两侧非空成功对照，再以同 query、相同其他字段显式选择本范围，然后尝试另一侧范围。
授权成功必须有精确本地 Ref、正文和 sources；POST 接纳或空结果不足以证明成功。
显式越界 selection 按当前 `IdentityFoundation.context` 的契约断言
`403 FORBIDDEN`，不返回 job 绑定，原 operation 查询为 unconfirmed。
原 Record、job、两个持久 result 入口仍只返回本范围；跨范围已知 ID 查询被拒绝。
最后复读原成功结果，防止越界尝试污染既有结果。

真实 HTTP 客户端复用 `RecallHTTP`，接纳后轮询原 job 并读取原 result；包括当前源码的
`400 REQUEST_IN_PROGRESS` 延续回执，不重发 Unknown 写入。
请求只使用实际 `RecallRequest`/`ScopeSelector` 字段，不添加客户端 K、身份或 grant 字段。
普通凭据的每次响应正文与响应头均检查受限 memory/source ID、正文与受限计数泄露；
错误采用实际 `ErrorResponse` 契约，禁止自由文本错误携带正文。

## 维护夹具输入

环境变量 `P3_AUTH0405_FIXTURE_FILE` 指向项目外 JSON：

```json
{
  "base_url": "https://configured-p3-origin.example",
  "cases": {
    "application_id-U01-lower": {},
    "application_id-U04-lower": {},
    "user_id-U01-lower": {},
    "user_id-U04-lower": {},
    "agent_id-U01-lower": {},
    "agent_id-U04-lower": {}
  }
}
```

上面的空对象只是索引示意，不能执行。每个 value 必须符合
`tests/recall_scope_isolation_support.py::ScopeIsolationCase`：

- `dimension` 对应索引；`run_id` 每次实际执行独立。
- `request` 使用 `sources=working`，selection 只绑定两侧相同的 session_id。
- `actors` 使用复用的 `F4Actor` 契约，分别包含 U01/U04 的凭据环境变量名、
  合法 scoped maintainer 环境变量名、精确 MemoryRef、原文、sha256、body/projection generation、sources。
  两侧身份的完整 home scope 绑定到夹具 scope，不通过请求伪造身份。
- `operator_env` 是另一个合法平台维护凭据环境变量名。
  普通 reader 凭据必须彼此不同，并与维护凭据分离；manifest 不保存 token。
- `configuration` 是 `/p3/configuration` 的实际 ConfigurationSnapshot；
  `server_settings` 使用实际 RecallSettings，candidate_limit 必须为 1。
- `source_sha`、`image_digest`、`backend_binding`、`model_binding`、`model_space`
  绑定目标源码、镜像、后端和模型；字段类型复用 AET-23 的 F4CaseData。
- `authorization_evidence_id` 引用权威无共享资格/政策证据；本用例不创建 grant。
  缺少政策依据记 blocked_requirement。合法共享正例属于 RC-AUTH-09。
- `maintenance_export` 指向项目外维护观测数组，观察者必须有合法维护资格。

先由合法维护控制面准备两侧同主题 Working 记忆，确认 Ready、body generation/hash、
projection generation/model space 和实际索引。受挑战侧的全范围索引探针必须记录两个实际命中：
外范围 Ref rank=1，合法 Ref rank=2，且外范围有限正分严格更高。
应用、用户、Agent 都使用这个 K=1 强对照。
配置快照和后续实际阶段回执共同核对 K，不能只在 manifest 写一个期望值。

## 原执行阶段证据

维护 exporter 通过现有合法观测能力原子发布 JSON 数组，每个原成功 job 一条
`ScopeStageObservation`。每例需要 baseline 和 explicit-own、两侧各自的四条实际回执。
此格式是测试侧导出协议，不是新增产品 HTTP route；本测试不实现维护 exporter。
准备方不能把手写期望数据当实际观察值。缺控制面/exporter/回执记 blocked_fixture，完整用例失败，
不以 skip、xfail 或 passed 冒充验收。

回执复用 `F4StageObservation` 并增加 `authorization_evidence_id` 和 `sharing_grant_ids`。
本无共享夹具要求真实资格回执的证据编号与 manifest 一致，实际消费的 sharing_grant_ids 为空。
若环境存在共享资格，应重新准备无共享副本，不应删除真实授权或放宽测试断言。

实际阶段必须绑定 run/operation/job/recall/trace、维护 observer、源码/镜像/config/backend/model、
query hash 和 query embedding 输入 hash；候选严格为 rank1 本地精确 Ref，且 generation/hash/模型匹配。
qualified/body_reads/result_refs 都只能是本地 Ref；rerank 开启时模型输入只能是本地 Ref，
关闭时输入为空。AccessObserved/EventEnvelope 使用真实产品契约，检查 read/packed、
本地 subject scope、initiator、trace 和事件去重身份。每个成功操作的事件 ID 必须独立。
受限高分候选仅由维护导出观测，普通客户端不可读取这个探针。

## 分开执行

辅助契约验证（不证明真实授权）与真实后端用例分别运行、分别统计：

```bash
python -B -m pytest tests/unit/test_recall_scope_isolation_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_scope_isolation.py -p no:cacheprovider --basetemp=<external-temp>
```

维护输入 schema 可从 `ScopeIsolationCase.model_json_schema()` 和
`ScopeStageObservation.model_json_schema()` 读取，输出到项目外。
Ready 和回执使用有界条件轮询，不以固定 sleep 代替屏障。
原始执行记录输出到项目外 `P3_RECALL_EVIDENCE_DIR`；默认系统临时目录下的
`aether-workspace-support/AET-24`。每次 UUID 子目录独立，保存脱敏状态、证据编号、
精确 Ref/hash 和来源绑定，不保存 token、正文或受限候选细节。
未改动身份/RBAC、grant 存储、产品授权、AET-11/AET-12 或父 issue。
