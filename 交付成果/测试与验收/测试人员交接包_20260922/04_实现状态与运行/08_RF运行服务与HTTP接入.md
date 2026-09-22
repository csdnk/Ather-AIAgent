> 测试交接副本：2026-09-22；代码基线 `2ee5b0e`。正文保留原日期及原批次范围，链接已调整。当前可测范围以交接包入口及《11_RF公共底座实现与接入》为准。

# RF运行服务与HTTP接入

日期：2026-09-22。Owner：RF。复用SQLiteUnitOfWork、Tasks、身份、事件和原有三流程，不替换领域状态机。

## 已接入的公共服务

| 服务入口 | 对象与实际行为 |
|---|---|
| `Foundation.monitoring` | `MonitoringPort.health/emit`已有实现。真实探针报告转为HealthObservation及RuntimeHealthSnapshot，按主体/作用域/授权代次缓存，过期不再ready；旧Diagnostics.health读取同一份能力证据 |
| `Foundation.telemetry` | 每个现有节点日志同时产生NodeLogRecord。保留旧日志结构和查询兼容；新监测查询输出p3/log/2，按当前主体和scope过滤 |
| `Foundation.tasks.progress` | 在现有任务上持久TaskWaitRecord、CheckpointRecord、WorkerHeartbeat；等待绑定领取令牌，旧等待不能影响新尝试。任务业务结果与completed断点、任务完成共用原事务 |
| `Foundation.dispositions` | 持久注册SignalDefinition、OperationDefinition、DispositionRule；采样间隔、过期、标签上限、连续次数和冷却生效。IncidentRecord与维护Task同事务建立；维护完成后独立复验才resolved |
| `Foundation.lifecycle` | ConfigurationSnapshot按旧版本条件激活并保留历史；BackupManifest及ResourceLocation记录SQLite在线备份。恢复到新目录，实际核对完整性、记录指纹和一致性水位 |
| `runtime.flows.http.create_app` | 带身份验证的本地HTTP Host；复用既有Remember/Recall、任务查询、恢复、监测、配置、备份服务；后台循环调用同一个ThreeFlows.tick |

`register_signal(definition, sampler)`、`register_operation(definition, handler)`、`register_verifier(name, callback)`在可信启动装配中调用。重启时重新注册回调，持久规则、信号和任务不丢弃。`configure_rule(ctx, rule)`要求配置权限并核对连续修订；`observe(ctx, subject, sample)`处理一个有证据的样本；`cycle(ctx)`采样并对账。部署方需要按实际依赖注册采样器及领域维护处理器，当前不会自作主张重启Provider或把记忆强制标为ready。

维护处理器输入是持久的`runtime.IncidentRecord`；本地适配要求`OperationDefinition.input_model=runtime.IncidentRecord`、`output_model=runtime.RunResult`；持久业务结果仍通过RunResult.result_ref引用。操作的Owner必须匹配subject的Owner。处理器继续实现TaskHandler，使用原Tasks.complete；配置为query_only的操作不能通过recover返回resume来绕过限制。`verification_operation`绑定独立的异步回调，检查真实业务状态，并返回同作用域、可读取且已持久的证明引用。仅返回任务完成状态不能代替业务核验。

等待附件由处理器在有租约的事务中调用`tasks.progress.wait(tx, ctx, task, record)`写入；随后返回与其一致的RunResult，RF使用next_check_at安排下一次查询/执行。断点用`checkpoint(...)`与阶段输出同事务写入。额外阶段是否可以恢复由领域处理器决定，公共底座不会猜测业务进度。

## 本地HTTP启动

先按既有Foundation身份接入方式在数据库登记主体及凭据摘要；不预置默认密码。安装项目依赖或`scripts/p3/requirements-collaboration.txt`。从业务仓库运行：

```text
python -m aether_agent_memory.runtime.flows --db <数据库绝对路径> --cache-root <运行缓存绝对路径> --embedding-profile lexical serve --host 127.0.0.1 --port 8080
```

lexical只用于基础联调；正常模型运行使用native及其模型配置。数据库、缓存、日志和备份均放项目外运行工作区。后台维护可由启动装配注册规则，并通过`create_app(runtime, maintenance_credential=取凭据的函数)`启用周期采样；CLI从P3_MAINTENANCE_KEY环境变量读取维护凭据。业务请求使用`Authorization: Bearer <凭据>`，幂等操作使用`X-Operation-ID`，凭据不进入日志。

| HTTP入口 | 用途 |
|---|---|
| GET `/p3/live` | 进程响应检查，无业务或身份数据 |
| GET `/p3/health`、`/p3/ready` | 运行真实有界探针；后者未ready返回503；不执行维护动作 |
| GET `/p3/runtime`、`/p3/logs/{trace_id}` | 当前授权范围的运行与日志查询 |
| GET `/p3/tasks/{task_id}`、`/p3/tasks/{task_id}/progress` | 任务状态及等待/断点 |
| POST `/p3/recovery` | 复用RecoveryRequest及原任务恢复入口 |
| GET `/p3/incidents`、POST `/p3/maintenance/cycle` | 查看可授权异常；推进注册采样器和业务验证 |
| PUT `/p3/configuration` | 提交snapshot及expected_version，登记当前部署配置快照 |
| POST `/p3/backups` | `{ "backup_id": "backup_01" }`创建在线备份 |
| POST `/p3/restore-drills` | `{ "backup_id": "backup_01", "restore_id": "restore_01" }`独立恢复并核验 |
| POST `/p3/remember`、`/p3/recall` | 原契约RememberRequest、RecallRequest；RecallRequest.sources仍是working/long_term/both/auto字符串 |

配置及备份要求主体同时具备CONFIGURE权限并在启动时显式列入`maintenance_principals`（CLI参数`--maintenance-principal`）。原因是RF数据库可能包含多个租户，普通租户维护权限不授予全库备份权。默认名单为空。目标路径由配置的backup_root和受限ID推导，不接受请求直接给任意文件路径；已有文件不覆盖，在线库不替换。

配置快照描述已部署的提供方与策略，config_hash是去掉config_hash/activated_at字段后的RFC8785 SHA-256。它不执行模型热加载、提供方迁移或秘密值注入；这些部署动作需要相应提供方接入。

## 验证及边界

新增验证位于`tests/runtime/p3/test_runtime_services.py`和`tests/runtime/flows/test_http.py`，进入原有协作门禁。覆盖租约与回滚、等待时序、真实日志格式、健康过期、租户隔离、连续采样去重、持久重启、维护后复验、复验期间撤权、未知操作不重建、真实备份恢复、HTTP鉴权及后台任务运行。

备份只覆盖RF SQLite数据库，包括其中内联记忆和任务记录，不包含外部向量库、原件Provider、模型文件和独立日志库。restore_state=passed表示此次隔离恢复和数据库内容核验成功，不是生产全系统恢复验收。HTTP服务为本地运行实现，尚未验证公网网关、分布式部署、Azure/AKS、OTLP或生产压测。B/A/C新增多块发布、全文组包、地址交接等接口仍需其各自业务实现。

接口catalog保留契约快照的implemented字段；当前实际运行状态以本文件和测试证据为准，不能用统一的contract_only标签推断所有接口仍无实现。
