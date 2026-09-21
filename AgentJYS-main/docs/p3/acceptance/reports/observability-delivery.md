# 日志追踪与健康检测验收

日期：2026-09-19。依据：PRD V1.2、P3 公共底座及三流程职责，以及本轮优先实现日志、Trace 和健康检测的要求。

## 交付结果

已在现有 Python Host 和 CLI 接入独立日志数据库、处理节点输入输出摘要、失败堆栈位置、事务提交/回滚记录、跨任务与事件的 trace_id、分页查询及 JSONL 导出。Trace 查询同时显示当前持久任务与事件投递状态，便于区分技术调用结果和业务事实。

健康检测包含 SQLite 与日志数据库实际读写探针、本地缓存目录读写核验、本地提取/词法计算检查、向量依赖可访问性、Worker 心跳及过期判断、授权范围内的任务和未知动作检测。模型或向量提供方尚未接入真实探测时返回 unknown。

使用说明、保留策略、脱敏规则及接入方式见[日志追踪与健康检测](../../development/06_日志追踪与健康检测.md)，技术决策见[ADR-P3-003](../../adr/ADR-P3-003_本地日志追踪与健康检测.md)。

## 实际校验

[自动报告](observability-validation.json)记录本批环境、源码摘要及检查结果。实际通过 **256 项测试**：34 项三流程/日志检测测试、33 项公共底座测试、189 项契约测试；72 个 Schema 一致性、Ruff、Mypy、底座与三流程演示均通过。

新增 12 项测试的证据：

| 故障或边界 | 观察结果 |
|---|---|
| 保存后换 Host，再执行后台任务 | 同 trace_id 可见提取、投影、事件消费、自动调度，父子节点关联保留 |
| 执行完成后丢响应 | 保留 submit 异常、recover/reconcile/query 和后续读取核验 |
| 跨用户、跨租户、撤权 | 不泄露他人 Trace，过期身份拒绝查询 |
| 无 maintenance:diagnose | Trace、健康及运行检测入口均拒绝 |
| 业务事务内异常 | 业务回滚，独立日志保留 started/rolled_back/failed |
| 敏感输入及异常 message | 日志不包含测试正文、密码及异常中的秘密；保留堆栈位置 |
| 子进程进入节点后 kill | 重开日志库仍有 started，标为 open，不伪造 returned |
| 异步任务取消 | 保留 cancelled 记录 |
| Worker 心跳变旧、向量依赖关闭 | Worker 不可用、long_term 降级，检测不改变业务 Task |
| 依赖探测超时、提供方未配置 | 分别返回 timeout 和 unknown |
| 向量检索失败但 Working 有结果 | 同一 Trace 可见依赖失败与 Recall degraded 输出 |
| 日志数据库写入异常 | 正式保存仍提交；丢日志计数、not_ready 显式暴露 |
| 保留上限及到期清理 | 分页无重复，超限/过期记录清理，声明保留范围 |
| CLI 请求失败 | stderr 给出 trace_id，可通过 CLI 查到失败节点；health 返回实际探测 |

表中部分场景由同一测试覆盖，因此场景数不等于测试函数数。

复现：

```text
python scripts/p3/validate_flows.py --report docs/p3/acceptance/reports/observability-validation.json
```

测试只操作独立临时数据库；没有修改用户业务数据，也没有部署外部资源。Windows 本地运行通过，远端 CI、Linux 和 Azure 尚未执行。

## 已知边界

- 本批为本地追踪存储与 CLI/Python 查询，没有 HTTP Host、OTLP 导出、Application Insights、容器或 Azure 部署。
- 日志与业务事务独立，不能提供跨库原子日志保证；Trace 可因进程退出、磁盘失败或保留清理而不完整。
- 返回前与运行检测的授权依赖业务数据库；身份存储不可用时详细查询会失败关闭，liveness 可独立响应。
- 只读探针和隔离探针不代表完整业务正确性；readiness 不自动重启进程或拒绝 CLI 调用。
- 日志默认不保存原始业务正文。日志保留不是业务数据保留策略，日志导出也不是数据库备份。
- 恢复继续依赖持久任务、授权、版本和外部执行证据，不能依据日志缺失认定未执行。
- 真实 LangMem 模型、Milvus、生产分层、性能指标及完整 PRD 验收继续保持待运行验证。
