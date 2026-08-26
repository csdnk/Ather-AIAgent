# ADR-0006: 消费者驱动契约测试与验收（对标 Pact）

- 状态：Proposed
- 日期：2026-08-19
- 决策层：验收
- 关联：[P4_SIMULATOR.md](../P4_SIMULATOR.md)、`contracts/p3-northbound-v1.json`、[P3_NORTHBOUND_API_V1.md](../P3_NORTHBOUND_API_V1.md)

## Context

黑盒交付（ADR-0001）与版本化演进（ADR-0002）都要求一个前提：**P3 演进时能自动发现「是否破坏了
P4」**，而不是靠人工回归。同时公司要求验收「可复现」。

**Consumer-driven contract（CDC）** 是业界事实标准，[Pact](https://docs.pact.io/pact_nirvana) 是代表
实现：**消费者**生成契约（pact）→ **provider** 用 verifier 验证 → **broker** 存契约并做 CI 门禁 →
任何 breaking change 在合并前被拦截。角色天然对应 P3 现状：P4 = consumer，P3 = provider。

当前 `src/aether_p4_simulator` 是 P4 参考实现但定位是 demo；`contracts/p3-northbound-v1.json` 是机器
可读契约但未被 CI 消费。需要把它们从「演示」提升为「验收与兼容性门禁」。

## Decision

1. **把 P4 Simulator 提升为 v1 契约消费者（consumer）。**
   `src/aether_p4_simulator` 从「参考 demo」升级为「P4 视角的契约消费者」：只通过
   `P3_NORTHBOUND_API_V1.md` 接口访问 P3，作为契约测试的客户端与契约（pact）生成方。P3 侧不改变
   对 P4 的契约。

2. **机器可读契约为 SSOT，纳入 CI 门禁。**
   `contracts/p3-northbound-v1.json` 是契约断言来源。CI 每次 P3 改动，用 P4 消费者视角重放契约断言，
   验证字段/状态码/错误语义，对齐 ADR-0002 的 breaking-change 清单。

3. **CDC 工作流（对标 Pact）：**
   ```
   P4 consumer 生成契约（pact）
        → P3 provider 用 verifier 验证
        → 契约存入 broker（或仓库内 contracts/）
        → CI 门禁：breaking change 在合并前拦截
   ```
   - 契约与 `contracts/p3-northbound-v1.json` 双向同步。
   - 新增字段/接口不会破坏 consumer；破坏性变更在 CI 即失败（关联 ADR-0002）。

4. **验收可复现。**
   每个合同指标对应一条一键脚本，复用 `scripts/run_server_acceptance.sh` 模式，产出可审计产物
   （日志/JSON 报告）。验收证据必须绑定 `runtime_profile=production`，禁止 demo/mock 冒充验收
   （与 `p3_runtime_architecture.md` Runtime Profile 一致）。

5. **推荐默认（分两步，先不引入重型框架）：**
   - **第一步（当前）**：手写「P4 → P3 北向契约测试」——用 P4 Simulator 的 HTTP 客户端断言 v1
     契约字段/状态码/错误语义，契约存 `contracts/`，不引入额外框架。
   - **第二步（后续评估）**：消费者增多后，再引入 Pact（pact-python）+ broker；在此之前不引入 Pact
     依赖。

6. **验收证据链**：每个指标验收至少包含 `契约断言 + 指标数据 + 复现命令 + runtime_profile`，缺一
   不可作为验收证据。

## Alternatives Considered

- **仅单元 + 集成测试**：能保证 P3 内部正确，但无法从 P4 视角发现契约漂移（P3 自己测自己看不到
  consumer 是否被破坏），不采纳为唯一手段。
- **立即引入 Pact + Broker**：体系最完备，但对当前「单一消费者 P4」偏重，先手写契约测试，避免过早
  引入基础设施，不采纳为当前默认（作为第二步）。
- **人工回归清单**：零成本但不可复现、易漏，无法作为验收门禁，仅作补充。

## Consequences

- **正面**：兼容性回归自动发现；CDC 工作流与 Pact 对齐，验收有可复现证据链；P4 Simulator 获得正式
  工程定位。
- **负面/代价**：需维护 `contracts/*.json` 与契约测试同步；CI 增加门禁成本。
- **待跟进**：
  - breaking-change 清单的 CI 自动化（关联 ADR-0002）。
  - 指标验收脚本化清单（关联 README「待补候选」P0：指标定义与验收口径）。
  - Pact/broker 是否引入（第二步评估）。
