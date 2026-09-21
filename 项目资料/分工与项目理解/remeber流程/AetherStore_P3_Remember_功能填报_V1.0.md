# AetherStore P3 · Remember 负责人功能填报 V1.0

## Person B — Remember / Memory Formation

**Primary Outcome**：Memory 可靠形成、可追踪、可版本化、可被 Recall 使用；Memory 没形成找 B。

按 B-EPIC-01～B-EPIC-08 组织为 **8 项功能，每个 Epic 对应一个 Feature**。每项统一填写基本信息、User Story、功能描述、正常流程、异常/降级、依赖、验收标准、计划、人天和备注。

## B 的最终 Epic 结构

| Epic ID | 最终 Epic | 功能名称 | 估算人天 | Epic 内独立计划 |
| --- | --- | --- | --- | --- |
| B-EPIC-01 | Ingest & Fact Formation | 记忆事件接入与可靠主事实形成 | 20 | D001～D020 |
| B-EPIC-02 | Lifecycle & Policy Versioning | 记忆分类、生命周期与策略版本管理 | 22 | D001～D022 |
| B-EPIC-03 | Durable Content & Capacity Guard | 可靠正文存储、内容映射与容量保护 | 18 | D001～D018 |
| B-EPIC-04 | Memoryize / Compression & Background Capacity | 异步记忆加工、压缩与后台容量治理 | 28 | D001～D028 |
| B-EPIC-05 | Make Recallable & Projection Health | 长期记忆可召回构建与投影健康管理 | 24 | D001～D024 |
| B-EPIC-06 | MemoryRead Capability | 当前 Working 读写与权威记忆读取能力 | 16 | D001～D016 |
| B-EPIC-07 | MemorySignal Capability | 记忆变化信号、可靠投递与消费确认 | 12 | D001～D012 |
| B-EPIC-08 | Recovery / Reconciliation & Retry Governance | 记忆恢复对账、全局重试与故障治理 | 28 | D001～D028 |

## 计划与人天口径

- **每个 Epic 独立从 D001 起算。** 设计、开发、测试、联调依次累计本 Epic 的有效投入日；D 序号表示工作量，不是自然日期，也不是多个 Epic 的串行项目日历。
- **估算合计 168 人天＝设计 29＋开发 69＋测试 43＋联调 27。** 按一人一天有效工作计量，各阶段含该阶段内评审和常规修正；本项联调只计 B 侧投入。两人同时参加一天计两人天。
- **规划团队 3 人，项目周期目标 90 个工作日。** B1/B2/B3 是本次容量规划的执行岗位，Owner B 对整体交付负责；岗位名称不代表已任命具体人员或新增外部承诺。同一岗位的不同 Epic 穿插推进，但同一有效投入时段不重复计数。
- 估算依据为 8 个 Epic 的 WBS、Remember 详细设计和 PRR 增补，按下文具体交付物自下而上核算。假设已有可接入的模型服务、RF/状态与执行基础设施、Redis 和 P2 Provider；包含 B 的契约、适配、Simulator、领域逻辑与验证，不包含从零建设外部平台/模型或 P2 引擎的投入。
- 3 人 × 90 工作日为 270 人天最大日历容量，168 人天功能投入约占 62.2%。余下 102 人天为人员可用性、日常事务、跨团队等待和风险调整的容量空间，**不另计为功能费用，也不代表 102 人天全部需要工作**。每岗最大投入 62 人天，整体周期还受依赖先后及真实环境就绪约束，不能用 168 ÷ 3 直接承诺完工日期。
- 本表为 2026-09-08 的文档估算，未从现有业务代码推断完成度，也未扣减既有实现。未给定统一开工日，故采用相对工作日；人天和交付窗口明确列出，不把历史 PRR 日期转写成当前承诺。

### 三人分工与并行关系

| 执行岗位 | 主要职责 | 负责 Epic | 设计 | 开发 | 测试 | 联调 | 功能投入（人天） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B1 | 主事实、生命周期/证据/策略、Signal 交接 | 01、02、07 | 9 | 24 | 13 | 8 | 54 |
| B2 | 内容、Working 读写、异步加工与后台容量 | 03、04、06 | 11 | 25 | 16 | 10 | 62 |
| B3 | 投影领域、统一恢复/重试、故障与集成验收 | 05、08 | 9 | 20 | 14 | 9 | 52 |
| 合计 | 3 人 | 8 个 Epic | 29 | 69 | 43 | 27 | 168 |

主事实、内容 Port/Simulator 和投影契约可以并行设计；B1 使用内容契约样例开发，B3 使用 A 的契约及模拟结果推进。B2 优先交付内容与 Task/租约骨架，再补齐压缩和 WorkingRead。真实 Ready 联调依赖 A 的向量能力及 E1/E2 证据；A 读取与 C 消费联调依赖 B 的资格、映射和 Signal 契约。恢复接口、指标和故障用例从前期设计接入，各业务能力具备后汇合验收。

### 90 个工作日的项目交付窗口

以下 **P 序号为项目全局工作日**，与每个 Epic 的 D 投入序号区分。M1/M2/M3 在本文用于基础能力、功能闭环、真实集成与生产验收三个交付层次；具体公司日历由项目计划映射。

| 项目窗口 | 交付重点与阶段出口 | B1 人天 | B2 人天 | B3 人天 | 合计 |
| --- | --- | --- | --- | --- | --- |
| P001～P015 | 完成 29 人天设计及 1 人天基础开发；形成接口/策略/资源 Profile 和验收样例，关键契约目标 P015 就绪 | 10 | 11 | 9 | 30 |
| P016～P040 | 投入 59 人天开发；M1 主事实、分类/内容基础链路可验证，Task/Outbox 与投影骨架可用 | 20 | 21 | 18 | 59 |
| P041～P065 | 投入开发 9、测试 38 人天；M2 Working、压缩、Ready、Signal 及恢复功能闭环，L1/L2 验证完成 | 14 | 18 | 15 | 47 |
| P066～P080 | 投入测试 5、联调 17 人天；真实 A/C/P2/Redis 联调、压测、半故障/删除/迁移演练 | 7 | 8 | 7 | 22 |
| P081～P090 | 投入联调 10 人天；持续验收、缺陷复核、证据归档与运行交接，M3 出口 | 3 | 4 | 3 | 10 |
| 合计 | 各窗口复用下文 168 人天，不额外叠加 | 54 | 62 | 52 | 168 |

真实 E1/E2、独立 Working/Prewarm Redis 和可联调的 A/C 能力目标在 **P065 前就绪**；P015 前确认契约、质量/性能/容量/重试参数及验收环境责任，P065 前复核真实环境。以上是排期所需交付条件，不是 Provider 已签收日期。若关键依赖未到位，可完成对应 L1/L2 工作，但受影响的 L3 及 M3 不能以模拟结果替代，应按实际等待和新增范围调整窗口。

持续验收按 PRR P0-6 分别准备“合同基线 72h/Chaos”和“E1 集成 72h”的证据，暂在 P081～P090 内各安排一轮自动运行，B 的部署观察、问题定位及报告工时包含在 08 的测试/联调中。72h 是运行时长，不按 3 人全天值守折算；是否与故障注入重叠由 IS/验收负责人在 P015 前冻结。重新跑完整战役或新增 24 小时轮班值守不在当前基准估算中。

---

## B-EPIC-01 · Ingest & Fact Formation

**Epic 范围**：MemoryEvent；Fact-First；幂等；Scope / Provenance；MemoryRecord；Success 与 Accepted 的区别。

**关联 PRR Action**：—。

**Epic 级完成标准**：必要正文和主事实可靠提交、合法 Type 已发布后，按本次请求承诺判定结果；未完成的承诺输出只能如实返回 Accepted / Partial，重复事件不产生重复主事实。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | B-EPIC-01 |
| Epic Name | Ingest & Fact Formation |
| Feature ID | B-FEAT-01-01 |
| Feature Name | 记忆事件接入与可靠主事实形成 |
| Owner | B（Remember / Memory Formation） |
| Contributor | B1；B2（内容与 Working）；RF / P4 / Auth |
| 目标里程碑 | M1（基础形成能力） |

### 2. User Story

作为提交对话、任务结果或显式记忆的上游系统，我希望每次写入都能获得可追踪的事实身份和准确的完成状态，从而在超时重试、服务重启或后台尚未完成时，仍能确认内容是否已可靠保存并继续查询。

### 3. 功能描述

#### 3.1 功能目标

建立从可信 MemoryEvent 到可靠 MemoryRecord 的接入、幂等、提交和响应闭环，区分主事实已形成、当前 Working 可用与长期派生完成。

#### 3.2 核心输入

- MemoryEvent 的内容或受管理引用、source / source_event、业务发生时间、类型与重要度 Hint。
- 可信 RequestContext：身份、Scope、request_id、trace_id、deadline、幂等键；更正请求携带目标与 expected_memory_version。
- required_outputs、输入边界及存储 Profile；B-EPIC-03 提供的精确内容绑定，B-EPIC-02 提供的轻量分类规则。

#### 3.3 核心处理

1. 校验身份、Memory.Write 权限、Scope、输入大小/编码/时间和引用来源；业务任务 ID 与后台 Task ID 分别保存。
2. 在租户及调用身份域中原子登记幂等键、规范化版本和输入指纹。相同语义复用原 operation；同键异义返回冲突。指纹不含每次变化的 trace / deadline，重试重新授权。
3. 小正文按 W1 随主事实保存可靠快照；大正文先通过内容 Port 获得精确版本、摘要与持久化证明。必要正文结果未知时只保留可查询操作。
4. 在可靠事务或已证明的等价边界内保存 MemoryRecord / MemoryVersion、ContentBinding、来源、幂等结果、检查点及可重放的分类/Signal 待办。
5. 完成轻量分类后才对外发布；内部已提交但未分类的 Active 记录由检查点恢复，不能作为成功 Memory 被读取。
6. 核对 required_outputs：全部已验证才 Success；主事实和合法 Type 已确认、未完成目标均可靠接管才 Accepted；部分承诺失败或未交接则 Partial；事实未确认返回失败/未知语义及操作查询依据。

#### 3.4 核心输出

- 稳定 operation_id、memory_id / memory_version、state_version、提交证据及 Scope / Provenance。
- RememberResponse：fact_confirmed、required_outputs / confirmed_outputs、独立 Task / Projection / Signal 状态、pending_items 和原因。
- 可恢复的分类检查点、后续任务需求和事实变化记录。

### 4. 正常流程

接收事件 → 校验身份和输入 → 登记幂等身份 → 确认必要正文 → 原子提交主事实及恢复待办 → 发布合法分类 → 核对本次承诺 → 返回 Success / Accepted / Partial 或失败/未知结果。主事实提交之后的派生错误独立记录。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 未认证、越权或非法输入 | 提交前拒绝，保留稳定原因及脱敏 Trace | 不生成可用 Memory |
| 同键重放或同键异义 | 重放复用原操作；异义冲突；每次重验权限 | 不重复建事实、不覆盖原输入 |
| 正文写或本地事务结果未知 | 查询原操作与提交证据，由恢复流程收敛 | 不因超时认定未提交，也不假报已保存 |
| 提交后分类前崩溃 | 从持久检查点恢复分类，uncertain 归 Working | 无合法 Type 的记录不对外发布 |
| 主事实已存但同步 Working 失败 | 保留事实和缺口，按承诺返回 Partial；可靠接管符合条件时才 Accepted | 不回滚真实已存内容 |
| 后台或 Signal 尚未完成 | 逐项返回真实进度；非同步目标不阻塞已满足的输出 | 不将队列接收当全链成功 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 接入、身份与请求封套 | 可信 Scope、授权撤销、deadline 与正式响应映射 | P4 / Auth / RF |
| 可靠状态机制 | 事务、唯一幂等约束、CAS、提交查询与检查点 | RF / Infra；B 定义提交边界 |
| 分类和生命周期 | 轻量 Type 发布、版本与状态规则 | B，B-EPIC-02 |
| 可靠正文与当前 Working | 内容绑定、主事实落点及 Working 确认 | B，B-EPIC-03 / 06 |
| 后台与事件交接 | 持久 Task、Signal 载荷和可恢复待办 | B，B-EPIC-04 / 07 |

接口依赖：AL-B01、AL-B02、AL-P201、AL-P202、AL-RF01、AL-RF02。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按项目 P015 / P065 契约及真实环境窗口推进。

### 7. 验收标准

1. 未授权和跨 Scope 请求全部拒绝；同键同语义复用身份，同键异义稳定冲突；重试撤权不可沿用旧许可。
2. 提交前、正文已写、事务未知、事务后响应丢失等断点均能按原 operation 查询收敛，不重复 Memory，不提前删除候选孤儿。
3. 主事实、正文绑定、来源和恢复入口可逐项追踪；仅分配 ID、Broker 受理或 HTTP 成功均不足以确认事实。
4. Active 分类未发布时不对外 Success / Accepted / Partial；恢复后仍按合法 Type 和当前授权判断。
5. Success 覆盖全部 required_outputs；Accepted 的每个缺口都有持久接管；Partial 如实保留已确认结果，不隐式缩小原承诺。
6. Working-only 请求不等待 Embedding；长期请求在未 Ready 时不报告长期可召回。通过 RM-T01～RM-T05、RM-T18 及 BP2T-02 / 03 的对应场景。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。主执行岗位：B1；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D003 | 接入/幂等契约、Fact-First 提交序列、required_outputs 与响应判定表 |
| 开发 | D004 | D012 | 输入校验、操作登记、主事实事务、分类交接及查询响应实现 |
| 测试 | D013 | D017 | 权限/幂等、提交断点、未分类重启和四类响应验证记录 |
| 联调 | D018 | D020 | P4/RF 请求映射、内容/Working/后台交接与真实超时查询证据 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 3 |
| 开发 | 9 |
| 测试 | 5 |
| 联调 | 3 |
| **总人天** | **20** |

估算依据：设计 3 天覆盖接入、幂等和提交边界；开发 9 天覆盖校验 2、操作幂等 2、主事实提交 3、响应/查询 2；测试 5 天覆盖并发与提交断点；联调 3 天覆盖接入和派生交接。

### 10. 备注

- WBS 对应：B-EPIC-01-T1、B-EPIC-01-T2、B-EPIC-01-T3、B-EPIC-01-T4。
- PRR Action：—。
- 设计依据：主设计 §2.1～2.3、§3.1；数据定义 §3、§7.8；P2 对接 BP2-01～03。
- 工作量及责任边界：本项负责入口和业务事实事务。正文 Port/Simulator 计入 03，分类规则计入 02，Working 读写计入 06，Task 执行计入 04，Signal 交付计入 07；不重复计算这些实现。

---

## B-EPIC-02 · Lifecycle & Policy Versioning

**Epic 范围**：Working / Episodic / Semantic 逻辑语义；Active / Archived / Superseded / Expired / Deleted；分类、版本、冲突和衰减相关策略版本。

**关联 PRR Action**：B-P0-5（衰减 / 策略部分）。

**Epic 级完成标准**：生命周期状态机可测，uncertain → Working，冲突不静默；分类/衰减策略及证据可追踪，发布可以回退。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | B-EPIC-02 |
| Epic Name | Lifecycle & Policy Versioning |
| Feature ID | B-FEAT-02-01 |
| Feature Name | 记忆分类、生命周期与策略版本管理 |
| Owner | B（Remember / Memory Formation） |
| Contributor | B1；A / C / RF；产品与测试 |
| 目标里程碑 | M1（分类与生命周期），M2（策略发布） |

### 2. User Story

作为需要持续记住当前任务、历史事件和稳定知识的业务系统，我希望记忆能按证据、时间和明确策略分类、巩固、更正或失效，从而让新旧事实、冲突和过期内容具有可解释的处理结果。

### 3. 功能描述

#### 3.1 功能目标

统一类型、生命周期、证据依赖和版本规则，可靠形成 Semantic，向 A 提供版本化衰减与冲突语义，向后续表示和 Signal 提供资格变化。

#### 3.2 核心输入

- 当前 Memory / memory_version / state_version、Scope、内容来源、importance / confidence / stability。
- SessionEnd、TaskEnd、显式保留/更正/删除、业务 valid_to、用途变化及新增证据。
- 版本化分类/巩固/衰减策略、SemanticCandidate、EvidenceLink、ConflictGroup 和获准使用观察。

#### 3.3 核心处理

1. 按来源、业务阶段、时效与规则判定 Working / Episodic / Semantic；Hint 不替代授权或证据，uncertain 保留原因并归 Working。
2. SessionEnd 等触发一次有身份的 LifecycleEvaluation，决定继续当前用途、形成 Episodic 或按政策失效，不自动归档全部 Working。
3. 独立证据、时间、权限、一致性和冲突规则通过后发布 Semantic；保留来源与反向依赖，同源重复及一次摘要不算多份证据。
4. 更正按已确认身份规则创建新版本，原子或经一致性屏障更新当前指针、新版资格、旧版 Superseded 和失效待办；无法裁决的矛盾保留完整冲突组。
5. 业务到期在读取时立即排除；来源删除/到期/撤权沿反向依赖重评派生结论。Redis TTL、任务到期、模型切换均不等同于 Memory Expired。
6. 保存衰减策略版本与可信输入，按 Shadow → Canary → 发布/回退验证规则。A 执行排序；无获准使用证据保持 unknown，不强化，也不把时间衰减变成物理删除。

#### 3.4 核心输出

- 合法类型、生命周期、MemoryVersionState、替代链和分类/生命周期评估记录。
- 可核验的 SemanticCandidate / EvidenceLink / ConflictGroup、反向依赖及失效待办。
- 版本化语义策略、黄金样例、灰度/回退报告及供 MemoryRead / Signal 使用的事实。

### 4. 正常流程

收到新事实或生命周期事件 → 读取当前版本与证据 → 判定类型/用途/有效时间 → 校验状态转换及冲突 → 条件提交版本和资格 → 登记表示失效与 Signal 待办 → 向读取能力发布新事实。策略更新先比较样例，再灰度和回退演练。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 分类不确定或分类器失败 | 记录 uncertain，按 Working 处理 | 不伪造长期类型 |
| 证据不足或存在矛盾 | 保留 Candidate / ConflictGroup 及双方来源 | 不静默升 Semantic 或覆盖一方 |
| 旧版本并发更正 | CAS 拒绝失权提交，重新读取当前事实 | 当前指针与旧版资格一致 |
| 来源删除、到期或撤权 | 先限制受影响派生结论，再重评合法独立证据 | 摘要和 Semantic 不绕过来源权限 |
| 业务到期但缓存仍存在 | 读路径立即排除并登记失效 | 不等待扫描/TTL 才生效 |
| 策略发布质量回退 | 停止扩大并恢复稳定策略版本，保留历史决策 | 不回滚真实删除或抹除已发生事实 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 主事实/版本 | 可靠事实、版本前置条件及历史来源 | B，B-EPIC-01 |
| 规则与质量数据 | 分类、独立证据、冲突、保留与删除规则及正反例 | B；产品 / 测试 |
| 读取与信号 | 消费类型、资格、衰减和冲突完整性；传播失效 | B-EPIC-06 / 07；A / C |
| 共享使用观察 | 获准消费范围、去重身份、迟到窗口、实际使用证明 | RF / A / 调用方；B 评估 |
| 失效清理 | 反向依赖、精确表示清理与恢复屏障 | B，B-EPIC-08 |

接口依赖：AL-B02、AL-B03、AL-B04、AL-B05、AL-A03、AL-RF04。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按项目 P015 / P065 契约及真实环境窗口推进。

### 7. 验收标准

1. 覆盖五种生命周期的合法/非法转换；类型、memory_version、state_version 与物理 generation 分别校验。
2. uncertain 归 Working；SessionEnd 只触发评估；一次压缩或同源重复不能单独形成 Semantic。
3. 并发更正和旧回调不能覆盖新版；冲突成员及证据缺失有明确结果，A 不需猜测策略。
4. valid_to 到期立即失去当前读取资格；缓存 TTL 到期仅代表副本缺失；模型升级仅触发相关投影处理。
5. 来源撤销后受影响的派生结论可定位并先限制使用；删除不能被迟到访问/旧事件恢复。
6. 重复或跨版本使用事件不重复强化；仅 loaded / selected / emitted 时不宣称实际模型使用；未获准的 B 消费路径不启用。
7. 冻结规则集下 Shadow、Canary 和回退可复现，记录策略版本、评价时间、差异和回退原因；覆盖 RM-T06 / 07 / 20 / 21、RAT-05 / 10 / 11。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。主执行岗位：B1；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D004 | 类型/生命周期矩阵、版本与证据关系、衰减策略及发布验收样例 |
| 开发 | D005 | D014 | 分类与评估、版本/冲突、证据依赖及策略发布/回退实现 |
| 测试 | D015 | D019 | 状态边界、并发更正、证据撤销、迟到使用与策略黄金样例报告 |
| 联调 | D020 | D022 | A 语义策略消费、C 失效交接、RF 获准观察及策略回退演练 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 4 |
| 开发 | 10 |
| 测试 | 5 |
| 联调 | 3 |
| **总人天** | **22** |

估算依据：设计 4 天覆盖状态/时间、证据和策略；开发 10 天覆盖分类/评估 3、版本/冲突 3、证据依赖 2、策略控制 2；测试 5 天覆盖跨状态与证据边界；联调 3 天验证下游语义。

### 10. 备注

- WBS 对应：B-EPIC-02-T1、B-EPIC-02-T2、B-EPIC-02-T3。
- PRR Action：B-P0-5（衰减 / 策略部分）。
- 设计依据：主设计 §2.3、§6；数据定义 §4.3～4.6、§7.4；A 对接 RA-04 / 05 / 08；B 生产就绪补充 B-P0-5。
- 工作量及责任边界：本项定义生命周期决定、证据依赖及衰减政策；实际读取过滤计入 06，物理级联清理和跨系统恢复计入 08。A 负责排序和最终冲突呈现，C 负责热度与驻留。

---

## B-EPIC-03 · Durable Content & Capacity Guard

**Epic 范围**：Memory → content_ref mapping；ContentStorePort；Content Store Simulator；durable / checksum / head；容量 guard 与写超时确认。

**关联 PRR Action**：B-P0-3、B-P1-3（checksum / head 部分）。

**Epic 级完成标准**：容量满时明确拒绝并告警；写超时通过原操作及精确版本 head/get 等证据收敛；checksum 可校验。B 维护业务映射，物理 payload 权威仍属于 Durable Store Provider。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | B-EPIC-03 |
| Epic Name | Durable Content & Capacity Guard |
| Feature ID | B-FEAT-03-01 |
| Feature Name | 可靠正文存储、内容映射与容量保护 |
| Owner | B（Remember / Memory Formation） |
| Contributor | B2；P2 E2 / 内容 Provider；A / RF |
| 目标里程碑 | M1（内容基础能力） |

### 2. User Story

作为 Remember 和读取可信正文的 Recall，我希望每条 Memory 都能定位到获准、完整、持久的精确内容版本，并在容量不足或写入超时后得到明确结果，从而避免空引用、错版本和错误重写。

### 3. 功能描述

#### 3.1 功能目标

交付 OBJ-001 消费契约、内容模拟器、不可变内容绑定与真实 Provider 适配，补齐容量保护、完整性核验和写入未知的查询能力。

#### 3.2 核心输入

- 正文/Artifact 实际字节、编码、范围、摘要、Scope、稳定 content_operation_id 与目标资源。
- Memory / content / object 各自版本、来源绑定、读取用途、授权范围和保留要求。
- Provider 容量/健康及 observed_at、大小限制、持久性、可见性、幂等与负查询契约。

#### 3.3 核心处理

1. 定义 ContentStorePort 的 put / get / head / checksum / delete 与结果查询消费语义；Simulator 提供正常、容量满、部分读取和回执丢失场景。
2. 写前核验实际大小与新鲜容量观察；满容量明确拒绝；容量观察不可靠时按冻结准入策略限制写入，Provider 最终写失败同样如实处理。
3. 先持久化 ContentWriteIntent，再提交精确对象；只有内容版本、字节范围、摘要、durable 和可读性证据齐备才交付正式绑定。
4. Memory 到 Original / Artifact 的映射固定精确来源与版本；读取片段校验对应编码/范围摘要，不以可变 URL、latest 或不明算法 ETag 代替证明。
5. 写超时先查询原操作和精确对象；只有契约能证明未执行且无迟到副作用风险时才允许同操作重试；保留可见性窗口内的 unknown。
6. E2 成功而主事实未确认时登记候选孤儿；待引用和操作对账后按精确版本清理，交付给 08 的统一恢复扫描。

#### 3.4 核心输出

- 版本化 OBJ-001 契约、Content Store Simulator 和契约/故障样例。
- ContentWriteIntent、ContentBinding、精确 content_ref / object_version、durable / checksum / range 证据。
- 容量拒绝与告警、原操作查询结果、候选孤儿及待清理目标。

### 4. 正常流程

接收内容 → 校验权限/大小/容量 → 登记写意图 → 写精确对象 → 核验持久性和完整性 → 保存内容绑定 → 交主事实或 Artifact 使用。若响应未知，查询原操作后补登记或按证明重试；读取始终按已批准的版本和范围执行。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 容量满或 Provider 拒绝写入 | 停止该次写入，告警并返回明确原因 | 不产生成功内容绑定 |
| 写入超时或窗口内 not_found | 保存 unknown，按原操作查询 | 一次 HEAD 404 不触发盲目重写 |
| head/get 版本不同或 checksum 不符 | 拒绝该字节结果，隔离并留证 | 不输出可信正文 |
| 只返回部分范围 | 按 actual range / partial 明示 | 不以部分内容充当完整对象 |
| 正文已写但 B 提交未知 | 保护候选孤儿并查询主事实事务 | 不误删仍可能被引用的正文 |
| 真实 Provider 缺少必要证明 | 列明能力缺口，保留 Simulator 的独立结果 | 该真实路径不能标记验收通过 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 内容物理能力 | E2/Provider 精确版本、持久/完整性、操作查询和容量观察 | P2 E2 / 实际内容 Provider |
| 可靠状态 | 写意图、映射、幂等与引用原子保存 | RF / Infra；B 领域 |
| 主事实与 Artifact | 消费绑定并保存来源/质量结论 | B-EPIC-01 / 04 |
| CanonicalLoad 消费 | 读取获准表示并核验版本、范围、摘要 | A；B-EPIC-06 提供资格 |
| 统一恢复 | 孤儿保护、超时扫描、精确清理和重试预算 | B-EPIC-08 |

接口依赖：AL-B01、AL-B07、AL-P201、AL-P202、AL-P204、AL-RF05。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按项目 P015 / P065 契约及真实环境窗口推进。

### 7. 验收标准

1. Simulator 覆盖持久写、精确读取、容量满、checksum 错误、partial、已写丢响应、明确未执行及暂不可见。
2. Memory / content / object 版本有明确映射；head v1 + get v2、整对象 hash 证明任意片段、裸 ETag 均不能通过内容确认。
3. 写前 guard 与写时满容量均明确拒绝并触发告警，不形成指向未确认正文的成功 Memory。
4. 未知查询能区分已写、明确未写和不可确认；安全重试具备负查询/幂等/迟到副作用依据。
5. 候选孤儿在操作或引用未确认前不删除；共享引用和新版本不被旧清理误伤。
6. L2 契约测试与 L3 真实 E2 的 durable、checksum、超时恢复分别留证，覆盖 RM-T03 / 04、BP2T-02～04 / 09 / 11 / 12。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。主执行岗位：B2；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D003 | OBJ-001 契约与映射、容量 guard、超时/孤儿处理及 Simulator 场景 |
| 开发 | D004 | D010 | Port 消费适配、Simulator、内容绑定、完整性和容量保护实现 |
| 测试 | D011 | D015 | 精确版本/范围校验、容量满、三类超时与孤儿保护测试报告 |
| 联调 | D016 | D018 | 真实 E2 正文写读/校验/查询联调；A CanonicalLoad 内容交接证据 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 3 |
| 开发 | 7 |
| 测试 | 5 |
| 联调 | 3 |
| **总人天** | **18** |

估算依据：设计 3 天；开发 7 天覆盖 Port/Simulator 3、绑定与完整性 2、容量与未知查询 2；测试 5 天覆盖内容证据及副作用边界；联调 3 天验证真实 E2 和 A 消费。

### 10. 备注

- WBS 对应：B-EPIC-03-T1、B-EPIC-03-T2、B-EPIC-03-T3、B-EPIC-03-T4。
- PRR Action：B-P0-3、B-P1-3（checksum / head 部分）。
- 设计依据：主设计 §2.1～2.2、§4.3、§5.3；数据定义 §4.1～4.2、§8；P2 对接 BP2-01～04 / 07 / 08；B 生产就绪补充 B-P0-3、B-P1-3。
- 工作量及责任边界：本项实现内容 Port、Simulator、局部未知查询和校验；08 复用这些能力执行跨对象扫描和恢复。压缩质量归 04，物理存储内核、复制与 WAL 由 Provider 负责。

---

## B-EPIC-04 · Memoryize / Compression & Background Capacity

**Epic 范围**：异步 Memoryize、压缩 Artifact、持久 Task、后台 Worker 并发/队列预算、前台恶化时节流、压缩策略受控发布。

**关联 PRR Action**：B-P0-1、B-P0-5（压缩部分）、B-P1-3（Compression checksum 部分）。

**Epic 级完成标准**：压缩达到合同目标且质量合格；后台不拖垮 Working 前台，越线可降速/暂停；任务可恢复，失败保留 Original；压缩策略可灰度及回退。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | B-EPIC-04 |
| Epic Name | Memoryize / Compression & Background Capacity |
| Feature ID | B-FEAT-04-01 |
| Feature Name | 异步记忆加工、压缩与后台容量治理 |
| Owner | B（Remember / Memory Formation） |
| Contributor | B2；B3（恢复/投影）；A / RF / Infra / P2；测试 |
| 目标里程碑 | M2（异步长期加工），M3（容量验收） |

### 2. User Story

作为需要将长文本和长期记忆可靠加工的业务系统，我希望耗时操作能由可查询、可恢复的后台任务完成，并在保护当前交互的同时生成可信压缩表示，从而兼顾内容质量、存储效率和前台响应。

### 3. 功能描述

#### 3.1 功能目标

提供 Task/Outbox 到 Worker 的可靠执行骨架、可选压缩及质量门槛、Artifact 发布和后台资源预算，供压缩、投影重建及恢复任务复用。

#### 3.2 核心输入

- 已确认 Memory、精确 Original / content_version / hash、required_outputs、Task 输入指纹与策略版本。
- 压缩策略、质量样例、Artifact 持久字节、任务 deadline / lease / fencing；合同是否计入 Metadata / Vector / 索引开销，由 PRR D-3 冻结。
- 前后台独立并发与队列上限、前台 Working P99 / 错误率、Provider 健康、模型/资源限制。

#### 3.3 核心处理

1. 将耗时工作写为持久 AsyncTask 和 TaskDispatchOutbox，再投递执行通道；重复取件附着原任务，租约和 fencing 保证有效写回者。
2. 在线预算不足时，将同一输入和 operation 可靠交给 Task，并撤销原内联执行的发布资格；仅发送 Broker 消息不能返回 Accepted。
3. 冻结压缩来源、模型/策略与输出约束，校验主体、数值、时间、否定和证据等关键语义；不需要压缩时显式 skipped，Semantic 形成仍由 02 决定。
4. 经 03 保存 Artifact 并核验精确版本、实际字节和 checksum；质量、来源资格及持久化都通过后才批准为投影或读取输入。
5. 以真实持久化 Original 字节 / Artifact 字节计算压缩比；零分母、未落盘输出或失败样本单列。保留 Original 时另报 Original + Artifact 总占用。
6. 对压缩/重建/补发等后台流量设置统一资源配额和有界队列；前台越线时节流/暂停，按恢复条件渐进放行。策略先 Shadow 再 Canary，异常恢复稳定版本。

#### 3.4 核心输出

- AsyncTask / TaskDispatchOutbox、检查点、租约和执行结果、内联转后台证据。
- 质量合格的 CompressionArtifact、来源/策略/摘要、实际字节和压缩评测报告。
- 前后台预算配置、节流/恢复记录、压缩策略灰度及回退报告。

### 4. 正常流程

识别长期加工需求 → 固定输入并持久登记 Task/Outbox → 按后台预算取得执行权 → 可选压缩与语义质量检查 → 保存并验证 Artifact → 条件提交结果 → 交投影构建。每一步记录检查点，前台越线时暂停新的后台准入。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| Task 落库但消息未投递 | Outbox 按原任务重投 | 任务可查询且不丢失 |
| 重复取件、失去租约或旧内联回调 | 拒绝旧执行者发布，查询已有副作用 | 不重复批准结果 |
| 压缩未启用或不适用 | 明确 skipped，按策略使用 Original | 不将跳过计为压缩成功 |
| 质量不合格或 Artifact 写未知 | 隔离输出、保留 Original，查询持久结果 | 不以高压缩比掩盖语义错误 |
| 调用明确要求压缩但压缩失败 | 返回该目标失败/部分结果及可查任务 | 不隐式绕过承诺 |
| 后台队列满或前台延迟越线 | 拒绝/延后新任务，降速/暂停并告警 | 已确认主事实保留，前台资源受保护 |
| 新压缩策略退化 | 停止扩大并回退稳定策略，保留旧 Artifact 证据 | 未获准的新表示不进入当前投影 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 任务状态与执行通道 | 可靠状态、Outbox、Broker/Worker、时钟和执行身份 | RF / Infra；B 实现业务 Task |
| 正式内容与 Artifact | 精确来源、持久化和 checksum | B-EPIC-03；P2 E2 |
| 压缩模型与质量规则 | 可调用模型/资源、版本制品、固定评测集和质量门槛 | B；模型运行资源 / 产品 / 测试 |
| 前台及资源观察 | Working 指标、独立实例、资源配额与新鲜健康数据 | B-EPIC-06；RF / Infra / A / C |
| 投影与恢复 | 消费获准 Artifact；共享 retry budget 和扫描恢复 | B-EPIC-05 / 08 |

接口依赖：AL-B03、AL-B07、AL-RF01、AL-RF02、AL-RF03、AL-RF05、AL-P201。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按项目 P015 / P065 契约及真实环境窗口推进。

### 7. 验收标准

1. 落库未投递、重复消息、进程重启、租约失效和内联转 Task 均可恢复；旧执行者不能发布当前结果。
2. 只有质量合格、来源仍有效且持久化已证实的 Artifact 可被使用；失败/未知保留 Original，但回退需符合策略和请求承诺。
3. 固定数据集、策略和真实持久化字节口径下，压缩比 ≥5× 且质量门槛通过；报告失败/跳过覆盖率及总占用，不将其宣称为全系统存储下降 5 倍。
4. Working 核心 API 在已冻结的混合负载 Profile 下端到端 P99 <10 ms，同时报告错误率和吞吐；后台上限、队列满、越线节流、恢复放行均可复现。
5. 压缩、重建和补发使用有界后台预算；SDK 重试不与业务重试叠加扩散。
6. Shadow、Canary、Rollback 保留版本/输入/质量/性能证据；覆盖 RM-T10～12 / 23、RAT-09、BP2T-05 / 08 / 11。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。主执行岗位：B2；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D005 | Task/Outbox 与内联交接设计、压缩质量/字节口径、容量隔离与发布方案 |
| 开发 | D006 | D017 | 可靠任务执行、Artifact 质量与持久化、后台预算/节流及压缩策略控制实现 |
| 测试 | D018 | D024 | 重启/失权/交接测试、压缩质量与比率报告、混合负载和回退测试 |
| 联调 | D025 | D028 | 真实内容/模型/Worker 联调、前台保护压测及策略发布演练 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 5 |
| 开发 | 12 |
| 测试 | 7 |
| 联调 | 4 |
| **总人天** | **28** |

估算依据：设计 5 天覆盖执行、质量、资源与发布；开发 12 天覆盖 Task/Outbox 4、压缩/Artifact 4、预算节流 2、策略控制 2；测试 7 天含混合负载与质量回归；联调 4 天验证真实链路。

### 10. 备注

- WBS 对应：B-EPIC-04-T1、B-EPIC-04-T2、B-EPIC-04-T3。
- PRR Action：B-P0-1、B-P0-5（压缩部分）、B-P1-3（Compression checksum 部分）。
- 设计依据：主设计 §2.4～2.5、§5.7、§8.2；数据定义 §5.3～5.4、§6.1；P2 对接 BP2-04；B 生产就绪补充 B-P0-1 / 5、B-P1-3。
- 工作量及责任边界：共享 Task/Outbox 执行骨架和后台准入在本项只实现一次；05/07/08 复用。全局重试预算与恢复扫描归 08；Semantic 证据规则归 02；原始内容 Port 归 03；不包含从零训练模型或额外采购设备。

---

## B-EPIC-05 · Make Recallable & Projection Health

**Epic 范围**：ProjectionState Building → Ready；调用 A 的 Embedding / Vector Projection；ProviderResult 五态、版本校验、Stale / Rebuild 和投影健康告警。

**关联 PRR Action**：B-P0-4。

**Epic 级完成标准**：只有 B 写入领域 ProjectionState；Provider 未 READY 或证据不足不转 Ready；Pending / Stale 积压可观测和告警，重启及版本不匹配可恢复。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | B-EPIC-05 |
| Epic Name | Make Recallable & Projection Health |
| Feature ID | B-FEAT-05-01 |
| Feature Name | 长期记忆可召回构建与投影健康管理 |
| Owner | B（Remember / Memory Formation） |
| Contributor | B3；A（Embedding / VectorProjectionPort）；P2 E1 / RF；B2 |
| 目标里程碑 | M2（长期可召回） |

### 2. User Story

作为需要检索长期记忆的 Recall，我希望每个可召回标记都对应当前有效正文和完整、可查询的向量集合，从而避免部分构建、错模型或旧版本被当成已经可用的记忆。

### 3. 功能描述

#### 3.1 功能目标

统一内联/后台投影构建，固定完整输入，消费 A 的机制证据，执行 Ready Guard、条件发布、失效重建及积压告警。

#### 3.2 核心输入

- 当前有效 Episodic / Semantic、获准 Original / Artifact、memory_version / state_version 及精确来源。
- ChunkManifest、model_version、projection_schema_version、分块/预处理版本、目标空间/资源、维度、Task 租约。
- A 提供的 Passage 向量、逐项 ProviderResult、对象/操作/版本及可查询性证据。

#### 3.3 核心处理

1. 固定完整 ChunkManifest 和 build_fingerprint；保留 memory_id / chunk_id / memory_version / model_version / projection_schema_version 五元组，同时绑定来源、分块、预处理和模型空间。
2. 获有效执行权后 Pending → Building；按 usage=Passage 调 A 的 Embedding，验证 source_hash、实际模型/空间、维度、dtype 与有限数值。
3. 经 A 的 VEC-001 写入固定集合，逐项保存对象引用、操作身份及机制状态；批次 HTTP 成功或总 count 相同不足以证明全部完成。
4. ACCEPTED / PENDING 继续查询，UNKNOWN 查询原操作，FAILED 按可重试性处理；READY 仍须验证当前 Memory/资格、正文可读、全量 chunk、模型/Schema/metadata/目标空间和后端可查询性。
5. 以 memory_version、state_version、build_fingerprint 和租约作 CAS，保存 ReadyProof 并切换当前 Ready 集合；删除/更正/新构建已生效则旧结果失权并转清理。
6. 对象丢失、来源或模型/Schema 变化时将旧资格置 Stale 并建立关联重建 Task；输出 Pending/Building 年龄、Stale 比例、失败与修复指标及告警。

#### 3.4 核心输出

- ProjectionSet / ChunkManifest / ProjectionItem、五态领域状态、稳定对象引用与逐项 ProviderResult。
- 当前 ReadyProof、可召回资格、重建 Task 与失权残留清单。
- Pending / Stale 监控、阈值触发记录及恢复证据。

### 4. 正常流程

读取当前有效 Memory 与获准内容 → 固定 manifest/输入指纹 → Pending / Building → 调用 Passage Embedding → 经 VEC-001 写入并查询完整集合 → Ready Guard → CAS 保存 ReadyProof → 向 MemoryRead 发布资格并产生变化 Signal。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 同维度但不同模型空间、非法向量 | 拒绝写入并保留模型/输入错误 | 不伪造向量或 Ready |
| 部分 chunk 成功或仅总 count 相等 | 保持非 Ready，核验完整 manifest | 部分成功不升级集合状态 |
| Provider UNKNOWN 或响应丢失 | 查询原操作/对象，不盲目重复写 | 状态按真实证据收敛 |
| Artifact / 分块改变但五元组未变 | 用完整输入指纹区分新构建 | 不复用或覆盖旧输入结果 |
| Ready CAS 前删除、更正或租约失效 | 拒绝发布并登记残留清理 | 旧版本不能复活 |
| 已 Ready 对象后来丢失 | 先 Stale，再创建关联修复 Task | 保留历史成功记录及当前不可用事实 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| Memory 与获准表示 | 当前版本/资格、完整正文、质量合格 Artifact | B-EPIC-01 / 02 / 03 / 04 |
| 共享 Embedding | 真实 Passage 向量、模型/空间兼容和输入绑定 | A |
| VectorProjectionPort / VEC-001 | 五态、逐项写/查/删及集合可查询性证明 | A 机制；P2 E1 物理事实 |
| 执行与恢复 | Task/租约、预算、失权写回控制及统一扫描 | B-EPIC-04 / 08；RF |
| 读取及告警消费 | 当前投影资格消费、监控与告警通道 | B-EPIC-06；A / RF / Infra |

接口依赖：AL-A01、AL-A02、AL-P203、AL-P204、AL-B07、AL-RF02、AL-RF05。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按项目 P015 / P065 契约及真实环境窗口推进。

### 7. 验收标准

1. Projection 领域五态统一为 Pending / Building / Ready / Failed / Stale；Provider UNKNOWN 不新增为领域状态。
2. B 唯一写入 Ready；A/Provider 的 READY 只是必要机制证据，完整 Ready Guard 和 CAS 必须通过。
3. 五元组及扩展输入指纹能区分换 Artifact、分块、预处理和同维不同空间；版本错配、缺 chunk 和非法向量均不 Ready。
4. UNKNOWN、重启、部分构建和旧回调可收敛，不将不同 manifest 或模型的成功部分拼成完整集合。
5. 当前 Ready 对象丢失先 Stale，再生成修复 Task；模型升级不自动更正 Memory 主事实。
6. 冻结监控 Profile 后注入积压，Pending/Building 年龄及 Stale 指标越阈值可告警，修复后可观察收敛。
7. L2 Simulator Contract 与 L3 真实 E1/Embedding 分别验收；覆盖 RM-T13～16 / 19 / 24、RAT-06～09、BP2T-06～08。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。主执行岗位：B3；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D004 | 构建契约、manifest/输入指纹、ProviderResult 映射、Ready Guard 与健康指标设计 |
| 开发 | D005 | D014 | A 能力接入、逐项状态处理、ReadyProof/CAS、Stale/重建及告警实现 |
| 测试 | D015 | D020 | 五态/部分写/换模型/旧 Worker/对象丢失及监控告警测试报告 |
| 联调 | D021 | D024 | 真实 A/P2 构建可查询性、A MemoryRead 消费及模型切换联调证据 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 4 |
| 开发 | 10 |
| 测试 | 6 |
| 联调 | 4 |
| **总人天** | **24** |

估算依据：设计 4 天；开发 10 天覆盖输入/调用 3、逐项结果 2、ReadyProof/CAS 3、健康与重建接入 2；测试 6 天覆盖集合、版本和故障；联调 4 天验证真实向量与 Recall 资格。

### 10. 备注

- WBS 对应：B-EPIC-05-T1、B-EPIC-05-T2、B-EPIC-05-T3、B-EPIC-05-T4、B-EPIC-05-T5、B-EPIC-05-T6。
- PRR Action：B-P0-4。
- 设计依据：主设计 §2.6、§3.5；数据定义 §6.2～6.5；A 对接 RA-06 / 07；P2 对接 BP2-05 / 06；B 生产就绪补充 B-P0-4。
- 工作量及责任边界：本项负责 B 的投影领域编排与验证。Embedding、ANN、向量机制及 ProviderResult 适配由 A 负责；通用 Task 和预算复用 04/08，不另建恢复框架。

---

## B-EPIC-06 · MemoryRead Capability

**Epic 范围**：面向 A 的 scope / filter / version / state / confidence / stability / decay input / conflict / evidence；承接详细设计要求的当前 Working 物化、WorkingRead 与复核。

**关联 PRR Action**：—（为 B-P0-1、B-P1-1 / 2 提供 Working 前台验证入口）。

**Epic 级完成标准**：A 不感知 B 内部 Redis / DB / Schema；返回权威 Memory facts，partial / degraded / 无法核验语义明确；Working 当前能力独立于长期投影。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | B-EPIC-06 |
| Epic Name | MemoryRead Capability |
| Feature ID | B-FEAT-06-01 |
| Feature Name | 当前 Working 读写与权威记忆读取能力 |
| Owner | B（Remember / Memory Formation） |
| Contributor | B2；B1（领域策略）；A / RF / Redis Provider |
| 目标里程碑 | M2（读取交接） |

### 2. User Story

作为组装当前上下文和长期记忆的 Recall，我希望通过稳定能力获取当前 Working、Memory 资格和合法正文映射，并在发出前重新核对，从而正确处理空结果、不可核验、旧版本及失效内容。

### 3. 功能描述

#### 3.1 功能目标

交付当前 Working 物化与有界读取、MemoryRead 快照、语义属性与 Canonical 映射交接，并提供初查、发出前和重放前所需复核能力。

#### 3.2 核心输入

- 已授权 Scope、当前 Session / business_task_id、筛选/limit、读取用途和候选原始引用/版本。
- 权威 Memory / MemoryVersionState、有效时间、精确内容映射、ReadyProof、策略/冲突/证据。
- Working 可靠正文来源、Redis 条件写入/目标 generation、cache_expires_at、读取 deadline。

#### 3.3 核心处理

1. 从已确认事实和用途建立 WorkingMaterialization，向独立 Working Redis 写入精确版本/generation；Redis 只是当前副本，业务资格来自 B 可靠事实。
2. WorkingRead 根据当前 Session/业务任务与 Scope 有界读取，复核版本、用途与业务有效期；Working 不依赖 Embedding 或 Projection Ready。
3. 返回本次覆盖范围和 complete / partial / unavailable；完整零条必须有范围完成证据，超时、权限未知和缓存 miss 不直接解释为没有 Memory。
4. MemoryRead 对候选原始版本提供已知排除或无法核验结论，独立返回 working / long_term 可用性、confidence / stability / decay input、冲突完整性及证据。
5. 提供批准的 Original / Artifact 精确引用、编码/范围/摘要及来源，供 A 经 CanonicalLoad 消费；不悄悄给旧候选替换新版正文。
6. 支持 A 初查、发出前及重放前重新复核；并发删除/撤权/更正按双方明确的一致性窗口处理，记录版本依据和实际读观察。

#### 3.4 核心输出

- WorkingMaterialization、WorkingReadResult 的范围/覆盖/错误及当前正文或合法引用。
- MemoryReadSnapshot：版本、Type/Status、Scope、有效期、语义属性、冲突与可核验证据。
- 独立 Working / long_term 资格、合法内容映射及复核依据。

### 4. 正常流程

接收当前写入或 A 读取请求 → 验证 Scope 和当前用途 → 物化/读取 Working 或核对候选版本 → 复核生命周期与表示资格 → 返回真实覆盖状态、语义事实及内容映射 → A 在发出/重放前再次复核。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| Redis 写未知、miss 或 TTL 到期 | 保持事实，查询/重建副本；无获准回退时明示不可用 | 不将 miss 当 Memory Expired 或完整空 |
| 读取超时或范围未完成 | 返回 partial / unavailable 和覆盖依据 | 不以零条掩盖故障 |
| 候选版本已 Superseded / Deleted / Expired | 返回已知排除与允许披露的原因 | 不提供旧版当前资格 |
| 权限或当前版本无法核验 | 返回不可验证错误 | 不编造已删除或不存在事实 |
| 冲突成员、策略或正文不完整 | 明示缺口/不可用表示 | 不替 A 静默选择一方 |
| Prewarm 资源耗尽 | 验证 Working 独立资源与当前版本 | 不由 C 驱逐 Working 解决预热压力 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 权威 Memory 及语义 | 当前资格/有效期、版本/冲突、证据与衰减策略 | B-EPIC-01 / 02 |
| 内容映射与投影 | 获准正文、精确版本及当前 ReadyProof | B-EPIC-03 / 05 |
| Working Redis | 独立实例、条件写/读、版本与容量观察 | Redis Provider / RF / Infra |
| Recall 消费 | Scope/候选引用、复核时点和内容加载规则 | A |
| 恢复与降级策略 | 从可靠来源重建、半故障进入/退出及准入 | B-EPIC-08；RF |

接口依赖：AL-B05、AL-B06、AL-B07、AL-A03、AL-RF03、AL-RF04。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按项目 P015 / P065 契约及真实环境窗口推进。

### 7. 验收标准

1. Working 创建/读取绑定可靠事实与当前 generation；Working 写入未确认不能假报当前可用，旧 Worker 不能覆盖新版。
2. 当前 Working 可在长期能力不可用时独立读取；Working / long_term 两类可用性分别报告。
3. 完整空、partial、unavailable、已知排除与无法核验分别覆盖；limit/筛选后的范围完成性可追踪。
4. 业务到期立即排除，缓存过期不修改 Memory 生命周期；无批准的可靠来源回退时不冒充 Working 成功。
5. 更正、删除、撤权发生在初查后，A 能在最终发出和重放前复核；不将旧候选与新正文拼接。
6. A 经能力获取事实/映射，不直接访问 B 内部 key/schema；冲突、策略缺失和精确范围错误均明示。
7. 固定 Working 核心 API Profile 下端到端 P99 <10 ms，保留有效长尾和错误率；覆盖 RM-T08 / 09 / 21、RAT-01～05、BCT-09。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。主执行岗位：B2；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D003 | Working 物化/读契约、覆盖状态、MemoryRead 快照与复核一致性边界 |
| 开发 | D004 | D009 | Working 条件读写、范围过滤、MemoryRead/语义属性和内容映射交接实现 |
| 测试 | D010 | D013 | TTL/有效期、空/partial/超时、并发失效和基线 Working 性能报告 |
| 联调 | D014 | D016 | A 初查/发出/重放复核、真实 Redis 隔离及正文加载联调证据 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 3 |
| 开发 | 6 |
| 测试 | 4 |
| 联调 | 3 |
| **总人天** | **16** |

估算依据：设计 3 天；开发 6 天覆盖 Working 读写 2、权威过滤/复核 2、语义与映射交接 2；测试 4 天覆盖范围/版本和基础性能；联调 3 天验证 A 真实消费与独立 Redis。

### 10. 备注

- WBS 对应：B-EPIC-06-T1、B-EPIC-06-T2、B-EPIC-06-T3。
- PRR Action：—（为 B-P0-1、B-P1-1 / 2 提供 Working 前台验证入口）。
- 设计依据：主设计 §2.4、§3.1、§4.2、§6.2；数据定义 §5.1～5.2、§6.6；A 对接 RA-01～05；C 对接 BC-05。
- 工作量及责任边界：详细设计把 WorkingRead 归入 B-EPIC-06，本项同时承接其必要物化读写，不增加独立 Feature。本项测基础读写性能；04 测压缩/后台竞争，08 测迁移/故障恢复，不重复计算同一轮压测。

---

## B-EPIC-07 · MemorySignal Capability

**Epic 范围**：B → C MemorySignal；Signal Schema / Version；Submitted、补发及消费确认语义。

**关联 PRR Action**：—。

**Epic 级完成标准**：Submitted 不等于已消费；重启后未完成 Signal 可恢复/补发；C 通过契约消费，不理解 B 内部存储。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | B-EPIC-07 |
| Epic Name | MemorySignal Capability |
| Feature ID | B-FEAT-07-01 |
| Feature Name | 记忆变化信号、可靠投递与消费确认 |
| Owner | B（Remember / Memory Formation） |
| Contributor | B1；C / RF；B3（恢复协作） |
| 目标里程碑 | M2（B → C 信号交接） |

### 2. User Story

作为接收记忆变化并决定驻留与调度的 Operate，我希望获得有来源、有版本、可重放的 MemorySignal，并能明确反馈消费结果，从而在重复、乱序和中断时继续处理真实业务变化。

### 3. 功能描述

#### 3.1 功能目标

实现不可变变化载荷、事实与事件可靠提交、至少一次投递、匹配 C 持久消费 Ack 和有界补发，区分事件、传输与 C 动作状态。

#### 3.2 核心输入

- 已提交 Memory 的创建、分类/生命周期、版本、投影及获准使用变化。
- signal_id / signal_version、memory_version / state_version、Scope 摘要、importance、策略/原因与发生时间。
- C 的事件消费契约、载荷绑定 Ack / 查询结果，RF 通道与积压/保留策略。

#### 3.3 核心处理

1. 从已提交事实生成不可变 MemorySignal；内部未分类 Active 不发送无合法 Type 的创建事件，分类发布后补齐。
2. 将完整变化记录或 Signal 待办随业务事实可靠提交；不同变化生成不同事件，重发沿用同一身份/载荷。
3. 至少一次投递，分别记录 Pending、Submitted 和 Succeeded；只有匹配 signal_id/version 与载荷摘要的 C 持久消费 Ack 才完成交接。
4. 处理 Ack 丢失/先到/错版本、重复与乱序；删除/失效事件不能被简单合并吞掉，旧 Active 事件不能恢复新删除资格。
5. 补发复用原事件，预算耗尽告警并保留人工恢复；历史事件缺失时只能明确发 snapshot，不伪造历史变化。
6. 输出真实本地观察到 RF；使用字段无可靠证据为 unknown/null，不把发送 Signal 当成获准订阅 A AccessTrace。

#### 3.4 核心输出

- 不可变 MemorySignal、可恢复 SignalDelivery、匹配消费 Ack 与投递历史。
- 积压/年龄/失败指标、补发与快照水位、人工恢复审计。
- 与 C 的变化交接证据；不输出或代写 C 的 TierAction。

### 4. 正常流程

业务事实变化提交 → 保存不可变事件/可靠待办 → 按预算投递 → 记录 Submitted → 接收或查询 C 匹配持久消费 Ack → Succeeded。未确认事件保留原身份补发；C 调度动作独立推进。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| Broker / RF 已 Ack、C 未消费 | 只记录已发送/受理 | Signal 不置 Succeeded |
| C 已消费但 Ack 丢失或先到 | 按事件查询/重放并核对发送记录和载荷 | 确认后收敛，不创建新业务事件 |
| 重复、旧版本或错误载荷 Ack | 去重或拒绝并留审计 | 不完成其他信号 |
| 删除后迟到 Active / 使用事件 | 按状态版本与屏障处理 | 不恢复资格、不吞失效 |
| C 不可用、积压或预算耗尽 | 有界补发、告警、保留人工恢复入口 | 主事实不回滚 |
| Outbox 满或无法可靠保存 | 按实际主事实提交点报告缺口并限制准入 | 不声称已可靠交接 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 业务事实变化 | 完整不可变来源、类型/版本/投影变化 | B-EPIC-01 / 02 / 05 |
| 事件状态与通道 | 可靠提交、投递、Schema/权限与观察 | RF / Infra；B 维护事件事实 |
| Signal 消费与 Ack | 幂等持久消费、查询/确认、版本与删除优先级 | C |
| 统一恢复及预算 | 有界扫描、退避、告警、人工恢复审计 | B-EPIC-08；复用 04 执行机制 |

接口依赖：AL-C01、AL-C02、AL-C03、AL-RF02、AL-RF04。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按项目 P015 / P065 契约及真实环境窗口推进。

### 7. 验收标准

1. 事实变化与事件待办可在重启后恢复；历史载荷不被当前 Memory 状态覆盖。
2. Pending / Submitted / Succeeded 各有对应证据；仅 C 匹配持久消费 Ack 能完成 Signal。
3. Ack 丢失、先到、重复、乱序和错误版本均可处理；C 幂等消费，同一事件不重复强化价值。
4. 删除/失效不能被事件合并吞掉；快照补齐明确身份和水位，旧事件不复活新状态。
5. C 不可用时已确认 Memory 和基础读不回滚；未持久保存事件则明确交接缺口。
6. 未获准的 A 观察消费关闭，无真实模型使用证据不填 true 或 false；覆盖 RM-T17 / 18 / 20、BCT-01～04 / 06～08 / 10。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。主执行岗位：B1；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D002 | Signal/Ack 契约、不可变载荷、原子交接、乱序/补发与积压方案 |
| 开发 | D003 | D007 | 事件生成与投递、匹配 Ack/查询、去重补发及积压告警实现 |
| 测试 | D008 | D010 | Ack 丢失/先到/错版本、乱序删除、Outbox 满和重启回归报告 |
| 联调 | D011 | D012 | C 持久消费、RF 通道、去重及删除优先级联调证据 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 2 |
| 开发 | 5 |
| 测试 | 3 |
| 联调 | 2 |
| **总人天** | **12** |

估算依据：设计 2 天；开发 5 天覆盖载荷/可靠提交 2、投递确认 2、补发与积压 1；测试 3 天覆盖 Ack 和顺序边界；联调 2 天验证 C 持久消费。

### 10. 备注

- WBS 对应：B-EPIC-07-T1、B-EPIC-07-T2。
- PRR Action：—。
- 设计依据：主设计 §2.7；数据定义 §7.1～7.3；C 对接 BC-01～03 / 06。
- 工作量及责任边界：本项只完成事实事件交接；C 的热度、驻留计划、预热和动作实现由 C 负责。通用 Task/Outbox 执行机制复用 04，全局恢复预算复用 08；事件载荷和匹配消费逻辑在本项计入。

---

## B-EPIC-08 · Recovery / Reconciliation & Retry Governance

**Epic 范围**：未完成 Task、Projection Pending、内容写 unknown、重启/重复/旧版本恢复；Retry Budget；Redis 半故障；Working × migration 共存和双 Owner 窗口。

**关联 PRR Action**：B-P0-2、B-P1-1、B-P1-2、B-P1-3（删除级联 / 超时收敛部分）。

**Epic 级完成标准**：重试有预算、指数退避和 jitter；半故障有进入/退出阈值；任务可恢复且不重复主事实；unknown 可查询，旧版本不覆盖；迁移和双 Owner 窗口中 Working P99 与事实正确性可验收。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | B-EPIC-08 |
| Epic Name | Recovery / Reconciliation & Retry Governance |
| Feature ID | B-FEAT-08-01 |
| Feature Name | 记忆恢复对账、全局重试与故障治理 |
| Owner | B（Remember / Memory Formation） |
| Contributor | B3；B1 / B2；A / C / P2 / RF / Infra / IS |
| 目标里程碑 | M3（真实集成与生产就绪） |

### 2. User Story

作为维护 Remember 连续服务的运行人员，我希望在进程、存储、网络和迁移发生故障后，能根据真实证据恢复任务、修复派生结果和清理失效表示，并控制重试压力，从而恢复服务且不丢事实、不复活旧数据。

### 3. 功能描述

#### 3.1 功能目标

建立横切各 Epic 的统一对账扫描、业务重试预算、Redis 半故障准入、精确删除/恢复屏障和故障矩阵，汇总 B 侧真实集成及生产验收证据。

#### 3.2 核心输入

- RememberOperation、未完成 Task、Pending/Building/Stale Projection、ContentWriteIntent、SignalDelivery、CleanupRecord。
- 权威 Memory/内容/状态版本、删除与授权屏障、Provider 对象/操作状态、租约/fencing 和备份水位。
- 每条调用边的 Retry Owner、次数/总时长/退避/jitter、Redis 延迟/连接/内存观察、迁移及双 Owner 故障计划。

#### 3.3 核心处理

1. 启动先恢复可靠事实、当前指针和删除/授权屏障，再开放读取；有界扫描未完成项并取得租约，不根据进程重启推断任务成功。
2. 重读当前版本、来源证据、有效时间和取消/删除决定，再查询实际内容、完整投影集合、Redis generation 或 C 匹配 Ack。
3. 区分已完成、明确未执行、结果未知和目标后来损坏；已完成补登记，确认未执行才重试，失效先撤资格再清理，历史成功损坏建立新修复 Task。
4. 每条调用边明确一个主要 Retry Owner，次数含首次、次数和总时长双限制；应用指数退避与 jitter，统一约束 SDK/任务重试，预算耗尽保持可查失败/未决。
5. 按冻结的 Redis 延迟、连接和内存 Profile 进入降级，恢复达到退出阈值和观察窗口后渐进准入；重建/补发复用 04 后台配额。
6. 追踪 Original、Artifact、Projection、Working、A 派生缓存/历史 Context、C 热副本及证据依赖的失效；精确版本/generation 清理，逻辑删除与物理擦除分别确认。
7. 执行 Working × migration、W1～W5 双 Owner 窗口和备份恢复演练，验证旧回调、迟到副作用与删除防复活；输出端到端故障、性能和审计报告。

#### 3.4 核心输出

- ReconciliationRecord、CleanupRecord、AuditCorrection、新修复 Task 及逐项完成/未决证据。
- 调用边 Retry Budget 表、半故障进入/退出/恢复准入配置、告警和人工操作手册。
- W1～W5 故障矩阵、删除/备份恢复记录、混合负载及 B 侧生产验收报告。

### 4. 正常流程

启动恢复屏障 → 扫描未决对象并取得处理权 → 重读当前事实与版本 → 查询物理/消费证据 → 在预算内补登记/重试/重建/清理 → 再核验目标 → 保存结论或下次查询及告警。读屏障即时生效，物理清理分别收敛。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 写 unknown、短时 404 或缺回调 | 保留未知，查询原操作与可见性证据 | 不盲重试、不猜成功 |
| 租约丢失、旧版本或已取消任务 | 拒绝发布，继续追踪可能在途的外部副作用 | 旧执行不能恢复当前资格 |
| 历史成功目标后来丢失 | 目标置 Stale，建立新关联修复任务 | 不篡改原任务成功历史 |
| 发现历史误报成功 | 追加 AuditCorrection 和影响标记 | 保留原记录与纠正依据 |
| Redis 高延迟/连接/内存半故障 | 按阈值降级与限流，恢复后渐进放行 | 不把故障返回当业务空 |
| 重试预算耗尽或 Provider 证据不足 | 告警并保留责任方、原因和人工入口 | 不制造 Success 或无限重试 |
| 删除与迁移/迟到写/备份恢复竞争 | 先恢复屏障，按精确范围清理并核验共享引用 | 逻辑失效不等待清理，旧数据不复活 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 各业务领域查询和补偿 | 原操作、Task、内容、投影、Signal 的真实状态及幂等入口 | B-EPIC-01～07 |
| 物理查询与清理 | 精确对象/版本、负查询、删除层次、残留与备份水位 | P2 / Provider；向量经 A |
| 失效消费 | A 缓存/历史 Context、C 预热/在途动作的处理范围证据 | A / C |
| 状态/运行基础设施 | 备份恢复、租约/时间、Redis 健康、告警和恢复资源 | RF / Infra |
| 生产验收战役 | 真实 E1/E2/Redis 环境、迁移故障窗口、持续测试和证据归档 | IS 统筹；P2 / Infra / A / C；B 验证自身边界 |

接口依赖：AL-B05、AL-P202、AL-P204、AL-C02、AL-C03、AL-RF02、AL-RF03、AL-RF05。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按项目 P015 / P065 契约及真实环境窗口推进。

### 7. 验收标准

1. Task、内容写 unknown、投影积压和 Signal 未确认均可从可靠状态恢复；不重复主事实、不覆盖当前版本，未决项具有原因/Owner/下次动作。
2. 总次数和总时长受限，指数退避/jitter 生效；SDK 与任务重试无乘法放大，恢复流量受后台配额控制。
3. Redis 高延迟、连接和内存三类半故障分别验证进入、退出及恢复准入；恢复时无请求洪峰，错误/不可核验不假为空。
4. W1～W5 双 Owner 窗口按项目故障矩阵逐项注入；Working 与迁移并存的冻结 Profile 下端到端 P99 <10 ms，同时证明无主事实丢失、假成功和旧版本覆盖。
5. 删除级联覆盖全部获准表示及来源依赖；逻辑读屏障与物理完成分别记录；共享引用不误删，旧 cleanup 不删除新 generation。
6. 备份恢复先恢复删除/授权屏障；历史成功自然损坏创建新 Task，历史误报用追加审计纠正。
7. L1 领域、L2 Simulator、L3 真实环境结果分别留证；合同基线与 E1 集成持续验证分别归档。覆盖 RM-T03 / 04 / 08 / 10 / 11 / 16～24、BP2T-08～12、BCT-05 / 07～10。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。主执行岗位：B3；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 主要输出 |
| --- | --- | --- | --- |
| 设计 | D001 | D005 | 统一扫描/重试矩阵、半故障阈值方案、删除恢复屏障、W1～W5 与持续验收计划 |
| 开发 | D006 | D015 | 对账调度、预算/退避/jitter、半故障准入、精确级联与审计恢复实现 |
| 测试 | D016 | D023 | 重试风暴、三类 Redis 半故障、删除竞态、备份恢复和双 Owner 故障报告 |
| 联调 | D024 | D028 | 真实 A/C/P2 联合恢复、迁移共存、持续验收证据汇总及运行交接 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 5 |
| 开发 | 10 |
| 测试 | 8 |
| 联调 | 5 |
| **总人天** | **28** |

估算依据：设计 5 天覆盖跨对象恢复和故障矩阵；开发 10 天覆盖扫描/重试 4、半故障准入 2、级联/恢复屏障及审计 4；测试 8 天含故障注入与自动持续验证准备；联调 5 天含真实恢复、验收报告及交接。

### 10. 备注

- WBS 对应：B-EPIC-08-T1、B-EPIC-08-T2、B-EPIC-08-T3、B-EPIC-08-T4。
- PRR Action：B-P0-2、B-P1-1、B-P1-2、B-P1-3（删除级联 / 超时收敛部分）。
- 设计依据：主设计 §2.8、§3.3～3.5、§6.3～6.4、§8；数据定义 §7.5～7.7、§8；P2/C 接口删除和恢复场景；B 生产就绪补充 B-P0-2、B-P1-1～3；PRR Gap P0-6。
- 工作量及责任边界：复用 03 的内容查询/校验、04 的 Task/后台执行、05 的投影补偿和 07 的信号重投。本项计统一扫描/预算、跨对象恢复及系统故障验证。P2 负责物理迁移事务，C 负责调层动作；W1～W5 是交接故障窗口编号，区别于存储方案 W1。

---

## 附录：WBS / PRR 覆盖与共用工作边界

### WBS 覆盖

| Epic | Feature | WBS Task | 范围补充 |
| --- | --- | --- | --- |
| B-EPIC-01 | B-FEAT-01-01 | B-EPIC-01-T1～T4 | 接入、主事实、幂等和响应 |
| B-EPIC-02 | B-FEAT-02-01 | B-EPIC-02-T1～T3 | 分类、生命周期、版本/冲突及策略 |
| B-EPIC-03 | B-FEAT-03-01 | B-EPIC-03-T1～T4 | 内容 Port/Simulator、映射与未知查询 |
| B-EPIC-04 | B-FEAT-04-01 | B-EPIC-04-T1～T3 | 异步 Memoryize、压缩、执行恢复及后台容量 |
| B-EPIC-05 | B-FEAT-05-01 | B-EPIC-05-T1～T6 | 构建契约、调用、Ready、Stale/重建及健康 |
| B-EPIC-06 | B-FEAT-06-01 | B-EPIC-06-T1～T3 | Working 物化/读取、权威过滤和语义属性 |
| B-EPIC-07 | B-FEAT-07-01 | B-EPIC-07-T1～T2 | Signal Schema、投递和消费确认 |
| B-EPIC-08 | B-FEAT-08-01 | B-EPIC-08-T1～T4 | 统一恢复、对账、重试、半故障及删除级联 |

以上按原始 WBS 的逐项 Task 标题核对，共 29 个 B Task（4＋3＋4＋3＋6＋3＋2＋4），全部有对应 Feature；原始 WBS 首页“33”与正文枚举不一致，本文以实际 Task ID 为覆盖依据，不据此新增功能。

### PRR Action 覆盖

| PRR Action | Parent Gap | 计入 Epic | 验收证据 |
| --- | --- | --- | --- |
| B-P0-1 | P0-2 | 04 | 后台队列/并发上限、前台越线节流与混合负载 P99 |
| B-P0-2 | P0-2 | 08 | 全局次数/时长预算、指数退避、jitter 与恢复限速 |
| B-P0-3 | P0-2 | 03 | 写前容量 guard、容量满拒绝与告警 |
| B-P0-4 | P0-5 | 05 | Pending/Building 积压、Stale 指标与告警/修复 |
| B-P0-5 | P0-4 | 02（衰减/语义策略）；04（压缩） | 各自策略版本、Shadow/Canary 与回退演练 |
| B-P1-1 | P1-3 | 08 | Redis 延迟/连接/内存半故障进入/退出及恢复准入 |
| B-P1-2 | P1-5 | 08 | Working × migration、双 Owner W1～W5 矩阵 |
| B-P1-3 | P1-6 | 03（内容证据）；04（Artifact）；08（级联/恢复） | checksum 全链路、超时 head/原操作查询、精确删除与残留追踪 |

### 共用工作只计一次

| 共用能力 | 主要计入 | 其他 Epic 的投入边界 |
| --- | --- | --- |
| 主事实提交、幂等与检查点 | 01 | 02/07 只计各自领域变化及事件载荷的接入 |
| 内容 Port、Simulator 与精确查询/校验 | 03 | 04/06 消费正式映射；08 调用查询/清理接口，不重做 Port |
| Task/Outbox 执行、租约与后台准入 | 04 | 05/07/08 只计领域任务/投递的接入与业务处理 |
| Ready Guard 和投影补偿 | 05 | 08 负责扫描/调度和跨对象恢复，复用构建能力 |
| 当前 Working 物化与 MemoryRead | 06 | 01 消费确认；04/08 以此为前台观测对象 |
| Signal 载荷与 C 持久消费确认 | 07 | 其他 Epic 产生不可变变化并调用交接能力 |
| 全局 Retry Budget 与统一对账 | 08 | 各 Epic 实现自己的查询/补偿，不各建一套恢复调度 |
| 性能/故障证据 | 06 基线；04 后台竞争；08 迁移/恢复与总验收 | 同一测试结果可引用，执行工时不重复计入 |

### 外部就绪条件与确认责任

| 最迟目标窗口 | 需形成的具体结果 | 确认责任 | 影响 |
| --- | --- | --- | --- |
| P015 | W1 可靠提交、租约/Outbox、幂等/Retry Owner 和恢复 Profile | B + RF/Infra；AL-B01、AL-RF01/02/05 | 主事实、Task 和恢复可信边界 |
| P015 | 分类/证据/衰减/保留规则，压缩质量与真实字节口径 | B + 产品/测试；AL-B02～05/07；PRR D-3 | 生命周期、压缩及策略验收 |
| P015 | Passage/分块与 VEC-001、完整 manifest/五态/可见性样例 | A 提供，B 消费，P2 证明；AL-A01/02、AL-P203 | 投影真实 Ready |
| P015 | OBJ-001 精确版本/durable/checksum、超时负查询及删除层次 | P2/Provider 提供，B 消费；AL-P201/202/204 | 内容完整性、未知与删除收敛 |
| P015 | WorkingRead/MemoryRead/复核窗口、Signal 通道/持久 Ack/失效处理 | B 提供；A/C 消费确认；AL-B06、AL-A03、AL-C01～03 | 读交接与事件交接 |
| P015 | 独立 Redis/资源、性能/容量/半故障阈值、72h 战役范围与环境 DRI | RF/Infra/IS/验收负责人；AL-RF03/05；PRR D-4 | 前台性能、迁移及持续验收 |
| P015 | 是否批准 B 消费 A 使用观察及完整证据链 | RF/架构确认，A 提供；AL-RF04 | 未批准时保持该新消费路径关闭，字段 unknown |
| P065 | 真实 E1/E2、A/C 服务及 Working/Prewarm 独立实例实际可联调 | 各 Provider + RF/Infra/IS，B 核验消费证据 | P066 起 L3 与后续 M3 窗口 |

上述接口与资源清单记录本次计划的就绪条件；确认人、实际交付日期及契约版本仍在 Remember 主设计 §7 由对应 Owner 回填。运行阈值在冻结 Profile 中给出，本文不把未验证参数写成生产默认值。B 的交付是否完成，以对应范围的契约、实现/故障和真实集成证据为准。
