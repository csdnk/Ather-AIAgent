# Operate 的 MemoryRecord 写回闭环

更新日期：2026-10-08。MemoryRecord 写回逻辑位于 Operate；2026-10-07 的写回闭环继续保留。当前 Recall 已通过 Remember 公共读取接口消费登记地址：先热地址，无法读取时再冷地址。读取不再依赖 Operate 执行器，也不修复地址；本次读取适配没有修改 Operate 生产代码。详见[冷热地址读取契约](../../../docs/p3/development/17_Recall冷热地址读取契约.md)。

## 1. 现在的顺序

```text
收到访问或记忆变更通知，按既有规则聚合、调度
    ↓
同一记忆有未完成计划：继续查询原计划；新访问仍保留
    ↓
观察实际状态，计算目标；需要变更时提交动作
    ↓
查询原动作结果，重新确认实际层级、正文可读、内容一致
    ↓
在同一个 PostgreSQL 事务中：
    核对租户、作用域、记忆版本、正文地址、执行器实例及副本操作序号
    → 修改 MemoryRecord.cache_location
    → 保存 Operate 写回登记
    → 将动作标记为成功，发布动作结果事件
    ↓
沿用现有任务完成逻辑；若期间有新输入，再按最新热度评估
```

即使本轮不需要升降层，观察到可读的实际状态后，也会核对并修复地址字段。

## 2. 修改哪些字段

| 情况 | body_location | cache_location |
| --- | --- | --- |
| Ceph 冷层 → 建立 Redis 热副本 | 保持不变 | 写入经核验的 Redis 副本地址 |
| 热副本 → 冷层 | 保持不变 | 确认 Ceph 正文可读且副本已撤销后清空 |
| Redis 副本自然过期、已不存在 | 保持不变 | 后续 Operate 评估确认实际冷层后清空 |
| 查询超时、状态不明、读取核验失败 | 保持不变 | 保留原值，等待核验 |
| 归档/删除等清理 | 保持不变 | 原副本清理确认后清空对应失效或非当前版本的地址 |

只修改 `cache_location`，不修改正文、重要性、来源、语义版本、访问统计或 `object_revision`。
数据库内部的记录修订号通过 `put_if_revision` 更新，用于检测并发写入；它与业务版本号不同。

地址使用已有的 `ResourceLocation`：`provider_id=redis`，实例绑定实际 Redis 资源，保留 namespace、generation、content_hash。
`object_key` 是一个带 `encoding=redis_hash_v1` 的 JSON 字符串，记录 Redis hash 的 key、正文字段 field 和过期字段 expiry_field；不包含连接密码。
正文和副本必须对应同一个 generation 与 content_hash。

## 3. 常见问题如何处理

### Redis 成功，MemoryRecord 写回失败

例如 Redis 已建立热副本，但数据库写回失败：

1. PostgreSQL 事务回滚，不会出现“动作成功但该事务没写入 MemoryRecord”。
2. 动作保留或转为 `unknown`，写回失败时记录 `memory_record_sync_pending`。
3. 恢复时查询原 action_id，重新读取并核验实际状态，再尝试写回。
4. 在这次写回确认前，同一记忆不能提交另一个新计划。

如果数据库连 `unknown` 都无法保存，之前已持久化的 submitted/unknown 动作仍保留，恢复后继续查原动作。
如果事务已经成功、只是返回响应丢失，重试会读取已完成的动作，不会重复修改记录。

### 旧的升热回执晚于新降温到达

已结束的动作直接返回持久化结果，不再执行写回。因此旧升热回执不能重新填入已清空的缓存地址。

### 执行期间，记忆被更新成新版本

写回时再次检查当前版本、完整作用域、正文内容和地址。旧计划不能修改新版本。
若原动作已有确定的结束回执，但旧记忆已失效，则取消该计划的业务写回，保留待清理状态；不把旧版本标记为成功。
外部动作仍不确定时，继续查询原动作。

### 另一个线程修改了重要性等信息

Operate 在写回事务内读取最新完整记录，只替换 `cache_location`，再做版本条件写入。
不会用动作创建时的旧 MemoryRecord 覆盖后来的业务字段。
同一记忆的副本操作序号在核验期间发生变化，则放弃本次写回并重新核验；其他记忆引起的全局 epoch 变化不会单独阻止写回。

### Remember 后续保存与 cache_location 的关系

当前 `RememberPipeline.put` 对同一版本、相同 body_location 且仍 active 的记录保留已有缓存地址；新内容版本或撤回的记录不继承旧正文的准入标记。这是已有保存行为，冷热地址读取适配没有重写它。新写入和后续同步均复用 RedisCache 的规范地址构造。

Redis TTL、回收或故障仍可能使登记地址与副本实际状态暂时不同。已有 memory.changed、成功读取事件或到期评估触发 Operate 时，由维护方重新核验并处理地址。Recall 普通回源不创建副本、不续期，也不更新或清空 cache_location；读取超时不能作为副本已不存在的证明。静默漂移没有新增即时扫描兜底，不能承诺地址与存储始终同步。

## 4. 新增登记与兼容边界

- `operate_cache_sync_heads`：每个作用域下的记忆一个当前动作登记，用于阻止未确认写回期间的新计划，以及迟到动作覆盖。
- `operate_cache_bindings`：每个作用域下的记忆一份最近核验结果，包括记忆版本、内容摘要、缓存地址、实例、原动作和核验时间。覆盖保存，不按每次访问追加。对应非活动版本清理时移除。
- 两类数据都使用现有 PostgreSQL 元数据事务，不新增数据库，不修改 MemoryRecord 公共结构。
- 适配目前具备 Ceph 正文核验能力的真实 Redis 执行器。适配器对副本登记表的依赖集中在 `basic/record_sync.py`，执行器内部格式变化时需要同步检查这里。
- 历史 inline MemorySnapshot 没有地址字段，保持兼容，不凭空补造地址。对具有 body_location、但缺少受支持地址适配器的记录，不伪造写回成功。
- **Redis 和 PostgreSQL 不是同一个事务**：两者之间仍存在短暂状态差，通过持久化动作、重新核验和重试收敛。
- Recall 候选加载、冲突补读使用 `load_recall_batch`，最终正文使用 `load_bodies`，两者统一到 BodyReads。地址为空时不查 Redis；热地址失败回源，结果返回真实读取位置。
- 公共读取器检查完整作用域、已配置实例、namespace、generation/hash、实际内容及当前权限。未知历史地址格式安全回源，不由 Recall 迁移。
- `recall.access` 的 `stage=read`、`outcome=succeeded` 及原去重键不变。普通读取和缓存管理分离；TieredBodyCache 的显式准入、登记及清理能力保留。

## 5. 测试依据

专项测试位于 `tests/runtime/flows/test_operate_memory_record.py`。测试使用真实 PostgreSQL、真实 TLS Redis，Ceph 使用对象读取替身。

覆盖升热/降温、保持正文和其他字段不变、事务回滚、原计划恢复、响应丢失、重复/迟到回执、新版本竞争、并发业务字段更新、执行器序号变化、跨租户拒绝、超时不误清空、Redis 过期、重新补写、清理中断恢复和异步调度响应。

兼容回归包含既有两层调度、触发机制、Redis 执行器、平台调度能力、放置监测和 Temporal 历史重放测试。

2026-10-07 本地执行结果：上述专项和兼容回归合计 **103 项通过**；本次涉及的 4 个 Python 文件通过 Ruff 检查与格式检查，3 个 Operate 源码文件通过限定范围的 mypy 检查。
这些测试不等同于真实 Ceph、线上 Azure 或完整远端 Temporal 集群联调。

2026-10-08 读取适配的本地专项为 118 项通过，其中既有地址发布 16 项、新地址读取 52 项、公共正文 39 项、Recall 组包 9 项、正文 HTTP 2 项。Redis/Ceph 传输使用隔离替身，不是上述真实 PG/Redis 套件的重新验收。实际运行命令和外部联调边界见[CI 检查与复验](../../../docs/p3/development/11_CI检查与复验.md)。
