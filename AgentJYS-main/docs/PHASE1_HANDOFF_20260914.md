# Phase 1 — 异步可靠性交付

用户于 2026-09-14 明确要求连续实施 Phase 1 和 2，替代此前每阶段等待人工 review 的停点；仍独立记录阶段证据。未实现完整 Outbox。

## 修改与行为

- 三套 Memory/Context/Session work item 与 QueuePort：claim 生成新 claim_token；complete/fail/supersede 必须提交当前 token，并验证未过期 lease。旧 worker 不能覆盖新 owner。
- 三套内存 queue、三套 Redis queue：一致的 lease/reclaim/重试规则，pending(limit=...)。Redis 使用 due sorted set，以 ZRANGEBYSCORE LIMIT + 有界 MGET 读取到期 pending/lease/retry；Worker 不再全量读取后切片。
- Redis enqueue 的 dedupe/payload/due index 在 WATCH/MULTI 内原子提交；claim/complete/fail/retry 的 payload 与索引也原子更新。pending/claimed/failed work 无 TTL；成功/取代记录按原配置过期。
- SessionStore 增加游标 scan，SessionService.reconcile_extractions 从 committed Archive 重建工作，Runtime 每次 Session Worker drain 前推进一页。扫描使用 SCAN COUNT 提示，不是队列热轮询；每个 Session 的 Archive 数仍受上限控制。
- 未提取 Archive 不会被 max_archives 静默淘汰；已 succeeded Archive 不允许迟到 worker 降级。失败到重试之间进程退出时，pending/processing Archive 可修复遗留 failed job；明确 exhausted/failed Archive 不自动无限重试。
- compose.yaml 与 production override 显式使用 redis-data:/data、AOF everysec、RDB save 60 1000、noeviction。

文件范围：memory/projection.py、context_store/models.py 与 ports.py、session/models.py 与 ports.py 与 service.py、adapters 下六个 projection/extraction queue 及 session_store.py、application/projection.py/context_projection.py/session.py、runtime/service.py、两个 Compose 文件、pyproject.toml、tests/unit/test_queue_reliability.py、test_redis_queue_reliability.py、test_projection_reconciler.py、本报告。Phase 0 改动保留；B1、检索与 Context Kernel 算法、冻结 API 契约不改。

## 验证

先写的队列/恢复回归：15 failed、1 passed；随后修复。迟到 worker 降级 Archive 的补充测试也先失败再修复。

- 全量 pytest：445 passed、2 skipped（Textual/Docker 缺失）。新增 27 项；复核时补齐 Session supersede 的 token 保护，三个队列都实际执行 late-owner/supersede 断言。Redis 9 项使用 fakeredis，不代表真实 Redis 联调。
- Ruff：通过。compileall：通过。两个 Compose YAML 解析通过，未执行 Docker 部署。
- Mypy：与 Phase 0 相同，200 source files 中仅 p2/client.py:90 的 aether_agent_memory.p2.generated 缺失；没有新增类型错误。
- 命令：python -m pytest -q；python -m ruff check src tests scripts benchmarks；python -m mypy src；python -m compileall -q src。沿用 Phase 0 临时 venv，另安装 fakeredis；开发依赖已声明。

## Redis 的事实与恢复保证

| 数据 | 定位 | 保留边界 |
| --- | --- | --- |
| Memory 主记录、SessionRecord/Archive、Resource/Skill 主记录 | authoritative facts | 各存储原有 TTL 仍适用；Session 默认 30 天，不承诺无限保留。 |
| 三套派生 work、幂等记录 | 可由事实重建的交付/去重状态 | active work 无 TTL；terminal work 按配置清理；历史去重键可由后续维护回收。 |
| RetrievalTrace、AccessTrace、旧 ActionLog、B3 signals | telemetry/audit | 有限 TTL；不能替代业务事实。 |
| 缓存及 Celery broker/result | cache/任务传输状态 | 不能仅凭容器健康宣称业务工作完成。 |

container restart 与 recreate（同一 named volume，未执行 down -v）可加载 AOF/RDB；主机/进程突然崩溃时 everysec AOF 仍可能丢失最近约一秒，具体受 OS/存储影响。主机或磁盘永久损坏、volume 删除不在本地 volume 保证内，需要独立备份/复制/灾备。noeviction 使内存不足变成显式写入失败，不会靠随机淘汰权威事实释放内存。

升级旧 Redis 队列需先停止旧 worker，再对每个 queue 调用 migrate_legacy_index(cursor, limit=100)，按返回 cursor 循环至 0，之后启动新 worker；热轮询不会偷偷扫描旧全集。旧 work_id 与 payload key 保持兼容，新 claim 会生成 token；混跑新旧 worker 不安全。旧 TTL 已删除的 payload 只能从 Memory Reconcile / Context Reindex / Session Archive 重建。

## 已知边界与快照

lease token 保护队列状态迁移，不等于跨外部 Provider 的 exactly-once side effect；provider 幂等与 revision 仍必需。没有增加 lease heartbeat。真实 Redis restart/recreate/故障注入未运行。没有对 live 系统执行迁移或部署。

无 .git，无法提交独立 commit。Phase 1 修改前快照在 C:/Users/jorcy/AppData/Local/Temp/agentjys-phase12-1c51ac9d65/phase1-baseline；进入 Phase 2 前另保存 phase2-baseline，便于逐阶段 diff。

## Phase 1 文件索引

- [src/aether_agent_memory/memory/projection.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/memory/projection.py)
- [src/aether_agent_memory/context_store/models.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/context_store/models.py)
- [src/aether_agent_memory/context_store/ports.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/context_store/ports.py)
- [src/aether_agent_memory/session/models.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/session/models.py)
- [src/aether_agent_memory/session/ports.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/session/ports.py)
- [src/aether_agent_memory/session/service.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/session/service.py)
- [src/aether_agent_memory/adapters/projection_queue.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/projection_queue.py)
- [src/aether_agent_memory/adapters/context_projection_queue.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/context_projection_queue.py)
- [src/aether_agent_memory/adapters/session_extraction_queue.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/session_extraction_queue.py)
- [src/aether_agent_memory/adapters/redis_projection_queue.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/redis_projection_queue.py)
- [src/aether_agent_memory/adapters/redis_context_projection_queue.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/redis_context_projection_queue.py)
- [src/aether_agent_memory/adapters/redis_session_extraction_queue.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/redis_session_extraction_queue.py)
- [src/aether_agent_memory/adapters/session_store.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/session_store.py)
- [src/aether_agent_memory/application/projection.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/application/projection.py)
- [src/aether_agent_memory/application/context_projection.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/application/context_projection.py)
- [src/aether_agent_memory/application/session.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/application/session.py)
- [src/aether_agent_memory/runtime/service.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/runtime/service.py)
- [compose.yaml](D:/项目/JYS/AgentJYS-main/AgentJYS-main/compose.yaml)
- [compose.production.yaml](D:/项目/JYS/AgentJYS-main/AgentJYS-main/compose.production.yaml)
- [pyproject.toml](D:/项目/JYS/AgentJYS-main/AgentJYS-main/pyproject.toml)
- [tests/unit/test_queue_reliability.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_queue_reliability.py)
- [tests/unit/test_redis_queue_reliability.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_redis_queue_reliability.py)
- [tests/unit/test_projection_reconciler.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_projection_reconciler.py)

以上为 Phase 1 精确文件范围（另含本报告），不包括 __pycache__。部分文件在 Phase 2 又有接线改动；阶段审查请以 phase1-baseline 与 phase2-baseline 比较，最终工作区的 Phase 2 结果见 [docs/PHASE2_HANDOFF_20260914.md](D:/项目/JYS/AgentJYS-main/AgentJYS-main/docs/PHASE2_HANDOFF_20260914.md)。
