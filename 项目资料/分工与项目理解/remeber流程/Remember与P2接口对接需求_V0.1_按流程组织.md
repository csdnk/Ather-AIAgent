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
