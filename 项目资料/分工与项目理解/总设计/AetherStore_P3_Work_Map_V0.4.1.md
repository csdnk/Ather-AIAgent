# AetherStore P3 Architecture Freeze V0.4.1

> 状态：**ARCHITECTURE MODEL FROZEN**
> **READY FOR WBS AFTER CONSISTENCY CHECK**

> 本文件为冻结主文档。配套冻结文件：
> - `P2_CAPABILITY_REQUIREMENT_REGISTER_V0.4.1.md`
> - `P3_EXTERNAL_DEPENDENCY_AND_MOCK_STRATEGY_V0.4.1.md`
> - `FREEZE_CONSISTENCY_CHECK.md`
>
> 三份冻结文件的 REQ ID / Port Name / Domain Type / State Name / Mock Name / Owner / MUST-SHOULD 分类已逐项核对一致（见 `FREEZE_CONSISTENCY_CHECK.md`）。

---

## 0. 冻结基线（不再改）

| 内容 | 状态 |
|---|---|
| 三条 Value Stream：Remember / Recall / Operate | ✅ 冻结 |
| 主责任到角色层：A=Recall、B=Remember、C=Operate/Optimize | ✅ 冻结 |
| Recall = A 单一 E2E DRI（"召回错了找 A"） | ✅ 冻结 |
| B1/B2/B3 → Technical Capability / Stewardship（非 Primary Ownership） | ✅ 冻结 |
| P2 Consumer Requirement 方法（P3 defines what，P2 decides how） | ✅ 冻结 |
| Runtime MUST / Integrated MUST / Stage SHOULD 三层分类 | ✅ 冻结 |
| Adapter + Mock 双路径（业务代码不感知后端，禁止 `if mock`） | ✅ 冻结 |
| 三 Lane 验收（Mock ≠ 真实集成） | ✅ 冻结 |
| Semantic Decision / Observed Storage / Physical Actuation 分离 | ✅ 冻结 |
| ActuationTarget 抽象（P3 不绑定 Segment/Object） | ✅ 冻结 |
| Representation Placement Policy（逐 representation 独立分层） | ✅ 冻结 |
| P3 Internal Capability Contract Map | ✅ 冻结 |
| Unified Storage Control Simulator（共享 SimulatedStorageState） | ✅ 冻结 |

**仍不拍**：Person A/B/C 对应具体员工、Runtime Foundation Steward / Integration Steward 具体人选。

---

## 1. 三条 Value Stream 与主责任（冻结）

```
B → Remember / Memory Formation（写路径）
    Fact Formation → Classification → Consolidation → Memoryize → Make Recallable
    （Semantic Processing + Projection Build → Projection Ready）

A → Recall / Context Serving（读路径）
    Query Embedding → Candidate Retrieval → Canonical Load → Rank/Decay/Conflict → Context Pack

C → Operate / Optimize
    决策 → 聚合 → TierAction → Feedback → 对账 → 预测
```

- B1（Embedding 实现）/ B2（Memory 存储机制）/ B3（预测与层级机制）= Technical Capability，回答"怎么造"，不回答"谁对 Outcome 负责"。
- Semantic Compute（Embedding）是 Shared Capability（L3），写路径 passage embedding 与读路径 query embedding 共用。

---

## 2. Port 清单（9 个，冻结）

| # | Port | REQ | Port Owner |
|---|---|---|---|
| 1 | VectorProjectionPort | VEC-001 | A |
| 2 | VectorSearchPort | VEC-002 | A |
| 3 | ContentStorePort | OBJ-001 | B |
| 4 | ActuationTargetResolvePort | ACT-001 | C |
| 5 | SegmentIntrospectionPort | SEG-001 | C |
| 6 | PlacementStatePort | PLC-001 | C |
| 7 | TierActionExecutorPort | TIER-001 | C |
| 8 | ActionFeedbackPort | TIER-002 / TIER-003 | C |
| 9 | BackendHealthPort | HLT-001 | C |

---

## 3. Domain Objects（冻结）

### 3.1 ActuationTarget（统一版）

```
ActuationTarget {
  target_ref
  representation_type
  current_tier
  generation
  supported_operations
  target_type?   // diagnostic only
}
```

**P3 policy 禁止根据 provider-specific `target_type` 分支**；`target_type` 仅用于诊断/日志，不进入决策逻辑。

### 3.2 RepresentationPlacementPlan（正式 Domain Object）

Owner = Operate / Optimize DRI（C）。

```
RepresentationPlacementPlan {
  memory_id
  entries[] {
    representation_id
    representation_type
    provider_ref
    observed_tier
    desired_tier
    actuation_target_ref
    target_generation
    hotness_score
    decision_reason
    policy_version
    generated_at
    valid_until
  }
}
```

- 每个 Memory 的每个 representation 一条 entry；"Memory hot" 分解为 per-representation hotness。
- **RepresentationPlacementPlan 是 `MemorySignal / AccessTrace → per-representation placement decision → TierAction` 之间的正式 Domain Fact。**
- 典型策略：Vector Projection 保持热、Original Content 保持冷（Recall 先命中向量，仅最终候选加载原文）。

### 3.3 ProviderResult vs ProjectionState（双事实源消除）

- **ProviderResult**（A 拥有，机制执行结果）：`ACCEPTED / PENDING / READY / FAILED / UNKNOWN`。
- **P3 ProjectionState**（B 唯一拥有，领域生命周期事实）：`Pending / Building / Ready / Failed / Stale`。

`ProviderResult ≠ ProjectionState`；**A 禁止直接写 ProjectionState**，只有 B 能转 Ready。

---

## 4. P3 Internal Capability Contract Map（冻结）

| Capability | Provider | Consumer | Truth / Fact Owner |
|---|---|---|---|
| SemanticEmbeddingCapability | A | B（passage）、A（query） | A（向量输出） |
| MemoryReadCapability | B | A（Recall 读路径） | B（Memory 事实） |
| ProjectionBuildCapability | 编排=B；机制=A | B | 机制结果=A；ProjectionState=B |
| MemorySignalCapability | B | C | B（发送）；C（消费确认） |
| AccessTraceCapability | Producer=A/B；Consumer=C | C | 见 4.1 |
| CanonicalLoadCapability | B（契约+映射）；Durable Store Provider（物理） | A | 见 4.2 |

### 4.1 AccessTraceCapability 责任

- **Schema / Ingest Contract Owner** = Runtime Foundation Steward（角色，不指定具体员工）。
- **Producer** = A Runtime、B Runtime。
- **Consumer** = C。
- **Persistence** = Shared Runtime Trace Store。

> 禁止写"Fact Owner = Shared"而无责任角色；Schema/Ingest 契约必须由 Runtime Foundation Steward 唯一拥有。

### 4.2 CanonicalLoadCapability Truth Boundary

- **Contract Owner** = B。
- **Memory → content_ref Mapping Owner** = B。
- **Physical Payload Authority** = Durable Store Provider。
- **Consumer** = A。

> 不把 B 写成 canonical payload physical fact owner；B 只拥有"哪条 Memory 指向哪个 content_ref"的映射契约。

---

## 5. Projection 双 Owner 状态顺序（冻结）

```
B: ProjectionState = Building
A: VectorProjectionPort.upsert(...)
Provider: ACCEPTED → PENDING → READY
A: 返回 ProviderResult.READY
B: 校验 memory_version / model_version / schema_version
B: ProjectionState = Ready
```

- A 只返回 ProviderResult（ACCEPTED/PENDING/READY/FAILED/UNKNOWN），**禁止写 ProjectionState**。
- B 校验通过后才转 ProjectionState。

---

## 6. Recall Accountability 表（冻结）

| Recall 步骤 | 实现责任 |
|---|---|
| Query Embedding | A |
| Vector Search | A |
| Candidate validity | A |
| Memory facts | B 提供 |
| decay / conflict / provenance policy | B 定义能力，A 消费 |
| canonical payload | ContentStorePort（B 契约 + Durable Store Provider 物理权威） |
| **Context Pack E2E** | **A 负责最终结果** |

A accountable ≠ 全部亲自实现；A 有权要求 B 的 MemoryRead Capability 满足 Recall 契约，不重写 B 的 Memory 逻辑。

---

## 7. State Glossary（三文档一致性基准）

| 域 | 状态词汇 | Owner |
|---|---|---|
| ProviderResult（Projection 机制） | ACCEPTED / PENDING / READY / FAILED / UNKNOWN | A |
| ProjectionState（P3 领域） | Pending / Building / Ready / Failed / Stale | B |
| TierAction.ActionState（P3 决策） | Generated / Submitted / Succeeded / Failed / Unknown | C |
| P2 Action.status（Provider 执行） | ACCEPTED / RUNNING / SUCCEEDED / FAILED / UNKNOWN | P2 |
| Memory 业务状态 | Active / Archived / Superseded / Expired / Deleted | B |

映射：P3 Submitted ≈ P2 ACCEPTED/RUNNING；P3 Succeeded 仅在收到 P2 SUCCEEDED 反馈后；P3 Unknown = 超时无反馈 → Reconciliation。

---

## 8. 一致性声明

三份冻结文件已核对，REQ ID（11 项）、Port Name（9 个）、Domain Type、State Name、Mock Name（3 个 Simulator）、Owner、MUST/SHOULD 分类**完全一致**，见 `FREEZE_CONSISTENCY_CHECK.md`。

---

**ARCHITECTURE MODEL FROZEN**
**READY FOR WBS AFTER CONSISTENCY CHECK**

*（V0.4.1 完。）*
