# Phase 2 — C Representation-Level Control Domain 交付

日期：2026-09-14。范围：Phase 2 的代码实现、Standalone / Simulator 验证；不等于 Production 或 Real Integration 验收。

用户明确要求“直接进入 phase1 和2”，因此本轮连续实施两阶段，分别保留报告与基线；没有进入 Phase 3。Phase 1 记录见 [docs/PHASE1_HANDOFF_20260914.md](D:/项目/JYS/AgentJYS-main/AgentJYS-main/docs/PHASE1_HANDOFF_20260914.md)。

## 结果与边界

Production / Integration 的 Application SchedulerPort 已切换到新 OperateSchedulerAdapter。新 C Domain 不依赖旧 B3 HeuristicScheduler，也不引入 P2 数据平面实现；旧 B3 仍保留兼容调用，不删除。

主链如下：

```text
ScheduleMemoryUseCase → SchedulerPort → OperateSchedulerAdapter
  Memory Fact + MemorySignal + AccessTrace
    → RepresentationValueState
    → RepresentationPlacementPlan（多个 target）
    → ActionPlanner → TierAction
    → durable intent / CAS → StorageControlPort
    → ActionFeedback + observed placement → reconciliation
```

冻结 Northbound v1 六个 API contract 未改；未重构 B1、Unified Retrieval、Context Kernel 算法。application/services.py 仅增加 AccessTrace representation 标记；B2 仅调整 Signal heat/task metadata。不存在整个 Memory 从 L3 搬到 L0 的成功声明。

最终边界复核：B1、context、memory/retrieval、api 共 37 个非缓存文件与 Phase 1 起点逐文件 hash 一致。contracts/p3-northbound-v1.json 与 Phase 0 原始快照 SHA256 均为 2E223D5036EB10D0960DED898E5D0C4FAE271611416A652E0B0D00B1A14AF27D；阶段快照未复制 contracts，因此契约使用 Phase 0 原始快照核对。

## Task 2.1–2.8 对照

| Task | 实现及证据 | 保留边界 |
| --- | --- | --- |
| 2.1 Representation Domain | RepresentationRef、RepresentationValueState、ActuationTarget、RepresentationPlacementPlan、TierAction、ActionFeedback；kind 仅 canonical/vector/compressed。 | Ref 含 memory_id、representation_id、kind、revision；scope 单独携带。 |
| 2.2 Value / Heat | ValuePolicy 读取 Fact + Signal + AccessTrace，按 tenant/user/agent/session/task 及 representation 过滤；importance 为输入、heat 为衍生结果。 | 不读取旧 MemoryValueState.heat / MemorySignal.heat；启发式参数尚无收益验收。 |
| 2.3 Placement | PlacementPolicy 消费 observed placement 与资源状态；保留 authoritative，可增加/回收 cache；VECTOR 保留 Provider placement。 | 不由 MemoryType 推断 tier；没有实现权威原文迁移。 |
| 2.4 Action Plan | ActionPlanner 比较 desired / observed 多目标集合，输出 representation + target 级 action；禁止回收 authoritative。 | 当前启发式自动生成 KEEP/PROMOTE/DEMOTE；PREFETCH 已在模型/执行契约支持，尚无专门预测策略。 |
| 2.5 Lifecycle | GENERATED → SUBMITTED → SUCCEEDED / FAILED / UNKNOWN；Shadow 保持 GENERATED，不提交 actuator。 | SKIPPED 只存在于旧响应 DTO 兼容翻译，不是新 Domain 状态。 |
| 2.6 Feedback / Reconcile | 结果写 SQLite journal；成功需新鲜、scope/ref/revision 一致且 epoch 足够的 observed evidence；UNKNOWN 查询动作/placement，禁止盲目重提。 | Reconcile 由 schedule 请求或显式控制器调用驱动，尚无独立常驻 C worker。 |
| 2.7 Simulator | Stateful representations、placements、capacity/used、actions、outcomes、feedback；覆盖八类指定故障。 | 模拟 P3 contract，不执行物理存储迁移，不是 P2 实现。 |
| 2.8 Wiring | Composition Root 的 production/integration SchedulerPort 使用新 adapter；配置 HTTP 控制端或 Simulator/Unavailable adapter。 | 真实兼容控制服务未部署/联调；production 未配置时显式不可用，不回落到模拟成功。 |

Heat 为 0.4 × frequency + 0.4 × semantic + 0.2 × recency；semantic = (importance + relevance) / 2。频次证据与相关性随时间衰减，默认小时级尺度；importance=1 而无访问证据时 heat=0.2，不等于 importance。热度阈值、容量与迁移预算仍属于 heuristic-v1，不构成优化收益证明。

## 状态、幂等与重启

- SQLite journal 在 remote submit 之前持久化 intent，使用 WAL + synchronous=FULL；GENERATED → SUBMITTED 为原子 CAS。
- action_id 对应不可变 intent；同一 ID 不同内容拒绝。同一 scope / representation / target 的 unresolved LIVE action 占用 fence，阻止用新 ID 绕过 UNKNOWN 重提。Fence 不含 revision，旧 revision 未决动作也会阻挡新 revision。
- submit timeout 或其他可能发生在 dispatch 后的异常标记 UNKNOWN；后续查询动作/placement，证据不足继续 UNKNOWN。
- SUCCEEDED 必须验证目标 placement 收敛；旧 observation、错误 revision、scope 不匹配不能伪装成功。Feedback/observed state 随 action 持久化，并用于后续 reconcile。
- LIVE intent 已 reserve 但尚未 SUBMITTED 时退出：恢复扫描识别 GENERATED，CAS 取消为 FAILED（明确未提交），释放 fence，要求新计划；若并发提交者已经赢得 CAS，则按 SUBMITTED 路径查询，不能取消未知远端结果。Shadow GENERATED 不进入恢复扫描。
- journal 位于 data_dir/operate-actions.db。Compose 的 P3 使用既有 p3-data volume；单机、共用同一 SQLite 文件的控制器可协调，不承诺多主机分布式一致性。
- 本轮未新增 journal/plan-history 保留策略；Plan 在返回 metadata 中可审查，不是独立持久化的计划历史库。

## Simulator 故障证据

| 故障 | 预期与已测行为 |
| --- | --- |
| submit response timeout but succeeded | 首次 UNKNOWN；查询后 SUCCEEDED；submission_count 仍为 1。 |
| accepted then failed | 首次 SUBMITTED；reconcile 后 FAILED，无缓存成功误报。 |
| succeeded but feedback lost | 丢失反馈事件后仍可查询 action/placement 收敛，不重复 submit。 |
| P3 restart while action running | 重建 Controller + SQLite journal，保留远端 Simulator 状态；finish 后查询恢复成功。 |
| duplicate action_id | 同 intent 幂等；不同 intent 拒绝；两个 Controller 并发仅提交一次。 |
| desired != observed | 假成功保持 UNKNOWN，不宣称缓存已到位。 |
| stale placement / revision | 旧 epoch/revision 拒绝；过期 observation 不产生物理变更；旧成功 snapshot 不足以证明成功。 |
| capacity full | 显式失败；Policy 同时检查容量/网络/动作数/迁移字节预算。 |

重要区别：上面的 P3 restart 测试重建的是 Controller/journal，远端 Simulator 实例保留。默认进程内 Simulator 本身没有跨进程持久化；完整应用重启会重建模拟状态。当前测试不能证明真实 Provider 或整套 Compose 的重启恢复。

## Runtime 配置与控制协议

- AETHER_P3_STORAGE_CONTROL_URL：兼容 Storage Control HTTP 服务的 base URL；默认空。AppSettings、legacy config bridge 与 compose.yaml 均已透传。
- AETHER_B3_SHADOW_MODE：保留原配置名，默认 true，作用于新 scheduler；仅显式设置 false 才允许 LIVE submit。配置 URL 不会自动启用 LIVE。
- AETHER_P3_DATA_DIR：journal 所在目录；默认 Compose P3 值为 /var/lib/aether-p3，由 p3-data volume 挂载。
- integration/demo 未配置 URL：使用 Stateful Simulator。production 未配置 URL：使用 UnavailableStorageControl，C 健康状态 degraded，实际调度报依赖不可用；A/B 不因缺少 C 控制能力而被伪造为 C 成功。
- HTTP 模式支持下表，均为相对 base URL 的路径。消息 schema 见 [src/aether_agent_memory/operate/models.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/operate/models.py)，不是冻结 Northbound API 的变更。

| 方法与路径 | 输入 | 输出 |
| --- | --- | --- |
| POST actions | TierAction JSON | ActionFeedback JSON |
| POST actions/query | TierAction JSON | ActionFeedback JSON；404 表示未知/查无动作，不能直接推导可重试 |
| POST placements/query | representation + scope JSON | PlacementObservation JSON |

HttpStorageControl 默认 timeout=5 秒。当前绑定约定 submit 的 4xx 是确定拒绝并标记 FAILED；5xx/传输超时等按可能已执行处理为 UNKNOWN。真实服务接入必须确认这一语义，尤其不能把“已执行但响应超时”包装成 4xx。鉴权、TLS/mTLS、资源发现等正式环境接入条件未在本轮验收。

真实 actuator 还必须自行落实 action_id 幂等、scope/revision/epoch 验证、authoritative 保护和容量 guard。现有 P2 logical segment routing 不等于 representation cache placement；本轮没有把它包装为已完成的物理动作。HTTP 测试用 MockTransport，不是对真实服务发请求。

旧 DTO 为兼容仍需单一 source_tier/target_tier 时使用显式占位；这不进入 C 决策。真实 action_state、execution_mode、representation target 与 placement_plan 在 metadata 中，whole_memory_migration=false；new_tier 始终不用于宣称整条 Memory 已迁移。

## Changed files（本阶段）

新增 10 个源文件：

- [src/aether_agent_memory/operate/models.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/operate/models.py)：表示、目标、计划、状态与反馈模型。
- [src/aether_agent_memory/operate/policy.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/operate/policy.py)：ValuePolicy / PlacementPolicy / ActionPlanner 分离。
- [src/aether_agent_memory/operate/ports.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/operate/ports.py)：控制端与 journal 边界。
- [src/aether_agent_memory/operate/controller.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/operate/controller.py)：提交、状态机、UNKNOWN 与重启恢复。
- [src/aether_agent_memory/operate/__init__.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/operate/__init__.py)：Domain 包入口。
- [src/aether_agent_memory/adapters/operate_journal.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/operate_journal.py)：SQLite 持久意图、CAS、active fence。
- [src/aether_agent_memory/adapters/storage_control_simulator.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/storage_control_simulator.py)：有状态控制模拟器及故障注入。
- [src/aether_agent_memory/adapters/storage_control.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/storage_control.py)：HTTP 绑定与显式 unavailable adapter。
- [src/aether_agent_memory/adapters/operate_telemetry.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/operate_telemetry.py)：有界读取 Signal / AccessTrace；Redis 最近 100 条。
- [src/aether_agent_memory/adapters/operate_scheduler.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/operate_scheduler.py)：SchedulerPort 新主链及旧 DTO 边界翻译。

修改 7 个既有源文件与 1 个配置文件：

- [src/aether_agent_memory/bootstrap/container.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/bootstrap/container.py)：production/integration 新调度主链与资源构建。
- [src/aether_agent_memory/config/app_settings.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/config/app_settings.py)：Storage Control URL 与非敏感状态展示。
- [src/aether_agent_memory/runtime/legacy.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/runtime/legacy.py)：新配置透传；保留 legacy 兼容实现。
- [src/aether_agent_memory/runtime/service.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/runtime/service.py)：关闭新 scheduler 及其资源。
- [src/aether_agent_memory/b2/service.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/b2/service.py)：移除 heat=importance，Signal 保留 task scope。
- [src/aether_agent_memory/b3/application.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/b3/application.py)：取消 MemoryType → tier 推断；未知 placement 的旧 DTO 占位不授权迁移。
- [src/aether_agent_memory/application/services.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/application/services.py)：向量检索 AccessTrace 加 representation_kind 标签。
- [compose.yaml](D:/项目/JYS/AgentJYS-main/AgentJYS-main/compose.yaml)：传入 Storage Control URL 与默认 true 的 Shadow 开关；Phase 1 Redis 改动保留。

新增测试与文档：

- [tests/unit/test_operate_control.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_operate_control.py)：23 个控制域/故障/恢复用例。
- [tests/unit/test_operate_wiring.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_operate_wiring.py)：7 个接线/信号/真实 UseCase→新 Domain/Compose 配置用例。
- [docs/PHASE2_HANDOFF_20260914.md](D:/项目/JYS/AgentJYS-main/AgentJYS-main/docs/PHASE2_HANDOFF_20260914.md)：本报告。
- [docs/PHASE1_HANDOFF_20260914.md](D:/项目/JYS/AgentJYS-main/AgentJYS-main/docs/PHASE1_HANDOFF_20260914.md)：补充精确文件链接，属于 Phase 1 交付记录完善。

以上不包含 __pycache__、pytest cache 等生成物。工作区原有内容保留，没有执行清理或删除。

## Commands run / Validation

使用临时环境，不修改基础 Python：

```powershell
$env:PYTHONPATH=(Resolve-Path 'src').Path
$env:PYTHONDONTWRITEBYTECODE='1'
$phasePython='C:\Users\jorcy\AppData\Local\Temp\agentjys-phase0-5a6dc26b8a\venv\Scripts\python.exe'
& $phasePython -m pytest -q -rs
& $phasePython -m ruff check src tests scripts benchmarks
& $phasePython -m mypy src --cache-dir 'C:\Users\jorcy\AppData\Local\Temp\agentjys-phase12-1c51ac9d65\mypy'
$env:PYTHONPYCACHEPREFIX='C:\Users\jorcy\AppData\Local\Temp\agentjys-phase12-1c51ac9d65\pycache'
& $phasePython -m compileall -q src
```

| 检查 | 结果 |
| --- | --- |
| Phase 0 基线 | 418 passed。 |
| Phase 1 独立阶段结果 | 445 passed、2 skipped；新增 27 项。 |
| Phase 2 最终全量 pytest | 475 passed、2 skipped、2 warnings；本阶段新增 30 项，两阶段合计新增 57 项。 |
| Ruff | 最后一次全部通过；中间新增恢复分支触发一处行长错误，已仅换行修正后复检。 |
| compileall | 通过。 |
| Mypy | 未全绿：210 source files 中仅原有 p2/client.py:90 的 aether_agent_memory.p2.generated 缺失，无新增类型错误。 |
| Compose YAML | 两个文件均成功解析；不是 docker compose 启动验收。 |
| skipped | Textual 未安装；Docker 未安装。 |
| warnings | jieba 的 pkg_resources 弃用警告；FastAPI/Starlette TestClient 的 httpx 弃用警告。 |
| 未运行 | 真实 Redis restart/recreate、真实控制端联调、物理迁移、部署、性能与优化收益验收。 |

Red → Green 证据：初始 Domain 导入失败；接线/heat 别名/tier 推断 4 项先失败；过期成功反馈、跨 task heat、旧访问 heat 衰减各有先失败回归；Compose 两个透传断言先失败；pre-submit 重启恢复先因 pending 为空失败。分别实现修正后通过。没有仅为保持旧测试而保留 heat=importance 或 MemoryType→tier。

## Known gaps / 未验证项

1. 真正的 Provider 控制服务及其幂等、身份验证、placement 查询可靠性未联调。Simulator / MockTransport PASS ≠ Real Integration PASS。
2. journal 依赖本地共享 SQLite 文件；没有多主机 lease/leader 机制、日志保留/压缩与独立 plan-history store。正式高可用需要后续设计与验收。
3. Reconciliation 当前由 schedule/显式调用触发；无后续请求的 scope 不会自动运行 C 恢复。UNKNOWN 无确定结果会持续占用 fence；不强行转为重试成功。
4. 同一 representation 多 target 改变可能因首个动作推进 epoch 而让后续动作需要重新规划；没有宣称多目标原子事务。
5. Simulator 初次注册表示后，Memory revision 更新需要同步模拟/远端 representation 状态；旧 observed revision 会被拒绝，不静默视作最新版。
6. 资源预算为请求级启发式，真实容量当前由请求 ResourceState/Provider 自身 guard 约束；没有资源隔离或优化收益验收。预算消耗保守计入拟执行动作。
7. 新旧 API 边界兼容保留部分 legacy 字段，调用方应以 metadata 中的 representation-level 状态为准，不能把 SKIPPED 或占位 tier 当作新 Domain 结论。
8. 旧 B3 仍被 [src/aether_agent_memory/runtime/legacy.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/runtime/legacy.py)、[src/aether_agent_memory/demo/flow.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/demo/flow.py)、[src/aether_agent_memory/b3/__init__.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/b3/__init__.py) 及兼容 adapter 引用，因此没有删除。新 production/integration SchedulerPort 不调用 LegacySchedulerAdapter。
9. Phase 1 的 Redis 真正持久化、队列迁移、主机/磁盘故障恢复仍需独立验证；详见 Phase 1 报告。未实现完整 Outbox。

## 审查与后续停点

当前目录及祖先没有 .git，不能按阶段创建 commit；未擅自初始化仓库。通过两个快照区分阶段改动：

- Phase 1 起点：C:/Users/jorcy/AppData/Local/Temp/agentjys-phase12-1c51ac9d65/phase1-baseline
- Phase 2 起点（Phase 1 完成态）：C:/Users/jorcy/AppData/Local/Temp/agentjys-phase12-1c51ac9d65/phase2-baseline

Phase 2 起点快照已同步 Phase 1 的 Session supersede 收口修正，以便 code diff 不混入 Phase 1 后补项；它是审查基线，不是假称 Git commit。快照在临时目录，长期交付应另行归档。

当前停在 Phase 1/2 review。建议人工审查 Redis 旧队列升级与 Storage Control 协议，随后另行授权 Phase 3 的 A/B/C 分流验收。本轮没有创建部署、执行真实动作，也没有进入 Phase 3。
