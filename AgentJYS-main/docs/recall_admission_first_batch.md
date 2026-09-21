# Recall 请求受理与执行骨架

结构更新（2026-09-15）：新实现统一位于 `recall/`，共享 Embedding 位于 `recall/embedding/`。旧路径仅作兼容，后续开发按 [Recall / Remember / Operate 架构](flow_architecture.md) 进行。

后续迁移进展：真实推理后端现已迁入，ONNX 本地验证通过。下文验证数字保留受理骨架交付时记录；当前结果和运行方式见 [真实 Embedding 迁移](recall_embedding_native.md)。

更新：2026-09-15。本批在已有 Recall 契约和实验实现上补齐受理与执行边界，不替换冻结的 Northbound v1，也不新增真实推理、搜索或数据库实现。

## 本批能运行什么

```text
RecallInput
  → 输入校验
  → RecallAuthorizationPort 授权
  → scope_union_v1 自动选路
  → RecallAdmissionPort 准入检查
  → RecallExecutionStorePort 原子受理或附着已有执行
  → RecallAdmission（内部绑定，不是 ContextPack）
  → RecallExecutionService.start
  → RUNNING_REQUEST_VALIDATION
```

客户端不得传入 retrieval_mode、source_selection、allowed_sources。Query、预算、类型与时间范围必须合法；显式 session/task 的归属和一致性由可信授权适配器确认。未知授权拒绝，不丢弃非法编号后重选。

类型未提供时按 scope_union_v1 的 Working/Episodic/Semantic 展开；空列表拒绝。来源选择只使用已确认的范围、权限和类型，不读取服务健康、Query 关键词或缓存状态。

tokenizer/template 字段可以省略，由服务端 policy 解析；保留旧实验调用的显式字段兼容，但必须匹配绑定配置。重试使用原配置，新增权限不扩大原来源，撤权拒绝。重复调用不会延长原执行 deadline 或重新分配 Outbox 额度。

## 三个依赖 Port

| Port | A 实现的调用规则 | 外部应保证的能力 |
|---|---|---|
| RecallAuthorizationPort | 验证返回的身份、scope、逐来源允许/拒绝/未知及有效期；重试重新授权 | RF/身份 Owner 的可信证据，含显式 session/task 归属和一致性 |
| RecallAdmissionPort | 在原子受理内调用纯准入检查；默认限制执行总数与事件预留，已有绑定优先附着 | 准入快照与预留必须处于同一提交边界，不能先查容量再竞争写入 |
| RecallExecutionStorePort | 原子受理、按请求键/执行 ID 查找、领取租约、保存检查点、CAS 推进 | RF 的事务、唯一键、时限判断、租约和防旧写保证 |

`RecordRecallExecutionStore` 是对已有 `AtomicRecordStore` 的逻辑适配：沿用已有实验记录的 namespace 和序列化方式，新增租约、容量和检查点校验。它没有数据库连接或建表逻辑。实际数据库与生产持久化依然归 RF。

旧的 `RecallAdmissionService(atomic_store, authorization, policy)` 继续可用。新的 Port 注入方式为：

```python
service = RecallAdmissionService(
    None,
    authorization,
    policy,
    execution_store=rf_execution_store,
    admission_gate=admission_gate,
)
admitted = await service.admit(request)
execution = RecallExecutionService(rf_execution_store).start(
    admitted.request.tenant_id,
    admitted.request.recall_id,
    owner="recall-worker",
)
```

旧 `RecallQueryService` 仍依赖原 atomic-record 实验适配方式；本批未将它自动接入新 Port 流程，也未将它注册进正式 HTTP Host。下一批再接 Query 阶段，不能把它当作本批的新运行入口。

## 幂等、状态与结果未知

- 索引绑定 tenant、principal、完整 Scope 摘要与幂等键；请求、索引、执行、原策略和预留一致保存。显式改变语义返回 IDEMPOTENCY_CONFLICT。
- 授权等待期间执行可能变化，因此返回前重新读取绑定；不能返回过期快照或在原记录消失后新建执行。
- 原子受理必须先检查已有绑定，再检查容量。同键并发只能保留一个 recall_id、一个 commit_id 和一份预留。
- RF 变更调用发生结果未知时抛出 `RecallWriteUnknownError`；调用方只查询同一键。查无结果或查询失败返回 `RecallAdmissionUnconfirmedError`，它不是业务终态，也不附带 Context。
- 服务实例保留未查证的键，避免同实例盲目重交。跨实例/重启安全由 RF 保证：`find` 的 None 必须证明没有绑定或在途受理；未解决的持久化意图应报告 unknown。普通副本 not_found 不满足这一契约。
- `start` 领取租约并将 CREATED 推进到 RUNNING_REQUEST_VALIDATION。后续需由实际阶段处理器保存可信输出，再调用 `save_checkpoint` 和 `advance`。
- 检查点与输出必须同租户、同执行、同请求/策略，摘要匹配且不可覆盖。推进重新验证受控输出，不只检查一个布尔“已完成”。
- 所有推进验证 state_version、lease_token、租约与原 deadline；失租 Worker 和旧版本写入拒绝。活跃 Worker 数不超过 max_inflight_requests；其余受理执行等待，尚无自动调度 Worker。
- 十三态关系保留，但本批 `advance` 禁止写入任何终态。后续只有专用的结果/Trace/Outbox 原子提交能力可以完成业务终态。
- 终态或载荷已清除时，受理接口返回 REPLAY_REVALIDATION_REQUIRED。`RecallReplayPort` 仅定义未来复核边界，本批不返回历史正文。

## 演示与验证

在已安装项目及 dev 依赖的 Python 3.13 环境运行：

```text
python examples/recall_admission.py
python -m pytest tests/unit/recall/test_execution_skeleton.py -q
python -m pytest tests/unit/recall -q
```

演示输出三种检索模式的 RUNNING_REQUEST_VALIDATION，以及 SCOPE_DENIED、AUTHORITY_UNAVAILABLE 两个拒绝场景。`context_produced=false`，后端明确为 test-double。

测试替身位于 `mocks/recall.py`，不接入 bootstrap。它只模拟单进程事务和固定授权证据，不证明数据库隔离、持久化、加密、RF 服务可用性或真实进程重启恢复。

本批验收覆盖三模式、task-only、未知授权/撤权、非法筛选、服务器配置变化、显式语义冲突、同键并发、取消等待者、原子受理回包丢失、未知写不重发、独立 Worker 配额、检查点绑定/篡改、租约过期及旧 Worker 写入。标准字段与版本仍由原数据字典和已有模型维护。

后续工作：实现请求复核阶段处理器及其读取记账，接共享 Embedding Query 阶段，然后逐阶段接入候选发现、正文校验、排序装配和可靠最终提交。RF 联调需单独提供真实持久化与重启恢复证据。

## 本次验证结果

在已有 Python 3.13.15 开发环境执行：

- 修改前选定的 Recall/Context/北向契约基线：73 项通过。
- 新增执行骨架测试：43 项通过；结构迁移后新增 14 项依赖隔离与兼容测试，Recall 测试目录合计 110 项通过。
- 结构迁移后全部单元测试：583 项通过、1 项跳过；跳过项为缺少可选 python-docx 依赖的文档解析测试。
- `ruff check src tests scripts examples/recall_admission.py` 通过。
- Recall 模块与新测试替身的 mypy 检查通过；`git diff --check` 通过。
- 本地演示的三模式和两个拒绝分支正常运行。
- 全新进程屏蔽 B1/B2/B3 及旧 memory 包后，受理示例及使用测试后端的 Query/Passage 共享计算通过；未引入真实模型推理。

这些结果不包含真实 RF/P2 联调、进程崩溃后的数据库恢复或 Embedding 性能验收。未修改 `/api/v1/context` 和 bootstrap，未创建数据库、迁移或新的后台服务。
