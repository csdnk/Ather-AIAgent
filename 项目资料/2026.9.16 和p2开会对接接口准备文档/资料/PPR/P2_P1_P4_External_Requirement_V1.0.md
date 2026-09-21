# P2 / P1 / P4 External Requirement V1.0
# 明确：哪些事 P3 只提 Consumer Requirement，不负责实现

> 本文件列出 P3 对外部团队（P2/P1/P4/Infra）的**要求**，与 P3 自己的实现边界严格分开。
> 原则：`P3 defines what，P2/P1/P4 decides how`。P3 只持"可观测结果 + 可止血控制 + 可验收 SLO"。
> 完整 Consumer Requirement 见 `02_冻结架构_V0.4.1/P2_CAPABILITY_REQUIREMENT_REGISTER_V0.4.1.md`。

---

## 1. 对 P2 的要求（P3 Consumer Owner → P2 Provider Owner）

| REQ | Capability | P3 Consumer Owner | P3 要的语义（不实现） | P2 实现（P3 不设计） |
|---|---|---|---|---|
| VEC-001 | 向量投影写入 | A | 幂等 upsert、ProviderResult 五态、超时可 query | ANN/索引/写放大 |
| VEC-002 | 向量检索 | A | TopK + 稳定引用 + degraded/partial | ANN 检索/召回质量 |
| OBJ-001 | 规范内容对象读写 | B | durable/checksum/head、写超时 head 验证 | 对象存储内核/持久化 |
| ACT-001 | Data→Actuation Target Resolve | C | opaque ActuationTarget，不绑 Segment | 内部 target 组织 |
| SEG-001 | Segment 内省 | C | 段级观测（当前 Provider 能力） | 段组织 |
| PLC-001 | 真实层级/容量状态 | C | current_tier+generation+水位（只消费） | 层级/容量事实维护 |
| TIER-001 | TierAction 提交 | C | accepted/rejected+backend_task_id；accepted≠success | 执行准入 |
| TIER-002 | 迁移反馈 | C | SUCCEEDED 含 actual_tier+completion_time | 迁移执行 |
| TIER-003 | 动作状态查询 | C | query(action_id) 重启后仍有效 | 状态维护 |
| **TIER-004** | **迁移事务语义** | C | Copy→Verify→Cutover→Reclaim 的可观测/可验收语义 | **物理迁移事务实现** |
| HLT-001 | 健康/就绪 | C | engine_ready ≠ pod running | 引擎就绪判定 |
| VER-001 | 版本/generation | B/A/C | generation 随读写返回 | generation 递增存储 |

## 2. 对 P1 的要求（若 P1 合同恢复）

| 项 | P3 Consumer Owner | 说明 |
|---|---|---|
| IF-03 Tiering Hook（promote/demote/pin） | C | 原方案归 P1；当前 P2 改 K8s/CSI，**Provider 悬空**，需 PM 协调确认承接方 |

## 3. 对 P4 的要求（P4 无合同，P3 最小 native 过渡）

| 项 | P3 侧 | P4 侧 |
|---|---|---|
| 身份/RBAC/mTLS（IF-08/09） | A/B/C 各自最小 native（不阻塞 MVP） | 统一身份/网关 |
| OTel 全链路（IF-07） | A/B/C 实现 trace_id 贯通 | 定义 resource/span/tag 标准 |
| 网关/控制台 | 不实现 | 后续接入 |

## 4. 对 Infra 的要求

| 项 | P3 Consumer Owner | 说明 |
|---|---|---|
| 合同基线验收环境（Redis/Milvus/Celery/B1 + K8s Acceptance Profile） | IS | 提供/运维唯一 DRI（PM Decide 边界） |
| 节点宕机/网络延迟注入环境 | IS（战役） | 阶段4 验收战役 |
| Control/Data Plane 资源隔离 | C | 写入部署 Profile，Infra 执行 |

---

## 5. 明确"P3 不实现"清单（防误解）

1. 对象存储内核、ANN 检索内核、WAL/复制/Fencing。
2. **物理迁移事务**（Copy/Verify/Cutover/Reclaim、带宽执行、实际数据搬迁）。
3. 底层加密算法/KMS（依赖存储/公司平台）。
4. 企业统一身份/网关/控制台（P4）。
5. 监控聚合/告警平台（公司平台；P3 只提供指标出口 + 告警语义）。

---

*（External Requirement V1.0 完。P3 只定义 Consumer Requirement，实现由对应 Provider 决定。）*
