# AET-25：session/task 选择与授权范围求交集

RC-AUTH-06 的用例入口为 `tests/integration/test_recall_selection_intersection.py`。
本交付是测试实现，不声明真实后端验收通过。

| 变体 | 请求顺序 | 核对重点 |
| --- | --- | --- |
| s1 | S1 | session 过滤，不暗加 task |
| j1 | J1 | task 过滤，不暗加 session |
| s1j1 | S1+J1 | 两个条件求交集 |
| change-session | S1→S2→S1 | 相同主体/query，选择不残留 |
| change-task | J1→J2→J1 | 相同主体/query，选择不残留 |
| empty | `{}` | 外延绑定权威 Q01/目标契约；未知时只检查不越权 |
| omitted | 不发送 selection 字段 | 当前 RecallRequest 必填契约，真实 HTTP 422，原 operation 未绑定 |

每个变体拥有独立 tenant、run_id 和数据副本。每份夹具包含 U01 的
S1J1/S1J2/S2J1/S2J2 四条同主题 Working 记忆，以及同 tenant/application/agent、
同 S1J1、仅 user_id 不同的受限高分对象。U01 的 home session_id/task_id 均为空，
避免把“当前会话已绑定”混入选择范围测试。它确实拥有两个 session 和两个 task。
所有对象有不同 ID/正文 canary，维护者核对精确 Ref、来源、Ready 和 generation/hash。
每例先以精确 session+task 分别执行四条非空合法成功对照，再执行表中的请求。
不以空结果、POST 接纳、同名 ID 或占位注释判定通过。

## 当前契约与期望

请求使用真实 `RecallRequest`/`ScopeSelector`。`runtime/foundation/requests.py::select_scope`
允许 home 未绑定的 session/task 被 selection 收窄；application/user/agent 仍来自身份。
`MemoryRef.scope` 是目标归属，Pack/Record/job 的 scope 是 home 与 selection 合成的有效范围，
二者不被误当作必须完全相等。只选 S1 时不推定 task=J1；只选 J1 时不推定 session=S1。
返回项必须是事先准备的精确合法 Ref，正文和 sources 与独立夹具相同，渲染文本不含范围外 canary。
限定 session/task 不会授权同 session/task 的其他用户记忆。

服务端 candidate_limit 必须为 1。显式选择变体的首个主请求需要真实不利全范围探针：
最高分为 U01 自有但 selection 范围外的对象，且该对象、受限用户对象的分数均严格高于合法目标。
探针绑定五个实际对象的 Ref/hash/generation/model space、有限正分和排序。
切换步骤共享同 query/索引，不要求 S1 和 S2 的分数同时互相更高；其余步骤核对各自独立期望 Ref，
返回首选择时再次检查精确 Ref。空选择有权威外延时使用受限对象为全范围 Top1 的对照。
没有客户端 K 字段、身份覆盖、数据库补写或授权关闭。

每次成功操作轮询原 job，读取原 Record 及两条持久结果路径。
全部操作结束后按相反顺序复读此前所有原结果，防止后续选择污染原任务或结果。
请求通过现有 RecallHTTP 恢复原操作，包括 `400 REQUEST_IN_PROGRESS` 延续回执；不重发 Unknown 写入。
普通 reader 的每次响应正文和响应头检查范围外/未授权 memory/source ID、正文和受限计数泄露。
维护正文、受限高分探针仅通过合法维护视图读取，不落入普通响应。

## 外部维护输入

`P3_AUTH06_FIXTURE_FILE` 指向项目外 JSON，包含 `base_url` 和 `cases`。
`cases` 的键为上表七个变体名称，每个 value 符合
`tests/recall_selection_intersection_support.py::SelectionIntersectionCase`：

- `deployment`：复用 F4CaseData。包含 run_id、真实 RecallRequest 模板（working、selection={}）、
  原操作维护导出文件、平台 operator 凭据环境变量名、实际配置快照/RecallSettings、
  source SHA、image digest、backend/model binding 和 model space。
  actors[0] 必须与 memories[0] 完全一致，actors[1] 为受限用户对象。
- `principal_scope`：U01 的实际 home scope，session/task 未绑定。
- `memories`：严格按 S1J1/S1J2/S2J1/S2J2 排序的四个 F4Actor。
  name 都是 U01，共用一个 ordinary credential_env；每个对象包含合法 scoped maintainer 的
 环境变量名、精确 Ref、正文、body hash、body/projection generation 和 sources。
- `authorization_evidence_id`：真实归属/资格政策的证据编号；缺政策记 blocked_requirement。
- `expected_refs`：s1/j1/s1j1/s2/j2 对应的独立精确结果 Ref，必须落在各自授权与 selection 的交集。
  期望来自维护者准备的数据和目标契约，不能从被测响应反推。
- `empty_contract`：可缺省。若提供，包含 evidence_id、与目标一致的 source_sha、
  权威 eligible_refs 和精确 expected_ref，全部必须属于 U01。
  它记录 Q01/目标契约认可的外延，不由测试自行推断为当前 session 或全用户范围。

当前 RecallRequest.selection 必填，因此 omitted 用例不把省略改写为 `{}`；它核对
FastAPI 的真实缺字段 422 和 detail 中 body/selection 的 missing 诊断、无 job 绑定及
operation lookup unconfirmed。这是目标 Schema 的明确拒绝语义，不推定省略时的检索外延。
若目标改变为可省略，需要先更新目标契约绑定再调整用例。

empty 未提供权威契约时仍执行真实请求，核对普通响应、Pack 和候选/正文/模型/结果均不越权，
不要求命中集合完整，不假定 eligible_refs 等于全部自有记忆或仅当前会话。
证据记 safety_only，完整变体以 blocked_requirement 失败，不记 passed。

## 原执行维护回执

维护 exporter 通过现有合法观测能力原子发布 JSON 数组，一条原 job 对应一条
`SelectionStageObservation`。格式属于测试侧，不新增或猜测产品接口；本次不实现 exporter。
四条成功对照及每个成功主请求都需要实际回执。缺少控制面、夹具或回执记 blocked_fixture；
不能用手写期望回执冒充实际观察，也不使用无条件 skip/xfail。

回执复用 F4StageObservation，增加实际 selection、authorized_refs、eligible_refs、
authorization_evidence_id、selection_contract_id。授权集合绑定四条合法记忆；明确 selection 的
eligible_refs 必须等于交集；Q01 未知时只校验安全上界。空选择有契约时证据编号必须一致。
回执同时绑定原 run/operation/job/recall/trace、合法 observer、目标源码/镜像/config/backend/model、
query/embedding 输入 hash。候选、正文、模型和结果检查精确 Ref，事件使用真实
AccessObserved/EventEnvelope，核对 scope/initiator/trace/read/packed，跨操作事件身份不复用。

## 分开执行与记录

```bash
python -B -m pytest tests/unit/test_recall_selection_intersection_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_selection_intersection.py -p no:cacheprovider --basetemp=<external-temp>
```

辅助验证不证明真实授权，与 live HTTP 分别执行、分别统计。
Schema 可从 SelectionIntersectionCase/SelectionStageObservation.model_json_schema() 读取并输出到项目外。
Ready 和回执均有界条件轮询。原始记录写项目外 P3_RECALL_EVIDENCE_DIR，缺省系统临时目录下
`aether-workspace-support/AET-25`，每次独立 UUID 子目录。证据只保存状态、编号、选择、精确 Ref/hash
和来源绑定，不保存 token、正文或受限候选详情。不改身份/RBAC/grant 存储、AET-11/AET-12 或父 issue。
