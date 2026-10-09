# AET-26：请求与索引中的伪造授权不得生效

RC-AUTH-07/08 测试入口：`tests/integration/test_recall_forged_authorization.py`。
本交付实现测试，不声明真实安全验收通过，不修复产品或配置环境。

| 编号 | 变体 | 当前目标契约与断言 |
| --- | --- | --- |
| RC-AUTH-07 | body × tenant_id/application_id/user_id/agent_id/scope/grant/auth_epoch | 7 个独立变体；真实 Schema extra=forbid，HTTP 422 extra_forbidden |
| RC-AUTH-07 | selection × 相同 7 字段 | 4 个未知字段 422；application/user/agent 为合法选择字段，但越界返回 403 FORBIDDEN |
| RC-AUTH-08 | index-scope | 外范围 memory_id 被伪装成本地 scope，Remember 权威资格必须明确 excluded |
| RC-AUTH-08 | index-grant | 同样的非法 target，原始 entity 增加伪造 READ grant，仍不能授权 |
| RC-AUTH-08 | index-auth_epoch | 同样的非法 target，原始 entity 增加更高的伪造 auth_epoch，仍不能授权 |

共 17 份独立 scope/数据副本。每例两侧 tenant/application/user/agent 都不同，session/task 相同，
记忆 ID、正文 canary 独立，保持同主题 query。当前未知字段语义绑定 RecallRequest/ScopeSelector 的
ContractModel(extra=forbid)，没有从 Q01 猜测“忽略未知字段”。若目标 Schema 改变，应先更新目标契约绑定。
用例真实发送原始 JSON，不能在客户端用 typed model 丢掉注入字段后再发送。
伪造 grant 使用攻击者声明的虚构 resource，不向普通错误响应灌入维护者取得的受限 memory_id/正文。

每例先核对实际 READ、Ready、精确 Ref、正文、来源和 body/projection generation，再执行非空合法召回。
维护者的实际全范围 F4 探针必须证明外范围对象 rank1、分数严格高于合法对象，服务器 K=1。
成功结果必须是本地唯一精确 Ref/正文/sources，空结果或 POST 接纳不构成证明。
未知字段拒绝检查实际 FastAPI detail 的定位与 extra_forbidden；合法越界字段检查 FORBIDDEN，
均不得产生 job 绑定，原 operation lookup 为 unconfirmed。前后 auth/me 核对 principal/home_scope/permissions；
随后的新合法 job 还必须以实际维护回执核对完整 Principal（包括 auth_epoch）未被覆盖。

## Q18 受控索引输入

没有新增产品注入 HTTP route。维护者通过明确、合法的 Q18 控制器，针对
`forgery-{run_id}-attack` 的 search 返回屏障施加一次索引返回故障。
可在现有 Milvus search 适配边界构造实际 `distance` 与 `entity.target` 返回值；
entity 的 grant/auth_epoch 只是非权威索引元数据。不能关闭主体认证、伪造 Remember 的 allowed 结果、
改 grant 存储或改正文。真实 Remember qualify、load_bodies、reranker 和持久结果仍来自目标服务。
前置 baseline 和后置 after 操作不在注入窗口，不能把整个测试永久绑定到替身数据。

非法 target 保留受限对象的 memory_id/version/body hash/generation，scope 伪装为 U01 本地 scope。
这会让“只信索引 scope”的实现有机会暴露缺陷；不能仅用一个在向量发现层已被过滤的外租户对象
冒充 Remember 权威否决。本地合法 target 同时保留，分数严格低于非法 hit，保障 K=1 的不利场景。
内部 index_hits 可以出现非法 Ref，它不代表 READ 授权，也不计为正文读取。
必须观察到 Remember 对该精确 CandidateQualificationTarget 的 decision=excluded、guard/manifest 均为空。

测试分别检查 qualifier 结果、load_bodies 的全部读取尝试、实际正文读取、模型输入与 Pack 引用。
被排除 target 不能出现在任何正文读取尝试中；required reranker 必须真实处理合法正文，输入不含受限对象。
合格候选/正文/模型/结果只有本地 Ref。公共响应与诊断不得携带受限 memory/source ID、正文、
原始向量或 index payload；检查覆盖 POST 回执、轮询、Record、两条 result 路径、错误及响应头。
最终复读此前所有原结果，防止注入影响持久结果。

## 外部夹具和取证契约

`P3_AUTH0708_FIXTURE_FILE` 指向项目外 JSON：`base_url` + `cases`。
cases 按上表实际变体名索引，例如 body-tenant_id、selection-grant、index-scope。
每个 value 符合 `tests/recall_forged_authorization_support.py::ForgeryCase`，复用 F4CaseData：

- 独立 run_id；实际 RecallRequest（working、selection 绑定共用 session）。
- actors 按 U01/U04 排序，包含普通与合法 scoped maintainer 凭据环境变量名、精确 Ref、
  原文及 hash、generation、sources；manifest 不保存 token。
- principal 是实际可信 Principal，包含 principal_id、home_scope、permissions、auth_epoch，绑定 U01。
- operator_env 是独立平台维护凭据环境变量名，不能与普通 reader 混用。
- 实际 ConfigurationSnapshot、RecallSettings（candidate_limit=1、rerank_policy=required 和真实 reranker_model），
  目标 source SHA/image digest、backend/model binding/model space。
- q06_mapping_id 指向已明确的 qualify/load_bodies/rerank/Pack 取证映射；缺失记 blocked_requirement。
- 三个 index 变体必须有 q18_capability_id，缺注入能力记 blocked_fixture。
- maintenance_export 指向维护 exporter 原子发布的 JSON 数组，每条原成功 job 一个实际回执。

原成功 job 使用 ForgeryStageObservation（测试侧协议，不是新增产品 API）。除基础 F4StageObservation 外，
包含 trusted_principal、q06_mapping_id、body_read_attempts。索引攻击回执还包含
injection_kind/q18_capability_id/injection_evidence_id/injection_operation_id、raw_index_hit、index_hits、
真实 CandidateQualificationResult 列表，以及 injection→qualify→load→rerank→Pack 的严格有序阶段屏障编号。
前后合法对照必须没有 injection_kind，使用实际全范围探针，不用伪造 target 探针代替真实预条件。

全部回执绑定实际 run/operation/job/recall/trace、合法 observer、source/image/config/backend/model、
query/embedding 输入 hash，并核对真实 read/packed AccessObserved 事件及跨操作事件身份。
raw_index_hit 必须与实际非法 VectorCandidate.target 和分数完全一致，资格否决绑定其精确 target。
受限 Ref、原始 payload 和资格明细只存在合法维护视图；普通可交付摘要仅保存证据编号及精确 Ref/hash。
缺维护控制面/exporter/原 job 回执记 blocked_fixture，不跳过、不无条件 xfail、不记完整用例 passed。
本次不实现外部控制器/exporter，也不能手写“实际观察回执”来充当验收证据。

## 分开运行与统计

```bash
python -B -m pytest tests/unit/test_recall_forged_authorization_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_forged_authorization.py -p no:cacheprovider --basetemp=<external-temp>
```

unit 是严格契约辅助验证，使用构造的非法索引/权威否决回执检查断言敏感性，不能证明产品管线通过。
真实 HTTP 请求注入、真实后端加受控索引故障、严格辅助验证分别统计，lane 不混记。
Schema 可从 ForgeryCase/ForgeryStageObservation.model_json_schema() 输出到项目外。
Ready 与实际回执使用有界条件轮询，不能用固定 sleep 代替 Q18/Q06 阶段屏障。
原始记录写项目外 P3_RECALL_EVIDENCE_DIR，缺省系统临时目录下 aether-workspace-support/AET-26，
每次独立 UUID 子目录，不保存 token、正文或受限索引原始明细到交付摘要。
未改产品授权、身份/RBAC/grant 存储、性能或审计体系，未修改 AET-11/AET-12 或父 issue。
