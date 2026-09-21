## 里程碑

| 时间 | 里程碑 |
| --- | --- |
| 2026-09-18 | 核心契约 v0.1 基线确定：Remember 主事实与投影状态、Recall 请求与 Context Pack、Operate 信号与动作状态，以及公共字段、授权、幂等和跨模块接口；形成接口样例与验证清单，异常和恢复细节随联调补齐 |
| 2026-10-16 | 三模块基础闭环验证完成：Remember 接入、主事实与可召回构建，Recall Query 到 Context Pack，Operate 信号消费、基础策略与动作反馈；同步开展已就绪接口的真实联调、性能基线测量与故障验证 |
| 2026-11-06 | 主要真实接口首轮联调完成：验证 Remember 正文与投影、Recall 检索与正文读取、Operate 执行反馈与对账，以及共享运行时和外部板块交接；形成恢复验证结果和接口问题清单，明确阻断项责任人与关闭节点 |
| 2026-11-13 | 对齐 P2 RC 接口，冻结三模块 MVP 功能与接口范围，形成 RC 版本；确定状态与降级语义、模型与策略版本、发布回退流程及验收基线，后续集中处理缺陷与兼容性修正 |
| 2026-11-16～11-20 | 三模块集中验收：汇总前期真实联调、故障注入和性能测试证据，完成记忆形成、召回使用、调度反馈及恢复对账的端到端验证，形成验收报告与缺陷清单 |
| 2026-11-23～11-27 | 预留 5 个工作日用于阻断缺陷修复、接口兼容复验和完整链路回归；关闭交付阻断项，确认发布版本、回退方案与交付材料 |
| 2026-11-30 | 完成交付版本与材料确认，正式交付 MVP |

## 团队与分工

| 组 | Owner |
| --- | --- |
| Remember | 杨鹏通 / 赵旭东 |
| Recall | 陈凯 / 肖宇 |
| Operator | 沈家隆 / 谭旭梁 |
| Shared Runtime Foundation | 沈家隆 / 杨鹏通 |
| External Integration | 陈凯 / 赵旭东 / 谭旭梁 |
| 环境搭建与验收 | 陈凯 / 肖宇 |

# 第一章 Remember

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
- **估算合计 168 人天＝设计 29＋开发 69＋测试 43＋联调 27。** 按一人一天有效工作计量，各阶段含必要评审和修正；共享契约、Task/Outbox、Simulator 与恢复框架只计算一次，不因两人协作重复计入。
- **规划团队 2 人，项目窗口为 2026-09-09～2026-11-30。** 杨鹏通和赵旭东共同承担 Remember 的 8 个 Epic；每个 Feature 由一名 Owner 牵头，另一人作为 Contributor 参与评审、接口交接、联调和故障复核，两人共同对 Remember 整体交付负责。同一人员可并行承担多个 Epic，但同一有效投入时段不重复计数。168 人天沿用参考汇总口径，不能用 168 ÷ 2 直接推导完工日期，具体投入以项目窗口和里程碑为准。
- 估算依据为 8 个 Epic 的 WBS、Remember 详细设计和 PRR 增补，按下文具体交付物自下而上核算。假设已有可接入的模型服务、RF/状态与执行基础设施、Redis 和 P2 Provider；包含 B 的契约、适配、Simulator、领域逻辑与验证，不包含从零建设外部平台/模型或 P2 引擎的投入。
- 2026-09-09～2026-11-30 共 83 个自然日；168 人天功能投入沿用参考汇总口径，功能人天按 Feature Owner 归集，Contributor 的协作不重复计入总量。两位 Owner 的具体投入按项目窗口并行安排，不能把 Epic 的局部 D 序号相加后当作项目日历。
- 本表为 2026-09-09 的文档估算，未从现有业务代码推断完成度，也未扣减既有实现。Remember 功能计划直接使用日历日期；MVP 截止 2026-11-30。
- 状态均为规划基线，不代表已签收。

### 两人分工与协作关系

| 执行人员 | 主要职责 | 负责 Epic | 设计 | 开发 | 测试 | 联调 | 功能投入（人天） |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| 杨鹏通 | 主事实、生命周期/策略、Signal、恢复治理；协同内容、压缩、投影和 Working | 01、02、07、08（Owner）；03、04、05、06（Contributor） | 14 | 34 | 21 | 13 | 82 |
| 赵旭东 | 正文与内容映射、压缩、投影、Working 读写；协同主事实、生命周期、Signal 和恢复 | 03、04、05、06（Owner）；01、02、07、08（Contributor） | 15 | 35 | 22 | 14 | 86 |
| 合计 | 2 人 | 8 个 Epic | 29 | 69 | 43 | 27 | 168 |

主事实、内容 Port/Simulator 和投影契约可以并行设计；杨鹏通使用内容契约样例推进主事实，赵旭东使用 A 的契约及模拟结果推进内容和投影。两人共同完成 Task/租约骨架、压缩和 WorkingRead，并在接口评审、异常用例、真实联调和验收复核中交叉参与。真实 Ready 联调依赖 A 的向量能力及 E1/E2 证据；A 读取与 C 消费联调依赖 B 的资格、映射和 Signal 契约。恢复接口、指标和故障用例从前期设计接入，各业务能力具备后汇合验收。

### 项目交付窗口

以下按项目里程碑安排 Remember 的共同出口；各 Epic 的 D 投入序号仍只表示本 Epic 的工作量，不表示串行日期。M1 对应基础闭环，M2 对应首轮真实接口联调，M3 对应验收、回归和 MVP 交付。

| 项目窗口 | 交付重点与阶段出口 | 杨鹏通人天 | 赵旭东人天 | 合计 |
| --- | --- | ---: | ---: | ---: |
| 2026-09-09～2026-09-18 | 核心契约 v0.1 基线：主事实、正文映射、生命周期、Working、Signal、Task、Projection、恢复契约及接口样例完成 | 14 | 16 | 30 |
| 2026-09-19～2026-10-16 | 基础闭环：01/02/03/06/07 完成开发、单项测试和 M1 交接；主事实、分类、正文、Working、Signal 可验证 | 29 | 30 | 59 |
| 2026-10-17～2026-11-06 | 首轮真实接口联调：完成异步加工、长期可召回、恢复对账，以及 A/C/P2/Redis 真实链路首轮验证 | 23 | 24 | 47 |
| 2026-11-07～2026-11-13 | RC 准备与冻结：汇总联调证据，冻结三模块 MVP 功能、接口、状态/降级语义和回退基线，不新增功能 | 6 | 6 | 12 |
| 2026-11-16～2026-11-20 | 集中验收：执行端到端记忆形成、召回使用、信号交接和恢复对账验收，形成验收报告与缺陷清单 | 5 | 5 | 10 |
| 2026-11-23～2026-11-27 | 阻断缺陷修复、接口兼容复验和全链路回归，关闭交付阻断项，确认发布候选版本 | 5 | 5 | 10 |
| 2026-11-30 | 确认发布版本、回退方案和交付材料，正式交付 MVP | 0 | 0 | 0 |
| **合计** | 各窗口复用下文 168 人天，不额外叠加；11 月 7 日后不再安排新增功能开发 | **82** | **86** | **168** |

真实 E1/E2、独立 Working/Prewarm Redis 和可联调的 A/C 能力目标在 **2026-11-06 前就绪**；2026-09-18 前确认契约、质量/性能/容量/重试参数及验收环境责任，2026-11-06 前复核真实环境。以上是排期所需交付条件，不是 Provider 已签收日期。若关键依赖未到位，可完成对应 L1/L2 工作，但受影响的真实集成和 M3 不能以模拟结果替代，应按实际等待和新增范围调整窗口。

持续验收按 PRR P0-6 分别准备“合同基线 72h/Chaos”和“E1 集成 72h”的证据，集中验收安排在 2026-11-16～2026-11-20，阻断缺陷修复、兼容复验和全链路回归安排在 2026-11-23～2026-11-27。B 的部署观察、问题定位及报告工时包含在已分配的测试/联调人天中；72h 是运行时长，不按两人全天值守折算。重新跑完整战役或新增 24 小时轮班值守不在当前基准估算中。

各 Feature 计划表的完成时间表示该 Feature 在相应里程碑的功能和交接出口；2026-11-07 之后不再新增 Feature。RC、集中验收、阻断缺陷修复和全链路回归由项目窗口统一执行，从已计入的测试/联调人天中按窗口归集，不重复增加功能人天。窗口人天按项目交付出口归集，跨窗口阶段以实际交付节点计入；各 Feature 第 9 节的人天仍是唯一核算依据。

### 阶段人天汇总

| 阶段 | 人天 | 说明 |
| --- | ---: | --- |
| 设计 | 29 | 主事实、生命周期、内容、投影、恢复和接口契约设计 |
| 开发 | 69 | 记忆形成、正文/Working、异步加工、投影和 Signal 能力实现 |
| 测试 | 43 | 业务规则、并发、版本、故障恢复和容量边界验证 |
| 联调 | 27 | 与 A、C、P2、Redis 及 Shared Runtime 的真实接口验证 |
| **合计** | **168** | 与本组 Epic 合计一致 |

### 跨组依赖联系人

| 依赖类别 | 具体依赖 | 对接联系人 | 最迟形成时间 |
|---|---|---|---|
| 公共请求、状态与任务 | RequestContext、CAS、租约、Task、Outbox、审计 | 沈家隆 / 杨鹏通（Shared Runtime） | 2026-09-18 |
| 正文与对象 Provider | 精确版本、durable、checksum、head/get、操作查询和删除证据 | 陈晔 / 杨文博（P2 E2）；陈凯 / 赵旭东 / 谭旭梁（External Integration） | 2026-10-16 |
| 向量与 Projection | Passage Embedding、VEC-001、投影写查删和完成证据 | 陈凯 / 肖宇（Recall）；张晋 / 胡孝阳（P2 E1） | 2026-10-16 |
| Recall 消费 | MemoryRead、CanonicalLoad、Working/Context 资格复核 | 陈凯 / 肖宇（Recall） | 2026-10-16 |
| Operator 消费 | MemorySignal、使用观察和表示资格变化的消费确认 | 沈家隆 / 谭旭梁（Operator） | 2026-09-18 |
| 环境搭建与验收 | Redis、P2、模拟器、故障窗口和证据归档 | 陈凯 / 肖宇 | 2026-11-23 |

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
| Owner | 杨鹏通 |
| Contributor | 赵旭东（内容绑定与 Working 交接协作） |
| 目标里程碑 | M1 · 2026-10-16（基础形成能力） |

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
| 接入、身份与请求封套 | 可信 Scope、授权撤销、deadline 与正式响应映射 | P4 / Auth（联系人待确认）；Shared Runtime：沈家隆 / 杨鹏通 |
| 可靠状态机制 | 事务、唯一幂等约束、CAS、提交查询与检查点 | Shared Runtime：沈家隆 / 杨鹏通；Infra 联系人待确认；Remember：杨鹏通 / 赵旭东 |
| 分类和生命周期 | 轻量 Type 发布、版本与状态规则 | Remember：杨鹏通 / 赵旭东 |
| 可靠正文与当前 Working | 内容绑定、主事实落点及 Working 确认 | Remember：杨鹏通 / 赵旭东 |
| 后台与事件交接 | 持久 Task、Signal 载荷和可恢复待办 | Remember：杨鹏通 / 赵旭东 |

接口依赖：AL-B01、AL-B02、AL-P201、AL-P202、AL-RF01、AL-RF02。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按 2026-09-18 核心契约基线、2026-11-06 首轮真实接口联调及后续 RC/验收窗口推进。

### 7. 验收标准

1. 未授权和跨 Scope 请求全部拒绝；同键同语义复用身份，同键异义稳定冲突；重试撤权不可沿用旧许可。
2. 提交前、正文已写、事务未知、事务后响应丢失等断点均能按原 operation 查询收敛，不重复 Memory，不提前删除候选孤儿。
3. 主事实、正文绑定、来源和恢复入口可逐项追踪；仅分配 ID、Broker 受理或 HTTP 成功均不足以确认事实。
4. Active 分类未发布时不对外 Success / Accepted / Partial；恢复后仍按合法 Type 和当前授权判断。
5. Success 覆盖全部 required_outputs；Accepted 的每个缺口都有持久接管；Partial 如实保留已确认结果，不隐式缩小原承诺。
6. Working-only 请求不等待 Embedding；长期请求在未 Ready 时不报告长期可召回。通过 RM-T01～RM-T05、RM-T18 及 BP2T-02 / 03 的对应场景。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。Owner：杨鹏通；Contributor：赵旭东；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D003 | 2026-09-09～2026-09-11 | 核心契约 v0.1：接入/幂等契约、Fact-First 提交序列、required_outputs 与响应判定表 |
| 开发 | D004 | D012 | 2026-09-12～2026-09-30 | 输入校验、操作登记、主事实事务、分类交接及查询响应实现；10-16 前具备基础闭环 |
| 测试 | D013 | D017 | 2026-10-01～2026-10-08 | 权限/幂等、提交断点、未分类重启和四类响应验证记录 |
| 联调 | D018 | D020 | 2026-10-09～2026-10-16 | 完成 P4/RF、内容/Working/后台交接和 M1 基础闭环验证 |

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
| Owner | 杨鹏通 |
| Contributor | 赵旭东（版本读取、失效处理与策略联调协作） |
| 目标里程碑 | M1 · 2026-10-16（分类与生命周期）；M2 · 2026-11-06（策略发布） |

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
| 主事实/版本 | 可靠事实、版本前置条件及历史来源 | Remember：杨鹏通 / 赵旭东 |
| 规则与质量数据 | 分类、独立证据、冲突、保留与删除规则及正反例 | Remember：杨鹏通 / 赵旭东；产品/测试联系人待确认 |
| 读取与信号 | 消费类型、资格、衰减和冲突完整性；传播失效 | Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁 |
| 共享使用观察 | 获准消费范围、去重身份、迟到窗口、实际使用证明 | Shared Runtime：沈家隆 / 杨鹏通；Recall：陈凯 / 肖宇；调用方联系人待确认；Remember：杨鹏通 / 赵旭东 |
| 失效清理 | 反向依赖、精确表示清理与恢复屏障 | Remember：杨鹏通 / 赵旭东 |

接口依赖：AL-B02、AL-B03、AL-B04、AL-B05、AL-A03、AL-RF04。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按 2026-09-18 核心契约基线、2026-11-06 首轮真实接口联调及后续 RC/验收窗口推进。

### 7. 验收标准

1. 覆盖五种生命周期的合法/非法转换；类型、memory_version、state_version 与物理 generation 分别校验。
2. uncertain 归 Working；SessionEnd 只触发评估；一次压缩或同源重复不能单独形成 Semantic。
3. 并发更正和旧回调不能覆盖新版；冲突成员及证据缺失有明确结果，A 不需猜测策略。
4. valid_to 到期立即失去当前读取资格；缓存 TTL 到期仅代表副本缺失；模型升级仅触发相关投影处理。
5. 来源撤销后受影响的派生结论可定位并先限制使用；删除不能被迟到访问/旧事件恢复。
6. 重复或跨版本使用事件不重复强化；仅 loaded / selected / emitted 时不宣称实际模型使用；未获准的 B 消费路径不启用。
7. 冻结规则集下 Shadow、Canary 和回退可复现，记录策略版本、评价时间、差异和回退原因；覆盖 RM-T06 / 07 / 20 / 21、RAT-05 / 10 / 11。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。Owner：杨鹏通；Contributor：赵旭东；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D004 | 2026-09-09～2026-09-15 | 核心契约 v0.1：类型/生命周期矩阵、版本与证据关系、衰减策略及发布验收样例 |
| 开发 | D005 | D014 | 2026-09-16～2026-09-30 | 分类与评估、版本/冲突、证据依赖及策略发布/回退实现；10-16 前完成基础状态能力 |
| 测试 | D015 | D019 | 2026-10-01～2026-10-08 | 状态边界、并发更正、证据撤销、迟到使用与策略黄金样例报告 |
| 联调 | D020 | D022 | 2026-10-09～2026-11-06 | 10-16 完成分类/生命周期基础交接，10-17～11-06 完成 A 语义策略、C 失效交接和 RF 获准观察首轮联调 |

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
| Owner | 赵旭东 |
| Contributor | 杨鹏通（主事实提交与内容绑定协作） |
| 目标里程碑 | M1 · 2026-10-16（内容基础能力） |

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
| 内容物理能力 | E2/Provider 精确版本、持久/完整性、操作查询和容量观察 | P2 E2：陈晔 / 杨文博；实际内容 Provider 联系人待确认 |
| 可靠状态 | 写意图、映射、幂等与引用原子保存 | Shared Runtime：沈家隆 / 杨鹏通；Infra 联系人待确认；Remember：杨鹏通 / 赵旭东 |
| 主事实与 Artifact | 消费绑定并保存来源/质量结论 | Remember：杨鹏通 / 赵旭东 |
| CanonicalLoad 消费 | 读取获准表示并核验版本、范围、摘要 | Recall：陈凯 / 肖宇；Remember：杨鹏通 / 赵旭东 |
| 统一恢复 | 孤儿保护、超时扫描、精确清理和重试预算 | Remember：杨鹏通 / 赵旭东 |

接口依赖：AL-B01、AL-B07、AL-P201、AL-P202、AL-P204、AL-RF05。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按 2026-09-18 核心契约基线、2026-11-06 首轮真实接口联调及后续 RC/验收窗口推进。

### 7. 验收标准

1. Simulator 覆盖持久写、精确读取、容量满、checksum 错误、partial、已写丢响应、明确未执行及暂不可见。
2. Memory / content / object 版本有明确映射；head v1 + get v2、整对象 hash 证明任意片段、裸 ETag 均不能通过内容确认。
3. 写前 guard 与写时满容量均明确拒绝并触发告警，不形成指向未确认正文的成功 Memory。
4. 未知查询能区分已写、明确未写和不可确认；安全重试具备负查询/幂等/迟到副作用依据。
5. 候选孤儿在操作或引用未确认前不删除；共享引用和新版本不被旧清理误伤。
6. L2 契约测试与 L3 真实 E2 的 durable、checksum、超时恢复分别留证，覆盖 RM-T03 / 04、BP2T-02～04 / 09 / 11 / 12。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。Owner：赵旭东；Contributor：杨鹏通；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D003 | 2026-09-09～2026-09-12 | 核心契约 v0.1：OBJ-001 契约与映射、容量 guard、超时/孤儿处理及 Simulator 场景 |
| 开发 | D004 | D010 | 2026-09-13～2026-09-30 | Port 消费适配、Simulator、内容绑定、完整性和容量保护实现；10-16 前具备正文基础链路 |
| 测试 | D011 | D015 | 2026-10-01～2026-10-08 | 精确版本/范围校验、容量满、三类超时与孤儿保护测试报告 |
| 联调 | D016 | D018 | 2026-10-09～2026-10-16 | 完成正文基础交接、A CanonicalLoad 交接和 M1 内容链路验证 |

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
| Owner | 赵旭东 |
| Contributor | 杨鹏通（任务交接、信号与恢复协作） |
| 目标里程碑 | M2 · 2026-11-06（异步长期加工）；M3 · 2026-11-30（容量验收） |

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
| 任务状态与执行通道 | 可靠状态、Outbox、Broker/Worker、时钟和执行身份 | Shared Runtime：沈家隆 / 杨鹏通；Infra 联系人待确认；Remember：杨鹏通 / 赵旭东 |
| 正式内容与 Artifact | 精确来源、持久化和 checksum | Remember：杨鹏通 / 赵旭东；P2 E2：陈晔 / 杨文博 |
| 压缩模型与质量规则 | 可调用模型/资源、版本制品、固定评测集和质量门槛 | Remember：杨鹏通 / 赵旭东；模型运行资源/产品/测试联系人待确认 |
| 前台及资源观察 | Working 指标、独立实例、资源配额与新鲜健康数据 | Remember：杨鹏通 / 赵旭东；Shared Runtime：沈家隆 / 杨鹏通；Infra 联系人待确认；Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁 |
| 投影与恢复 | 消费获准 Artifact；共享 retry budget 和扫描恢复 | Remember：杨鹏通 / 赵旭东；Shared Runtime：沈家隆 / 杨鹏通 |

接口依赖：AL-B03、AL-B07、AL-RF01、AL-RF02、AL-RF03、AL-RF05、AL-P201。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按 2026-09-18 核心契约基线、2026-11-06 首轮真实接口联调及后续 RC/验收窗口推进。

### 7. 验收标准

1. 落库未投递、重复消息、进程重启、租约失效和内联转 Task 均可恢复；旧执行者不能发布当前结果。
2. 只有质量合格、来源仍有效且持久化已证实的 Artifact 可被使用；失败/未知保留 Original，但回退需符合策略和请求承诺。
3. 固定数据集、策略和真实持久化字节口径下，压缩比 ≥5× 且质量门槛通过；报告失败/跳过覆盖率及总占用，不将其宣称为全系统存储下降 5 倍。
4. Working 核心 API 在已冻结的混合负载 Profile 下端到端 P99 <10 ms，同时报告错误率和吞吐；后台上限、队列满、越线节流、恢复放行均可复现。
5. 压缩、重建和补发使用有界后台预算；SDK 重试不与业务重试叠加扩散。
6. Shadow、Canary、Rollback 保留版本/输入/质量/性能证据；覆盖 RM-T10～12 / 23、RAT-09、BP2T-05 / 08 / 11。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。Owner：赵旭东；Contributor：杨鹏通；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D005 | 2026-09-11～2026-09-18 | 核心契约 v0.1：Task/Outbox 与内联交接设计、压缩质量/字节口径、容量隔离与发布方案 |
| 开发 | D006 | D017 | 2026-09-19～2026-10-09 | 可靠任务执行、Artifact 质量与持久化、后台预算/节流及压缩策略控制实现 |
| 测试 | D018 | D024 | 2026-10-10～2026-10-16 | 重启/失权/交接测试、压缩质量与比率报告、混合负载和回退测试准备 |
| 联调 | D025 | D028 | 2026-10-17～2026-11-06 | 完成异步 Memoryize、真实内容/模型/Worker 联调、前台保护压测及策略发布首轮证据 |

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
| Owner | 赵旭东 |
| Contributor | 杨鹏通（主事实版本、Ready 资格与恢复协作） |
| 目标里程碑 | M2 · 2026-11-06（长期可召回） |

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
| Memory 与获准表示 | 当前版本/资格、完整正文、质量合格 Artifact | Remember：杨鹏通 / 赵旭东 |
| 共享 Embedding | 真实 Passage 向量、模型/空间兼容和输入绑定 | Recall：陈凯 / 肖宇 |
| VectorProjectionPort / VEC-001 | 五态、逐项写/查/删及集合可查询性证明 | Recall：陈凯 / 肖宇；P2 E1：张晋 / 胡孝阳 |
| 执行与恢复 | Task/租约、预算、失权写回控制及统一扫描 | Remember：杨鹏通 / 赵旭东；Shared Runtime：沈家隆 / 杨鹏通 |
| 读取及告警消费 | 当前投影资格消费、监控与告警通道 | Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Shared Runtime：沈家隆 / 杨鹏通；Infra 联系人待确认 |

接口依赖：AL-A01、AL-A02、AL-P203、AL-P204、AL-B07、AL-RF02、AL-RF05。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按 2026-09-18 核心契约基线、2026-11-06 首轮真实接口联调及后续 RC/验收窗口推进。

### 7. 验收标准

1. Projection 领域五态统一为 Pending / Building / Ready / Failed / Stale；Provider UNKNOWN 不新增为领域状态。
2. B 唯一写入 Ready；A/Provider 的 READY 只是必要机制证据，完整 Ready Guard 和 CAS 必须通过。
3. 五元组及扩展输入指纹能区分换 Artifact、分块、预处理和同维不同空间；版本错配、缺 chunk 和非法向量均不 Ready。
4. UNKNOWN、重启、部分构建和旧回调可收敛，不将不同 manifest 或模型的成功部分拼成完整集合。
5. 当前 Ready 对象丢失先 Stale，再生成修复 Task；模型升级不自动更正 Memory 主事实。
6. 冻结监控 Profile 后注入积压，Pending/Building 年龄及 Stale 指标越阈值可告警，修复后可观察收敛。
7. L2 Simulator Contract 与 L3 真实 E1/Embedding 分别验收；覆盖 RM-T13～16 / 19 / 24、RAT-06～09、BP2T-06～08。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。Owner：赵旭东；Contributor：杨鹏通；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D004 | 2026-09-12～2026-09-18 | 核心契约 v0.1：构建契约、manifest/输入指纹、ProviderResult 映射、Ready Guard 与健康指标设计 |
| 开发 | D005 | D014 | 2026-09-19～2026-10-09 | A 能力接入、逐项状态处理、ReadyProof/CAS、Stale/重建及告警实现 |
| 测试 | D015 | D020 | 2026-10-10～2026-10-16 | 五态/部分写/换模型/旧 Worker/对象丢失及监控告警测试准备 |
| 联调 | D021 | D024 | 2026-10-17～2026-11-06 | 完成长期可召回构建、真实 A/P2 可查询性、A MemoryRead 消费及模型切换首轮联调 |

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
| Owner | 赵旭东 |
| Contributor | 杨鹏通（领域策略、版本复核与正文交接协作） |
| 目标里程碑 | M2 · 2026-11-06（读取交接） |

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
| 权威 Memory 及语义 | 当前资格/有效期、版本/冲突、证据与衰减策略 | Remember：杨鹏通 / 赵旭东 |
| 内容映射与投影 | 获准正文、精确版本及当前 ReadyProof | Remember：杨鹏通 / 赵旭东 |
| Working Redis | 独立实例、条件写/读、版本与容量观察 | Redis Provider 联系人待确认；Shared Runtime：沈家隆 / 杨鹏通；Infra 联系人待确认 |
| Recall 消费 | Scope/候选引用、复核时点和内容加载规则 | Recall：陈凯 / 肖宇 |
| 恢复与降级策略 | 从可靠来源重建、半故障进入/退出及准入 | Remember：杨鹏通 / 赵旭东；Shared Runtime：沈家隆 / 杨鹏通 |

接口依赖：AL-B05、AL-B06、AL-B07、AL-A03、AL-RF03、AL-RF04。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按 2026-09-18 核心契约基线、2026-11-06 首轮真实接口联调及后续 RC/验收窗口推进。

### 7. 验收标准

1. Working 创建/读取绑定可靠事实与当前 generation；Working 写入未确认不能假报当前可用，旧 Worker 不能覆盖新版。
2. 当前 Working 可在长期能力不可用时独立读取；Working / long_term 两类可用性分别报告。
3. 完整空、partial、unavailable、已知排除与无法核验分别覆盖；limit/筛选后的范围完成性可追踪。
4. 业务到期立即排除，缓存过期不修改 Memory 生命周期；无批准的可靠来源回退时不冒充 Working 成功。
5. 更正、删除、撤权发生在初查后，A 能在最终发出和重放前复核；不将旧候选与新正文拼接。
6. A 经能力获取事实/映射，不直接访问 B 内部 key/schema；冲突、策略缺失和精确范围错误均明示。
7. 固定 Working 核心 API Profile 下端到端 P99 <10 ms，保留有效长尾和错误率；覆盖 RM-T08 / 09 / 21、RAT-01～05、BCT-09。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。Owner：赵旭东；Contributor：杨鹏通；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D003 | 2026-09-09～2026-09-11 | 核心契约 v0.1：Working 物化/读契约、覆盖状态、MemoryRead 快照与复核一致性边界 |
| 开发 | D004 | D009 | 2026-09-12～2026-09-30 | Working 条件读写、范围过滤、MemoryRead/语义属性和内容映射交接实现 |
| 测试 | D010 | D013 | 2026-10-01～2026-10-08 | TTL/有效期、空/partial/超时、并发失效和基线 Working 性能报告 |
| 联调 | D014 | D016 | 2026-10-09～2026-11-06 | 10-16 完成 Working/MemoryRead 基础交接，10-17～11-06 完成 A 初查/发出/重放、真实 Redis 隔离及正文加载首轮联调 |

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
| Owner | 杨鹏通 |
| Contributor | 赵旭东（投影/内容状态与恢复补发协作） |
| 目标里程碑 | M2 · 2026-11-06（B → C 信号交接） |

### 2. User Story

作为接收记忆变化并决定驻留与调度的 Operator，我希望获得有来源、有版本、可重放的 MemorySignal，并能明确反馈消费结果，从而在重复、乱序和中断时继续处理真实业务变化。

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
| 业务事实变化 | 完整不可变来源、类型/版本/投影变化 | Remember：杨鹏通 / 赵旭东 |
| 事件状态与通道 | 可靠提交、投递、Schema/权限与观察 | Shared Runtime：沈家隆 / 杨鹏通；Infra 联系人待确认；Remember：杨鹏通 / 赵旭东 |
| Signal 消费与 Ack | 幂等持久消费、查询/确认、版本与删除优先级 | Operator：沈家隆 / 谭旭梁 |
| 统一恢复及预算 | 有界扫描、退避、告警、人工恢复审计 | Remember：杨鹏通 / 赵旭东；Shared Runtime：沈家隆 / 杨鹏通；B-EPIC-04：赵旭东 |

接口依赖：AL-C01、AL-C02、AL-C03、AL-RF02、AL-RF04。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按 2026-09-18 核心契约基线、2026-11-06 首轮真实接口联调及后续 RC/验收窗口推进。

### 7. 验收标准

1. 事实变化与事件待办可在重启后恢复；历史载荷不被当前 Memory 状态覆盖。
2. Pending / Submitted / Succeeded 各有对应证据；仅 C 匹配持久消费 Ack 能完成 Signal。
3. Ack 丢失、先到、重复、乱序和错误版本均可处理；C 幂等消费，同一事件不重复强化价值。
4. 删除/失效不能被事件合并吞掉；快照补齐明确身份和水位，旧事件不复活新状态。
5. C 不可用时已确认 Memory 和基础读不回滚；未持久保存事件则明确交接缺口。
6. 未获准的 A 观察消费关闭，无真实模型使用证据不填 true 或 false；覆盖 RM-T17 / 18 / 20、BCT-01～04 / 06～08 / 10。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。Owner：杨鹏通；Contributor：赵旭东；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D002 | 2026-09-13～2026-09-16 | 核心契约 v0.1：Signal/Ack 契约、不可变载荷、原子交接、乱序/补发与积压方案 |
| 开发 | D003 | D007 | 2026-09-17～2026-09-30 | 事件生成与投递、匹配 Ack/查询、去重补发及积压告警实现 |
| 测试 | D008 | D010 | 2026-10-01～2026-10-08 | Ack 丢失/先到/错版本、乱序删除、Outbox 满和重启回归报告 |
| 联调 | D011 | D012 | 2026-10-09～2026-11-06 | 10-16 完成 Signal 基础交接，10-17～11-06 完成 C 持久消费、RF 通道、去重及删除优先级首轮联调 |

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
| Owner | 杨鹏通 |
| Contributor | 赵旭东（内容、投影恢复与故障验收协作） |
| 目标里程碑 | M3 · 2026-11-30（真实集成与生产就绪） |

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
| 各业务领域查询和补偿 | 原操作、Task、内容、投影、Signal 的真实状态及幂等入口 | Remember：杨鹏通 / 赵旭东 |
| 物理查询与清理 | 精确对象/版本、负查询、删除层次、残留与备份水位 | P2 E2：陈晔 / 杨文博；P2 E1：张晋 / 胡孝阳；Recall：陈凯 / 肖宇 |
| 失效消费 | A 缓存/历史 Context、C 预热/在途动作的处理范围证据 | Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁 |
| 状态/运行基础设施 | 备份恢复、租约/时间、Redis 健康、告警和恢复资源 | Shared Runtime：沈家隆 / 杨鹏通；Infra 联系人待确认 |
| 生产验收战役 | 真实 E1/E2/Redis 环境、迁移故障窗口、持续测试和证据归档 | IS/验收联系人待确认；P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文；Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁；Remember：杨鹏通 / 赵旭东 |

接口依赖：AL-B05、AL-P202、AL-P204、AL-C02、AL-C03、AL-RF02、AL-RF03、AL-RF05。能力与确认依据见 [Remember 主设计 §7](../分工与项目理解/分工与项目理解/remeber流程/运行时详细设计_V0.1/记忆形成流程详细设计_V0.1.md#alignment)；按 2026-09-18 核心契约基线、2026-11-06 首轮真实接口联调及后续 RC/验收窗口推进。

### 7. 验收标准

1. Task、内容写 unknown、投影积压和 Signal 未确认均可从可靠状态恢复；不重复主事实、不覆盖当前版本，未决项具有原因/Owner/下次动作。
2. 总次数和总时长受限，指数退避/jitter 生效；SDK 与任务重试无乘法放大，恢复流量受后台配额控制。
3. Redis 高延迟、连接和内存三类半故障分别验证进入、退出及恢复准入；恢复时无请求洪峰，错误/不可核验不假为空。
4. W1～W5 双 Owner 窗口按项目故障矩阵逐项注入；Working 与迁移并存的冻结 Profile 下端到端 P99 <10 ms，同时证明无主事实丢失、假成功和旧版本覆盖。
5. 删除级联覆盖全部获准表示及来源依赖；逻辑读屏障与物理完成分别记录；共享引用不误删，旧 cleanup 不删除新 generation。
6. 备份恢复先恢复删除/授权屏障；历史成功自然损坏创建新 Task，历史误报用追加审计纠正。
7. L1 领域、L2 Simulator、L3 真实环境结果分别留证；合同基线与 E1 集成持续验证分别归档。覆盖 RM-T03 / 04 / 08 / 10 / 11 / 16～24、BP2T-08～12、BCT-05 / 07～10。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。Owner：杨鹏通；Contributor：赵旭东；项目并行窗口见前文。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D005 | 2026-09-15～2026-09-18 | 核心契约 v0.1：统一扫描/重试矩阵、半故障阈值方案、删除恢复屏障、W1～W5 与持续验收计划 |
| 开发 | D006 | D015 | 2026-09-19～2026-10-09 | 对账调度、预算/退避/jitter、半故障准入、精确级联与审计恢复实现 |
| 测试 | D016 | D023 | 2026-10-10～2026-10-23 | 重试风暴、三类 Redis 半故障、删除竞态、备份恢复和双 Owner 故障报告准备 |
| 联调 | D024 | D028 | 2026-10-17～2026-11-06 | 完成首轮真实恢复联调、A/C/P2 联合恢复、迁移共存、删除屏障和恢复对账证据 |

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
| 2026-09-18 | W1 可靠提交、租约/Outbox、幂等/Retry Owner 和恢复 Profile | B + RF/Infra；AL-B01、AL-RF01/02/05 | 主事实、Task 和恢复可信边界 |
| 2026-09-18 | 分类/证据/衰减/保留规则，压缩质量与真实字节口径 | B + 产品/测试；AL-B02～05/07；PRR D-3 | 生命周期、压缩及策略验收 |
| 2026-09-18 | Passage/分块与 VEC-001、完整 manifest/五态/可见性样例 | A 提供，B 消费，P2 证明；AL-A01/02、AL-P203 | 投影真实 Ready |
| 2026-09-18 | OBJ-001 精确版本/durable/checksum、超时负查询及删除层次 | P2/Provider 提供，B 消费；AL-P201/202/204 | 内容完整性、未知与删除收敛 |
| 2026-09-18 | WorkingRead/MemoryRead/复核窗口、Signal 通道/持久 Ack/失效处理 | B 提供；A/C 消费确认；AL-B06、AL-A03、AL-C01～03 | 读交接与事件交接 |
| 2026-09-18 | 独立 Redis/资源、性能/容量/半故障阈值、72h 战役范围与环境 DRI | RF/Infra/IS/验收负责人；AL-RF03/05；PRR D-4 | 前台性能、迁移及持续验收 |
| 2026-09-18 | 是否批准 B 消费 A 使用观察及完整证据链 | RF/架构确认，A 提供；AL-RF04 | 未批准时保持该新消费路径关闭，字段 unknown |
| 2026-11-06 | 真实 E1/E2、A/C 服务及 Working/Prewarm 独立实例实际可联调 | 各 Provider + RF/Infra/IS，B 核验消费证据 | 2026-11-07 起进入 RC、验收与回归窗口 |

上述接口与资源清单记录本次计划的就绪条件；确认人、实际交付日期及契约版本仍在 Remember 主设计 §7 由对应 Owner 回填。运行阈值在冻结 Profile 中给出，本文不把未验证参数写成生产默认值。B 的交付是否完成，以对应范围的契约、实现/故障和真实集成证据为准。


# 第二章 Recall

# AetherStore P3 · Recall 负责人功能填报 V0.1

## Person A — Recall / Context Serving

**Primary Outcome**：从 Query 到最终 Context Pack 的完整读路径正确、可降级、可追踪；Recall 最终结果错误找 A。

按 A-EPIC-01～A-EPIC-10 组织为 **10 项功能**，每个 Epic 对应一个 Feature，统一填写一套 User Story、功能描述、流程、依赖、验收、计划和人天。Epic 名称、范围、PRR Action 与完成标准沿用分工说明。公司视图见[功能汇总表](AetherStore_P3功能汇总表_V0.1.md)。

## A 的最终 Epic 结构

| Epic ID | 最终 Epic | 功能名称 | 估算人天 | Epic 内独立计划 |
| --- | --- | --- | --- | --- |
| [A-EPIC-01](#a-epic-01--semantic-compute--resource-isolation) | Semantic Compute & Resource Isolation | 共享向量计算、资源隔离与模型发布 | 28 | D001～D028 |
| [A-EPIC-02](#a-epic-02--vector-projection-mechanism) | Vector Projection Mechanism | 向量投影写入、确认、恢复与删除 | 20 | D001～D020 |
| [A-EPIC-03](#a-epic-03--vector-backend-simulator) | Vector Backend Simulator | 向量后端模拟与故障验证 | 7 | D001～D007 |
| [A-EPIC-04](#a-epic-04--query-runtime--ingress-protection) | Query Runtime & Ingress Protection | 召回查询接入与流量保护 | 9 | D001～D009 |
| [A-EPIC-05](#a-epic-05--candidate-retrieval--search-degradation) | Candidate Retrieval & Search Degradation | 候选检索与搜索降级 | 8 | D001～D008 |
| [A-EPIC-06](#a-epic-06--candidate-validation--reference-integrity) | Candidate Validation & Reference Integrity | 候选有效性与引用完整性校验 | 8 | D001～D008 |
| [A-EPIC-07](#a-epic-07--canonical-load) | Canonical Load | 可信原文加载与预热回退 | 9 | D001～D009 |
| [A-EPIC-08](#a-epic-08--rank--decay--conflict-execution) | Rank / Decay / Conflict Execution | 排序、衰减与冲突规则执行 | 6 | D001～D006 |
| [A-EPIC-09](#a-epic-09--context-assembly) | Context Assembly | 上下文组装、预算控制与可靠交付 | 12 | D001～D012 |
| [A-EPIC-10](#a-epic-10--recall-observability-slo-audit--acceptance) | Recall Observability, SLO, Audit & Acceptance | 召回可观测性、SLO、审计与验收 | 29 | D001～D029 |

## 计划与人天口径

- **计划**：每个 Epic 窗口内含设计、开发、测试、联调四个阶段与额外缓冲日；2026-09-25～09-27 与 2026-10-01～10-08 暂不排开发；2026-11-23～11-27 集中联调，2026-11-28～11-30 为整体缓冲、复验与交付。
- **人天**：为 Recall 剩余交付工作量的初估，含设计、开发、测试、联调余量，不回算历史已投入；每个 Epic 仅一个工作量 Owner，协作者投入不重复累计。总量为 136 人天＝设计 23＋开发 55＋测试 33＋联调 25。
- **团队配置**：2 人（陈凯 / 肖宇）；两位责任人共同负责 Recall 的全部 Epic。Epic 内 D001 等仅表示该 Epic 的投入序号，不是项目自然日期；各 Epic 保留的当前牵头人不代表独立的人天池。
- **负责人**：陈凯 / 肖宇对 Recall 整体交付负责；B、Operator、P2、Shared Runtime 保留各自领域职责，外部团队实施投入和设备采购不包含在本表。
- 状态均为规划基线，不代表已签收。

### 责任人与并行关系

| 责任域 | 责任人 | 负责 Epic | 功能投入（人天） |
| --- | --- | --- | --- |
| Recall | 陈凯 / 肖宇 | A-EPIC-01～A-EPIC-10 | **136** |
| 合计 | 2 人 | 10 个 Epic | **136** |

- Embedding、Recall 入口和模拟器按统一契约分别开发，可以并行启动；真实 Query 联调接在基础向量能力之后。
- 投影、候选检索、资格校验和正文加载可使用模拟器及明确标识的契约样例并行开发；真实联调分别依赖 P2 和 Remember 的对应能力。
- 排序和 Context 可先按固定样例开发；完整链路验收在候选、资格、正文、最终复核与持久化能力具备后进行。
- Trace/事件、指标和审计工具从前期设计开始穿插开发；性能、模型切换和完整链路验收在各相关能力交付后汇合。
- 陈凯 / 肖宇共同推进全部 Epic，可按能力依赖在多个 Epic 间并行穿插；投入日不重复占用，个人不单独拆分人天。

### 项目交付窗口

Recall 项目窗口为 2026-09-09～2026-11-30；统一里程碑见文档开头。各 Epic 按项目日期推进，2026-09-25～09-27 与 2026-10-01～10-08 不安排开发；2026-11-23～11-27 进行真实接口、性能与故障联调，2026-11-28～11-30 完成缓冲、复验与交付。

### 阶段人天汇总

| 阶段 | 人天 | 说明 |
| --- | ---: | --- |
| 设计 | 23 | Embedding、Query、检索、校验、正文加载和 Context 设计 |
| 开发 | 55 | Recall 入口、向量投影、检索、排序和 Context 能力实现 |
| 测试 | 33 | 降级、版本、引用完整性、预算、性能和故障验证 |
| 联调 | 25 | 与 B、C、P2、Shared Runtime 及实际调用方的接口验证 |
| **合计** | **136** | 与本组 Epic 合计一致 |

### 跨组依赖联系人

| 依赖类别 | 具体依赖 | 对接联系人 | 最迟形成时间 |
|---|---|---|---|
| 记忆事实与资格 | MemoryRecord、MemoryRead、ContentBinding、ProjectionState、版本和删除屏障 | 杨鹏通 / 赵旭东（Remember） | 2026-09-18 |
| 向量 Provider | Collection、写入、查询、删除、状态和版本/维度事实 | 张晋 / 胡孝阳（P2 E1）；陈凯 / 赵旭东 / 谭旭梁（External Integration） | 2026-10-16 |
| 正文 Provider | Canonical Content 的 get/head/range、checksum、durable 和操作查询 | 陈晔 / 杨文博（P2 E2） | 2026-10-16 |
| 调度与热副本 | Prewarm/Promote 的目标、动作、Placement 和执行反馈 | 沈家隆 / 谭旭梁（Operator） | 2026-11-13 |
| 共享运行时 | RequestContext、状态、任务、投递、审计和 Trace 关联 | 沈家隆 / 杨鹏通（Shared Runtime） | 2026-09-18 |
| 环境搭建与验收 | 真实 Redis、向量/正文 Provider、调用方和 E2E 验收窗口 | 陈凯 / 肖宇 | 2026-11-23 |

## 文档依据

- 公司结构与工作范围：[Epic 分工](AetherStore_P3_Owner_Epics_Merged_V1.0.md)、[原始 WBS](AetherStore_P3_Engineering_WBS_V1.0.md)、[A 工作包](PROGRAM_A_RECALL_WORK_PACKAGE.md)、[A 生产就绪补充](../../../PPR/PROGRAM_A_Production_Readiness_Addendum_V1.0.md)。
- 填写格式：[功能填报模板](AetherStore_P3_功能填报模板_V1.0.md)、[汇总表模板](AetherStore_P3_功能汇总表模板_V1.0.md)。
- 流程、字段与接口：[运行时主设计](运行时详细设计_V0.1/召回流程详细设计_V0.1.md)、[数据定义](运行时详细设计_V0.1/召回数据定义_V0.1.md)、[Recall 总流程](AetherStore_P3_Recall_Overall_Flow_V1.0.md)、[P2 对接](运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md)、[B 对接](运行时详细设计_V0.1/召回与记忆形成接口对接需求_V0.1.md)、[C 对接](运行时详细设计_V0.1/召回与调度优化接口对接需求_V0.1.md)。
- 外部能力和接入条件：[跨模块接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。正文中的 AL 编号沿用此清单。

---
## A-EPIC-01 · Semantic Compute & Resource Isolation

**Epic 范围**：passage/query 两类 Embedding；model contract/version/dimension；真实向量；吞吐与资源隔离；模型变更安全。

**关联 PRR Action**：A-P0-2、A-P0-3、A-P0-5。

**Epic 级完成标准**：passage/query 语义清晰；query 不被 passage/后台抢占；隔离态与混合负载 Profile 均满足吞吐目标；模型/维度变化可 Shadow/Canary/Rollback。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-01 |
| Epic Name | Semantic Compute & Resource Isolation |
| Feature ID | A-FEAT-01-01 |
| Feature Name | 共享向量计算、资源隔离与模型发布 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东；Shared Runtime / 验收负责人 |
| 目标里程碑 | M1 |

### 2. User Story

作为 Remember 模块、Recall Runtime 和模型服务维护者，我希望获得兼容、可追踪且有资源保障的正文/查询向量服务，并能安全升级和回退模型，从而支撑记忆写入与在线检索，控制混合负载及模型变更风险。

### 3. 功能描述

#### 3.1 功能目标

统一提供 Passage/Query 真实向量生成、模型与检索空间绑定、完成结果复用、资源隔离、性能验证，以及 Shadow/Canary/Rollback 受控发布能力。

#### 3.2 核心输入

- SemanticEmbeddingRequest：文本或受控片段、usage、source_hash、模型契约、租户/授权和 deadline。
- B 提供 Passage 的片段及版本依据；Recall 提供 Query。
- Query/Passage/后台负载、模型版本、硬件、batch、并发和队列配置。
- 前台延迟与错误率观察、已签收 Acceptance Profile。
- 当前和候选模型制品、模型/预处理版本、维度、检索空间绑定。
- 评测样本、发布条件、回退条件和 B 的投影可读性事实。

#### 3.3 核心处理

1. 校验输入、授权和摘要，固定模型、预处理、维度与检索空间。
2. 分配独立队列、并发和执行资源，落实 Query 高于 Passage、高于后台的优先级。
3. 按租户、用途和完整输入绑定查询已完成结果；未命中则执行真实推理。
4. 验证向量维度、有限数值及输入对应关系，保存执行次数、可信结果和摘要。
5. 前台恶化时限制后台准入或暂停相应任务，并记录触发和恢复依据。
6. 在固定模型/设备/数据集下运行两套 Profile，测有效吞吐、P95/P99、错误率及隔离效果。
7. 校验候选模型与目标空间，固定发布版本和请求绑定。
8. Shadow 比较新旧模型，再按获准范围 Canary。
9. 监测兼容性和质量/性能，异常回退至已验证兼容的稳定绑定，保存全过程证据。

#### 3.4 核心输出

- SemanticEmbeddingResult：受控向量及摘要、model_version、dimension、用途和输入绑定。
- SemanticEmbeddingExecution：执行状态、尝试次数与可恢复结果引用。
- 经验证的资源配额和节流配置。
- 两套 Benchmark 报告、容量边界和运行配置记录。
- 版本化发布配置、评测对比和灰度记录。
- 回退结果及交给端到端验收的发布演练证据。

### 4. 正常流程

接收正文或查询请求 → 校验用途、授权和模型/空间 → 按用途分配资源 → 复用完成结果或真实推理 → 校验并保存向量 → 返回调用方。容量验证执行隔离态和混合负载态两套 Profile；模型变更依次执行兼容性检查、Shadow、Canary 和发布/回退，并留存证据。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 输入超长、摘要错误或模型不兼容 | 推理前拒绝并给出稳定原因 | 不产生成功向量 |
| 推理超时或进程重启 | 共享执行层按原 deadline 和额度恢复；调用方重放附着原执行 | 不增加隐式重试额度 |
| 批内单条失败或向量非法 | 逐项绑定并拒绝非法结果 | 不以零向量或其他条目冒充成功 |
| 共享设备无法证明隔离 | 调整部署或降低 Passage 准入 | 不宣称已实现前台保障 |
| 前台延迟越线 | 节流后台并观察恢复 | 有明确容量保护证据 |
| 任一 Profile 不达标 | 记录瓶颈并回到调优 | 性能验收不通过 |
| 模型或维度不兼容 | 阻止查询与投影跨空间混用 | 不进入真实切换 |
| 灰度质量或时延不达标 | 停止扩大并执行回退 | 恢复获准的稳定绑定 |
| 稳定模型对应投影不可读 | 保留故障及可读性事实 | 不将配置回退冒充服务恢复 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| B / Recall | 明确用途、片段和模型空间的消费绑定 | Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇 |
| 模型运行环境 | 获准模型、运行资源和版本制品 | Recall：陈凯 / 肖宇；Remember：杨鹏通 / 赵旭东 |
| 可靠执行存储 | 执行绑定、租约和结果一致保存 | Shared Runtime：沈家隆 / 杨鹏通 |
| 共享 Embedding | 真实推理及基础用途区分 | Recall：陈凯 / 肖宇 |
| 容量测试调用器 | 生成查询负载并采集前台阶段时延，完整 Recall 联验纳入 A-EPIC-10 | Recall：陈凯 / 肖宇 |
| 性能验收环境 | 设备、数据集、指标口径和负载基线 | 验收负责人联系人待确认；Shared Runtime：沈家隆 / 杨鹏通 |
| 共享 Embedding | 请求模型固定、版本与结果追踪 | Recall：陈凯 / 肖宇 |
| Memory / Projection 事实 | 新旧模型的投影编排、当前可读性与切换依据 | Remember：杨鹏通 / 赵旭东 |
| 评测与发布环境 | 质量/时延基线、发布配置、运行观察 | Recall：陈凯 / 肖宇；Shared Runtime：沈家隆 / 杨鹏通；验收负责人联系人待确认 |

接口依赖：AL-B05、AL-B08、AL-RF05、AL-RF04、AL-PM01。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. Query 与 Passage 均输出来自真实模型的合法向量，模型/维度/来源可追踪。
2. 同输入合法重放可复用完成结果；不同租户、usage、模型或片段绑定不误复用。
3. 单条异常不造成批次结果错配，崩溃恢复不重置推理额度。
4. Passage 结果交回 B，由 B 决定写入；Query 不自动写入长期索引。
5. 隔离态和混合负载态均按冻结口径验证有效吞吐 ≥2000 item/s。
6. 记录模型、维度、设备、batch、并发、warm-up、P95/P99 和错误率。
7. 混合负载中 Query 保留资源不被 Passage 或后台占满。
8. 前台恶化能够触发后台节流，恢复行为可复现；正式时延阈值按签收 Profile 判断。
9. Shadow、Canary、Rollback 均有可执行步骤和演练结果。
10. 在途请求沿用已固定模型，不因配置更新混用维度和空间。
11. 兼容性不满足时阻止切换；回退后校验实际可用链路。
12. 发布记录能够关联模型、空间、指标、原因与 trace。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：陈凯。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D005 | 2026-09-09～2026-09-15 | 模型/输入绑定与复用设计；容量与双 Profile 测试方案；发布与回退方案及判定条件 |
| 开发 | D006 | D017 | 2026-09-16～2026-09-30 | 推理服务、结果校验及恢复实现；资源隔离、优先级及节流实现；版本绑定和受控切换实现 |
| 测试 | D018 | D024 | 2026-10-09～2026-10-23 | 真实向量、隔离复用及崩溃用例；混合负载测试和调优证据；不兼容拦截及回退测试 |
| 联调 | D025 | D028 | 2026-10-26～2026-11-06 | B Passage 与 Recall Query 调用记录；正式环境双 Profile Benchmark；与 B 联调的发布演练记录 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 5 |
| 开发 | 12 |
| 测试 | 7 |
| 联调 | 4 |
| **总人天** | **28** |

### 10. 备注

- WBS 对应：A-EPIC-01-T1、A-EPIC-01-T2、A-EPIC-01-T3、A-EPIC-01-T4。
- PRR Action：A-P0-2、A-P0-3、A-P0-5。
- 设计依据：主设计第 2.1～2.3、2.8、5.4 节；数据定义第 19、20、25 章；B 对接说明。；Epic A-EPIC-01；A 生产就绪补充；主设计第 2.3 节及参数目录。；Epic A-EPIC-01；A 生产就绪补充 A-P0-5；主设计第 2.2、3.2 节。
- 工作量及责任边界：包含原共享推理、资源保障和模型发布三部分的完整工作量。B 负责投影领域编排与切换，A 不代写 ProjectionState。专项性能与模型发布演练在本项实现；A-EPIC-10 汇集证据并验证完整 Recall 链路，不重复计入这些实现。

---
## A-EPIC-02 · Vector Projection Mechanism

**Epic 范围**：VectorProjectionPort；五元组幂等；ProviderResult 五态；写入/query/delete/version/schema。

**关联 PRR Action**：—。

**Epic 级完成标准**：ProviderResult=ACCEPTED/PENDING/READY/FAILED/UNKNOWN 均有明确语义；A 不写 ProjectionState；版本/维度错误不误报 Ready。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-02 |
| Epic Name | Vector Projection Mechanism |
| Feature ID | A-FEAT-02-01 |
| Feature Name | 向量投影写入、确认、恢复与删除 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东；P2 / Shared Runtime |
| 目标里程碑 | M1 |

### 2. User Story

作为 Remember 投影构建与维护模块，我希望将获准向量写入存储、查询真实完成状态，并在超时、版本更新和删除时安全收敛，从而为领域 Ready 判定提供可信证据，避免重复写入、旧版本覆盖和删除后复活。

### 3. 功能描述

#### 3.1 功能目标

统一提供 VectorProjectionPort 契约、五元组幂等、ProviderResult 五态处理、写入及操作/目标查询、有界恢复、版本更新与准确目标删除。

#### 3.2 核心输入

- B 批准的 VectorProjectionRequest：准确五元组、目标空间、Passage 向量、内容引用、元数据、授权。
- 稳定幂等身份和载荷摘要；P2 原始操作与对象证据。
- 原 VectorProjectionOperation、调用意图、尝试额度和已有证据。
- B 发起的合法新版本目标或准确删除授权；P2 操作、目标和屏障事实。

#### 3.3 核心处理

1. 固定目标、幂等键和请求指纹，可靠保存操作及调用意图。
2. 调用 upsert，按需 query_operation/get_projection 核验准确目标、载荷和索引状态。
3. 保留原始证据，形成 ACCEPTED/PENDING/READY/FAILED/UNKNOWN，交由 B 执行 Ready Guard。
4. 按原身份恢复 PENDING/UNKNOWN，先查证，满足无迟到副作用等保证才允许预算内安全重发。
5. 真实版本变化写新目标，把结果交回 B 决定领域切换。
6. 删除前可靠保存退役标记，禁止旧写重发；核验对象退出索引以及在途旧写不会复活。

#### 3.4 核心输出

- VectorProjectionOperation、ProjectionTargetBinding 和可追踪操作引用。
- ProviderResult：机制状态、准确身份、证据与原因。
- 恢复后的操作及 ProviderResult 观察版本。
- 删除完成证据，或带原因和证据的 attention_required。

### 4. 正常流程

B 提交准确目标及获准向量 → 固定五元组和幂等绑定 → 可靠保存调用意图 → P2 写入 → 查询原操作并核验目标/索引 → 返回 ProviderResult 供 B 执行 Ready Guard。超时或重启按原身份恢复；版本变化写入新目标；删除先建立退役约束，再删除并验证不会复活。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 同幂等身份载荷变化 | 拒绝请求并保留原操作 | 不覆盖原向量 |
| P2 已受理但索引未就绪 | 返回 ACCEPTED 或有证据的 PENDING | 不提前 READY |
| 回包丢失或完成证据不全 | 记录 UNKNOWN 或有依据的 PENDING | 转恢复功能继续核验 |
| 查询 not_found 或写入超时 | 保留未知并继续预算内查证 | 不据此盲目重写 |
| 恢复窗口耗尽 | 保留 PENDING/UNKNOWN 并标识 attention_required | 不虚报成功或永久失败 |
| 同五元组 rebuild 请求 | 在 P2 调用前拒绝 | 保持当前版本不支持的明确边界 |
| 删除 Ack 缺少防复活证据 | 继续核验或保留未确定状态 | 不报告安全删除完成 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| Passage Embedding | 可信向量及模型/输入绑定 | Recall：陈凯 / 肖宇 |
| 投影领域编排 | 目标、五元组、写入授权与 Ready Guard | Remember：杨鹏通 / 赵旭东 |
| VEC-001 | upsert、query_operation、get_projection 及完成证据 | P2 E1：张晋 / 胡孝阳 |
| 持久化机制 | 幂等、调用意图和结果保存 | Shared Runtime：沈家隆 / 杨鹏通 |
| 投影写查机制 | 准确绑定和 ProviderResult | Recall：陈凯 / 肖宇 |
| 领域决策 | 版本变更、清理时机与删除授权 | Remember：杨鹏通 / 赵旭东 |
| 操作恢复与屏障 | 按原键查询、精确删除、迟到写隔离 | P2 E1：张晋 / 胡孝阳；P2 E2：陈晔 / 杨文博 |
| 恢复持久化 | 租约、次数、退役和未决操作保留 | Shared Runtime：沈家隆 / 杨鹏通 |

接口依赖：AL-B08、AL-P205～208、AL-RF05、AL-P206、AL-P207。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 五元组、模型、维度、Schema、向量及元数据绑定均校验。
2. 同键同载荷重复请求复用原操作，同键冲突明确拒绝。
3. ProviderResult 五态可区分，缺索引/目标证据不返回 READY。
4. B 能消费机制结果且 A 不修改 ProjectionState；模拟与真实 P2 证据分别保存。
5. 重启保持同一操作及已消耗额度，UNKNOWN 不触发无证据重写。
6. 旧版本迟到 READY 不修改新版本目标，也不直接改变 B 当前可读性。
7. 删除精确作用于授权目标；完成证据包含不可查询及不会复活。
8. 同版本 rebuild 拒绝、重复删除、未知恢复和崩溃场景均可复现。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：陈凯。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D004 | 2026-09-09～2026-09-12 | 投影契约、幂等绑定与五态映射；恢复、更新及删除状态规则 |
| 开发 | D005 | D012 | 2026-09-14～2026-09-24 | 写入、查询、准确对象核验实现；恢复协调器、版本更新和删除屏障消费 |
| 测试 | D013 | D016 | 2026-09-28～2026-09-29 | 幂等/冲突/索引未就绪契约用例；超时重启/迟到写/重复删除用例 |
| 联调 | D017 | D020 | 2026-09-30～2026-10-12 | B Ready Guard 与 P2 写查联调；B 与 P2 故障恢复及删除联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 4 |
| 开发 | 8 |
| 测试 | 4 |
| 联调 | 4 |
| **总人天** | **20** |

### 10. 备注

- WBS 对应：A-EPIC-02-T1、A-EPIC-02-T2、A-EPIC-02-T3。
- PRR Action：—。
- 设计依据：主设计第 2.4～2.6、2.8 节；数据定义第 21～24 章；P2 对接 RP2-06～08。；主设计第 2.7 节；数据定义第 21～24 章；P2 对接 RP2-07～09。
- 工作量及责任边界：正常写查、五态处理和恢复/更新/删除统一核算，共享契约和状态处理不重复计费。A 只提供机制结果，B 唯一拥有 ProjectionState。同五元组 rebuild 按现有规则拒绝，不包含新增代际重建能力。

---
## A-EPIC-03 · Vector Backend Simulator

**Epic 范围**：有状态向量模拟；index_ready；幂等；版本/维度；超时/失败/stale/partial/unknown 故障注入。

**关联 PRR Action**：—。

**Epic 级完成标准**：可独立验证 VectorProjection / Search 契约与失败链；不依赖真实 P2；不实现真实 ANN。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-03 |
| Epic Name | Vector Backend Simulator |
| Feature ID | A-FEAT-03-01 |
| Feature Name | 向量后端模拟与故障验证 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东 |
| 目标里程碑 | M1 |

### 2. User Story

作为 Recall 与 Remember 开发者，我希望在真实 P2 尚未就绪时验证向量写入、查询和故障恢复契约，从而提前开发并发现接口和状态处理问题。

### 3. 功能描述

#### 3.1 功能目标

提供有状态 Vector Backend Simulator，支撑向量写读契约与可重现故障验证。

#### 3.2 核心输入

- 投影、搜索契约请求和已配置的模拟向量/版本/索引状态。
- 正常模式或显式故障注入配置。

#### 3.3 核心处理

1. 维护目标、幂等、模型/维度与 index_ready 等模拟状态。
2. 返回与状态一致的写入、查询、删除和预置候选结果。
3. 按注入配置模拟 timeout、failed、stale、partial、unknown 和回包丢失。

#### 3.4 核心输出

- 模拟 Provider 响应与可检查的状态快照。
- 可重复执行的契约场景和故障证据。

### 4. 正常流程

配置契约场景 → 初始化状态 → 执行消费方请求 → 正常响应或注入故障 → 检查状态与结果 → 输出测试证据。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 重复写入 | 按同目标同载荷幂等处理 | 模拟状态不重复增长 |
| 版本或维度不符 | 显式返回契约错误 | 不产生虚假就绪 |
| 故障模式启用 | 标注注入类型及配置 | 不会把模拟证据当成真实后端事实 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 投影和搜索契约 | 方法、状态及输入输出基线 | Recall：陈凯 / 肖宇 |
| 联调使用方 | 契约场景、故障断言与测试调用 | Recall：陈凯 / 肖宇；Remember：杨鹏通 / 赵旭东 |

接口依赖：AL-P202、AL-P205～208。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 同一输入配置能够重现相同正常或故障过程。
2. 幂等、索引未就绪、版本/维度错误和五态行为覆盖。
3. 正常模式响应与模拟状态一致；故障模式明确可辨。
4. 模拟契约通过与真实 P2 联调通过分别记录，不实现或声称具备真实 ANN。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：肖宇。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D001 | 2026-09-09 | 模拟状态与故障目录 |
| 开发 | D002 | D004 | 2026-09-10～2026-09-14 | 有状态模拟器及注入开关 |
| 测试 | D005 | D006 | 2026-09-15～2026-09-16 | 正常一致性和故障场景测试 |
| 联调 | D007 | D007 | 2026-09-17～2026-09-18 | A/B 使用模拟器的契约验收记录 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 1 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 1 |
| **总人天** | **7** |

### 10. 备注

- WBS 对应：A-EPIC-03-T1、A-EPIC-03-T2。
- PRR Action：—。
- 设计依据：Epic A-EPIC-03；WBS A-EPIC-03；P2 对接联调场景。
- 工作量及责任边界：提供有状态模拟器和显式故障注入，不实现真实 ANN。消费方业务处理在各自 Epic 内实现；模拟与真实 P2 验收分别记录。

---
## A-EPIC-04 · Query Runtime & Ingress Protection

**Epic 范围**：Query→Query Embedding；契约校验；入口限流；Query Embedding 队列上限；Deadline。

**关联 PRR Action**：A-P0-1。

**Epic 级完成标准**：突发流量下有明确 admission；429/Busy 语义稳定；队列有上限；限流时前台 P99 不失控。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-04 |
| Epic Name | Query Runtime & Ingress Protection |
| Feature ID | A-FEAT-04-01 |
| Feature Name | 召回查询接入与流量保护 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东；P4 / Shared Runtime |
| 目标里程碑 | M2 |

### 2. User Story

作为 Agent 调用方，我希望提交问题、授权范围和预算后获得自动选路、兼容 Query 向量和有界准入的召回服务，从而简化调用并避免突发流量压垮在线链路。

### 3. 功能描述

#### 3.1 功能目标

完成 Recall 入口、内部来源选择、Query 调用、幂等执行和有界准入。

#### 3.2 核心输入

- 问题、可信身份/Scope、session/task、筛选约束、token 预算、deadline、幂等键。
- 获准模型/空间配置及入口容量配置；上游不传 retrieval_mode。

#### 3.3 核心处理

1. 校验授权及请求，按 scope_union_v1 固定 working_only、long_term_only 或 combined。
2. 建立 RecallRequestIndex/RecallExecution 和阶段检查点，处理同键重放及冲突。
3. 执行入口限流和有界排队；长期分支调用共享 Query Embedding 并校验检索空间。

#### 3.4 核心输出

- RecallRequest、SourceSelection、RecallExecution 和 QueryEmbeddingResult（适用时）。
- 稳定准入拒绝、Busy/429 或协议错误，以及向后续阶段交接的执行绑定。

### 4. 正常流程

收到请求 → 授权/参数/幂等校验 → 固定来源模式 → 有界准入 → 按需 Query 向量化 → 交给候选发现。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 越权、非法预算或同键不同请求 | 入口拒绝 | 不开始检索 |
| 队列满或容量不足 | 稳定 Busy/429 映射 | 排队有上限 |
| Query 向量不兼容或失败 | 保留原因，按固定模式的失败规则处理 | combined 可在剩余时间内继续 Working，不重选模式 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 网关与调用方 | 身份、请求、预算和状态转交 | P4 / Auth 联系人待确认；实际调用方联系人待确认 |
| 共享基础 | 授权、CAS/租约、执行持久化及资源准入 | Shared Runtime：沈家隆 / 杨鹏通 |
| Query Embedding | 真实向量与兼容空间 | Recall：陈凯 / 肖宇 |

接口依赖：AL-RF01、AL-RF02、AL-RF04、AL-P401、AL-B05、AL-PM01。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 三种来源模式有确定选择依据，受理后故障不改变已固定模式。
2. working_only 不调用 Query Embedding；长期分支校验模型/维度/空间。
3. 同键重放不新建执行，向量传输重试不引发额外推理额度。
4. 突发流量下队列有上限、429/Busy 稳定，前台 P99 按冻结 Profile 验证。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：陈凯。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D002 | 2026-09-09～2026-09-12 | 入口、模式和流量保护设计 |
| 开发 | D003 | D006 | 2026-09-14～2026-09-24 | 请求执行、Query 接入和限流实现 |
| 测试 | D007 | D008 | 2026-09-28～2026-09-29 | 模式/幂等/突发流量测试 |
| 联调 | D009 | D009 | 2026-09-30 | 网关、RF 与共享 Query 调用联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 2 |
| 开发 | 4 |
| 测试 | 2 |
| 联调 | 1 |
| **总人天** | **9** |

### 10. 备注

- WBS 对应：A-EPIC-04-T1、A-EPIC-04-T2。
- PRR Action：A-P0-1。
- 设计依据：主设计第 3.1、3.2 节；数据定义第 1～5 章；A 生产就绪补充。
- 工作量及责任边界：负责入口、来源模式、执行骨架、Query 调用及入口保护。共享推理/隔离归 A-EPIC-01，候选发现归 A-EPIC-05，最终交付与结果重放归 A-EPIC-09。

---
## A-EPIC-05 · Candidate Retrieval & Search Degradation

**Epic 范围**：VectorSearchPort；TopK；filter；稳定引用；partial/degraded；Search 后端半故障/不可用降级。

**关联 PRR Action**：A-P1-3。

**Epic 级完成标准**：Search 超时/不可用不冒充完整；降级阈值明确；结果携带稳定引用和版本；可输出 recall_availability。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-05 |
| Epic Name | Candidate Retrieval & Search Degradation |
| Feature ID | A-FEAT-05-01 |
| Feature Name | 候选检索与搜索降级 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东；P2 |
| 目标里程碑 | M2 |

### 2. User Story

作为 Recall 调用方，我希望按查询条件获得当前 Working 和相关长期记忆候选，并明确获知来源缺失及部分故障，从而正确判断本次检索是否完整。

### 3. 功能描述

#### 3.1 功能目标

按既定模式读取 Working 和长期候选，统一记录来源覆盖、稳定引用和搜索故障。

#### 3.2 核心输入

- 固定来源模式、Query 向量（适用时）、Scope、filter、TopK 和剩余 deadline。
- B Working 结果与 P2 search 返回的候选及完成性证据。

#### 3.3 核心处理

1. working_only 读 B；long_term_only 查 P2；combined 按阶段规则读取两来源。
2. 保留候选 ID、稳定引用、版本、score/rank 及来源证据。
3. 形成两来源 SourceReadResult，显式处理部分结果、超时、半故障和缺失。

#### 3.4 核心输出

- VectorCandidateSet、SourceReadResult 与交给资格校验的候选。
- 来源覆盖和缺失原因，供最终 recall_availability 判定。

### 4. 正常流程

读取固定模式 → 调用 B Working / P2 search → 校验来源响应 → 保存候选和来源完整性 → 交给资格校验。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 搜索超时或后端半故障 | 采用可信部分并保留缺失原因 | 不宣称来源完整 |
| 确认完整但无候选 | 保存正常空来源事实 | 由后续汇总判断正常空 |
| 引用/版本缺失或响应不可信 | 隔离受影响候选并记录缺口 | 不把脏候选作为成功搜索内容 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| Query 入口 | 固定模式与兼容 Query 向量 | Recall：陈凯 / 肖宇 |
| Working 读取 | 当前 session/task 的有效 Working 候选与完整性 | Remember：杨鹏通 / 赵旭东 |
| VEC-002 | 带 Scope/filter/TopK 的检索与稳定引用 | P2 E1：张晋 / 胡孝阳 |

接口依赖：AL-B01、AL-P201、AL-P202、AL-RF04、AL-PM01。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. Working 是独立来源，Prewarm 不作为第三个搜索来源。
2. TopK、过滤和租户范围按契约生效；候选版本和引用可追踪。
3. 完整空、partial、timeout、failed 有不同事实记录，零条不直接当成功空。
4. 降级阈值和恢复行为形成配置及故障验证；未签收数值不宣称生产达标。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：陈凯。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D001 | 2026-09-09 | 来源契约和搜索降级规则 |
| 开发 | D002 | D004 | 2026-09-10～2026-09-14 | Working/向量发现适配及结果归一 |
| 测试 | D005 | D006 | 2026-09-15～2026-09-16 | 完整空/partial/超时/半故障测试 |
| 联调 | D007 | D008 | 2026-09-17～2026-09-18 | B Working 与真实 P2 搜索联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 1 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 2 |
| **总人天** | **8** |

### 10. 备注

- WBS 对应：A-EPIC-05-T1、A-EPIC-05-T2、A-EPIC-05-T3。
- PRR Action：A-P1-3。
- 设计依据：主设计第 3.3、4 章；数据定义第 6、7 章；P2 RP2-01；B Working 对接。
- 工作量及责任边界：负责 Working/长期候选发现及来源事实归一，Prewarm 不作为第三个搜索来源。候选资格、正文读取和统一终态判定分别归后续 Epic。

---
## A-EPIC-06 · Candidate Validation & Reference Integrity

**Epic 范围**：调用 B 的 MemoryReadCapability；校验 Memory state/version/scope/conflict/evidence；悬空引用检测。

**关联 PRR Action**：A-P1-2。

**Epic 级完成标准**：只使用当前有效版本；object_ref / graph_node_ref 等引用悬空可识别并显式处理；不因脏候选产生完整结果。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-06 |
| Epic Name | Candidate Validation & Reference Integrity |
| Feature ID | A-FEAT-06-01 |
| Feature Name | 候选有效性与引用完整性校验 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东 |
| 目标里程碑 | M2 |

### 2. User Story

作为 Recall 调用方，我希望只有当前版本、权限及状态有效且引用可解析的候选进入后续流程，从而避免过期、已删除、越权或悬空引用导致错误上下文。

### 3. 功能描述

#### 3.1 功能目标

消费 B 的权威 MemoryRead 事实校验候选，并提供可供最终复核复用的校验适配。

#### 3.2 核心输入

- 候选 memory_id、memory_version、表示/对象引用及来源。
- B 返回的权限、状态、当前版本、Working TTL、投影可读性和语义证据。

#### 3.3 核心处理

1. 按准确身份向 B 核验 state/version/scope/conflict/evidence 等事实。
2. 区分 Working 当前性与长期 Projection 可读性，不向 Working 强加长期索引条件。
3. 检测 object_ref/graph_node_ref 悬空，保存通过、权威排除和未知原因。

#### 3.4 核心输出

- CandidateValidationResult 及可信候选资格快照。
- 排除依据、悬空引用和未确定事实，供加载与终态处理。

### 4. 正常流程

收到候选 → B 权威核验 → 检查当前版本/授权/适用可读性 → 识别悬空引用 → 保存结果 → 交正文加载。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 删除、过期或旧版本被权威确认 | 排除并保存依据 | 不进入正文采用集合 |
| 权限/状态无法确认 | 隔离并记录缺失 | 不假定候选有效 |
| 对象或图引用悬空 | 记录可定位的引用问题 | 不以高相似度绕过校验 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 候选发现 | 稳定身份和来源证据 | Recall：陈凯 / 肖宇 |
| MemoryRead | 当前权威事实、批量校验与有效窗口 | Remember：杨鹏通 / 赵旭东 |
| 引用事实 | 准确对象/映射与版本依据 | Remember：杨鹏通 / 赵旭东；对应 Provider 联系人待确认 |

接口依赖：AL-B01、AL-B03、AL-B05、AL-P201。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 无效生命周期、旧版本、越权和未知权限候选均不能被采用。
2. Working TTL 与长期 Projection 可读性分别校验。
3. 悬空引用可定位到 memory/representation/版本和 trace。
4. 保留最终复核所需逐项身份及证据；不重新定义 B 的领域过滤规则。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：肖宇。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D001 | 2026-09-09 | 资格与引用校验规则 |
| 开发 | D002 | D004 | 2026-09-10～2026-09-14 | MemoryRead 消费适配与结果记录 |
| 测试 | D005 | D006 | 2026-09-15～2026-09-16 | 失效/越权/未知/悬空引用测试 |
| 联调 | D007 | D008 | 2026-09-17～2026-09-18 | B 批量核验及有效窗口联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 1 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 2 |
| **总人天** | **8** |

### 10. 备注

- WBS 对应：A-EPIC-06-T1。
- PRR Action：A-P1-2。
- 设计依据：主设计第 3.4、3.7 节；数据定义第 8 章；B 对接说明。
- 工作量及责任边界：B 定义并提供 Memory 权威事实，A 消费这些事实完成候选校验。首次校验和复用适配在本项实现；返回前的最终复核编排与 Pack 重组归 A-EPIC-09。

---
## A-EPIC-07 · Canonical Load

**Epic 范围**：经 B 的 mapping + ContentStorePort 读取原文；checksum/not_found/partial。

**关联 PRR Action**：—。

**Epic 级完成标准**：canonical payload 加载失败可定位；checksum/not_found/partial 不被静默吞掉；A 不拥有物理 payload 事实。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-07 |
| Epic Name | Canonical Load |
| Feature ID | A-FEAT-07-01 |
| Feature Name | 可信原文加载与预热回退 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东；P2 / Operator / Shared Runtime |
| 目标里程碑 | M2 |

### 2. User Story

作为 Recall 调用方，我希望读取与获准 Memory 版本及范围一致的完整正文，并在可选热副本不可用时回退至正式内容，从而获得可信内容和可定位的加载结果。

### 3. 功能描述

#### 3.1 功能目标

通过 B 正式映射加载 Working 内联内容或 canonical 原文，校验完整性并控制读取资源。

#### 3.2 核心输入

- 已通过资格校验的候选、B 正式 content_ref/版本/范围/授权/expected_hash。
- 读取次数/字节预算、deadline 和可选热副本证据。

#### 3.3 核心处理

1. 校验已有内联字节；需物理读取时按需补 head 元数据。
2. 调用前预占次数和字节；仅在可保障正式回退时尝试可选 Prewarm。
3. 按准确版本/范围读取正式正文，核对编码、hash 和实际来源，结算或保守保留额度。

#### 3.4 核心输出

- ContentLoadResult 与 RecallCandidate 可信正文快照。
- 读取账本、checksum/范围校验结果及真实加载来源。

### 4. 正常流程

取 B 内容映射 → 校验内联正文或准备读取 → 预占额度 → 可选 Prewarm → 必要时 canonical get/get_range → 校验/结算 → 保存可信正文。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 缓存 miss、失效或释放竞态 | 在预算内由唯一责任方执行正式回退 | 回退成功不因缓存故障自动降级 |
| checksum 不符、not_found 或断流 | 拒绝采用不可信正文，保留原因 | 不把 partial 字节当完整内容 |
| 重启后接收字节未知 | 预算保守按预留扣费，统计保持未知 | 不重置额度或伪造真实流量 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 资格结果与内容映射 | 当前有效候选及准确内容授权/摘要 | Recall：陈凯 / 肖宇；Remember：杨鹏通 / 赵旭东 |
| OBJ-001 | get/get_range、按需 head、条件版本与字节上限 | P2 E2：陈晔 / 杨文博；Durable Provider 联系人待确认 |
| 可选热副本 | 获准表示、释放行为和真实来源 | Operator：沈家隆 / 谭旭梁；Remember：杨鹏通 / 赵旭东；Provider 联系人待确认 |
| 资源与账本 | 原子预占、并发及取消支持 | Shared Runtime：沈家隆 / 杨鹏通 |

接口依赖：AL-B02、AL-P203、AL-P204、AL-C01、AL-C04、AL-RF04。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 跨版本、范围、编码或 checksum 不符的内容不被采用。
2. Working 内联正文同样验证；Working 与 Prewarm 不混用。
3. 预热失败后可信正式回退不自动降级，Provider 已内部回退时不重复回退。
4. 读取预算在并发、断流和崩溃下仍有界，真实 bytes 未知不填为零。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：陈凯。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D002 | 2026-09-09～2026-09-12 | 映射、校验、读取账本与回退设计 |
| 开发 | D003 | D005 | 2026-09-14～2026-09-24 | 正文适配器、完整性校验和可选热读 |
| 测试 | D006 | D007 | 2026-09-28～2026-09-29 | 损坏/超限/断流/释放/重启测试 |
| 联调 | D008 | D009 | 2026-09-30～2026-10-12 | B 映射及 Provider 正文读取联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 2 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 2 |
| **总人天** | **9** |

### 10. 备注

- WBS 对应：A-EPIC-07-T1。
- PRR Action：—。
- 设计依据：主设计第 3.5、3.5.1 节；数据定义第 9、10 章及读取账本；P2 RP2-02～05；C 对接。
- 工作量及责任边界：包括正式原文及可选热副本读取、完整性校验和资源账本，不建设底层物理存储或 C 的调度能力。成功正式回退不因缓存失败单独降级。

---
## A-EPIC-08 · Rank / Decay / Conflict Execution

**Epic 范围**：消费 B 提供的语义属性、衰减/冲突/证据规则执行排序。

**关联 PRR Action**：—。

**Epic 级完成标准**：B 定义 Memory Domain 属性/规则，A 对 Recall 执行结果负责；排序可解释、可追踪输入来源。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-08 |
| Epic Name | Rank / Decay / Conflict Execution |
| Feature ID | A-FEAT-08-01 |
| Feature Name | 排序、衰减与冲突规则执行 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东 |
| 目标里程碑 | M2 |

### 2. User Story

作为上下文使用方，我希望优先获得相关且可解释的记忆，减少重复内容并完整保留必要冲突，从而避免排序或裁剪掩盖重要证据和分歧。

### 3. 功能描述

#### 3.1 功能目标

对已可信加载的候选去重、融合来源排名，消费 B 的语义/衰减/冲突策略。

#### 3.2 核心输入

- 已校验且已加载的 RecallCandidate、各来源 rank 及真实来源证据。
- B 提供的版本化语义策略、衰减/证据属性、完整冲突成员和获准回退。

#### 3.3 核心处理

1. 按内容身份合并重复，固定代表候选及正文/加载路径绑定。
2. 按版本化规则融合来源内排名，不直接比较异构原始分数。
3. 执行 B 语义策略并形成必须一起呈现的冲突关系，保存稳定排序依据。

#### 3.4 核心输出

- RankedRecallCandidates、可追溯 final_rank 和 policy_version。
- 冲突成员及解释、语义回退或隔离原因。

### 4. 正常流程

接收可信候选 → 内容身份去重 → 来源排名融合 → 执行 B 语义规则 → 核验完整冲突成员 → 保存稳定排序。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 语义事实缺失 | 仅用 B 明确批准的回退，否则隔离 | 保留缺口，不编造默认语义 |
| 冲突成员不完整或不安全 | 整组隔离 | 不只呈现单方 |
| 同内容重复命中或同分 | 按固定代表与稳定键处理 | 不靠重复次数抬高权重 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 可信正文 | 身份、加载结果与来源依据 | Recall：陈凯 / 肖宇 |
| Memory 语义规则 | 策略、衰减、冲突成员与证据 | Remember：杨鹏通 / 赵旭东 |

接口依赖：AL-B04。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. 只排序已通过校验和加载的候选；高分不覆盖权限。
2. 重复内容合并后仍保留来源证据，实际加载归因不拼接其他候选。
3. 同输入与策略版本得到稳定排序，不直接混比来源 raw_score。
4. 冲突完整呈现或整组隔离，语义回退记录原因。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：陈凯。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D001 | 2026-09-09 | 去重、融合及冲突规则设计 |
| 开发 | D002 | D004 | 2026-09-10～2026-09-14 | 稳定排序和 B 策略执行实现 |
| 测试 | D005 | D005 | 2026-09-15 | 重复/同分/缺语义/冲突测试 |
| 联调 | D006 | D006 | 2026-09-16 | B 策略及冲突成员联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 1 |
| 开发 | 3 |
| 测试 | 1 |
| 联调 | 1 |
| **总人天** | **6** |

### 10. 备注

- WBS 对应：A-EPIC-08-T1。
- PRR Action：—。
- 设计依据：主设计第 3.6 节；数据定义第 11 章；B 语义策略对接。
- 工作量及责任边界：B 定义语义属性和衰减/冲突/证据规则，A 执行并记录依据。Token 分组、最终复核与预算装配归 A-EPIC-09。

---
## A-EPIC-09 · Context Assembly

**Epic 范围**：Context Pack；Token Budget；complete/degraded/failed；truncated；missing_sources。

**关联 PRR Action**：—。

**Epic 级完成标准**：Context Pack 状态正确；缺失源与预算截断显式；不能把 degraded 冒充 complete。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-09 |
| Epic Name | Context Assembly |
| Feature ID | A-FEAT-09-01 |
| Feature Name | 上下文组装、预算控制与可靠交付 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Remember：杨鹏通 / 赵旭东；Shared Runtime / 实际调用方 |
| 目标里程碑 | M2 |

### 2. User Story

作为 Agent 调用方，我希望获得预算内、引用可追踪且状态准确的 Context Pack，从而安全使用内容，并区分完整有内容、正常空、降级和失败结果。

### 3. 功能描述

#### 3.1 功能目标

实现最终复核、ContextPack 装配、四终态判定及可靠提交/结果重放。

#### 3.2 核心输入

- 稳定排序和完整冲突成员、已加载候选及替补。
- 调用方确认的模板/tokenizer、token_budget、deadline、来源覆盖、缺失和授权证据。

#### 3.3 核心处理

1. 按不可拆分内容组试装，对全部已加载可组装候选及替补向 B 做一轮最终复核。
2. 移除失效/未知项，重算完整冲突组和实际渲染 Token，再按规则判定终态。
3. 一致保存 Pack、终态、最小 Trace 和已产生事件 Outbox；确认提交后返回，并执行同键重放/保留规则。

#### 3.4 核心输出

- ContextPack / ContextItem：真实文本、引用、版本、used_tokens、truncated 和缺失说明。
- 四业务终态：COMPLETE_AVAILABLE、COMPLETE_EMPTY、DEGRADED_AVAILABLE、FAILED_UNAVAILABLE；对应 result_class/recall_availability。

### 4. 正常流程

试装预算组 → B 最终复核全部已加载候选 → 剔除并重算 → 判定拟议终态 → 一致提交/查证 → 有效期内返回。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| 有合格组但全部装不下 | 返回预算失败，正文为空 | 不能标为 COMPLETE_EMPTY |
| 最终权限或安全证明失败 | 清空待交付内容并安全失败 | 不跳过复核 |
| 提交效果未知或恢复超期 | 按原提交身份查证或依既定隔离规则收尾 | 未证明提交不发出正文/业务终态 |
| 历史正文已清除或版本失效 | 按既有重放规则拒绝或返回可证明结果 | 不以同键新建执行补正文 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| 排序与资格适配 | 已加载候选和最终复核消费能力 | Recall：陈凯 / 肖宇 |
| 最终权威核验 | 当前状态、授权、版本、TTL 与有效窗口 | Remember：杨鹏通 / 赵旭东 |
| 调用格式 | 真实注入模板、tokenizer 与预算消费 | 实际调用方 / Agent Runtime 联系人待确认 |
| 一致持久化 | 唯一提交、恢复查证、Outbox 和分期清除 | Shared Runtime：沈家隆 / 杨鹏通 |

接口依赖：AL-B03、AL-B06、AL-RF02、AL-P401、AL-P403。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. used_tokens 按完整渲染文本精确计数且不超预算，冲突组不被拆散。
2. 替补候选也经过最终复核，不临时重搜或加载新版本补包。
3. 完整有内容、完整空、降级有内容、失败不可用四种终态均有可复现用例。
4. 缓存成功回退及正常 Token 裁剪不单独强制降级；来源故障不冒充正常空。
5. 一致提交得到确认前不发布正文；提交未知、重复调用、崩溃和正文清除后重放保持安全。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：陈凯。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D002 | 2026-09-09～2026-09-12 | 预算、复核、终态与提交设计 |
| 开发 | D003 | D007 | 2026-09-14～2026-09-24 | Pack 装配、一致提交及重放实现 |
| 测试 | D008 | D010 | 2026-09-28～2026-10-09 | 四终态/超预算/复核变化/提交崩溃测试 |
| 联调 | D011 | D012 | 2026-10-12～2026-10-16 | B、RF 与实际调用方端到端交付联调 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 2 |
| 开发 | 5 |
| 测试 | 3 |
| 联调 | 2 |
| **总人天** | **12** |

### 10. 备注

- WBS 对应：A-EPIC-09-T1、A-EPIC-09-T2。
- PRR Action：—。
- 设计依据：主设计第 3.7、3.8、3.8.1、4 章；数据定义第 12～14 章及提交/保留嵌入结构；总流程状态机。
- 工作量及责任边界：包含最终复核、预算、四终态、最小 Trace/Outbox 一致提交、重放和保留行为。完整阶段事件投递与旁路归因归 A-EPIC-10；发出正文不等于模型已使用。

---
## A-EPIC-10 · Recall Observability, SLO, Audit & Acceptance

**Epic 范围**：Recall trace；degraded/缺失源告警；E2E SLO/availability；结果重放审计；Benchmark/Acceptance Evidence。

**关联 PRR Action**：A-P0-4、A-P1-1、A-P1-4（并承接 A-P0-5 的发布验收证据）。

**Epic 级完成标准**：Query→Context Pack trace_id 贯通；complete/degraded/failed、缺失源、projection/model/version 可查；SLO/availability 有口径；告警可触发；同 trace 可定位/重放；Lane 2 与 Lane 3 验收证据分离。

### 1. 基本信息

| 字段 | 内容 |
| --- | --- |
| Project | P3 |
| Epic ID | A-EPIC-10 |
| Epic Name | Recall Observability, SLO, Audit & Acceptance |
| Feature ID | A-FEAT-10-01 |
| Feature Name | 召回可观测性、SLO、审计与验收 |
| Owner | 陈凯 / 肖宇（Recall） |
| Contributor | Shared Runtime；Remember：杨鹏通 / 赵旭东；Operator：沈家隆 / 谭旭梁；P2 / 实际调用方 / 验收负责人 |
| 目标里程碑 | M3 |

### 2. User Story

作为运维人员、Operator 调度模块和验收负责人，我希望查询完整 Recall 访问过程、监控服务指标和故障告警，并凭同一 trace 审计回放关键决策及验证预热使用事实，从而定位错误、评估服务表现并确认交付质量。

### 3. 功能描述

#### 3.1 功能目标

贯通 Query→Context Pack 的 Trace 与业务访问事件，提供可靠投递、可选预热使用核验、E2E SLO/availability 指标与告警、审计回放及模拟/真实环境分离的验收证据。

#### 3.2 核心输入

- 各阶段真实访问观察：request/trace、memory/representation、版本、来源、latency、bytes。
- C/Provider 动作成功、实际副本及有效窗口证据；RF 覆盖证据；可用的实际调用方回执。
- Recall 终态、阶段延迟、reason_code、来源覆盖和真实 Trace。
- 获准统计窗口、分母、阈值和 Acceptance Profile。
- trace_id、固定模型/策略/模板版本、候选和校验快照、来源/预算/提交证据。
- 现有开发验收场景、P2/B/C 联调场景、性能报告和发布演练记录。

#### 3.3 核心处理

1. 分别记录 Search Hit、Candidate Accepted、Canonical Loaded、Context Selected、Context Emitted 等实际时点。
2. 映射获批共享 Schema，经 Outbox 稳定身份投递和补发，保留 Ack 的真实含义。
3. 旁路关联准确动作、版本、表示、实际副本和窗口，形成可版本化核验；无使用回执时保持未知。
4. 定义 complete/degraded/failed、available/empty/unavailable 的指标映射与统计分母。
5. 生成 E2E P95/P99、降级率、缺失源等指标及诊断维度。
6. 按签收阈值触发/恢复告警，提供来源、阶段和 trace 定位入口。
7. 按权限读取保存的证据重放决策，核对过滤、排序、预算和终态。
8. 设计并执行完整读链路、投影写链路、故障恢复和真实依赖集成用例。
9. 汇集各 Feature 专项证据，分别出具模拟契约和真实 P2 验收结论及未满足项。

#### 3.4 核心输出

- RecallTrace、RecallAccessObservation、OutboxEntry。
- ContextUseAssessment / PlacementVerificationRecord：真实命中、未知原因、证据和评估版本。
- 指标定义、仪表盘、告警规则和诊断说明。
- 告警触发、定位及恢复证据。
- 可追溯审计/回放结果与不可重放原因。
- 端到端测试记录、Lane 2 / Lane 3 分列的验收报告和证据索引。

### 4. 正常流程

各阶段生成真实 Trace/访问观察 → 可靠保存并经 Outbox 投递 → 聚合终态、时延和来源指标 → 触发或恢复告警 → 按证据执行旁路核验与审计回放 → 执行跨功能和真实 P2 验收 → 汇集 Benchmark、发布演练及验收报告。

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
| --- | --- | --- |
| RF 暂不可用或回包丢失 | 保留 Outbox 并按原身份补发 | 不把 Ack 当作 C 已消费 |
| 缺实际副本、版本或动作关联 | 保留不可验证原因 | 不按时间接近推断命中 |
| 窗口未闭合或事件覆盖不足 | 保留未知；迟到证据生成新评估版本 | 不当作未命中 |
| 只有 Context 发出记录 | 只证明发出事实 | 不推断调用方收到或模型已使用 |
| 正常空或正常预算裁剪 | 按业务规则分类 | 不一律计为失败或降级 |
| 遥测缺失 | 标明覆盖不足并暴露观测异常 | 不输出虚假的健康率 |
| 依赖半故障 | 按实际来源/阶段呈现告警 | 不只报无信息的总错误 |
| 证据已清除或缺少必要版本 | 明确不可重放原因 | 不声称可以精确复现 |
| 当前权限失效 | 限制审计/重放内容输出 | 不通过历史 Pack 绕过授权 |
| 模拟通过但真实 P2 未就绪 | 只报告模拟契约结果 | 不标记真实联调通过 |
| 专项指标或发布演练不通过 | 记录未满足项并定位负责 Feature | 不通过全链路放行 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
| --- | --- | --- |
| Recall 各阶段 | 真实时点、来源、加载与交付事实 | Recall：陈凯 / 肖宇 |
| 共享事件体系 | AccessTrace Schema、摄取/Ack 和覆盖证据 | Shared Runtime：沈家隆 / 杨鹏通 |
| 动作与副本证据 | 成功动作、实际表示、generation 或等效关联 | Operator：沈家隆 / 谭旭梁；Provider 联系人待确认 |
| 使用回执 | 接收与模型使用事实 | 实际调用方联系人待确认；P4 / Auth 联系人待确认 |
| 终态和追踪 | 准确分类与阶段指标 | Recall：陈凯 / 肖宇 |
| 监控基础 | 指标采集、查询和告警通道 | Shared Runtime：沈家隆 / 杨鹏通；监控 Provider 联系人待确认 |
| 验收口径 | SLO、阈值、窗口及统计分母 | 项目验收负责人联系人待确认 |
| 全部 A Feature | 可运行实现、专项测试和版本证据 | Recall：陈凯 / 肖宇 |
| 真实联调环境 | Remember、P2、Shared Runtime、调用方，适用时 Operator | Remember：杨鹏通 / 赵旭东；P2 对接人；Shared Runtime：沈家隆 / 杨鹏通；实际调用方联系人待确认；Operator：沈家隆 / 谭旭梁 |
| 验收评测 | 签收标准、测试数据及放行评审 | 项目验收负责人联系人待确认 |

接口依赖：AL-C01～04、AL-P204、AL-RF02、AL-RF03、AL-P402、AL-PM01。能力要求和接入条件见[接口依赖清单](运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)。

### 7. 验收标准

1. Query 到 Context 的 trace_id 可贯通，模型/投影版本、过滤和缺失原因可定位。
2. 检索、校验、加载、选择、发出分别记录，bytes/latency 使用真实观察口径。
3. 重复、乱序、重启和丢失回包不制造新的业务访问，投递状态不冒充消费状态。
4. 只有匹配实际副本与版本等证据才归因；无覆盖证据不判未命中。
5. 旁路核验不阻塞基础 Recall，不改写 C 的 TierAction/Plan，不凭相关性宣称性能收益。
6. 四业务终态到三类结果和 availability 的统计映射一致。
7. degraded 率、缺失源与 E2E 时延均能查询并按来源定位。
8. 注入超时/partial/后端异常能触发相应告警，恢复能留存证据。
9. 正式阈值按 Acceptance Profile 执行，达标结论以实测证据为依据。
10. 同 trace 能解释用了哪个 Memory/投影/模型、为何过滤或降级。
11. 固定证据可重放关键决策；缺失/过期/撤权场景不伪造复现。
12. 模拟与真实环境结论分开，正常、空、降级、失败及关键恢复链覆盖。
13. 性能双 Profile、模型发布演练和告警证据可引用，所有未满足项有归属。

### 8. 计划

本 Epic 从 D001 独立计算，阶段仅累计本 Epic 的有效投入日，不承接其他 Epic 的结束日。当前牵头：肖宇。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
| --- | --- | --- | --- | --- |
| 设计 | D001 | D004 | 2026-09-09～2026-09-15 | 阶段事件、投递与归因规则；SLO/availability、分母及阈值方案；审计回放与 E2E 验收方案 |
| 开发 | D005 | D015 | 2026-09-16～2026-10-16 | Trace、Outbox 及旁路核验实现；指标、仪表盘与告警配置；审计/回放工具与集成场景编排 |
| 测试 | D016 | D023 | 2026-10-19～2026-11-06 | 重复/乱序/缺证据/迟到修订测试；分类准确性及告警触发/恢复测试；跨 Feature 回归与证据一致性检查 |
| 联调 | D024 | D029 | 2026-11-09～2026-11-20 | Shared Runtime / Operator 事件消费与核验记录交接；监控环境与验收口径联调；真实 P2 E2E 及验收报告 |

### 9. 人天

| 工作项 | 人天 |
| --- | --- |
| 设计 | 4 |
| 开发 | 11 |
| 测试 | 8 |
| 联调 | 6 |
| **总人天** | **29** |

### 10. 备注

- WBS 对应：A-EPIC-10-T1、A-EPIC-10-T2。
- PRR Action：A-P0-4、A-P1-1、A-P1-4（并承接 A-P0-5 的发布验收证据）。
- 设计依据：主设计第 5.2、5.3、7 章；数据定义第 14～18 章；C 对接 RC-01～06。；Epic A-EPIC-10；A 生产就绪补充；主设计第 4、7、9 章。；WBS A-EPIC-10；A 工作包完整 DoD；主设计第 9 章及 P2/B/C 接口验收清单。
- 工作量及责任边界：包含追踪/预热核验、SLO/告警、审计回放和统一验收。各 Epic 局部测试与业务实现不重复计入；A-P0-5 的发布实现及专项演练归 A-EPIC-01，本项承接其证据并完成全链路验收。RF 拥有共享 Schema/Ingest，C 拥有策略及动作事实。

---

## 附录：WBS / PRR 覆盖

| Epic ID | 统一 Feature ID | WBS Task | PRR Action |
| --- | --- | --- | --- |
| A-EPIC-01 | A-FEAT-01-01 | A-EPIC-01-T1、A-EPIC-01-T2、A-EPIC-01-T3、A-EPIC-01-T4 | A-P0-2、A-P0-3、A-P0-5 |
| A-EPIC-02 | A-FEAT-02-01 | A-EPIC-02-T1、A-EPIC-02-T2、A-EPIC-02-T3 | — |
| A-EPIC-03 | A-FEAT-03-01 | A-EPIC-03-T1、A-EPIC-03-T2 | — |
| A-EPIC-04 | A-FEAT-04-01 | A-EPIC-04-T1、A-EPIC-04-T2 | A-P0-1 |
| A-EPIC-05 | A-FEAT-05-01 | A-EPIC-05-T1、A-EPIC-05-T2、A-EPIC-05-T3 | A-P1-3 |
| A-EPIC-06 | A-FEAT-06-01 | A-EPIC-06-T1 | A-P1-2 |
| A-EPIC-07 | A-FEAT-07-01 | A-EPIC-07-T1 | — |
| A-EPIC-08 | A-FEAT-08-01 | A-EPIC-08-T1 | — |
| A-EPIC-09 | A-FEAT-09-01 | A-EPIC-09-T1、A-EPIC-09-T2 | — |
| A-EPIC-10 | A-FEAT-10-01 | A-EPIC-10-T1、A-EPIC-10-T2 | A-P0-4、A-P1-1、A-P1-4（并承接 A-P0-5 的发布验收证据） |

每个 Epic 的工作量包含其 WBS 和关联的生产就绪任务。A-P0-5 的发布实施与专项演练只在 A-EPIC-01 核算；A-EPIC-10 承接发布验收证据和全链路验证。

原 A-01～A-04 是运行时设计主题，不等同于 A-EPIC-01～04。来源与加载落实于 A-EPIC-04～07；失败处理分布在各功能并于 A-EPIC-09 统一终态；AccessTrace 与 Placement Verification 纳入 A-EPIC-10。


# 第三章 Operate

# AetherStore P3 · Operate / Optimize 负责人功能填报 V1.1

## Person C — Operate / Optimize

> 本版以 P2 负责人填报中的项目基线为日期参考：2026-09-18 冻结公共契约，2026-10-16 完成核心路径，2026-11-13 冻结 RC 接口，2026-11-20 功能冻结，2026-11-30 完成 MVP 交付。本版补充 C 组的项目日期、实名责任域和 Shared Runtime Foundation，10 个 C Epic 及 152 人天总量不变。

**Primary Outcome**：把 MemorySignal、AccessTrace、真实 Placement 和资源事实转换为调度决策、TierAction、反馈和对账闭环；TierAction 长时间 Unknown 时由 C 负责收敛。

本版参照 A 组填报文档的呈现方式，按 `C-EPIC-01`～`C-EPIC-10` 组织为 **10 项汇总功能**，每个 Epic 对应一个汇总 Feature。原有 20 个原子子功能保留在各 Epic 的“子功能映射”中，用于追溯 WBS 和实现范围。

### 编号规则

- `C-FEAT-01`～`C-FEAT-10`：公司汇报口径的汇总 Feature，每个 Epic 对应一个。
- `C-SUB-01`～`C-SUB-20`：Epic 下的原子子功能，对应原有 WBS 和实现范围。
- 下方“基本信息”的 `Feature ID` 使用汇总 Feature 编号；“子功能映射”使用子功能编号。

## C 的最终 Epic 结构

| Epic ID | 最终 Epic | Feature ID | Feature Name | 子功能编号 | 估算人天 | Epic 内独立计划 |
|---|---|---|---|---|---:|---|
| C-EPIC-01 | Signal / Trace Consumption | C-FEAT-01 | Signal / Trace 接入与消费 | C-SUB-01、C-SUB-02 | 10 | D001～D010 |
| C-EPIC-02 | Representation Hotness | C-FEAT-02 | Representation 热度计算 | C-SUB-03 | 8 | D001～D008 |
| C-EPIC-03 | RepresentationPlacementPlan | C-FEAT-03 | PlacementPlan 对象与构建运行时 | C-SUB-04、C-SUB-05 | 18 | D001～D018 |
| C-EPIC-04 | Actuation Target Resolve | C-FEAT-04 | ActuationTarget 解析与不透明目标消费 | C-SUB-06、C-SUB-07 | 12 | D001～D012 |
| C-EPIC-05 | Scheduling View & Capacity Observation | C-FEAT-05 | 调度视图与容量观测 | C-SUB-08、C-SUB-09 | 14 | D001～D014 |
| C-EPIC-06 | Placement Decision, Capacity Guard & Admission Control | C-FEAT-06 | 放置决策、容量保护与准入 | C-SUB-10、C-SUB-11 | 20 | D001～D020 |
| C-EPIC-07 | TierAction Runtime & Operator Control | C-FEAT-07 | TierAction 运行时与人工控制 | C-SUB-12、C-SUB-13 | 18 | D001～D018 |
| C-EPIC-08 | Storage Control Simulator | C-FEAT-08 | 统一存储控制模拟器与故障套件 | C-SUB-14、C-SUB-15 | 20 | D001～D020 |
| C-EPIC-09 | TierAction / Placement Reconciliation & Migration Safety | C-FEAT-09 | TierAction、Placement 业务对账与迁移安全 | C-SUB-16、C-SUB-17 | 18 | D001～D018 |
| C-EPIC-10 | Optimize / Predict & Controlled Release | C-FEAT-10 | 优化、预测与受控发布 | C-SUB-18、C-SUB-19、C-SUB-20 | 14 | D001～D014 |
| **合计** |  |  |  |  | **152** | — |

## 计划与人天口径

- **计划**：每个 Epic 窗口内含设计、开发、测试、联调四个阶段与额外缓冲日；2026-09-25～09-27 与 2026-10-01～10-08 暂不排开发；2026-11-23～11-27 集中联调，2026-11-28～11-30 为整体缓冲、复验与交付。
- **人天**：为 Operate / Optimize 剩余交付工作量的初估，含设计、开发、测试、联调余量，不回算历史已投入；每个 Epic 仅一个工作量 Owner，协作者投入不重复累计。总量为 152 人天＝设计 34＋开发 55＋测试 37＋联调 26。
- **团队配置**：2 人（沈家隆 / 谭旭梁）；两位责任人共同负责 Operator（Operate / Optimize）的全部 Epic。Epic 内 D001 等仅表示该 Epic 的投入序号，不是项目自然日期；各 Epic 保留的当前牵头人不代表独立的人天池。
- **负责人**：沈家隆 / 谭旭梁对 Operator（Operate / Optimize）整体交付负责；A、B、P2、Provider 和 Shared Runtime 保留各自领域职责，外部团队实施投入和设备采购不包含在本表。
- 状态均为规划基线，不代表已签收。

### 责任人与并行关系

沈家隆 / 谭旭梁共同负责 C 组全部 Epic；不再按内部执行岗位拆分，也不把公共工作重复计入 C 组。每个 Epic 的当前牵头人和跨组协作人以该 Epic 的计划段落为准，牵头人不代表独立的人天池。

| 责任域 | 责任人 | 负责 Epic | 功能投入（人天） |
| --- | --- | --- | ---: |
| Operator | 沈家隆 / 谭旭梁 | C-EPIC-01～C-EPIC-10 | **152** |
| 合计 | 2 人 | 10 个 Epic | **152** |

- 输入消费、P2 契约、模拟器和预测能力可以按依赖并行启动；先统一对象、版本和状态语义。
- Placement、ResourceState 和 ActuationTarget 契约稳定后完成真实 P2 联调；模拟器可提前验证动作和故障收敛。
- 预测与受控发布依赖热度、Plan、Action 和反馈数据，相关基础能力就绪后汇合验证。
- 沈家隆 / 谭旭梁可在多个 Epic 间并行穿插推进，但同一工作日不重复占用；个人不单独拆分人天，整体工作量仍为 152 人天。

### 项目交付窗口

以下日期沿用 P2 文档的项目基线。C 的 Epic 计划表同时保留 Epic 内 `D001` 序号和项目实际日期；两个口径不能相互换算。`2026-09-25～09-27` 与 `2026-10-01～10-08` 不安排开发，必要的评审、测试、等待和整改仍可在功能窗口内进行。

| 项目日期 | C 组节点 | C 侧主要责任输出 | 依赖 / 出口 |
| --- | --- | --- | --- |
| 2026-09-09 | 项目启动与公共对象对齐 | 确认 MemorySignal、AccessTrace、Placement、ResourceState、ActuationTarget、TierAction 的责任边界；启动 Shared Runtime 和模拟器 | 沈家隆 / 谭旭梁建立联合工作流；等待公共契约评审 |
| 2026-09-18 | 契约 v0.1 冻结 | 冻结事件信封、版本、幂等、对象关联、状态和结果确认语义 | Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文；Shared Runtime：沈家隆 / 杨鹏通按同一契约实现和联调 |
| 2026-10-16 | 公共底座与 C 核心路径基线 | 完成输入消费、热度、PlacementPlan、观察视图、动作状态机和模拟器的可运行骨架 | 形成 `Signal / Trace → Plan → TierAction` 的可验证路径 |
| 2026-11-06 | 调度输入与控制规则冻结 | 冻结 Placement / ResourceState 观察、ActuationTarget、容量保护、准入、重试和对账规则 | 后续只允许缺陷修复和必要兼容调整 |
| 2026-11-13 | RC 接口 v0.9 冻结 | 完成 C 与 P2/Provider 的提交、查询、反馈和人工控制接口核对 | 进入端到端联调准备 |
| 2026-11-20 | C 组功能冻结 | 完成 10 个 Epic 的功能实现、单元测试、接口证据和发布回退方案 | 阻断新增功能范围，遗留问题进入集中修复 |
| 2026-11-23～11-27 | 集中联调、Chaos、性能与端到端验收 | 验证真实 P2 执行、Unknown 对账、重启恢复、容量保护、模拟器 Case 1～6 和策略回退 | 形成验收证据和缺陷关闭清单 |
| 2026-11-28～11-30 | 缓冲、复验与交付 | 复验修复项，归档计划、动作、反馈、对账和发布记录 | MVP 交付 |

### 阶段人天汇总

| 阶段 | 人天 | 说明 |
|---|---:|---|
| 设计 | 34 | 公共对象/状态设计和各 Epic 的增量规则设计 |
| 开发 | 55 | 公共运行能力一次实现和各 Epic 的具体逻辑 |
| 测试 | 37 | 公共测试能力和各 Epic 的业务、故障用例 |
| 联调 | 26 | A/B/P2 接口验证、故障联调和端到端验证 |
| **合计** | **152** | 与本次复评后的 C 组规划总人天一致 |

### 跨组依赖联系人

| 依赖类别 | 具体依赖 | 对接联系人 | 最迟形成时间 |
|---|---|---|---|
| 记忆事实与信号 | MemoryRecord、MemorySignal、版本、生命周期和表示资格变化 | 杨鹏通 / 赵旭东（Remember） | 2026-09-18 |
| Recall 访问事实 | AccessTrace、Recall 结果和访问阶段语义 | 陈凯 / 肖宇（Recall）；实际观测组件联系人待确认 | 2026-09-18 |
| 调度输入与执行反馈 | Placement、ResourceState、ActuationTarget、ExecutionFeedback 和 Provider task | 刘佳正 / 陈晔（P2 公共底座）；高琛 / 高旭（P2 平台接入）；杨文博 / 王广诚（P2 可观测与验收）；Provider 联系人待确认 | 2026-11-13 |
| 共享运行时 | State Catalog、Signal/Telemetry、任务、投递、权限、审计和关联查询 | 沈家隆 / 杨鹏通（Shared Runtime） | 2026-09-18 |
| 环境搭建与验收 | 模拟器、真实 Provider、Chaos、性能和 E2E 验收窗口 | 陈凯 / 肖宇 | 2026-11-23 |

---

## C-EPIC-01 · Signal / Trace Consumption

**Epic 范围**：消费 B 的 `MemorySignal` 和 Recall / 实际观测组件产生的 `AccessTrace`，完成校验、去重、重放和有效访问归一化。

**关联 PRR Action**：—。

**Epic 级完成标准**：重复信号和 Trace 不重复计数或重复调度；消费游标可恢复；Submitted 不被误认为已完成消费；输入事实可追踪、可重放。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-01 | MemorySignal 消费 |
| C-SUB-02 | AccessTrace 消费 |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-01 |
| Epic Name | Signal / Trace Consumption |
| Feature ID | C-FEAT-01 |
| Feature Name | Signal / Trace 接入与消费 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | Remember：杨鹏通 / 赵旭东（MemorySignal 生产）；Recall：陈凯 / 肖宇（Recall 结果与访问阶段）；实际观测组件联系人待确认（AccessTrace 观测）；Shared Runtime：沈家隆 / 杨鹏通（投递与 Schema） |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为调度决策方，我希望可靠、幂等地消费 MemorySignal 和真实 AccessTrace，从而及时感知业务变化，并基于真实访问而不是搜索命中进行热度和调度计算。

### 3. 功能描述

#### 3.1 功能目标

接入并归一化 C 的两类输入事实：MemorySignal 用于感知记忆版本、生命周期或表示资格变化；AccessTrace 用于记录检索、加载、上下文选择和实际使用。C 不拥有这两类事实的生产权。

#### 3.2 核心输入

```text
event_id
event_type
schema_version
source
memory_id
memory_version
representation_id
signal_version
event_stage
source_provider
occurred_at
observed_at
scope
trace_id
idempotency_key
payload_summary
```

#### 3.3 核心处理

1. 校验来源、Schema、对象标识、版本、Scope 和时间。
2. 按 `event_id` 或幂等键记录消费结果，重复事件不重复生成动作或统计。
3. 按来源版本处理乱序 Signal，旧版本不得覆盖新输入。
4. 按事件阶段归一化 AccessTrace，只把正文加载、上下文选中和实际使用计入有效访问。
5. 保存各来源消费游标，支持补发、重放和服务重启恢复。
6. 将有效变化和访问事实提供给 Representation Hotness，不直接命令 P2 迁移。

#### 3.4 核心输出

```text
signal_consumption_record
normalized_access_trace
consumption_status
last_consumed_version
aggregation_window
effective_access
replay_cursor
plan_recompute_trigger
trace_id
```

### 4. 正常流程

```text
接收 MemorySignal / AccessTrace
↓
校验事件身份、版本和时间
↓
登记幂等消费记录
↓
按事件阶段归一化访问事实
↓
保存游标和统计窗口
↓
触发表示级热度和 Plan 重算
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 重复 Signal 或 Trace | 按事件身份去重 | 不重复生成动作或访问统计 |
| 缺少稳定对象标识、版本或时间 | 拒绝进入调度计算并记录原因 | 不产生新的 TierAction |
| Trace Schema 版本漂移 | 拒绝不兼容记录并告警 | 不使用不确定数据驱动调度 |
| 投递或消费暂时不可用 | 保留游标和待处理记录，等待补发 | 已提交事实不被改写 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| MemorySignal | 记忆版本、生命周期和表示资格变化通知 | Remember：杨鹏通 / 赵旭东 |
| AccessTrace | 真实访问阶段事实 | Recall：陈凯 / 肖宇；实际观测组件联系人待确认 |
| Delivery / Replay | 补发、重放和消费游标 | Shared Runtime：沈家隆 / 杨鹏通 |
| Hotness Runtime | 消费归一化后的输入 | Operator：沈家隆 / 谭旭梁 |

### 7. 验收标准

1. 相同 `event_id` 或幂等键重复投递时只产生一次有效消费结果。
2. Search Hit 不会被单独计为实际使用。
3. 缺少对象标识、版本或时间的事件不会驱动物理调度。
4. 旧版本 Signal 不会覆盖 C 已保存的新版本输入。
5. 消费游标能够保存、恢复和重放。
6. MemorySignal 消费完成不会被错误标记为 TierAction 成功。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-09-09～2026-09-22`；当前牵头：沈家隆。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D002 | 2026-09-09～2026-09-10 | 统一输入信封、去重、重放和访问计数口径 |
| 开发 | D003 | D006 | 2026-09-11～2026-09-16 | Signal/Trace 消费、游标和归一化实现 |
| 测试 | D007 | D008 | 2026-09-17～2026-09-18 | 重复、乱序、版本漂移和补发测试 |
| 联调 | D009 | D010 | 2026-09-21～2026-09-22 | 与 Recall：陈凯 / 肖宇、实际观测组件联系人待确认和 Shared Runtime：沈家隆 / 杨鹏通联调 |

计划窗口：2026-09-09～2026-09-22；窗口内未分配日期用于跨组评审、等待和整改，不新增人天。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 2 |
| 开发 | 4 |
| 测试 | 2 |
| 联调 | 2 |
| **总人天** | **10** |

### 10. 备注

```text
- 原子子功能：C-SUB-01、C-SUB-02；WBS：C-EPIC-01-T1、C-EPIC-01-T2。
- AccessTrace 的 Schema/Ingest 由 Shared Runtime 负责，C 负责消费和业务归一化。
- 该 Feature 的设计人天包含 C 组统一输入消费约定，其他 Epic 不重复计算这部分公共设计。
```

---

## C-EPIC-02 · Representation Hotness

**Epic 范围**：按 `representation_id` 计算独立热度，综合有效访问、时间衰减、上下文使用、重要度和资源价值。

**关联 PRR Action**：—。

**Epic 级完成标准**：同一 Memory 的不同 representation 可以得到不同热度；热度可追溯、可重算；热度不是 B 的生命周期状态。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-03 | Per-Representation Hotness |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-02 |
| Epic Name | Representation Hotness |
| Feature ID | C-FEAT-02 |
| Feature Name | Representation 热度计算 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | Remember：杨鹏通 / 赵旭东（重要度等语义属性）；Recall：陈凯 / 肖宇（访问事实） |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为调度策略方，我希望每个 representation 都有独立、可解释的热度，从而避免把同一 Memory 的所有表示统一升层或降层。

### 3. 功能描述

#### 3.1 功能目标

基于有效访问和业务属性计算表示级 `hotness_score`，输出升层、降层或 Keep 的决策输入。热度是 C 的派生事实，不改变 MemoryRecord 的生命周期状态。

#### 3.2 核心输入

```text
memory_id
representation_id
effective_access_count
access_stability
temporal_decay
context_use
importance
resource_value
observation_window
policy_version
```

#### 3.3 核心处理

1. 按 `representation_id` 聚合有效访问和上下文使用。
2. 对频次、稳定性、时间衰减和业务属性进行归一化。
3. 按当前 `policy_version` 计算 `hotness_score`。
4. 保存统计窗口、输入指纹、分量和计算时间。
5. 根据连续窗口和阈值输出目标方向；输入不足时输出 Keep / No-op。

#### 3.4 核心输出

```text
representation_id
hotness_score
score_components
observation_window
policy_version
input_fingerprint
recommended_direction
decision_reason
calculated_at
```

### 4. 正常流程

```text
读取有效 AccessTrace 和业务属性
↓
按 representation 聚合统计窗口
↓
计算频次、衰减、使用和资源价值
↓
生成 hotness_score
↓
应用阈值和连续窗口
↓
输出给 PlacementPlan
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 只有 Memory 级热度 | 不强行复制到所有 representation | 保守 Keep / No-op |
| 访问窗口不完整 | 标记输入不完整并降低决策可信度 | 不发起激进调度 |
| 策略版本缺失 | 使用上一稳定策略或 Keep / No-op | 不产生无版本决策 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| AccessTrace | 实际访问和使用统计 | Recall：陈凯 / 肖宇；实际观测组件联系人待确认；C-EPIC-01：沈家隆 |
| MemoryRecord 语义属性 | importance、confidence 等业务输入 | Remember：杨鹏通 / 赵旭东 |
| PolicyContext | 权重、阈值、窗口和版本 | Operator：沈家隆 / 谭旭梁 |

### 7. 验收标准

1. 同一 Memory 的不同 representation 可以得到不同热度。
2. 搜索命中不会单独被当作有效使用。
3. 热度结果能够追溯到统计窗口、输入指纹和 `policy_version`。
4. 输入不足时自动降级为 Keep / No-op 或保守候选。
5. 相同版本、相同输入得到可复现的热度结果。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-09-23～2026-10-13`；当前牵头：沈家隆。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D002 | 2026-09-23～2026-09-24 | 热度分量、权重、阈值和窗口规则 |
| 开发 | D003 | D005 | 2026-09-28～2026-09-30 | 表示级热度计算和结果保存 |
| 测试 | D006 | D007 | 2026-10-09～2026-10-12 | 多表示、衰减、阈值和边界测试 |
| 联调 | D008 | D008 | 2026-10-13 | 与 AccessTrace、B 语义属性联调 |

计划窗口：2026-09-23～2026-10-13；2026-09-25～09-27 和 2026-10-01～10-08 不安排开发，空档用于等待契约和整改。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 2 |
| 开发 | 3 |
| 测试 | 2 |
| 联调 | 1 |
| **总人天** | **8** |

### 10. 备注

```text
- 原子子功能：C-SUB-03；WBS：C-EPIC-02-T1。
- hotness_score 是 C 的派生决策输入，不是 MemoryRecord 的业务状态。
- 权重、阈值或窗口变化必须产生新的 policy_version。
```

---

## C-EPIC-03 · RepresentationPlacementPlan

**Epic 范围**：定义 `RepresentationPlacementPlan` 领域对象，并将 Signal、AccessTrace、Placement 和资源事实转换为可审计计划。

**关联 PRR Action**：—。

**Epic 级完成标准**：Plan 字段完整；能区分 observed 和 desired；计划可审计、可重算；计划失效时不直接创建 TierAction。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-04 | Placement Plan Domain Object |
| C-SUB-05 | Plan Build Runtime |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-03 |
| Epic Name | RepresentationPlacementPlan |
| Feature ID | C-FEAT-03 |
| Feature Name | PlacementPlan 对象与构建运行时 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | Remember：杨鹏通 / 赵旭东（Memory/Representation 关联）；P2 公共底座：刘佳正 / 陈晔（观察事实）；Provider 联系人待确认；C-EPIC-02：沈家隆（热度模块） |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为调度系统，我希望把观察事实、目标层级、热度和决策依据保存到同一个 PlacementPlan 中，从而让每次调度都能被校验、审计和重新计算。

### 3. 功能描述

#### 3.1 功能目标

冻结 C 侧 Plan 领域对象及其构建运行时。Plan 表达期望状态，不表达物理执行结果，也不修改 P2 的真实 current_tier。

#### 3.2 核心输入

```text
memory_id
representation_id
representation_type
provider_ref
observed_tier
current_generation
hotness_score
decision_reason
policy_version
input_fingerprint
manual_override
generated_at
valid_until
MemorySignal
AccessTrace
ResourceState
```

#### 3.3 核心处理

1. 校验表示对象、Provider 引用、观察层级和 generation 的关联。
2. 合并有效 Signal、访问事实、Placement 观察和资源输入。
3. 生成 `observed_tier`、`desired_tier`、`target_generation` 和有效期。
4. 记录热度、策略版本、输入指纹、原因和人工控制影响。
5. 输入不足、资源过期或存在冲突时输出 Keep / No-op，不伪造目标。
6. 新事实改变目标时创建新 Plan，旧 Plan 保留用于审计。

#### 3.4 核心输出

```text
plan_id
memory_id
representation_id
entries
observed_tier
desired_tier
target_generation
decision_reason
policy_version
input_fingerprint
generated_at
valid_until
supersedes_plan_id
keep_noop_decision
```

### 4. 正常流程

```text
接收热度和真实观察输入
↓
校验对象、版本、新鲜度和控制条件
↓
生成 observed_tier 与 desired_tier
↓
记录原因、策略、输入指纹和目标代际
↓
保存新的 PlacementPlan
↓
交给前置检查和 ActuationTarget 解析
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| Plan 字段缺失 | 拒绝保存或标记不可执行 | 不生成 TierAction |
| generation 与最新观察不一致 | 使当前 Plan 失效并重新读取 | 不使用旧 Plan 调度 |
| 目标层级与当前层级一致 | 记录 Keep / No-op | 不创建物理动作 |
| 关键输入 stale/unknown | 保存降级原因，等待下一轮 | 不提交新的非 Keep 动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| Representation Hotness | 表示级热度和决策方向 | C-EPIC-02：沈家隆 |
| PlacementObservation | 当前真实层级、generation 和可读性 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| ResourceState | 容量、压力和迁移资源事实 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| MemorySignal / AccessTrace | 业务变化和实际访问输入 | MemorySignal：Remember：杨鹏通 / 赵旭东；AccessTrace：Recall：陈凯 / 肖宇、实际观测组件联系人待确认；C-EPIC-01：沈家隆 |

### 7. 验收标准

1. Plan 至少包含表示标识、observed_tier、desired_tier、target_generation、policy_version 和有效期。
2. Plan 能明确区分目标计划与真实物理观察。
3. Plan 失效时不会直接创建 TierAction。
4. 新 Plan 不覆盖旧 Plan，旧 Plan 可用于审计和解释。
5. 相同版本和相同输入能够重算出一致的 Plan。
6. 缺少关键输入时输出 Keep / No-op，不伪造 Placement。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-10-13～2026-11-05`；当前牵头：沈家隆。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D005 | 2026-10-13～2026-10-19 | Plan 字段、有效性、输入合并和重算规则 |
| 开发 | D006 | D012 | 2026-10-20～2026-10-28 | Plan 持久化、校验和构建运行时 |
| 测试 | D013 | D016 | 2026-10-29～2026-11-03 | 字段完整性、缺失输入、冲突和重算测试 |
| 联调 | D017 | D018 | 2026-11-04～2026-11-05 | 与 A/B 输入、P2 Placement 和 ResourceState 联调 |

计划窗口：2026-10-13～2026-11-05；真实 Placement / ResourceState 联调以 P2 核心路径在 2026-10-16 形成基线为前提。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 5 |
| 开发 | 7 |
| 测试 | 4 |
| 联调 | 2 |
| **总人天** | **18** |

### 10. 备注

```text
- 原子子功能：C-SUB-04、C-SUB-05；WBS：C-EPIC-03-T1、C-EPIC-03-T2。
- Plan 是 C 的 Desired State，不是 P2 的 current_tier，也不是物理迁移任务。
- 该 Feature 的设计人天包含 C 组统一 Plan 对象边界，其他 Epic 不重复设计同一套 Plan 语义。
```

---

## C-EPIC-04 · Actuation Target Resolve

**Epic 范围**：根据 representation 和目标层级解析 P2 可执行的不透明 `ActuationTarget`，并在动作生成时整体消费该目标。

**关联 PRR Action**：—。

**Epic 级完成标准**：P3 不按 P2 的 provider-specific `target_type` 分支；目标能稳定关联到 representation、Provider 和 generation；目标失效时不提交动作。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-06 | ActuationTarget Resolve Contract |
| C-SUB-07 | Opaque Target Consumption |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-04 |
| Epic Name | Actuation Target Resolve |
| Feature ID | C-FEAT-04 |
| Feature Name | ActuationTarget 解析与不透明目标消费 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | P2 公共底座：刘佳正 / 陈晔；P2 平台接入：高琛 / 高旭（目标解析）；Provider 联系人待确认；Shared Runtime：沈家隆 / 杨鹏通（契约治理） |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为 P3 调度方，我希望根据 representation、目标层级和 generation 获得稳定的不透明执行目标，从而不理解 P2 内部 Segment、Object 或物理路径也能提交统一动作。

### 3. 功能描述

#### 3.1 功能目标

实现 `ActuationTargetResolvePort` 及其消费规则。C 负责逻辑对象到 Provider 引用的关联和契约校验，但不设计或解析 P2 的内部目标结构。

#### 3.2 核心输入

```text
representation_id
desired_tier
target_generation
plan_id
valid_until
trace_id
request_id
action_type
```

#### 3.3 核心处理

1. 校验 representation、Plan、目标层级和 generation 的一致性。
2. 请求 P2/Provider 解析 opaque ActuationTarget。
3. 保存 target_id、provider_ref、支持动作、目标代际、route_epoch 和有效期。
4. 检查目标是否仍在计划有效期内，且支持当前动作。
5. 将目标作为整体写入 TierAction，不解析、拼装或按内部类型分支。

#### 3.4 核心输出

```text
target_id
representation_id
provider_ref
provider_name
source_tier
supported_operations
target_generation
route_epoch
resolved_at
valid_until
target_resolution_status
target_validation_result
```

### 4. 正常流程

```text
读取有效 PlacementPlan
↓
提交 representation、目标层级和 generation
↓
P2 / Provider 返回 opaque ActuationTarget
↓
校验目标关联、支持动作和有效期
↓
保存并整体转发目标
↓
进入 TierAction 生成流程
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 目标无法解析 | 记录 unresolvable 原因 | 不创建 TierAction |
| representation、Provider 或 generation 不一致 | 拒绝结果并要求重新读取 | 当前 Plan 不可执行 |
| 目标已过期或不支持动作 | 重新解析或记录 Keep / No-op | 不提交旧目标 |
| 代码尝试按 target_type 分支 | 阻止提交并记录架构违规 | 不产生物理动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| PlacementPlan | 提供目标层级和目标代际 | C-EPIC-03：沈家隆 |
| ActuationTargetResolvePort | 抽象表示到执行目标解析 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| ACT-001 | 目标解析契约和错误语义 | P2 平台接入：高琛 / 高旭；External Integration：陈凯 / 赵旭东 / 谭旭梁 |
| TierAction Runtime | 保存和提交统一动作 | C-EPIC-07：沈家隆 / 谭旭梁 |

### 7. 验收标准

1. C 能以 representation、desired_tier 和 target_generation 请求目标解析。
2. 解析结果至少能关联 target_id、provider_ref、支持动作、目标代际和有效期。
3. C 的业务逻辑不按 P2 的 provider-specific `target_type` 编写分支。
4. 目标失效、对象不存在或版本冲突时不会生成 TierAction。
5. opaque target 能完整关联到 Plan 和 representation，并可供后续查询、反馈和对账使用。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-09-15～2026-10-12`；当前牵头：沈家隆。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D003 | 2026-09-15～2026-09-17 | 目标解析输入输出、有效期和失效规则 |
| 开发 | D004 | D007 | 2026-09-21～2026-09-24 | 目标解析适配和 opaque target 消费 |
| 测试 | D008 | D009 | 2026-09-28～2026-09-29 | 合同、过期、版本冲突和架构约束测试 |
| 联调 | D010 | D012 | 2026-09-30～2026-10-12 | 与 P2/Provider 目标解析联调 |

计划窗口：2026-09-15～2026-10-12；联调阶段跨越 P2 的接口等待窗口，未列日期用于协作和整改，不新增人天。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 3 |
| 开发 | 4 |
| 测试 | 2 |
| 联调 | 3 |
| **总人天** | **12** |

### 10. 备注

```text
- 原子子功能：C-SUB-06、C-SUB-07；WBS：C-EPIC-04-T1、C-EPIC-04-T2。
- C 负责 memory_id → representation_id → provider_ref 的逻辑关联；P2/Provider 负责真实目标解析。
- ActuationTarget 解析成功不等于动作执行成功。
```

---

## C-EPIC-05 · Scheduling View & Capacity Observation

**Epic 范围**：消费真实 Placement、Segment 观测和资源状态，形成调度所需的观察视图。

**关联 PRR Action**：C-P1-3。

**Epic 级完成标准**：C 只消费真实观察，不伪造 current_tier；过期或未知观察会被标记并触发安全降级；段级统计不反推对象级事实。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-08 | Placement State Consumption |
| C-SUB-09 | Segment Introspection Consumption |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-05 |
| Epic Name | Scheduling View & Capacity Observation |
| Feature ID | C-FEAT-05 |
| Feature Name | 调度视图与容量观测 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | P2 公共底座：刘佳正 / 陈晔；P2 平台接入：高琛 / 高旭（Placement、Segment、ResourceState 接入）；Provider 联系人待确认；Shared Runtime：沈家隆 / 杨鹏通（观察契约） |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为调度决策方，我希望读取 P2 提供的真实层级、generation、容量和压力观测，从而区分“计划要放哪里”和“现在实际在哪里”，并在资源不足时保护在线服务。

### 3. 功能描述

#### 3.1 功能目标

消费 `PlacementObservation`、`SegmentIntrospection` 和 `ResourceState`，保存带来源和新鲜度的观察副本，向决策和 Capacity Guard 提供可靠输入。

#### 3.2 核心输入

```text
representation_id
provider_ref
target_id
resource_scope_id
provider_name
segment_ref
trace_id
request_id
```

#### 3.3 核心处理

1. 按 representation、Provider、目标或资源范围请求真实观察。
2. 校验对象标识、generation、route_epoch、版本、来源和观察时间。
3. 根据 `observed_at` 判断 Placement 和资源快照是否新鲜。
4. 保存 current_tier、可读性、支持动作、容量、压力、迁移和延迟指标。
5. 将段级观测用于容量和诊断，不反推具体 Memory 或 representation 的层级。
6. 对 stale、unknown、not_found 和冲突结果执行安全降级。

#### 3.4 核心输出

```text
PlacementObservation
ResourceState
segment_observation
current_tier
generation
route_epoch
supported_operations
readable
serving_ready
capacity_snapshot
pressure_snapshot
freshness
observation_source
```

### 4. 正常流程

```text
计划前或动作前请求真实观察
↓
接收 P2 返回的 Placement、Segment 和资源快照
↓
校验对象、版本和新鲜度
↓
保存最新观察副本
↓
生成 SchedulingView 输入
↓
供 Placement Decision、Capacity Guard 和 Reconciliation 使用
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| current_tier 或 generation 缺失 | 标记观察不可用于非 Keep 动作 | 等待补齐或重查 |
| 观察版本低于已保存版本 | 丢弃旧观察 | 不覆盖当前事实 |
| Provider 不可用或观察过期 | 保留旧快照并标记 stale/unknown | 只允许查询、对账和 Keep |
| Segment not_found | 记录资源观测缺失 | 不根据缺失信息发动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| PlacementStatePort | 获取真实 current_tier 和 generation | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| SegmentIntrospectionPort | 获取段级容量、压力和健康观测 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| ResourceState | 获取资源范围、预算和迁移状态 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| PLC-001 / SEG-001 | 观察契约和错误语义 | P2 平台接入：高琛 / 高旭；External Integration：陈凯 / 赵旭东 / 谭旭梁 |

### 7. 验收标准

1. C 能获取并保存 current_tier、generation、route_epoch 和 observed_at。
2. stale、unknown 或 not_found 结果不会驱动新的非 Keep 动作。
3. 旧观察不会覆盖更高版本的观察事实。
4. C 不根据自己的动作记录推算 current_tier 或真实容量。
5. 段级统计不会被当作对象级 Placement 事实。
6. 观察能够关联到 representation、provider_ref 和资源范围。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-10-09～2026-10-28`；当前牵头：沈家隆。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D003 | 2026-10-09～2026-10-13 | Placement、Segment、ResourceState 的消费边界 |
| 开发 | D004 | D008 | 2026-10-14～2026-10-20 | 观察适配、快照保存和 SchedulingView 输入 |
| 测试 | D009 | D011 | 2026-10-21～2026-10-23 | stale、unknown、not_found 和乱序测试 |
| 联调 | D012 | D014 | 2026-10-26～2026-10-28 | 与 P2 Placement、Segment 和资源接口联调 |

计划窗口：2026-10-09～2026-10-28；真实 Placement、Segment 和 ResourceState 以 P2 2026-10-16 核心路径基线为准。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 3 |
| 开发 | 5 |
| 测试 | 3 |
| 联调 | 3 |
| **总人天** | **14** |

### 10. 备注

```text
- 原子子功能：C-SUB-08、C-SUB-09；WBS：C-EPIC-05-T1、C-EPIC-05-T2。
- PlacementObservation 和 ResourceState 的权威方是 P2/Provider，C 只保存消费快照和决策证据。
- “计划目标”和“真实层级”必须保持分离。
```

---

## C-EPIC-06 · Placement Decision, Capacity Guard & Admission Control

**Epic 范围**：计算 desired tier，执行阈值、冷却、容量、带宽、并发、预算和 Scope 配额检查，决定动作是否准入。

**关联 PRR Action**：C-P0-1、C-P0-2、C-P1-4。

**Epic 级完成标准**：目标层容量不足不发动作；在线 P99 恶化能够节流或暂停后台动作；规则有版本、有原因、有界且可解释。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-10 | Desired Tier and Supported Operations |
| C-SUB-11 | Cooldown, Capacity Guard and Admission |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-06 |
| Epic Name | Placement Decision, Capacity Guard & Admission Control |
| Feature ID | C-FEAT-06 |
| Feature Name | 放置决策、容量保护与准入 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | P2 公共底座：刘佳正 / 陈晔；P2 平台接入：高琛 / 高旭（资源事实和支持动作）；Provider 联系人待确认；Remember：杨鹏通 / 赵旭东（业务属性）；Shared Runtime：沈家隆 / 杨鹏通（策略与配额） |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为调度平台负责人，我希望在提交动作前统一检查真实层级、支持动作、容量、并发、冷却和配额，从而避免调度震荡、资源超载和后台动作挤占 Working 资源。

### 3. 功能描述

#### 3.1 功能目标

根据 observed tier、hotness、supported_operations 和资源事实计算 desired tier，并在动作生成前执行 Capacity Guard、Cooldown 和 Admission Control。条件不足时输出 Keep / No-op。

#### 3.2 核心输入

```text
representation_id
current_tier
hotness_score
supported_operations
target_generation
policy_version
cooldown_context
manual_override
resource_scope_id
available_capacity
working_reserved_capacity
prewarm_budget
prewarm_used
pin_budget
pin_used
active_migration_count
max_concurrent_migration
migration_bandwidth
read_latency_p99
write_latency_p99
action_cost
priority
```

#### 3.3 核心处理

1. 校验 Placement 和 ResourceState 的新鲜度及 Provider 健康状态。
2. 根据 current tier、hotness 和支持动作计算 desired tier。
3. 计算 Working、Prewarm 和 Pin 的可用余量。
4. 检查冷却、阈值、并发、带宽、优先级、Scope 配额和人工控制。
5. 输出 Allow、Throttle、Pause、Reject New 或 Keep / No-op。
6. 保存 policy_version、decision_reason、资源快照引用和拒绝原因。

#### 3.4 核心输出

```text
desired_tier
decision_reason
policy_version
input_fingerprint
supported_operation_check
cooldown_result
capacity_guard_result
budget_check_result
admission_decision
throttle_level
resource_snapshot_reference
```

### 4. 正常流程

```text
读取 Placement、Hotness 和 ResourceState
↓
计算 desired_tier 和动作成本
↓
执行支持动作、冷却、容量、并发和配额检查
↓
判断 Allow / Throttle / Reject / Keep
↓
记录准入证据
↓
允许通过的候选进入 TierAction.Generated
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| Working 预留不足 | 停止新增低优先级后台动作 | 保护 Working 读写 |
| 迁移并发或带宽达到上限 | Throttle 或 Keep | 不提交超预算动作 |
| ResourceState stale/unknown | 禁止新的非 Keep 动作 | 等待状态恢复或人工处置 |
| 目标动作不被支持 | 记录不支持原因 | 不创建该动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| PlacementObservation | current_tier、generation 和可读性 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| ResourceState | 容量、压力、迁移和延迟事实 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| Representation Hotness | 表示级热度 | C-EPIC-02：沈家隆 |
| PolicyContext | 阈值、冷却、预算和配额规则 | Operator：沈家隆 / 谭旭梁；Shared Runtime：沈家隆 / 杨鹏通 |

### 7. 验收标准

1. 每个决策都能说明 current_tier、desired_tier、policy_version 和 decision_reason。
2. 不支持的动作不会进入 TierAction。
3. 观察过期、generation 不匹配或人工控制阻止时输出 Keep / No-op 或等待。
4. Working、Prewarm 和 Pin 预算能够分别判断。
5. 并发、带宽和 P99 影响能够触发限流或暂停。
6. 同一对象在冷却窗口内不会反复生成相同方向动作。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-10-19～2026-11-13`；当前牵头：沈家隆。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D005 | 2026-10-19～2026-10-23 | 目标层级、准入、预算、冷却和保护规则 |
| 开发 | D006 | D012 | 2026-10-26～2026-11-03 | Decision、Capacity Guard 和 Admission 实现 |
| 测试 | D013 | D017 | 2026-11-04～2026-11-10 | 超载、冷却、配额、降级和 Keep 测试 |
| 联调 | D018 | D020 | 2026-11-11～2026-11-13 | 与 P2 Placement、ResourceState 和支持动作联调 |

计划窗口：2026-10-19～2026-11-13；2026-11-06 前完成调度输入和保护规则冻结，后续只做兼容修正和联调缺陷处理。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 5 |
| 开发 | 7 |
| 测试 | 5 |
| 联调 | 3 |
| **总人天** | **20** |

### 10. 备注

```text
- 原子子功能：C-SUB-10、C-SUB-11；WBS：C-EPIC-06-T1、C-EPIC-06-T2。
- C 计算 desired_tier，但不修改 P2 的 current_tier。
- C 不能根据自己的动作记录推算真实容量，容量事实必须来自 P2/Provider。
```

---

## C-EPIC-07 · TierAction Runtime & Operator Control

**Epic 范围**：管理 TierAction 的状态、幂等、Provider 状态映射和人工控制，区分动作已接收与动作已完成。

**关联 PRR Action**：C-P0-3、C-P0-4、C-P1-5。

**Epic 级完成标准**：Generated、Submitted、Succeeded、Failed、Unknown 五态完整；Accepted 不等于成功；人工控制有权限、幂等、有效期和审计。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-12 | TierAction State Machine |
| C-SUB-13 | Provider State Mapping and Operator Control |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-07 |
| Epic Name | TierAction Runtime & Operator Control |
| Feature ID | C-FEAT-07 |
| Feature Name | TierAction 运行时与人工控制 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | P2 平台接入：高琛 / 高旭（执行状态和控制能力）；Provider 联系人待确认；Shared Runtime：沈家隆 / 杨鹏通（状态目录、权限和审计） |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为调度执行方和运维人员，我希望动作状态、Provider 映射以及暂停、恢复、取消、Pin 和 Force Keep 有统一规则，从而不把已接收误认为已完成，并能在异常时安全接管。

### 3. 功能描述

#### 3.1 功能目标

管理 C 拥有的 `TierAction.ActionState`，映射 P2 的 ACCEPTED、RUNNING、SUCCEEDED、FAILED、UNKNOWN，并管理 `OperatorControlTask` 的控制意图和审计。C 不拥有 P2 的物理执行状态。

#### 3.2 核心输入

```text
action_id
idempotency_key
plan_id
representation_id
provider_ref
actuation_target
action_type
source_tier
desired_tier
expected_generation
policy_version
priority
deadline
provider_task_id
provider_status
actual_tier
result_generation
operator_control_id
control_type
scope
reason
operator_id
effective_from
effective_until
permission_context
```

#### 3.3 核心处理

1. 由有效 Plan 和准入结果生成 TierAction。
2. 按允许转换记录 Generated、Submitted、Succeeded、Failed 或 Unknown。
3. 将 Provider 状态映射到 C 状态，明确 Accepted/Submitted 不等于 Succeeded。
4. 校验 actual_tier、generation、对象引用和结果证据。
5. 校验人工控制人的权限、作用域、有效期和原因。
6. 重试创建新的 action_id，并保存与旧动作的关联。

#### 3.4 核心输出

```text
TierAction
action_id
action_state
provider_task_id
provider_status
actual_tier
result_generation
error_code
operator_control_task
control_state
audit_record
created_at
submitted_at
completed_at
```

### 4. 正常流程

```text
PlacementPlan 通过准入和目标校验
↓
生成 TierAction
↓
提交 P2，进入 Submitted
↓
接收或查询 Provider 状态
↓
校验实际层级和 generation
↓
收口为 Succeeded、Failed 或进入 Unknown 对账
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| P2 返回 ACCEPTED/RUNNING | 保持 Submitted | 不宣告成功 |
| 提交响应丢失或结果无法判断 | 进入 Unknown | 交给 C-EPIC-09 对账 |
| 重复 action_id 或幂等键 | 复用已有动作事实 | 不重复执行 |
| Cancel 不被当前物理阶段支持 | 保留待确认或失败事实 | 不伪造已取消 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| PlacementPlan | 提供动作目标和前置版本 | C-EPIC-03：沈家隆 |
| SubmitTierAction | 接收抽象动作并返回任务状态 | P2 平台接入：高琛 / 高旭；Provider 联系人待确认 |
| QueryActionStatus / Feedback | 查询和反馈动作结果 | P2 平台接入：高琛 / 高旭；Provider 联系人待确认 |
| State Catalog / Audit | 统一状态、权限和审计 | Shared Runtime：沈家隆 / 杨鹏通 |

### 7. 验收标准

1. Generated、Submitted、Succeeded、Failed、Unknown 五态语义和转换完整。
2. ACCEPTED/RUNNING 只能映射为 Submitted。
3. Succeeded 必须同时满足 Provider 成功、actual_tier 正确和 generation 校验通过。
4. Unknown 能关联 action_id 并进入对账。
5. 重复 action_id 或幂等键不会重复执行物理动作。
6. 人工控制必须校验权限、作用域、有效期和原因，并生成审计记录。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-10-26～2026-11-18`；当前牵头：沈家隆。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D004 | 2026-10-26～2026-10-29 | TierAction 状态、Provider 映射和人工控制规则 |
| 开发 | D005 | D010 | 2026-10-30～2026-11-06 | 动作记录、状态机、控制任务和审计 |
| 测试 | D011 | D014 | 2026-11-09～2026-11-12 | 状态转换、幂等、权限和控制限制测试 |
| 联调 | D015 | D018 | 2026-11-13～2026-11-18 | 与 P2 提交、反馈和 Freeze/Unfreeze/Cancel 联调 |

计划窗口：2026-10-26～2026-11-18；Provider 受理、反馈和人工控制的最终语义以 2026-11-13 RC 接口冻结为准。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 6 |
| 测试 | 4 |
| 联调 | 4 |
| **总人天** | **18** |

### 10. 备注

```text
- 原子子功能：C-SUB-12、C-SUB-13；WBS：C-EPIC-07-T1、C-EPIC-07-T2。
- C 拥有 TierAction.ActionState；P2 拥有 Provider 任务和物理执行状态。
- Operator Control 主要影响新的调度准入，已提交动作仍需按反馈和对账规则收敛。
```

---

## C-EPIC-08 · Storage Control Simulator

**Epic 范围**：提供共享 `SimulatedStorageState`，统一模拟 Placement、ActuationTarget、TierAction、Feedback 和 Health 视图，并覆盖 Case 1～6 故障场景。

**关联 PRR Action**：—。

**Epic 级完成标准**：正常模式下各控制视图共享同一状态；六类故障可重复注入；模拟器能验证状态机、幂等和对账，但不替代真实 P2 迁移验收。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-14 | Unified Storage Control Simulator |
| C-SUB-15 | Simulator Case 1-6 Fault Suite |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-08 |
| Epic Name | Storage Control Simulator |
| Feature ID | C-FEAT-08 |
| Feature Name | 统一存储控制模拟器与故障套件 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | Operator：沈家隆 / 谭旭梁；P2 平台接入：高琛 / 高旭（模拟适配）；Provider 联系人待确认；环境搭建与验收：陈凯 / 肖宇 |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为 C 的开发、测试和验收人员，我希望多个控制能力共享一个可恢复的模拟存储状态，并能重复注入典型故障，从而在真实 P2 尚未就绪时验证调度闭环。

### 3. 功能描述

#### 3.1 功能目标

建设测试环境的 `SimulatedStorageState` 和统一 Control Port 视图，覆盖正常成功、失败、反馈丢失、超时、重启、重复动作和 desired/observed 不一致。

#### 3.2 核心输入

```text
representation_id
provider_ref
current_tier
generation
action_id
idempotency_key
desired_tier
provider_task_id
feedback_status
fault_mode
restart_marker
case_id
```

#### 3.3 核心处理

1. 保存对象当前层级、generation、动作记录、反馈记录和查询记录。
2. 让 Placement、Target、Action、Feedback 和 Health 视图读取同一模拟状态。
3. 正常模式下成功动作更新 current_tier 和 generation。
4. Fault Mode 注入延迟、失败、反馈丢失、重启和重复提交。
5. 驱动 C 的 Query、Reconciliation 和 Retry 逻辑，验证最终收口。
6. 输出可重放的测试证据，不把模拟结果当成真实 P2 事实。

#### 3.4 核心输出

```text
SimulatedStorageState
PlacementObservation
ActuationTarget
SubmitResult
ExecutionFeedback
ActionStatus
resource_health_snapshot
case_execution_record
query_history
assertion_result
```

### 4. 正常流程

```text
测试提交抽象 TierAction
↓
模拟器记录 action_id 和幂等键
↓
返回 ACCEPTED / RUNNING
↓
模拟执行并更新层级与 generation
↓
返回 SUCCEEDED 和反馈
↓
C 查询 Placement 并完成断言
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 真实 P2 尚未就绪 | 使用共享模拟状态验证控制面 | Lane 2 可先行 |
| 故障注入器不可用 | 标记测试未完成并保存现场 | 该 Case 需要重跑 |
| 模拟器重启 | 从持久化或独立进程状态恢复 | 验证动作不重复 |
| 模拟反馈与 Placement 矛盾 | 保留矛盾证据并进入对账 | 验证 C 不盲信单一反馈 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| C Control Ports | 被模拟的统一控制接口 | Operator：沈家隆 / 谭旭梁 |
| SimulatedStorageState | 共享模拟世界状态 | Operator：沈家隆 / 谭旭梁；环境搭建与验收：陈凯 / 肖宇 |
| TierAction / Reconciliation | 被验证的状态机和收口能力 | C-EPIC-07：沈家隆 / 谭旭梁；C-EPIC-09：谭旭梁 |
| Test Driver | 故障注入、重启和断言 | 环境搭建与验收：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁 |

### 7. 验收标准

1. Placement、Target、Action、Feedback 和 Health 视图读取同一模拟状态。
2. 正常成功路径会更新 current_tier 和 generation。
3. 重复 action_id 不重复执行。
4. 模拟状态可以在重启后恢复。
5. Case 1～6 能被重复注入、执行和复盘。
6. 模拟器只验证 C 控制面，不被当作真实 P2 或物理迁移验收依据。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-09-09～2026-10-16`；当前牵头：谭旭梁。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D003 | 2026-09-09～2026-09-11 | 共享状态、Control Port 视图和故障模型 |
| 开发 | D004 | D011 | 2026-09-14～2026-09-24 | Simulator、状态持久化和测试驱动 |
| 测试 | D012 | D017 | 2026-09-28～2026-10-09 | 正常路径和 Case 1～6 Fault Test |
| 联调 | D018 | D020 | 2026-10-12～2026-10-16 | 与 C 状态机、查询和对账能力联调 |

计划窗口：2026-09-09～2026-10-16；模拟器先行提供 Lane 2 验证，不能替代后续真实 P2 迁移验收。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 3 |
| 开发 | 8 |
| 测试 | 6 |
| 联调 | 3 |
| **总人天** | **20** |

### 10. 备注

```text
- 原子子功能：C-SUB-14、C-SUB-15；WBS：C-EPIC-08-T1、C-EPIC-08-T2。
- SimulatedStorageState 仅用于测试环境；生产 current_tier 和 generation 由 P2/Provider 提供。
- 共享模拟器框架只开发一次，六类故障用例复用同一状态。
```

---

## C-EPIC-09 · TierAction / Placement Reconciliation & Migration Safety

**Epic 范围**：处理反馈丢失、Unknown、stale Placement、generation mismatch、P2 重启和重试恢复，确保动作先核实再收敛。本 Epic 只负责 Operate 领域的业务对账；通用对账任务的持久化、租约、重试调度、恢复和审计由 Shared Runtime Foundation 提供。

**关联 PRR Action**：C-P0-5、C-P1-1、C-P1-2、C-P1-5。

**Epic 级完成标准**：Unknown 先查询不盲重试；Action、Plan、Feedback 和 Placement 能对账；重启后不重复动作；只有确认原动作未产生物理效果后才新建 action_id。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-16 | Unknown / Stale / Missing Feedback Reconciliation |
| C-SUB-17 | Query / Retry Policy |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-09 |
| Epic Name | TierAction / Placement Reconciliation & Migration Safety |
| Feature ID | C-FEAT-09 |
| Feature Name | TierAction、Placement 业务对账与迁移安全 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | P2 平台接入：高琛 / 高旭（状态查询和反馈）；P2 公共底座：刘佳正 / 陈晔（放置观察）；Provider 联系人待确认；Shared Runtime：沈家隆 / 杨鹏通（Reconciliation Runtime 的持久任务、租约、重试、恢复和审计机制） |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为调度闭环负责人，我希望反馈丢失、Placement 过期或 generation 不一致时能够核实真实状态，并按预算安全重试，从而避免错误成功和重复物理动作。

### 3. 功能描述

#### 3.1 功能目标

使用 Shared Runtime 提供的 `ReconciliationTask` 运行机制，针对 TierAction Unknown、反馈丢失、stale Placement、generation mismatch 和 Action/Observation 不一致，完成调度领域的查询、比较、结果收口和重试决策。C 不重复建设通用任务、租约、重试和恢复框架。

#### 3.2 核心输入

```text
reconciliation_task_id
action_id
plan_id
provider_task_id
provider_ref
expected_generation
actual_tier
desired_tier
route_epoch
provider_status
observation_id
feedback_id
retry_of_action_id
retry_count
retry_budget
backoff_context
restart_marker
reconciliation_reason
```

以上字段分为两部分：任务状态、租约、尝试次数、下次执行时间和审计关联由 Shared Runtime 统一维护；`action_id`、`plan_id`、期望与实际层级、generation、比较结果和收口决策由 C 按调度业务语义提供或判断。

#### 3.3 核心处理

1. C 发现 Unknown、反馈丢失、观察过期或版本冲突时，按 Shared Runtime 契约登记一项由 C 负责业务收口的对账任务。
2. Shared Runtime 负责持久保存任务、取得租约并调度查询；C 提供查询所需的 action_id，并通过 P2 查询原动作和最新 Placement。
3. C 比较 Action、Plan、Feedback 和 Observation 的对象、层级、generation、route_epoch。
4. C 判断是否达到目标：已确认达到目标时收口为 Succeeded；明确未执行时进入 Failed 或允许重新计划。
5. 证据不足时由 Shared Runtime 保持任务可恢复状态，C 不直接发起新的物理动作。
6. 只有 C 确认原动作未产生物理效果后，才允许在 Shared Runtime 的预算和租约约束下创建新的 action_id。

#### 3.4 核心输出

```text
ReconciliationTask
reconciliation_state
query_result
latest_placement_observation
comparison_result
convergence_decision
retry_decision
retry_budget_remaining
next_retry_at
new_action_id
retry_of_action_id
evidence
```

### 4. 正常流程

```text
发现 TierAction Unknown 或 Placement 观察不一致
↓
C 按统一契约登记 ReconciliationTask
↓
Shared Runtime 调度查询；C 查询原 action_id 和最新 Placement
↓
C 比较层级、generation、route_epoch 和对象引用
↓
C 收口原 TierAction
↓
确认未执行后，在统一预算约束下创建新 action_id
↓
新动作重新走完整准入和提交流程
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| Provider 查询仍无结论 | 保持 Waiting / Running | TierAction 继续 Unknown |
| Feedback 与 Placement 冲突 | 保留冲突证据并继续核验 | 不宣告成功 |
| 重试预算用尽 | 停止自动重试并告警 | 等待人工或下一轮计划 |
| C 或 P2 重启 | Shared Runtime 恢复未终态任务；C 先查询原动作 | 不重复提交原动作 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| QueryActionStatus | 按 action_id 查询原动作 | P2 平台接入：高琛 / 高旭；Provider 联系人待确认 |
| PlacementObservation | 获取真实层级和版本 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| ExecutionFeedback | 获取动作过程和结果证据 | P2 平台接入：高琛 / 高旭；Provider 联系人待确认 |
| Reconciliation Runtime | 持久任务、租约、恢复、退避、预算和审计 | Shared Runtime：沈家隆 / 杨鹏通 |
| TierAction | 被对账和重试的动作记录 | C-EPIC-07：沈家隆 / 谭旭梁 |

### 7. 验收标准

1. Unknown、反馈丢失、stale Placement 和 generation mismatch 都能创建对账任务。
2. 对账先查询原 action_id，不盲目创建新的 action_id。
3. 实际层级达到目标且 generation 校验通过时，原动作可收口为 Succeeded。
4. 证据不足时不错误宣告成功或失败。
5. 每次重试都有 retry_of_action_id、预算、退避和原因记录。
6. C 重启后未终态动作可恢复，且不重复执行。
7. ReconciliationTask 的任务状态由 Shared Runtime 统一维护，C 的 comparison_result、convergence_decision 和 retry_decision 作为业务结果单独记录，二者不混为一个状态。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-10-19～2026-11-20`；当前牵头：谭旭梁。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D004 | 2026-10-19～2026-10-22 | 对账条件、收口规则、查询和重试预算 |
| 开发 | D005 | D010 | 2026-10-23～2026-11-02 | C 的对账业务规则、查询适配、结果收口和动作重试决策；公共运行时由 Shared Runtime 提供 |
| 测试 | D011 | D015 | 2026-11-03～2026-11-09 | Unknown、stale、冲突、重启和预算测试 |
| 联调 | D016 | D018 | 2026-11-10～2026-11-13 | 与 P2 查询、Placement 和 Feedback 联调 |

计划窗口：2026-10-19～2026-11-20；2026-11-14～11-20 用于缺陷整改、功能冻结前复验和对账证据整理，不新增人天。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 6 |
| 测试 | 5 |
| 联调 | 3 |
| **总人天** | **18** |

### 10. 备注

```text
- 原子子功能：C-SUB-16、C-SUB-17；WBS：C-EPIC-09-T1、C-EPIC-09-T2。
- C 负责 TierAction 与 Placement 的业务比较、结果收口和是否重计划的判断；Shared Runtime 负责 ReconciliationTask 的运行状态和公共恢复机制。
- ReconciliationTask 负责查询、比较和收敛，不因查询本身直接发起迁移。
- 重试不是直接再次提交的授权；旧 TierAction 历史不可被原地改写。
```

---

## C-EPIC-10 · Optimize / Predict & Controlled Release

**Epic 范围**：建立启发式基线、特征与策略版本契约，接入预测、回退和效果测量，支持 Shadow、Canary、Progressive 和 Rollback。

**关联 PRR Action**：C-P0-6。

**Epic 级完成标准**：同版本同输入的 Heuristic 决策可复现；预测异常时按稳定版本、Heuristic、Keep/No-op 回退；命中率和资源影响可审计；不直接跳到 RL 或 Provider-specific action。

### 子功能映射

| 子功能编号 | 主要内容 |
|---|---|
| C-SUB-18 | Frozen Heuristic Baseline |
| C-SUB-19 | Feature Contract and Policy Version |
| C-SUB-20 | Prediction, Fallback and Measurement |

### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | C-EPIC-10 |
| Epic Name | Optimize / Predict & Controlled Release |
| Feature ID | C-FEAT-10 |
| Feature Name | 优化、预测与受控发布 |
| Owner | 沈家隆 / 谭旭梁（Operator） |
| Contributor | Recall：陈凯 / 肖宇（访问事实）；Remember：杨鹏通 / 赵旭东（记忆事实）；P2 公共底座：刘佳正 / 陈晔；P2 可观测与验收：杨文博 / 王广诚（资源和执行反馈）；Provider 联系人待确认；Shared Runtime：沈家隆 / 杨鹏通（版本和发布治理）；环境搭建与验收：陈凯 / 肖宇 |
| 目标里程碑 | MVP · 2026-11-30 |

### 2. User Story

作为调度优化负责人，我希望预测策略有稳定基线、版本和完整回退链，并能用真实访问和执行结果评估收益，从而控制发布风险并判断策略是否带来实际调度效果。

### 3. 功能描述

#### 3.1 功能目标

冻结第一版 Heuristic，定义 feature/label/policy/model 版本，接入预测并建立“上一稳定版本 → Heuristic → Keep / No-op”的回退链，测量命中率、预热收益、资源影响和收敛情况。

#### 3.2 核心输入

```text
representation_id
feature_snapshot_reference
label_snapshot_reference
prediction_result
prediction_score
prediction_window
model_version
policy_version
heuristic_result
previous_stable_result
current_tier
observed_tier
access_outcome
hit_after_prefetch
resource_impact
release_stage
release_scope
```

#### 3.3 核心处理

1. 固定基线输入、权重、阈值、窗口和冷却规则。
2. 校验特征、标签、模型、策略版本和预测窗口。
3. 按 Shadow → Canary → Progressive 控制策略放量。
4. 对预测结果执行资源、版本、冲突和动作可执行性检查。
5. 预测异常时回退到上一稳定版本，再回退到 Heuristic，最后 Keep / No-op。
6. 将可用结果交给 PlacementPlan，不直接调用 Provider-specific action。
7. 使用真实 AccessTrace、ExecutionFeedback 和 Placement 评估命中率、预热收益、资源影响和对账收敛。

#### 3.4 核心输出

```text
heuristic_decision
prediction_decision
fallback_level
selected_policy_version
selected_model_version
policy_release_record
decision_reason
hit_rate_measurement
prewarm_effectiveness
resource_impact_measurement
evaluation_report
rollback_reason
```

### 4. 正常流程

```text
准备版本化特征、标签、模型和策略
↓
执行 Heuristic 或预测并校验结果
↓
进入 Shadow，再按范围进入 Canary / Progressive
↓
通过准入检查后生成 PlacementPlan
↓
预测异常时回退到上一稳定版本
↓
上一稳定版本不可用时回退到 Heuristic
↓
全部不可用时 Keep / No-op
↓
用真实访问、反馈和 Placement 评估效果
```

### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 特征、标签或策略版本不匹配 | 阻止发布或决策 | 不产生无版本结果 |
| 模型或预测服务异常 | 回退到上一稳定版本 | 不因模型异常发新动作 |
| 上一稳定版本不可用 | 使用 Frozen Heuristic | 保持可解释基线决策 |
| 所有决策输入不足 | 输出 Keep / No-op | 不进行不确定调度 |
| Canary 指标回归 | 停止放量并回退 | 不扩大影响范围 |

### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| Representation Hotness / PlacementPlan | 热度、目标和决策承载 | C-EPIC-02：沈家隆；C-EPIC-03：沈家隆 |
| AccessTrace / MemorySignal | 访问和业务变化事实 | AccessTrace：Recall：陈凯 / 肖宇、实际观测组件联系人待确认；MemorySignal：Remember：杨鹏通 / 赵旭东；C-EPIC-01：沈家隆 |
| Placement / Feedback / ResourceState | 真实结果和资源影响 | P2 公共底座：刘佳正 / 陈晔；Provider 联系人待确认 |
| Policy / Model Registry | 版本保存、读取和发布 | Operator：沈家隆 / 谭旭梁；Shared Runtime：沈家隆 / 杨鹏通 |
| Heuristic Baseline | 可复现的安全回退 | Operator：沈家隆 / 谭旭梁 |

### 7. 验收标准

1. 相同 policy_version 和相同输入得到一致的 Heuristic 决策。
2. 每个预测决策能够关联 feature、label、model 和 policy 版本。
3. 预测异常时能按“上一稳定版本 → Heuristic → Keep / No-op”顺序回退。
4. 回退不会删除历史 Plan、TierAction 或审计记录。
5. 预测不会绕过 PlacementPlan、ActuationTarget 和准入检查直接提交动作。
6. 命中率、预热命中、资源影响和调度结果能够关联到版本和 action_id。
7. Shadow、Canary、Progressive 和 Rollback 均有可执行步骤和证据。

### 8. 计划

本 Epic 从 `D001` 独立计算，阶段仅累计本 Epic 的有效投入日；项目窗口为 `2026-11-06～2026-11-27`；当前牵头：沈家隆。

| 阶段 | 开始时间（Epic 内） | 完成时间（Epic 内） | 项目日期 | 主要输出 |
|---|---|---|---|---|
| 设计 | D001 | D003 | 2026-11-06～2026-11-10 | Heuristic、特征/标签、版本、回退和测量口径 |
| 开发 | D004 | D008 | 2026-11-11～2026-11-17 | 基线、版本治理、预测接入和评估能力 |
| 测试 | D009 | D012 | 2026-11-18～2026-11-23 | 复现、版本漂移、模型异常和效果测量测试 |
| 联调 | D013 | D014 | 2026-11-24～2026-11-25 | 与访问、资源、反馈和端到端调度联调 |

计划窗口：2026-11-06～2026-11-27；2026-11-20 后不新增功能，2026-11-26～11-27 用于集中验收、缺陷整改和发布证据整理。

### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 3 |
| 开发 | 5 |
| 测试 | 4 |
| 联调 | 2 |
| **总人天** | **14** |

### 10. 备注

```text
- 原子子功能：C-SUB-18、C-SUB-19、C-SUB-20；WBS：C-EPIC-10-T1、C-EPIC-10-T2、C-EPIC-10-T3。
- 第一版先建立可复现 Heuristic，不直接从第一版跳到 RL。
- 预测结果只能进入 C 的计划和准入流程，不能直接跳到 P2 动作。
- 目标收益和正式发布日期仍需项目负责人确认。
```

---

## C 组统一责任边界

```text
C：MemorySignal / AccessTrace 消费、representation 热度、PlacementPlan、TierAction、准入、调度域业务对账和预测策略；负责比较 Action 与 Placement 并决定收口或重计划。
B：MemoryRecord、MemorySignal 主事实、生命周期和语义属性。
Recall：负责 Recall 最终结果并提供访问阶段事实；实际观测组件产生对应的 AccessTrace 事实，联系人待确认。
P2 / Provider：PlacementObservation、ResourceState、ExecutionFeedback、Provider 任务和物理迁移。
Shared Runtime：统一状态目录、投递、重放、持久任务、Reconciliation Runtime、权限、审计和公共契约治理；不替 C 做 Action 与 Placement 的业务判断。
```

## C 组统一验收链

```text
MemorySignal / AccessTrace / Placement / Resource
  -> Representation Hotness
  -> RepresentationPlacementPlan
  -> ActuationTarget
  -> TierAction
  -> Provider Execution
  -> ExecutionFeedback + PlacementObservation
  -> Shared Runtime ReconciliationTask + C 业务对账
  -> 下一轮重新评估
```

统一要求：Plan 是期望状态，PlacementObservation 是真实观察，TierAction 是一次动作记录；Accepted/Submitted 不等于 Succeeded；Unknown 必须先 Query 和 Reconciliation，重试必须创建新的 `action_id`。

---

# 第四章 Shared Runtime Foundation

# AetherStore P3 · Shared Runtime Foundation 负责人功能填报 V0.2

**章节定位**：Shared Runtime Foundation 是 A、B、C、P2 共用的运行时基础。本章节将 V0.3 生命周期设计拆为三个可独立验收的 Feature：状态目录、Signal/Telemetry 标准、可观测关联模型。三项交付共同保证统一规则，但不接管 `MemoryRecord`、`AccessTrace`、`PlacementPlan` 或 Provider 物理状态的业务所有权。

**责任人**：沈家隆 / 杨鹏通。External Integration 由陈凯 / 赵旭东 / 谭旭梁协助，环境搭建与验收由陈凯 / 肖宇协助。

## SRF 的最终 Feature 结构

| Epic ID | Epic Name | Feature ID | Feature Name | 独立交付物 | 估算人天 | 项目窗口 |
|---|---|---|---|---|---:|---|
| SRF-EPIC-01 | Shared Runtime Foundation | SRF-FEAT-01 | P3 State Catalog | [P3_State_Catalog_V0.1.md](../业务对象生命周期/P3_State_Catalog_V0.1.md) | 14 | 2026-09-09～2026-11-06 |
| SRF-EPIC-01 | Shared Runtime Foundation | SRF-FEAT-02 | P3 Signal / Telemetry Standard | [P3_Signal_and_Telemetry_Standard_V0.1.md](../业务对象生命周期/P3_Signal_and_Telemetry_Standard_V0.1.md) | 16 | 2026-09-09～2026-11-13 |
| SRF-EPIC-01 | Shared Runtime Foundation | SRF-FEAT-03 | P3 Observability Correlation Model | [P3_Observability_Correlation_Model_V0.1.md](../业务对象生命周期/P3_Observability_Correlation_Model_V0.1.md) | 14 | 2026-10-09～2026-11-27 |
| **合计** |  |  |  | 3 份独立交付物 | **44** | 2026-09-09～2026-11-27 |

## 计划与人天口径

- **计划**：三个 Feature 按项目实际日期并行推进；SRF-FEAT-01 负责对象和状态语义，SRF-FEAT-02 负责事件与遥测语义，SRF-FEAT-03 在前两项初稿稳定后负责端到端关联查询和告警。2026-11-28～11-30 用于整体缓冲、复验和 MVP 交付。
- **人天**：为 Shared Runtime Foundation 横向公共能力的初估，包含设计、开发、测试和联调，不回算历史已投入；三个 Feature 的总量为 **44 人天＝设计 12＋开发 16＋测试 10＋联调 6**。公共状态、事件、投递、任务、关联和审计能力只计算一次，不重复计入 Remember、Recall、Operate 或 P2 的业务人天。
- **团队配置**：2 人（沈家隆 / 杨鹏通）；两位责任人共同负责三个 Feature，不把 44 人天拆分到个人，也不把跨组协作者的配合投入重复计入 SRF。
- **负责人**：沈家隆 / 杨鹏通对 Shared Runtime Foundation 整体交付负责；Remember、Recall、Operator、P2 和环境验收团队保留各自业务与验证职责，SRF 不替业务 Owner 做业务判断。
- **状态均为规划基线，不代表已签收。** Feature 内不使用 D 序号，计划日期直接采用项目实际日期，避免把公共设计误解为某个业务 Epic 的投入。

### 责任人与并行关系

沈家隆 / 杨鹏通共同负责 SRF 的三个 Feature；Feature 的功能人天按交付物归集，公共能力不在 Feature 之间重复计算。

| 责任域 | 责任人 | 负责 Feature | 功能投入（人天） |
|---|---|---|---:|
| Shared Runtime Foundation | 沈家隆 / 杨鹏通 | SRF-FEAT-01～SRF-FEAT-03 | **44** |
| 合计 | 2 人 | 3 个 Feature | **44** |

- SRF-FEAT-01 与 SRF-FEAT-02 可以并行设计和开发；两者共同提供 SRF-FEAT-03 所需的对象、状态、事件和关联字段基础。
- Remember、Recall、Operator 和 P2 分别提供业务对象及真实事实，SRF 负责公共规则、投递、任务、关联和审计能力的统一交接。
- 两位责任人可在多个 Feature 间穿插推进，但同一工作日不重复占用；个人不单独拆分人天，整体工作量仍为 44 人天。

### 项目交付窗口

SRF 项目窗口为 2026-09-09～2026-11-30；统一里程碑见文档开头。各 Feature 的计划表保留实际日期，三个 Feature 可以按依赖并行推进。

| 项目日期 | SRF 节点 | SRF 侧主要责任输出 | 依赖 / 出口 |
|---|---|---|---|
| 2026-09-09 | 项目启动与公共对象对齐 | 明确对象目录、状态维度、事件封套、关联字段和各方 Owner | 沈家隆 / 杨鹏通建立联合工作流；A/B/C/P2 提供对象清单 |
| 2026-09-18 | 核心契约 v0.1 冻结 | 冻结 State Catalog 初稿、Event Envelope、Signal / Telemetry 字段和公共关联键 | Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁；P2 负责人确认 |
| 2026-10-16 | 公共底座与核心路径基线 | 完成状态校验、事件投递、去重、重放和基础关联查询能力 | A/B/C/P2 按统一契约接入，形成公共能力基线 |
| 2026-11-06 | 状态、版本和对账输入冻结 | 完成 State Catalog 验收，冻结跨组状态映射、版本和完成证据 | A/B/C/P2 完成状态映射和证据核对 |
| 2026-11-13 | RC 公共接口冻结 | 完成 Signal / Telemetry、Ack、重放、任务和关联接口核对 | 进入集中联调和端到端验收准备 |
| 2026-11-20 | SRF 功能冻结 | 完成三个 Feature 的功能、测试证据和回退方案 | 阻断新增公共能力，遗留问题进入集中修复 |
| 2026-11-23～11-27 | 集中联调与端到端验收 | 验证形成、Recall、调度、P2 反馈和对账链路的统一关联 | 环境搭建与验收：陈凯 / 肖宇组织；各业务 Owner 提供证据 |
| 2026-11-28～11-30 | 缓冲、复验与交付 | 复验修复项，归档公共契约、状态、事件、任务和关联查询材料 | MVP 交付 |

### 阶段人天汇总

| 阶段 | 人天 | 说明 |
|---|---:|---|
| 设计 | 12 | State Catalog、Event Envelope、Signal / Telemetry、关联字段和查询视图设计 |
| 开发 | 16 | 状态校验、投递确认、去重重放、任务接管、关联查询和审计能力实现 |
| 测试 | 10 | 状态转换、重复/乱序、重启、Unknown、删除竞态和关联完整性验证 |
| 联调 | 6 | 与 Remember、Recall、Operator、P2 和环境验收的接口与端到端验证 |
| **合计** | **44** | 与三个 Feature 合计一致 |

### 跨组依赖联系人

| 依赖类别 | 具体依赖 | 对接联系人 | 最迟形成时间 |
|---|---|---|---|
| Remember 业务事实 | MemoryRecord、MemorySignal、版本、生命周期和删除屏障 | 杨鹏通 / 赵旭东（Remember） | 2026-09-18 |
| Recall 访问事实 | Recall 请求、候选、ContextPack 和 AccessTrace | 陈凯 / 肖宇（Recall） | 2026-09-18 |
| Operator 业务对象 | PlacementPlan、TierAction、对账结果和策略版本 | 沈家隆 / 谭旭梁（Operator） | 2026-09-18 |
| P2 / Provider 事实 | Placement、ResourceState、ExecutionFeedback 和 Provider task | 刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文（P2） | 2026-11-06 |
| 环境与验收 | 联调环境、故障演练、验收证据和交付归档 | 陈凯 / 肖宇 | 2026-11-23 |

---

## SRF-FEAT-01 · P3 State Catalog

**Feature 范围**：对象总目录、业务权威、身份和版本规则、状态维度、允许转换、完成证据、跨对象影响和删除/对账边界。

**Feature 级完成标准**：A、B、C、P2 对同一对象、状态、版本、终态和完成证据使用同一解释；任何对象都有唯一业务权威，旧版本和迟到结果不能覆盖当前事实。

#### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | SRF-EPIC-01 |
| Epic Name | Shared Runtime Foundation |
| Feature ID | SRF-FEAT-01 |
| Feature Name | P3 State Catalog |
| Owner | 沈家隆 / 杨鹏通（Shared Runtime Foundation） |
| Contributor | Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁；P2：刘佳正 / 陈晔 |
| 目标里程碑 | 2026-11-06 状态和对账输入冻结 |

#### 2. User Story

作为 P3 各业务流和 P2 的负责人，我希望所有业务对象都有统一的身份、版本、权威方、状态和完成证据定义，从而不会出现三套互相冲突的生命周期管理方式。

**Story 拆解**

- S1 作为业务 Owner，我希望知道谁能写入某个对象，从而避免多个主事实。
- S2 作为跨组消费者，我希望知道状态和版本的准确含义，从而正确处理 Pending、Succeeded、Failed 和 Unknown。
- S3 作为运维和验收方，我希望按对象和动作查询完整证据，从而定位状态不一致和恢复责任。

#### 3. 功能描述

##### 3.1 功能目标

建立 P3 对象目录和状态语义目录，覆盖 Remember、Recall、Operate、P2 / Provider 以及公共任务、投递、审计、删除和清理记录。

##### 3.2 核心输入

- 各领域对象定义、稳定身份、Scope、版本规则和业务权威；
- `MemoryRecord`、`ProjectionState`、`RecallRequest`、`AccessTrace`、`PlacementPlan`、`TierAction`、`PlacementObservation`、`ResourceState` 和 `ExecutionFeedback` 的状态需求；
- `AsyncTask`、`ReconciliationTask`、`DeliveryRecord`、`AuditRecord`、`Tombstone` 和 `CleanupRecord` 的公共生命周期需求；
- 版本更新、删除、共享目标、迟到写回和 Unknown 场景。

##### 3.3 核心处理

1. 登记对象的身份、版本、Scope、权威方、写入者、消费者和查询入口。
2. 将状态拆为业务有效性、版本当前性、任务执行、事件投递、外部动作、观察新鲜度和清理进度等维度。
3. 为每个对象定义允许转换、终态、转换条件、完成证据和异常处理。
4. 定义版本更新、删除、失效、动作冲突和共享物理目标的影响矩阵。
5. 定义状态变更与事件、任务、审计、对账和清理记录的关联规则。

##### 3.4 核心输出

- 对象总目录和业务权威矩阵；
- 状态语义、转换矩阵和完成证据表；
- 跨对象影响、删除、版本和对账规则；
- 统一查询所需的对象关系和审计引用规则。

#### 4. 正常流程

收集业务对象定义 → 登记身份、版本和权威方 → 定义状态维度及转换 → 绑定完成证据和异常处理 → 评审跨对象影响矩阵 → 发布 State Catalog → A/B/C/P2 按目录接入。

#### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 两个团队声明同一对象为主事实 | 暂停发布冲突定义，要求明确唯一权威 | 不产生双主事实 |
| 状态名称相同但完成证据不同 | 分开对象语义并补充证据，不用名称强行合并 | 状态含义可解释 |
| 旧版本结果迟到 | 按版本和状态修订拒绝写回并记录审计 | 当前版本不被覆盖 |
| 删除与投影/动作并发 | 先启用屏障，逐项登记清理和在途责任 | 失效对象不被重新发布 |
| 物理观察缺失 | 标记观察未知或过期，禁止依赖它的新动作 | 不伪造 current_tier 或成功 |

#### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| Remember 对象和生命周期 | `MemoryRecord`、版本、Projection 资格、删除和 `MemorySignal` | 杨鹏通 / 赵旭东 |
| Recall 对象和访问阶段 | Recall 请求、候选、ContextPack 和 `AccessTrace` | 陈凯 / 肖宇 |
| Operate 对象和动作语义 | `SchedulingView`、`PlacementPlan`、`TierAction`、对账规则 | 沈家隆 / 谭旭梁 |
| P2 / Provider 事实 | 物理放置、资源、反馈和 Provider 任务状态 | 刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文 |
| 公共契约评审 | 版本、幂等、权限、任务和审计约束 | 沈家隆 / 杨鹏通 |

#### 7. 验收标准

1. 目录覆盖所有跨组对象，并明确唯一权威、写入者、消费者、版本和查询入口。
2. `MemoryRecord`、`ProjectionState`、`RecallRequest`、`TierAction`、`DeliveryRecord`、`AsyncTask` 和 `ReconciliationTask` 的状态转换可执行、可查询。
3. `Submitted`、`Succeeded`、`Failed` 和 `Unknown` 有不同的完成证据，迟到结果不能直接回退终态。
4. 版本更新、删除、失效、共享引用和物理目标冲突均有影响矩阵。
5. A、B、C、P2 评审通过同一版 State Catalog，未出现第二套冲突定义。

#### 8. 计划

| 阶段 | 开始时间 | 完成时间 | 主要输出 | 责任人 |
|---|---|---|---|---|
| 设计 | 2026-09-09 | 2026-09-18 | 对象目录、状态维度、权威和转换初稿 | 沈家隆 / 杨鹏通 |
| 开发 | 2026-09-21 | 2026-10-16 | 目录、状态校验和关系查询实现 | 沈家隆 / 杨鹏通 |
| 测试 | 2026-10-19 | 2026-10-30 | 版本、乱序、删除、冲突和 Unknown 用例 | 沈家隆 / 杨鹏通；陈凯 / 肖宇协作 |
| 联调 | 2026-11-02 | 2026-11-06 | A/B/C/P2 状态映射和完成证据验收 | 沈家隆 / 杨鹏通；杨鹏通 / 赵旭东；陈凯 / 肖宇；沈家隆 / 谭旭梁；刘佳正 / 陈晔；张晋 / 胡孝阳；杨文博；徐博文；高琛 / 高旭 |

#### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 5 |
| 测试 | 3 |
| 联调 | 2 |
| **总人天** | **14** |

#### 10. 备注

- 本 Feature 交付物为 [P3_State_Catalog_V0.1.md](../业务对象生命周期/P3_State_Catalog_V0.1.md)。
- 本 Feature 统一规则和公共查询能力，不替 B 判断记忆是否有效，不替 A 判断上下文是否可信，不替 C 判断是否调层，不替 P2 判断物理执行结果。

---

## SRF-FEAT-02 · P3 Signal / Telemetry Standard

**Feature 范围**：`MemorySignal`、`AccessTrace`、`PlacementObservation`、`ResourceState`、`ExecutionFeedback`、Event Envelope、投递确认、幂等、重放以及四类观测记录的边界。

**Feature 级完成标准**：所有跨组事件都能说明 Producer、Authority、对象、版本、时间、关联和证据；事件事实、投递状态、技术 Trace 和审计记录彼此不混淆。

#### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | SRF-EPIC-01 |
| Epic Name | Shared Runtime Foundation |
| Feature ID | SRF-FEAT-02 |
| Feature Name | P3 Signal / Telemetry Standard |
| Owner | 沈家隆 / 杨鹏通（Shared Runtime Foundation） |
| Contributor | Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁；P2：刘佳正 / 陈晔 |
| 目标里程碑 | 2026-11-13 RC 公共接口冻结 |

#### 2. User Story

作为 A、B、C、P2 的交接负责人，我希望 Signal 和 Telemetry 的产生方、含义、投递、确认和关联方式统一，从而知道什么是真实业务事实，什么只是技术调用记录。

**Story 拆解**

- S1 作为消费者，我希望重复事件可以去重、乱序事件可以识别，从而不重复计算和调度。
- S2 作为 C，我希望区分 Search Hit、正文加载和 Context 交付，从而只用真实访问事实计算热度。
- S3 作为运维，我希望回包丢失后可以重放和查询，从而不把 Unknown 伪装成成功。

#### 3. 功能描述

##### 3.1 功能目标

定义公共 Event Envelope、MemorySignal、AccessTrace、PlacementObservation、ResourceState、ExecutionFeedback、DeliveryRecord 及 OTel Trace、Runtime Log、AuditRecord 的字段含义和交接层次。

##### 3.2 核心输入

- B 提交的记忆、版本、生命周期、资格和删除事实；
- A / Recall 真实产生的检索、校验、加载、选择和上下文交付阶段；
- P2 / Provider 的放置、资源、任务、反馈和查询事实；
- 请求、Scope、版本、幂等、事件序号、时间和证据关联要求。

##### 3.3 核心处理

1. 统一事件外层字段：事件身份、类型、Schema 版本、Producer、Authority、对象、版本、时间和关联引用。
2. 规定 `MemorySignal` 是 B 已提交变化的通知，不是 C 或 P2 的调度命令。
3. 规定 `AccessTrace` 只记录真实发生的 `retrieved`、`validated`、`loaded`、`selected`、`used_in_context` 阶段。
4. 规定 Provider 反馈和放置观察必须保留来源、代次、观察时间和证据，Accepted 不等于完成。
5. 规定 Outbox、Inbox、DeliveryRecord、Ack、重放、去重、乱序、缺口和超出预算后的人工处理。
6. 区分业务访问事实、技术调用链、诊断日志和审计责任记录。

##### 3.4 核心输出

- Event Envelope 和事件类型标准；
- MemorySignal、AccessTrace、PlacementObservation、ResourceState、ExecutionFeedback 字段语义表；
- 投递、确认、补发、重放、幂等和乱序规则；
- AccessTrace、OTel Trace、Runtime Log 和 AuditRecord 的边界说明。

#### 4. 正常流程

权威方提交业务事实 → 通过 Outbox 保存事件 → Shared Runtime 按公共封套投递 → 消费方按事件身份去重并保存视图或待办 → 消费方确认对应层次的处理 → 发生缺口、超时或 Unknown 时进入补发、查询或对账。

#### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| Ack 丢失 | 保留原 `event_id` 重发，消费者幂等处理 | 不产生重复热度或动作 |
| 事件乱序 | 按 `state_revision` 或事件序号检查；发现缺口则补拉 | 不用到达时间覆盖新事实 |
| Search Hit 无后续加载 | 只记 `retrieved`，不补记 `loaded` 或 `used_in_context` | 热度不被夸大 |
| Provider 只返回 Accepted | 记录受理事实，等待反馈或查询 | 不提前写成功 |
| 事件超过重试预算 | 进入 AttentionRequired 或人工处理 | 未交付责任仍可查询 |

#### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| MemorySignal 生产 | 记忆版本、生命周期、资格和删除事实 | Remember：杨鹏通 / 赵旭东 |
| AccessTrace 生产 | Recall 定义各阶段语义并负责最终 Context 结果；实际访问观测组件产生真实访问事实 | Recall：陈凯 / 肖宇；实际观测组件联系人待确认 |
| Provider 观察和反馈 | 放置、资源、任务、代次和完成证据 | P2：刘佳正 / 陈晔 / 张晋 / 胡孝阳 / 杨文博 / 徐博文 |
| 投递与任务机制 | Outbox、Inbox、游标、Ack、重放和租约 | Shared Runtime：沈家隆 / 杨鹏通 |
| C 消费 | 去重、有效访问归一化、热度和调度输入 | Operator：沈家隆 / 谭旭梁 |

#### 7. 验收标准

1. 每种跨组事件都能查到 Producer、Authority、对象、版本、时间、关联和证据。
2. 同一事件重复投递时，C 不重复计热度、不重复创建动作；事件本体保持不可变。
3. `retrieved`、`validated`、`loaded`、`selected`、`used_in_context` 的定义和产生时机可验证。
4. Provider 的 Accepted、Running、Succeeded、Failed、Unknown 有明确映射，不能以受理冒充完成。
5. Ack 丢失、乱序、缺口、重放、重启和超预算场景有可重复证据。
6. AccessTrace、OTel Trace、Runtime Log 和 AuditRecord 使用统一关联字段但保持独立边界。

#### 8. 计划

| 阶段 | 开始时间 | 完成时间 | 主要输出 | 责任人 |
|---|---|---|---|---|
| 设计 | 2026-09-09 | 2026-09-18 | Event Envelope、Signal、Telemetry 和 Ack 规则 | 沈家隆 / 杨鹏通 |
| 开发 | 2026-09-21 | 2026-10-16 | 事件封套、投递、游标、去重和重放能力 | 沈家隆 / 杨鹏通 |
| 测试 | 2026-10-19 | 2026-11-06 | 重复、乱序、缺口、Unknown 和投递故障用例 | 沈家隆 / 杨鹏通；陈凯 / 肖宇协作 |
| 联调 | 2026-11-07 | 2026-11-13 | A/B/C/P2 Signal 与 Provider 反馈接口冻结 | 沈家隆 / 杨鹏通；Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁；Remember：杨鹏通 / 赵旭东；P2：刘佳正 / 陈晔；Provider 联系人待确认 |

#### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 6 |
| 测试 | 4 |
| 联调 | 2 |
| **总人天** | **16** |

#### 10. 备注

- 本 Feature 交付物为 [P3_Signal_and_Telemetry_Standard_V0.1.md](../业务对象生命周期/P3_Signal_and_Telemetry_Standard_V0.1.md)。
- 字段名用于说明契约语义，不在本 Feature 中定义字段数据类型、底层消息中间件或具体数据库表。

---

## SRF-FEAT-03 · P3 Observability Correlation Model

**Feature 范围**：以 `request_id`、`trace_id`、`memory_id`、`memory_version`、`representation_id`、`recall_id`、`plan_id`、`action_id`、`provider_task_id`、`generation` 和 `policy_version` 贯通形成、Recall、调度、Provider 反馈和对账。

**Feature 级完成标准**：能够按 `memory_id`、`recall_id` 和 `action_id` 查询完整链路；能够区分业务事实、技术调用、运行诊断和审计证据；Unknown、差异和删除残留均有可定位责任。

#### 1. 基本信息

| 字段 | 内容 |
|---|---|
| Project | P3 |
| Epic ID | SRF-EPIC-01 |
| Epic Name | Shared Runtime Foundation |
| Feature ID | SRF-FEAT-03 |
| Feature Name | P3 Observability Correlation Model |
| Owner | 沈家隆 / 杨鹏通（Shared Runtime Foundation） |
| Contributor | Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁；P2：杨文博 / 王广诚 |
| 目标里程碑 | 2026-11-27 跨组链路验收 |

#### 2. User Story

作为项目验收和运维人员，我希望从一条记忆、一次召回或一次调度动作查询完整的关联证据，从而判断问题发生在哪个阶段、由谁负责、当前是否需要恢复或对账。

**Story 拆解**

- S1 作为验收方，我希望看到 MemoryRecord 到 ContextPack 的链路，从而证明召回结果来源可信。
- S2 作为 C，我希望看到 Plan、Action、Feedback 和 Placement 的关联，从而判断动作是否真的完成。
- S3 作为运维，我希望告警直接指向对象、版本、任务和责任方，从而缩短排查时间。

#### 3. 功能描述

##### 3.1 功能目标

建立统一的链路关联字段、查询视图、审计引用、Reconciliation 观测和指标告警模型，支持形成、Recall、Operate、P2 / Provider 的端到端追溯。

##### 3.2 核心输入

- `MemoryRecord`、`MemorySignal`、`ContentBinding`、`ProjectionState` 和 `AccessTrace`；
- `RecallRequest`、`RecallExecution`、`RecallCandidate`、`ContextPack` 和读取结果；
- `SchedulingView`、`PlacementPlan`、`ActuationTarget`、`TierAction`、`ExecutionFeedback` 和 `PlacementObservation`；
- `AsyncTask`、`ReconciliationTask`、`DeliveryRecord`、`AuditRecord`、指标和告警事件。

##### 3.3 核心处理

1. 定义请求、技术 Trace、业务对象、版本、动作、任务、Provider 代次和策略版本的关联关系。
2. 建立按 `memory_id`、`recall_id` 和 `action_id` 的聚合查询视图，并标注来源、版本、记录时间和缺失项。
3. 规定 Recall 各阶段、C 的计划和动作、P2 的反馈和放置观察分别记录，不通过相近时间猜测关联。
4. 为 Signal 积压、Projection 缺项、长期 Pending/Unknown、对账未决、删除残留、旧版本写回和 Action/Placement 不一致建立指标和告警。
5. 为对账任务保留原动作、目标、观察、比较结果、收口决定和新动作关联。

##### 3.4 核心输出

- 全链路关联图和字段模型；
- memory、recall、action 三类查询视图；
- AccessTrace、OTel Trace、Runtime Log、AuditRecord 和 EventRecord 的边界规则；
- Reconciliation 可观测性、指标、告警和端到端验收证据。

#### 4. 正常流程

形成主事实并发布事件 → Recall 记录候选、读取和 Context 阶段 → C 关联访问和资源事实生成 Plan → 解析目标并生成 Action → P2 返回 Feedback 和 Placement → C 完成对账 → 通过对象 ID、请求 ID 和动作 ID查询完整证据。

#### 5. 异常 / 降级

| 场景 | 系统行为 | 最终结果 |
|---|---|---|
| 只找到候选但未加载 | 只显示候选和对应阶段 | 不报告内容已使用 |
| Action 回包丢失 | 保留原 Action，创建对账任务并查询原操作 | 不换 ID 盲重试 |
| Feedback 与 Placement 矛盾 | 同时保存两类证据，标记差异并交 C 收口 | 不以单一反馈覆盖真实观察 |
| Trace 被采样丢失 | 使用业务事件、任务和审计记录补足关键链路 | 不因 Trace 缺失伪造业务事实 |
| 删除后仍有残留 | 告警指向对象版本、清理层次和 Provider | 责任和下一步可查询 |

#### 6. 依赖

| 依赖对象 | 需要的能力 | Owner / Provider |
|---|---|---|
| Remember 事实和事件 | 主事实、版本、Signal、投影和删除屏障 | 杨鹏通 / 赵旭东 |
| Recall 阶段事实 | 请求、候选、加载、Context 和 AccessTrace | 陈凯 / 肖宇 |
| Operate 计划和动作 | Plan、Target、Action、对账和策略版本 | 沈家隆 / 谭旭梁 |
| P2 可观测事实 | Feedback、Placement、Resource、Provider task 和代次 | P2 可观测与验收：杨文博 / 王广诚；P2 公共底座：刘佳正 / 陈晔；P2 E1：张晋 / 胡孝阳；P2 E2：陈晔 / 杨文博；P2 E3：徐博文 / 刘佳正；P2 平台接入：高琛 / 高旭 |
| 监控与验收环境 | Metrics、Trace、日志查询、告警和 E2E 数据集 | 王广诚；陈凯 / 肖宇 |

#### 7. 验收标准

1. 按 `memory_id` 可查询主事实、版本、正文映射、投影、事件、Recall、调度、动作、观察、清理和审计引用。
2. 按 `recall_id` 可查询请求、候选、资格、加载、AccessTrace、ContextPack、缺失来源和结果终态。
3. 按 `action_id` 可查询 Plan、Target、提交、Provider task、Feedback、Placement、对账和后续动作。
4. 不同 Memory 版本、Representation、Provider 目标和 generation 不会被错误合并。
5. Signal 积压、Projection 缺项、Pending/Unknown、对账未决、删除残留和动作/观察不一致均可告警定位。
6. Action 回包丢失、版本冲突、观察过期、动作后再次迁移和删除竞态均有端到端证据。

#### 8. 计划

| 阶段 | 开始时间 | 完成时间 | 主要输出 | 责任人 |
|---|---|---|---|---|
| 设计 | 2026-10-09 | 2026-10-16 | 关联字段、链路图、记录边界和查询视图设计 | 沈家隆 / 杨鹏通；Remember：杨鹏通 / 赵旭东；Recall：陈凯 / 肖宇；Operator：沈家隆 / 谭旭梁；P2 公共底座：刘佳正 / 陈晔；P2 E1：张晋 / 胡孝阳；P2 E2：陈晔 / 杨文博；P2 E3：徐博文 / 刘佳正；P2 平台接入：高琛 / 高旭 |
| 开发 | 2026-10-19 | 2026-11-06 | memory、recall、action 聚合查询和审计关联 | 沈家隆 / 杨鹏通 |
| 测试 | 2026-11-09 | 2026-11-20 | 指标、告警、Unknown、删除和冲突验证 | 沈家隆 / 杨鹏通；杨文博 / 王广诚 |
| 联调 | 2026-11-23 | 2026-11-27 | 形成、Recall、调度、反馈和对账端到端证据 | 环境搭建与验收：陈凯 / 肖宇组织；Remember：杨鹏通 / 赵旭东；Operator：沈家隆 / 谭旭梁；P2 公共底座：刘佳正 / 陈晔；P2 E1：张晋 / 胡孝阳；P2 E2：陈晔 / 杨文博；P2 E3：徐博文 / 刘佳正；P2 平台接入：高琛 / 高旭；P2 可观测与验收：杨文博 / 王广诚；Shared Runtime：沈家隆 / 杨鹏通 |

#### 9. 人天

| 工作项 | 人天 |
|---|---:|
| 设计 | 4 |
| 开发 | 5 |
| 测试 | 3 |
| 联调 | 2 |
| **总人天** | **14** |

#### 10. 备注

- 本 Feature 交付物为 [P3_Observability_Correlation_Model_V0.1.md](../业务对象生命周期/P3_Observability_Correlation_Model_V0.1.md)。
- 聚合查询是可追溯视图，不构成新的业务主事实；任何缺失项必须显示来源和缺失原因。

## SRF 统一责任边界

```text
SRF：统一对象目录、状态语义、事件封套、投递、幂等、任务、关联查询、审计和恢复机制。
B：负责 MemoryRecord、MemorySignal、生命周期和表示资格的业务事实。
A：负责 Recall 请求、候选、读取、ContextPack 和真实访问阶段。
C：负责热度、PlacementPlan、ActuationTarget、TierAction、准入和调度域业务对账。
P2 / Provider：负责真实 Placement、ResourceState、Provider task 和 ExecutionFeedback。
```

SRF 不替业务 Owner 做业务判断，不把公共投递成功、任务受理或 Provider `Accepted` 直接解释为领域成功，不决定 `current_tier`、物理复制、迁移、切换或回收结果。

## SRF 总体验收

1. 三个 Feature 的交付物可以独立评审，但对象、状态、Signal 和关联字段能够相互引用且没有冲突。
2. A、B、C、P2 使用同一套公共身份、版本、幂等、时间、状态和证据确认语义。
3. 重复投递、乱序、重启、回包丢失、Unknown、删除竞态和共享目标冲突均能从统一查询视图追踪。


---

# 第五章 External Integration

# AetherStore P3 · External Integration：P2 对接计划 V0.1

编制日期：2026-09-09。计划起点：2026-09-10。MVP 交付目标：2026-11-30。

本文说明 Remember、Recall、Operate 在各阶段如何与 P2 交接接口、开展联调并确认结果。P3 负责业务契约、消费适配与验证，P2 负责存储、检索、资源状态及其承接的执行能力。以下日期是对接规划；P2 功能日期引用其负责人填报，新增接口要求和双方联合窗口需要落实到交接记录，不表示已经完成或获得 P2 承诺。

## 1. 对接责任

| P3 负责人 | 对接职责 | P2 对口范围与人员依据 |
| --- | --- | --- |
| 陈凯 | EI 总协调；向量写入、查询、操作恢复及检索；汇集接口问题和版本差异 | E1 向量：胡孝阳 / 张晋；公共 API：高旭 / 高琛 |
| 赵旭东 | 正文写读、版本与范围、完整性、持久化及恢复；衔接 Remember 的内容映射与 Ready 判定 | E2 对象：杨文博 / 陈晔；公共持久化与恢复：刘佳正 / 陈晔 |
| 谭旭梁 | 目标解析、Segment、Placement、容量、动作提交、反馈与对账；落实执行能力承接 | 公共底座：陈晔 / 刘佳正；Placement：陈晔；容量与诊断：王广诚；动作执行承接通过接口清单单独落实 |
| 肖宇 | 配合 Recall 消费端联调，执行环境检查、故障用例与端到端验收，整理复验记录 | 部署接入：高琛 / 高旭；验收：杨文博 / 王广诚 |

P2 人员用于按其现有填报定位对口方，不构成新增任务指派。每个节点由表中 P3 负责人牵头，P2 对口人在交接记录中确认；同一次接口联调同时支撑业务模块和 EI，投入只计一次。

## 2. 阶段对接安排

| 时间 | P2 计划依据 | P3 与 P2 在这一阶段完成什么 | 阶段交付物 |
| --- | --- | --- | --- |
| 2026-09-10～09-18 | 09-18 公共契约 v0.1 基线 | 交换三模块的输入输出、公共身份与资源标识、版本/幂等/错误语义；逐项标记现有接口能够覆盖的要求和需要补充的能力 | 接口映射清单、请求响应样例、双方联系人、缺口清单 |
| 2026-09-21～09-30 | Collection、持久化基础在 09-24 联调；公共 API 在 09-30 联调 | 核对 SDK/RPC 或 HTTP 版本、测试端点、凭据权限、Namespace/Collection/Bucket 与测试数据；09-24 前明确操作查询、投影核验和动作执行的承接方案 | 可用接入配置、资源创建与权限用例、接口交接日期 |
| 2026-10-09～10-16 | 10-16 向量写入、S3 核心读写及 Health 联调 | P3 完成适配器与测试样例准备，接收 P2 基础接口版本；验证可用端点，安排随后一周的双方联合调用 | 基础接口交接包、适配配置、联合用例清单 |
| 2026-10-19～10-23 | 10-16 基础接口已进入联调；10-23 Range GET、Segment 与后台任务模型联调 | 跑通正文写入/读取和向量写入的首轮真实调用，核对逐项结果、错误与 Trace；接收 Range、Segment 和任务接口 | 基础写读证据、错误映射、下一批接口样例 |
| 2026-10-26～11-06 | 10-30 向量可见性/删除、完整性及探针；11-06 HNSW、稳定引用与 Placement | 分批验证删除、正文范围/摘要、索引可见性、资源观察与恢复场景；11-06 汇总已交付能力的首轮联调结果，列明仍受后续 P2 交付影响的节点 | 首轮联调报告、恢复验证结果、阻断项责任与关闭节点 |
| 2026-11-09～11-13 | 11-13 ANN/Metadata Filter、对象恢复及 RC 接口冻结 | 交接完整检索与对象恢复版本，对齐 RC（候选发布版本）的字段、状态、支持范围和兼容规则；为下一周集中验收固定版本 | RC 接口矩阵、版本清单、完整检索及恢复验收用例 |
| 2026-11-16～11-20 | P2 完成 11-13 交接项；向量恢复、容量诊断在 11-20 联调 | 集中验收完整检索、可信正文读取、对象恢复和已具备的调度链路；11-20 接收向量恢复与容量诊断版本，安排后续联合复验 | P3 首轮集中验收报告、晚到能力交接包、缺陷清单 |
| 2026-11-23～11-27 | P2 集中端到端与 Chaos 验收至 11-27 | 完成向量恢复、容量诊断的联合验证；与 P2 合并复现跨模块故障，修复阻断缺陷并重跑受影响链路 | 联合验收记录、缺陷关闭证据、与 P3 相关的性能结果 |
| 2026-11-30 | P2 总体交付 11-30；性能 Release Gate 在其细项计划中为 11-28 | 核对 P2 最终版本与此前联调版本的差异，检查受影响用例和最终性能结论，确认 P3/P2 对接交付材料 | 版本对应表、支持范围、最终验收结论与运行交接材料 |

11-06 是首轮接口联调节点，11-13 是 RC 接口基线节点，两者均不表示所有真实能力已经验收。11-20 才交接的能力安排在 11-23～11-27 验证，因此这五个工作日同时承担晚到能力验证和修复复验，不能全部算作空闲缓冲。11-28、11-29 为周末，不作为 P3 常规修复工作日。

## 3. 每个接口节点对接什么、何时完成

本节日期均为 2026 年。EI-P2 编号用于本文件的交接节点追踪，不替代既有 REQ、Feature 或 AL 编号。“完成”指在指定版本上取得表内验证证据，接口文档存在或模拟器通过不单独构成真实对接完成。

| 节点 | 对接内容与完成条件 | P2 对应功能及计划依据 | P3 联调与完成节点 | P3 牵头 |
| --- | --- | --- | --- | --- |
| EI-P2-01 公共接入 | P3 提供 tenant/Scope、资源和请求标识、幂等键、deadline；P2 提供方法、凭据、资源状态、错误和版本映射。能创建/查询测试资源，越权与同键冲突有明确结果 | P2-FEAT-01-01/02；P2-FEAT-06-01 于 09-30 联调 | 09-18 核心字段基线；09-30 完成接入配置和公共 API 验证 | 陈凯 |
| EI-P2-02 正文基础写读（OBJ-001） | P3 提供正文、目标引用和权限；P2 返回对象引用及写读结果。写入后可按同一引用读取，重复请求和失败可定位；持久化保证在 EI-P2-06 继续核验 | P2-FEAT-03-02，10-16 联调 | 10-16 接收版本；10-19～10-23 完成基础 PUT/GET/HEAD 首轮验证 | 赵旭东 |
| EI-P2-03 向量写入（VEC-001、RP2-06） | P3 提供向量、模型/Schema、Memory 与正文关联、目标身份、幂等键和载荷指纹；P2 返回原操作关联及逐项结果。完成写入映射、重复写和批量失败验证，受理不标为 READY | P2-FEAT-02-01/02，分别于 09-24、10-16 联调 | 10-16 接收写入版本；10-19～10-23 完成基础写入联调 | 陈凯 |
| EI-P2-04 操作查询与投影核验（VEC-001、RP2-07/08） | 按原操作查进度，按准确目标查存在性、载荷绑定、持久化和索引可查询性；覆盖回包丢失、重启、索引未就绪。P3 形成机制结果，Remember 根据版本和证据执行 Ready 判定 | 后台任务 P2-FEAT-01-07 于 10-23、向量可见性 P2-FEAT-02-04 于 10-30、HNSW P2-FEAT-02-06 于 11-06 联调；通用任务接口是否覆盖原操作查询需专门对齐 | 09-24 明确方法和完成性保证；11-02～11-06 首轮验证，11-09～11-13 完成已承接能力的核验；缺少保证时保留未通过结论 | 陈凯，赵旭东配合 |
| EI-P2-05 向量删除与代际隔离（VEC-001、RP2-09、VER-001） | P3 指定准确目标、Generation 和删除身份；P2 返回删除进度与可见性依据。删除后新查询不返回被删代际，旧在途写不能复活旧目标；合法新代际重建单独验证 | P2-FEAT-02-05，10-30 联调；删除/在途写屏障和同版本重建规则还需结合 AL-P206/207 对齐 | 09-24 明确新旧代际与屏障规则；11-02～11-06 首轮验证；11-16～11-20 纳入整链回归 | 陈凯，赵旭东配合 |
| EI-P2-06 正文范围、完整性与持久化（OBJ-001、RP2-02/03） | P3 提供获准正文映射、准确版本/范围、期望摘要与字节上限；P2 返回实际字节、范围、版本、摘要及持久化依据。验证 Range、断流、损坏、写超时后 head 查证和跨版本读取 | P2-FEAT-03-03 于 10-23、03-04 于 10-30、03-06 于 11-06 联调 | 10-26～11-06 分批验证范围和完整性；11-09～11-13 完成持久化保证对接；与 Recall 的完整读取在 11-16～11-20 验收 | 赵旭东，肖宇配合 |
| EI-P2-07 向量检索（VEC-002、RP2-01） | P3 提供 Query 向量、空间、Scope、筛选和 TopK；P2 返回稳定引用、分数、版本和完整/部分结果依据。验证隔离、空结果、超时、旧版本和过滤组合 | P2-FEAT-02-06 于 11-06、02-08 于 11-13 联调 | 11-06 前只验证已交付检索能力；11-13 交接完整 ANN/Filter 版本；11-16～11-20 完成正式矩阵验证 | 陈凯，肖宇配合 |
| EI-P2-08 引用与版本一致性（VER-001） | 对齐 Vector→Object 引用和源版本，验证引用缺失、目标不可用及更新/删除竞态；P2 提供存储引用事实，P3 保留 Memory 资格和最终复核责任 | P2-FEAT-05-01 于 11-06、05-02 于 11-13 联调 | 11-09～11-13 完成引用映射交接；11-16～11-20 验证检索到正文的引用链 | 赵旭东，陈凯配合 |
| EI-P2-09 目标解析与 Segment（ACT-001、SEG-001） | P3 提供 representation/provider 引用；P2 返回可执行目标、generation、支持动作及 Segment 观察。确认目标与表示的映射和失效行为，P3 业务策略不绑定 P2 内部分段格式 | P2-FEAT-01-06 于 10-23 联调；ActuationTarget 解析与 supported_operations 没有直接等价的独立交付项 | 09-24 明确目标解析承接；10-26～11-06 验证 Segment 与已交接目标映射；11-13 固定支持范围 | 谭旭梁 |
| EI-P2-10 Placement、容量与版本观察（PLC-001、VER-001） | P3 查询实际层级、版本、容量与观察时间；P2 给出真实映射、可用容量和 stale/unknown 依据。验证观测过期时 P3 保持 Keep/No-op，不凭标签推断物理事实 | P2-FEAT-03-05 于 11-06、08-04 于 11-20 联调；Storage Profile 需另核对能否表达所需对象/表示的实际放置状态 | 11-09～11-13 验证基础 Placement；11-20 交接容量诊断；11-23～11-25 完成容量与过期观测联合验证 | 谭旭梁 |
| EI-P2-11 动作提交、反馈与恢复（TIER-001～004） | P3 提供 action_id、目标/代际、期望动作及预算；承接方返回受理结果、任务关联、执行状态与实际完成事实。验证重复提交、反馈丢失、重启查询、取消限制，以及 Copy→Verify→Cutover→Reclaim 的可观测语义 | 现有 P2 填报未找到直接对应的完整执行链路；P2-FEAT-09-02 的标准工具对象互操作迁移不能直接等同于本项 | 09-24 明确承接方、方法与交付范围；提出 10-23 前交接可联调版本的需求，10-26～11-13 为目标联合窗口；实际日期以承接记录落实，11-16～11-20 验收已支持动作 | 谭旭梁 |
| EI-P2-12 Health、Trace 与故障定位（HLT-001） | P3 传递 request_id/trace_id 并消费分层健康状态；P2 提供资源/引擎是否可服务及原因、关联日志与指标。验证进程存活但恢复未完成时不被认定为业务 Ready | P2-FEAT-08-01 于 10-16、07-03/08-02 于 10-30 联调 | 10-19～10-23 接入基础 Health；11-02～11-06 完成探针与 Trace 关联；故障验收期间持续复核 | 陈凯，肖宇配合 |
| EI-P2-13 对象与向量恢复 | P3 提供恢复前后的请求、版本、已确认写入和预期读取结果；P2 提供恢复进度、数据/索引状态与 Ready 依据。验证已确认数据、引用与版本一致，以及恢复中请求的降级行为 | P2-FEAT-03-07 于 11-13、02-09 于 11-20 联调 | 对象恢复在 11-16～11-20 验证；向量恢复在 11-23～11-25 联合验证，缺陷在 11-27 前复验 | 赵旭东负责对象；陈凯负责向量；肖宇执行联合用例 |
| EI-P2-14 联合验收与交付 | 固定双方版本、配置、数据集和指标，覆盖 Remember→Recall→Operate 链路；交换真实故障和性能证据，检查最终发布版本差异及回退步骤 | P2-FEAT-10-01/02 于 11-27、10-03 于 11-28 联调 | 11-16～11-20 完成 P3 已就绪能力验收；11-23～11-27 联合复验；11-30 核对最终版本与 Release Gate 后交付 | 陈凯统筹；肖宇执行；赵旭东、谭旭梁处理对应链路 |

## 4. 必须提前落实的接口差异

以下事项在 09-18 进入接口清单，09-24 前形成双方处理记录。记录应明确提供方、支持方式、交接版本和日期；如不支持，写明受影响的 MVP 功能及范围处理责任，不以模拟结果关闭真实集成项。

| 差异 | 为什么要单独对齐 | 处理负责人 |
| --- | --- | --- |
| 原操作查询与准确投影核验 | P2 的通用 Task、Search 或索引状态不能自动证明某一次写入的效果，也不能自动证明准确目标的载荷、持久化和可查询性 | 陈凯；对应 AL-P205～208 |
| 删除与新代际重建 | P2 允许同 ID 以新 Generation 再写入；P3 需要防止旧写和迟到 READY 复活已经失效的目标，必须区分合法新构建与旧请求重放 | 陈凯、赵旭东；对应 AL-B08、AL-P206/207 |
| 可信正文版本、范围与摘要 | 存在 GET/Range/HEAD 不代表已满足 P3 的准确版本、条件读取、字节上限及摘要覆盖规则 | 赵旭东；对应 AL-P203 |
| 目标解析、真实 Placement 与调层执行 | Segment 管理、Storage Profile 或对象互操作迁移不直接等同于 ACT/PLC/TIER 所需能力；需要逐项落实承接，明确逻辑路由与物理完成的证据区别 | 谭旭梁；对应 ACT-001、PLC-001、TIER-001～004 |

Recall 已有 AL 事项的契约签收状态继续维护在原[跨模块接口依赖清单](../Recall流程/运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)，本文件只说明交接节点和排程，不复制一套签收状态。动作执行能力未落实时，EI-P2-11 的真实完成日期不能据此表认定；EI 汇集影响，由项目负责人处理 MVP 范围或交付安排。

## 5. 每次对接如何交接与确认

1. **联调前交换材料**：P3 提供需求编号、请求与期望结果、正常/异常用例；P2 提供可调用接口、版本、访问方式、支持限制及样例。确认接入配置后再进入真实联调。
2. **按同一条请求核对两端结果**：关联 request_id、trace_id、operation/action_id、资源及版本。分别检查请求被受理、执行结束和结果可使用三个层次，避免把受理当作成功。
3. **记录问题并回到对应节点复验**：记录失败输入、双方版本、日志和复现步骤，落实修复责任、交付版本及复验日期；接口变更同时检查受影响的其他链路。
4. **形成节点完成记录**：至少包含 EI-P2/REQ/Feature 编号、双方确认人、实际日期、接口与部署版本、通过用例、证据位置和明确的未覆盖范围。部分通过只确认相应范围。

11-23～11-27 的联合窗口需与 P2 提前协调，特别是向量恢复、容量诊断及最终性能证据。P2 若按其计划在 11-28 才给出性能 Release Gate，11-30 需核对该结论与已验收版本；新增阻断缺陷或改变语义的晚期修复需重新评估交付，不能仅以日期到达视为通过。

## 6. 文档依据

- [P3 总体里程碑与人员分工](AetherStore_P3_功能负责人填报汇总_Remember_Recall_Operate.md)。
- [P3 对 P2 的外部需求](../../../PPR/P2_P1_P4_External_Requirement_V1.0.md)：REQ 与实现责任边界。
- [Recall 与 P2 接口需求](../Recall流程/运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md)：六项核心逻辑接口及补充读取能力。
- [跨模块接口依赖清单](../Recall流程/运行时详细设计_V0.1/跨模块待确认事项_V0.1.md)：AL 编号与契约签收位置。
- [Operate 负责人填报](../operate流程/AetherStore_P3_C组功能负责人填报_V0.1.md)：目标解析、观察、执行与对账需求。
- P2 日期和对口人员来自本次提供的《AetherEngine_P2_功能负责人填报_V1.0(1).md》，编制日期 2026-09-08。本文保留对应 Feature ID 和交接日期，按其具体功能计划分批对接，不将 P2 总体里程碑视作全部接口同时可用。
