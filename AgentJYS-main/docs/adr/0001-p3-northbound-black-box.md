# ADR-0001: P3 交付形态——组件独立服务 + 产品应用化交付（HTTP 仅为过渡）

- 状态：Proposed
- 日期：2026-08-19
- 决策层：交付/集成形态
- 关联：[P3_NORTHBOUND_API_V1.md](../P3_NORTHBOUND_API_V1.md)、[P4_SIMULATOR.md](../P4_SIMULATOR.md)、[WEB_DASHBOARD.md](../WEB_DASHBOARD.md)

## Context

P3 最终要「给用户使用」，而不是只交付一个可调的 HTTP 服务。先看这条赛道的成熟产品怎么做交付，
再决定 P3 的形态：

| 产品 | 交付形态 |
| --- | --- |
| [Mem0](https://github.com/mem0ai/mem0) | open-source memory layer：**Python/JS 双 SDK + 托管 API + self-host** |
| [Zep](https://help.getzep.com/v3/concepts) | enterprise agent memory：**SaaS + self-host**，Graph + fact + 时间知识图谱 |
| [Letta](https://deepwiki.com/letta-ai/letta/1.3-key-concepts) | **SDK + server + 控制台 + memory blocks**（core/archival/recall） |
| [LangSmith](https://support.langchain.com/articles/3714335605) | **托管平台 + SDK**，organization/workspace/project 三级多租户 |

**共同结论：没有一个产品是「只给裸 HTTP」的。** SDK 是开发者接入的第一入口，控制台/平台是运维与
治理入口，HTTP API 只是这些形态底层的承载协议。这直接否定了「P3 只交付 HTTP 服务 + 契约文档」的
做法。

同时必须区分两个被混淆的层面：

1. **组件进程形态**——P3 在运行时是独立进程，还是被嵌入为库/sidecar？
2. **产品交付形态**——用户/公司最终以什么形式拿到并「用起来」P3？

## Decision

1. **组件进程形态：独立服务（进程/容器边界）。**
   P3 runtime 是独立部署的服务组件，不是进程内库、不是 SDK 注入 P4、不是 sidecar（理由：P4 是异构
   渠道，进程内库造成语言绑定与版本耦合）。

2. **功能接口：黑盒 HTTP v1 契约（当前过渡形态）。**
   P4/开发者通过 HTTP v1 契约消费 P3，不导入 P3 模块、不直连 Redis/Milvus/Celery/P2/B1。此点是对外
   接口的长期边界约束；但「HTTP 作为唯一接触面」是**过渡**，不是终点。

3. **产品交付形态：四层应用化交付（对齐 Mem0/Zep/Letta/LangSmith 的通用形态）：**
   - **服务端 runtime**：可独立部署的 P3 服务（B1/B2/B3 + 依赖编排）。
   - **开发者接入层（SDK / CLI）**：多语言 SDK 封装 HTTP 契约，让 Agent 开发者不手写 HTTP。对标
     Mem0 的 Python/JS 双 SDK，**阶段 2 先做 Python SDK**（与 P3 技术栈一致）。
   - **管理控制台（Web 应用）**：登录后可建 Agent、看 Trace、看运行状态、管 Policy、跑验收。现有
     `web/`（React）与 `scripts/dashboard_page.py`（HTML）是雏形，需产品化为正式控制台——对标
     LangSmith 平台与 Letta 控制台。
   - **部署与运维包**：私有化部署（Compose / 安装包），可选托管 SaaS——对标 Zep 的 self-host/SaaS
     双形态。

4. **HTTP 的定位（明确声明）**：v1 用 HTTP 仅为「简洁方便 + 快速冻结契约」；最终用户不直接面对裸
   HTTP——开发者走 SDK、运维走控制台，HTTP 是底层承载。

5. **演进路线（分阶段，避免把当前形态当终点）：**
   - **阶段 1（当前）**：HTTP API + 契约冻结；`web/` 控制台作为内部演示/联调工具。
   - **阶段 2**：Python SDK + 管理控制台产品化（登录/权限/Agent 管理/Trace 视图/验收页，关联
     ADR-0004）。
   - **阶段 3**：私有化部署包 / 托管形态，应用化交付成熟。

6. **黑盒/白盒按角色分层：**
   - **开发者**：黑盒——只面对 SDK / HTTP 契约，看不到内部实现。
   - **运维/管理员**：控制台白盒——可看状态/指标/Trace/任务，但不暴露内部实现细节（Redis key、
     算法参数、P2 集合命名）。

## Alternatives Considered

- **只交付裸 HTTP + 文档（原方案）**：把「当前过渡形态」当「最终交付形态」，与行业所有对标产品
  相悖，已否决。
- **进程内库（Python SDK）**：耦合最紧，与「P4 是异构渠道」冲突，不采纳为组件形态（SDK 只作应用
  化交付的接入层，而非 P3 本体）。
- **sidecar**：适合「透明拦截」（B1 之于推理），不适合业务组件 P3，不采纳。
- **托管 SaaS 单一形态**：作为阶段 3 可选项；优先私有化部署更契合公司现有形态。

## Consequences

- **正面**：交付形态对齐行业通用范式（SDK + 服务 + 控制台），不再是自创；HTTP 过渡不锁死未来；
  `web/` 控制台有明确产品化路径；公司「谁能安装/配置/看状态/验收」有了完整答案。
- **负面/代价**：应用化交付（SDK、控制台产品化、部署包）是超出「跑通链路」的额外工程项，需单独
  立项排期，不能与 B1/B2/B3 指标工作混为同一验收口径。
- **待跟进**：
  - Python SDK 范围与 API 面（阶段 2 立项）。
  - 管理控制台产品化范围（登录/RBAC/多租户，关联 ADR-0004）。
  - 私有化部署 vs 托管 SaaS 选型。
  - 运维面（metrics/日志/告警）清单。
