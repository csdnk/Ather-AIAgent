# Phase 0 交付与人工审查 — 2026-09-14

## 结论与边界

本轮仅实现 Phase 0：Memory Fact-First 与 AccessTrace 正确性。没有进入 Phase 1/2/3。

Standalone 全量测试为 **418 passed、2 skipped**；Ruff 和 compileall 通过。Mypy 仍有 **1 个修改前已存在的缺失模块错误**，因此不能宣称全部质量门禁通过，也不能宣称 Real Integration 或 A/B/C Production 验收通过。

A/B/C 保持业务责任流；B1/B2/B3 仍为历史实现模块。本轮没有调整这些边界。

## Changed files

共 7 个实现文件、5 个测试文件，以及本交付文档。

| 文件 | 修改 |
| --- | --- |
| [src/aether_agent_memory/semantic/manager.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/semantic/manager.py) | write 只保存事实，移除前台 embedding 和 Milvus upsert；保留 recall 和构造参数兼容性。 |
| [src/aether_agent_memory/episodic/manager.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/episodic/manager.py) | 同样移除 embedding-before-save，覆盖直接写入与归档路径。 |
| [src/aether_agent_memory/b2/memory_consolidation.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/b2/memory_consolidation.py) | 直接 consolidation 先保存源事实，再调用 extractor、合并或修订。 |
| [src/aether_agent_memory/b2/service.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/b2/service.py) | 直接压缩先保存源事实及 pending 状态；归档新 ID 不继承旧 ID 的 vector ready 状态。 |
| [src/aether_agent_memory/adapters/projection_executor.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/adapters/projection_executor.py) | 现有 Worker 完成投影时同步 typed status 与 metadata status，避免恢复后一个 succeeded、一个 pending。 |
| [src/aether_agent_memory/application/services.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/application/services.py) | single 真正调用 record；batch 优先 record_many；legacy fallback 每条一次，失败 best-effort；Recall 构造 trace 时填充 scope。 |
| [src/aether_agent_memory/memory/retrieval/models.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/src/aether_agent_memory/memory/retrieval/models.py) | AccessTrace 增加可选 tenant/user/agent/session/task 字段，兼容旧 trace；未改 Retrieval 算法。 |
| [tests/unit/test_fact_first.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_fact_first.py) | 新增 12 个测试用例，含 B1 故障与恢复、入队顺序、Session、归档、合并、压缩及丢入队检查。 |
| [tests/unit/test_long_text_fact_first.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_long_text_fact_first.py) | 新增 3 个 standalone 用例：broker 成功/失败、B1 故障后 Worker 重跑恢复。 |
| [tests/unit/test_access_trace.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_access_trace.py) | 原有 2 个测试保留，新增 12 个参数化展开用例。 |
| [tests/unit/test_semantic_manager.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_semantic_manager.py) | 更新异步写入语义；召回质量测试显式准备向量，加强排序及非零得分断言。 |
| [tests/unit/test_episodic_manager.py](D:/项目/JYS/AgentJYS-main/AgentJYS-main/tests/unit/test_episodic_manager.py) | 更新异步写入语义；显式准备向量，保留衰减与相关性验证。 |
| [docs/PHASE0_HANDOFF_20260914.md](D:/项目/JYS/AgentJYS-main/AgentJYS-main/docs/PHASE0_HANDOFF_20260914.md) | 本次交付、测试证据、路径矩阵与未闭环项。 |

## Behavior changed

1. Semantic/Episodic write 返回表示 **Fact persisted**，不再表示 embedding/vector 已 ready。新事实默认 embedding_status=pending、vector_projection_status=pending。已有合法 embedding 可保留，不在写入中调用 B1。
2. 应用写入成功后复用现有 Projection Queue；直接 manager 写入不引入新队列机制，可通过现有 Reconcile 生成工作。必须运行现有 Worker，不能假设异步投影会自行完成。
3. Worker/provider 故障不删除 Memory；队列记录失败，事实保持 pending/failed。B1 恢复后已有 Worker 可完成 embedding/vector，状态才变为 succeeded。
4. Consolidation/直接压缩失败仍可能向调用方抛错，但源事实已保存。**请求报错不等于事实不存在**；本轮没有把所有派生异常重写成成功响应。
5. 归档得到新 Memory ID：不再继承旧 ID 的向量索引成功状态；内容未变时可以复用 embedding。
6. AccessTrace 缺省 adapter 仍是 no-op；adapter 抛错不改变业务返回。批量部分写入后抛错时不再回退重放，以免制造重复；legacy adapter 某条失败后仍尝试其余条目。
7. 未新增/修改冻结 Northbound v1 六个 API 的请求或响应契约。Fact/Projection 区分使用现有 Memory 状态字段表达。

## Write-path 审计矩阵

这里的“符合 Fact-First”指主事实在派生 provider 工作之前持久化，不等于所有派生任务都已有自动可靠恢复。

| Write path | Fact persistence point | Derived work point | Failure behavior / recovery |
| --- | --- | --- | --- |
| Working direct write | BaseMockMemoryManager.write → MemoryStore.upsert | 不同步调用 B1；需要时由上层入队/Reconcile | 存储失败才无法保存事实；不依赖 embedding。 |
| Semantic / Episodic direct write | 各 manager.write → Base.write → MemoryStore.upsert | MemoryProjectionReconciler → 现有 Projection Worker | B1 不可用时仍可读取 SQLite 事实，状态 pending；测试已验证重开 store、规划、失败及恢复。 |
| Memory Event | WriteMemoryUseCase → MemoryService.ingest → manager.write | 可选 consolidation/compression 在保存之后；应用最终 best-effort enqueue | B1 不在保存路径；queue enqueue 丢失后事实仍在，Reconcile 可重建 embedding/vector 工作。 |
| Session Extraction | SessionService.commit 已保存 Archive；candidate 经 WriteMemoryUseCase 保存 Memory | 先从 Archive 抽取 candidate，再形成 Memory，最后投影入队 | B1 故障不阻断 candidate 的 Memory 保存。若 extraction 自身失败，Archive 是保留的源事实；Session 丢入队自动恢复留待 Phase 1。 |
| Long Text | API 必要时先保存原文对象；submit_long_text 在 task status/broker 调用前 _persist_memory | Celery worker 读取/创建事实后 compression → B1 → P2 vector，可选 Milvus | broker/B1 故障后 Memory 仍可读；B1 恢复并重跑同 payload 可完成。P2 原文对象保存是 primary-data 操作，不应与向量投影混为一谈；原文保存本身失败时提交仍会失败。 |
| Consolidation / Merge | MemoryConsolidator.consolidate 先 manager.write(source) | extractor → enriched fact → duplicate merge/conflict revision | extractor 异常保留源事实；Semantic manager 不再调用 B1。合并并非跨记录事务，本轮没有新增并发/中断恢复保证。 |
| Legacy archive_session | 新 Episodic ID 先保存，然后处理可选压缩及源 Working 的 archive 状态 | 复用已有 embedding，新 ID vector=pending；Reconcile 规划 | B1 不再阻断归档写入；新 ID 不会误报旧索引已覆盖它。后续失败可能留下两个记录，未承诺事务原子性。 |
| Direct compress_memory | 保存源 Memory，并显式 compression_status=pending | compress_and_store → attach artifact → 再保存 | compressor/artifact 失败时源事实仍在且 SUMMARY 可规划；通用 SUMMARY 执行器目前未配置，需要既有 B2 compression 路径重试。 |

Memory Event、Session Extraction 使用真实应用链路与内存 adapter 验证；直接写入和长文本故障测试使用真实 SQLite 持久化。B1/P2/broker 均为受控替身，无外部服务依赖。

## Tests added / red → green

共新增 **27 个参数化展开用例**，不是 27 个测试函数。三个针对性文件最终共 29 个用例，含原有 AccessTrace 2 个。

- 在源代码修改前：核心新增回归加原有 AccessTrace 用例为 **12 failed、9 passed**。
- 长文本测试夹具修正后、源代码修改前：三个文件为 **12 failed、12 passed**。长文本已有 Fact-First 顺序，3 项本来通过，不伪称修复了原本不存在的问题。
- 后续先添加边界断言：归档新 ID 错误继承 ready、直接压缩先压后存分别失败，再修改实现。
- 加强恢复断言后：Semantic/Episodic 的 typed status 已 succeeded，但 metadata 仍 pending，2 项失败；再修改现有 Executor 的状态写回。
- 原召回测试中同步 embedding 假设导致 6 项失败；改为显式准备已完成向量，同时加强相关性排序断言，没有降低断言来迁就无向量零分。
- 最终全量：**418 passed、2 skipped、2 warnings，5.76 秒**。

早期新增长文本测试曾误用 SQLite.close 和将同步 compress 当作 coroutine；已修正测试夹具。这些夹具错误不计作产品缺陷。

## Validation commands and results

工作目录：D:/项目/JYS/AgentJYS-main/AgentJYS-main

隔离解释器（未修改基础 Conda 环境）：

```powershell
$phase0Python = 'C:\Users\jorcy\AppData\Local\Temp\agentjys-phase0-5a6dc26b8a\venv\Scripts\python.exe'
$env:PYTHONPATH = (Resolve-Path -LiteralPath 'src').Path
$env:PYTHONDONTWRITEBYTECODE = '1'

& $phase0Python -m pytest -q -rs
& $phase0Python -m ruff check src tests scripts benchmarks
& $phase0Python -m mypy src --cache-dir 'C:\Users\jorcy\AppData\Local\Temp\agentjys-phase0-5a6dc26b8a\mypy-phase0'
$env:PYTHONPYCACHEPREFIX = 'C:\Users\jorcy\AppData\Local\Temp\agentjys-phase0-5a6dc26b8a\pycache'
& $phase0Python -m compileall -q src
```

| 检查 | 结果 |
| --- | --- |
| pytest | 418 passed；跳过 Textual 场景（未安装 textual）、Docker Compose（未安装 Docker）。 |
| Ruff，src/tests/scripts/benchmarks | All checks passed。仅格式化了本轮 5 个测试文件。 |
| Mypy，200 source files | 1 error：aether_agent_memory.p2.generated 缺失，位于 p2/client.py:90；修改前与修改后相同，没有新增错误。未靠 suppress 或伪造 stub 使其变绿。 |
| compileall src | exit 0；缓存输出到临时目录。 |
| 边界核查 | 实现差异仅限上列 7 个文件；contracts、API schemas/routers、B1、Unified Retrieval 实现、Context Kernel、B3、bootstrap、队列协议/claim 实现没有修改。 |

临时 venv 继承 system-site-packages，另装 celery 5.6.3、redis 5.3.1、jieba 0.42.1 及依赖，以执行已有及新增 Celery 路径。不是干净 lockfile 重建。环境仍有原有版本偏差/可选依赖缺失（例如 Pydantic、pydantic-settings 低于项目声明版本，以及 pymilvus/OTel/structlog 缺失），不能代表生产依赖验收。Warnings 为 jieba/pkg_resources 与 Starlette/httpx 弃用提示。

## Known gaps / 不扩大本轮承诺

- Phase 1：claim token、bounded Redis polling、SessionExtraction lost-enqueue recovery、Redis durability 未实现。
- 通用 SUMMARY executor 仍明确未配置；能规划 SUMMARY 不等于可以自动执行压缩恢复。
- 直接 manager/legacy 服务调用需要显式 Reconcile；没有新增自动周期扫描。
- 默认既有生产向量适配器仍负责原有后端；本轮没有新增 Semantic 专用队列或把可选 Milvus 投影扩为新的多目标 fan-out。
- Memory Event 可选 consolidation/compression 仍是“事实之后的同步处理”；本轮仅去除前台 embedding/vector projection，不重写所有 B2 调度。
- 原文 Single Ownership、Outbox、跨记录事务/幂等并发问题尚未收口。
- Phase 2 representation-level C、Storage Control Simulator 和 Phase 3 分流验收均未开展。
- 未运行真实 B1/P2/Milvus/Redis 或 Celery broker 联调、重启/重建测试、2000 QPS、SLO/资源隔离验收。测试替身通过不代表 Real Integration 通过。
- Mypy 的缺失 generated 模块仍需在原有 P2 codegen/构建流程中解决。

## Git / 可审查性与下一步

工作目录及祖先没有 .git，无法按“每阶段单独 commit”完成提交；没有擅自 git init。

修改前备份保留在：
C:/Users/jorcy/AppData/Local/Temp/agentjys-phase0-5a6dc26b8a/baseline

可用只读差异比较（exit 1 代表存在差异）：

```powershell
git -c core.autocrlf=false diff --no-index -- 'C:\Users\jorcy\AppData\Local\Temp\agentjys-phase0-5a6dc26b8a\baseline\src' 'src'
git -c core.autocrlf=false diff --no-index -- 'C:\Users\jorcy\AppData\Local\Temp\agentjys-phase0-5a6dc26b8a\baseline\tests' 'tests'
```

备份位于临时目录，不应当作长期版本历史。建议人工 review 本轮语义与差异，再在有 Git 元数据的正式仓库中形成 Phase 0 独立提交。**本轮在此停止，不自动启动 Phase 1。**

