# Recall 依赖接口与适配设计 V0.2

日期：2026-09-16。本文只定义 A 侧消费要求和原职责内的能力输出。**表中的本地方法不是要求其他组新增同名 API**；真实协议、类型及保证按原 Owner 的确认版本接入。

## 1. 接口分三层

1. **Recall 内部阶段接口**：见 [01](01_Recall内部架构与阶段设计_V0.2.md)，由我们实现，不暴露成八个远程服务。
2. **Recall 消费依赖的 Port**：隔离授权、B 事实、P2 搜索/读取和 SRF 机制。我们写消费逻辑、映射和契约测试，不重建依赖方的主事实或基础设施。
3. **A 已有职责内的共享能力**：Embedding、向量投影机制。面向 B 的交接与在线 Query 分开，B 的编排和 Ready 判定不进入 Recall Runtime。

原 `RIF-01～14` 保留为本目录索引，RB/RA/RC/RP2 保留原能力编号。不同编号不要求对应不同方法、表或微服务。

每个适配配置记录外部契约版本、本地映射版本、字段/证据来源、支持范围与原始脱敏样例。未知字段不以期望值回填；外部原始状态和错误作为证据保留。缺少安全必需字段时隔离相应路径，不能强行转成成功 DTO。

## 2. A 与 B 的对接映射

依据：[A 的 B 对接需求](../Recall流程/运行时详细设计_V0.1/召回与记忆形成接口对接需求_V0.1.md)、[B 的 A 对接需求](../remeber流程/运行时详细设计_V0.1/记忆形成与召回接口对接需求_V0.1.md)。下表是对照，不修改任何一方原编号或定义。

| 我们的接口索引 | A 侧编号 | B 侧编号 | 方向 | 我们实现什么 |
|---|---|---|---|---|
| RIF-07 | RB-01 | RA-01 | B → A | Working 有界读取的请求与响应校验 |
| RIF-09 | RB-02、RB-05 | RA-02 | B → A | 初次/最终/重放事实复核，长期 Ready 与空间核验 |
| RIF-10 | RB-03 | RA-03 | B → A/内容 Provider | 消费正式映射；加载并验证真实字节 |
| RIF-11 | RB-04 | RA-04 | B → A | 消费语义/冲突事实，执行 Recall 排序与原子组 |
| RIF-13 的失效消费 | RB-06 | RA-05 | B → A | 去重接收、清理 A 派生内容、输出处理范围证据 |
| RIF-06 | RB-08 | RA-06 | A → B | Query/Passage 共享真实推理及输入/空间绑定 |
| RIF-14 | RB-09 | RA-07 | A → B | 向量投影机制的逐项结果、查询/删除证据 |
| RIF-13 的观察输出 | RB-07 | RA-08 | A → RF；B 消费待批准 | 输出真实阶段事实，不自行开 B 订阅 |

### 2.1 WorkingRead：RIF-07

本地逻辑操作：`read_working(授权范围, 当前session/task, 筛选, limit, 字节上限, 调用预算)`。输入身份取自原已受理请求及当前授权结果，不能从客户端随意拼接。

消费输出包括：稳定候选引用、Memory/版本、来源内顺序、B 的当前性与有效期证据、内联正文或合法内容引用、范围完成证据、外部契约版本。字段类型和具体编码优先映射 B 的 [WorkingReadResult](../remeber流程/运行时详细设计_V0.1/记忆形成数据定义_V0.1.md#working)，不复制 B 的存储结构。

| 外部事实 | Recall 本地处理 |
|---|---|
| 有界完整且零条 | SourceReadResult.complete，允许后续判断正常空 |
| partial，即使零条 | 保留已核实候选及缺口，不补成 complete |
| 超时/服务不可用/无法证明完整 | 来源 unavailable 或对应 partial；不说“没有记忆” |
| Redis miss/缓存 TTL 到期 | 不推断领域过期；按 B 的返回事实处理 |
| B 已支持可靠来源回退 | 校验其证据并记录实际路径；不伪记 Redis 命中 |
| 内联正文超限 | 传输/解码边界就受控拒绝，记录预算事实；不先无限读取再截断 |

B 新设计区分业务有效期、Working 用途期限和缓存 TTL。我们消费 B 的资格结论与时间证据，不把三个字段折叠成一个本地过期时间自行决定领域生命周期。关联 `A:AL-B01/B03`；B 的本地 W1 方案不构成 A 可以直读其状态库的授权。

### 2.2 MemoryRead 与语义：RIF-09/11

本地逻辑操作：`validate_candidates(准确候选集合, purpose, 当前授权, 预算)`；purpose 区分初查、发出前、重放前。同一个外部批量接口可满足多个时点，不能因为分成三个本地操作就要求 B 新开三个接口。

输入必须保留原候选身份、发现版本、表示、来源引用及需要的内容范围。输出逐项包含：当前事实及其版本、资格结论、理由、证据和有效窗口。A 将有依据的结果映射为 `accepted/excluded/unverifiable`；批量漏项按 unverifiable，不能用上次 accepted 补齐。

初查通过不是最终交付许可；最终复核包括替补。候选级权限未知为局部缺口，请求级授权不能确认阻断整包。Projection 非 Ready 只用于长期资格，不否定有效 Working。访问和复核不回写 B 的 Memory 或 ProjectionState。

语义消费另有逻辑操作 `resolve_semantics(已验证候选, 评价时间/所需事实, 策略引用)`：需要 B 的排序依据、衰减规则版本、冲突完整成员、必要提示及获准回退。A 不要求外部必须返回乘法权重，也不自创默认 confidence。缺成员则冲突组隔离；仅 B 批准的回退可进入带缺口的组装。关联 `A:AL-B03/B04/B05`、`B:AL-A03`。

### 2.3 Canonical 映射与失效：RIF-10/13

本地逻辑操作 `resolve_content(已通过候选, 获准表示/范围, 当前授权)` 消费 B 的正式映射：精确对象/内容版本、表示、范围、编码、权威期望摘要与来源证明。实际读取交相应 Provider；A 不自行选择新的 Artifact 或扩读正文。

本地失效消费入口接收已验证的 Memory/版本/状态修订、失效范围、tombstone、事件身份和证据。先可靠保存去重与清理意图，再执行 A 自有派生内容清理；“接收确认”和“处理完成证据”分开。证据说明已处理范围、剩余项和错误，不把尚未清理说成完成。重复通知不重复执行，乱序删除/撤权不能被普通更新吞掉。

清理范围包括 A 管理的派生缓存、正文/Context 保留及相关受控引用；最小幂等/审计保留按已确认规则。收到清理通知不取消当前访问复核；反之清理尚未完成也不能让已知失效正文继续输出。无法证明处理顺序时按准确对象向权威方补查，不按不透明版本字符串排序。关联 `A:AL-B02/B06`、`B:AL-A03`。

## 3. P2/内容 Provider 的消费接口

复用已有 [P2 DTO](../../../AgentJYS-main/src/aether_agent_memory/p2/contracts.py)。实际协议映射依据 [A 最新 P2 对接文档](../Recall流程/运行时详细设计_V0.1/召回与P2接口对接需求_V0.1.md)第一章；DTO 的字段不代表 proto 已支持。

| RIF / 能力 | 输入 | 必须核实的输出 | 当前协议差距与 A 行为 |
|---|---|---|---|
| RIF-08 / RP2-01、VEC-002 | 已验证 Query 向量/空间、授权范围/筛选、TopK、期限 | 稳定引用、来源 rank、模型/空间关联、完成边界与证据 | 文档核对的 SearchVector 请求无 scope/filter；未证明隔离/筛选前不启用该真实路径 |
| RIF-10 / RP2-02、OBJ-001 | B 批准的对象版本/范围、期望摘要、接收上限、期限 | 实际 bytes、长度、范围/版本依据、元数据、完成/错误 | 既有整对象 get 不能自动替代有界精确读取；不能下载全库/全文再过滤补保证 |
| RIF-10 / RP2-03 | 必要时按准确目标查询元数据 | 同版本对象的 size/checksum 等 | head 是按需步骤；整对象摘要不是片段摘要，不因多一次 head 就认定读取一致 |
| RIF-10/13 / RP2-04/05 | 获准热副本读取或读取附带观察 | 实际 Provider/副本/generation/回退事实 | 可选；缺少时关闭归因或保持未知，不由目标层级猜实际路径 |

正文范围转换以原公共 ByteRange 和提供方协议为准。当前文档明确本地半开区间 `[start,end)` 映射到 proto 含上界 `end-1`；先验证范围合法、非空、获批，不能省略 end 扩读到 EOF。另做边界契约测试，避免双重转换。

对 `P2ContentReadResult` 的 data 重算实际长度与摘要，按确认的范围/版本证明核验；不能把请求字段回显当成响应证明。etag 不默认等于内容 hash；编码不从正文“猜对”就视为批准。not_found 是物理观察，不代表 B 已删除 Memory。

SDK、Adapter 与 Recall 读调用重试需指定一个主要 Owner；不能让各层各自自动重试。所有真实接收和排队都受原预算与实例限制；取消等待不代表后端工作/资源已停止。关联 `A:AL-P202/P203/P204/RF04`。

## 4. RF/SRF 机制的使用边界

沿用已有 `RecallExecutionStorePort` 的 `find/get/admit/claim/save_checkpoint/advance`。A 定义业务条件与结果分类；RF 提供对应原子性、租约和持久化保证。SRF 新规范增加了公共表达依据，不等于现有存储方法已经满足全部要求。

以下是**需在 A 本地 Port 表达的语义草案**，不规定 RF 数据库或外部方法名：

| 本地操作 | 输入与结果 | 核心条件 |
|---|---|---|
| 保存阶段输出 | ExecutionGuard、检查点、输出、已发生观察/Outbox 引用 → 新执行版本 | 输出、检查点及事件关联可靠一致；重复同身份同摘要可查询，异摘要冲突 |
| 预留调用与记账 | guard、稳定调用身份、类型、次数/字节/期限 → 预留证明；回填实际观察 | 调用前原子占用；重启不重置；未知接收量按原规则保守扣费但不伪造真实流量 |
| 固定最终准备稿 | guard、commit_id/generation、拟议包/Trace/事件及摘要 → 准备证明 | 不能覆盖异义准备稿；与原请求/证据绑定 |
| 提交最终结果 | 原提交身份、guard、准备稿/证据有效截止 → 提交证明/未知 | 唯一结果；原子保存 Pack、终态、Trace、Outbox；存储侧校验期限与执行权 |
| 查询最终提交 | tenant/recall_id/commit_id → 已提交事实或未决事实 | 普通 not_found 不证明无迟到写；结果未知不发布终态 |
| 隔离并失败收尾 | 原身份/generation、超期/过期依据及无正文失败稿 → 原结果或隔离提交证明 | 原子检查是否已有结果；否则隔离旧稿并提交失败结果，迟到旧提交不得生效 |
| 投递/确认 | 固定 event_id、编码版本/摘要 → 摄取确认或未确认 | 同 ID 异载荷拒绝；Ack 必须有该契约认可的可靠摄取证据 |
| 清理与恢复挂接 | A 目标/期限/比较规则、进度与证据引用 → 可恢复记录 | 只处理 A 自有对象；公共 Task 完成不自动修改 Recall 终态 |

本地存储业务适配可以使用 RF 的既有原子记录能力组合这些语义，但生产是否可表达所有原子条件必须验证。不能新增一套 A 私有数据库来绕开尚未确认的保证。逻辑对象也不意味着各建一张表。

SRF 的公共对象目录、关联查询及 External Integration 的外部证据能力是依赖；我们只登记/提供 Recall 的对象、关系、业务 Guard 和查询事实，不重做它们的公共系统。关联 `A:AL-RF01～05`。

## 5. 访问观察与 C 的对接

采用 [Recall 本地观察](../Recall流程/运行时详细设计_V0.1/召回流程详细设计_V0.1.md)第七章。下面是 **A 侧候选映射**，真实发送需使用确认的共享 Schema，不能把三列枚举混发。

| Recall 本地事实 | SRF 规范对应阶段 | C 输入交付物中的阶段名 | A 侧采用条件 |
|---|---|---|---|
| SearchHit | retrieved | search_hit | 实际发现稳定候选；不证明有效 |
| CandidateAccepted | validated | candidate_accepted | 初次资格核验通过；保留当时证据 |
| CanonicalLoaded | loaded | content_loaded | 获批正文/可信副本加载并校验成功 |
| WorkingLoaded | loaded | content_loaded | 内联 Working 已校验；source 标识内联，不能虚构外部读或 Provider |
| ContextSelected | selected | context_selected | Recall 原定义为最终复核/预算后待提交条目；与 SRF 广义中途选择不完全相同 |
| ContextEmitted | used_in_context | context_emitted | 实际交给传输层；不从终态反推 |
| RecallCompleted | 无逐 Memory 访问阶段 | 不强塞上述枚举 | 请求级完成及来源汇总；零候选不能虚构 memory_id |

SRF 允许只记录真实发生的阶段。A 保留上述本地严格时点；适配需要同时保留阶段来源/语义版本。尤其 ContextSelected 的时点差异及 Working 内联缺少外部 Provider 的情形，不能靠改字符串消除，需 `A:AL-RF03/C03` 确认对应报文。

共享 envelope 的 event_id、producer/authority、owner_flow、tenant/scope、subject/version、request/trace、时间、correlation/evidence 等，分别从 A 的已保存事实与可信上下文映射；字段必填性和外部编码沿用 SRF 契约。Memory 版本、执行 state_version、Provider generation、route_epoch 各自保存，不互换。

事件与投递分开：本地事件不可变；Outbox 保留本地摘要与外部编码摘要；RF 摄取确认、每消费者 Delivery 状态、C 业务消费确认是不同事实。C 的消费状态只消费其确认，不由 A 在发出事件后推定，也不等待 C 的调度动作成功才能返回 Context。

`ContextEmitted` 之后还需实际调用方证据才能说明 received 或 used_in_model_request；未知保留 null。重复补证使用原 delivery 身份，真正再次交付才是新 delivery。普通日志和采样 OTel 不能替代可靠业务事件；日志不复制 Query/正文/向量/Context 或认证信息。

Prewarm 初期默认关闭。以后只消费合法表示的同版本热副本；miss/释放竞态按已确认正式回退处理。只有动作、准确副本/版本和物理证据能关联时才填写 action_id，不根据 C 的 Submitted 或 Succeeded 标签单独推断本次使用。C 的状态机、热度权重、Redis 策略均不在本次设计范围。

B 的 RA-08 是待批准消费需求；本轮不新增 B 订阅或 A→B 私有 Trace 通道。关联 `A:AL-B07/P402`。

## 6. 原职责内共享 Embedding 与向量机制

### 6.1 RIF-06：保留现有共享能力

复用 [embedding/models.py](../../../AgentJYS-main/src/aether_agent_memory/recall/embedding/models.py)、[service.py](../../../AgentJYS-main/src/aether_agent_memory/recall/embedding/service.py)、[native.py](../../../AgentJYS-main/src/aether_agent_memory/recall/embedding/native.py)。已有逻辑入口 `embed(request)`、`vector_for(request,result)`，输入解析用 `EmbeddingInputPort`，推理由 BackendPort 执行。

请求固定 caller/caller_request、usage、input_ref/source_hash、input_binding、model_binding、deadline 和执行策略。模型绑定包括模型/版本、维度、dtype、Schema、预处理版本、检索空间及契约引用。结果绑定原输入、向量摘要/引用和验证证据；同维度不等于同空间。

Query 由 A 解析已受理问题；Passage 消费 B 批准的文本/范围与完整输入身份，不由 A 重新选正文或重新分块。B 新设计描述其固定 manifest，而分块实现归属仍列为 `B:AL-A01`；A 当前只接收稳定片段，不据此改变分工。

共享服务统一实际推理重试；上层超时后附着同执行，不能换 caller_request 重开额度。实际 CPU 调用被取消等待后可能仍占槽位。Query/Passage 的资源隔离按部署 Profile 验证，独立实例本身不证明跨进程 CPU/内存隔离。

既有 ONNX BGE 中文 512 维结果可复用为真实推理证据；OpenVINO/IPEX、生产吞吐和故障恢复不得据此宣称通过。适配契约关联 `A:AL-B05/B08/RF05`、`B:AL-A01`。

### 6.2 RIF-14：与 Recall 在线读取分开

已有 `VectorProjectionPort.submit(request)` 和 `query(tenant_id,caller_ref,authorization_ref,operation_id)`；delete 用 operation_kind 表达。当前只有模型/Port，完整机制协调器仍待开发。

B 提供固定 manifest、稳定 item/chunk 身份、完整输入指纹、批准向量和目标。我们逐项绑定物理目标与请求，返回 `ACCEPTED/PENDING/READY/FAILED/UNKNOWN`、operation、目标/版本、Provider 和证据；不靠 HTTP 批次成功或总数量相等宣布全批 READY。A 不写 B 的 ProjectionState。

输入身份不仅是 memory/chunk/memory_version/model_version/projection_schema_version 五元组，还需对应 source/Artifact 版本与范围/hash、分块/预处理、空间/维度、metadata 和目标。缺项、不同 manifest 的成功结果不能拼为同一成功集合。外部 Processing 等状态只能依据明确版本映射，不自动改成本地 READY。

**同五元组输入变化与重建仍有未解决边界。** B 新设计要求区别 Artifact/分块改变；A 原设计明确同版本 rebuild 暂不支持。我们检测冲突、保留原操作、拒绝未支持重建；不换 task/attempt/幂等键绕过旧写或删除屏障，不替双方签发新代际。

副作用 UNKNOWN 先查原操作和准确目标。普通 not_found 不授权重发；删除需要索引退出及防旧写复活证据。A 的机制 READY 只供 B 做自己的当前版本与完整集合核验。上述差异关联 `A:AL-B08/P206/P207/RF05`、`B:AL-A02`，详见 [04](04_本轮修订依据与对接差异_V0.2.md)。
