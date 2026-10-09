# AET-23 / RC-AUTH-03 测试用例实现

Owner：A（Recall 消费方测试）。本次只实现测试用例，不要求当前环境执行通过，
不修改产品授权、RBAC、grant 存储或 AET-11/AET-12。

## 用例矩阵

| 变体 | 请求顺序 | 前置与关键断言 |
|---|---|---|
| `a-first-a-lower` | U01 → U04 | U01 合法候选低于外租户 Top1，K=1 仍召回 U01 精确 Ref |
| `b-first-a-lower` | U04 → U01 | 同上，反转顺序，排除先写/先查导致的跨租户覆盖 |
| `a-first-b-lower` | U01 → U04 | U04 合法候选低于外租户 Top1，K=1 仍召回 U04 精确 Ref |
| `b-first-b-lower` | U04 → U01 | 同上，反转顺序 |
| `a-first-same-body-cache` | U01 → U04 | 正文/hash 相同、memory_id 相同，实际 cache 命中仍保留各自 Ref 和来源 |
| `b-first-same-body-cache` | U04 → U01 | 同上，反转顺序 |

每个变体使用独立 tenant 对和 run_id。双方 application/user/agent/session/task、
memory_id 和 version 字面相同；tenant 不同。四个压力变体使用不同正文作为泄露
canary；两个缓存变体使用相同正文，额外核对各自来源与事件。

双方发送相同 query、相同 operation ID 和相同请求体。先核对身份、F4 Ready、
完整正文、来源和 Ref/hash/generation，再通过 POST、原 job 轮询和原 result 重取
建立非空合法 READ 对照。然后交错多次 GET 原 Record/result、重放已成功的原
operation，检查 job/recall 不碰撞、Pack 不变，并分别尝试读取另一租户的已知
Record/job/result。另建各自独有 operation，验证他方 lookup 为 `unconfirmed`
且没有 job、input hash、workflow 或 HTTP 请求数据；这不被解释为“没有任何副作用”。

普通响应（含异步继续、错误、lookup 和 job 元数据）先经过泄露检查，才保存
允许字段。Pack 用当前 ContextPack 契约校验，拒绝未知调试字段、重复 Ref、
空/降级成功对照以及外租户正文。维护回执核对 candidate/qualification/body/model/
result/event 边界；模型不开 rerank 时要求 body model input 为空，而 query embedding
仍绑定原 query hash。事件使用产品 EventEnvelope 和 AccessObserved 契约验证。

## 夹具输入

公开 Remember 写入接口自动产生 memory_id，没有合法的自定义 ID 请求字段。
因此同名 F4 必须由维护者通过目标允许的准备方式预置；测试不改数据库指针、不
替换授权，也不新增产品 API。不能准备这种数据时，运行前提是 `blocked_fixture`。

设置 `P3_AUTH03_FIXTURE_FILE` 指向维护者准备的 JSON 输入：

- 顶层 `base_url`：真实 P3 API origin；`cases`：以六个变体名为键的对象。
- 每个 case 按 [F4Case](../../tests/recall_tenant_isolation_support.py) 定义，包含
  `run_id`、`request`、两个 `actors`、`maintenance_export`、`operator_env`、
  `configuration`、`server_settings`、源码 SHA、镜像 digest、后端/model binding
  摘要和 `model_space`。
- 每个 actor 按 F4Actor 定义，含 U01/U04、凭据环境变量**名称**、范围内维护者
  凭据环境变量名称、精确 MemoryRef、正文、SHA-256、body/projection generation
  和精确 SourceRef。不得在文件里写 Bearer 值。
- 范围内维护者用于合法读取本租户 Remember 快照/正文；`operator_env` 是另一个
  合法平台维护身份，用于当前 `/p3/configuration` 和 `/p3/admin/tasks/{job_id}`。
  普通身份不能共享这些维护凭据。DIAGNOSE 不被当成任意业务正文或平台权限。
- `server_settings` 消费实际 RecallSettings，必须 `candidate_limit=1`。运行时
  `/p3/configuration` 要与提供的激活快照一致，阶段回执也必须记录实际 K=1。
  测试不往公开请求体添加 K/top_k/candidate_limit。

可在已安装项目依赖的环境中导出测试输入 Schema，输出放独立工作区：

```sh
PYTHONPATH=src:tests python -B -c 'import json; from recall_tenant_isolation_support import F4Case; print(json.dumps(F4Case.model_json_schema(), ensure_ascii=False, indent=2))'
```

## 维护回执输入

`maintenance_export` 指向合法维护导出者原子发布的 JSON 数组，每个原 job 一条
[F4StageObservation](../../tests/recall_tenant_isolation_support.py) 记录。测试等待
**匹配该 job 的回执**，不靠固定 sleep 推断阶段已完成，也不复用别的 job 的记录。

导出者须从目标实际观测采集，不能把 fixture 的期望值手填成“观测”：

- `run_id/operation_id/job_id/recall_id/trace_id` 对应实际原操作；observer 对应
  `/p3/auth/me` 的合法平台维护主体，trace 同时核对合法 admin task 视图。
- 源码 SHA、镜像 digest、config/backend/model binding 与本次目标一致。
- 压力变体的 `unfiltered_probe` 来自同 query、同模型空间的维护索引探针：
  精确两个目标，外租户 rank=1，合法目标 rank=2，有限正分数严格高低有别。
  每项使用产品 VectorCandidate/ProjectionTarget，核对 Ref/generation/body hash。
- 实际有 scope 过滤的 `candidates` 恰为本租户 rank=1，实际 server limit=1；
  `qualified/body_reads/result_refs` 只含自身精确 Ref，模型输入不含外租户 Ref。
- 缓存变体必须观测到实际 `body_paths=["cache"]`；重复 GET 不能被冒充 cache 命中。
- `events` 是完整产品 EventEnvelope，至少有原 recall 的 read/packed 访问事件，
  tenant、memory/version、initiator 和 trace 一致；两个租户的 event ID 不碰撞。

本次没有实现或改造维护导出控制面。当前公开接口不能提供完整阶段数据时明确
保留夹具依赖；不推测新的取证 HTTP 路由，不把合成观测当成真实后端证据。

## 检查与运行

两个执行类别分开：

```sh
# 测试辅助的契约/泄露断言；不是产品授权验收
python -B -m pytest tests/unit/test_recall_tenant_isolation_support.py \
  tests/unit/test_recall_authorization_support.py \
  -p no:cacheprovider --basetemp=<external-workspace>/unit-tmp

# 真实 HTTP 用例；需要上述预置 F4 和合法维护回执
python -B -m pytest tests/integration/test_recall_cross_tenant_top1.py \
  -p no:cacheprovider --basetemp=<external-workspace>/live-tmp
```

复用的 RecallHTTP 已补齐当前 P3 的 `400 + REQUEST_IN_PROGRESS + 原 job 头`
继续处理，始终轮询原任务，不重发未知 POST；同时保留原来的同步/202 兼容分支。
失败终态、空结果、无回执都不能证明隔离通过。用例没有无条件 XFAIL，也没有
为了通过测试而关闭授权或替换真实后端。

`P3_RECALL_EVIDENCE_DIR` 指向项目外独立工作区；默认使用系统临时目录下的
`aether-workspace-support/AET-23`，每次独占目录。只写允许字段、Ref/hash 和证据
编号；受限候选/正文/原事件继续保留在合法维护导出中。
