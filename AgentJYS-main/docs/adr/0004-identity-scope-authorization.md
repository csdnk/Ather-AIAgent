# ADR-0004: 身份与权限——v1 扁平 scope 隔离，层级租户与 Key Scope 留作阶段 2（折中）

- 状态：Proposed
- 日期：2026-08-19
- 决策层：授权模型
- 关联：[P3_NORTHBOUND_API_V1.md](../P3_NORTHBOUND_API_V1.md) §3、[P4_SIMULATOR.md](../P4_SIMULATOR.md)

## Context

公司问：谁能写 Memory、谁能查、谁能删、谁能改 Policy、谁能看 Trace、不同 Agent 之间能否互访。
业界成熟产品的多租户与权限范式（作为**阶段 2 的方向依据**）：

- **LangSmith**：三级层级租户 **organization → workspace → project** + access policies +
  workload isolation；多租户下「每客户一个 workspace」。
  [LangSmith 多租户](https://support.langchain.com/articles/3714335605)
- **Pinecone**：**API key 带 scope（read / write）** + environments + organization/projects；
  用 key 粒度做最小权限。[Pinecone key scopes](https://theneuralbase.com/pinecone/learn/advanced/api-key-scopes-read-write/)

共同结论：业界终态 = **层级租户 + API Key scope + 环境隔离**。但这是「产品化终态」，直接一步到位会
带来 `organization_id` 契约改造 + key scope 校验逻辑两块新增开发。

**本 ADR 采用折中**：v1 阶段**不改造**、保持扁平 scope 零新增开发；把「层级租户 + key scope」明确
降级为**阶段 2 演进方向**，但仍在**文档层**把权限语义定义清楚——做到「先定义、后实现」，既不为
工作量买单，又不让权限语义空白。

## Decision

1. **P3 不自建统一身份平台 / 不做登录 / 不实现完整 RBAC。**
   身份认证与 RBAC 属于平台/网关层。P3 消费上游已授权上下文（OIDC/JWT 解析后的 scope）或平台签发的
   API Key；不重复造身份系统。

2. **v1：扁平 scope 隔离（现状冻结，零新增开发）。**
   - 保持 `tenant_id / user_id / agent_id / session_id` 扁平 scope，**不新增 `organization_id`，
     不做层级租户模型**，避免 v1 契约与隔离键改造的工作量。
   - 隔离语义：不同 `tenant_id` 之间、同一 `tenant_id` 下不同 `agent_id` 之间的数据默认互不可见。
   - 缺 scope 或 scope 非法 → `422`（已冻结契约 §5）。

3. **v1：概念层定义 Key Scope（先定语义，不实现校验）。**
   - 在契约/文档层定义三种 scope 语义，把「谁能做什么」映射清楚：
     - `read`：只读 Memory / Context / Search / Task 查询。
     - `write`：写入 Memory / 提交长文本任务。
     - `admin`：管理 Policy、查看全量 Trace、删除数据。
   - **实现时机**：P3 侧对 key scope 的实际校验（解析凭证、按 scope 拒绝）作为**阶段 2 工程项**，
     与层级租户一起立项；v1 只保留「scope 完整性校验 + 隔离」。

4. **授权模型（推荐默认）：信任上游「已授权上下文」。**
   调用方已通过平台网关认证授权，P3 只做 scope 完整性 + 隔离校验，不重复认证。部署要求 P3 位于
   trusted gateway 之后。

5. **无权限/越权表现（冻结语义）：**
   - scope 缺失/非法 → `422`（v1 现状）。
   - 显式越权（key 无相应 scope 或跨租户访问）→ `403`，作为 **v2 候选**，不在 v1 改变错误码语义。

6. **环境隔离（对齐 LangSmith workload isolation）：**
   `runtime_profile`（demo/local/production）与数据环境强绑定：demo 数据不得与 production 混用，
   mock 运行不得冒充验收证据（与 `p3_runtime_architecture.md` 一致）。

## Alternatives Considered

- **v1 即实现层级租户 + key scope 校验**：终态正确，但需 `organization_id` 契约改造 + key scope
  校验逻辑，工作量超出 v1 范围，**折中后不采纳为 v1 决策**，作为阶段 2。
- **完全不做隔离（信任调用方自报 ID）**：最简，但租户数据可被任意伪造访问，无法通过公司审查，不采纳。
- **P3 内建 RBAC/登录**：控制力最强，但重复造身份系统、扩大交付面，与「业务组件」定位冲突，不采纳。
- **完全不定义 key scope（连语义都不写）**：工作量最小，但权限语义空白，违背「Out of Scope ≠
  Undefined」，不采纳。

## Consequences

- **正面**：v1 零权限改造工作量；权限语义（谁查/写/删/管 policy）已在文档层定义清楚，公司能拿到
  明确答案；终态方向（层级租户 + key scope）已显式声明，不重复造身份系统。
- **负面/代价**：v1 期间「谁能管 policy / 跨租户访问」只有**语义约定、无强制校验**，真正强制在
  阶段 2 才落地；期间 P3 依赖上游网关的正确授权。
- **待跟进**：
  - 阶段 2 立项：层级租户（`organization → tenant → agent`）+ key scope 校验（对齐 LangSmith/Pinecone）。
  - 403 vs 422 的正式冻结。
  - 跨租户/跨 Agent 访问例外策略。
  - B3 Policy 管理授权（谁能改 policy，`admin` scope 粒度）。
