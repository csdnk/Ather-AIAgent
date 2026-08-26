# ADR-0002: 北向 API 版本化、兼容性与弃用策略（对标 Stripe date-based versioning）

- 状态：Proposed
- 日期：2026-08-19
- 决策层：接口契约
- 关联：[P3_NORTHBOUND_API_V1.md](../P3_NORTHBOUND_API_V1.md)、`contracts/p3-northbound-v1.json`

## Context

公司问「上层接口是否需要修改」，本质不是「要不要加版本号」，而是：**P3 演进时，已接入的 P4 / 租户
会不会被悄悄改坏；旧版本到底怎么收尾**。这是一整套契约生命周期管理，行业已有成熟范式：

- **Stripe date-based versioning（标杆）**：每个 API 版本按发布日期命名，每个账户/请求**锁定（pin）**
  一个版本；新功能只进新版本，老账户永远不受影响，直到主动升级。
  [HN 讨论](https://news.ycombinator.com/item?id=38950364)
- **OpenAI / Pinecone**：URL 前缀版本（`/v1`）+ 显式弃用通告（deprecation notice）+ 迁移窗口。
- **LangChain / Mem0 的 SDK 兼容层**：多语言 SDK 在客户端吸收版本差异，让用户业务代码不直接面对
  底层契约变化。

共同结论：**版本化 ≠「加个 /v2」，而是「版本锁定 + 兼容演进 + 有期限弃用」三件事的组合。**

## Decision

1. **单一事实源（SSOT）**：
   - 人类可读：`docs/P3_NORTHBOUND_API_V1.md`
   - 机器可读：`contracts/p3-northbound-v1.json`（CI 校验一致，不一致以机器可读为准）。

2. **兼容规则（只增不改）**：
   v1 内**允许**：新增可选字段 / 可选接口 / 可选查询参数、放宽不破坏语义的默认值。
   以下均属 **breaking change，必须发新版本**：

   | 类别 | 示例 |
   | --- | --- |
   | 删除/重命名字段 | 删除 `assembled_text`、`memory_refs` |
   | 改变字段含义或类型 | `score` 从 0..1 改成 0..100 |
   | 收紧合法输入 | 原本接受空 `query`，现在拒绝 |
   | 改变成功状态码 | `200`→`201`、`202`→`200` |
   | 删除接口或改路径 | 移除 `/api/v1/b2/search`、改 `/api/v1/memory/events` 路径 |
   | 改变错误/幂等语义 | `422` 与 `409` 的边界变化 |

3. **版本化策略：URL 前缀 + 版本锁定（pin）**（取 Stripe 的「锁定」思想，用 URL 前缀落地）：
   - 契约版本由 URL 前缀表达：`/api/v1/*`、`/api/v2/*` 并存。
   - **版本锁定**：每个 `tenant_id` 绑定一个契约版本；P3 升级契约时，老租户继续跑老版本，新租户
     默认新版本，迁移是**逐租户**进行，不是全局一刀切。这是 Stripe「account pinning」的落地。
   - **推荐默认**：当前所有租户锁定 v1，不新开 v2。

4. **弃用/下线（deprecation/sunset）策略**：
   - breaking change 进入 v2 时，v1 标记 deprecated，给出**通告期**（建议 ≥ 90 天）。
   - P4/租户在迁移窗口内升级到 v2。
   - 迁移窗口结束后 v1 下线；下线日期由**双方书面冻结**，不在 PRD 里隐式假设。

5. **SDK 兼容层**：应用化交付的 SDK（ADR-0001 阶段 2）在客户端吸收契约版本差异，让 Agent 开发者
   业务代码不直接面对 v1/v2 切换。

6. **消费者驱动契约（CDC）**：P4 是 v1 唯一业务消费者，契约变更以 P4 诉求锁定。演进判定流程：
   `提出变更 → breaking-change 清单判定 → 非破坏入 v1；破坏开 v2 → 逐租户迁移 → P4 验证`。

## Alternatives Considered

- **date-based versioning（Stripe 式）**：最稳、最适合海量异构消费者；但当前 P4 是单一消费者，且已
  有 `/api/v1` 前缀，迁移到 date-based 成本大于收益，**暂不采纳**，等消费者数量上来再评估。
- **header 版本化（Accept: application/vnd.aether.v2+json）**：网关/缓存友好，但当前 HTTP 直连形态
  过重，暂不采纳。
- **无版本化、就地修改**：每次改接口都可能破坏已接入的 P4，无法并行升级，不采纳。

## Consequences

- **正面**：P4 升级有保障；「逐租户锁定版本」避免全局大爆炸式升级；弃用有明确期限，不再无限并存。
- **负面/代价**：多版本并存 + 逐租户迁移有运维成本；需维护 SDK 兼容层与 `contracts/*.json` 同步。
- **待跟进**：
  - gRPC 北向契约（如未来新增）是否复用同一策略。
  - 长文本「轮询 → 回调」是否作为 v2 的破坏性变更。
  - deprecation 通告的载体（changelog / status 页）。
