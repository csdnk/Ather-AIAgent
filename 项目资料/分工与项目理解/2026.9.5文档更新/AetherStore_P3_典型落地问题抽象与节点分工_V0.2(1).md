# AetherStore P3 典型落地问题抽象与节点分工

# 一、公司提出的问题

## 1.1 Redis / RAG / S3 之间的数据流转

公司提出：

> 数据在 Redis / RAG / S3 之间如何流转？  
> 降级时，Redis 中的数据如何释放？

需要明确：

- 同一条 Memory 在 Redis、Vector / RAG、S3 中分别是什么形态；
- 哪一份是业务主事实；
- 哪一份是派生表示；
- 哪一份是 Hot / Runtime Materialization；
- 数据进入 Redis 的条件；
- 数据退出 Redis 的条件；
- Redis 释放前需要确认什么；
- Redis 释放失败、反馈丢失、状态未知时如何处理。

---

## 1.2 数据调度与升降级过程

公司提出：

> 数据调度的过程是什么？  
> 升级场景下，数据如何从 S3 加载入 RAG、Redis？  
> 降级场景下，数据如何从 Redis 回迁至 RAG，Redis 中的数据如何释放？  
> 整个过程的调用日志如何产生？

需要明确：

- Promote / Prefetch / Demote / Release 分别操作什么；
- 调度对象是 Memory 还是某个 Representation；
- Memory 如何映射到真实 Provider / ActuationTarget；
- P3 如何产生 TierAction；
- Provider 如何执行真实动作；
- 哪一步是 Accepted；
- 哪一步是 Running；
- 哪一步才是 Succeeded；
- 哪一步更新 Observed State；
- 整个过程如何通过 trace_id / memory_id / representation_id / action_id 关联。

---

## 1.3 AccessTrace、MemorySignal、TierState / ResourceState 的来源

公司提出：

> AccessTrace、MemorySignal、TierState / ResourceState 如何拿到？  
> 基于什么方式？

需要明确：

- 谁产生；
- 谁是 Authority；
- 什么时候产生；
- Push 还是 Pull；
- API、Event、MQ、OTel、Poll 或其他方式；
- 是否持久化；
- 是否支持补发 / 重放；
- 多久算 stale；
- 丢失后如何处理；
- 谁消费。

---

# 二、我们的理解和抽象

## 2.1 Operational Runtime Model

公司提出的问题集中在三个运行面：

```text
Concrete Data Plane
真实数据驻留与数据流

Concrete Control Plane
真实调度、执行与状态闭环

Concrete Telemetry Plane
真实信号、Trace、日志和反馈来源
```

对应：

```text
Domain Model
↓
Runtime Flow
↓
Operational Runtime Model
↓
Production Readiness
```

---

## 2.2 Data Plane：Memory 不是在三个存储之间整体搬迁

统一理解：

```text
Memory
│
├── MemoryRecord / Domain Fact
│
├── Canonical Content
│      └── Durable Content Provider
│
├── Retrieval Projection
│      └── Vector / Retrieval Provider
│
└── Runtime / Hot Materialization
       └── Redis
```

因此：

```text
Memory
≠
Redis Record
≠
Vector Record
≠
S3 Object
```

同一条 Memory 可以同时存在多个 Representation。

---

## 2.3 Control Plane：Placement Decision 与 Physical Migration 分离

P3 负责：

```text
Memory / Representation
↓
Placement Decision
↓
ActuationTarget
↓
TierAction
↓
Submit
↓
Feedback / Query
↓
Observed State
↓
Reconciliation
```

Provider / P2 负责：

```text
Physical Migration
├── Copy
├── Verify
├── Cutover
└── Reclaim
```

统一边界：

```text
P3:
为什么做
什么时候做
对哪个 Representation 做
需要什么结果
异常后如何处理

Provider / P2:
底层如何执行
真实状态是什么
真实资源状态是什么
```

---

## 2.4 Telemetry Plane：业务信号与技术调用链分离

### AccessTrace

业务访问事实：

```text
retrieved
validated
loaded
selected
used_in_context
```

### MemorySignal

Memory Domain 变化事实：

```text
memory_id
memory_type
lifecycle
importance
version
```

### TierState / PlacementState

真实 Placement 事实：

```text
current_tier
generation
supported_operations
```

### ResourceState

真实资源事实：

```text
capacity
available
pressure
migration_bandwidth
backend_health
```

### OTel Trace

技术调用链：

```text
request_id
trace_id
span_id
```

### Runtime Log

业务运行过程：

```text
memory_id
representation_id
action_id
old_state
new_state
reason_code
policy_version
provider_status
```

---

# 三、问题扩展

## 3.1 数据驻留问题

需要明确：

- MemoryRecord 在哪里；
- Canonical Content 在哪里；
- Vector Projection 在哪里；
- Working Materialization 在哪里；
- Prewarm Materialization 在哪里；
- Compression Artifact 在哪里；
- `memory_id → content_ref` 在哪里；
- `memory_id → projection_ref` 在哪里；
- 哪一份 authoritative；
- 哪一份 derived；
- 哪一份可重建；
- 哪一份允许释放。

---

## 3.2 Memory Formation 问题

完整链路：

```text
MemoryEvent
↓
Fact Formation
↓
Canonical Content Write
↓
MemoryRecord / Mapping Confirm
↓
Embedding
↓
Vector Projection Build
↓
Projection Ready
↓
Optional Working / Hot Materialization
↓
MemorySignal
```

需要明确：

- 主事实成功点；
- Success / Accepted / Partial 的返回点；
- Canonical Content 写超时；
- Projection Build 失败；
- Redis 写失败；
- MemorySignal 发送失败；
- 重启后的未完成任务恢复。

---

## 3.3 Recall 正常、降级与失败问题

完整链路：

```text
Query
↓
Query Embedding
↓
Vector Search
↓
Candidate Validation
↓
MemoryRead
↓
Canonical Content Load
↓
Rank / Conflict
↓
Context Pack
```

需要覆盖：

- Embedding unavailable；
- Vector timeout；
- Vector partial；
- Projection Pending；
- stale candidate；
- Superseded / Deleted / Expired；
- MemoryRead failure；
- Redis unavailable；
- Canonical Content not found；
- Canonical Content timeout；
- checksum mismatch；
- conflict；
- token budget truncate；
- partial sources。

结果语义：

```text
Complete
Degraded
Failed
```

---

## 3.4 Promote / Prefetch 问题

需要明确：

```text
memory_id
↓
representation_id
↓
provider_ref
↓
ActuationTarget
↓
desired_tier
↓
TierAction
```

不同 Representation 的 Promote 语义分别定义：

```text
Canonical Content Promote
Vector Projection Promote
Hot Materialization Build
Prefetch
```

---

## 3.5 Demote / Release 问题

完整节点：

```text
Demote Decision
↓
确认低层 Serving / Authoritative Copy 可用
↓
停止依赖高层 Materialization
↓
提交 Release / Demote
↓
Provider 执行 Physical Release
↓
Provider 返回真实状态
↓
Refresh Observed State
↓
Reconciliation
```

需要覆盖：

- Release 前置条件；
- Pin；
- Prefetch Window；
- generation；
- Provider failure；
- feedback lost；
- Unknown；
- restart。

---

## 3.6 Action 并发与冲突问题

需要覆盖：

- Submitted 时再次 Promote；
- Submitted 时收到 Demote；
- duplicate action；
- generation mismatch；
- policy_version 变化；
- Memory Deleted；
- Projection Stale；
- target disappeared；
- feedback lost；
- Provider Unknown；
- P3 restart。

---

## 3.7 AccessTrace 采集语义

需要区分：

```text
Search Hit
Candidate Accepted
Canonical Loaded
Context Selected
Context Emitted
```

并明确：

- 哪些算 access；
- 哪些算 hit；
- 哪些算真实使用；
- Redis hit 与 Vector hit 是否分别记录；
- Prefetch 后真实访问如何关联 action_id。

---

## 3.8 ResourceState 问题

需要覆盖：

```text
total_capacity
used_capacity
available_capacity

working_reserved_capacity
prewarm_budget
prewarm_used
pin_budget
pin_used

active_migration
migration_bandwidth
resource_pressure

backend_health
read_latency
write_latency
```

需要明确：

- Authority；
- acquisition；
- sampling；
- freshness；
- stale；
- unknown；
- snapshot / event；
- P3 消费规则。

---

## 3.9 Trace / Log / Audit 贯通问题

关联 ID：

```text
request_id
trace_id
memory_id
representation_id
task_id
action_id
policy_version
generation
```

需要贯通：

```text
Memory Formation
↓
Projection Build
↓
Recall
↓
AccessTrace
↓
Scheduling Decision
↓
TierAction
↓
Provider Execution
↓
Observed Placement
↓
Subsequent Recall Hit
```

---

## 3.10 Failure / Recovery / Operator Control 问题

需要覆盖：

```text
Retry
Reconciliation
Pause
Cancel
Force Keep
Pin
Manual Promote
Manual Demote
Advisory
Restart Recovery
Manual Intervention
```

---

# 四、具体分工

# 4.1 B —— Remember / Memory Formation

## B-01 Memory Representation Inventory

负责定义：

```text
Memory
├── MemoryRecord
├── Canonical Content
├── Compression Artifact
├── Vector Projection
├── Working Materialization
└── MemorySignal
```

每种 Representation 明确：

- Owner；
- authoritative / derived；
- provider；
- stable ID；
- version；
- 是否可重建；
- 删除语义；
- Lifecycle 影响。

交付：

`Memory_Representation_Inventory_V0.1.md`

---

## B-02 Remember Data Residency Profile

负责明确：

- MemoryRecord 存储位置；
- Canonical Content Provider；
- Vector Projection Provider；
- Working Redis 内容；
- Compression Artifact 存储位置；
- `memory_id → content_ref`；
- Working Redis 与 Prewarm Redis 的边界。

交付：

`Remember_Data_Residency_Profile_V0.1.md`

---

## B-03 Remember Runtime Sequence

负责定义：

```text
MemoryEvent
↓
Fact Formation
↓
Canonical Content
↓
MemoryRecord
↓
Projection
↓
Recall Ready
↓
MemorySignal
```

覆盖：

- normal；
- content write timeout；
- projection failure；
- compression failure；
- Working Redis unavailable；
- MemorySignal failure；
- restart recovery。

交付：

`Remember_Runtime_Sequence_V0.1.md`

---

## B-04 Memory Lifecycle × Representation Effect

负责定义 Memory 状态变化对各 Representation 的影响：

```text
Active
Archived
Superseded
Expired
Deleted
```

对应：

```text
Canonical Content
Projection
Working Materialization
MemorySignal
```

交付：

`Memory_Lifecycle_Representation_Effect_V0.1.md`

---

# 4.2 A —— Recall / Context Serving

## A-01 Recall Source & Load Model

负责明确：

```text
Working Redis
Prewarm Redis
Vector / Retrieval Provider
Canonical Content Provider
```

分别承担：

```text
Search Source
Metadata Source
Content Source
Hot Materialization
```

交付：

`Recall_Source_and_Load_Model_V0.1.md`

---

## A-02 Recall Failure / Degradation Matrix

负责定义所有主要失败点的：

```text
Complete / Degraded / Failed
reason_code
retry
context behavior
```

覆盖：

- Embedding；
- Vector；
- Projection；
- MemoryRead；
- Redis；
- Canonical Content；
- conflict；
- token budget；
- partial source。

交付：

`Recall_Degradation_Matrix_V0.1.md`

---

## A-03 AccessTrace Producer Model

负责定义 Recall 过程中的业务访问事件：

```text
Search Hit
Candidate Accepted
Canonical Loaded
Context Selected
Context Emitted
```

并定义：

```text
memory_id
representation_id
request_id
trace_id
source_provider
observed_tier
retrieved
validated
loaded
selected
used_in_context
latency
bytes
```

交付：

`Recall_AccessTrace_Producer_Spec_V0.1.md`

---

## A-04 Recall × Placement Verification

负责定义 Promote / Prefetch 后真实 Recall 的验证事实：

```text
action_id
↓
subsequent recall
↓
target representation hit
↓
used_in_context
↓
hit_after_prefetch
```

交付：

`Recall_Placement_Verification_V0.1.md`

---

# 4.3 C —— Operate / Optimize

## C-01 Operate Input Source Map

负责定义：

```text
MemorySignal
AccessTrace
TierState
ResourceState
ActionHistory
ExecutionFeedback
PolicyContext
```

每项明确：

- Producer；
- Authority；
- Trigger；
- acquisition；
- Push / Pull；
- persistence；
- freshness；
- replay；
- loss handling。

交付：

`Operate_Input_Source_Map_V0.1.md`

---

## C-02 Representation → ActuationTarget Mapping

负责定义：

```text
representation_id
↓
provider_ref
↓
ActuationTarget
↓
supported_operations
```

覆盖：

```text
Canonical Content
Vector Projection
Prewarm Materialization
其他可调度 Representation
```

交付：

`Actuation_Target_Mapping_V0.1.md`

---

## C-03 TierAction Runtime Sequence

### P3 / C 节点

```text
Placement Decision
↓
Admission
↓
Resolve ActuationTarget
↓
Read Observed State
↓
Generate TierAction
↓
Submit
```

### Provider / P2 节点

```text
ACCEPTED
↓
RUNNING
↓
Physical Migration
├── Copy
├── Verify
├── Cutover
└── Reclaim
↓
SUCCEEDED / FAILED / UNKNOWN
```

### P3 / C 收口节点

```text
Feedback / Status Query
↓
Refresh Observed State
↓
TierAction State Update
↓
Reconciliation
```

交付：

`TierAction_Concrete_Sequence_V0.1.md`

---

## C-04 TierAction State & Conflict Model

负责定义：

```text
Generated
Submitted
Succeeded
Failed
Unknown
```

并覆盖：

- duplicate action；
- opposite action；
- generation mismatch；
- policy changed；
- Memory Deleted；
- Projection Stale；
- feedback lost；
- timeout；
- restart；
- Provider Unknown。

交付：

`TierAction_State_and_Conflict_Model_V0.1.md`

---

## C-05 ResourceState Consumer Contract

P3 / C 定义所需字段：

```text
capacity
working_reserved
prewarm_budget
prewarm_used
pin_budget
pin_used
available
pressure
active_migration
migration_bandwidth
backend_health
read_latency
write_latency
```

Provider / P2 提供真实事实。

交付：

`ResourceState_Consumer_Contract_V0.1.md`

---

## C-06 Prewarm Redis Lifecycle

### P3 / C 负责

```text
什么时候Prefetch
什么时候Promote
什么时候Keep
什么时候Demote
什么时候允许Release
Pin规则
Prefetch Window
Release Admission
Release后的Observed State处理
```

### Redis / Provider 负责

```text
实际写入
实际删除
TTL / Eviction执行
物理内存回收
真实执行结果
```

交付：

`Prewarm_Redis_Lifecycle_V0.1.md`

---

# 4.4 RF-Steward —— Shared Runtime Foundation

## RF-01 P3 State Catalog

统一：

```text
Stateful Object
Owner
States
Terminal
Recovery
Cross-Runtime Mapping
```

交付：

`P3_State_Catalog_V0.1.md`

---

## RF-02 Signal / Telemetry Standard

统一：

```text
RequestContext
request_id
trace_id
memory_id
representation_id
task_id
action_id
policy_version
generation
```

统一四类证据：

```text
AccessTrace
OTel Trace
Runtime Structured Log
Audit Log
```

交付：

`P3_Signal_and_Telemetry_Standard_V0.1.md`

---

## RF-03 Observability Correlation Model

统一贯通：

```text
Memory Formation
↓
Projection
↓
Recall
↓
AccessTrace
↓
Scheduling Decision
↓
TierAction
↓
Provider Feedback
↓
Observed Placement
↓
Subsequent Recall Hit
```

交付：

`P3_Observability_Correlation_Model_V0.1.md`

---

# 4.5 Integration Steward —— P2 / Provider Integration

负责整理 P3 对 Provider / P2 的能力要求：

```text
PlacementState
ResourceState
ActuationTarget Resolve
TierAction Submit
Action Status Query
ExecutionFeedback
Backend Health
Metrics / Trace
```

Provider / P2 负责：

```text
Physical State
Physical Execution
Resource Truth
Provider Internal Recovery
```

交付：

`P2_Operational_Runtime_Requirement_Delta_V0.1.md`
