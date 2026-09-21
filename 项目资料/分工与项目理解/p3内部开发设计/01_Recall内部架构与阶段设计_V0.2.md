# Recall 内部架构与阶段设计 V0.2

日期：2026-09-16。范围：Recall 内部；依赖文档不构成我们修改其他流程的授权。

## 1. 设计依据和交付边界

沿用 [Recall 主设计](../Recall流程/运行时详细设计_V0.1/召回流程详细设计_V0.1.md)第三、四、七章和 [数据定义](../Recall流程/运行时详细设计_V0.1/召回数据定义_V0.1.md)。受理成功只返回执行引用；只有唯一最终提交确认后，才允许发布四种业务终态及相应 Context。

我们实现请求校验与选路、执行编排、候选消费与核验、正文完整性检查、排序与预算、最终复核、可靠收尾及真实访问观察。共享 Embedding 和向量投影机制仍在原 A 工作范围内，但不是在线 Recall 每次执行必须经过的写入步骤。

Memory 主事实、当前性、内容映射、冲突语义与领域 Projection Ready 继续以 B 提供的事实为准；调度计划和动作以 C 为准；公共持久化机制使用 RF/SRF；实际搜索、字节和物理效果使用相应 Provider。这里不为这些依赖设计内部实现。

## 2. Recall 目标目录

以下为**目标结构**，不是宣称文件均已存在。保留已有模块，按开发批次增加处理器，不为整齐而一次性搬迁源码。

```text
src/aether_agent_memory/recall/
  models.py                 # 沿用 Recall 领域记录和十三态
  admission.py              # 输入规范化、授权、原策略绑定、幂等受理
  routing.py                # scope_union_v1 纯规则
  ports.py                  # 已有执行存储/准入/重放边界
  store.py                  # 对 RF 原子记录能力的业务适配，不是数据库驱动
  execution.py              # 合法迁移、ExecutionGuard、唯一推进入口
  service.py                # 拟新增：受理/驱动/查询的应用编排
  stage_contracts.py        # 拟新增：本地阶段输入、输出引用及处理决定
  stages/                   # 拟新增：八阶段处理器
    request_validation.py
    query_embedding.py
    discovery.py
    candidate_validation.py
    content_load.py
    ranking.py
    context_assembly.py
    finalization.py
  query.py                  # 迁移现有实验逻辑；兼容入口只能委托新编排
  embedding_input.py        # 已受理 Query 的可信输入解析
  dependencies/             # 拟新增：A 消费依赖的 Protocol 和版本映射
    remember.py             # Working/事实复核/映射/语义/失效的本地端口
    provider.py             # 搜索和正文读取；复用现有 P2 DTO
    runtime.py              # 账本、最终提交、Outbox 等 A 所需语义
    observations.py         # Recall 本地观察到共享契约的映射
  embedding/                # 已有共享 Query/Passage 真实推理
  vector_projection/        # 已有模型/Port；机制协调器另批完成
```

`service.py`、`stage_contracts.py`、`stages/`、`dependencies/` 是本轮拟定的 Recall 本地命名，实施时按原模型复用。生产外部适配器和装配位置遵循既定集成约定，不通过本目录给其他组新分配目录或任务。

依赖方向：应用编排 → 阶段处理器 → 本地 Port → 注入的适配能力。处理器不直接读取 B 的 Redis/表，不导入 B/C 服务实现。新路径不依赖 B1/B2/B3；不在本轮删除旧兼容路径。共享 Embedding 继续由 `recall/embedding/` 提供，不回调 B1。

## 3. 唯一执行入口和内部交接

已有受理入口 `RecallAdmissionService.admit(RecallInput)` 保留。对外请求 DTO 与内部已解析的 RecallRequest 分开；客户端不能提交检索模式。内部实验输入中可出现的模型相关校验字段，不自动变成开放 API 参数；实际模型空间、模板、tokenizer、参数版本仍由服务端绑定。

目标应用边界如下，名称为本地设计草案，不是外部 HTTP/RPC 声明：

| 操作 | 输入 → 输出 | 规则 |
|---|---|---|
| 受理 | 调用输入 → 执行引用 | 原子创建或附着原绑定；同键异义冲突；未知写先查询 |
| 驱动当前阶段 | tenant/recall_id、有效执行权 → 进度或已确认结果引用 | 读取原请求/策略；不得建立第二条业务执行 |
| 查询进度 | 当前授权、执行引用 → 进度/终态元数据 | 查询不授予正文访问权，不触发新检索 |
| 获取/重放正文 | 当前授权、原结果引用 → 原不可变结果或明确拒绝 | 独立只读复核；尚未实现时不开放正文重放 |

阶段处理器统一逻辑签名：`run(StageContext) -> StageOutcome`。两个类型是拟新增本地值对象，只包装既有记录引用，不替换领域记录：

| 对象 | 必需内容 | 约束 |
|---|---|---|
| StageContext | 原 request/execution/policy 引用；当前 ExecutionGuard；前置检查点引用；预算和依赖能力 | 从可信存储加载；不能注入新策略/范围或延长 deadline |
| StageOutcome | 当前 stage；产物引用及摘要；阶段观察；诊断/缺口变化；继续、跳过或进入收尾的决定 | 不直接包含“已发布成功”；不自行修改执行状态 |
| RecallCheckpoint | 沿用原数据字典的身份、阶段、输入/输出摘要、策略与结果引用 | 不是普通日志；关联输出必须可读且可核验 |

处理器负责调用、校验和构造产物；执行协调者负责检查点与推进。外部调用前需通过账本 Port 保存意图并占用额度，不能只在内存中计数。阶段输出和已发生观察的 Outbox 关联要可靠保存；所需事务能力由 RF 接口适配，不能拆成不受保护的多次普通写入。

统一顺序：读取原绑定 → 核验执行权和前置证据 → 预留调用预算/登记意图 → 执行和校验 → 保存产物/检查点/阶段事实 → 使用返回的新状态版本推进。检查点已保存但推进回包丢失时，先读当前执行；相符则采用既有事实，不重复调用。保存未知也先查询原身份。失租或旧版本只能停止写回，不推断外部执行已经停止。

## 4. 八阶段输入输出

保留 `CREATED`、八个 `RUNNING_*` 与四终态，共十三态。以下是正常路径；合法提前收尾和空集捷径仍沿用原状态机，不要求执行没有必要的外部调用。

| 状态/本地处理器 | 输入 | 持久产物与出口条件 |
|---|---|---|
| RUNNING_REQUEST_VALIDATION | 原请求、原策略、当前身份/授权证据 | 复核结果和检查点；原范围及绑定成立才进 Query；请求级拒绝/未知转可靠收尾 |
| RUNNING_QUERY_EMBEDDING | 原模式、Query 输入摘要、已绑定模型、剩余额度 | 成功才有 QueryEmbeddingResult；Working-only 记录跳过；combined 失败记长期缺口后按预算继续 |
| RUNNING_VECTOR_SEARCH | 原 SourceSelection、可信 Query 或跳过证据 | 每来源 SourceReadResult；可信搜索响应才有 VectorCandidateSet；保留原引用及完成范围 |
| RUNNING_CANDIDATE_VALIDATION | 有界原始候选及来源证据 | CandidateValidationResult：accepted/excluded/unverifiable；仅 accepted 进入加载 |
| RUNNING_CANONICAL_LOAD | 合格候选、B 批准映射、原字节/调用预算 | ContentLoadResult 与可用 RecallCandidate；精确版本/范围/摘要通过；保留实际路径证据 |
| RUNNING_RANKING | 已核验正文、来源排名、B 语义/冲突事实、原排序策略 | RankedRecallCandidates；去重与稳定排序；无未经验证候选 |
| RUNNING_CONTEXT_ASSEMBLY | 排序候选、模板/tokenizer、预算、B 最终复核 | FinalValidationBatch、RecallBudgetGroup、待提交 ContextPack；含替补的最终集合复核并重新计数 |
| RUNNING_TRACE_FINALIZATION | 上述证据、拟议结果、固定提交身份 | 唯一最终结果、终态、最小 Trace、已发生事实的 Outbox 一致提交；确认后允许交付 |

`RUNNING_VECTOR_SEARCH` 是历史阶段名，Working-only 仍经过候选发现，但不调用向量搜索。Query 阶段结束后才进入发现；combined 的 Working 与 Vector 仅在发现阶段内并行，不把 Working 提前到 Query 阶段。

提前收尾由收尾协调者补齐缺少的两份来源记录：未请求来源为 `not_requested`，必需却未调用来源为 `unavailable` 且 `read_attempted=false`，可能已发出调用则如实记 attempted。不得伪造搜索空集或发现检查点；已完成的来源事实不因后续失败重写。

## 5. 各阶段的具体规则

### 5.1 受理与 Query

`scope_union_v1` 仅使用已确认范围、逐来源授权和类型。非法显式 session/task、空 Query、显式空类型列表、非法时间筛选、缺少显式 token 预算、客户端指定模式均拒绝。没有 session/task 时，符合范围/类型/授权的长期请求合法。

重试先查原绑定，以原策略解释输入并重验当前权限；固定原模式、预算、deadline、模型空间和提交身份，不按新配置改路由。准入判断与新绑定/额度预留同处原子边界；已存在执行不重复预留。

首次 Query 调用先持久绑定共享执行身份。Recall 只附着或补取同一个共享执行；模型实际重试归 Embedding 服务。working_only 不创建假 QueryEmbeddingResult；long_term_only Query 失败转失败收尾；combined 保持模式不变，记录长期缺失，安全和预算允许时继续 Working。请求级授权失败不能降级绕过。

### 5.2 候选与正文

每来源独立报告 `complete/partial/unavailable/not_requested`，`complete` 必须有本次范围、筛选、limit 的完成证据。TopK 搜索完成不是全库扫描完成；空列表不是完成证据。

B 确认已删除/越权/过期/旧版等为已知排除；查询失败、必要版本/资格未知为缺口。旧向量命中不能偷偷替换成新正文。Working 不要求长期 Projection Ready；长期需要 B 的当前可读事实以及 Query/Passage 空间兼容证据。

内联 Working 也经过正文校验，避免重复下载但不能省略预算和最终复核。可靠正文回退只消费 B 已支持的能力；A 不自行从 B 的事务库补读。缓存 TTL/miss 不证明 Memory 已删除或过期。

正文读取先固定 B 认可的表示、准确版本、范围、编码及权威期望摘要，再执行有界接收。整对象 checksum 不能验证任意子范围；`head(v1)+get(v2)` 不能拼成合法证据。读失败/断流的已接收字节仍按账本记费。Prewarm 仅是同一获批内容的可选路径，不是第三种来源。

### 5.3 排序与组装

按原 `identity_key` 合并同一已验证内容，保留全部来源。代表候选沿用 Working 优先、有效 rank、candidate_id、content_result_ref 的确定性规则；正文与实际加载证据作为一组保留。来源内去重重排后按原策略做 RRF，不混比 Working 和 Vector 的原始分数。

B 的语义/衰减/冲突事实缺失时，只能用其明确批准的回退并记录缺口；无批准则隔离受影响候选，不编造默认质量或“无冲突”。必须成组的冲突及提示整体保留或移除。

按固定模板渲染整包后精确计 token，包含引用、提示和分隔符。装不下的完整组跳过，继续尝试后面较小组；不硬截正文，不预设 Working/长期固定配额。

最终复核冻结**全部已加载且可能组装的候选与替补**，按 B 批量上限拆分并逐项留证。只做原流程规定的一轮复核，重试仍受原额度；缺项、未知、过期证据不能用初查补齐。仅在通过集合内重建冲突组、计算 eligible_count，再重渲染得到 packed_count。此时不追加搜索或新版本下载。

### 5.4 收尾、返回与恢复

终态计算是可测试的纯规则：先检查不变量和请求安全；有安全组时按必需来源及缺口判断完整/降级；零合格组且来源完整、无缺口才是正常空；有合格组却全部装不下为预算失败。详细优先序沿用主设计 4.1，不新增第五种业务终态。

| 业务终态 | 采用条件（均以可靠最终提交确认为发布前提） |
|---|---|
| COMPLETE_AVAILABLE | 有安全内容，必需来源完整，无未解决缺口 |
| COMPLETE_EMPTY | 没有合格内容，必需来源完整，权威事实/正常规则足以确认空，无未解决缺口 |
| DEGRADED_AVAILABLE | 有安全内容，但仍有必需来源、内容、语义或必要解释缺口 |
| FAILED_UNAVAILABLE | 请求安全失败、不变量失败、全部合格组装不下，或不能构成可信 Context |

先判断安全/不变量，再判断内容数量和缺口；不能仅凭非空 Pack 选择可用状态。提交未确认时，上表只是拟议结果，不向调用方发布业务终态。

最终提交沿用固定 commit_id、generation、准备稿摘要和租约/版本约束，原子保存结果、终态、Trace、Outbox。可用/正常空提交还需存储侧检验原业务期限与最终证据有效截止。普通 `advance` 不能代替最终提交。

未知提交先查同一 commit；普通 `not_found` 不证明迟到提交不会生效。超过业务期限后仅按原 P-22/P-23/P-24 做查证或原子隔离旧提交并失败收尾，不再调用推理/搜索/正文/B 复核。隔离不能确认时保持未终态；额度耗尽标 attention_required，不伪造失败，也不启动第二执行。

提交成功不等于已经发出。交给传输层前检查本地交付期限和证据截止，超期不发正文，保留原终态并走重放复核语义。`ContextEmitted` 只能在实际交给传输层后追加；崩溃留下交付证据缺口时不能反推“已经使用”。

终态正文重放需独立接入预算、当前权限/B 事实复核和仍可读取的原快照；不刷新原业务 deadline，不重新检索拼接旧结果。P-16=0 或正文已清除时拒绝正文重放。

## 6. 与 SRF 对接而不替换领域模型

SRF 状态目录中的 `Running/Completed/Degraded/Failed` 是不同粒度的共享描述，不能直接替换 Recall 十三态。我们保留原 state、state_version、lease 和证据；若需要共享视图，另做带来源和映射版本的只读投影。

投影草案：运行阶段对应运行中；两个 COMPLETE 终态可汇总为 Completed，但保留 available/empty；DEGRADED_AVAILABLE 和 FAILED_UNAVAILABLE 分别汇总为 Degraded/Failed。CREATED、共享 Cancelled、ContextPack 的 Building/Emitted/Degraded 等不作无条件字符串转换：它们涉及受理、业务结果与交付不同维度，确认前返回原事实，不伪造对应状态。

SRF 的 Task/Delivery/Cleanup 状态不写回 RecallExecution。我们向公共恢复能力提供 Recall 的查询、比较、隔离与收尾规则；公共机制是否有对应方法及原子保证由原 Owner 确认。所需接口见 [02](02_Recall依赖接口与适配设计_V0.2.md)。
