# 第一章 Remember

# Remember 与 P2：接口对接需求 V0.1（按流程组织）

整理日期：2026-09-07。维护方：Remember（B）；接收方：P2 / Content、Vector Provider；向量机制对接方：Recall / Embedding（A）。Operate（C）负责驻留决策和相应执行闭环。

**八项对接需求总览：沿用既有 BP2-01～BP2-08 编号，按实际执行流程展开。** 编号代表需要取得的能力和事实，不要求实现八个独立 RPC。Artifact 复用正文写入契约；正文查询、向量查询和删除核验可以由多个实际方法组合满足。

**本文导航。** 所有可点击链接只定位到本文件的标题，链接文字标明章节号和内容名称。例如 BP2-04 的完整说明在 2.2 节，BP2-05 在 2.4 节，BP2-06 在 2.5 节；这些编号是需求标识，章节才是阅读位置。参考材料仅作为纯文字来源说明。

| 所属流程 | 核心能力及原编号 | 功能 | 我们提供的核心输入 | 期望 P2 返回的数据 |
|---|---|---|---|---|
| 正文形成 | [1.4 正文写入（BP2-01）](#14-步骤三写入-original-与-canonicalbp2-01) | 可靠保存本次精确字节，取得可恢复的物理写入事实 | 稳定 client operation、Scope、资源、字节、摘要、编码、条件 | 精确对象与版本、实际字节/摘要、持久性/可读性、原操作关联 |
| 正文确认与读取 | [3.3 正文元数据核验（BP2-02）](#33-步骤二按需-head-与-checksumbp2-02)、[3.4 正文读取（BP2-02）](#34-步骤三读取获准的精确正文或片段bp2-02) | 核对或读取获准版本的完整正文/片段 | 精确 locator、获准范围、可信 expected_hash、版本条件、接收上限 | 同版本 metadata/bytes、实际范围、完整性、可读性及失败原因 |
| 未知恢复 | [1.5 原正文操作查询（BP2-03）](#15-步骤四查询原正文操作并判断是否允许重发bp2-03) | 区分已写丢回执、执行中、明确未执行和未知 | 原 client/provider operation、幂等键、请求指纹、已知目标 | 原操作进度/效果、准确对象事实、负查询与安全重发依据 |
| 长期构建 | [2.2 Artifact 持久化（BP2-04）](#22-步骤一保存并确认-artifactbp2-04) | 保存加工结果，供 B 结合质量结论发布表示 | 已固定输出字节、摘要、资源、写入身份；B 保留来源/加工关联 | 输出对象版本、实际持久字节、持久性和可读性；不返回语义质量结论 |
| 长期构建，经 A | [2.4 向量写入（BP2-05）](#24-步骤三经-a-写入准确向量目标bp2-05) | 保存每个 Passage 向量及正文/模型/检索属性关联 | 准确五元组、完整输入绑定、真实向量、元数据、稳定幂等键 | 原写操作及逐项目标结果；受理、执行、完成与未知分别保留 |
| 长期构建，经 A | [2.5 投影操作与对象核验（BP2-06）](#25-步骤四查询原向量操作并核验准确对象bp2-06) | 证明完整 manifest 中每项的实际存储和索引状态 | 原操作、准确目标、预期指纹、模型/空间/Schema、完整清单映射 | 对象存在、真实载荷绑定、持久化/可查询性、缺项与完成证据 |
| 失效与清理 | [4.3 正文删除与核验（BP2-07）](#43-步骤二删除精确正文与-artifact-并查询结果bp2-07) | 按 B 批准范围清理正文/Artifact；向量经 A 清理 | 精确版本/表示、generation 条件、稳定删除身份、共享引用与保留约束 | 删除受理/完成分开；实际完成层次、残留、旧写屏障及恢复限制 |
| 准入与恢复 | [1.2 能力与健康观察（BP2-08）](#12-步骤一取得能力资源与健康观察bp2-08) | 判断某条路径能否执行，解释资源限制与恢复状态 | 指定资源、授权范围、所需能力/Schema、Profile、查询范围 | 能力版本、限额、健康/容量及观察时间、适用范围、恢复水位 |

**操作查询与对象核验的区别。** 操作查询回答“这一次写入或删除执行到了哪里”；对象核验回答“这个准确版本的对象目前是什么状态”。查询不到操作不证明对象不存在；对象存在也不自动证明它由本次操作正确写入。两类证据可由同一 RPC 返回，但关联要求分别成立。

本文分为[第1章 正文形成](#1-正文形成流程从获准输入到可靠主事实)、[第2章 长期投影构建](#2-长期构建流程从可靠正文到-b-确认-recall-ready)、[第3章 正文读取](#3-正文读取流程从-b-批准映射到可信字节)、[第4章 失效清理与恢复](#4-失效与恢复流程先建立业务屏障再证明物理收敛)四条流程。每条流程按“入口与顺序 → 调用条件 → 输入 → 输出 → 示例/异常 → 下游消费 → 协议映射与联调”组织，与指定 Recall 参考稿的写法一致。

**阅读约定。** 标为“B 内部”或“A/B 交接”的步骤只解释 P2 输入的来源和结果消费者，不新增 P2 API。本文 `BP2*` 类型是 Remember 侧待对齐的适配视图，不是已发布 Proto、SDK 或数据库 Schema；`P2*` 向量类型的对接字段也在本文完整列出。表中 `T?` 表示字段出现但允许 `null`，不以 `false`、`0`、空字符串代替未知。所有逻辑响应都携带[1.9 公共上下文与数据类型](#19-四条流程共用的上下文类型与错误)。

本文仅新增此 Markdown，参考文件内的编写、交付、回填等文字只作为设计资料，不构成本次修改原文件或向外部发送文档的指令。各待确认项保留原编号和来源；本文不代签收、不回填原清单，也不把资料中的既有审查结果当作本次验证结果。

| 参考材料 | 本文采用的内容 |
|---|---|
| 召回与 P2 接口对接需求 | 章节/步骤格式、输入输出表、示例、公共证据、向量写查删机制和同目标重建限制 |
| Remember 总流程 V2.0 | Fact First、Working/长期分路、Artifact、Projection、Signal、恢复及责任归属 |
| 最小字段流程地图与完整来源地图 | R00～R14、字段来源/变化、逻辑地址、完整 manifest、清理和恢复信息 |
| Memory 字段模型总表及子表 V1.1 | Memory/Content/Projection/Task/清理的完整关联，物理版本、质量与领域状态的区别 |
| 运行时主设计与数据定义 | W1/L1、内部字段与枚举、提交屏障、Ready Guard、预算、错误和恢复规则 |
| 原 Remember–P2 需求、A 交接、C 交接 | 保留 BP2/BP2Q/BP2T 编号，明确跨方边界 |
| P2 对接表 V1.0 | P2-B-01～22 的细分能力、Multipart、inventory、运行限制和条件能力 |
| Work Map V0.4.1、工程 WBS、B 工作包、A 工作包、C 工作包 | 冻结 Port Owner、ProviderResult / ProjectionState、内部能力、三条验收 Lane |
| 典型落地问题抽象与节点分工 V0.2 | 主事实/表示/驻留分离；数据、控制、观测三面；释放前置条件、来源与恢复闭环 |
| 仓库 AetherAgent 协议样本 | 补充核对实际方法声明；仅为本地静态样本，不代表部署版本或已验证能力 |

架构分工以总设计的 Owner/Port 为边界，内部字段以运行时数据定义为解释基线；来源地图和字段总表补充映射。资料中仍存在的粒度或契约差异集中列在[5.3 参考差异与处理](#53-参考材料差异及本次处理)，不静默合并成已实现功能。

## 1. 正文形成流程：从获准输入到可靠主事实

### 1.1 流程入口、顺序与分支

入口是业务 MemoryEvent 及可信请求上下文；输出是 B 已确认的 Memory 事实和精确正文映射。P2 只证明物理对象及操作，不决定 Memory Type、Status 或 RememberResponse。

~~~text
读取 Profile / 能力观察（R00）→ 验证请求、Scope、幂等和输入（R01）
  ├─ W1 小正文：固定可靠快照 → B 本地提交
  └─ L1 大正文 / 必须外存：
       保存 ContentWriteIntent → E2 put（R02）
         ├─ 证据完整 → 确认精确正文
         ├─ 已受理 / 执行中 / 超时 → 查询原操作 → 按需 head/get 核验
         └─ 明确失败 / 完整性不符 → 保留原因与恢复入口
确认必要正文 → B 提交 Memory / Version / ContentBinding / 待办（R03）
  → 发布合法分类 → Working 或长期分支 → 按约定输出返回
~~~

| 顺序 | 调用方 → 提供方；能力 | 本步输入与产物 | 下一步 |
|---|---|---|---|
| 1 | B 消费公共能力观察；[1.2 能力与健康观察（BP2-08）](#12-步骤一取得能力资源与健康观察bp2-08) | Profile、资源、所需能力 → 有新鲜度和范围的准入依据 | 校验输入；不改变 Memory 状态 |
| 2 | B 内部；[1.3 固定输入与写入意图](#13-步骤二固定输入与写入意图b-内部) | 可信 Scope、受管理输入、原幂等键 → RememberOperation、ContentWriteIntent | W1 本地提交，或发送 E2 put |
| 3 | B → OBJ-001 / E2；[1.4 正文写入（BP2-01）](#14-步骤三写入-original-与-canonicalbp2-01) | 固定写入载荷 → 操作回执及准确对象事实 | 完成证据齐全则采用，否则查询 |
| 4 | B → OBJ-001 / E2；[1.5 原正文操作查询（BP2-03）](#15-步骤四查询原正文操作并判断是否允许重发bp2-03) | 原操作关联 → 进度、效果和重发依据 | 核验原目标；未知不换键写入 |
| 5 | B → OBJ-001 / E2；[1.6 正文完成核验（BP2-02）](#16-步骤五核验正文版本完整性与完成证据bp2-02) | 精确对象/版本 → hash、实际字节、durable/readable 证据 | 提交业务映射 |
| 6 | B → B State Store；[1.7 主事实提交](#17-步骤六提交主事实并按本次承诺返回b-内部) | 可靠内容准备证据 → 主事实、版本、映射及后续待办 | 转入第二章或提供 Working |

W1 是现有运行时的评审基线：小型可靠正文随 B 事务持久化，Redis 保存可重建的 Working 表示。它不要求每条短记忆同步调用 E2。L1 中 E2 写入与 B 提交不假设分布式事务；“Fact Formation 准备”与“主事实提交成功”必须区分。

贯穿例子：上游提交“接口约定。”。示例 UTF-8 正文为 15 字节；确认后 B 建立 `memory-42/memory-v3`、`binding-42-v3`，对应 `object-42/object-v3`。这些业务版本和物理版本只要求映射一致，不要求字符串相同。

### 1.2 步骤一：取得能力、资源与健康观察（BP2-08）

#### 1.2.1 功能与调用时机

启动、Profile 更新、首次进入对应能力、观察过期或恢复后，确认指定 E2/E1 资源支持哪些操作、版本语义和限额。逻辑签名：`describe_capabilities / observe_health(context, BP2CapabilityInput) → BP2CapabilitySnapshot`；允许由既有公共接口和签收的运行合同组合提供。

**Port 归属保持总设计不变：BackendHealthPort / HLT-001 的 Owner 是 C。** B 通过既有公共观察交接消费 E2 所需事实；A 消费 E1 的模型空间/资源约束。此处不新建 B 专属 Health Port，也不让 B 接管 C 的调度健康闭环。

#### 1.2.2 我们发送的数据：BP2CapabilityInput

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| profile_ref | ContractRef | 是 | 固定的环境、适配与运行规则版本；同一原操作不随重试切换 |
| resource_ref | ProviderRef | 是 | 指定 E1/E2 资源，不无界枚举其他租户 |
| scope | Scope | 是 | 已授权范围；P2 仍执行服务身份和资源权限检查 |
| required_capabilities | `List<String>` | 是 | 本次需要的 put/get/query/delete/版本/条件等能力，非空 |
| model_contract_ref | ContractRef? | 是 | E1 模型/空间/Schema 核对时使用；E2 可为 null |

#### 1.2.3 期望返回的数据：BP2CapabilitySnapshot

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| provider_ref / resource_ref | ProviderRef | 是 | 实际 Provider 和资源；与请求隔离边界一致 |
| capability_version | Version? | 是 | 能解释当前支持范围的版本；未知不得宣称已具备能力 |
| capabilities | `List<CapabilityEntry>` | 是 | 每项为 `{name, support, contract_ref}`；support 为 supported/partial/unsupported/unknown，contract_ref 可 null；必需项未知则不能准入该路径 |
| backend_health | String | 是 | healthy/degraded/unavailable/unknown 的适配观察；保留原值证据 |
| observed_at / fresh_until | Timestamp? | 是 | 来源观察时间和有效截止；若截止由消费方按合同计算，须保留计算依据，不冒充 Provider 原字段 |
| limits_ref / consistency_profile_ref | ContractRef? | 是 | [5.2 运行限额与重试窗口](#52-运行限额重试与完成窗口)中的限额及持久性、可见性、幂等、负查询规则 |
| resource_observation_ref | EvidenceRef? | 是 | 容量、压力、采样窗口和资源范围；由适配器保存原响应得到 |
| model_binding_ref | ContractRef? | 是 | E1 维度、dtype、metric、模型空间及 Schema 的已确认绑定 |
| recovery_observation_ref | EvidenceRef? | 是 | 恢复阶段、影响范围、水位；不适用时 null，详见第四章 |
| evidence | `List<P2Evidence>` | 是 | 原观察、实际方法与合同版本的关联 |

例如：E2 put/get 可用、E1 对象核验不支持时，B 可以完成已满足契约的正文目标，长期 Projection 保持未完成。不能因进程 Healthy 将 E1 视为 Ready，也不能把 C 的 Prewarm 空闲容量当作 Working 或 E2 写入配额。缺少有效观察只影响依赖该观察的新操作，保留已经确认的历史事实。

**下一步。** B 保存本次 allowed/degraded/blocked 准入结论及依据，进入输入校验；P2 在实际写入时仍须执行限额。对应 BP2Q-01/07/08、BP2T-11/12。

### 1.3 步骤二：固定输入与写入意图（B 内部）

| 交接方向 | 必须保存的数据 | 来源与处理 |
|---|---|---|
| 上游/Auth → B | request_id、trace_id、Scope、输入、来源事件、发生时间、required_outputs、deadline | 先验权限、类型、编码与大小；未通过不发 E2 |
| B → 幂等记录 | operation_id、idempotency_scope/key、规范化版本、input_fingerprint | 同键同义重用原操作；同键异义 Conflict；每次重放重新授权 |
| B → ContentWriteIntent | content_operation_id、client_operation_ref、input_ref、expected_hash/bytes、provider_contract_ref、稳定目标（如有） | 在外部副作用之前可靠保存；provider_operation_ref 此时可为空 |
| B → E2 适配器 | 原写入身份、获准资源、精确字节和条件 | B 本地引用须解析成真实字节或正式协议支持的受控流，不能把引用字符串当正文 |

`memory_id/version` 未确认时，不要求 P2 从正文或 URL 生成业务身份。ContentWriteIntent 可先独立存在；已确认 ContentBinding 的 Memory 关联随主事实提交。

本次写入语义指纹绑定租户、Provider/资源、目标、Scope 的授权语义、内容摘要/字节数/媒体类型/编码和版本条件；规范化算法/字段由 OBJ-001 适配合同固定。每次 request/trace/deadline、凭据和观察时间不进入语义指纹。本文不把 RememberRequest 指纹、正文 hash 和 A 投影 request_fingerprint 当成同一个摘要。

### 1.4 步骤三：写入 Original 与 Canonical（BP2-01）

#### 1.4.1 功能与调用时机

必要正文必须经 E2 保存且 ContentWriteIntent 已持久化后调用。逻辑签名：`put_content(context, BP2ContentWriteInput) → BP2ContentMutationResult`。静态协议中的候选方法为 `PutObject`；幂等、版本条件与完成性仍需签收。

#### 1.4.2 我们发送的数据：BP2ContentWriteInput

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| client_operation_ref | ProviderRef | 是 | 调用前固定、P2 可识别/查询的客户端操作身份 |
| idempotency_key | Id | 是 | 同作用域同写入稳定；可与 client_operation_ref 共用编码，但两种语义须被合同覆盖 |
| request_fingerprint | Hash | 是 | 本次规范写入语义，coverage=normalized_request；同键换载荷拒绝 |
| resource_ref | ProviderRef | 是 | 获准目标资源，由 Profile/资源契约取得 |
| scope | Scope | 是 | 已授权写范围；不得仅靠 metadata 中 tenant 字符串获得权限 |
| target_object_ref | ProviderRef? | 是 | 如有稳定预分配对象键则传入；由 P2 分配时可 null，但必须可按原操作恢复 |
| payload | bytes | 是 | 精确待写字节；流式传输需等价绑定与限额，不传任意可变外部 URL |
| expected_hash | Hash | 是 | 对 payload 全文计算，coverage=whole_content |
| expected_bytes | UInt | 是 | 真实字节数；文本 >0，等于 payload 长度，非字符数/Token 数 |
| media_type / encoding | String | 是 | 媒体类型和明确字节编码，不允许静默转码后沿用原 hash |
| precondition_ref | ProviderRef? | 是 | 合同认可的不可变创建/条件版本/防覆盖条件；无令牌时须有已签收等价保证 |

#### 1.4.3 期望返回的数据：BP2ContentMutationResult

正文 put、delete 及其操作查询共用此视图；每次带公共响应上下文。Provider 不必使用同名字段，但适配后必须能解释这些事实。

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| operation_kind | put / delete | 是 | 对应原动作，不能将查询当作新的变更 |
| client_operation_ref / idempotency_key | ProviderRef / Id | 是 | 可追溯原请求；回显本身不证明操作已经执行 |
| request_fingerprint | Hash? | 是 | 已被 P2 证实关联的请求语义；无法证实时 null |
| provider_operation_ref | ProviderRef? | 是 | 已取得的远端操作引用；首次回复丢失时可能未知 |
| operation_status | String | 是 | accepted/running/completed/rejected/failed/not_found/unknown；具体规则见下表 |
| raw_status | String? | 是 | 原协议状态；无原字段则 null，保留原响应 |
| content_state | BP2ContentObservation? | 是 | 当前已取得的准确对象事实；仅受理时可 null |
| resubmit_assessment | P2ResubmitAssessment? | 是 | 原操作的无效果/无迟到/同键可重发依据，见 1.5 |
| delete_verification | BP2DeleteVerification? | 是 | 删除的完成层次、残留及屏障，定义见 4.3；put 时 null |
| evidence | `List<P2Evidence>` | 是 | 此次操作身份、进度及效果的证据 |

| operation_status | P2 需要证实什么 | B 处理 |
|---|---|---|
| accepted / running | 准确原操作已受理 / 明确仍执行中 | 保存观察并查询，尚不确认正文可靠完成 |
| completed | 原操作已结束 | 再核验对象、实际字节、持久/可读条件；不能仅凭此词提交主事实 |
| rejected / failed | 明确拒绝/失败，保留副作用说明 | 不伪造“绝无对象”；残留仍可清理/核验 |
| not_found | 本次未找到操作记录 | 若缺少负查询最终性，仍为未知 |
| unknown | 关联、效果或必要证据无法判定 | 先查询，不换键盲写 |

完全没有可信响应时，B 在本地记录 `unknown` 和传输错误，不伪造一份 P2 回包。

#### 1.4.4 准确对象与实际字节：BP2ContentLocator / BP2ContentObservation

| BP2ContentLocator 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| provider_ref / resource_ref / object_ref | ProviderRef | 是 | Provider、隔离资源和准确对象的不透明引用 |
| object_version | ProviderRef? | 是 | 精确物理版本；不支持版本字段时必须有不可变对象定位等价证明 |
| generation | ProviderRef? | 是 | 物理条件令牌；不等于 Memory state_version 或 memory_version |
| address_contract_ref | ContractRef | 是 | 引用解析、迁移、版本/条件、租户隔离的契约 |

| BP2ContentObservation 字段 | 类型 | 必填 | 定义与采用条件 |
|---|---|---|---|
| locator | BP2ContentLocator | 是 | 本次实际核验的物理目标 |
| object_present | Bool? | 是 | true=已证实存在；false=权威查无；null=无法判断 |
| actual_bytes | UInt? | 是 | 该版本完整对象的实际大小；未知不填 0 |
| actual_hash | Hash? | 是 | 实际存储正文摘要及覆盖；可能需按 BP2-02 回读后计算 |
| media_type / encoding | String? | 是 | 该版本的真实媒体/字节编码，可由不可变内容契约证明 |
| etag | String? | 是 | 原 ETag；算法和 Multipart 语义未确认时只作为不透明标识 |
| durable / readable | Bool? | 是 | 满足相应合同的持久性 / 精确版本可读性；不由 HTTP 状态推断 |
| observed_at | Timestamp? | 是 | Provider 观察时间；B 接收时间另存 |
| binding_evidence / completion_evidence | `List<P2Evidence>` | 是 | 对象/版本/字节绑定，以及持久性/可读性证明 |

逻辑地址是推荐适配方案，不代表 P2 已提供虚拟地址服务。若原方法使用 bucket/key，由适配器按受控映射解析；B 不从 memory_id 猜物理路径。迁移不能改变历史 binding 所指字节；如需更换 locator，由 B 核验后创建/切换映射，不能借“地址不透明”省略版本和删除保证。

**示例与下一步。** 写入“接口约定。”时 `expected_bytes=15`，全文 SHA-256 为 `598bda3350ccd8b28477531944d5cefe46b467c5444fb90c05d806e323f4976b`。返回 accepted 只进入查询；返回 completed 且目标/全文摘要/持久与可读证据全部匹配，才进入 1.7。P2 仅回显 expected_hash 不证明实际存储字节一致。

#### 1.4.5 大对象分支：Multipart（沿用 BP2-01 / BP2-04）

| 顺序 | 我们提供 | 期望取得 | 失败与下游 |
|---|---|---|---|
| 创建上传 | 原 ContentWriteIntent、资源/稳定目标、总字节与全文摘要 | 可与原操作关联的 upload_id | 创建回执丢失必须有查询或等价恢复路径 |
| 上传分片 | upload_id、part_no、实际字节、分片摘要 | 已接收分片及大小/校验事实 | 重试固定分片身份；禁止静默更换该片字节 |
| 列出分片 | upload_id、预期分片清单 | 实际 part 清单与完整性 | 总计数不代替逐片核对 |
| 完成上传 | upload_id、已确认完整清单及原输出绑定 | 最终精确对象版本、完整对象大小/摘要、持久/可读事实 | 完成响应丢失按原操作查询，不新建 upload |
| 终止/清理 | 原 upload_id、明确清理范围 | 终止状态、残留/保留约束 | Abort Ack 不单独证明所有残留字节已擦除 |

该分支仅在启用大对象分片方案时必需；实际入参和完成语义由 P2 确认。未完成 upload 不能建立可用 ContentBinding；Multipart ETag 不默认等于全文摘要。普通 put 已满足大小需求时无须调用这些方法。

### 1.5 步骤四：查询原正文操作并判断是否允许重发（BP2-03）

#### 1.5.1 功能与输入

用于正常异步执行、写超时、回执丢失、删除等待与重启恢复。逻辑签名：`query_content_operation(context, BP2ContentOperationQueryInput) → BP2ContentMutationResult`。

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| operation_kind | put / delete | 是 | 原动作；读查询不会产生第二次写入 |
| resource_ref / scope | ProviderRef / Scope | 是 | 原资源和获准查询范围 |
| client_operation_ref / idempotency_key | ProviderRef / Id | 是 | 原写/删的稳定查询身份；没有 P2 回执仍可使用 |
| request_fingerprint | Hash | 是 | 原请求语义；不得按当前内容重算并冒充原输入 |
| provider_operation_ref | ProviderRef? | 是 | 已取得则提供；须与 client operation 关联同一操作 |
| known_locator | BP2ContentLocator? | 是 | 已知准确目标时提供；首次回执丢失允许 null |

#### 1.5.2 期望输出与安全重发数据

返回 1.4 的完整 `BP2ContentMutationResult`，包含操作事实和已取得的准确对象状态。独立操作表不是强制实现，但替代查询必须能证明原请求的效果；仅有对象存在性不充分。

| P2ResubmitAssessment 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| no_effect_confirmed | Bool? | 是 | 已证明原请求未产生效果 |
| no_late_effect_confirmed | Bool? | 是 | 已证明原请求不会稍后生效，不能只依据本地 Cancel |
| same_key_resubmit_allowed | Bool? | 是 | 原合同允许相同键、相同载荷继续提交 |
| idempotency_valid_until | Timestamp? | 是 | 原键有效截止；无时间型截止时由合同解释，不推定无限有效 |
| evidence | `List<P2Evidence>` | 是 | 上述判断对应的原操作、范围和窗口证据 |

只有前三项均为 true、证据及窗口有效、原次数/时间预算仍允许、当前授权和目标仍成立，才可按原键重发。终态失败是否允许新恢复操作须按契约单独决策，不能通过普通重放重开已终结操作。幂等记录过期、单次 404、查询超时和重试次数耗尽都不是“明确未执行”。

| 查询场景 | 期望事实 | 处理与下一步 |
|---|---|---|
| P2 已写，首次回复丢失 | 按 client ref 找到原对象/版本和完成证据 | 采用原目标，继续 1.6/1.7；不产生第二份正文 |
| P2 已受理仍在执行 | 有据 running 或可见性尚未满足 | 保留未确认，按原预算继续查询 |
| 明确未执行且无迟到可能 | 三项安全重发依据齐全 | 重新核验当前权限后重发原载荷 |
| 操作 not_found / 窗口内 HEAD 404 | 无完整负查询最终性 | 保留 unknown；不确认 absent、不立即清理候选孤儿 |
| P2 已写但 B 主事实提交未知 | 物理完成与业务提交两类事实分开 | 先重读 B operation/提交证据，按第四章补登记或继续对账 |

对应 BP2Q-03/07、BP2T-02/03/08/10。Query 失败只新增一次观察，不覆盖最后确定的事实。

### 1.6 步骤五：核验正文版本、完整性与完成证据（BP2-02）

必要时复用[3.3 正文元数据核验（BP2-02）](#33-步骤二按需-head-与-checksumbp2-02)的 `head_content` 和[3.4 正文读取（BP2-02）](#34-步骤三读取获准的精确正文或片段bp2-02)的 `get_content`。put 已有充分、同版本、可验证证据时无须固定追加一次 HEAD；HEAD 也不能独自证明所有持久性保证。

| 核验项 | 输入依据 | 通过条件 |
|---|---|---|
| 原操作与对象关联 | ContentWriteIntent、client ref、请求指纹 | 准确 Provider/资源/对象版本来自原操作或等价证据 |
| 完整性与大小 | expected_hash、expected_bytes、媒体/编码 | 实际同一版本全文字节匹配；必要时回读计算；不可只核对回显字段 |
| 持久性 | 使用的 OBJ-001/Provider 合同 | durable=true，证明合同承诺的故障范围；不自称拥有 WAL/复制事实 |
| 可读性 | 同版本读取/可见性依据 | readable=true；写已保存但暂不可读仍不满足当前完整确认条件 |
| 并发与授权 | 当前 Scope、目标条件、业务操作 | 换版/撤权/旧 Worker 不能发布当前映射 |

通过后把 ContentWriteIntent 的内部 `observed_result` 更新为 `confirmed_written`，保存原证据，准备 ContentBinding。未知保存 `unknown`，可信负查询才 `confirmed_absent`；最小地图中的 written/absent 是简称，不新增一套存储状态。

### 1.7 步骤六：提交主事实并按本次承诺返回（B 内部）

B 在本地事务或可证明的等价提交屏障中保存 MemoryRecord、MemoryVersion、版本资格、ContentBinding、幂等结果、阶段检查点和必要待办；W1 同时保存可靠小正文。合法分类发布后才对外提供可用 Memory。P2 不提交 B 的领域事务。

| 结果 | 必要条件 | 下游消费 |
|---|---|---|
| Success | fact 已确认，合法 Type、当前授权和本次 required_outputs 全部验证 | Working 目标需当前 Working 可读；long_term 目标需第二章 Ready Guard |
| Accepted | fact 已确认且合法 Type 已发布；未完成目标有持久 Task/continuation 接管 | 返回真实 Memory/Task、独立表示状态和 pending_items |
| Partial | 已确认主事实/部分目标，另有未完成、失败或交接缺口 | 逐项说明已完成内容与缺口，保留主事实 |
| Failed，certainty=unknown（适用时） | 必要正文或主事实尚未确认，或请求被拒绝 | 返回原 operation 与原因；不伪造已确认 memory_id/version 或 Accepted Memory |

原字段模型中的 Provider “accepted/pending”不能直接映射为 RememberResponse.Accepted。E2 成功而 B 事务失败/未知时，先保留候选孤儿并查询提交事实；只有保护窗口、引用和在途写风险均核实后才进入清理。主事实已确认后的 Artifact、Vector、Redis、Broker 或 C 故障，分别报告派生缺口。

### 1.8 本流程的协议映射与联调要求

| 需求 | 本地协议样本已声明的方法 | 仍需 P2 明确的保证 |
|---|---|---|
| put | `PutObject(bucket,key,data) → ObjectMeta` | client-key 幂等/查询、同键冲突、版本与条件、实际字节/完成性；ObjectMeta 不含显式 durable |
| head/get | `HeadObject`、`GetObject`、`GetObjectRange` | 精确版本、条件读、摘要覆盖、可见性、授权映射；详见 3.6 |
| Multipart | CreateMultipartUpload、UploadPart、ListParts、CompleteMultipartUpload、AbortMultipartUpload | 原操作与 upload 关联、清单完整性、全文校验、最终版本、完成未知和残留清理 |
| 原操作查询 | 此 ObjectService 样本未声明 by-client-operation 查询 | 提供实际其他入口或等价方案；不能只依赖首次回复中的 operation ID |
| 能力/健康/容量 | 参考对接表提出 Catalog/Health/资源能力 | 实际 API 和公共消费路径待确认；不因历史名称即认为已部署 |

本流程原验收项：BP2T-01/02/03；能力与故障项 BP2T-11/12；正文核验项 BP2T-04 见第三章。正常写入、已写丢回执、暂不可见、明确未写、E2 成功而 B 提交未知须分别提供脱敏请求/响应、合同版本和预期/实测结果。

### 1.9 四条流程共用的上下文、类型与错误

#### 1.9.1 公共请求与响应上下文

`P2CallContext / P2ResponseContext` 的完整对接字段见下两表，可映射到 SDK 上下文或正式传输 metadata，不指定新的 HTTP 路径/请求头。正文 Scope 和向量 A.Scope 的结构、差异及适配规则均在本文 1.9.2 说明。

| P2CallContext 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| request_ref / trace_id | Id | 是 | 本次调用与技术链路；不作为稳定幂等键或授权凭据 |
| tenant_id | Id | 是 | 已认证租户，与输入 Scope 和目标一致 |
| provider_ref / contract_ref | ProviderRef / ContractRef | 是 | 实际固定 Provider、原协议及适配版本 |
| deadline_at | Timestamp | 是 | 原执行剩余期限内的调用截止；超时不等于远端取消 |

| P2ResponseContext 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| request_ref | Id | 是 | 本次可信 RPC/响应关联 |
| provider_ref / contract_ref | ProviderRef / ContractRef | 是 | 实际来源与解释原响应的合同 |
| provider_request_ref | ProviderRef? | 是 | Provider 自有诊断请求 ID；无则 null |
| observed_at | Timestamp? | 是 | 来源观察时间，不用 B/A 接收时间代填 |
| error | P2Error? | 是 | 部分/失败原因或调用错误；正常无错为 null |

鉴权走正式身份通道；Scope.authorization_ref 是审计/批准依据，不能替代有效凭据。租约/fencing、Task ID、数据库 revision 等本地字段不自动成为 P2 条件令牌。

#### 1.9.2 基础类型、摘要与证据

| 类型 | 本文采用的表示与规则 |
|---|---|
| Id / Version / ProviderRef | 非空不透明字符串；Version/generation 不自行按字典序比较大小；外部原类型无损适配 |
| ContractRef / EvidenceRef | 可按授权回查的版本化合同 / 原始事实证据引用；不是公开 URL 或凭据 |
| UInt / Number / Bool | 非负安全整数 / 有限数值 / 布尔值；禁止精度溢出、NaN 和 Infinity |
| Timestamp | UTC RFC3339 毫秒格式，例如 `2026-09-07T02:00:00.000Z` |
| T? / `List<T>` | 字段出现，可 null / 有序数组；空数组表示该范围内无条目，不表示未知 |
| ByteRange | `{start,end}`，半开字节区间 `[start,end)`，0≤start<end；必须说明内容版本与编码域 |
| Hash（BP2 正文类型） | Remember 形式 `{algorithm,value,coverage,byte_start,byte_end}`；sha256 为 64 位小写十六进制；whole_content/normalized_request/normalized_input 的起止为 null，byte_range 必须有起止 |
| Scope（BP2 正文类型） | `{tenant_id,project_id,user_id,agent_id,session_id,business_task_id,authorization_ref}`；tenant/授权依据必有，其余可 null，null 不能扩大授权 |
| A.Hash | 向量接口的摘要结构为 `{algorithm,value,byte_encoding,range}`；algorithm/value/byte_encoding 为非空字符串，value 为算法长度匹配的小写十六进制；range 为 ByteRange 或 null，null 表示该摘要对象的完整字节 |
| A.Scope | 向量接口的范围结构为 `{tenant_id,project_id,agent_id,session_id,task_id}`；五字段均出现，tenant_id 为 Id，其余为 Id?；task_id 表示业务任务，null 不代表任意范围 |

Hash 的 coverage、编码、规范化和实际字节必须一致。`source_hash`、正文 checksum、向量 hash、metadata_hash、manifest_hash 与请求指纹各有对象，不能互换。

正文 Scope 与向量 A.Scope 不能直接互相赋值，适配须满足下列条件：

| Remember Scope 字段 | A.Scope 中的对应字段 | 对接规则 |
|---|---|---|
| tenant_id、project_id、agent_id、session_id | 同名字段 | 按原批准范围传递；不得在适配时放宽为 null 或默认全局 |
| business_task_id | task_id | 仅映射业务任务身份，不使用 B async_task_id 或 P2 task ID 替代 |
| user_id | 无直接同名字段 | 相关用户约束必须由获准空间、服务授权或明确扩展契约完整落实；无法表达必要约束时拒绝该路径，不静默丢弃 |
| authorization_ref | 不作为检索属性字段 | 批准依据由 B/A 保存，实际调用仍使用正式凭据并校验资源范围；不把凭据写入索引 metadata |

两种 Hash 在转换时同时核对算法、值、内容编码与覆盖范围：正文 byte_range 的起止可映射到 A.Hash.range；全文或规范化对象的摘要使用 range=null，并分别注明实际字节编码或规范化编码。没有字节域依据时不能仅改字段名完成转换。

| P2Evidence 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| kind | String | 是 | 原证据类别；示例标签不作为新的冻结枚举 |
| contract_ref | ContractRef | 是 | 该事实的范围、含义和有效窗口 |
| provider_evidence_ref | ProviderRef? | 是 | 独立外部证据引用；无时可 null，但原完整响应+合同必须足以证明 |
| observed_at | Timestamp? | 是 | 来源时间；缺失保留未知 |

证据与父对象的准确目标、原操作、范围和观察绑定。B/A 保存原响应和合同后才生成本地 EvidenceRef；P2 无须实现 B 的证据表。不同版本或互不相容时点的“存在”“durable”“可查询”不能拼成完成证明。

#### 1.9.3 公共错误事实与处理

`P2Error = {raw_code, category, message, retryable?, retry_after_ms?, effect, evidence[]}`。raw_code/category/message 为字符串；retryable 为 Bool?，retry_after_ms 为 UInt?；effect 为 no_effect/may_have_effect/unknown/not_applicable，只读使用 not_applicable。字段均出现，可空项用 null，evidence 为 P2Evidence 数组。原传输错误不能包装为成功 body，日志不记录凭据或完整敏感正文/向量。

| category | 适配处理 | B 本地原因示例 |
|---|---|---|
| invalid_argument / binding_mismatch | 拒绝非法大小、编码、维度、范围或输入绑定；不静默截断 | RM_INPUT_INVALID / RM_CONTENT_INTEGRITY_FAILED / RM_EMBEDDING_INVALID |
| permission_denied | 当前授权/租户不符；不改用更宽范围恢复 | RM_SCOPE_DENIED |
| conflict / version_mismatch | 同键异义或条件冲突；查询当前事实，旧结果失权 | RM_IDEMPOTENCY_CONFLICT / RM_VERSION_STALE |
| unsupported | 必需能力不支持，关闭依赖该保证的真实路径；记录替代方案待确认 | 按受影响阶段保留 RM_READY_PROOF_MISSING 等及原错误 |
| not_found | 区分操作、准确对象和资源；负查询需说明最终性 | RM_CONTENT_UNKNOWN / RM_PROVIDER_UNKNOWN，或有据缺失 |
| rate_limited / resource_exhausted | 有界退避/拒绝；保留原期限，后台不侵占前台保留 | 保留原错误与未完成目标 |
| timeout / cancelled / unavailable | 变更可能已执行，先查原操作；取消不证明无迟到效果 | RM_CONTENT_UNKNOWN / RM_PROVIDER_UNKNOWN / RM_CLEANUP_PENDING |
| provider_error | 未识别回包或内部故障保留来源；不猜成功/失败终态 | 按阶段保存未知与下一步 |

retryable=true 只是原错误建议，不构成允许重发副作用的证据。正文业务重试由 B 负责；向量一次机制调用由 A 负责，B 只负责原任务的接管、查询和总预算。

## 2. 长期构建流程：从可靠正文到 B 确认 Recall Ready

### 2.1 流程入口、顺序与分支

入口是主事实已确认、当前资格有效且需要长期检索的 Episodic/Semantic。Working 当前读取不要求 Embedding 或长期 Projection；压缩也不是长期构建的必经步骤。

~~~text
已确认 Memory / 当前资格 / 原正文
  → 需要后台执行：先保存 Task + Outbox，再派发（R04）
  → B 分类/巩固/证据判断（R05；不属于 P2）
  → 可选 Artifact 生成 + 质量检查 + E2 持久化（R06）
      ├─ 合格输出：采用精确 Artifact binding
      └─ 跳过或获准回退：采用 Original binding
  → B 固定 ProjectionSet / build_fingerprint / 完整 manifest（R07）
  → A 生成并验证 Passage 向量 → A 调 P2 upsert（R08）
  → A 查询原操作 / 核验准确对象与索引（R08/R09）
  → A 保存 ProviderResult → B 汇总全部必需 chunk
  → B Ready Guard + 当前资格 CAS → ProjectionState=Ready（R09）
  → A MemoryRead 可消费；B Signal 经 RF 交 C（R13）
~~~

| 顺序 | 调用方 → 提供方；能力 | 本步产物 | 下一步 |
|---|---|---|---|
| 1 | B 内部 / Worker → OBJ-001；[2.2 Artifact 持久化（BP2-04）](#22-步骤一保存并确认-artifactbp2-04) | Original 或质量+持久性均合格的 Artifact | 固定完整投影输入 |
| 2 | B → A SemanticEmbeddingCapability；[2.3 投影输入与 Passage 向量](#23-步骤二固定完整输入并生成-passage-向量ab-交接) | 已绑定来源和模型的合法 Passage 向量 | A 固定准确目标与写入载荷 |
| 3 | A → VEC-001 / E1；[2.4 向量写入（BP2-05）](#24-步骤三经-a-写入准确向量目标bp2-05) | 每项原操作、目标、进度及真实观察 | 不足以完成时继续查询 |
| 4 | A → VEC-001 / E1；[2.5 投影操作与对象核验（BP2-06）](#25-步骤四查询原向量操作并核验准确对象bp2-06) | 原操作效果、真实载荷绑定、对象和可查询性 | A 形成机制结果 |
| 5 | A → B；[2.6 Ready Guard](#26-步骤五b-对完整集合执行-ready-guard) | ProviderResult 与逐项证据 | B 对完整 manifest 执行 Guard |
| 6 | B 内部；[2.7 资格与结果发布](#27-步骤六发布读取资格task-结果与变化信号) | ReadyProof、当前 ProjectionState、Task 目标与 Signal 待办 | 交给 A/C 各自消费 |

内联与异步使用同一输入、原操作和 Ready Guard。转后台前必须可靠保存 Task/Outbox，撤销旧内联执行者的提交资格；投递成功或 Worker 函数返回不能替代目标完成。

### 2.2 步骤一：保存并确认 Artifact（BP2-04）

#### 2.2.1 功能、输入与调用条件

加工与验质由 B/Worker 执行，P2 负责输出字节存储。逻辑接口直接复用 `put_content(context, BP2ContentWriteInput) → BP2ContentMutationResult`，不新增名为“压缩成功”的 P2 API。

| 输入/记录 | 提供方 | 用途与约束 |
|---|---|---|
| memory_ref、source_binding_ref、来源版本/摘要 | B | 固定准确 Original/Canonical；生成期间换版/撤权使旧提交失权 |
| artifact_id、input_fingerprint、generation_contract_ref | B/Worker | 绑定加工输入和版本化规则；非确定性重生成不同字节使用新 Artifact 身份 |
| quality / quality_evidence | B/质量执行者 | 检查关键事实、数字、主体、否定、时间、约束、来源与冲突；不是 P2 事实 |
| ContentWriteIntent + BP2ContentWriteInput | B | 对候选输出固定真实字节、全文摘要、大小和稳定写入身份；来源关联在 B 保存 |

#### 2.2.2 期望输出、示例与异常

P2 返回与 1.4 相同的操作及 `BP2ContentObservation`，证实输出的 locator、实际字节、durable/readable 和原操作关系。B 保存 `output_binding_ref`，并同时检查质量资格。

| 情况 | B 采用规则 | 下一步 |
|---|---|---|
| quality=passed，输出持久/可读/完整 | Artifact 可作为获准表示；保存来源和输出双向关联 | 2.3 固定该 Artifact 输入 |
| 输出已存，但质量 failed/unverified | 不发布 Artifact 的投影资格；保留 Original | 仅在策略和本次 required_outputs 允许时回退 Original |
| 质量通过，但写入 unknown | 保留该候选与原 ContentWriteIntent | BP2-03 查询；不反复生成新字节掩盖未知写入 |
| 不需要压缩 | 显式 skipped，无空 Artifact 或失败占位 | 直接用 Original 构建 |
| 请求明确要求 artifact 完成 | 质量或存储未满足即保留未完成项 | 不能用向量 Ready 掩盖该目标失败 |

压缩比使用同数据集、同口径的 `original_persisted_bytes / compressed_persisted_bytes`，分母必须 >0；两份正文都保留时，该比率不等于系统总存储下降。对应 BP2Q-02、BP2T-05。

### 2.3 步骤二：固定完整输入并生成 Passage 向量（A/B 交接）

#### 2.3.1 B 固定集合与清单

| 数据对象 | 本次必须固定的数据 | Owner 与后续用途 |
|---|---|---|
| ProjectionSet | projection_set_id、memory_ref、source_binding_ref、artifact_ref?、build_fingerprint、resource_ref | B；同一输入跨 Task 重放保持一致，输入变化创建新集合 |
| ChunkManifest | manifest_id、完整 chunks、manifest_hash、chunking_contract_ref | B；非空、有界、chunk_id 唯一，不能按部分成功缩小清单 |
| ChunkSpec | chunk_id、ordinal、source_range_ref、source_hash、provider_item_key | B；来源范围可反查，字符/Token 偏移不得直接用作字节偏移 |
| 模型/空间 | model_contract_ref、model_version、embedding_space_ref、dimension、projection_schema_version | A 提供机制规则，B 固定目标；资源和 Query/Passage 空间须兼容 |
| 当前资格 | memory_version、state_version、Scope、有效期、执行租约 | B；异步完成与 Ready CAS 时重新核验 |

build_fingerprint 绑定 source/version/hash、Artifact、分块/预处理规则、模型/空间/维度/Schema 及资源；不能将 manifest 总数量当作其完整性摘要。尚未调用的 chunk 只在 manifest 中记为预期项，不伪造 Provider UNKNOWN 回包。

#### 2.3.2 B 到 A 的 Embedding 交接

| 方向 | 输入/输出 | 使用与失败去向 |
|---|---|---|
| B → A | usage=Passage、受管理片段、source_hash、来源/范围绑定、caller 身份与原请求、授权、model_binding、deadline | A 核验输入、模型上限和当前权限；不向 P2 请求推理 |
| A → B/向量机制 | vector_ref/真实 vector、vector_hash、input_binding_digest、实际 model_binding、validation_evidence_ref | A 验证有限数值、非空、维度、dtype、模型/空间和输入绑定 |
| B → A 投影入口 | 准确五元组、写入授权、经验证 embedding_result_ref、正文表示/版本/范围、检索属性及批准依据 | A 固定原目标、幂等键、指纹；本地保存调用意图后才发 P2 |

P2 不接受把 `vector_ref` 字符串当成真实向量。Query 用途、非法向量、同维不同空间或来源错配必须在 upsert 前拒绝；不切换默认模型“补成功”。A 自己的推理重试与向量调用重试按其 EM/VP 契约执行，B Task 重试不重置 A 的原预算。

### 2.4 步骤三：经 A 写入准确向量目标（BP2-05）

#### 2.4.1 功能与调用时机

A 已验证向量、授权、稳定目标及不可变载荷后调用 `upsert(context: P2CallContext, input: P2UpsertInput) → P2MutationResult`，对应 Recall 参考稿 RP2-06。B 不绕过 VectorProjectionPort 直接维护 E1。

#### 2.4.2 我们发送的数据：P2UpsertInput

| 字段 | 类型 | 必填 | 来源、定义与约束 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | A 固定的租户/Provider/空间/五元组 |
| provider_idempotency_key | Id | 是 | A 调用前固定，同操作重试沿用 |
| request_fingerprint | A.Hash | 是 | 绑定原动作、准确目标、完整向量/模型/正文/属性载荷及适用条件的规范语义；编码与版本在 BP2Q-04 签收；同键不同载荷拒绝 |
| payload | P2ProjectionPayload | 是 | 实际向量、模型、正文关联和检索属性 |
| precondition_ref | ProviderRef? | 是 | P2 合同认可的条件；无则须有等价绑定/不覆盖保证，不填 B 本地 lease |

| P2ProjectionTarget 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| tenant_id / provider_ref | Id / ProviderRef | 是 | 目标租户和 Provider |
| retrieval_space_ref | ContractRef | 是 | 已批准检索空间及资源映射，不随重试换集合 |
| identity | ProjectionIdentity | 是 | `{memory_id,chunk_id,memory_version,model_version,projection_schema_version}`，五字段均为非空 ID/Version |
| physical_target_ref | ProviderRef? | 是 | 准确向量对象 ID；未分配时须有稳定、唯一、可查的映射方案 |

| P2ProjectionPayload 字段 | 类型 | 必填 | 来源、定义与约束 |
|---|---|---|---|
| usage | Passage | 是 | 实际 Embedding 用途 |
| vector | `List<Number>` | 是 | 真实向量；长度与维度一致，传输 dtype/字节序已明确 |
| model_binding | EmbeddingModelBinding | 是 | 见下表；模型/空间兼容性不能只看维度 |
| source_hash / input_binding_digest | A.Hash | 是 | 实际模型输入摘要 / 输入与 B 来源上下文绑定；预处理后摘要不自动等于原正文片段摘要 |
| vector_hash | A.Hash | 是 | 固定 dtype/字节序编码后的向量摘要 |
| representation_id / content_ref / content_version | Id / ProviderRef / Version | 是 | B 批准的表示和正式正文版本 |
| approved_range | ByteRange | 是 | 原正文获准范围；预处理映射另有证据 |
| metadata | P2ProjectionMetadata | 是 | `{scope:A.Scope,memory_type,occurred_at}`；长期类型 Episodic/Semantic，未知业务时间为 null |
| metadata_hash | A.Hash | 是 | 仅对上述三字段的规范语义视图计算，具体输入及编码见本表后的说明 |

metadata_hash 的摘要输入严格为 `JCS({scope, memory_type, occurred_at})`：JCS 指 JSON 规范化序列化，序列化结果使用 UTF-8，再计算 SHA-256；A.Hash 的 byte_encoding 为 jcs-utf8、range 为 null。Scope 中的可空维度和未知 occurred_at 均显式保留 null。仅更换批准证据引用不改变该摘要，Scope、类型或发生时间的实际值变化必须改变摘要。`owner_metadata_evidence_ref` 由 A 留作批准依据，不进入这三个字段的摘要输入，也不作为索引属性发送。

| EmbeddingModelBinding 字段 | 类型 | 定义 |
|---|---|---|
| model_id / model_version | Id / Version | 实际固定模型身份与版本 |
| dimension / dtype | UInt / String | 正维度与实际数值类型；不能静默有损转换仍用原摘要 |
| embedding_schema_version / preprocessing_version | Version | 向量输出格式 / 输入处理与 Query-Passage 编码规则 |
| retrieval_space_ref / model_contract_ref | ContractRef | 可比较空间 / 输入上限、数值编码、归一化等合同 |

`projection_set_id/build_fingerprint/manifest/provider_item_key` 是 B 完整集合及反查信息，应在 B/A 持久映射中绑定准确 P2 目标和原操作；若要求写进 P2 metadata，必须另行对齐字段与指纹版本。不能直接向已有 `P2ProjectionMetadata` 三字段增加成员后仍声称旧摘要不变。B 的 `user_id/business_task_id/authorization_ref` 与 A.Scope 的适配也须显式确认，不得丢失必要授权约束。

#### 2.4.3 期望返回的数据：P2MutationResult

下表完整列出向量 upsert/delete 及其操作查询共用的返回字段；目标、载荷和可查询状态分别在本文 2.4.2、2.5.3 定义。

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| operation_kind | upsert / delete | 是 | 原变更动作 |
| target | P2ProjectionTarget | 是 | 原准确目标 |
| provider_idempotency_key | Id | 是 | 原幂等键；不是本次 request ID |
| request_fingerprint | A.Hash? | 是 | 实际已关联载荷语义；仅回显不证明真实写入 |
| provider_operation_ref | ProviderRef? | 是 | 原 P2 操作；没有时必须支持按原键查证 |
| operation_status | String | 是 | accepted/running/completed/rejected/failed/not_found/unknown |
| raw_status | String? | 是 | 原 P2 状态，无则 null |
| target_state | P2TargetState? | 是 | 已取得的对象/索引/删除事实，定义见 2.5 |
| resubmit_assessment | P2ResubmitAssessment? | 是 | 与 1.5 相同的三项安全重发事实；绑定向量原目标/动作/键/指纹 |
| evidence | `List<P2Evidence>` | 是 | 本次原操作关联和进度证据 |

批量只是一种传输方式。A/B 必须能定位每个目标的结果、缺项、错误和原操作；若用等价范围证明，须证明精确覆盖请求中的全部必需 item。`inserted=2` 或批次成功状态不能替代两项完整结果。

#### 2.4.4 示例与异常处理

下面是 B 汇总两个 chunk 的**流程观察示例**，不是新增 P2 批量 RPC 报文；标识均为示意。

```json
{
  "projection_set_id": "projection-set-42-v3",
  "manifest_ref": "manifest-42-v3",
  "expected_chunk_ids": ["chunk-1", "chunk-2"],
  "observations": [
    {"chunk_id": "chunk-1", "provider_status": "READY", "evidence_ref": "evidence-item-1"},
    {"chunk_id": "chunk-2", "provider_status": "PENDING", "evidence_ref": "evidence-item-2"}
  ],
  "projection_state": "Building"
}
```

chunk-1 的 READY 也须有真实绑定、持久化、可查询性证据；示例引用本身不构成证明。chunk-2 继续查询；B 不把整集合设为 Ready，不重写已确认项，也不从 manifest 移除未完成项。若 chunk-2 为 UNKNOWN，先查原操作而非重新生成向量。

**下一步。** A 保存原观察并调用 BP2-06。对应 BP2Q-04/05、BP2T-06/07/08。

### 2.5 步骤四：查询原向量操作并核验准确对象（BP2-06）

#### 2.5.1 查询原操作：P2OperationQueryInput

逻辑签名：`query_operation(context, P2OperationQueryInput) → P2MutationResult`，对应 RP2-07。用于受理后等待、首次回复丢失、写删超时与重启恢复。

| 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | 原准确目标，不换版本/空间 |
| operation_kind | upsert / delete | 是 | 原动作 |
| provider_operation_ref | ProviderRef? | 是 | 已知则提供；首次回复丢失可 null |
| provider_idempotency_key | Id | 是 | 原 P2 幂等键；无 operation_ref 时仍能查证 |
| request_fingerprint | A.Hash | 是 | A 已保存的原载荷语义 |

返回 2.4 的完整 P2MutationResult。client-key 与 provider operation 若矛盾，不能任选一个继续；普通 not_found 不允许重写。安全重发须满足 1.5 的三项事实，并沿用 A 原操作次数和恢复窗口。

#### 2.5.2 核验准确目标：P2TargetQueryInput

逻辑签名：`get_projection(context, P2TargetQueryInput) → P2TargetState`，对应 RP2-08。它查询对象/索引事实，不代替 B 的 MemoryRead 或最终资格判断。

| 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | 准确目标，已知物理 ID 必须匹配逻辑身份 |
| expected_request_fingerprint | A.Hash? | 是 | 要核对的原 upsert 载荷；删除核验不要误填无载荷的 delete 指纹 |
| related_operation_ref | ProviderRef? | 是 | 已知原操作引用；未知可 null |
| include_payload | Bool | 是 | 是否回读实际向量/元数据；已有等价绑定证明时可 false |

#### 2.5.3 期望返回的数据：P2TargetState

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | 本次实际核验目标 |
| object_present | Bool? | 是 | 准确对象存在性；权威查无才 false |
| stored_request_fingerprint | A.Hash? | 是 | 实际存储载荷对应指纹，不能只是请求摘要的回显 |
| stored_payload | P2ProjectionPayload? | 是 | 实际回读载荷；未回读/对象不存在/无法取得时 null |
| durable | Bool? | 是 | 是否满足签收的持久性条件 |
| index_queryable | Bool? | 是 | 是否满足目标空间查询可见性；不保证任意 Query 都能命中 |
| delete_confirmed | Bool? | 是 | 准确目标删除确认，非删除观察时可 null |
| late_write_barrier_confirmed | Bool? | 是 | 旧在途写已排空或受有效屏障约束 |
| binding_evidence | `List<P2Evidence>` | 是 | 实际向量、模型、Schema、正文关联、检索属性绑定 |
| completion_evidence | `List<P2Evidence>` | 是 | 持久/可查询或删除/屏障完成依据 |
| observed_at | Timestamp? | 是 | 来源观察时间 |

允许回读载荷重算摘要，或提供合同认可的等价存储绑定证明。ANN 搜索未命中不能证明对象不存在；总 count、SegmentStats、Pod Healthy、索引任务完成也不能代替准确目标证明。是否需要 search probe 由 A/P2/B 的完成契约决定，本文不增加每项强制在线搜索。

| 返回事实 | A 的机制处理 | B 消费 |
|---|---|---|
| 原操作可信受理 | ACCEPTED | 保持非 Ready，跟踪原操作 |
| 明确仍执行/索引构建中 | PENDING | 保持非 Ready，按预算补取 |
| 对象存在、真实绑定正确、durable=true、index_queryable=true，证据完整 | READY（upsert） | 进入完整集合 Ready Guard |
| 有据拒绝/明确失败 | FAILED | 保存原原因、可能残留与恢复条件 |
| 超时、关联不明、必要证据缺失、普通 not_found | UNKNOWN | 不当成功，也不当已确认失败；继续核验 |

已收到 completed 但缺必要证据，仍不能 READY；仅在有明确执行中证据时才能 PENDING，其余保持 UNKNOWN。历史 READY 是当时事实，不证明对象此刻仍存在。

### 2.6 步骤五：B 对完整集合执行 Ready Guard

A 一致保存原观察、ProviderResult 和原操作查询入口后交 B。B 逐项保存 ProjectionItem，再重新读取当前主事实；只有 B 能更新 ProjectionState。

| Guard 核验项 | 必需输入 | 不满足时 |
|---|---|---|
| 当前 Memory 与资格 | memory_ref、state_version、Active、长期类型、Scope、业务有效期 | 失效/换版则拒绝旧结果，原集合 Stale 或按原因保持非 Ready |
| 来源内容 | source_binding、精确版本/摘要、可读证据；Artifact 另有 accepted 资格 | 不用新正文解释旧向量；缺证据不 Ready |
| 完整 manifest | manifest_ref/hash、全部必需 chunk、build_fingerprint | 缺项只保留部分结果，不缩小预期清单 |
| 每项实际绑定 | 准确五元组、原操作、来源/向量摘要、模型/空间/dtype/Schema/metadata/资源 | 不拼接不同集合或模型的成功部分 |
| 每项可查询性 | 对象存在、机制完成及可查询证据、有效观察窗口 | UNKNOWN、延迟索引或证据不足均不 Ready |
| 发布屏障 | 当前 memory_version、state_version、build_fingerprint、有效租约 | CAS 失败不覆盖新结果；外部残留另行对账 |

通过后保存不可变 ReadyProof，并条件发布当前 ProjectionSet=Ready。Proof 至少包含 ready_proof_id、projection_set_id、memory_ref、eligibility_state_version、build_fingerprint、manifest_ref、content_evidence_ref、items_evidence_ref、guard_contract_ref、evaluated_at。

**状态不能混用：** A 的 ProviderResult 为 ACCEPTED/PENDING/READY/FAILED/UNKNOWN；B 的 ProjectionState 为 Pending/Building/Ready/Failed/Stale。B 不新增 ProjectionState.Unknown，也不把 P2 返回的 READY 直接写入领域状态。

### 2.7 步骤六：发布读取资格、Task 结果与变化信号

| 消费方 | B 提供什么 | 完成边界 |
|---|---|---|
| A MemoryRead / CanonicalLoad | 当前 Memory/版本/状态/Scope、Ready 集合、正式内容映射及获准范围 | A 仍须在实际使用/发出前复核；P2 搜索命中不替代资格 |
| B AsyncTask | 已验证目标、逐项证据、未完成列表 | 全部 required_outputs 满足才 Succeeded；task_id 存在不等于可召回 |
| C / RF | MemorySignal：signal_id/version、Memory/version、source_state_version、表示/资格变化、payload_hash | B 先持久 Signal/Outbox；C 持久消费后才有匹配 Ack；P2 不确认 Signal |

C 尚未消费 Signal 不应阻塞已经满足的长期读取能力，但未确认交接须如实保留。A/B 的 AccessTrace 由实际业务阶段产生，RF 管 Schema/摄取；P2 IO 次数不能当作 Context 已使用。

### 2.8 后续分支：换版、缺项修复与同目标重建

| 变化或请求 | 本文采用的处理 | 必须保留的限制 |
|---|---|---|
| 同目标、同载荷重投 | 附着 A 原 operation，查询/返回已保存结果 | 新 Task/attempt 不生成新物理 ID，不刷新预算 |
| 同目标、不同载荷 | 拒绝同键冲突；B 重新评估合法新输入 | 不原地覆盖旧向量、重算旧指纹或随机换键 |
| 真实 Memory/model/Schema 或合法片段身份变化 | B 新建完整构建输入，经 A 以准确新目标执行 | B 决定新旧资格切换与旧目标清理；旧结果不能覆盖当前版本 |
| manifest 部分项尚未确认 | 查询原操作；仅在已确认安全条件下继续原项 | 不能对 UNKNOWN 项盲重写；已确认项保留 |
| 历史 Ready 对象后来消失 | B 先标 Stale，新增关联修复 Task，保留原成功历史 | 新修复 Task 本身不授权同五元组再次写入 |
| 明确同五元组 rebuild | 保留 B 的业务修复需求；按当前 Recall 草案，A 受理前返回 `PROJECTION_REBUILD_UNSUPPORTED`，P2 调用数为 0 | 代际身份、旧写失权、删除屏障与新指纹契约签收后才启用；不得伪造新 memory_version 绕过 |

新 projection_set_id 只表示 B 的新构建记录，不能自动扩展 A 的物理目标身份。以上差异关联 BP2Q-04/05/06、Remember AL-A02/AL-P203/AL-P204 和 Recall 同版本重建待确认项，详见 5.3。

### 2.9 本流程的协议映射与联调要求

| 需求 | 已观察到的协议声明 | 需要对齐 |
|---|---|---|
| 写向量 | `InsertVector(collection,records)`；VectorRecord 为 id、repeated float values、graph_node_id?、metadata_json?；返回 inserted | 是否有 upsert/同键冲突语义、客户端幂等、目标映射、逐项结果与真实绑定；float 与实际 dtype 的兼容 |
| 查原操作 / 查准确对象 | 此 VectorService 样本未声明独立操作查询、准确 ID get/readiness | 提供现有其他入口或扩展方案；SearchVector/SegmentStats 不能替代全部证明 |
| 删除向量 | `DeleteVectors(collection,ids) → deleted` | 精确版本/条件、幂等、索引退出与防迟到复活，见第四章 |

BP2T-01 覆盖正文到 Ready；BP2T-05 覆盖 Artifact；BP2T-06/07 覆盖部分写、五态、同维不同空间及完整性；BP2T-08 覆盖旧 Worker、转后台和迟到写。另补充“同五元组 rebuild 在 P2 前拒绝”和“新集合不能绕过旧目标指纹”场景，作为 BP2T-08 的本次展开项。

联调证据须同时展示 P2 物理事实、A ProviderResult 适配和 B Ready Guard 三个环节；只验证 InsertVector 返回计数不能完成长期构建验收。

## 3. 正文读取流程：从 B 批准映射到可信字节

### 3.1 流程入口、顺序与分支

本流程同时供第一章写后确认、第二章 Artifact/Passage 输入加载，以及 R10 的 A 在线 CanonicalLoad 使用。每次读取均先有用途、授权和准确目标；在线 Recall 的检索、排序与 ContextPack 仍由 A 负责。

~~~text
调用方提出读取用途 / 原 Memory 或候选版本 / Scope / deadline
  → B MemoryRead 核对当前资格，返回 ContentBinding + ApprovedContentRange
  → 已有 W1 / Working 可信内联字节：本地校验，避免重复下载
  → 需要物理读取：缺必要元数据时 head
       → 获准可选 Prewarm 路径，或 canonical get/get_range
       → 校验版本 / 编码 / 实际范围 / 字节数 / 可信期望摘要
  → 返回可信正文或明确缺口
  → A 组装发出前再次复核 B 当前资格；记录实际访问阶段（R10/R11）
~~~

| 顺序 | 调用方 → 提供方；能力 | 本步产物 | 下一步 |
|---|---|---|---|
| 1 | A/B → B 资格与内容映射；3.2 | 当前授权快照、准确 binding 和批准范围 | 未获准不构造物理读取 |
| 2 | A/B → OBJ-001；[3.3 正文元数据核验（BP2-02）](#33-步骤二按需-head-与-checksumbp2-02) | 同版本大小/编码/摘要及版本条件 | 判断是否可在预算内读取 |
| 3 | A/B → OBJ-001；[3.4 正文读取（BP2-02）](#34-步骤三读取获准的精确正文或片段bp2-02) | 实际字节与读取证据 | 验证实际响应，不以状态码直接采用 |
| 4 | A/B 本地校验；3.5 | 可信内容或明确失败/缺失事实 | 按用途继续构建或 Recall |

### 3.2 步骤一：取得当前资格与批准范围（A/B 交接）

| 交接 | 必需数据 | 采用条件 |
|---|---|---|
| 调用方 → B | 原 Memory/version 或稳定候选引用、read_purpose、Scope、deadline | 原候选不被静默替换成新版本 |
| B → 调用方 | MemoryReadSnapshot：memory_snapshot_id、requested/current_memory_ref、state_version、decision、当前类型/状态及资格证据 | decision=eligible 才继续；excluded 与 unverifiable 分开 |
| B → 调用方 | ContentBinding：content_binding_ref、representation_id、content_version、locator、媒体/编码/大小 | 来自可信映射；不猜物理 key，不使用 mutable latest |
| B → 调用方 | ApprovedContentRange：whole_content/byte_range、边界、expected_hash、approval_evidence_ref | 获准范围及摘要指向同一字节域；whole_content 转成明确 `[0,byte_size)` 后调用 |

Working 资格检查当前 Session/业务任务、业务期限和主事实；Redis miss/TTL 仅表示副本观察。长期候选必须匹配 B 当前有效 Ready 集合。CanonicalLoad 契约由 B 拥有，实际字节权威由 Durable Provider 拥有；物理 RPC 可以由 A 的读取适配器执行，不要求正文绕经 B 再传输一次。

### 3.3 步骤二：按需 head 与 checksum（BP2-02）

#### 3.3.1 功能与输入

用于获取必要对象大小、版本、编码及摘要事实。逻辑签名：`head_content(context, BP2ContentMetadataInput) → BP2ContentMetadataResult`。已有可靠映射或 put/get 响应覆盖所需事实时可跳过，不要求新增独立 checksum RPC。

| BP2ContentMetadataInput 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| locator | BP2ContentLocator | 是 | 需要核验的准确版本或不可变目标 |
| scope | Scope | 是 | 当前获准资源和正文范围 |
| approved_range | ByteRange | 是 | 本次需要解释的字节范围；不强迫 Provider 返回未支持的片段摘要 |
| version_condition_ref | ProviderRef? | 是 | 与后续 get 一致的条件，或使用已证实不可变定位 |

#### 3.3.2 期望返回与异常

| BP2ContentMetadataResult 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| status | String | 是 | ok/not_found/version_mismatch/unknown/failed |
| content_state | BP2ContentObservation? | 是 | 1.4 定义的同版本对象元数据；ok 时非 null |
| evidence | `List<P2Evidence>` | 是 | 本次查询、版本与摘要覆盖依据 |

HEAD 的整对象 hash 只能证明该版本全文，不能重新标注为局部片段 hash。Provider 只给 MD5/BLAKE3，而本次可信期望为 SHA-256 时，不能直接比较不同算法；可按合同回读字节计算所需摘要。ETag 在分片/加密等情形下的含义必须确认。

示例：HEAD 观察 `object-v3`，随后 GET 返回 `object-v4`，即使大小相同也必须拒绝；不能把两次回包拼成 v3 的读取证明。HEAD 404 不更改 B 的 Memory 生命周期，也不自动授权重新写入。

### 3.4 步骤三：读取获准的精确正文或片段（BP2-02）

#### 3.4.1 功能与输入：BP2ContentReadInput

逻辑签名：`get_content / get_content_range(context, BP2ContentReadInput) → BP2ContentReadResult`。

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| content_binding_ref | Id | 是 | B 批准的业务映射；P2 可不直接接收此本地 ID，但适配器须保留关联 |
| locator | BP2ContentLocator | 是 | 准确物理版本或不可变对象 |
| scope / read_purpose | Scope / String | 是 | 当前授权与用途；具体用途值由调用契约固定 |
| approved_range | ByteRange | 是 | 精确半开字节区间，不在失败/超限时缩短并冒充完整读取 |
| expected_hash | Hash | 是 | B/原内容契约提供的可信范围摘要；可由调用方本地持有，不强制原 RPC 接收 |
| version_condition_ref | ProviderRef? | 是 | 条件读/不可变目标依据；不能猜 ETag 就是版本条件 |
| max_response_bytes | UInt | 是 | 调用前已预留的实际正文接收上限；解压后的限制也需合同明确 |

#### 3.4.2 期望输出：BP2ContentReadResult

| 字段 | 类型 | 必填 | 定义与采用条件 |
|---|---|---|---|
| status | String | 是 | ok/partial/not_found/version_mismatch/unknown/failed；是物理读取事实 |
| locator | BP2ContentLocator | 是 | 响应对应目标；实际版本仍需证据，不只靠回显 |
| data | bytes? | 是 | 实际接收字节；ok 时非 null；失败残留须隔离 |
| returned_range | ByteRange? | 是 | 实际范围；ok 时必须等于 approved_range |
| returned_bytes | UInt | 是 | 本次实际响应字节数；不是 Base64 文本长度或整个对象大小 |
| coverage | String | 是 | complete/partial/unavailable，相对于本次批准范围 |
| content_state | BP2ContentObservation? | 是 | 同版本元数据；ok 时具备足够的对象/版本/编码事实 |
| readable | Bool? | 是 | 本次准确版本是否可读；unknown 不填 false |
| read_path | String | 是 | canonical/prewarm/unknown；实际来源，不按计划位置填写 |
| read_source_ref | EvidenceRef? | 是 | 实际 Provider/放置/热副本观察；可缺，不能伪造 generation/action 归因 |
| evidence | `List<P2Evidence>` | 是 | 字节、范围、版本与本次响应关联 |

当实际收到 partial 字节时保留 partial 和已接收字节，不把它作为完整批准范围的成功内容。原 P2 没有返回 body/完整计数时，只保存本地断流观察，不伪造完整 BP2ContentReadResult；本地接收层计数须标明来源。

#### 3.4.3 完整返回示例与字节校验

请求获准读取 `binding-42-v3` 的 `[0,15)`，expected_hash 为 1.4 所列全文 SHA-256。以下是完整 `BP2ContentReadResult` 适配视图示例，公共响应上下文另带；证据/合同/对象标识均为示意。JSON 中 bytes 使用标准 Base64。

```json
{
  "status": "ok",
  "locator": {
    "provider_ref": "p2-e2-demo",
    "resource_ref": "resource-demo",
    "object_ref": "object-42",
    "object_version": "object-v3",
    "generation": null,
    "address_contract_ref": "address-demo-v1"
  },
  "data": "5o6l5Y+j57qm5a6a44CC",
  "returned_range": {"start": 0, "end": 15},
  "returned_bytes": 15,
  "coverage": "complete",
  "content_state": {
    "locator": {
      "provider_ref": "p2-e2-demo",
      "resource_ref": "resource-demo",
      "object_ref": "object-42",
      "object_version": "object-v3",
      "generation": null,
      "address_contract_ref": "address-demo-v1"
    },
    "object_present": true,
    "actual_bytes": 15,
    "actual_hash": {
      "algorithm": "sha256",
      "value": "598bda3350ccd8b28477531944d5cefe46b467c5444fb90c05d806e323f4976b",
      "coverage": "whole_content",
      "byte_start": null,
      "byte_end": null
    },
    "media_type": "text/plain",
    "encoding": "utf-8",
    "etag": null,
    "durable": true,
    "readable": true,
    "observed_at": "2026-09-07T02:00:00.000Z",
    "binding_evidence": [{
      "kind": "exact_content_binding",
      "contract_ref": "content-demo-v1",
      "provider_evidence_ref": "evidence-binding-demo",
      "observed_at": "2026-09-07T02:00:00.000Z"
    }],
    "completion_evidence": [{
      "kind": "durable_readable",
      "contract_ref": "content-demo-v1",
      "provider_evidence_ref": "evidence-completion-demo",
      "observed_at": "2026-09-07T02:00:00.000Z"
    }]
  },
  "readable": true,
  "read_path": "canonical",
  "read_source_ref": null,
  "evidence": [{
    "kind": "exact_range_read",
    "contract_ref": "content-demo-v1",
    "provider_evidence_ref": "evidence-read-demo",
    "observed_at": "2026-09-07T02:00:00.000Z"
  }]
}
```

解码结果必须是 UTF-8 的“接口约定。”、15 字节；A/B 自己计算实际 SHA-256 并与可信 expected_hash 比较。本例整对象与批准范围恰好一致；不能据此把全文摘要用于任意子区间。物理持久性证据可以来自同一不可变版本的可靠写入合同与回执，不要求每次 GET 重新执行持久化。

### 3.5 步骤四：消费读取事实、回退与访问观察

| 情况 | 调用方处理 | 下游约束 |
|---|---|---|
| 同版本、正确范围/编码/hash、权限成立 | 形成已验证内容结果 | 按用途继续 Artifact、Passage 或 Recall |
| not_found / timeout / unknown | 保存准确目标和原因；必要时进入 R14 对账 | 不将 Memory 自动改为 Deleted/Expired |
| partial / 错范围 / 错版本 / 摘要不符 | 隔离返回内容，记完整性/覆盖缺口 | 不进入 Context，也不生成已确认来源向量 |
| 获准 Prewarm 命中且内容可信 | 按实际来源采用 | Prewarm 是同一正文的可选加载路径，不新增候选源 |
| Prewarm 未采用，需要 canonical 回退 | 按唯一回退责任方、剩余预算读取正式版本 | 不重复执行 Provider 已完成的内部回退，不在线触发 Promote/Prefetch |
| 正文可信但实际 tier/action 归因缺失 | 基础读取可完成，归因保留不可验证 | 不把 C 计划、预期位置或 IO hit 当作实际使用证据 |

所有读取、失败残留、校验失败、重试/回退实际字节均纳入调用方预算；本地取消不抹去已消耗字节。A 在线 Recall 沿用其读取预留、调用次数、重启保守记账规则；B 后台任务沿用 B 的原 Task 预算，不能直接套用另一条执行的额度。

A 负责 CanonicalLoaded/ContextSelected/ContextEmitted 等真实访问事件，B 记录自己实际观察的阶段；无模型回执时 `model_used` 保留 null。RF 负责 Schema/持久摄取，C 消费；B 额外消费 A 的 AccessTrace 仍依原授权待确认项，本文不新增订阅。

### 3.6 本流程的协议映射与联调要求

| 需求 | 协议样本 | 对接要求 |
|---|---|---|
| 全文 GET | `GetObject(bucket,key) → ObjectBytes(data,meta)` | 只在获准全文且版本可固定时使用；不以全文下载替代无权限的 Range |
| Range GET | `GetObjectRange(bucket,key,start,end?)` | 原 end 含上界；本文 `[start,end)` 适配为 `end-1`。例如 `[0,15)` 发送 0～14；空区间不得发送 |
| HEAD/checksum | ObjectMeta 为 bucket、key、etag、size、md5_hex?、blake3_hex? | 样本未显式给 object_version/编码/持久性；需条件读取或不可变映射及相应证据 |
| 实际来源/Prewarm | 此 ObjectBytes 样本未声明实际 tier、generation、action 或回退轨迹 | 依既有 A/C/Provider 契约可选提供；缺失不能伪造归因 |

BP2T-04 应覆盖正确 Range、首尾边界、跨版本 HEAD/GET、部分字节、ETag 非全文 hash、不同摘要算法、断流和接收超限；BP2T-01 覆盖正式正文可回溯。该契约与 Recall 的 RP2-02/03 需同一次对齐，避免 B 写入承诺与 A 读取解释不一致。

## 4. 失效与恢复流程：先建立业务屏障，再证明物理收敛

### 4.1 流程入口、顺序与分支

触发包括更正/替代、到期、删除、撤权、取消、超时、重启、对象缺失、未完成上传和孤儿残留。业务删除、缓存释放、对象擦除是不同目标，不能统称“删掉 Memory”。

~~~text
R12 业务失效：B 授权决定 → 提交 state_version / 读屏障 / 墓碑及待办
  → 固定逐表示、逐版本清理范围 + 共享引用 / 保留检查
  ├─ 正文 / Artifact：B → OBJ-001 → E2 delete / query / inventory
  ├─ Vector：B → A VEC-001 → E1 delete / query / get_projection
  ├─ Working：B → 实际 Redis Provider 条件释放
  └─ Prewarm / 在途迁移：B 交失效约束 → C 动作 / Provider 反馈
  → 按要求的完成层次核验 → CleanupRecord / ReconciliationRecord

R14 故障恢复：恢复 B 权威事实与屏障 → 取得执行权 → 重新核验资格
  → 查询原操作及准确对象 → 比对期望 / 实际集合
  → 补登记 / 查询等待 / 获准同键重发 / 失效清理 / 受限修复 / 人工
  → 再次核验 → 有据收敛，或保留未知及下一步
~~~

| 顺序 | 调用方 → 提供方；能力 | 本步产物 | 下一步 |
|---|---|---|---|
| 1 | B 内部；4.2 | 业务读屏障、准确 CleanupRecord 和授权范围 | 无权读取立即阻断，物理清理随后推进 |
| 2 | B → OBJ-001 / E2；[4.3 正文删除与核验（BP2-07）](#43-步骤二删除精确正文与-artifact-并查询结果bp2-07) | 原删除操作、实际完成层次 | 原操作查询与残留核验 |
| 3 | B → A → VEC-001 / E1；4.4 | 准确向量删除、索引退出与屏障事实 | A 机制完成交 B 清理收口 |
| 4 | B/A 消费 Provider inventory / 恢复观察；4.5 | 范围、游标/水位、残留和恢复限制 | 与完整预期集合及墓碑比对 |
| 5 | B 对账；4.6 | 原操作/当前资格对齐后的下一步与复核证据 | 不盲重试、不复活旧版本 |

### 4.2 步骤一：B 固定失效屏障与清理意图

| B 记录 | 必须包含 | 作用 |
|---|---|---|
| 业务决定与版本状态 | memory_ref、state_version、Status、有效期/撤权/墓碑、decision_ref | 先阻断不再获准的读取与结果发布；不等待 P2 完成 |
| CleanupRecord | cleanup_id、memory_ref、representation_ref、cleanup_scope、required_completion | 精确到表示/版本/generation；不从单一 memory_id 推断全版本删除 |
| 范围与保留依据 | shared_reference_check_ref、retention 约束、批准对象/版本清单 | 共享对象仍被有效 Memory 引用时，不直接物理擦除 |
| 恢复关联 | 原 operation/task、在途写入引用、下一次核验与 Owner | Cancel 只撤 B 写回资格，继续观察可能存在的外部副作用 |

cleanup_scope 沿用 `specified_version / specified_representation / approved_all_versions`；required_completion 沿用 `logical_invisible / current_version_removed / physical_erased`。它们属于 B 清理记录，P2 接收的是已解析、可授权的物理范围，不由 P2 自行扩大业务范围。

Working 向长期交接时，先确认可靠正文与映射，再按策略完成所需投影和保护窗口后释放旧 Working。普通 Demote/Release 由 C 对获准表示编排，并先确认低层 serving/authoritative copy 可用；P2 执行 Copy/Verify/Cutover/Reclaim。业务删除则以失效屏障为前提，不能用 Pin/Promote 把 Deleted/Expired 恢复为可读。

### 4.3 步骤二：删除精确正文与 Artifact 并查询结果（BP2-07）

#### 4.3.1 功能与输入：BP2ContentDeleteInput

逻辑签名：`delete_content(context, BP2ContentDeleteInput) → BP2ContentMutationResult`。B 在授权、屏障、共享引用和保留检查通过后，按准确对象发起；样本候选方法为 DeleteObject。

| 字段 | 类型 | 必填 | 定义与约束 |
|---|---|---|---|
| client_operation_ref / idempotency_key | ProviderRef / Id | 是 | 稳定删除操作身份，与 put 区分 |
| request_fingerprint | Hash | 是 | 原删除语义、准确目标、范围及完成要求的指纹 |
| locator | BP2ContentLocator | 是 | 本次批准删除的准确对象版本 |
| scope | Scope | 是 | 当前删除授权范围 |
| required_completion | String | 是 | logical_invisible/current_version_removed/physical_erased；需与 P2 能证明的层次明确映射 |
| precondition_ref | ProviderRef? | 是 | generation/版本条件或等价排空屏障，不能使用 B state_version 代替 |
| related_write_refs | `List<ProviderRef>` | 是 | 已知历史在途写关联；空列表不证明不存在其他迟到写 |
| cleanup_authorization_ref | EvidenceRef | 是 | B 已完成准确范围、共享引用、retention 检查的依据；有效凭据仍走正式鉴权 |

全版本清理需明确获准范围、快照/水位和条件；本视图按精确目标执行。若使用 Provider 批量/全版本 API，须证明逐项目标或等价范围完整，不能把一次不带 version 的 DeleteObject 当全版本擦除。

#### 4.3.2 期望返回：BP2DeleteVerification

返回 1.4 的 `BP2ContentMutationResult`，operation_kind=delete；未完成时继续 BP2-03。完成性由下面的 delete_verification 与操作/目标证据共同解释。

| 字段 | 类型 | 必填 | 定义与采用条件 |
|---|---|---|---|
| locator | BP2ContentLocator | 是 | 实际核验的准确目标，与删除授权一致 |
| required_completion | String | 是 | 原要求的完成层次 |
| confirmed_level | String? | 是 | 已有证据的 logical_invisible/current_version_removed/physical_erased；未确认则 null |
| object_present | Bool? | 是 | 准确目标当前存在性；不是全部历史版本存在性 |
| late_write_barrier_confirmed | Bool? | 是 | 受影响旧写不会重新创建目标的屏障/排空证据 |
| residual_refs | `List<ProviderRef>` | 是 | 已知残留；只有覆盖完整时空集合才证明范围内无残留 |
| verification_coverage | String | 是 | complete/partial/unavailable，相对于原授权清理范围 |
| retention_constraint_ref | EvidenceRef? | 是 | Object Lock、共享引用、保留期等实际限制 |
| observed_at | Timestamp? | 是 | 来源核验时间 |
| evidence | `List<P2Evidence>` | 是 | 对象版本、实际层次、残留覆盖及屏障证据 |

`confirmed_level` 不通过字符串排序判断“更高”；只有合同明确的层次覆盖关系才可满足原要求。soft delete / delete marker 通常只证明相应可见性变化，不能直接标 physical_erased。B 本地读屏障已经成立，也不等于 P2 logical_invisible 已被验证。

#### 4.3.3 示例、失败与下一步

下面是一个**未完成的完整 BP2DeleteVerification 示例**：当前版本已移除，但原任务要求物理擦除，且仍有保留残留。它不能使 CleanupRecord.confirmed。

```json
{
  "locator": {
    "provider_ref": "p2-e2-demo",
    "resource_ref": "resource-demo",
    "object_ref": "object-42",
    "object_version": "object-v3",
    "generation": "generation-v3",
    "address_contract_ref": "address-demo-v1"
  },
  "required_completion": "physical_erased",
  "confirmed_level": "current_version_removed",
  "object_present": false,
  "late_write_barrier_confirmed": null,
  "residual_refs": ["retained-copy-42-v3"],
  "verification_coverage": "partial",
  "retention_constraint_ref": "retention-evidence-demo",
  "observed_at": "2026-09-07T03:00:00.000Z",
  "evidence": [{
    "kind": "specified_version_removed",
    "contract_ref": "delete-demo-v1",
    "provider_evidence_ref": "delete-observation-demo",
    "observed_at": "2026-09-07T03:00:00.000Z"
  }]
}
```

重复删除仍查原操作；accepted、HTTP 200、删除计数、一次 HEAD 404 均不能证明完整擦除。条件不符需重新核验准确目标，不能去掉 generation 强删。未知保持 `RM_CLEANUP_PENDING`，继续查询并保留业务屏障；无可行自动收敛路径时留下明确责任方和人工处理原因。

### 4.4 步骤三：经 A 删除向量并核验不再复活（BP2-07）

逻辑签名沿用 `delete_projection(context, P2DeleteInput) → P2MutationResult`，对应 RP2-09；后续用 2.5 的 query_operation / get_projection。B 指定目标与授权，A 保存退役标记和原操作后执行，删除不调用 Embedding。

| P2DeleteInput 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | B 批准的准确五元组/租户/Provider/空间 |
| provider_idempotency_key | Id | 是 | 稳定 delete 键，与原 upsert 键区分 |
| request_fingerprint | A.Hash | 是 | 删除语义摘要，不包含新向量载荷 |
| precondition_ref | ProviderRef? | 是 | 条件删除/屏障；采用排空方案可 null，但完成证据仍必需 |
| related_upsert_keys | `List<Id>` | 是 | 已知历史在途写关联，不能仅凭列表断言不存在其他受影响写 |

| A 形成 delete READY 的必要事实 | 期望值/要求 |
|---|---|
| 准确 target | 与批准清理的版本、chunk、模型、Schema、租户和空间一致 |
| object_present / index_queryable | false / false |
| delete_confirmed / late_write_barrier_confirmed | true / true |
| completion_evidence | 精确删除、索引退出及旧写排空/屏障的可验证依据 |

这只表示向量机制删除完成，不自动证明 E2、Working、Prewarm、备份中的相应字节已全部清理。删除 Ack 后仍可检索，或旧 upsert 仍可能生效，均不能形成安全完成结论。A 退役标记不替代 B 的生命周期，也不独自证明 P2 已阻止迟到写。

### 4.5 步骤四：获取残留清单与恢复观察（BP2-07 / BP2-08）

#### 4.5.1 Inventory 输入、输出与完整性

逻辑签名：`inspect_inventory(context, BP2InventoryInput) → BP2InventoryResult`。它是残留/集合核验需求，可由分页枚举、逐项查询或等价清单证明满足；向量 inventory 由 A 对接，不新建 B 的 E1 Port。

| 输入字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| resource_ref / scope | ProviderRef / Scope | 是 | 获准资源与查询范围 |
| inventory_kind | String | 是 | objects/versions/uploads/projections；每种支持情况需确认 |
| target_set_ref | EvidenceRef | 是 | 精确授权查询/清理范围，或完整 manifest 的适配依据；不能做无授权全库扫描 |
| cursor | ProviderRef? | 是 | 原轮分页游标；首次 null，失效须按合同重新开启扫描 |
| page_limit | UInt | 是 | 有界正整数，符合 Provider 限额 |
| snapshot_ref | ProviderRef? | 是 | 如支持一致快照/水位则固定；无则须解释并发观察限制 |

| BP2InventoryResult 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| resource_ref / inventory_kind | ProviderRef / String | 是 | 实际观察范围和种类 |
| entries | `List<InventoryEntry>` | 是 | 每项 `{object_ref,object_version?,generation?,provider_operation_ref?,metadata_evidence_ref?}`；关联准确目标，未知字段 null |
| coverage | String | 是 | complete/partial/unavailable，定义为整个获准 target_set 的覆盖；单页有结果不表示完整 |
| next_cursor | ProviderRef? | 是 | 后续页；null 只有在有效完成证据下才表示扫描结束 |
| snapshot_ref / watermark | ProviderRef? / String? | 是 | 一致快照/扫描水位及解释依据；不支持保留 null |
| observed_at | Timestamp? | 是 | 来源时间 |
| evidence | `List<P2Evidence>` | 是 | 查询边界、分页完整性和并发保证 |

`entries=[]` 且 partial/unavailable 不能证明没有孤儿。分页结束还须验证所需版本/上传类型确实在覆盖内；普通当前对象列表不等于所有版本清单。长扫描若无一致快照，应保留观察区间与并发变化限制，不制造“全库同时不存在”的结论。

#### 4.5.2 Provider Task、事件与备份恢复

| 观察能力 | 输入 | 期望输出 | 消费边界 |
|---|---|---|---|
| Provider 自有 Task 查询 | 远端 task/operation 或稳定 client ref、资源/Scope | 原任务状态、目标、错误、结果及保留期，关联原操作 | 作为 BP2-03/06 的实现方式；不替代 B AsyncTask，任务完成还要目标核验 |
| 可选对象/操作事件 | 获准范围、消费者、恢复 cursor | event_id、准确对象/operation/version、event_type、发生时间、顺序/重放水位 | 只加速 Pull 查询/失效发现；通知丢失不得使恢复无入口，不等于 MemorySignal |
| 恢复/备份观察 | Profile、资源/影响范围、目标集合、恢复前屏障/墓碑水位 | restore_epoch、backup/tombstone_watermark、恢复阶段、读写限制、证据和观察时间 | 形式可为运维合同+可调用核验；P2 提供物理恢复事实，B/RF 决定恢复后资格放行 |

以上扩展字段的实际类型/必填性与获取方式由承接合同确认，不冒称样本 proto 已声明。外部 Provider Task ID、B async_task_id、业务 business_task_id、C action_id 分别保存；不能拿一种 ID 查询另一种对象。

### 4.6 步骤五：按原操作与当前资格收敛（B 编排）

| 观察或断点 | B/A 下一步 | 关闭事项的条件 |
|---|---|---|
| E2 已写，B 尚未确认/提交未知 | 先查 B operation/提交屏障，再查原 E2 写入与精确目标 | 能补登记且当前授权/承诺成立，或明确孤儿保留/清理方案 |
| 原变更 unknown | 查询原操作；对象核验与负查询证据分开 | 有据完成/失败，或保留未决；不因时间过去直接判断 |
| 明确未执行、无迟到、同键可重发 | 检查原预算/幂等窗口/当前资格后原载荷重发 | 新回执仍按完整目标证据核验 |
| Task/Worker/内联执行者失租 | 拒绝旧本地提交；新执行者附着原操作查询 | fencing 与状态版本匹配；远端残留已纳入追踪 |
| Ready 对象后来丢失 | 先 Stale，建立新修复 Task，保留历史真实成功 | 当前修复路径受 2.8 同目标重建限制；不得重放历史 READY 当已修复 |
| 删除后旧写迟到 | 保持读屏障，查询具体残留，经准确目标清理 | 索引/对象退出及迟到屏障均证实；当前新对象不被旧清理误删 |
| 旧备份恢复 | 先恢复主事实、当前指针、授权和最新墓碑屏障，再核验表示 | 在删除/撤权约束生效前不重新开放读取 |
| 恢复窗口/次数耗尽 | 保留 UNKNOWN/PENDING 和证据，明确 next_check/人工 Owner | 不制造永久失败或成功，也不无限重试 |

ReconciliationRecord 保存 target、trigger、observations、decision、action_refs、verification_refs、outcome、next_check_at 和 reasons；CleanupRecord 只有满足原 `required_completion` 才能 confirmed。后台恢复沿原身份和预算，前台 deadline、Task deadline、租约、Memory 有效期分别处理。

### 4.7 本流程的协议映射与联调要求

| 需求 | 已声明/参考能力 | 未解决的契约点 |
|---|---|---|
| 正文删除 | 样本 `DeleteObject(GetObjectRequest{bucket,key}) → ObjectMeta` | 未显式支持版本/代际/幂等/完整擦除；不可自动解释为全版本删除 |
| 正文 inventory | ListObjects、ListObjectsPaged；返回 ObjectMeta、is_truncated、next_continuation_token | 查询范围隔离、版本清单、未完成 upload 枚举、快照/水位及并发限制 |
| 向量删除/核验 | DeleteVectors；准确对象/原操作查询需另确认 | 精确目标、索引退出、删除前后排序、防迟到复活 |
| 恢复/备份/远端任务 | 参考表中的运行能力，未以本样本证明已具备 | 原操作保留期、恢复屏障、备份水位、RPO/RTO、告警与恢复节流 |

BP2T-08/09/10 分别验证迟到/旧 Worker、精确与全版本清理、进程/Provider/备份恢复；BP2T-02/03 覆盖原操作恢复与孤儿。BP2T-09 应展开共享引用、retention、软删、条件不符、扫描 partial、空结果但覆盖未知、取消后残留和索引延迟退出。

## 5. 对接确认、引用差异与验收

### 5.1 待确认事项与对接材料

以下完整列出本次对接必须回答的问题、对应章节和责任方；均为待确认需求。BP2Q 是本文的问题编号，AL、RM-F 和历史编号仅保留来源对照，不作为跳转目标，也不要求读者另开文件才能了解本表的要求。

| 原问题编号 | 需要回答的具体问题 | 对接项与正文位置 | 责任与原 AL / 补充来源 |
|---|---|---|---|
| BP2Q-01 | 实际 E1/E2 Proto/SDK/API、版本、资源/namespace、身份接入、真实环境及明确不支持项是什么；与本地样本有什么差异 | BP2-01～08；1.2、1.8、2.9、3.6、4.7 | P2/Integration；AL-P201/203、AL-RF01/03；RM-F15 |
| BP2Q-02 | 哪些证据证明 durable/readable；如何固定 object_version 或等价不可变定位；整文/范围摘要、ETag、编码、Multipart、外部引用怎样确认 | BP2-01/02/04；1.4、1.6、2.2、3.3～3.4 | B OBJ-001 / P2 E2；AL-P201；RM-F04 |
| BP2Q-03 | 首次回复丢失时按哪个 client ref 查；负查询何时最终；操作/幂等保留期、可见性窗口、无迟到效果、条件写和孤儿保护窗怎样证明 | BP2-03；1.5、4.6 | B/P2；AL-P202、AL-B01；RM-F05 |
| BP2Q-04 | 五元组怎样关联完整 manifest、build_fingerprint、source/模型/metadata；A ProviderResult/批量逐项怎样映射；集合字段是否进入 P2 载荷和摘要 | BP2-05；2.3～2.4、2.8 | A/B/P2；AL-A02、AL-P203；RM-F08/10 |
| BP2Q-05 | 准确对象、真实存储绑定、durable、index_ready、read-after-write/search probe 分别证明什么；空间切换/延迟索引/负结果窗口怎样处理 | BP2-06；2.5～2.6 | A/P2 提机制事实，B Guard；AL-P203、AL-A02；RM-F09 |
| BP2Q-06 | 指定版本/表示与全部版本删除怎样授权和执行；软删、物理擦除、共享引用、保留、在途写屏障、取消残留、inventory 和备份防复活怎样证明 | BP2-07；4.2～4.7 | B/A/C/P2/RF 各自边界；AL-P204、AL-B05、AL-RF05；RM-F11 |
| BP2Q-07 | 健康/容量的来源、新鲜度/粒度、限额、超时、SDK 重试、主要 Retry Owner、退避、fencing 条件与恢复节流是什么 | BP2-03/08；1.2、1.9、4.5～4.6、5.2 | C 公共 Health Port、B/A 消费、P2/RF；AL-RF02/05、AL-P202；RM-F14 |
| BP2Q-08 | 每项能力的负责人、当前交付时间、可用环境、适配样例、故障用例、真实集成证据和 SLA Profile 是什么 | BP2-01～08；5.2、5.5 | P2/Integration 统筹；AL-P201～204、AL-RF05；历史 D13 仅追溯，不沿用旧日期 |

每项答复提供“支持/部分支持/不支持”、真实方法/版本、请求响应样例、字段的来源与转换、缺失处理、保证范围和验证证据。鉴权凭据由受控渠道交付，文档中只记接入机制及引用。

向量目标、载荷和操作/对象核验已在本文 2.4～2.5 列出，重建边界在 2.8；正文写入与确认在 1.4～1.6，读取在 3.3～3.4。跨方需要签收的缺口由本节 BP2Q-01～08 汇总，B 不单方面冻结 A 的机制算法或 P2 的完成保证。

### 5.2 运行限额、重试与完成窗口

| 需要回填的契约参数 | 单位/范围 | 必须满足的约束 | 对应 B 参数 |
|---|---|---|---|
| max_request_bytes / max_response_bytes、inline_content_bytes | byte；说明传输前/解码后/解压后的口径 | 正整数；输入/接收边界先验证，不能静默截断正文 | BP-01/02 |
| max_batch_items / max_batch_bytes、max_dimension、max_chunks | 条/byte/维度 | 与 A 模型和完整 manifest 一致；不能缩小集合掩盖失败 | BP-07 |
| request/task/call timeout、finalization_reserve、queue_wait | ms / 绝对 deadline | 子调用取原剩余期限；排队计时；Task 续做不改写原承诺 | BP-02/03 |
| max_attempts / retry_window / backoff / jitter | 次/ms/比例 | 次数含首次，时间+次数双限制；B/A/SDK 不叠加隐式重试 | BP-05 |
| foreground/background concurrency、queue_limit、quota | 并发/条/byte | 前后台分配额；Provider 实际执行配额，健康快照不替代写入准入 | BP-08 |
| lease / heartbeat / claim_batch | ms/条 | heartbeat < lease；旧执行者失权；本地租约不冒充 P2 物理屏障 | BP-06 |
| write/delete visibility bound、negative-query rule | ms/一致性合同 | 说明对象、索引、操作记录分别何时可查及负查询最终性 | BP-05/10 |
| idempotency / operation retention | ms/截止时间或等价规则 | 覆盖原操作重试、故障与恢复窗口；过期不等于未执行 | BP-05/11 |
| orphan_grace / retention / tombstone_retention | ms/截止时间 | 覆盖在途写、旧回调、共享引用、备份恢复风险 | BP-11 |
| inventory cursor lifetime / snapshot / scan limit | ms/条/水位 | 明确完整性边界与并发变化；游标过期不能猜测扫描完成 | BP-10 |
| health freshness / metrics window / recovery phase | 时间/资源范围 | 过期/unknown 不能作为新操作已具备条件的依据 | BP-08/10/12 |
| p95 / p99 / availability / RPO / RTO | ms/比例/时间 | 绑定真实 Payload、并发、资源规格、网络、索引状态和混合负载 | AL-RF05 / 实际 Provider Profile |

本文不虚构生产数值。工作包中的 Embedding 吞吐、压缩比、Working 延迟等指标各有业务和测量范围，不能直接改写成所有 E2/E1 RPC 的统一 SLA。关键契约/参数缺失时，相关真实完成路径保留未就绪，文档和 Simulator 开发可按各自 Lane 继续。

### 5.3 参考材料差异及本次处理

| 差异 | 资料中的不同粒度/表述 | 本文处理及待确认边界 |
|---|---|---|
| Fact Formation 与正文先后 | 节点抽象图先列 Fact Formation 再 Canonical；运行时要求必要正文先证实后确认主事实 | 将身份/意图准备与可靠提交分开；L1 正文证据先于 B 提交，W1 小正文随本地事务提交 |
| 必须外存还是允许可靠内嵌 | 总流程强调 Durable Content；运行时给出 W1/L1 评审基线 | 不强制每条短 Working 同步 E2；具体 State Store 保证仍待 AL-B01 |
| P2 accepted 与 Remember Accepted | 字段总表正文组合中有 Pending/Accepted 概括；运行时公共响应要求主事实已确认 | P2 accepted 只表示物理受理；无主事实时按 Failed+unknown/原 operation 表达，不造 Accepted Memory |
| ProviderResult 权威 | 最小字段表有“P2 返回五态”的摘要；总设计规定 ProviderResult 归 A | P2 原状态 → A 无损适配五态 → B Ready Guard；领域 Ready 始终由 B 写 |
| 同五元组重建 | B WBS/运行时要求 Stale/rebuild；Recall 本版明确受理前拒绝同目标 rebuild | 保留业务修复需求与当前不支持边界；新 Task/新集合不绕过机制，等待代际/屏障契约 |
| 向量关联 metadata | Remember 来源地图要求完整业务反查；Recall P2ProjectionMetadata 固定为 scope/type/time 三字段 | 必要反查先由 B/A 可靠映射；是否写入 P2 及摘要版本升级单列 BP2Q-04，不静默增字段 |
| Hash、Scope 和字段别名 | Remember Hash 使用 coverage，Recall 使用 byte_encoding/range；Scope 的 user/business_task 等维度不同 | 正文 BP2 类型与 A 原类型分开注明；逐字段适配不得丢失范围/权限；不改写任何原字典 |
| object_version 与逻辑地址 | 字段表允许不支持版本时等价证明；读取稿用 bucket/key；来源地图推荐逻辑地址 | 允许已证明不可变定位；准确版本/字节保证不能省；逻辑地址是推荐适配，不宣称已有新服务 |
| 健康与资源 Owner | 流程图 B 查询健康；Work Map 的 HLT-001 Owner 为 C | B 消费公共观察，A 负责 E1 机制能力；保持既有 Port 归属 |
| 物理释放与生命周期删除 | 落地问题包含 Demote/Release/迁移；Remember 包含 Deleted/Expired | C 决定驻留、P2 执行物理动作；B 负责资格/失效屏障与准确清理授权，不用迁移改 Memory Type |
| 观测与业务使用 | P2 metrics、OTel、AccessTrace、MemorySignal 都能关联请求 | 四类证据分开；IO 命中不等于使用，Trace 采样不替代持久操作/业务事件 |

上述处理是本次文档整理口径及显式能力边界，不代表修改冻结架构或替任何 Owner 签收外部保证。未明确的具体字段转换、兼容升级和完成条件由 5.1 的对应问题承接。

### 5.4 原需求、细分能力与流程覆盖

| 原 BP2 编号 | 细分对接表编号 | 本文主位置 |
|---|---|---|
| BP2-01 | P2-B-02/06 | 1.4 写入与 Multipart |
| BP2-02 | P2-B-03/04 | 1.6 写后确认；3.3/3.4 读取 |
| BP2-03 | P2-B-05/18 | 1.5 原操作；4.6 恢复 |
| BP2-04 | P2-B-02/06 | 2.2 Artifact，复用正文契约 |
| BP2-05 | P2-B-11/12 | 2.3/2.4 输入与向量写入 |
| BP2-06 | P2-B-13/14/15/17/18 | 2.5/2.6 核验及 Guard；4.5 inventory |
| BP2-07 | P2-B-07/08/16/17/21 | 4.3/4.4 精确删除；4.5 残留与恢复 |
| BP2-08 | P2-B-01/09/10/11/19/20/21/22 | 1.2 准入；1.9 关联与错误；4.5 恢复；5.2 限制 |

| Remember 阶段 | 本文对应流程/交接 | P2 参与方式 |
|---|---|---|
| R00 能力准入 | 1.2 | 公共能力/健康/资源观察，保留 Owner 和新鲜度 |
| R01 校验与幂等 | 1.3 | B 内部，外部调用前固定身份 |
| R02 正文确认 | 1.4～1.6 | E2 put/query/head/get |
| R03 主事实提交 | 1.7 | B State Store 提交，不要求 P2 提交 Memory 业务事务 |
| R04 Working / Task | 1.1、2.1、3.2、4.2 | Redis/Task 不自动由 P2 承接；大正文仍可引用 E2 |
| R05 分类与巩固 | 2.1、2.2 | B 决策，必要正文读取/保存复用 OBJ-001 |
| R06 Artifact | 2.2 | P2 仅存取输出字节，B 判质量 |
| R07 输入与分块 | 2.3 | B 完整清单，A/P2 提供模型/资源合同 |
| R08 向量生成与写入 | 2.3～2.5 | A 生成/适配，P2 存储/返回物理事实 |
| R09 Ready Guard | 2.5～2.7 | A/P2 提证据，B 唯一发布 Ready |
| R10 MemoryRead / CanonicalLoad | 第三章 | B 资格/映射，P2 准确版本字节，A 负责最终使用 |
| R11 使用事实 | 3.5、5.3 | P2 返回实际来源/IO 观察；业务访问事件由 A/B 产生 |
| R12 失效与清理 | 4.2～4.5 | B 屏障后经正确 Port 执行，C 处理驻留动作 |
| R13 Signal | 2.7 | B/RF/C 交接，P2 不确认业务 Signal |
| R14 恢复与对账 | 1.5、2.8、4.5～4.6 | 原操作/准确对象/清单/水位；先观察再决策 |

### 5.5 按流程执行联调验收（全部待执行）

| 原场景编号 | 场景与验收要点 | 对接项 |
|---|---|---|
| BP2T-01 | 正常长正文 → B 主事实 → 可选 Artifact / A 向量机制 → B Guard；所有 Scope、对象、版本、字节和关联可追溯；W1 小正文允许按契约跳过 E2 | BP2-01～06 |
| BP2T-02 | 已写丢回复、明确未写、暂不可见分别收敛；无 provider_operation_ref 仍能按原 client ref 查询；不重复 Memory/对象 | BP2-01/03 |
| BP2T-03 | E2 成功，B 事务失败/未知/回复丢失；先核对提交屏障，候选孤儿不提前删除，恢复能补登记 | BP2-01/03 |
| BP2T-04 | HEAD/GET 换版、Range 上下界、错误编码/hash、partial、不同摘要算法、ETag 非摘要、断流超限；不采用不可信正文 | BP2-02 |
| BP2T-05 | Artifact 输出已存但质量失败、质量通过但写未知、显式 skipped；Original 保留，回退条件和 required_outputs 正确 | BP2-04 |
| BP2T-06 | 多 chunk 部分成功、缺项、五态、无回执、只有总 count；完整 manifest 不变，未调用项不伪造 Provider 观察 | BP2-05/06 |
| BP2T-07 | 同维不同空间、模型/dtype/Schema/metadata 不符、延迟索引、请求摘要仅回显；证据不足不 Ready | BP2-05/06 |
| BP2T-08 | 旧 Worker、内联转后台、删除后迟到 upsert、Ready CAS 前换版；旧结果不覆盖新资格。同五元组 rebuild 按 Recall 参考稿的 A 机制草案受理前拒绝且 P2 调用数为 0，真实启用边界待签收 | BP2-03/05/06/07 |
| BP2T-09 | 精确/全版本/重复删除、共享引用、retention、软删与物理擦除、generation 失配、inventory partial；按实际层次报告，未证实屏障不确认无复活 | BP2-07 |
| BP2T-10 | B/A/Provider 重启与旧备份恢复；原操作/幂等窗口不重置，先恢复权限/墓碑/当前指针；历史成功与后续损坏分开 | BP2-03/07/08 |
| BP2T-11 | 容量满、长尾、半故障、过期健康观察、查询风暴、游标失效；限额与恢复节流生效，后台不占前台保留预算 | BP2-08，关联全部调用 |
| BP2T-12 | Simulator 或历史 Milvus 通过但真实 P2 不可用；按 Profile/Lane 分别报告，禁止把模拟结果当真实 E2/E1 集成通过 | BP2-01～08 |

每项真实联调记录应含：对应 BP2/BP2Q/BP2T、实际 Provider/API/适配版本、环境 Profile、输入范围、脱敏原请求响应、字段来源/转换、预期与实测、操作/trace 关联、证据位置、Owner、日期和未满足项。

Lane 1 领域验证、Lane 2 Simulator Contract/Fault 与 Lane 3 真实 P2 集成分别记录。正常和故障示例只解释契约，不能替代真实持久化、索引可见、删除、防复活、恢复或性能验证。

### 5.6 本次整理与检查范围

本次按指定 Recall 参考稿组织 Remember–P2 需求，保留八项 BP2、八项 BP2Q 和十二项 BP2T 原编号，补齐四条流程及 R00～R14 的输入来源、调用、物理返回、状态消费和恢复去向。总设计与节点分工用于核对 Owner、Port、表示关系和观测边界。

本次仅进行文档结构、链接、示例数据和来源保护检查；未修改业务实现，未执行真实 P2/A/C/Redis 集成、故障注入或性能测试。所有实际方法保证、参数和跨方待确认项仍须由对应 Owner 提供契约与证据。

检查结果：Markdown 围栏、表格列数及本文标题链接检查通过；所有跳转目标均在本文件内，没有跨文件或网页链接。3 个 JSON 示例可解析，读取示例的 Base64、15 字节长度和 SHA-256 一致，部分投影/未完成删除示例未误报完成。本交付仅维护本文件，原始参考材料及其他现有文件保持不变。


# 第二章 Recall

# Recall 与 Embedding：P2 接口对接需求 V0.1（按流程组织）

更新：2026-09-07。维护方：Recall 负责人（A）；接收方：P2 / Provider。B 指 Remember，C 指 Operate。

**六个核心接口总览：Recall 两项，Embedding 投影存储四项。** 下表先说明需要 P2 提供什么；接口名称链接到后文的完整定义。这里按逻辑能力计数，不要求拆成六个独立 RPC，也不表示每次执行都要调用全部接口。实际方法及保证仍需按后文契约对齐。

| 所属流程 | 核心接口及原编号 | 功能 | 我们提供的核心输入 | 期望 P2 返回的数据 |
|---|---|---|---|---|
| Recall | [向量检索 search（RP2-01）](#13-步骤二向-p2-搜索长期候选rp2-01) | 根据问题向量，在获准范围内查找有界 TopK 长期候选 | P2SearchInput：Query 向量、模型/检索空间、Scope、类型/时间筛选、top_k | P2SearchResult：稳定候选引用、模型/Schema 版本、分数/排名、complete/partial/failed 及相应证据 |
| Recall | [正文读取 get_content / get/get_range（RP2-02）](#17-步骤六读取正式正文或执行回退rp2-02) | 读取 B 批准的准确版本正文或字节片段 | P2ContentReadInput：正文目标与版本、approved_range、可信 expected_hash、版本条件、max_response_bytes | P2ContentReadResult：读取状态、实际字节/范围/字节数、版本/编码/摘要元数据及响应关联证据 |
| Embedding 投影存储 | [写入向量 upsert（RP2-06）](#24-步骤三调用-p2-写入向量rp2-06) | 保存 Passage 向量、模型信息、正文关联和检索元数据 | P2UpsertInput：准确五元组目标、真实向量及关联载荷、稳定幂等键、请求指纹、适用写入条件 | P2MutationResult：原操作关联、受理/执行/完成等状态，以及已取得的目标绑定和完成证据；受理不等于 READY |
| Embedding 投影存储 | [查询操作 query_operation（RP2-07）](#25-步骤四查询原写入或删除操作状态rp2-07) | 查询一次既有写入或删除操作，支持响应丢失、超时及重启后的恢复 | P2OperationQueryInput：原目标、动作、幂等键、请求指纹及已取得的 P2 操作引用 | P2MutationResult：原操作进度、效果和证据；可评估时给出安全重发依据，普通 not_found 不表示允许重写 |
| Embedding 投影存储 | [核验目标 get_projection（RP2-08）](#26-步骤五核验投影准确目标与索引状态rp2-08) | 核验准确向量对象的实际存储内容和索引状态 | P2TargetQueryInput：准确目标、预期载荷指纹、关联操作引用、是否回读载荷 | P2TargetState：对象存在性、实际载荷绑定、持久化/可查询性，以及适用的删除和屏障事实与证据 |
| Embedding 投影存储 | [删除向量 delete_projection（RP2-09）](#29-后续分支删除旧目标并核验不再复活rp2-09) | 删除 B 批准的准确目标，确认其退出查询且不会被旧在途写重新创建 | P2DeleteInput：准确目标、删除幂等键/指纹、适用删除条件及已知历史写入关联 | P2MutationResult：删除操作关联与进度；完成判定须有对象消失、索引退出及旧写不会复活的证据 |

**查询操作与核验目标的区别：** query_operation 查“这一次写入或删除执行到了哪里”，get_projection 查“这个向量对象实际处于什么状态”。两类事实可以由同一个 RPC 返回，但必须分别满足原操作关联和准确目标核验要求。六项接口共用 [P2CallContext / P2ResponseContext](#191-公共请求与响应上下文)，详细字段、空值及证据规则见各节。

**补充能力与职责：** [head/checksum（RP2-03）](#15-步骤四按需查询正文元数据rp2-03)按需补充元数据，可由可信映射或正文读取响应覆盖；[Prewarm（RP2-04）](#16-步骤五获准时先尝试热副本rp2-04)是可选读取加速；[实际来源与归因（RP2-05）](#182-随读取返回实际来源与放置观察rp2-05)是读取附带证据，三者不额外计入上述六项核心接口。Query/Passage 向量生成由 A 提供，候选资格与正文映射由 B 提供。

本文分为[第一章 Recall 流程](#1-recall-流程从问题到可信上下文)和[第二章 Embedding 流程从正文片段到可核验投影](#2-embedding-流程从正文片段到可核验投影)。每章按执行顺序说明调用条件、接口功能、输入、期望输出、数据定义、异常处理和下游消费。生成向量由 A 负责；P2 提供向量存储、检索和物理正文读取。

**阅读约定。** 标为“A/B 内部交接”的步骤用于说明 P2 输入从哪里来、结果由谁消费，不新增 P2 API。P2* 类型和逻辑方法名是待对齐的适配视图，尚不是已发布报文；正式 SDK/RPC、鉴权、字段映射和完成保证以签收契约为准。带 ? 的字段仍须出现，未知用 null；两章共用的上下文、基础类型、证据和错误见[1.9](#19-两条流程共用的调用与错误数据)。

本次设计逐项对照以下资料，未确认事项只在[《跨模块待确认事项》](跨模块待确认事项_V0.1.md)维护签收状态。RP2、P2Q、P2T 原编号保留；P2T 是待执行验收要求。

| 对照文档 | 本文采用的约束 |
|---|---|
| [Recall 总流程](../AetherStore_P3_Recall_Overall_Flow_V1.0.md)与[主设计第 3 章](召回流程详细设计_V0.1.md#3-主链路怎么实现a-01) | Recall 的阶段顺序、Working/长期分支、资格校验、正文加载、最终复核和提交 |
| [主设计第 2 章](召回流程详细设计_V0.1.md#2-embedding-与向量投影写入怎么实现)与[数据定义](召回数据定义_V0.1.md) | Query/Passage 分工、向量和投影对象、幂等、UNKNOWN、写删恢复及预算 |
| [Work Map](../../总设计/AetherStore_P3_Work_Map_V0.4.1.md)与[A 工作包](../../总设计/PROGRAM_A_RECALL_WORK_PACKAGE.md) | A 负责 Embedding、VEC-001、VEC-002；B 负责 OBJ-001 契约/内容映射和 ProjectionState |
| [Remember V2](../../remeber流程/2026-09-03-201608-remember-V2.0.md)与[B 对接说明](召回与记忆形成接口对接需求_V0.1.md) | B 准备片段、批准投影/正文目标并执行 Ready Guard；P2 命中不代替 B 资格事实 |
| [Operate 总流程](../../operate流程/AetherStore_P3_Operate_Optimize_Overall_Flow_V1.0.md)与[C 对接说明](召回与调度优化接口对接需求_V0.1.md) | 热副本读取、回退及实际使用归因；在线 Recall 不发起调层或预热控制 |
| [当前 P2 proto](../../../aether-agent-memory/engine/proto/aether_engine.proto)与[P2 API 文档](../../../aether-agent-memory/engine/docs/P2_AetherEngine_API接口文档_v0.1.md) | 原接口声明与本次需求逐项映射；声明存在不等于部署及完成性保证已验证 |

## 1. Recall 流程：从问题到可信上下文

### 1.1 流程入口、顺序与分支

入口是已通过校验、完成模式绑定的 RecallRequest / RecallExecution。输出是可靠提交后的 ContextPack；P2 只提供本流程中的候选和字节事实，不负责 ContextPack 终态。

~~~text
已授权问题、范围、策略和 deadline
  → A 生成并校验 Query 向量（working_only 跳过）
  → 候选发现：B Working 读取 / P2 search（combined 在此阶段可并行）
  → B 核验候选资格 → B 提供准确内容映射和期望摘要
  → 正文加载：
      已有 Working 内联字节 → 本地校验
      需要物理读取 → 按需 head → 获准且预算足够时 Prewarm
                                ├─ 同版本正文可信 → 采用
                                └─ 未采用 → canonical get/get_range → 校验
  → 去重/排序 → B 对全部已加载可组装候选及替补做一轮最终复核
  → A 组装、可靠提交并在有效期内发出 ContextPack
~~~

| 顺序 | 调用方 → 提供方；能力 | 何时调用及本步产物 | 下一步 |
|---|---|---|---|
| 1 | Recall → A SemanticEmbeddingCapability；[1.2](#12-步骤一生成并校验-query-向量a-内部能力) | long_term_only/combined 生成 QueryEmbeddingResult；不写库 | 进入候选发现 |
| 2 | Recall → P2 VEC-002/search，RP2-01；[1.3](#13-步骤二向-p2-搜索长期候选rp2-01) | 使用 Query 向量返回 P2SearchResult；Working 由 B 的 RB-01 独立读取 | 保留两来源事实，交 B 核验 |
| 3 | Recall → B RB-02/RB-03；[1.4](#14-步骤三经-b-核验资格并取得内容映射) | 得到当前资格和准确正文、版本、范围及期望摘要 | 已有内联字节直接验证；其余准备读取 |
| 4 | A → 内容 Provider OBJ-001/head，RP2-03；[1.5](#15-步骤四按需查询正文元数据rp2-03) | 仅在缺少必要大小/版本/编码依据时调用；可由可信映射或 get 响应覆盖 | 决定加载准入和版本条件 |
| 5 | A → 获准内容 Provider，RP2-04；[1.6](#16-步骤五获准时先尝试热副本rp2-04) | 可选、仅一次热副本探测；保留首次正式回退额度 | 可信命中直接采用，否则按唯一责任方回退 |
| 6 | A → P2/Durable Provider OBJ-001/get/get_range，RP2-02；[1.7](#17-步骤六读取正式正文或执行回退rp2-02) | 无热副本、未启用或需要回退时取得准确正文 | A 校验字节；取得响应附带的 RP2-05 来源证据 |
| 7 | A → B 最终复核，再由 A/RF 提交；[1.8](#18-步骤七采用读取事实并完成-recall) | 只用已加载可信正文完成组装；P2 不承担最终复核/提交 | 返回原执行结果 |

working_only 不调用 Query Embedding 和 VEC-002；Working 只有引用时仍可能按 B 映射调用 OBJ-001。combined 先结束 Query 阶段，再进入 Working/Vector 发现；Query 失败时只有在剩余时间允许下才继续 Working，并保留长期来源缺失，不改选模式。Prewarm 是同一正文的可选加载路径，不是第三个候选来源。

贯穿例子：查询“这个项目之前定过哪些约定？”，搜索命中 projection-42-1-v3，B 确认其对应 memory-42/chunk-1/memory-v3，并批准 content-42-v3 的 [0,15) 字节。后续每次读取和校验都沿用这份身份，不能用新正文解释旧命中。

### 1.2 步骤一：生成并校验 Query 向量（A 内部能力）

#### 1.2.1 功能、输入输出与失败去向

Recall 使用共享 SemanticEmbeddingCapability，把当前问题变为兼容检索空间中的真实 Query 向量。这是 A 内部能力调用，不向 P2 请求推理，也不触发第二章的写入链。

| 交接方向 | 数据与字段 | 定义及来源 |
|---|---|---|
| Recall → 共享能力 | SemanticEmbeddingRequest：usage=Query、input_ref、source_hash、input_binding_digest/input_binding_ref | 固定问题输入及其证据；不读取候选正文重新 Embedding |
| Recall → 共享能力 | caller_ref、caller_request_ref、authorization_ref、model_binding、deadline_at | caller_ref 为 A 固定调用身份，caller_request_ref=recall_id；模型/空间及期限来自本次已绑定执行 |
| 共享能力 → Recall | SemanticEmbeddingResult：vector_ref、vector_hash、usage、model_binding、source_hash、input_binding_digest、validation_evidence_ref | 受控保存的真实向量及输入/模型/数值证据；失败不创建空向量成功对象 |
| Recall → 本地检索适配器 | QueryEmbeddingResult → P2SearchInput.query_vector / model_binding | 再核对本次输入与检索兼容性，解析真实向量用于 1.3 节；不把 vector_ref 字符串当向量发送 |

完整字段见[共享请求](召回数据定义_V0.1.md#19-semanticembeddingrequest共享向量化请求)、[共享结果](召回数据定义_V0.1.md#20-semanticembeddingresult共享向量结果)和[QueryEmbeddingResult](召回数据定义_V0.1.md#query-embedding)。共享能力内部推理与复用步骤见[2.2](#22-步骤一b-交付片段a-生成-passage-向量)。

共享模块是推理唯一 Retry Owner，使用 EM-04，最多 2 次含首次。Recall 的 P-08/P-09 只约束附着原执行/补取结果，不构成第二层模型重试；重启沿用 caller 绑定，已失败调用仍重放失败。生成或兼容性检查失败时不调 search：combined 留下长期缺失并按剩余预算处理 Working，long_term_only 进入失败收尾。

#### 1.2.2 模型与空间数据：EmbeddingModelBinding

EmbeddingModelBinding 的完整定义在数据字典 19.3；此处列出对接必须能解释的字段：

| 字段 | 类型 | 定义 |
|---|---|---|
| model_id | Id | 具体模型身份 |
| model_version | Version | 实际固定版本 |
| dimension | uint | >0，等于实际向量长度 |
| dtype | ExternalLabel | 数值类型，不能隐式有损改换后仍使用原 vector_hash |
| embedding_schema_version | Version | 向量输出格式版本 |
| preprocessing_version | Version | 输入处理及 Query/Passage 编码规则版本 |
| retrieval_space_ref | ContractRef | Query/Passage 可比较的空间契约 |
| model_contract_ref | ContractRef | 输入上限、计数、归一化、数值编码和字节序等完整规则 |

Query 与第二章 Passage 使用同一类型。usage 可以决定不同编码前缀，但可比较性必须由 retrieval_space_ref/model_contract_ref 证明；维度相同不代表空间兼容。P2 不得静默转换 dtype、切换模型或使用另一集合补齐结果。

### 1.3 步骤二：向 P2 搜索长期候选（RP2-01）

#### 1.3.1 功能与调用时机

在已批准的租户、范围和兼容检索空间内，用 Query 向量获取有界 TopK 候选及完成性依据。Recall 的 `long_term_only`、`combined` 调用，`working_only` 不调用；模式由 Recall 内部选路，不由 P2 判别。

逻辑签名：`search(context: P2CallContext, input: P2SearchInput) → P2SearchResult`。当前待适配入口为 SearchVector；候选后续仍交 B 校验资格、版本和 Projection 可读性。

#### 1.3.2 我们发送的数据：P2SearchInput

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| query_vector | List<number> | 是 | QueryEmbeddingResult 中解析出的真实向量；有限数值且长度匹配 |
| usage | `Query` | 是 | 本调用用途，不能隐式保存为长期投影 |
| model_binding | EmbeddingModelBinding | 是 | 实际模型、版本、维度/dtype、预处理和空间，见 1.2.2 |
| retrieval_space_ref | ContractRef | 是 | 已批准的检索空间；与 Query/候选模型兼容 |
| scope | Scope | 是 | 认证后批准范围，见数据字典附录 A；不能静默放宽 |
| memory_types | List<ExternalLabel> | 是 | Recall 筛选中适用于长期分支的 Episodic/Semantic，非空；不传 Working |
| occurred_after | Timestamp? | 是 | B 业务时间的含下界，未筛选为 null |
| occurred_before | Timestamp? | 是 | B 业务时间的不含上界，未筛选为 null |
| top_k | uint | 是 | >0，受 Recall 固定策略及签收服务限额约束；不静默截小 |

`allowed_sources`、内部 retrieval_mode、未交接的本地向量引用不作为 P2 检索参数。范围/过滤在 P2 原契约中的表示和执行方式仍需确认；当前 proto 的 collection/query/top_k 三个字段不足以直接表达这些约束。不能读取越权候选后再把本地过滤当作租户隔离的替代。

#### 1.3.3 期望 P2 返回的数据：P2SearchResult 与 P2SearchHit

| P2SearchResult 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| retrieval_space_ref | ContractRef | 是 | 实际使用的空间及解释依据 |
| requested_k | uint | 是 | 本次实际约定的 K，与请求一致 |
| completion | `complete` / `partial` / `failed` | 是 | 本次有界搜索是否完成；不声称全库精确 TopK |
| hits | List<P2SearchHit> | 是 | 同一次尝试返回的有序候选；条数 ≤ requested_k |
| partial_reason | ExternalLabel? | 是 | partial 时必须解释；complete 时 null；failed 的原因放公共 error |
| ranking_contract_ref | ContractRef | 是 | 分数含义、排序方向或返回顺序的明确契约 |
| query_binding_evidence | List<P2Evidence> | 是 | 实际空间、授权范围及筛选应用到本次搜索的依据 |
| completion_evidence | List<P2Evidence> | 是 | 有界搜索完成或部分返回的依据；不能从条数猜测 |

| P2SearchHit 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| projection_ref | ExternalId | 是 | 可交 B 解析的稳定 Projection 引用；原 SearchHit.id 须有确认映射 |
| provider_rank | uint | 是 | 从 1 开始的原排名；可按已签收的返回顺序适配，不能按分数臆造 |
| raw_score | number? | 是 | 原分数；缺失为 null，不直接跨空间或与 Working 比较 |
| model_version | Version | 是 | 候选对应模型版本，必须与 Query 空间兼容 |
| projection_schema_version | Version | 是 | 候选投影 Schema；不是 Memory 版本 |
| identity | ProjectionIdentity? | 是 | 若返回完整五元组需有依据；未返回时通过稳定引用交 B 解析，不用当前版本补写 |
| source_evidence | List<P2Evidence> | 是 | 原引用、版本和排序的来源依据，可关联该条原响应及空间契约 |

模型/Schema 版本可以来自条目元数据，或来自能证明整个集合单一绑定的不可变空间契约；不能按当前默认模型代填。Memory 当前性、生命周期、TTL、正式正文、最终可读结论不由 SearchHit 宣告。

#### 1.3.4 返回示例与处理规则

示例请求：已验证的三维 Query 向量、`space-demo-v1`、获准 `tenant-demo/project-demo`、`memory_types=["Semantic"]`、`top_k=3`；时间条件均为 null。下面是 P2SearchResult 草案示例，公共响应上下文另带：

```json
{
  "retrieval_space_ref": "space-demo-v1",
  "requested_k": 3,
  "completion": "complete",
  "hits": [{
    "projection_ref": "projection-42-1-v3",
    "provider_rank": 1,
    "raw_score": 0.91,
    "model_version": "model-v1",
    "projection_schema_version": "projection-v1",
    "identity": {
      "memory_id": "memory-42", "chunk_id": "chunk-1",
      "memory_version": "memory-v3", "model_version": "model-v1",
      "projection_schema_version": "projection-v1"
    },
    "source_evidence": [{
      "kind": "candidate_binding", "contract_ref": "<候选映射契约>",
      "provider_evidence_ref": "<原条目及空间证据>",
      "observed_at": "2026-09-07T02:00:00.000Z"
    }]
  }],
  "partial_reason": null,
  "ranking_contract_ref": "<原排序契约>",
  "query_binding_evidence": [{
    "kind": "search_scope", "contract_ref": "<范围及过滤契约>",
    "provider_evidence_ref": "<本次查询应用范围的证据>",
    "observed_at": "2026-09-07T02:00:00.000Z"
  }],
  "completion_evidence": [{
    "kind": "bounded_search_complete", "contract_ref": "<搜索完成契约>",
    "provider_evidence_ref": "<本次完整响应证据>",
    "observed_at": "2026-09-07T02:00:00.000Z"
  }]
}
```

请求 K=3、实际只返回 1 条也可以是 complete，前提是完成证据充分。`complete + []` 才是已确认的有界搜索空；`partial + []` 或超时不能当正常空。failed 时不将不可信候选包装成成功集合；若存在可验证的部分结果，应明确 partial 及相应证据。

每次重试保留独立响应，不能拼接不同尝试的排名冒充一次完整搜索。combined 的长期失败不改为 working_only，最终按主设计降级规则处理。缺引用/版本依据的候选隔离，B 确认旧 Projection 已替代则排除，不配新正文。

相关待确认项：P2Q-01～P2Q-05、P2Q-10～P2Q-12。

**本步输出交给谁。** A 保存原响应和契约后形成 VectorCandidateSet、VectorCandidate 及长期 SourceReadResult；下一步把稳定 projection_ref 和发现版本交给 B。identity 的五元组字段见[2.3.2](#232-准确投影目标p2projectiontarget)，不得由 A 按当前版本补写。每次 search 使用[1.9.1](#191-公共请求与响应上下文)的调用上下文，并在发出前占用原执行读取次数。

### 1.4 步骤三：经 B 核验资格并取得内容映射

#### 1.4.1 本步交接与加载准入

这是 MemoryRead / CanonicalLoad 的 A/B 交接，分别对应 RB-02、RB-03、RB-05。P2 的 SearchHit 不能直接提供本步的领域结论，A 也不在在线 Recall 中调用第二章的 get_projection 来代替 B 的 Ready 判定。

| 交接 | 输入 | 期望取得的事实及下一步 |
|---|---|---|
| A → B 核验候选 | 原稳定引用、发现版本、Query 模型空间、已授权 Scope | B 的 Memory/version、类型、当前状态与权限；Working 的 TTL/当前性；长期 Projection Ready 及模型/Schema 兼容证据 |
| A → B 获取内容映射 | 已获准的 Memory/版本及所需范围 | representation_id/type、content_ref/version、approved_range、可信 expected_hash 及正式物理定位/读取契约；可选获准 cache_read_ref |
| A 本地保存 | 上述事实与原候选的关联 | CandidateValidationResult；accepted 才可准备加载，excluded 为已知排除，unverifiable 隔离并记缺失 |

完整本地字段见[资格校验](召回数据定义_V0.1.md#candidate-validation)和[ContentLoadResult](召回数据定义_V0.1.md#content-load)；B 原报文字段由[B 对接说明](召回与记忆形成接口对接需求_V0.1.md#1-双向接口与信息交接)对齐。已知旧版本排除，正文或资格未知记录缺口；禁止猜 bucket/key、跨租户读取或给旧候选换新正文。B 未提供合法映射时不构造 P2ContentTarget，也不发正文读取。

#### 1.4.2 交给内容接口的目标：P2ContentTarget

| 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| content_ref | ExternalId | 是 | B 的正式内容引用 |
| content_version | Version | 是 | B 批准读取的正文版本 |
| representation_id | ExternalId | 是 | 原内容表示身份 |
| representation_type | ExternalLabel | 是 | B 契约中的类别，不由 A 自建枚举 |
| bucket | ExternalId | 是 | B/Provider 批准的实际对象空间映射 |
| key | ExternalId | 是 | 对应准确版本的对象定位或条件读取目标，不能由 memory_id 猜测 |

这里的 bucket/key 对应当前 P2 ObjectService，不表示允许暴露内部缓存 key。若实际 Provider 使用其他 locator，通过正式适配映射；Memory 版本、内容版本、模型版本和 placement_generation 具有各自语义，不能要求字符串相等。

#### 1.4.3 所有物理读取之前的额度规则

按[主设计 3.5.1](召回流程详细设计_V0.1.md#351-调用前记账并发准入与崩溃恢复)先保存 RecallReadAttempt，再发出调用。head/search 也占调用次数，但向量和元数据不计正文额度；正文调用预留完整批准范围，max_response_bytes 取本次已保存的 reserved_bytes。始终满足 bytes_charged + bytes_reserved ≤ P-20；多个候选按固定来源/排名/稳定引用顺序准入，不能按并发返回快慢争抢同一份额度。

只有可证明有界或接收层能限制正文 byte 的路径可启用；压缩内容还要限制解压后的正文 byte。Working 内联正文在发现调用时预留并计费，后续验证不重复计费。收到的失败残留、校验失败、重试和回退字节都扣预算，SDK 隐式重试关闭；崩溃后实际量未知，保守扣除整份预留并保留“实际量未知”，不伪造统计。

下面的 head 不是每条内容必调的步骤；已掌握必要可信元数据时可跳过。Prewarm 也只有在本次预留后仍能保留首次正式回退额度和时间时才探测，否则直接走 1.7 节。

### 1.5 步骤四：按需查询正文元数据（RP2-03）

#### 1.5.1 功能与输入

在读取前估算大小、校验准确版本和范围，或为实际返回字节取得完整性依据。可与 get 一次返回；不要求 P2 新增独立 checksum 方法。当前声明中 HeadObject 返回 ObjectMeta，checksum 由其 md5_hex/blake3_hex 等信息及契约解释。

逻辑签名：`head_content(context: P2CallContext, input: P2ContentMetadataInput) → P2ContentMetadataResult`。

| P2ContentMetadataInput 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| content | P2ContentTarget | 是 | 同 1.7 节批准的正文表示与版本 |
| approved_range | ByteRange | 是 | 需要验证的字节范围；不能默认整对象摘要覆盖任意片段 |
| version_condition_ref | ExternalId? | 是 | 与 get 同样的版本条件或不可变定位依据 |

#### 1.5.2 期望 P2 返回的数据

P2ContentMetadataResult 包含 `status`、`meta`、`evidence`，并带公共响应上下文：

| 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| status | `ok` / `not_found` / `version_mismatch` / `failed` | 是 | 对准确对象的元数据查询结果 |
| meta | P2ContentMetadata? | 是 | ok 时必须有；其余可 null |
| evidence | List<P2Evidence> | 是 | 本次对象/版本/摘要范围的原始依据 |

P2ContentMetadata 同时用于1.7 节读取结果：

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| content_ref | ExternalId | 是 | B 内容引用与实际对象的可验证映射 |
| content_version | Version | 是 | 实际证实的正文版本；不能用 memory_version 替代 |
| representation_id | ExternalId | 是 | 对应 B 批准的内容表示 |
| object_size_bytes | uint | 是 | 同一版本完整对象的字节长度，区别于本次 returned_bytes |
| content_encoding | ExternalLabel | 是 | 字节表示/文本编码的契约标识，可由不可变内容契约证明 |
| etag | string? | 是 | Provider 原 ETag；语义未经证明时只作不透明标识 |
| checksum | Hash? | 是 | 来源实际提供的算法、值、字节编码及覆盖范围；未提供可 null |
| version_evidence | List<P2Evidence> | 是 | 以上元数据与准确版本绑定的依据 |

checksum 可空不表示跳过完整性校验：A 仍必须有可信 expected_hash，并对实际 data 计算 actual_hash；缺少可信期望依据或无法证明范围时不得采用正文。MD5/BLAKE3 能否用于对应内容契约，以及 ETag 在普通/分片对象下的含义，由 B 与 P2 确认；不能只按接口文档的简述把所有 ETag 当摘要。

#### 1.5.3 示例与竞态规则

示例输入同 1.7 节，期望 `status=ok`、`content_version=content-v3`、`object_size_bytes=15`，并取得该版本与 `[0,15)` 摘要的关联依据。若只能返回整对象摘要，要明确 checksum.range=null 所指的是完整对象，不能把它改写为任意局部范围。

head 返回 content-v3、随后 get 实际返回 content-v4 时，两次证据不相容；不能拼出 content-v3 已验证。应使用签收的条件读取/不可变版本对象，或拒绝该次内容并按剩余预算处理。checksum、head、get 可以共用契约，但不能假定可变对象上的多次调用属于同一快照。

相关待确认项：P2Q-06、P2Q-07、P2Q-10。

### 1.6 步骤五：获准时先尝试热副本（RP2-04）

#### 1.6.1 功能与输入

**功能：** 获准启用 Prewarm 时，尝试 B 认可的同版本热副本；miss、失效或校验失败后，在剩余预算内回退正式正文。仍属于原长期 Memory，不是 Working 或第三个候选来源。

逻辑调用可表示为 `read_with_cache(context: P2CallContext, input: P2CachedReadInput) → P2ContentReadResult`；也可由既有内容接口内部处理，不新增 Redis Port。

| P2CachedReadInput 字段 | 类型 | 必填 | 含义 |
|---|---|---|---|
| content_read | P2ContentReadInput | 是 | 与正式读取完全一致的批准版本、范围和期望摘要 |
| cache_read_ref | ExternalId | 是 | 经 B/Provider 认可的可读热副本引用；不是内部 Redis key |
| fallback_owner | `caller` / `provider` | 是 | 签收的单次回退责任方；不能上下层各自重复回退 |

#### 1.6.2 期望返回：P2ContentReadResult 与 P2CacheTrace

响应仍按 1.7、1.8 节验证正文，额外返回 P2CacheTrace：

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| cache_read_ref | ExternalId? | 是 | 实际尝试的已批准副本引用，不能根据 Memory ID 猜测 |
| cache_outcome | `hit` / `miss` / `invalid` / `unavailable` / `unknown` | 是 | 热路径实际结果；hit 仍须正文校验 |
| fallback_performed | bool? | 是 | Provider 是否已执行正式回退；未知为 null |
| final_read_path | `prewarm` / `canonical` / `unknown` | 是 | 最终响应字节的路径，必须与 P2ContentReadResult.read_path 一致 |
| diagnostic_code | ExternalLabel? | 是 | 失效、释放、校验失败等原始分类，正常可 null |
| evidence | List<P2Evidence> | 是 | 缓存尝试与内部回退依据 |

#### 1.6.3 示例、回退与下一步

示例：Provider 内部热副本 miss、正式回退成功，返回可信 data、`cache_outcome=miss`、`fallback_performed=true`、`final_read_path=canonical`。A 保留诊断并采用可信正文，不重复回退，也不单因 miss 自动降级。若已确认由 A 回退，则 Provider 返回该次 miss 事实，由 A 统一安排一次正式读取。

回退责任或实际路径未知时不能猜测已回退/未回退；按契约与预算处理，归因为不可验证。在线读取不触发 Promote/Prefetch/Release。热副本与正式来源都不可用时记录内容缺口。

**本步与下一步的连接。** 命中后仍按 1.8 节核验；可信时跳过 1.7 节。由 caller 回退时，纯 miss 返回该次未取得字节的事实（status=not_found、data=null、cache_outcome=miss），invalid/unavailable 返回相应失败和诊断；A 为下一次正式读取单独占次数与字节。Provider 已按契约内部回退时，直接校验其最终字节，不再发一次 canonical 请求。

Provider 内部回退只有在契约能说明调用次数、各次交给 A 的正文 byte 上限/归集方式及实际路径时才可启用，不能用最后一次 returned_bytes 冒充两路累计接收量。无法映射到原账本时使用已确认的 caller 回退路径；未确认能力登记在[AL-P203/AL-P204 与 C 清单](跨模块待确认事项_V0.1.md#3-p2--内容-provider)，不靠隐藏重试绕过额度。

### 1.7 步骤六：读取正式正文或执行回退（RP2-02）

#### 1.7.1 功能与调用时机

候选经 B 资格校验并取得合法 CanonicalLoad 映射后，读取批准版本的完整正文或准确片段。由 B 牵头定义内容契约，P2/Durable Provider 提供字节；本次仅涉及读取。

逻辑签名：`get_content(context: P2CallContext, input: P2ContentReadInput) → P2ContentReadResult`。当前可适配 GetObject/GetObjectRange。Working 已有可信正文时无需重复下载。

#### 1.7.2 我们发送的数据：P2ContentReadInput

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| content | P2ContentTarget | 是 | B 批准的正文、表示、版本及物理定位映射，见 1.4.2 |
| approved_range | ByteRange | 是 | 批准的准确半开字节范围 `[start,end)`，相对 content_ref 的指定字节表示 |
| expected_hash | Hash | 是 | B/原内容契约提供的可信期望摘要，覆盖本次批准范围 |
| version_condition_ref | ExternalId? | 是 | Provider 支持时使用已确认条件读取标识；否则须有不可变版本定位等保证 |
| max_response_bytes | uint | 是 | A 已预留并允许接收的字节上限，不构成静默截断许可 |

内容身份和 expected_hash 来自 B 认可的依据；不能从不可信缓存的回包重新定义“期望内容”。expected_hash 可由 A 本地持有，不强制 P2 接口原样接收，但适配必须保留校验用途。

#### 1.7.3 期望 P2 返回的数据：P2ContentReadResult

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| status | `ok` / `not_found` / `version_mismatch` / `failed` | 是 | 字节读取事实；不等于 Memory 生命周期状态 |
| content | P2ContentTarget | 是 | 响应所关联的请求目标；ok 时实际版本必须由 meta 及证据证明，不能只靠回显 |
| data | bytes? | 是 | 实际响应字节；ok 时非 null；失败残留字节只隔离处理，不输出为可信正文 |
| returned_range | ByteRange? | 是 | 实际返回的范围；ok 时与 approved_range 一致 |
| returned_bytes | uint | 是 | 实际响应字节数，ok 时等于 data 长度与批准范围长度 |
| meta | P2ContentMetadata? | 是 | 版本、编码、长度和摘要事实，见 1.5 节；ok 时必须有 |
| read_path | `canonical` / `prewarm` / `unknown` | 是 | 实际采用路径的事实；无依据为 unknown，不用期望位置代填 |
| placement | P2ReadPlacement? | 是 | 可选物理来源/放置证据，见 1.6、1.8 节；缺失可 null |
| cache_trace | P2CacheTrace? | 是 | 可选热副本及内部回退事实，见 1.6、1.8 节；未使用可 null |
| evidence | List<P2Evidence> | 是 | 正文版本、范围及此次字节响应的关联证据 |

`bytes` 在 gRPC 中为原始字节；本文 JSON 示例用标准 Base64。不得将 Base64 字符串长度当作读取字节数。传输压缩、编码转换和范围所用字节表示必须由内容契约说明，A 不静默转码后套用原摘要。

#### 1.7.4 示例与校验

示例输入：`content-42-v3 / content-v3 / representation-42-v3`，B 批准范围 `[0,15)` 和“接口约定。”的可信期望摘要。适配到当前 Range RPC 时，发送 `start=0,end=14`，因为 P2 的 end 含上界。只有契约证实完整对象就是该批准范围时才可用 GetObject，不扩大下载范围。

以下为 P2ContentReadResult 草案示例；representation_type、bucket/key 和证据标识均为示意映射：

```json
{
  "status": "ok",
  "content": {
    "content_ref": "content-42-v3", "content_version": "content-v3",
    "representation_id": "representation-42-v3",
    "representation_type": "<B批准的表示类别>",
    "bucket": "bucket-demo", "key": "object-demo-v3"
  },
  "data": "5o6l5Y+j57qm5a6a44CC",
  "returned_range": {"start": 0, "end": 15},
  "returned_bytes": 15,
  "meta": {
    "content_ref": "content-42-v3", "content_version": "content-v3",
    "representation_id": "representation-42-v3",
    "object_size_bytes": 15, "content_encoding": "utf-8",
    "etag": null,
    "checksum": {
      "algorithm": "SHA-256", "value": "<上述15字节的可信摘要>",
      "byte_encoding": "utf-8", "range": {"start": 0, "end": 15}
    },
    "version_evidence": [{
      "kind": "content_version", "contract_ref": "<正式内容版本契约>",
      "provider_evidence_ref": "<本次字节与版本绑定证据>",
      "observed_at": "2026-09-07T02:00:01.000Z"
    }]
  },
  "read_path": "canonical", "placement": null, "cache_trace": null,
  "evidence": [{
    "kind": "content_read", "contract_ref": "<正文读取契约>",
    "provider_evidence_ref": "<本次范围与响应证据>",
    "observed_at": "2026-09-07T02:00:01.000Z"
  }]
}
```

A 仍需自己校验实际字节与期望摘要、版本、范围和授权关联；P2 的 `status=ok` 不直接等于 ContentLoadResult.validated=true。not_found 只说明本次未取到字节，不宣告 Memory 已删除；部分字节、错版本、摘要不符均不能进入 Context。

**调用前额度与断流。** max_response_bytes 必须来自 A 已可靠保存的 RecallReadAttempt.reserved_bytes；完整批准范围装不进余额时不调用，不允许缩短范围冒充成功。A 关闭 SDK 隐式重试，每次重试/回退重新占用原执行额度。P2 或 A 的接收层须能限制正文 byte；取消、断流、校验失败后的已接收字节仍扣费。A 崩溃后无法确定实际量时，按本次预留全额扣预算，并把统计完整性标为 false，不能伪造 P2 返回的 returned_bytes。包装/base64/压缩的口径与 Working 内联读取统一见主设计 3.5.1。

相关待确认项：P2Q-01、P2Q-05～P2Q-07、P2Q-10～P2Q-12。

### 1.8 步骤七：采用读取事实并完成 Recall

#### 1.8.1 A 怎样采用 P2 返回的正文

| P2 事实 | A 形成的对象 | 采用条件与下一步 |
|---|---|---|
| P2ContentReadResult + P2ContentMetadata | ContentLoadResult | 同 B 授权和映射核对租户、身份、版本、表示、范围及 actual_hash；全部通过才 validated=true |
| 资格 accepted + 正文 validated=true | RecallCandidate | 候选、字节、ContentLoadResult 和加载路径绑定同一条来源，交去重/排序 |
| P2CacheTrace | CacheDiagnostic | 保留探测、失败和回退事实；正式回退可信时不单因缓存失败降级 |
| 下节 P2ReadPlacement | 访问观察及 PlacementVerificationRecord 的输入 | 可选归因证据；正文可信但来源/动作未知时继续基础召回，归因标为不可验证 |

ContentLoadResult.load_path 沿用 working_inline/prewarm/canonical，表示 A 实际采用或尝试的逻辑路径，不新增 unknown 枚举。P2ReadPlacement 或 P2ContentReadResult.read_path 未提供内部来源时保留原 unknown，actual_provider/归因证据可空；不能据此宣称实际热副本命中。A 对原数据算出的实际摘要不得被 P2 的 ok 或请求字段回显代替。

#### 1.8.2 随读取返回实际来源与放置观察（RP2-05）

**功能：** 说明本次字节实际从哪里读取，为 A 向 C 提供真实访问及预热使用证据。输入是既有读取的关联信息，不需要第二次正文读取或新的调层控制接口；P2ReadPlacement 可直接附在读取响应中。

| P2ReadPlacement 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| actual_provider | ExternalId? | 是 | 实际读取字节的 Provider，无证据为 null |
| observed_tier | ExternalLabel? | 是 | 该次读取的真实层级，不是 desired_tier |
| placement_generation | Version? | 是 | 原放置版本；不自行比较大小，不要求与 Memory 版本相等 |
| observed_at | Timestamp? | 是 | Provider 对该放置事实的观察时间 |
| producing_action_id | ExternalId? | 是 | 能唯一关联该副本/读取的 C 动作；不能按时间接近猜测 |
| evidence | List<P2Evidence> | 是 | 与父读取的内容、表示、版本、范围及动作绑定的证据 |

示例：同一正式版本的热副本成功读取，Provider 能证明实际副本和动作，则返回实际层级/generation/动作引用。A 还需结合 C 的动作成功事实及有效窗口核验，P2 无须返回 `load_hit`、`context_hit` 或收益指标。

若正文版本、范围和摘要均可信，但 placement 缺失或 producing_action_id 无法确认，基础召回继续，归因记为不可验证；不能把证据缺失写成“未命中”。相关待确认项：P2Q-08、P2Q-09，及 AL-C01、AL-C02、AL-C04、AL-P204。

#### 1.8.3 下游排序、最终复核和可靠提交

可信正文交给[主设计 3.6～3.8](召回流程详细设计_V0.1.md#36-排序)：去重代表必须同时保留自己的正文及路径；按 B 的语义/冲突规则排序。B 的一轮最终复核覆盖全部已加载可组装候选及替补，逐条保存证据和有效期，再计算合格组数 E 和最终预算装配数 P。未知、变更或过期项不能进入 Context；不在这一步重新 search、读取新版本或触发投影修复。

A/RF 可靠提交 ContextPack、Trace 和待投递事实后，发出前仍检查 deadline 与证据有效期。截止后的提交恢复只查提交事实或按原机制隔离旧草稿，不再调用 P2 取内容。P2 不返回 Recall 的四种业务终态，也不判定 Context 命中、实际模型使用或 Operate 收益。

访问事件由 A 在真实发生时分别记录命中、加载、选中和发出；实际接收/模型使用需调用方证据。迟到响应只留证，不覆盖失租或已终结执行。终态、缺口和预算处理统一按[主设计第 4 章](召回流程详细设计_V0.1.md#4-结果故障与恢复a-02)。

### 1.9 两条流程共用的调用与错误数据

以下类型供 Recall 与 Embedding 的全部 P2 逻辑接口共用；第二章不另建一套类型。A 本地 RecordHeader、lease_token、CAS、执行记录与受控引用不自动成为 P2 报文。每个字段的原协议来源、适配转换及缺失处理应在对接时说明。

#### 1.9.1 公共请求与响应上下文

每个逻辑接口都有 P2CallContext；可映射到 gRPC metadata、SDK 上下文或正式请求字段，不指定新的 HTTP 路径或 header 名。

| P2CallContext 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| request_ref | Id | 是 | 本次调用关联，区别于稳定变更幂等键；查询有自己的调用关联 |
| trace_id | Id | 是 | 调用链追踪，不作为鉴权依据 |
| tenant_id | Id | 是 | 已认证租户，与 target/scope 一致 |
| provider_ref | ExternalId | 是 | 实际调用的固定 Provider |
| contract_ref | ContractRef | 是 | 使用的 P2 原协议及适配版本 |
| deadline_at | Timestamp | 是 | 本次调用剩余期限；写操作超时不代表远端已取消 |

鉴权仍由正式传输机制完成，P2 必须验证其与 tenant/scope 的对应关系；本地 authorization_ref 仅是审计依据，不能替代有效凭证。服务端 deadline、取消、过载和 SDK 内部重试机制需按原契约确认。

每个逻辑响应带 P2ResponseContext：

| 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| request_ref | Id | 是 | 与本次请求关联，可由已确认 RPC 调用关联得到 |
| provider_ref | ExternalId | 是 | 实际响应 Provider，保留路由/服务契约依据 |
| contract_ref | ContractRef | 是 | 解释原响应所使用的契约版本 |
| provider_request_ref | ExternalId? | 是 | Provider 自有诊断请求 ID；无则 null |
| observed_at | Timestamp? | 是 | Provider 提供的观察时间；缺失不使用 A 时间冒充 |
| error | P2Error? | 是 | 失败、部分结果原因或调用错误；正常无错误时 null |

原协议以 gRPC status 返回错误时，适配为 error；不得伪造成功 body。没有可信响应时只保存 A 的调用失败观察，不构造来源对象。

#### 1.9.2 基础类型和证据：P2Evidence

| 类型 | 本文采用的表示与规则 |
|---|---|
| Id / ExternalId / Version / ContractRef | 本地用非空不透明字符串表示；外部原类型须无损适配；Version 不能自行比较大小，ContractRef 必须能定位原契约及版本 |
| ExternalLabel | 原契约中的非空标签；由原 Owner 解释，不把示例值当成新增冻结枚举 |
| Timestamp | UTC RFC3339，毫秒精度，如 `2026-09-07T02:00:00.000Z` |
| uint / number / bool | 非负安全整数 / 有限数值 / 布尔值；禁止 NaN、Infinity 和精度溢出 |
| T? | 字段需要出现，但允许 null；unknown 不写成 false、0 或空字符串 |
| List<T> | 有序数组，无条目为 []；不能用 null 冒充空结果 |
| ByteRange | `{start:uint,end:uint}`，0≤start<end，含下界、不含上界；按指定内容字节计算，不按字符/token |
| Hash | `{algorithm:string,value:string,byte_encoding:string,range:ByteRange?}`；value 为该算法长度匹配的小写十六进制；range=null 只指该摘要对象的完整字节 |
| Scope | `{tenant_id,project_id?,agent_id?,session_id?,task_id?}`，所有字段出现，可空维度用 null；null 不等于任意范围；完整语义见数据字典附录 A |

本地 JSON 摘要采用 JCS + UTF-8 + SHA-256；具体计算字段以数据字典为准。源正文算法按内容契约消费，不把 vector_hash、metadata_hash、source_hash 和正文 checksum 互相替代。

| P2Evidence 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| kind | ExternalLabel | 是 | 原证据所证明的事实类别，如持久化、存储绑定、索引可见、版本或屏障；示例标签不作为新冻结枚举 |
| contract_ref | ContractRef | 是 | 明确该证据证明什么、适用范围及窗口的签收契约 |
| provider_evidence_ref | ExternalId? | 是 | 如有可查询证据对象，返回其原引用；无独立对象可 null，此时原完整响应及契约本身须足够证明事实 |
| observed_at | Timestamp? | 是 | 来源观察时间；无则 null，不伪造 |

证据必须绑定父对象的准确目标/请求、内容版本/范围及本次观察，不能跨对象或不一致时点拼接。证据链接须能按授权解析；有字符串而取不到相应事实不算证明。A 保存原响应及契约关联后，才形成自己的 EvidenceRef；P2 不需要实现 A 的证据表，也不因返回一个 `true` 就自动满足证明要求。

#### 1.9.3 统一错误事实：P2Error

下列 category 是本草案用于对齐错误语义的分类，不要求 P2 重命名现有 `P2Err_*` 或 gRPC status。

| 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| raw_code | ExternalLabel | 是 | 原 P2 错误码或传输状态 |
| category | ExternalLabel | 是 | 映射到下表分类；未知保留 provider_error，不能猜测 |
| message | string | 是 | 脱敏诊断，不含文本、向量、凭证或内部 key |
| retryable | bool? | 是 | 原协议是否建议重试；不能单独作为写重发许可 |
| retry_after_ms | uint? | 是 | Provider 能提供的重试等待建议；仍受原 deadline/预算限制 |
| effect | `no_effect` / `may_have_effect` / `unknown` / `not_applicable` | 是 | 变更副作用是否确定；只读为 not_applicable；no_effect 仍不单独证明未来不会迟到生效 |
| evidence | List<P2Evidence> | 是 | 明确拒绝、失败及效果判断的来源依据 |

| category | 所需处理 |
|---|---|
| invalid_argument / binding_mismatch | 维度、dtype、版本、Schema、范围或参数不合法；不靠换默认模型/放宽范围修复 |
| permission_denied | 拒绝越权或租户不符；不降级到更宽授权 |
| unsupported | 明示不支持的过滤、版本读取、查询或完成性能力及替代契约；不静默忽略 |
| conflict | 同键不同载荷、条件冲突或已退役目标；不覆盖旧绑定 |
| not_found | 区分对象、操作、空间不存在；操作 not_found 不推出无副作用，正文 not_found 不推出 Memory 已删除 |
| rate_limited / resource_exhausted | 超出大小、维度、并发、队列等限额；不截断向量/正文或缩小 K 冒充成功 |
| timeout / cancelled / unavailable | 读取按剩余预算处理；变更效果可能未知，先查原操作 |
| provider_error | 保留原分类与事实，无法解释的响应不伪造业务终态 |

### 1.10 本流程的协议映射与联调要求

#### 1.10.1 现有协议能提供什么

以下依据当前仓库 proto 声明，不推定其他版本或传输层保证。API 文档的 SearchHit 表少列 metadata_json，当前 proto 已有该可选字段，按 proto 核对。

| 本次需求 | 已声明的原方法及字段 | 仍需对齐的内容 |
|---|---|---|
| search | `SearchVector(collection, query, top_k)`；返回 `hits`，每条有 `id, score, graph_node_id?, metadata_json?` | 该请求未声明 scope/filter；需要确认隔离、过滤、空间绑定、排序和 complete/partial 语义 |
| 正文读取 | `GetObject(bucket, key)`、`GetObjectRange(bucket, key, start, end?)`；返回 `ObjectBytes(data, meta)` | 版本/条件读取、批准范围、编码及摘要覆盖；Range 的原 end 为**含上界** |
| head/checksum | `HeadObject(bucket, key)` 返回 ObjectMeta：`bucket, key, etag, size, md5_hex?, blake3_hex?` | 没有独立 checksum RPC；同版本绑定与片段摘要语义仍需确认 |
| 热副本/归因 | 上述响应未声明实际热副本、内部回退、放置 generation 或动作关联字段 | 确认由哪个 Provider、接口或已有证据提供；可独立不启用 |

head/checksum 是同一个内容元数据能力的事实需求，不要求独立 checksum RPC；RP2-05 是读取附带事实，不额外读取正文。VEC-002 的范围/过滤/完成性不足时，不启用不满足契约的长期检索路径；可选热副本/归因可独立关闭。

#### 1.10.2 P2 需提供的对接材料

每个接口提供实际方法及契约版本、输入输出样例、草案字段的来源/转换/缺失处理、鉴权与错误、限额和测试环境；确认状态只回写[独立清单](跨模块待确认事项_V0.1.md#3-p2--内容-provider)。

| 原问题编号 | 需确认内容 | 对接项 | 关联 AL |
|---|---|---|---|
| P2Q-01、P2Q-12 | 实际 SDK/RPC/HTTP 接口、版本、测试环境、稳定样例及故障条件 | RP2-01～RP2-05 | AL-P202、AL-P203、AL-P204 |
| P2Q-02、P2Q-03、P2Q-04 | 模型/Schema/空间绑定；范围/过滤/TopK；完整/partial/空；稳定引用、版本/排序及 B 认可映射 | RP2-01 | AL-B05、AL-P201、AL-P202 |
| P2Q-06、P2Q-07 | 正式版本读取、get/head/checksum；版本竞态、范围上下界、编码/摘要覆盖 | RP2-02、RP2-03 | AL-P203、AL-B02、AL-B03 |
| P2Q-08、P2Q-09 | 热副本实际 Provider、读取/释放/回退；来源/层级/generation/时间和动作关联，不支持项明示 | RP2-04、RP2-05 | AL-C01、AL-C02、AL-C04、AL-P204 |
| P2Q-05、P2Q-10 | 鉴权/租户隔离/关联；超时/取消/过载/可重试错误、效果说明及更新可见性 | RP2-01、RP2-02、RP2-03 | AL-RF01、AL-RF04、AL-P201、AL-P202、AL-P203 |
| P2Q-11 | 读取大小/并发/批量/时长、限流与排队、超限反馈，和 Recall 实验配置兼容性 | RP2-01～RP2-04 | AL-RF04、AL-P202、AL-P203 |

#### 1.10.3 按 Recall 顺序验收

模式由授权范围和类型筛选驱动 A 内部产生，P2 不注入模式。下面是待执行场景，不是通过记录。

| 原场景编号 | 场景与验收要点 | 对接项 |
|---|---|---|
| P2T-01、P2T-02、P2T-17 | 候选证据可验证并交 B 校验；long_term_only 完成且零条、无其他缺口时才可正常空；working_only 已有可信正文时不检索或重复下载 | RP2-01、RP2-02 |
| P2T-03、P2T-04 | partial/超时且无可信内容不能算空；combined 长期失败但 Working 安全可交付时降级可用 | RP2-01 |
| P2T-05、P2T-06 | 范围/过滤不支持明确反馈，不越权或放宽；模型/维度/dtype/空间不兼容不混检 | RP2-01 |
| P2T-07、P2T-18 | 引用/版本未知则隔离；B 确认旧 Projection 已替代则排除，不补写、不配新正文 | RP2-01 |
| P2T-08、P2T-09、P2T-10、P2T-11 | 查无字节不冒充 Memory 删除；摘要/版本/残缺字节/head-get 竞态不通过时不输出；核验半开与含上界 Range 转换 | RP2-02、RP2-03 |
| P2T-12、P2T-13 | 热 miss 后权威回退成功不自动降级，内部回退不重复；释放竞态两路不可用时记录缺失，不触发调层补救 | RP2-04 |
| P2T-14 | 正文可信而层级/动作证据缺失时，基础读取继续，归因为不可验证 | RP2-05 |
| P2T-15、P2T-16 | 迟到响应不覆盖已结束/失租执行，重试不重获预算；TopK、大小或资源超限显式处理 | RP2-01、RP2-02、RP2-03 |
| P2T-28 | 最后一份正文余额下多个并发请求只有获准预占者可读取；断流、校验失败、回退及 A 重启后保守扣费，底层 SDK 不重复扣/重试 | RP2-02～RP2-04 |

真实联调需记录契约/Provider 版本、输入范围、脱敏原响应、字段映射、预期与实测、trace 和日期。文档检查或示意数据不能代替真实服务联调。

## 2. Embedding 流程：从正文片段到可核验投影

### 2.1 流程入口、顺序与已有数据定义

本章覆盖 Passage 向量生成、B 发起投影、P2 写入、操作查询、准确目标核验、结果交付，以及后续更新和删除。入口由 B 指定片段、模型与构建依据；出口是 A 已可靠保存的机制结果，B 再决定 ProjectionState。Query 复用同一推理能力，但在第一章取得向量后直接搜索，不进入本章写库步骤。

~~~text
B 准备准确 Memory/版本/chunk 的获准文本及输入证据
  → A Embedding(Passage)：绑定模型 → 推理或安全复用 → 验证并保存向量
  → 返回 SemanticEmbeddingResult 给 B
  → B 独立批准投影 upsert（向量结果 + 五元组 + 内容映射 + 元数据）
  → A 固定目标/载荷/幂等键并保存调用意图
  → P2 upsert
  → 按需 query_operation → get_projection / 等效完成证据核验
  → A 保存 ProviderResult 并向 B 返回 operation_id
  → B 补取结果并执行 Ready Guard → B 决定领域 Ready

写入回包丢失或效果未知
  → 查询原键/原操作 → 核验准确目标
  → 只有证明无效果、不会迟到且原键可安全重发时，在原额度内重发

真实版本更新
  → B 批准新五元组 → 重走写入链 → B 决定版本切换及旧目标清理
旧目标清理
  → B 批准精确 delete → A 保存退役标记 → P2 delete
  → query/get 核验删除、索引退出及旧写屏障 → A 返回机制结果
~~~

| 顺序 | 调用方 → 提供方；能力 | 输入及本步产物 | 后续处理 |
|---|---|---|---|
| 1 | B → A SemanticEmbeddingCapability，RB-08；[2.2](#22-步骤一b-交付片段a-生成-passage-向量) | 固定文本/输入绑定、usage=Passage、模型与授权 → SemanticEmbeddingResult | 向量先交 B，生成成功不自动写 P2 |
| 2 | B → A VectorProjectionPort，RB-09；[2.3](#23-步骤二b-批准投影a-固定目标与存储载荷) | 五元组、Passage 结果和内容/元数据 → VectorProjectionRequest、目标绑定和原操作 | 校验、可靠保存后才允许变更 |
| 3 | A → P2 upsert，RP2-06；[2.4](#24-步骤三调用-p2-写入向量rp2-06) | P2UpsertInput → P2MutationResult | 完成证据不足则查询；证据已齐可直接进入 2.7 |
| 4 | A → P2 query_operation，RP2-07；[2.5](#25-步骤四查询原写入或删除操作状态rp2-07) | 原目标、键/操作引用及指纹 → 原操作状态和证据 | 不产生新写；首次回包丢失也能按原键查 |
| 5 | A → P2 get_projection，RP2-08；[2.6](#26-步骤五核验投影准确目标与索引状态rp2-08) | 准确目标及预期绑定 → 实际载荷/存储/索引事实 | 可由 upsert/query 同次响应覆盖，不强制额外 RPC |
| 6 | A → B，RB-09；[2.7](#27-步骤六保存机制结果并交给-b) | operation_id + 已保存的最新 ProviderResult | B 决定 Ready Guard；等待/未知可补取和有界恢复 |
| 后续更新 | B 决策，A 重用写入链；[2.8](#28-后续分支真实版本更新与重建边界) | 真实的新 Memory/model/投影 Schema 版本及新五元组 | 旧结果不覆盖新目标；同目标 rebuild 本版拒绝 |
| 后续清理 | B → A → P2 delete，RP2-09；[2.9](#29-后续分支删除旧目标并核验不再复活rp2-09) | B 准确删除授权 → P2MutationResult | 按 2.5/2.6 查证后回到 2.7；不重新推理 |

本章序号表示处理和证据检查顺序，不要求每次调用全部 RPC。upsert 响应已经完整证明存储绑定和完成性时，可直接形成结果；异步或 UNKNOWN 才进入查询，缺准确目标事实时补充 get。PENDING/UNKNOWN 循环受原 VP 预算限制，不无限轮询。

#### 2.1.1 已有数据定义与本章使用位置

**已经有定义。** A 的完整本地对象仍以[《召回数据定义》](召回数据定义_V0.1.md)为唯一维护位置；本文只展开双方实际交接的数据视图，不复制本地执行表、租约或结果保存结构。

| 已有数据对象 | 已定义的内容 | 与 P2 的关系 |
|---|---|---|
| [19. SemanticEmbeddingRequest](召回数据定义_V0.1.md#19-semanticembeddingrequest共享向量化请求) | Query/Passage 用途、固定文本引用、输入摘要、授权、模型和空间、deadline、复用依据 | A 的推理输入，不整包发送给 P2 |
| [20. SemanticEmbeddingResult](召回数据定义_V0.1.md#20-semanticembeddingresult共享向量结果) | 真实向量引用、维度/dtype/模型绑定、输入和向量摘要、校验证据 | A 解析出真实向量，分别用于 Passage 写入或 Query 检索 |
| [21. VectorProjectionRequest](召回数据定义_V0.1.md#21-vectorprojectionrequest向量写入或删除请求) | upsert/delete、投影五元组、目标、正文/范围/元数据映射、请求指纹 | 2.4、2.9 节写删请求的来源 |
| [22. ProjectionTargetBinding](召回数据定义_V0.1.md#22-projectiontargetbinding目标绑定与禁止旧写复活) | 准确目标、首次写入绑定、退役标记 | A 本地防止旧目标重投；不能代替 P2 的在途写屏障 |
| [23. VectorProjectionOperation](召回数据定义_V0.1.md#23-vectorprojectionoperation可恢复的机制操作) | 稳定幂等键、P2 操作引用、调用前记账、查询、恢复和最新结果 | 2.5、2.6 节查询的来源；本地 operation_id 不冒充 P2 操作 ID |
| [24. ProviderResult](召回数据定义_V0.1.md#24-providerresult向量投影机制的事实结果) | ACCEPTED/PENDING/READY/FAILED/UNKNOWN、目标与完成证据、安全重发建议 | A 根据 P2 事实形成并交给 B；不是要求 P2 原样返回的结构 |
| [25. SemanticEmbeddingExecution](召回数据定义_V0.1.md#25-semanticembeddingexecution推理次数与结果恢复) | 推理状态、已占用次数、原 deadline、结果引用和恢复控制 | A 本地执行记录，不发给 P2 |

因此，当前需要补齐的是**这些本地数据如何映射到 P2，以及 P2 必须返回哪些事实**，不是重新设计 Embedding 对象。已有定义也不代表真实推理、存储与联调已经完成。

### 2.2 步骤一：B 交付片段，A 生成 Passage 向量

#### 2.2.1 功能与输入

B 按自身切分、版本及内容规则准备固定文本，A 的 SemanticEmbeddingCapability 接收规范化请求并生成真实向量。这是 RB-08 的能力交接，P2 在此步骤没有推理或正文写入调用。

| 输入组 | 数据定义与来源 | A 的校验和保存 |
|---|---|---|
| 调用关联与授权 | caller_ref、caller_request_ref、trace_id、authorization_ref；B 提供固定输入的调用身份和当前授权依据 | 同租户授权通过后规范化为 SemanticEmbeddingRequest；重试/Task attempt 不更换 caller_request_ref |
| 输入文本与片段证据 | input_ref、source_hash、input_binding_digest、input_binding_ref | A 固定受控文本；按实际 UTF-8 字节校验 source_hash，并核对 B 的 chunk、来源版本和输入转换快照 |
| 用途与模型 | usage=Passage、model_binding | model_binding 的完整字段复用[1.2.2](#122-模型与空间数据embeddingmodelbinding)，受理后不随默认配置变动 |
| 期限与配置 | deadline_at、execution_policy_ref | 固定 EM-01～EM-04，与 B 调用剩余期限取更早截止 |
| A 生成的本地绑定 | embedding_request_id、reuse_digest、RecordHeader | 字段来源及摘要按[数据定义 19](召回数据定义_V0.1.md#19-semanticembeddingrequest共享向量化请求)，不要求 B 或 P2 自行生成 A 的记录 |

超长输入明确拒绝，由 B 重新准备合法片段，不静默截断。Query/Passage 分开排队并保留各自资源；复用只采用同租户、usage、输入摘要/上下文、模型及空间完全匹配的已完成结果，使用前重新核验授权。不同请求的在途计算本版不合并；同一次调用的重试附着原执行。

#### 2.2.2 输出数据与下一步

以下是 A 向 B 返回的共享结果视图，不是 P2 的返回结构。完整记录以[SemanticEmbeddingResult](召回数据定义_V0.1.md#20-semanticembeddingresult共享向量结果)为准。

| 输出字段 | 类型 | 含义及 B 的使用 |
|---|---|---|
| embedding_result_id、request_ref | Id、Ref<SemanticEmbeddingRequest> | 结果及其最初输入请求；结果复用不改写原来源，本次调用另保留关联 |
| usage | Passage | 本章成功结果固定用途；Query 结果不能提交为投影 |
| model_binding | EmbeddingModelBinding | 实际固定模型、维度/dtype、Schema、预处理和兼容空间 |
| source_hash、input_binding_digest | Hash | 实际输入与 B 指定片段/版本绑定 |
| vector_ref、vector_hash | ProtectedRef、Hash | 可恢复解析的真实向量和规范 dtype/字节序摘要 |
| validation_evidence_ref、validated_at | EvidenceRef、Timestamp | 输入、实际模型、数值及推理/复用校验事实 |

A 先验证维度、有限数值、usage、模型/空间及摘要，再可靠保存结果。推理唯一重试协调者是 SemanticEmbeddingExecution，使用原 EM-04 和 deadline；SDK 不叠加重试，崩溃不归零。错误按主设计 EmbeddingErrorCode 返回，不生成成功向量，也不发 upsert。

B 收到结果后独立决定是否提交 2.3 节。生成成功只证明向量可用，不证明 P2 已存储；真实向量只在 A 适配 2.4 节时从 vector_ref 解析，不把本地引用冒充 P2 载荷。

### 2.3 步骤二：B 批准投影，A 固定目标与存储载荷

#### 2.3.1 B 到 A 的投影请求与本地绑定

对应 RB-09。B 指定一个准确目标及写入授权，A 规范化为[VectorProjectionRequest](召回数据定义_V0.1.md#21-vectorprojectionrequest向量写入或删除请求)。下面只列该交接的业务输入，A 的 RecordHeader/策略/指纹等完整本地字段仍维护在数据字典。

| 输入组 | 必须交接的数据 | 当前处理 |
|---|---|---|
| 动作与意图 | operation_kind=upsert、build_intent=initial_or_retry | 明确 rebuild 本版受理前拒绝，错误 PROJECTION_REBUILD_UNSUPPORTED，不调用 P2 |
| 调用与授权 | B 的 caller_ref/caller_request_ref、trace_id、准确目标 authorization_ref、owner_evidence_ref | 新 task/attempt 不产生新操作身份；每次重放仍核验当前权限 |
| 目标 | identity 五元组、provider_ref、retrieval_space_ref 和所属 tenant | 形成下节 P2ProjectionTarget；不可仅按 memory_id 覆盖 |
| 向量 | payload.embedding_result_ref | 必须解析出同租户、usage=Passage、与目标模型/空间匹配的已验证结果 |
| 正文与属性 | representation_id、content_ref/content_version、approved_range、content_evidence_ref、metadata 及其批准依据 | 必须与 B 输入片段及向量结果相容；形成 2.3.3 的 P2ProjectionPayload |
| 等待期限 | wait_deadline_at | 与 VP 同步等待限制取更早时间；到期只结束等待，不声明远端已取消 |

A 固定 target_digest、operation_key、request_fingerprint 后，一致保存不可变请求、ProjectionTargetBinding 和 VectorProjectionOperation。每次远程调用前再记录调用意图、租约及消耗次数；保存失败不发 P2。操作键由租户/Provider/空间/五元组及动作决定，随机请求或任务 ID 不参与；同目标同动作同载荷重放原操作，不同载荷冲突。

delete 也从本能力入口进入，但 build_intent=null、payload=null，完成准确删除授权及退役标记后直接走 2.9 节，不进入 Passage 推理和 upsert。

#### 2.3.2 准确投影目标：P2ProjectionTarget

| 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| tenant_id | Id | 是 | 所属租户，参与目标身份 |
| provider_ref | ExternalId | 是 | 固定目标 Provider |
| retrieval_space_ref | ContractRef | 是 | 固定检索空间，映射到 collection 等物理空间 |
| identity | ProjectionIdentity | 是 | 完整投影五元组，见下表 |
| physical_target_ref | ExternalId? | 是 | P2 准确向量对象 ID；尚未分配时可 null，但必须有稳定、唯一、可查证的映射方案 |

ProjectionIdentity 复用数据字典 21.3，不修改其语义：

| 字段 | 类型 | 定义 |
|---|---|---|
| memory_id | ExternalId | B 的 Memory 身份 |
| chunk_id | ExternalId | B 的片段身份 |
| memory_version | Version | 该次构建或删除对应的 Memory 版本 |
| model_version | Version | 投影所用模型版本，须与实际向量绑定相符 |
| projection_schema_version | Version | 原投影 Schema，与 embedding_schema_version 分开 |

A 本地 target_digest 在以上租户/Provider/空间/五元组上计算；operation_key 再区分 upsert/delete。准确计算以数据字典 21.4、23 章为准。collection + vector.id 的编码、租户隔离及与 B projection_ref 的映射须签收，不能在重试时重新随机分配目标。

#### 2.3.3 向量及关联载荷：P2ProjectionPayload

| 字段 | 类型 | 必填 | 来源与定义 |
|---|---|---|---|
| usage | `Passage` | 是 | 从已验证 SemanticEmbeddingResult 取得 |
| vector | List<number> | 是 | 从 vector_ref 解析的真实向量；具体传输 dtype/字节序按模型与 P2 契约 |
| model_binding | EmbeddingModelBinding | 是 | 已固定实际模型、输出和兼容检索空间 |
| source_hash | Hash | 是 | 原 Embedding 输入 UTF-8 字节摘要；不自动等于正文片段摘要 |
| input_binding_digest | Hash | 是 | B 片段、来源版本及输入上下文绑定摘要 |
| vector_hash | Hash | 是 | 按固定 dtype/字节序编码后的向量字节摘要 |
| representation_id | ExternalId | 是 | B 批准的投影表示关联 |
| content_ref | ExternalId | 是 | 正式正文引用 |
| content_version | Version | 是 | 本投影对应的正文版本 |
| approved_range | ByteRange | 是 | B 批准的原正文片段范围；转换为 Embedding 输入的证据由 A/B 保留 |
| metadata | P2ProjectionMetadata | 是 | B 批准、P2 需要存储用于检索的属性 |
| metadata_hash | Hash | 是 | 对本次 metadata 的 scope、memory_type、occurred_at 规范语义视图重算；等于本地 ProjectionPayload.metadata_hash |

EmbeddingModelBinding 复用[1.2.2](#122-模型与空间数据embeddingmodelbinding)的同一组字段；其权威本地定义在数据字典 19.3。

当前 VectorRecord.values 为 proto `float`。若 A 使用的 dtype 与其不相同，必须先确认兼容编码/精度与摘要规则，不能静默转换并继续声称载荷完全一致。

P2ProjectionMetadata 是本地 ProjectionMetadata 的外发属性视图：

| 字段 | 类型 | 必填 | 定义 |
|---|---|---|---|
| scope | Scope | 是 | B 批准的租户/项目/Agent/会话/任务范围快照 |
| memory_type | ExternalLabel | 是 | 本版长期类型为 Episodic 或 Semantic |
| occurred_at | Timestamp? | 是 | B 业务发生时间，未知为 null；不能填本次写库时间 |

本地 ProjectionMetadata 另有 `owner_metadata_evidence_ref`，仅由 A 保留为批准依据，**不参与 metadata_hash，也不发送到索引**。唯一摘要输入为 `JCS({scope, memory_type, occurred_at})` 的 UTF-8 字节，SHA-256，Hash.range=null、byte_encoding=jcs-utf8；scope 可空维度和未知时间均显式为 null。P2ProjectionMetadata 正是这三项语义视图，因此 A/P2 可重算同一摘要；存储编码不同须先无损还原该视图。仅换证据引用不改变摘要，Scope/类型/时间实际变化必须改变摘要并触发同键冲突；真实存储载荷绑定不能只用回显摘要证明。

本地投影记录及请求指纹使用数据定义的 projection-data-0.2 / projection-request-fingerprint-0.2 规则；本文的接口草案版本与本地记录版本分别管理。旧指纹不原地重算或换键重写。A 对 build_intent=rebuild 在调用 P2 前拒绝，该本地意图不伪装成现有 P2 方法；同目标重建能力及代际绑定单列在待确认清单第 7 章。

### 2.4 步骤三：调用 P2 写入向量（RP2-06）

#### 2.4.1 功能与调用时机

将 B 指定的**一个投影五元组**对应的 Passage 向量、模型信息、正文关联和过滤属性保存到 P2。A 已完成授权、向量验证、目标绑定和调用意图持久化后才调用。当前 `InsertVector` 是待适配入口，其 upsert 语义尚需 P2 确认。

逻辑签名：`upsert(context: P2CallContext, input: P2UpsertInput) → P2MutationResult`。公共上下文见[1.9.1](#191-公共请求与响应上下文)，P2MutationResult 见本节 2.4.3。

#### 2.4.2 我们发送的数据：P2UpsertInput

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | 完整租户/Provider/空间/五元组，见 2.3.2；不能只传 memory_id |
| provider_idempotency_key | Id | 是 | A 在调用前固定的 P2 幂等键；同操作重试沿用 |
| request_fingerprint | Hash | 是 | 数据字典 21.4 的固定请求语义摘要；同键不同摘要拒绝 |
| payload | P2ProjectionPayload | 是 | 实际向量、模型、正文映射、元数据及摘要，见 2.3.3 |
| precondition_ref | ExternalId? | 是 | 若采用条件写/代际屏障，传 P2 契约认可的条件；无该条件方案时为 null，不填写本地租约令牌 |

本地 `vector_ref`、`embedding_result_ref` 是 A 的受控引用。A 必须解析出真实向量再适配 P2，不能只把这两个字符串作为向量上传。授权凭证通过正式鉴权通道传递；不写入 metadata_json。

#### 2.4.3 期望 P2 返回的数据：P2MutationResult

本结构同时用于 upsert、delete 及其操作查询；每次响应带 1.9.1 的 P2ResponseContext。字段表示需要取得的事实，可由原报文及签收契约适配得到，不强制 P2 使用同名 JSON。

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| operation_kind | `upsert` / `delete` | 是 | 实际变更类型，必须与原请求一致 |
| target | P2ProjectionTarget | 是 | 本操作对应的准确目标 |
| provider_idempotency_key | Id | 是 | 对应原请求的幂等键；仅回显键不能证明已经执行 |
| request_fingerprint | Hash? | 是 | P2 已关联的请求语义；尚不能确认关联时为 null，不按本地输入补成已验证 |
| provider_operation_ref | ExternalId? | 是 | P2 的操作标识；可能尚未取得，此时必须具备按原键查询或等效核验路径 |
| operation_status | P2OperationStatus | 是 | 本文的事实分类，见下表；与 A 的 ProviderResult 五态不同 |
| raw_status | ExternalLabel? | 是 | 原 P2 状态或响应标签；若协议无此字段，保留原响应证据，不编造标签 |
| target_state | P2TargetState? | 是 | 当前已取得的对象、载荷及完成性观察，见 2.6 节；受理阶段可 null |
| resubmit_assessment | P2ResubmitAssessment? | 是 | 仅在能评估安全重发时返回，见 2.5 节；普通 not_found 不产生肯定结论 |
| evidence | List<P2Evidence> | 是 | 此次操作关联及进度的原始依据；见 1.9.2；明确成功/拒绝/失败须有依据 |

| P2OperationStatus | 必须具有的事实 | A 的处理 |
|---|---|---|
| accepted | 已受理准确操作，尚无后续完成信息 | 形成 ACCEPTED，继续查询 |
| running | 明确仍在执行，或索引仍未达到约定条件 | 形成 PENDING，继续查询 |
| completed | P2 声明该操作结束 | 再核验 target_state；满足2.7 节条件才形成 READY |
| rejected / failed | 明确拒绝或失败，不是可能仍在执行 | 按有据事实形成 FAILED；保留副作用说明 |
| not_found | 操作查询没有找到记录 | 不能据此认定前次无效果；通常维持 UNKNOWN |
| unknown | 效果、对应关系或必要证据无法判定 | UNKNOWN，先查询 |

网络超时导致完全没有可信响应时，A 自己记录 UNKNOWN；不伪造一份来自 P2 的 `operation_status=unknown` 响应。错误还需按 1.9.3 表达其副作用确定性。

#### 2.4.4 约束与示例

同目标、同动作、同载荷复用原操作；同键更换向量、正文映射或元数据必须拒绝。Memory/model/投影 Schema 版本变化，使用新的五元组和物理目标。Query 向量不进入本接口；多 chunk 分别产生操作，单条完成不代表整份 Memory 完成。

贯穿样例：`memory-42 / chunk-1 / memory-v3 / model-v1 / projection-v1`。以下是**草案业务参数示例**，公共 context 单独传递；`<…>` 是待替换的摘要/契约/引用占位符。三维向量仅用于展示格式，不代表真实模型结果或生产维度。

```json
{
  "target": {
    "tenant_id": "tenant-demo",
    "provider_ref": "p2-demo",
    "retrieval_space_ref": "space-demo-v1",
    "identity": {
      "memory_id": "memory-42",
      "chunk_id": "chunk-1",
      "memory_version": "memory-v3",
      "model_version": "model-v1",
      "projection_schema_version": "projection-v1"
    },
    "physical_target_ref": "vector-demo-42-1-v3"
  },
  "provider_idempotency_key": "upsert-demo-42-1-v3",
  "request_fingerprint": {
    "algorithm": "SHA-256", "value": "<完整请求指纹>",
    "byte_encoding": "jcs-utf8", "range": null
  },
  "payload": {
    "usage": "Passage",
    "vector": [0.25, 0.5, -0.75],
    "model_binding": {
      "model_id": "model-demo", "model_version": "model-v1",
      "dimension": 3, "dtype": "float32",
      "embedding_schema_version": "embedding-v1",
      "preprocessing_version": "preprocess-v1",
      "retrieval_space_ref": "space-demo-v1",
      "model_contract_ref": "<模型契约及float32字节序>"
    },
    "source_hash": {
      "algorithm": "SHA-256", "value": "<原始输入UTF-8摘要>",
      "byte_encoding": "utf-8", "range": null
    },
    "input_binding_digest": {
      "algorithm": "SHA-256", "value": "<输入上下文摘要>",
      "byte_encoding": "jcs-utf8", "range": null
    },
    "vector_hash": {
      "algorithm": "SHA-256", "value": "<规范向量字节摘要>",
      "byte_encoding": "raw", "range": null
    },
    "representation_id": "representation-42-v3",
    "content_ref": "content-42-v3", "content_version": "content-v3",
    "approved_range": {"start": 0, "end": 15},
    "metadata": {
      "scope": {
        "tenant_id": "tenant-demo", "project_id": "project-demo",
        "agent_id": null, "session_id": null, "task_id": null
      },
      "memory_type": "Semantic", "occurred_at": null
    },
    "metadata_hash": {
      "algorithm": "SHA-256", "value": "3ea4eb2a391598ea5c075e2b5eec5bc4122f0fe16fedb868cd23dca6042e092f",
      "byte_encoding": "jcs-utf8", "range": null
    }
  },
  "precondition_ref": null
}
```

其中 source_hash 可对应示例文本“接口约定。”的 15 个 UTF-8 字节；正式正文不随向量上传。P2 原适配示意为 `collection ← 空间映射`、`records[0].id ← 准确物理目标`、`values ← vector`、`metadata_json ← 已确认的关联字段编码`。其余幂等、操作查询与证明不能仅靠塞入 metadata_json 就宣称具备。

期望首次响应：准确关联该 upsert，返回 `operation_status=accepted` 和 `provider_operation_ref=operation-demo-1`，`target_state` 可为 null。之后查询到 `object_present=true、durable=true、index_queryable=true`，并核验真实载荷绑定及完成证据，A 才交付 READY。当前协议仅返回 `inserted`，仍需按 P2Q-13～P2Q-16 补齐语义。

**本步出口。** A 保存 P2 原响应及其操作关联。若已获得完整目标、真实载荷绑定和完成证据，进入 2.7 节判定；只有受理/执行中或效果未知则转 2.5 节。正式原文由 B 的内容流程保存，本接口只写向量及批准元数据；不隐式调用 OBJ-001 写正文。

### 2.5 步骤四：查询原写入或删除操作状态（RP2-07）

#### 2.5.1 功能与调用时机

查明**一次既有 upsert/delete 操作**是否受理、执行中、完成或明确失败。用于正常异步等待、请求超时、首次响应丢失和 A 重启恢复；不会创建新的变更操作。逻辑签名：`query_operation(context: P2CallContext, input: P2OperationQueryInput) → P2MutationResult`。

#### 2.5.2 我们发送的数据：P2OperationQueryInput

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | 原操作准确目标，不能换空间或版本 |
| operation_kind | `upsert` / `delete` | 是 | 原动作，区分同目标上的写与删 |
| provider_operation_ref | ExternalId? | 是 | 已取得时优先使用；首次响应丢失可 null |
| provider_idempotency_key | Id | 是 | 原 P2 幂等键；没有 operation_ref 时仍须能够据此核验 |
| request_fingerprint | Hash | 是 | A 已固定的原载荷语义；防止关联到同键不同请求 |

同时提供操作引用和幂等键时，二者必须关联同一操作；不一致不能任选其一继续。A 的 operation_id 仅用于本地定位，不直接当作 provider_operation_ref。

#### 2.5.3 期望返回与安全重发数据定义

返回完整 P2MutationResult，特别是操作关联、当前事实及相应证据。若 P2 不采用独立操作表，也须提供能证明**原请求效果**的等效权威查询；只有目标是否存在仍不充分。

P2ResubmitAssessment 定义如下，各字段必须关联原租户、目标、动作、幂等键及指纹：

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| no_effect_confirmed | bool? | 是 | 是否已证明原请求没有产生效果；未知为 null |
| no_late_effect_confirmed | bool? | 是 | 是否已证明原请求不会在稍后生效 |
| same_key_resubmit_allowed | bool? | 是 | 契约是否允许沿用原键提交同载荷 |
| idempotency_valid_until | Timestamp? | 是 | 可证明的原键有效截止；无时间型截止时为 null，由契约解释有效窗口 |
| evidence | List<P2Evidence> | 是 | 上述判断的权威依据及适用窗口；肯定判断不能只靠普通 not_found |

仅前三项均为 true、证据和幂等窗口有效，且 A 原写次数及恢复预算允许时，才可建议 safe_resubmit。已经 settled 的 FAILED 不借此重开。

#### 2.5.4 示例与异常

| 场景 | 输入示例 | 期望 P2 输出及 A 处理 |
|---|---|---|
| 正常等待 | 上例 target；`operation_kind=upsert`；`provider_operation_ref=operation-demo-1`；原键/原指纹 | `running`、索引尚未就绪的事实 → PENDING |
| 首次响应丢失 | 同一 target/键/指纹；`provider_operation_ref=null` | 按原键找回准确操作及状态，不要求 A 换键重写 |
| 查无操作记录 | 同一查询 | `not_found`；无法证明无效果/无迟到时 resubmit_assessment 为 null 或未知 → UNKNOWN |
| 可以安全重发 | 同一查询 | 三项肯定事实及有效证据齐全 → A 可在原额度内按同键同载荷重发 |
| 记录已过保留期 | 原键已不能得到权威结果 | 明示证据/幂等窗口失效；不能把“记录过期”当作“从未执行” |

下面是按原键找回已完成 upsert 的 **P2MutationResult 期望返回示例**，公共响应上下文另带。这里假定 P2 已提供可验证的存储绑定与完成性契约；占位证据不代表现实中已有该能力。首次 upsert 也可直接返回同一结构，delete 则按2.9 节更换动作及完成事实。

```json
{
  "operation_kind": "upsert",
  "target": {
    "tenant_id": "tenant-demo", "provider_ref": "p2-demo",
    "retrieval_space_ref": "space-demo-v1",
    "identity": {
      "memory_id": "memory-42", "chunk_id": "chunk-1",
      "memory_version": "memory-v3", "model_version": "model-v1",
      "projection_schema_version": "projection-v1"
    },
    "physical_target_ref": "vector-demo-42-1-v3"
  },
  "provider_idempotency_key": "upsert-demo-42-1-v3",
  "request_fingerprint": {
    "algorithm": "SHA-256", "value": "<完整请求指纹>",
    "byte_encoding": "jcs-utf8", "range": null
  },
  "provider_operation_ref": "operation-demo-1",
  "operation_status": "completed", "raw_status": null,
  "target_state": {
    "target": {
      "tenant_id": "tenant-demo", "provider_ref": "p2-demo",
      "retrieval_space_ref": "space-demo-v1",
      "identity": {
        "memory_id": "memory-42", "chunk_id": "chunk-1",
        "memory_version": "memory-v3", "model_version": "model-v1",
        "projection_schema_version": "projection-v1"
      },
      "physical_target_ref": "vector-demo-42-1-v3"
    },
    "object_present": true,
    "stored_request_fingerprint": {
      "algorithm": "SHA-256", "value": "<完整请求指纹>",
      "byte_encoding": "jcs-utf8", "range": null
    },
    "stored_payload": null,
    "durable": true, "index_queryable": true,
    "delete_confirmed": null, "late_write_barrier_confirmed": null,
    "binding_evidence": [{
      "kind": "stored_payload_binding", "contract_ref": "<实际存储绑定契约>",
      "provider_evidence_ref": "<完整载荷与准确目标绑定的证据>",
      "observed_at": "2026-09-07T01:59:00.000Z"
    }],
    "completion_evidence": [{
      "kind": "durable_and_queryable", "contract_ref": "<持久化及索引完成契约>",
      "provider_evidence_ref": "<该目标满足完成条件的证据>",
      "observed_at": "2026-09-07T01:59:00.000Z"
    }],
    "observed_at": "2026-09-07T01:59:00.000Z"
  },
  "resubmit_assessment": null,
  "evidence": [{
    "kind": "operation_binding", "contract_ref": "<按原键查询操作的契约>",
    "provider_evidence_ref": "<原键与目标及该操作对应的证据>",
    "observed_at": "2026-09-07T01:59:00.000Z"
  }]
}
```

当前 proto 未声明此查询方法；准确接入方式及窗口对应 P2Q-13、P2Q-15、P2Q-16，未确认前未知写入不能自动盲重试。

**下一步。** 查询返回的 target_state 已涵盖准确载荷和完成事实时，直接按 2.7 节形成结果；仍缺目标事实时执行 2.6 节。继续查询、等待或安全重发都使用原目标、原键、原指纹和原预算；任何一次普通 not_found 都不是新建 upsert 的许可。

### 2.6 步骤五：核验投影准确目标与索引状态（RP2-08）

#### 2.6.1 功能与调用时机

查明**一个准确投影对象当前是什么**：是否存在、存储载荷是否对应预期、是否持久化、能否按约定被检索，以及删除是否完成。它与2.5 节查操作进度互补。允许由同一次 status/get 响应覆盖，不强制多建 RPC。

逻辑签名：`get_projection(context: P2CallContext, input: P2TargetQueryInput) → P2TargetState`。ANN 未命中和 Segment 行数都不能代替准确目标查验。

#### 2.6.2 我们发送的数据：P2TargetQueryInput

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | 完整目标，物理 ID 已知时须与逻辑目标一致 |
| expected_request_fingerprint | Hash? | 是 | 要核对的原写入载荷指纹；delete 时可为原 upsert 指纹，不能误用无载荷的 delete 指纹 |
| related_operation_ref | ExternalId? | 是 | 关联待确认的 P2 操作；无引用时可 null |
| include_payload | bool | 是 | 是否需要回读真实载荷；已有充分存储绑定证明时可 false，减少向量传输 |

#### 2.6.3 期望 P2 返回的数据：P2TargetState

| 字段 | 类型 | 必填 | 含义与采用条件 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | 实际查验目标；已确定物理 ID 时必须带回对应关系 |
| object_present | bool? | 是 | 准确对象存在性；权威查无才为 false，无法判断为 null |
| stored_request_fingerprint | Hash? | 是 | 实际存储载荷对应的请求指纹；不是简单回显 expected_request_fingerprint |
| stored_payload | P2ProjectionPayload? | 是 | 实际回读内容；未请求、对象不存在或无法取得时可 null |
| durable | bool? | 是 | 是否满足签收的持久化条件；不等同于“收到请求” |
| index_queryable | bool? | 是 | 是否满足目标空间的查询可见性规则；不是保证某条任意 Query 必定命中 |
| delete_confirmed | bool? | 是 | 是否已确认准确目标删除完成；普通写入观察不适用时 null |
| late_write_barrier_confirmed | bool? | 是 | 旧在途写是否已被排空或受有效屏障约束；仅本地停止重试不充分 |
| binding_evidence | List<P2Evidence> | 是 | 对实际向量、模型、Schema、正文映射及过滤属性的绑定证明 |
| completion_evidence | List<P2Evidence> | 是 | 持久化/索引或删除/屏障达到约定条件的证明 |
| observed_at | Timestamp? | 是 | P2 能提供的本次状态观察时间；A 收到响应的时间另记 |

P2 可以返回实际载荷供 A 按原向量编码计算摘要，或按签收契约提供等效的存储绑定证明。**只保存/回显 A 传入的一个摘要字段，不能自行证明其他向量和元数据也正确存储。** 采用哪种证明及它覆盖什么，须由 AL-P205、AL-P207 确认。

来自多次查询的状态可能发生变化；不能把不同目标、版本或不兼容观察时点的“存在”“索引就绪”拼成一份 READY 证据。

#### 2.6.4 示例与异常

| 示例输入 | 期望输出 | 处理 |
|---|---|---|
| 上例 target + 原 upsert 指纹；`include_payload=false` | 对象存在，真实绑定正确，durable=true，index_queryable=true，证据齐全 | upsert 可进入机制 READY 判定 |
| 同一目标 | 对象存在，durable=true，index_queryable=false，明确仍在索引中 | PENDING，继续查询 |
| 同一目标 | 对象存在，但 stored_request_fingerprint/回读向量或元数据不符 | 记录 PROJECTION_BINDING_MISMATCH，不 READY |
| 删除后查询同一目标 | object_present=false，但屏障仍未知 | 不能确认安全删除；继续核验或保留 UNKNOWN |

当前 proto 未声明准确向量 get/readiness；具体方法和完成性证明对应 P2Q-13、P2Q-14、P2Q-16、P2Q-17。

**下一步。** 返回同[1.9.1](#191-公共请求与响应上下文)的响应上下文，由 A 按下节形成机制结果。索引明确仍在构建才 PENDING；完成性或对应关系无法判断为 UNKNOWN。ANN 搜索未命中不证明对象不存在；是否另需 search probe 属于 P2Q-16/AL-P207，未签收不增加一条强制搜索步骤。

### 2.7 步骤六：保存机制结果并交给 B

#### 2.7.1 从 P2 事实形成 A 的 ProviderResult

P2MutationResult / P2TargetState 是物理事实输入，A 的 ProviderResult 是机制结论；B 的 ProjectionState 是领域状态，三者不能混用。

| 来源事实 | A 形成或更新的本地对象 | 必须满足的条件 |
|---|---|---|
| P2 已受理/执行中 | ProviderResult ACCEPTED/PENDING | 原目标、动作和操作关联可信；非 P2 事实不能伪造为 Ack |
| upsert 完成事实 | ProviderResult READY | object_present=true、durable=true、index_queryable=true，真实载荷绑定及签收完成条件证据齐全 |
| delete 完成事实 | ProviderResult READY，operation_kind=delete | object_present=false、index_queryable=false、delete_confirmed=true、late_write_barrier_confirmed=true，准确目标及完成证据齐全 |
| 明确拒绝/失败 | ProviderResult FAILED | 有依据且不是可能仍在执行；保留原原因与效果说明 |
| 超时/操作关联不明/必要完成证据不足 | ProviderResult UNKNOWN；只有可信证据证明原操作仍执行/建索引才为 PENDING | 收到响应也可能 UNKNOWN；不以成功状态码、计数、普通 not_found 或恢复额度耗尽替代机制结论 |

本地 ProviderResult 字段以[数据定义 24](召回数据定义_V0.1.md#24-providerresult向量投影机制的事实结果)为准。binding_evidence_ref、completion_evidence_ref 指向 A 保存的原响应及契约事实链；durable 是 P2 完成性输入，归入本地完成证据，不擅自新增同名本地字段。仅回显请求摘要不能证明真实载荷，互不相容时点的证据也不能拼成 READY。

#### 2.7.2 返回给 B、补取与未知恢复

A 一致保存原始观察、不可变 ProviderResult 和 Operation 最新结果引用，再通过既有 submit/query 向 B 返回 operation_id 及最新已保存结果，不依赖新回调或共享事件。CAS 失败的旧 Worker 不改写新结果；保存失败而 P2 可能已执行时，报告机制暂不可用并恢复查证，不能重新盲写。

B 读取 ACCEPTED/PENDING/UNKNOWN 时，知道机制尚未取得完成结论，可按 operation_id 补取；获取 READY 后仍执行自己的 Ready Guard，核对当前 Memory/version、正文、模型/维度/Schema 和可读证据。旧 memory-v3 的 READY 不适用于已切换的 memory-v4，也不自动让整份多 chunk Memory Ready。字段消费与当前性由[RB-09](召回与记忆形成接口对接需求_V0.1.md#1-双向接口与信息交接)约定。

等待到期时返回最新保存状态与操作标识；后台按原 VP-02/VP-03 额度恢复。只有 2.5 节的无效果、无迟到、同键可重发三项均证实且窗口有效，才可在原变更次数内重发同载荷。FAILED 同键重放仍返回失败；READY 重放是历史事实，不证明当前对象仍在。额度/恢复窗口耗尽则 attention_required，保留 UNKNOWN/PENDING 及证据，不制造永久失败或成功。

本章使用[主设计第 2 章的 EM/VP 参数](召回流程详细设计_V0.1.md#2-embedding-与向量投影写入怎么实现)，与 Recall 在线 P 参数分开；B 重投和 A 重启不重置原额度。写路径不生成 SearchHit、ContextSelected、ContextEmitted 事件。

### 2.8 后续分支：真实版本更新与重建边界

| B 的请求/变化 | A 与 P2 的执行顺序 | B 采用结果的条件 |
|---|---|---|
| 同目标、同载荷的普通重试 | 复用原 operation_id，查询或返回原保存结果；不重新生成向量/分配物理 ID | 历史 READY 不证明缺失对象已修复，当前可读性仍需 B 核验 |
| 同目标但向量、内容映射或属性发生变化 | 同 operation_key 的 request_fingerprint 冲突，拒绝覆盖 | 由 B 决定合法版本变化及新的构建输入 |
| Memory/model/投影 Schema 的真实版本变化 | B 批准新五元组；依次执行 2.2～2.7，写入独立物理目标 | B 核对新版本，决定领域切换及旧目标清理时间 |
| 明确同五元组 rebuild | 受理前返回 PROJECTION_REBUILD_UNSUPPORTED；P2 调用数为 0 | 同版本新代际方案签收并另升契约后才启用 |
| 旧目标待清理 | B 提供准确删除授权，执行 2.9 → 2.5/2.6 → 2.7 | 新旧目标结果分开消费，迟到旧 READY 不覆盖新版本 |

只更换 owner_metadata_evidence_ref 仍需重验权限/语义，但不会改变 metadata_hash/指纹；Scope、类型、时间或指纹明确包含的 content_ref 等真实值变化不能忽略。不能用新 task/attempt、随机键、伪造版本或原地重算旧指纹来绕过绑定。

同版本重建的代际身份、旧写失权、删除屏障和 Ready Guard 消费统一在[待确认事项第 7 章](跨模块待确认事项_V0.1.md#7-同版本投影重建必须一起确认的决策)，本章不把尚未签收的新机制写成现有接口。

### 2.9 后续分支：删除旧目标并核验不再复活（RP2-09）

#### 2.9.1 功能与调用时机

删除 B 明确批准的准确投影目标，确认其退出查询，并防止旧在途 upsert 在删除后重新生效。A 先可靠保存目标退役标记，再调用 P2；不按一个 memory_id 无条件删除所有版本或 chunk。

逻辑签名：`delete_projection(context: P2CallContext, input: P2DeleteInput) → P2MutationResult`。当前待适配入口为 DeleteVectors。

#### 2.9.2 我们发送的数据：P2DeleteInput

| 字段 | 类型 | 必填 | 功能、来源与约束 |
|---|---|---|---|
| target | P2ProjectionTarget | 是 | B 批准删除的完整目标，与原 upsert 目标对应 |
| provider_idempotency_key | Id | 是 | 稳定 delete 幂等键，与 upsert 键区分 |
| request_fingerprint | Hash | 是 | 删除请求语义摘要，不含向量载荷 |
| precondition_ref | ExternalId? | 是 | P2 条件删除/代际屏障条件；若采用排空后删除方案可 null，但仍须取得完成证据 |
| related_upsert_keys | List<Id> | 是 | A 已知的该目标历史在途写关联，供核验；不能仅靠此列表断言不存在其他受影响写入 |

不重新调用 Embedding，不携带新的向量或内容。屏障/排空证明须覆盖契约范围内可能迟到影响该目标的旧写；具体条件标识与作用域由 P2 确认。

#### 2.9.3 期望返回、示例与完成条件

返回2.4.3 节 P2MutationResult，`operation_kind=delete`。target_state 必须给出如下事实，A 才可形成 delete READY：

| 返回事实 | 必须值或要求 |
|---|---|
| target | 与批准删除的租户、空间、五元组一致 |
| object_present | false |
| index_queryable | false |
| delete_confirmed | true |
| late_write_barrier_confirmed | true |
| completion_evidence | 可验证的精确删除、索引退出及屏障/排空依据 |

示例：沿用2.4 节目标，发送 `provider_idempotency_key=delete-demo-42-1-v3`、对应删除指纹、`related_upsert_keys=["upsert-demo-42-1-v3"]`。P2 首先返回 `accepted`；查询确认上述四个布尔事实及完成证据后才结束核验。

重复 delete 应按原键返回原操作事实；`deleted=0` 可能是已经删除、目标不存在或其他情况，不能仅凭计数推导安全完成。删除 Ack 后仍能检索，或者旧 upsert 仍可能生效，均不能 READY。已退役五元组在 V0.1 不自动复活，B 负责批准新的构建身份。

相关待确认项：P2Q-13、P2Q-15、P2Q-17、P2Q-18。

**本分支闭环。** delete 沿用 2.4.3 的 P2MutationResult、1.9 的公共上下文/错误；受理或效果未知后以 operation_kind=delete 调用 2.5 节，再按需调用 2.6 节查准确目标。四个删除布尔事实及有效完成证据齐全，A 才按 2.7 节保存 delete READY 交 B。退役标记只限制 A 的机制操作，不修改 B 的 Memory/Projection 生命周期。

### 2.10 本流程的协议映射与联调要求

#### 2.10.1 现有协议能提供什么

| 本次需求 | 已声明的原方法及字段 | 仍需对齐的内容 |
|---|---|---|
| upsert | `InsertVector(collection, records)`；record 为 `id, values, graph_node_id?, metadata_json?`；返回 `inserted` | 是否具有 upsert/同键冲突语义；稳定目标、幂等键、操作查询、存储及索引证明 |
| query/get | 当前 VectorService 未声明按操作或准确向量 ID 查询的方法 | 确认既有其他接口或扩展方案；不能用 SearchVector/SegmentStats 顶替 |
| delete | `DeleteVectors(collection, ids)`；返回 `deleted` | 精确版本绑定、幂等、删除完成、索引退出和在途写屏障 |

inserted=1 只是当前响应中的计数，deleted=1 也不证明旧写不会迟到复活。幂等/操作查询/准确对象核验及完成性不能仅靠 metadata_json 增加几个字段就宣称支持；未签收完成性契约时，真实 READY 路径关闭。

#### 2.10.2 P2 需提供的对接材料

按 2.4 → 2.5 → 2.6 → 2.9 提供真实方法、请求和响应样例、逐字段适配、错误/副作用、保证范围、服务限制及测试环境；实际签收状态只记入[独立清单](跨模块待确认事项_V0.1.md#3-p2--内容-provider)。

| 原问题编号 | 需确认内容 | 对接项 | 关联 AL |
|---|---|---|---|
| P2Q-13 | VEC-001 实际 upsert/delete/get/status 方法和样例，物理目标如何绑定租户、空间及完整五元组 | RP2-06、RP2-07、RP2-09 | AL-P205 |
| P2Q-14 | 向量 dtype/维度/Schema、模型空间、正文引用及过滤属性编码；如何证明真实存储载荷与输入相符 | RP2-06、RP2-08 | AL-P205、AL-B08 |
| P2Q-15 | 幂等作用域/保留期、同键冲突、首次响应丢失时查询、not_found/记录过期及安全重发条件 | RP2-06、RP2-07 | AL-P206 |
| P2Q-16 | 受理、存储完成、index_ready、read-after-write、search probe 各证明什么；READY 所需完成证据 | RP2-07、RP2-08 | AL-P207 |
| P2Q-17 | 精确删除、条件/代际、在途 upsert 与 delete 排序、索引退出及防复活证据 | RP2-09、RP2-07 | AL-P206、AL-P207 |
| P2Q-18 | 写/查/删的大小、维度、批量、并发、超时、限流、逐项部分失败及联调环境 | RP2-06～RP2-09 | AL-P208 |

#### 2.10.3 按 Embedding 与投影顺序验收

| 原场景编号 | 场景与验收要点 | 对接项 |
|---|---|---|
| P2T-19 | upsert 向量/元数据/目标完整，机制 READY 有持久化、绑定和可查询性证据；B Ready Guard 前不宣称领域 Ready | RP2-06、RP2-08 |
| P2T-20 | 同目标同载荷重复写复用原操作；同键换向量、Schema 或元数据拒绝，不覆盖旧内容 | RP2-06、RP2-07 |
| P2T-21 | P2 已执行但首次回复丢失，无 operation_ref 仍按原键查证；UNKNOWN 不换键盲重试；记录过期不当未执行 | RP2-07 |
| P2T-22 | ACCEPTED/PENDING、延迟索引、对象存在但绑定错误分别处理；成功状态码、inserted 计数或请求摘要回显不直接 READY | RP2-07、RP2-08 |
| P2T-23 | A 在调用前/调用后/落结果前重启，租约与预算不重置；迟到响应或回调不覆盖新结果 | RP2-06、RP2-07 |
| P2T-24 | V3→V4、新模型或 Schema 使用独立目标；旧操作迟到结果不替换新目标，B 决定旧目标清理 | RP2-06、RP2-08、RP2-09 |
| P2T-25 | 精确/重复 delete、先删后到的旧 upsert、删除 Ack 后索引仍可见；无屏障证据不确认安全删除 | RP2-09、RP2-07 |
| P2T-26 | 多 chunk 部分失败逐项保留；非法/Query 向量在 P2 前拒绝；过载和恢复窗口耗尽不伪造成功/永久失败 | RP2-06～RP2-09 |
| P2T-27 | 只更换 owner_metadata_evidence_ref 时 metadata_hash/请求指纹不变；改变 Scope/类型/时间时摘要改变并触发同键冲突；A/P2 对规范属性视图重算一致 | RP2-06、RP2-08 |
| P2T-29 | 已返回 completed 但缺必要证据、回包绑定矛盾、普通 not_found：均不能 READY；确有执行中证据才 PENDING，其他保持 UNKNOWN | RP2-07、RP2-08 |
| P2T-30 | A 收到明确同目标 rebuild 意图时受理前拒绝且 P2 调用数为 0；普通重投复用原操作，真实新五元组按已确认路径处理 | RP2-06、RP2-09 |

真实联调需保留契约/Provider 版本、脱敏原请求响应、A 字段映射、逐项预期与实测、trace 和日期。示意向量、模拟结果和文档校验不能当成真实推理、存储或删除验收。

#### 2.10.4 本次文档核对范围

本次按两条流程重排接口，并对照主设计、数据字典、B/C 交接和当前 proto 核对执行顺序、职责、字段流转及异常分支；保留 RP2-01～RP2-09、P2Q-01～P2Q-18、P2T-01～P2T-30。四个 P2 JSON 示例分别随搜索、正文读取、写入和操作查询接口保留，字段表中的数据定义与共用类型相互链接。

本次是开发文档修订；外部契约仍以独立待确认清单为准，真实能力按对应启用条件接入。


# 第三章 Operate

# AetherStore P3 C 组与 P2 接口对接需求 V0.2（按流程组织）

更新：2026-09-07。维护方：C/Operate；接收方：P2 / Provider。

**文档目的与阅读方式。**

本文档参考《召回与P2接口对接需求_V0.1》采用按流程组织的写法，说明 C 组在完整调度流程中：

- 哪一步需要调用 P2；
- C 需要向 P2 发送哪些字段；
- P2 需要返回哪些字段；
- C 如何判断返回结果；
- 失败、超时、反馈丢失和版本冲突后如何继续。

本文档只描述 C/Operate 与 P2/Provider 的调度对接，不复制 A/Recall 与 P2 的搜索、正文读取和在线 `Prewarm` 接口。

本文档中的字段只定义业务含义，不冻结具体数据类型、序列化格式、RPC/HTTP 方法名或接口路径。逻辑接口不要求一定拆分为独立 RPC，最终方法和报文以 C/P2 联调签收结果为准。

本文档不冻结具体 Representation 类型。`Representation` 只表示与 `Memory` 关联、可以被读取或调度的抽象表示。

**C-P2 对接范围。**

**C 的职责边界与完整流程：**

```text
MemorySignal / AccessTrace / PolicyContext
        ↓
读取真实 Placement 和 ResourceState
        ↓
C 生成 RepresentationPlacementPlan
        ↓
ResolveActuationTarget
        ↓
C 生成 TierAction
        ↓
SubmitTierAction
        ↓
P2 执行物理动作
        ↓
ExecutionFeedback / QueryActionStatus
        ↓
重新读取 Placement
        ↓
C 完成 Action 收口与 Reconciliation
```

C 负责：

- 为什么调度；
- 什么时候调度；
- 对哪个 `representation_id` 调度；
- 目标层级和优先级是什么；
- 如何处理冲突、反馈丢失和 Unknown。

P2 / Provider 负责：

- 将抽象目标解析为真实执行目标；
- 执行 Copy、Verify、Cutover、Reclaim 等物理步骤；
- 返回真实层级、版本、路由和资源事实；
- 返回执行进度、最终结果和失败原因。

**不属于本文档的接口：**

以下能力属于 A/Recall-P2 对接范围，不在本文档重复定义完整请求和响应：

| 能力 | 用途 | 与 C 的关系 |
|---|---|---|
| `search` | 在线召回时搜索候选 | C 不调用它生成调度动作 |
| `get_content` / `get_range` | 在线召回时读取正文 | C 不通过它执行迁移 |
| `head/checksum` | 在线读取前后的正文元数据核验 | C 不用它替代 Placement 查询 |
| RP2-04 `Prewarm` | 在线读取时尝试已经存在的热副本 | 不产生 C 的 `TierAction` |
| RP2-05 `P2ReadPlacement` | 返回读取时的实际来源和放置观察 | 作为 C 的 `AccessTrace` / 归因输入 |

**核心接口总览。**

下表按逻辑能力计数，不要求每项都是独立 RPC，也不表示每次流程都必须调用全部接口。

| 所属阶段 | 接口编号 | 逻辑能力 | C 提供的核心输入 | 期望 P2 返回的数据 |
|---|---|---|---|---|
| 状态读取 | CP2-01 | `GetPlacement` | Representation、Provider 引用或不透明目标 | 真实层级、对象版本、路由版本、可读性和观测时间 |
| 资源准入 | CP2-02 | `GetResourceState` | 资源范围和 Provider 范围 | 容量、压力、预算、并发、带宽和健康状态 |
| 目标解析 | CP2-03 | `ResolveActuationTarget` | `representation_id`、目标层级、目标版本 | 不透明 `ActuationTarget`、支持动作、有效期和当前版本 |
| 动作提交 | CP2-04 | `SubmitTierAction` | C 生成的 `TierAction` 和幂等键 | `provider_task_id`、接收状态和错误原因 |
| 结果查询 | CP2-05 | `QueryActionStatus` | `action_id` 或 `provider_task_id` | 原动作的真实进度、结果、层级、版本和证据 |
| 反馈订阅 | CP2-06 | `WatchExecutionFeedback` | `action_id`、反馈游标 | 增量执行反馈，支持去重和断点续传 |

支持能力：

- CP2-07：物理迁移执行，由 P2/Provider 内部承接，不要求 C 直接调用 `Copy`、`Verify`、`Cutover`、`Reclaim`；
- CP2-08：`Freeze`、`Unfreeze`、可选 `Cancel`，只有 P2 明确支持时启用。

**两类查询必须区分：**

```text
QueryActionStatus：这一次 action 执行到了哪里？
GetPlacement：这个对象当前真实处于什么状态？
```

两类事实可以由同一个 P2 服务返回，但必须分别满足：

- `QueryActionStatus` 必须绑定原 `action_id` 或 `provider_task_id`；
- `GetPlacement` 必须返回当前真实 `current_tier`、`generation` 和 `route_epoch`；
- P2 不能只返回“任务存在”，而不返回真实执行结果或可对账的状态；
- C 不能只凭 Action 状态代替最新 Placement。

## 1. C-P2 流程：从输入事实到调度收口

| 顺序 | 调用方 → P2 | 调用时机 | 本步产物 | 下一步 |
|---|---|---|---|---|
| 1 | C → `GetPlacement` | 生成计划前，或已有观察过期 | 当前真实 Placement | 判断是否需要调度 |
| 2 | C → `GetResourceState` | 计划提交前 | 当前资源准入事实 | 判断是否允许新动作 |
| 3 | C → `ResolveActuationTarget` | Plan 有效且确定目标动作后 | 不透明 ActuationTarget | 生成 TierAction |
| 4 | C → `SubmitTierAction` | 目标和资源校验通过后 | P2 接收结果 | 等待反馈或查询 |
| 5 | P2 → C `WatchExecutionFeedback` | 动作执行期间和完成后 | ExecutionFeedback | 推进 Action 状态 |
| 6 | C → `QueryActionStatus` | 反馈丢失、超时、重启或 Unknown | 原动作真实结果 | 判断是否需要继续对账 |
| 7 | C → `GetPlacement` | 成功反馈后、对账时、释放前 | 最新真实 Placement | 收口 Action 或创建对账任务 |

### 1.1 流程入口、顺序与分支

```text
C 获取 Placement
  → C 获取 ResourceState
  → C 生成 PlacementPlan
  → C 解析 ActuationTarget
  → C 生成 TierAction.Generated
  → C 提交 TierAction
  → P2 返回 ACCEPTED / RUNNING
  → P2 执行物理动作
  → P2 返回 SUCCEEDED
  → C 再次获取 Placement
  → C 校验层级、对象、generation、route_epoch
  → TierAction.Succeeded
```

### 1.2 反馈丢失流程

```text
反馈丢失或超时
  → TierAction.Unknown
  → QueryActionStatus
  → GetPlacement
  → 已达到目标：原 Action 收口为 Succeeded
  → 明确未执行：根据最新 Plan 创建新的 action_id
  → 仍无法确认：继续 Reconciliation，不盲目重试
```

### 1.3 步骤一：读取真实 Placement（CP2-01）

#### 1.3.1 功能与调用时机

`GetPlacement` 用于确认某个抽象 `Representation` 当前真实位于什么层级、属于哪个对象版本、路由是否发生变化，以及当前是否仍可读。

以下场景必须调用或重新调用：

- 生成新的 PlacementPlan 前；
- 提交动作前发现快照可能过期；
- 收到 `SUCCEEDED` 反馈后；
- `TierAction` 进入 `Unknown` 后；
- 计划 `Release` 或 `Demote` 前；
- P2 重启、路由变化或对账时。

逻辑签名：`GetPlacement(input) → PlacementObservation`。

#### 1.3.2 我们发送的数据：PlacementQueryInput

| 字段 | 含义 | 是否必须 | P2 使用方式 |
|---|---|---|---|
| `representation_id` | C 的抽象调度对象标识 | 是 | 定位表示对象 |
| `provider_ref` | 已知的 Provider 稳定引用 | 条件必填 | 精确定位 Provider 对象 |
| `target_id` | 已解析的不透明目标 | 条件必填 | 在目标已解析时缩小查询范围 |
| `trace_id` | 本次查询的调用链标识 | 是 | 贯通查询和日志 |
| `request_id` | 本次业务请求标识 | 推荐 | 关联上游请求 |

`provider_ref`、`target_id` 和 `representation_id` 的对应关系不一致时，P2 必须拒绝或返回明确冲突，不能自行选择一个继续执行。

#### 1.3.3 期望 P2 返回的数据：PlacementObservation

| 字段 | 含义 | C 的使用方式 |
|---|---|---|
| `observation_id` | 本次观测唯一标识 | 去重、审计和对账 |
| `representation_id` | 被观测的抽象表示 | 校验对象一致性 |
| `provider_ref` | Provider 稳定引用 | 与 C 保存的引用核对 |
| `target_id` | 适用时返回的不透明目标 | 关联目标解析结果 |
| `provider_name` | 实际 Provider 标识 | 路由和审计 |
| `current_tier` | 当前真实层级 | 与 Plan 的目标层级比较 |
| `generation` | 当前 Provider 对象版本 | 防止旧观察覆盖新事实 |
| `route_epoch` | 当前路由或切换版本 | 判断路由是否变化 |
| `observed_at` | P2 观察时间 | 判断快照新鲜度 |
| `observation_source` | 事实来源 | 记录 P2、Executor 或 Provider |
| `supported_operations` | 当前支持的动作 | 判断动作是否可执行 |
| `readable` | 当前副本是否可读 | 判断是否可以作为读取来源 |
| `serving_ready` | 是否可作为 Serving 副本 | 判断是否可以释放高层副本 |
| `replica_count` | 当前可用副本数量 | 判断释放后是否仍有可用副本 |
| `state_version` | 状态快照版本 | 处理乱序观察 |

#### 1.3.4 示例与异常

| P2 返回情况 | C 的处理 |
|---|---|
| 对象不存在 | 检查映射是否过期；不自行创建新的物理引用 |
| `current_tier`、`generation` 或 `observed_at` 缺失 | 不能用于新的非 `Keep` 动作，重新查询或等待补齐 |
| 观察版本低于 C 已保存版本 | 丢弃旧观察，不覆盖当前事实 |
| Provider 暂时不可用 | 保留旧事实但标记过期，不提交新的非 `Keep` 动作 |

### 1.4 步骤二：读取资源状态（CP2-02）

#### 1.4.1 功能与调用时机

`GetResourceState` 用于判断 P2 当前是否有能力接收和执行新的后台动作。C 不根据自己已经提交的动作反推资源状态。

逻辑签名：`GetResourceState(input) → ResourceState`。

#### 1.4.2 我们发送的数据：ResourceStateQueryInput

| 字段 | 含义 | 是否必须 |
|---|---|---|
| `resource_scope_id` | 要查询的资源池、Provider 范围或实例标识 | 是 |
| `provider_name` | 指定或过滤 Provider | 条件必填 |
| `trace_id` | 调用链标识 | 是 |
| `request_id` | 业务请求标识 | 推荐 |

#### 1.4.3 期望 P2 返回的数据：ResourceState

| 字段 | 含义 | C 的使用方式 |
|---|---|---|
| `resource_scope_id` | 资源事实适用范围 | 确认快照是否适用 |
| `provider_name` | 资源事实来源 | 识别 Provider |
| `state_version` | 资源状态版本 | 处理乱序更新 |
| `observed_at` | 资源观察时间 | 判断新鲜度 |
| `freshness` | 资源事实是否新鲜 | 只有新鲜时允许正常准入 |
| `backend_health` | Provider 健康状态 | 判断是否允许提交 |
| `total_capacity_bytes` | 资源总量 | 计算容量使用情况 |
| `used_capacity_bytes` | 当前已用资源 | 计算剩余资源 |
| `available_capacity_bytes` | 当前可用资源 | 判断动作成本是否可容纳 |
| `pressure_ratio` | 资源压力水平 | 判断限流和释放候选 |
| `working_reserved_capacity_bytes` | 在线 Working 预留量 | 保护在线读写 |
| `prewarm_budget_bytes` | C 后台预取/预热预算上限 | 限制 C 的后台动作；不包含 Recall RP2-04 的在线读取额度 |
| `prewarm_used_bytes` | C 后台预取/预热当前占用 | 计算 C 后台动作的剩余额度 |
| `pin_budget_bytes` | Pin 预算上限 | 限制固定驻留 |
| `pin_used_bytes` | Pin 当前占用 | 计算固定驻留剩余额度 |
| `active_migration_count` | 当前迁移数量 | 判断是否还能提交迁移 |
| `max_concurrent_migration` | 迁移并发上限 | 限制迁移并发 |
| `migration_bandwidth_bytes_per_sec` | 当前迁移带宽 | 评估迁移能力 |
| `migration_bandwidth_budget_bytes_per_sec` | 后台迁移带宽上限 | 保护在线服务 |
| `read_latency_p99_ms` | 读取延迟 P99 | 判断在线读取影响 |
| `write_latency_p99_ms` | 写入延迟 P99 | 判断在线写入影响 |

#### 1.4.4 示例与异常

当 `freshness` 为 `STALE` 或 `UNKNOWN`，或者 `backend_health` 不是 `READY` 时，C 默认只允许查询、对账和 `Keep/No-op`，不提交新的 `Promote`、`Demote`、`Prefetch` 或 `Release`。

### 1.5 步骤三：解析 ActuationTarget（CP2-03）

#### 1.5.1 功能与调用时机

`ResolveActuationTarget` 把 C 的抽象 `representation_id` 和目标层级解析为可以提交给 P2 的不透明执行目标。

C 不需要知道 P2 内部使用的是 Segment、Object、Shard、Cache Key 还是物理路径。

逻辑签名：`ResolveActuationTarget(input) → ActuationTarget`。

#### 1.5.2 我们发送的数据：ActuationTargetResolveInput

| 字段 | 含义 | 是否必须 |
|---|---|---|
| `representation_id` | 要调度的抽象表示 | 是 |
| `desired_tier` | C 计划达到的目标层级 | 是 |
| `target_generation` | C 当前计划绑定的 Provider 对象版本 | 是 |
| `plan_id` | 来源 PlacementPlan | 是 |
| `trace_id` | 调用链标识 | 是 |
| `valid_until` | C 计划有效截止时间 | 推荐 |

#### 1.5.3 期望 P2 返回的数据：ActuationTarget

| 字段 | 含义 | C 的使用方式 |
|---|---|---|
| `target_id` | 不透明执行目标 | 保存并转发，不解析内容 |
| `representation_id` | 目标对应的抽象表示 | 必须与请求一致 |
| `provider_ref` | Provider 稳定引用 | 关联真实对象 |
| `provider_name` | 执行 Provider | 路由和审计 |
| `source_tier` | 解析时真实所在层级 | 与最新 Placement 对照 |
| `supported_operations` | 当前支持的动作列表 | 判断计划动作是否可执行 |
| `target_generation` | 当前目标版本 | 与计划版本核对 |
| `route_epoch` | 当前路由版本 | 判断目标是否仍有效 |
| `resolved_at` | 目标解析时间 | 判断解析结果新鲜度 |
| `valid_until` | 目标有效截止时间 | 过期后重新解析 |

#### 1.5.4 示例与异常

以下情况不能继续提交动作：

- 目标不存在；
- `representation_id`、`provider_ref` 或版本不一致；
- 映射已经过期；
- 目标不支持所需动作；
- P2 无法确认目标当前版本。

P2 应返回明确的 `error_code` 和可重试建议。C 不自行拼装 `target_id`，也不把 `provider_ref` 当成 `ActuationTarget` 使用。

### 1.6 步骤四：调用 P2 提交 TierAction（CP2-04）

#### 1.6.1 功能与调用时机

只有 Placement、ResourceState、ActuationTarget 和冲突检查全部通过后，C 才提交 `TierAction`。

逻辑签名：`SubmitTierAction(input) → SubmitResult`。

P2 接收的是 C 的抽象动作，不是 Provider 专属命令。

#### 1.6.2 我们发送的数据：SubmitTierActionInput

| 字段 | 含义 | 是否必须 | P2 校验 |
|---|---|---|---|
| `action_id` | C 生成的动作唯一标识 | 是 | 原样保存和返回 |
| `idempotency_key` | 动作幂等键 | 是 | 重复提交不得重复执行 |
| `plan_id` | 来源计划标识 | 是 | 关联决策来源 |
| `representation_id` | 调度对象标识 | 是 | 定位抽象对象 |
| `provider_ref` | Provider 稳定引用 | 是 | 校验目标对象 |
| `actuation_target` | 不透明执行目标 | 是 | 校验目标有效性 |
| `action_type` | C 选择的调度动作 | 是 | 校验是否支持 |
| `source_tier` | 动作开始前的层级 | 是 | 与真实 Placement 对照 |
| `desired_tier` | 动作目标层级 | 是 | 作为执行目标 |
| `expected_generation` | C 预期对象版本 | 是 | 不匹配时拒绝或返回冲突 |
| `policy_version` | C 使用的策略版本 | 是 | 保留审计，不重新解释 |
| `priority` | 动作优先级 | 推荐 | 排队和准入 |
| `deadline` | 动作最晚有效时间 | 推荐 | 过期动作不能盲目执行 |
| `trace_id` | 调用链标识 | 是 | 贯通执行日志 |
| `request_id` | 业务请求标识 | 推荐 | 关联上游请求 |

#### 1.6.3 期望 P2 返回的数据：SubmitResult

| 字段 | 含义 | C 的处理 |
|---|---|---|
| `action_id` | 原动作标识 | 必须与请求一致 |
| `provider_task_id` | P2 创建的执行任务标识 | 后续查询和反馈关联 |
| `provider_status` | 当前接收或执行状态 | 映射 C 的 ActionState |
| `observed_at` | P2 返回状态的时间 | 判断接收结果新鲜度 |
| `trace_id` | 调用链标识 | 贯通日志 |
| `error_code` | 接收失败原因 | 失败时必须提供 |
| `retryable` | P2 的重试建议 | C 仍须先对账，不能直接重试 |

`ACCEPTED` 或 `RUNNING` 只表示 P2 已接收或开始处理，不表示物理迁移已经成功。

#### 1.6.4 示例与异常

| P2 返回情况 | C 的处理 |
|---|---|
| 幂等键重复且原动作存在 | 返回并复用原动作事实，不产生第二次执行 |
| `expected_generation` 不匹配 | 当前 Plan 失效，重新读取 Placement 后重新计算 |
| 目标过期 | 重新解析目标，不能继续使用旧目标 |
| P2 过载或资源不足 | 按错误原因进入等待或重新计划，不伪造成功 |
| 已提交但响应丢失 | Action 进入 `Unknown`，使用 CP2-05 查询 |

### 1.7 步骤五：查询原 TierAction 状态（CP2-05）

#### 1.7.1 功能与调用时机

`QueryActionStatus` 用于反馈丢失、超时、C 重启、P2 重启和 `Unknown` 对账。

逻辑签名：`QueryActionStatus(input) → ActionStatus / ExecutionFeedback`。

#### 1.7.2 我们发送的数据：ActionStatusQueryInput

| 字段 | 含义 | 是否必须 |
|---|---|---|
| `action_id` | C 的动作标识 | 至少提供一个 |
| `provider_task_id` | P2 任务标识 | 已知时提供 |
| `idempotency_key` | 原动作幂等键 | 推荐 |
| `trace_id` | 调用链标识 | 是 |
| `request_id` | 业务请求标识 | 推荐 |

#### 1.7.3 期望 P2 返回的数据：ActionStatus

查询结果至少需要包含：

| 字段 | 含义 |
|---|---|
| `action_id` | 原动作标识 |
| `provider_task_id` | P2 任务标识 |
| `provider_status` | 当前真实执行状态 |
| `actual_tier` | 实际达到的层级，适用时提供 |
| `pre_generation` | 执行前版本 |
| `post_generation` | 执行后版本，适用时提供 |
| `route_epoch` | 执行完成时路由版本 |
| `migration_stage` | 当前物理执行阶段，适用时提供 |
| `completion_time` | P2 认为完成的时间，适用时提供 |
| `error_code` | 失败或未知原因 |
| `retryable` | 重试建议 |
| `observed_at` | 本次查询观察时间 |
| `evidence` | 支持该结果的证据引用 |

“任务存在”不能单独作为成功结果。P2 应尽可能返回动作效果、当前层级和版本，以便 C 对账。

#### 1.7.4 示例与异常

| 查询结果 | C 的处理 |
|---|---|
| 已确认达到目标，且版本和对象匹配 | 原 Action 收口为 `Succeeded` |
| 明确未执行 | 依据最新 Plan 创建新的 `action_id` |
| 明确执行失败 | Action 收口为 `Failed`，按策略判断是否重新计划 |
| 仍在执行 | 保持 `Submitted`，继续等待反馈 |
| 结果仍未知 | 创建或继续 `ReconciliationTask`，不能盲重试 |
| 普通 `not_found` | 不能直接解释为“未执行”，需根据 P2 的幂等和查询契约判断 |

### 1.8 步骤六：接收 ExecutionFeedback（CP2-06）

#### 1.8.1 功能与调用时机

P2 应支持回调、事件流或可断点续传的反馈读取能力，使 C 能获得动作进度和最终结果。

逻辑签名：`WatchExecutionFeedback(input) → ExecutionFeedback`。

#### 1.8.2 我们发送的数据：FeedbackWatchInput

| 字段 | 含义 | 是否必须 |
|---|---|---|
| `action_id` | 要监听的 C 动作 | 是 |
| `feedback_cursor` | 反馈消费位置 | 断点续传时提供 |
| `trace_id` | 调用链标识 | 是 |
| `consumer_id` | C 的反馈消费方标识 | 推荐 |

#### 1.8.3 期望 P2 返回的数据：ExecutionFeedback

| 字段 | 含义 | C 的使用方式 |
|---|---|---|
| `feedback_id` | 反馈事件唯一标识 | 去重和审计 |
| `action_id` | 关联 C 动作 | 推进对应状态机 |
| `provider_task_id` | P2 任务标识 | 反馈丢失时查询 |
| `provider_status` | P2 执行状态 | 映射 C 的 ActionState |
| `actual_tier` | 实际达到的层级 | 校验目标是否达到 |
| `pre_generation` | 执行前版本 | 与预期版本对照 |
| `post_generation` | 执行后版本 | 确认版本变化 |
| `route_epoch` | 完成时路由版本 | 校验路由切换 |
| `migration_stage` | 当前物理执行阶段 | 观察 Copy、Verify、Cutover、Reclaim 进度 |
| `completion_time` | P2 认为完成的时间 | 记录执行完成事实 |
| `error_code` | 错误原因 | 分类失败和恢复 |
| `retryable` | P2 的重试建议 | 不能替代 C 对账 |
| `observed_at` | 反馈观察时间 | 判断反馈新鲜度 |
| `next_feedback_cursor` | 下一次消费位置 | 支持断点续传 |
| `evidence` | 结果证据 | 关联审计和对账 |

#### 1.8.4 示例与结果判断

| P2 返回情况 | C 的处理 |
|---|---|
| `ACCEPTED` / `RUNNING` | Action 保持 `Submitted` |
| `SUCCEEDED` 且层级、对象和版本匹配 | 重新查询 Placement 后进入 `Succeeded` |
| `FAILED` 且明确未执行 | 进入 `Failed`，按策略判断是否重新计划 |
| 反馈丢失、超时或结果无法确认 | 进入 `Unknown`，调用 CP2-05 并对账 |

### 1.9 步骤七：物理迁移和一致性控制（CP2-07/08）

#### 1.9.1 物理迁移

P2 / Provider 将 C 的抽象动作转换为内部步骤：

```text
Copy
  -> Verify
  -> Cutover
  -> Reclaim
```

这些步骤不成为 C 的业务状态，但 P2 应通过 `migration_stage` 提供可观察进度，并在最终结果中说明：

- 是否完成复制；
- 是否完成完整性校验；
- 是否完成读取路由切换；
- 是否已经回收旧副本；
- 当前是否仍有可读副本；
- 是否发生版本或路由变化。

#### 1.9.2 Freeze、Unfreeze、Cancel

如果迁移或路由切换需要一致性保护，P2 可以提供以下能力：

| 能力 | P2 应做什么 | 需要关联的字段 |
|---|---|---|
| `Freeze` | 在切换前冻结对象、目标或相关写入 | `action_id`、`provider_ref`、`generation`、原因 |
| `Unfreeze` | 在成功或失败后恢复服务 | `action_id`、执行结果 |
| `Cancel` | 在安全阶段停止动作 | `action_id`、`provider_task_id`、取消结果 |
| 状态查询 | 返回冻结、迁移和恢复状态 | `action_id`、`provider_task_id`、`observed_at` |

P2 必须说明 `Cancel` 在哪些阶段有效，以及取消后真实 `current_tier`、`generation` 和副本状态是什么。C 不能默认所有已提交动作都可以取消。

### 1.10 C 与 Recall-P2 的边界

#### 1.10.1 RP2-04 `Prewarm` 不等于 C 的 `Prefetch`

| 项目 | Recall RP2-04 `Prewarm` | C 的 `Prefetch` |
|---|---|---|
| 发起方 | A/Recall | C |
| 触发时机 | 在线读取当前正文时，获准后尝试一次 | C 根据预测和策略生成后台计划后 |
| 主要目的 | 加速本次读取，尝试使用已有热副本 | 提前准备目标放置，服务后续访问 |
| 是否创建 C 的 `TierAction` | 否 | 是 |
| 是否触发 `Promote` / `Release` | 否 | 可以按 C 计划触发，须经 P2 校验和反馈 |
| 结果来源 | 随读取返回来源和放置观察 | `ExecutionFeedback` 加后续 `GetPlacement` |

在线读取即使命中热副本，也不能反推 C 已经执行过 `Prefetch` 或 `Promote`。在线读取不触发 `Promote / Prefetch / Release`。

#### 1.10.2 C 消费 RP2-05 的方式

RP2-05 的 `P2ReadPlacement` 是读取响应附带的观察事实，不需要第二次正文读取，也不需要新增调层控制接口。A/Recall Runtime 应将以下字段写入 `AccessTrace` 或等价观察记录：

| 字段 | 含义 | C 的处理 |
|---|---|---|
| `actual_provider` | 本次实际提供字节的 Provider | 记录实际来源 |
| `observed_tier` | 本次读取发生时的真实层级 | 作为访问观察，不直接作为当前状态 |
| `placement_generation` | 本次读取对应的放置版本 | 与动作和对账记录关联 |
| `observed_at` | Provider 观察放置事实的时间 | 判断观察是否过期 |
| `producing_action_id` | 能由证据唯一关联到该副本或读取的 C 动作 | 能确认时关联，不能按时间猜测 |
| `evidence` | 内容、表示、版本、范围和动作绑定证据 | 用于 Placement Verification 和审计 |

正文读取成功但放置证据缺失时，Recall 可以继续，归因标记为不可验证；不能把证据缺失判为读取失败，也不能把它判为 `Prefetch` miss。

## 2. C-P2 流程共用的调用、错误与状态数据

### 2.1 公共请求与响应上下文

每次 C-P2 调用和反馈至少应能关联：

| 字段 | 含义 |
|---|---|
| `request_id` | 业务请求标识 |
| `trace_id` | 技术调用链标识 |
| `memory_id` | 业务 Memory 标识，适用时提供 |
| `representation_id` | 抽象表示标识，适用时提供 |
| `provider_ref` | Provider 稳定引用，适用时提供 |
| `target_id` | P2 返回的不透明执行目标，适用时提供 |
| `plan_id` | C 计划标识，适用时提供 |
| `action_id` | C 动作标识，适用时提供 |
| `provider_task_id` | P2 任务标识，适用时提供 |
| `generation` | Provider 对象版本，不能与 Memory 版本混用 |
| `route_epoch` | Provider 路由或切换版本 |
| `occurred_at` | 业务动作发生时间 |
| `observed_at` | P2 观察或反馈时间 |
| `schema_version` | 报文结构版本 |
| `idempotency_key` | 适用时使用的幂等键 |

### 2.2 统一状态规则

```text
TierAction.Generated
  -> Submitted
  -> Succeeded
  -> Failed
  -> Unknown
```

统一规则：

- `ACCEPTED` / `RUNNING` 不等于成功；
- `SUCCEEDED` 必须结合真实 Placement 确认；
- 反馈丢失或结果不确定进入 `Unknown`；
- `Unknown` 先查询和对账，不能直接重试；
- 重试必须创建新的 `action_id`；
- P2 不重新解释 C 的 `policy_version`；
- C 不修改 `current_tier`、`generation`、`route_epoch` 或 Provider 任务状态；
- C 的 `desired_tier` 是计划目标，不是物理事实。

### 2.3 统一错误事实

P2 返回失败或未知时，至少应说明：

- `error_code`；
- 失败或未知发生在哪个阶段；
- 是否已经产生物理副作用；
- 是否仍可能在执行；
- 是否允许查询、等待、取消或重试；
- 当前可确认的层级、版本和副本状态；
- 关联的 `action_id`、`provider_task_id` 和证据。

仅返回“失败”“任务不存在”或“已接受”而没有效果说明，不能支持 C 正确收口。

## 3. 后续分支：状态闭环与异常处理

### 3.1 反馈丢失与未知恢复

```text
SubmitTierAction 已发出但响应丢失
  -> C 不重新生成幂等键
  -> 按原 action_id / idempotency_key 查询
  -> 查询 Provider 状态
  -> 查询最新 Placement
  -> 按真实结果收口
```

### 3.2 P2 重启后的恢复

P2 重启后必须仍能按 `action_id`、`provider_task_id` 或幂等键查询原动作。C 恢复时先查询和对账，不因为自身重启就重新提交相同动作。

### 3.3 版本冲突与重新计划

```text
expected_generation 不等于当前 generation
  -> 拒绝提交或 Action 进入 Unknown
  -> 重新读取 Placement
  -> 使旧 Plan 失效
  -> 根据最新事实重新计算
```

C 不能自行递增 `generation`，也不能用 `desired_tier` 覆盖 `current_tier`。

### 3.4 Release 失败或状态未知

Release 前必须确认：

- 低层 Serving / Authoritative Copy 可读；
- 目标 `generation` 和 `route_epoch` 仍有效；
- 没有有效 Pin 或 Force Keep；
- 没有冲突的 Submitted / Unknown 动作；
- 目标副本已经完成写入和完整性校验。

Release 失败、反馈丢失或状态未知时，保留原高层副本，不提前修改 `current_tier`，进入查询和 `ReconciliationTask`。

## 4. 本流程的协议映射与联调要求

1. 六个逻辑接口的实际 SDK/RPC/HTTP 方法、版本和测试环境。
2. `representation_id` 与 `provider_ref` 的稳定关系及失效规则。
3. `target_id` 是否由 P2 统一解析，以及有效期和失效规则。
4. `generation`、`mapping_generation`、`target_generation` 的区别和更新时机。
5. `route_epoch` 在 Copy、Cutover、Reclaim 哪一步发生变化。
6. `current_tier`、`actual_tier`、`desired_tier` 的返回和使用规则。
7. `GetPlacement` 和 `QueryActionStatus` 是否可以由同一个 RPC 覆盖，但是否仍保留两类事实语义。
8. `ExecutionFeedback` 的回调、事件流、查询和断点续传方式。
9. 同一 `action_id`、幂等键重复提交时的返回规则。
10. P2 过载、超时、取消、重启和 Provider Unknown 的状态语义。
11. `Freeze`、`Unfreeze`、`Cancel` 的支持范围和生效阶段。
12. P2 是否可以提供 RP2-05 的实际来源、放置版本和动作绑定证据。
13. RP2-04 `Prewarm` 与 C `Prefetch` 是否在接口层、预算和动作记录上明确分开。
14. 在线读取命中热副本时，P2 是否保证不隐式创建 C 的调度动作。

### 4.1 P2 需提供的对接材料

P2 对每项接口至少提供：

- 实际方法名称和契约版本；
- 请求和响应样例；
- 字段来源、转换规则和缺失处理；
- 错误码、超时、限流、取消和重试语义；
- 幂等键作用域和保留时间；
- `action_id`、`provider_task_id` 和目标引用的关联方式；
- 状态、版本、层级和副本事实的保证范围；
- 测试环境和可复现的异常场景；
- P2 重启后查询和恢复方式。

### 4.2 按 C-P2 顺序验收

P2 至少需要证明：

- 能返回稳定的 `provider_ref`；
- 能返回 C 无需解析的不透明 `target_id`；
- 能返回真实 `current_tier`、`generation` 和 `route_epoch`；
- 能返回完整且带时间的 `ResourceState`；
- 能按幂等键接收 `TierAction` 并避免重复执行；
- 能返回 `provider_task_id` 和接收状态；
- 能按 `action_id` 查询真实动作结果；
- 能通过反馈流或回调返回执行进度和最终结果；
- 成功结果包含实际层级、版本和路由事实；
- 失败结果包含错误原因和副作用说明；
- 反馈丢失时仍可查询和对账；
- P2 重启后动作和状态仍可查询；
- 迁移完成后能返回最新 Placement Observation；
- 能区分 RP2-04 `Prewarm` 和 C 的 `Prefetch`；
- 在线读取不会隐式生成 C 的 `TierAction`；
- 能明确 `Freeze`、`Unfreeze` 和 `Cancel` 的支持范围。

## 5. 核心结论

```text
C 提供调度意图和抽象 TierAction。
P2 提供目标解析、物理执行、真实状态和执行反馈。
C 通过 Feedback + Placement 完成最终收口。
```

C-P2 的最小稳定闭环是：

```text
GetPlacement
  -> GetResourceState
  -> ResolveActuationTarget
  -> SubmitTierAction
  -> WatchExecutionFeedback / QueryActionStatus
  -> GetPlacement
  -> Reconciliation
```
