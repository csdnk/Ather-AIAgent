# 公共底座首批运行验收

交付日期：2026-09-19。依据：PRD V1.2及已交付的RF/B/A/C协作契约。范围：单机SQLite共享运行底座和工程样例。指标体系、面板及产品性能目标暂缓。

## 结果

可信上下文、统一事务、持久任务、Outbox/Inbox/Delivery、最小追溯和恢复已实现。样例输入可由独立Worker处理，并能在重启后找回任务、识别过期租约、由Handler判断恢复、阻止迟到提交。

本地运行检查通过：33项运行测试、189项既有契约测试、72个Schema一致性、代码检查、静态类型检查，以及独立目录工程演示。自动结果及文件摘要见[foundation-validation.json](foundation-validation.json)。CI已配置同一入口，远端CI和Linux实际部署尚未执行。

## 关键证据

| 场景 | 实际验证方式 | 观察结论 |
|---|---|---|
| 原子提交 | 写完输入、Task及Outbox后抛错 | 三者均回滚 |
| 请求幂等 | 同key同内容重复提交；同key改内容 | 返回原任务；不同内容被拒绝 |
| 租户与用户隔离 | 两租户各两用户，查询和恢复越权尝试 | 越权被拒绝；显式同租户授权可查询，撤销后拒绝 |
| 撤权与旧配置 | 身份epoch升级，再执行旧任务和加载旧配置 | 停止旧主体处理；拒绝旧epoch配置 |
| 持久恢复 | 子进程领取后kill、事务写入后kill、提交后kill | 未提交数据回滚；已提交数据保留；重启后结果唯一 |
| 并发领取 | 两个独立进程竞争一个任务 | 只有一个持有效租约领取成功 |
| 迟到提交 | 旧租约恢复后提交；提交事务内时间越过租约 | 提交拒绝并回滚 |
| 续租与超时 | 实际异步等待超过初始租约；超过请求截止 | 持续续租后正常提交；超时无假成功 |
| 丢失事件确认 | 消费效果与Inbox提交后丢ACK | 根据Inbox补齐确认，效果只有一次 |
| 消费中断 | 消费写入后抛错，再投递 | 效果与Inbox一起回滚，恢复后一次生效 |
| 结果不明 | 独立SQLite模拟执行端先执行后丢响应 | Unknown后查询同action_id，确认原效果，未重复提交 |
| 模拟端无证据 | 查询持续返回未知 | 有限查询后attention_required，不猜测成功 |
| 人工恢复 | 有效租约、过期租约及expected_revision校验 | 不抢占有效执行；记录受理、原因与最终结果 |
| 类别及租户限额 | 一个租户到达队列限制，另一租户提交和领取 | 前者被限制，后者可继续 |
| 在线与后台隔离 | 同scope同时领取后台及在线类别任务 | 后台不占用在线类别执行名额 |
| 状态真实性 | 查询业务能力健康 | 未接入能力标记unknown |

## 代码与复现

维护代码位于`src/aether_agent_memory/runtime/foundation/`，复用现有`runtime/capability_store.py`；测试位于`tests/runtime/p3/`。在Python 3.13隔离环境执行：

```text
python -m pip install -r scripts/p3/requirements.txt
python scripts/p3/validate_foundation.py --report docs/p3/acceptance/reports/foundation-validation.json
python scripts/p3/demo_foundation.py --directory <新的运行目录>
```

故障测试使用独立临时数据库，不操作用户运行数据库。原始测试输出与临时演示目录在校验进程的系统临时目录，正式目录只保存代码、说明和摘要。已有未提交代码保留，未执行全量旧应用测试。

## 尚未完成

- 真实Remember、Recall和Operate的Handler及领域规则，实际模型、Milvus和调度Provider。
- 新产品HTTP Host装配、OTel导出和Azure监测接入、真实依赖探针。
- 容器与Azure部署、备份及隔离还原、多主机任务调度；SQLite只支持当前单机边界。
- 生产规模公平调度、数据保留及历史事件回放。当前采用有界执行和租户限额，但未声称生产容量。

工程事件`engineering.saved`和样例结果均明确标为`engineering_only`；故障测试的外部动作明确标为simulated，不能替代正式调度验收。下一步按[底座实现与运行说明](../../development/04_公共底座首批实现与运行.md)完善服务装配和部署能力，并让各流程接入自己的Handler。
