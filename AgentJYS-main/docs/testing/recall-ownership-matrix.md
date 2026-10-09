# AET-32：诊断权限与结果所有权矩阵

RC-AUTH-14 位于 `tests/integration/test_recall_ownership_matrix.py`，辅助契约与响应断言在
`tests/recall_ownership_matrix_support.py`。本交付仅实现测试，不声明真实后端通过；不修改
AET-11/AET-12、产品认证/RBAC/grant 存储或父 issue。

## 矩阵边界

矩阵为五种 viewer × 七种接口，共 35 个独立 pytest 变体。每个变体独立 tenant/scope、F1
数据副本、run_id、F12 原 Pack、operation/job/recall/trace 及外部证据路径，不依赖其他变体执行顺序。

| viewer | 权限 | 相对 U01 scope | 不依赖未决政策的安全约束 |
| --- | --- | --- | --- |
| U01 | READ + DIAGNOSE | 原发起者 | 原结果精确一致；合法诊断字段受限，无正文/凭据 |
| U06 | READ + DIAGNOSE | 完全相同 scope，不同 principal | 同 scope READ 与操作发起者区分；不能看到 U01 Trace 内容 |
| U07 | 仅 DIAGNOSE | 完全相同 scope，不同 principal | 不得交付结果正文；不能看到 U01 Trace 内容 |
| U02-user | READ + DIAGNOSE | 仅 user_id 不同 | 不得穿透 scope 获得原对象/正文/Trace |
| U02-agent | READ + DIAGNOSE | 仅 agent_id 不同 | 同上，单独验证 agent 边界 |

使用源码已经存在的真实 GET 接口，不猜 `/traces/{id}` 或管理员路由：

| surface | 原对象接口 |
| --- | --- |
| diagnostic-task | `/p3/tasks/{job_id}` |
| trace-list | `/p3/traces?flow=recall&limit=100`，遍历 next_before |
| trace-detail | `/p3/logs/{trace_id}?limit=100`，遍历 next_after |
| job | `/p3/operations/{job_id}` |
| record | `/p3/recalls/{recall_id}` |
| recall-result | `/p3/recalls/{recall_id}/result` |
| operation-result | `/p3/operations/{job_id}/result` |

任务诊断、job、Record 和结果是否可见并非同一政策。尤其同 scope 的 DIAGNOSE 可能允许任务
元数据，但 Trace 按 principal+scope 隔离；READ 不等于 DIAGNOSE。矩阵不把某个身份自设为全权管理员。
U01 增加 DIAGNOSE 仅用于明确的合法诊断对照；四个普通身份必须不在 deployment maintenance 列表。

## 外部夹具与原成功对照

`P3_AUTH14_FIXTURE_FILE` 指向项目外 JSON：`base_url` 和 `cases`，cases 用上述
`viewer:surface` 索引，各 value 按 OwnershipCase schema 准备。每次执行都使用新路径；
maintenance_export/success_directory 不允许复用旧运行。policy_file 是本变体的独立 Q01/Q06 政策文件。
普通与合法维护凭据只传环境变量名且不得相同。受限原始材料仅保存在合法维护视图及外部受控目录。

先 GET 当前配置、四个 `/p3/auth/me` 核对真实权限枚举与 scope；通过维护接口检查精确 F1
咖啡/乌龙茶两份 active Working Ready 记忆、版本、来源、正文 hash、body/projection generation 和模型。
U01 对咖啡执行实际 Recall：必须非空成功、精确 coffee Ref/正文/来源，原 job 成功，两个原结果
路径均返回同一 ContextPack。合法维护任务视图提供原 trace；OwnershipStage 绑定真实部署及原请求，
核对候选/正文/模型/结果/事件，不允许隐式 grant 或普通身份拥有 deployment maintenance 权限。
另用 U01 实际 GET 原任务诊断和原 Trace 日志，要求真实非空日志、类型正确及无正文/凭据；
readyz 只作 Ready 屏障，不作为 Trace 权限证明。原 F12 Pack 保存在外部 0600 文件。
原诊断对照也检查全部普通/维护凭据泄漏。Trace 日志使用最长 30 秒有界条件等待；缺少合法对照
或保留日志时记 blocked_fixture，字段错误、对象不符、正文或凭据泄漏仍直接断言失败。

## Q01/Q06 与受阻单元

policy_file 使用 MatrixPolicy：`q01_evidence_id`、`q06_evidence_id` 指向已明确的政策与证据映射，
`source_sha` 绑定目标源码，`rules` 按变体给出 MatrixRule：visible/filtered/denied、确切 HTTP
status/code 以及顶层 allowed_fields。规则须由合法维护人员根据已确认契约提供，不能由实际响应
反推 expected；字段范围还受 Python DTO/固定 Trace 页结构约束，不能通过宽泛 allowlist 放行正文。
policy_sha256 为排除自身字段后 `json.dumps(payload, sort_keys=True, separators=(",", ":"))`
的 UTF-8 SHA-256。可从模型导出 schema 到项目外工作区。

政策文件、Q01/Q06 evidence 或本单元 rule 缺失时，该单元仍先执行实际 GET、安全检查和分页，
最后记录 blocked_requirement 并失败。其余 34 个独立单元继续由 pytest 执行；不 skip，不将受阻
单元记 passed。缺部署、凭据、维护观察或 Ready 记 blocked_fixture；损坏/错误绑定证据是测试失败。
Record 可见性、HTTP 403/404 隐藏策略及字段允许范围由已确认 Q01/Q06 决定，不由测试自行决定。

## 响应与证据

合法元数据严格按 TaskRecord/TaskOperationView/RecallRecord 校验并绑定原对象及 scope；
合法结果逐字段等于保存的原 Pack，不允许替换/裁剪。Trace 页只允许当前固定字段，日志逐条
使用 NodeLogRecord 校验并关联原 trace/operation。列表跨页聚合验证原 Trace 的存在或缺失。
非发起者 Trace GET 可按已确认政策返回受限 200 页或拒绝；受限页 records=[]、无后续 cursor、
overview 无 span/count/open_span_ids。当前页契约会回显调用者已知的 trace_id；这不是 Trace 内容授权，
错误响应或其他字段不能借此回显受限事实。

所有公开元数据、Trace、错误与头部禁止正文、凭据、任意 input/output/raw index 字段。
错误还禁止回显原 operation/job/recall/trace 和记忆 ID；U02 不能获得原对象的受限标识。
304 不能绕过所有权校验；分页游标不能重复，分页总等待 30 秒，HTTP 30 秒，Ready 条件等待 60 秒。
无固定 sleep 推断阶段，无直接数据库读取或授权覆盖。

脱敏摘要仅在外部 P3_RECALL_EVIDENCE_DIR（默认系统临时工作区 AET-32 UUID 目录），
保存源码 SHA/镜像/backend/model/config 绑定、真实权限枚举、原关联 ID、Ref/hash、stage 和
Q01/Q06 evidence、响应 status/code/request_id 与分页数；正文和凭据不进入摘要。

```bash
python -B -m pytest tests/unit/test_recall_ownership_matrix_support.py -p no:cacheprovider --basetemp=<external-temp>
python -B -m pytest tests/integration/test_recall_ownership_matrix.py -p no:cacheprovider --basetemp=<external-temp>
```

严格辅助校验使用构造响应和错误变体，单独统计，不能替代真实后端安全验收。
