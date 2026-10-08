# Recall 冷热地址读取契约

日期：2026-10-08。适用于 `codex/recall-address-first` 分支，改动建立在基线 `2ee4627281c9141e7a436857092320d65d337767` 之上；该基线号不包含本次实现，提交与发布状态以对应 PR 和部署记录为准。

本次修复正文读取顺序：先消费本条记忆登记的热地址，热地址为空或不可用时再按权威地址读取。向量检索、相似度阈值、ContextPack 数量和网页来源展示不在本次变更范围。

## 调用关系与职责

```text
ContextAssembly：候选加载 / 冲突成员补读
  → RememberBoundary.load_recall_batch
  → RememberPipeline.load_recall_batch → BodyReads.load

ContextAssembly：正式正文读取
  → RememberBoundary.load_bodies
  → RememberPipeline.read_body → BodyReads.read

BodyReads：授权、主记录验证 → 读取 → 返回前复核
  cache_location 非空且通过校验 → RedisCache.read_location
  地址为空 / 缓存不可用         → Bodies.read_authority(body_location)
```

候选和冲突补读原先调用的同步 `MemoryReadPort.load` 会在进程缺少已验证正文时提前回源。因此不能只调整最后的 `read_body`，也不能用仍忽略 cache_location 的旧 `load_async` 代替。新批量入口直接用本次校验过的元数据和正文构造 `MemoryReadBatch`。旧同步读取及后台 hydration 留给既有调用方，未全局改写。

## 对外与内部接口

HTTP 请求/响应模型及 `FullBodyReadResult` 字段不变。内部新增 `RecallBodyReadPort(MemoryFoundationPort)`：

```python
async def load_recall_batch(
    self, ctx: TrustedContext, refs: tuple[MemoryRef, ...]
) -> MemoryReadBatch: ...
```

`MemoryFoundationPort.load_bodies` 保留原签名。自定义 generation Recall 正文提供方需要实现新增批量方法；Host 装配会尽早检查能力，不静默退回旧同步加载。重复引用被拒绝，成功项与资格结果对应；依赖不可用不能假装正常空结果，失效正文不能入包。

公共存储另新增只读能力：

```python
async def read_location(
    self, scope: Scope, location: ResourceLocation, authority: ResourceLocation
) -> str | None: ...
```

Azure 从同一个已配置的 `RedisCache` 注入 `cache_reader`。它不读取 Operate 动作表，也不调用缓存执行器。原 `body_cache` 及 `TieredBodyCache` 仍提供显式首次准入、登记和清理能力。已直接传入同时实现地址读取协议的缓存提供方可由 `Bodies` 识别；仅提供旧 get/put 的自定义缓存需要另注入地址读取器，否则正文安全回源。

## 地址、安全与副作用

- 空地址：零次 Redis 读取。不能仅凭同作用域和同哈希绕过本条记忆的地址登记。
- 有效地址：实例、namespace、完整 scope、正文代次、哈希、`redis_hash_v1` 的 key/field/expiry_field 都必须匹配服务端预期；JSON 字段顺序可不同，重复/额外/未知字段拒绝。
- 旧地址或损坏的可选缓存字段：安全回源，不自动迁移，不放宽主记录验证。
- 权限、精确记忆版本、当前状态及来源：读前核验；读后再次核验对象/关系/正文绑定。仅缓存位置改变且正文事实未变时，可使用已取到并验证的内容。
- 读取期间网络 I/O 在元数据事务外进行。请求有总期限，热层最多使用剩余时间的一半并受配置 socket 超时约束；这不代表底层线程被强制终止。
- 普通读取不调用 `admit_verified_cache`，不准入、续期、填缓存或写 cache_location。原 Lua 清理过期字段保留。
- 热命中返回真实热地址；回源返回权威地址。历史内联记录直接使用校验后的内联正文，不伪造 Redis 地址。
- `recall.access` 仍由 Recall 按原规则产生。重复尝试及多阶段读取沿用同一访问去重键；读取失败不伪造成功事件。

缓存不可用允许回源，权威正文失败则保留错误语义：直接本地正文读取缺文件仍为 `DEPENDENCY_UNAVAILABLE`，远端权威正文不存在仍为 `NOT_FOUND`；批量入口将此类依赖失败记为逐项不可验证，不能伪装正常空结果。授权失败、主记录非法和总期限耗尽不能作为普通缓存未命中继续放行。

批量最终复核明确区分两种情况：当前资格不允许（如已删除）仍为 `excluded`；当前资格允许、但读取期间关系或正文事实变化导致快照无法核验，必须为 `unverifiable`。后一种不能被误当作“没有相关记忆”：唯一候选失效时返回不可用；有其他有效候选时保留它们并附 `qualification_unverifiable` 降级说明，失效项不记录成功 read。

## 其他模块应了解的影响

| 模块 | 具体影响 |
| --- | --- |
| Remember | `read_body` 的实现委托给新 BodyReads；新增 load_recall_batch。读取不再兼做缓存修复。保存/纠正等显式写入入口保留。 |
| Remember 正文 HTTP / 范围读取 / Runtime 管理诊断 | 共享 read_body，因此获得真实 path/location 和无缓存登记副作用的行为；路由代码与外部字段不变。 |
| Operate | `operate/` 生产源码未改；TieredBodyCache、record_sync、升降温、事件消费继续使用。缓存漂移由维护方处理，不能再期待 Recall 回源时顺手修复登记。 |
| Runtime | 增加独立 cache_reader 注入和 Azure 不允许混用外部存储提供方的检查；Redis 复用现有连接和 TTL 脚本。 |
| 自定义 Recall 提供方 | 须补齐 load_recall_batch；现有 generation 组包测试替身已适配。 |
| 来源原文 / 前端 | SourceAccess 的原文读取、来源数和最多显示多少篇原文未改变；不能把整次网页对话“零 Ceph 请求”作为本次验收条件。 |

## 回归入口与证据边界

离线回归文件：`tests/unit/test_cache_address_reads.py`、`test_cache_locations.py`、`test_body_address_reads.py`、`test_recall_address_assembly.py`、`test_body_read_consumers.py`。实际执行本项目 Redis 适配、Remember、Identity、Recall 组包、事件和正文 HTTP 路由逻辑；元数据、Redis/Ceph 网络传输、向量候选和 HTTP 可信上下文使用隔离替身。它们验证路径与约束，不证明真实 Redis 服务端 Lua、PostgreSQL 隔离、进程重启或 Azure 网络表现。

```bash
PYTHONPATH=src:tests python -B -m pytest tests/unit/test_cache_locations.py tests/unit/test_cache_address_reads.py tests/unit/test_body_address_reads.py tests/unit/test_recall_address_assembly.py tests/unit/test_body_read_consumers.py --confcutdir=tests/unit -q -p no:cacheprovider
```

在 `AgentJYS-main` 工作目录运行。Windows PowerShell 完整命令见[CI 检查与复验](11_CI检查与复验.md)；临时数据使用项目外专用目录。上述五组测试已加入 GitHub CI。

真实服务回归仍需独立测试环境：`tests/runtime/flows/test_generation_assembly.py`、`test_remember_observation_batches.py`、`test_remember_initial_cache.py`、`test_operate_memory_record.py`，并完成真实 Recall/Temporal 联调。本次旧的“读取时修复缓存”测试改为“回源不修复 + 读取中撤权拦截”；Operate publication 测试夹具仅增加独立 reader 注入。云端故障注入应只针对测试请求/数据，不能关闭共享服务。

### 2026-10-08 首次实现阶段验证记录

| 检查 | 当次结果 | 证明范围 |
|---|---|---|
| 五文件冷热地址离线回归 | 118 passed | 地址为空/命中/失败回源、授权与版本复核、候选/冲突读取、访问去重和共享 HTTP 消费者 |
| P3 契约 | 384 passed | 字段、接口签名、示例和追溯一致性 |
| 平台测试 | 296 passed，107 skipped | 执行项通过；依赖外部环境的跳过项未验收 |
| 静态与 Schema | Ruff 通过；Mypy 301 个源文件通过；132 schemas checked | 静态类型、代码规则与模型生成一致性 |
| Azure 真实冷热链路 | 未运行 | 需另行真实服务联调、故障及并发取证 |

这些结果来自实现完成时的本地验证，测试范围可能交叉，不能直接相加当作独立业务场景数。文档同步不会把旧结果更新成新测试结果；远端 CI、PR 合并和云端发布也各有独立状态。

### 2026-10-08 提交前复验补充

独立评审发现并修复了“读取中关系变化被当作正常排除”的边界缺陷。新增四个组包实例覆盖唯一候选失效、首条读后失配、批次最终失配及真正删除的正常排除；修复前为 3 failed / 1 passed，修复后五文件专项为 **122 passed**。平台仍为 **296 passed / 107 skipped**；契约 **384 passed**、全源码 Mypy/Ruff 和 wheel 构建再次通过。

首轮隔离 AKS 测试收集 76 项，执行 **69 passed / 3 failed / 0 skipped**，达到三次失败后停止，另 4 项未执行；被测源码摘要未变，测试 Pod 和专用 PostgreSQL/Milvus 数据库均确认删除。组包 24 项、Operate 地址发布 30 项、首次缓存 12 项均通过。传输和元数据使用真实 Azure 服务，但部分测试的 Ceph 或模型依赖有明确替身，不能笼统称为全链路真实模型验收。

三个失败均在 `test_remember_observation_batches.py` 的旧预压缩要求：长文本应先等待压缩，以及两种超过 8000 字节文本应登记 source_reference。当前 Remember 已取消新请求预压缩并保存完整正文；与基线对照，相关 `prepare_save/persist_save/commit_save` 和这两个测试函数没有因本次改动变化。本 PR 保留失败用例及证据，不修改 Remember 保存策略来迎合旧断言；完整套件尚不能宣称全绿。后续缓存回源、撤权及版本竞争的定点复验以对应 PR 执行记录为准，不能把首轮未执行项计为通过。

GitHub 的 `azure-tests` Environment 当前未配置，不能把本机调度 AKS 的结果当作 Azure Actions 的通过状态。手动测试复用仓库 `run_aks_tests.py` 的源码打包、证据判定和 UID 清理，以及 `aks_test_entrypoint.py`；凭据留在 AKS，未修改在线 Deployment。

## 相关文档

- [当前流程与异常时序](../architecture/03_三流程与异常时序.md)：从检索引用到正文交付的图示。
- [数据归属与一致性](../architecture/04_数据归属与一致性.md)：PostgreSQL、Redis、Ceph、Milvus 分工。
- [Recall 与 Embedding 接入](10_Recall与Embedding_A侧接入.md)：现行装配与内部端口。
- [Operate 地址同步说明](../../../src/aether_agent_memory/operate/MEMORY_RECORD_SYNC.md)：地址维护、普通读取与副本管理的边界。
- [验收场景与证据要求](../acceptance/01_验收场景与证据要求.md)：已验证范围及真实服务待验收项。

## 框架依据

项目约束：Python >=3.13、redis >=5.2,<6、Pydantic >=2.13.4；本次未新增第三方运行依赖。

1. [Azure Cache-Aside](https://learn.microsoft.com/en-us/azure/architecture/patterns/cache-aside)：命中使用缓存，缺失访问权威存储。官方常见示例会回填；本项目由 Operate 管理热度，因此不在普通读取中回填是项目职责约定。
2. [Redis HGET](https://redis.io/docs/latest/commands/hget/) 与 [Lua 原子执行](https://redis.io/docs/latest/develop/programmability/eval-intro/)：当前数据在 hash 字段中，继续复用原 TTL Lua，不能用裸 GET 地址替代。
3. [redis-py 5.2.1 连接配置](https://redis.readthedocs.io/en/v5.2.1/connections.html)：复用连接/socket 超时和池，不从记录读取任意主机或凭据。
4. [Python asyncio.to_thread](https://docs.python.org/3.13/library/asyncio-task.html#running-in-threads)：阻塞存储读取不占用事件循环；取消等待不等同终止运行中的线程。
5. [Pydantic Models](https://docs.pydantic.dev/latest/concepts/models/)：保留主记录 model_validate，未使用跳过验证的 model_construct。
6. [OWASP 授权实践](https://cheatsheetseries.owasp.org/cheatsheets/Authorization_Cheat_Sheet.html)：地址不能替代鉴权；读取前后及组包继续检查当前权限。
