# P3 目标架构与流程图

**[完整可编辑图集](P3_整体架构与完整流程.excalidraw)** · **[PRD 依据、设计选择与实现差距](PRD目标与实现差距.md)**

2026-09-21 按用户要求调整：这套图用于**先确定完整流程，再按流程开发**。产品依据为[PRD 租户补全版 V1.3](../../PRD版本/AetherBrain_P3_PRD_租户补全版_V1.3.docx)。主图展示应实现的行为；代码缺口单独登记，不因未实现而省略目标步骤。实线表示目标处理关系，不表示已经实现或验收。

白底黑线、短节点、直角连接的风格保持不变。公共运行底座简称 RF；B 为 Remember，A 为 Recall，C 为 Operate。接口编号对应协作图，不表示全局执行顺序。

## 阅读顺序

| 分图 | 目标内容 |
|---|---|
| [00 全景](00_overview.excalidraw)、[05 租户](05_tenant.excalidraw) | 业务入口、三流程、可信身份、授权、停用撤权 |
| [01 Remember](01_remember.excalidraw)、[10 协作](10_remember_links.excalidraw) | 保存、资格、变更、删除及跨流程交接 |
| [16 加工展开](16_remember_processing.excalidraw) | 长文、稳定事实、压缩按规则选择或组合 |
| [02 Recall 主线](02_recall.excalidraw)、[11 协作与 RRF](11_recall_links.excalidraw) | 来源、候选、资格、排序、完整组和交付 |
| [13 冲突组与预算](13_conflict_budget.excalidraw) | 成员补全、保留分歧、整组计数和跳过超长组 |
| [14 原请求和历史结果](14_history_result.excalidraw) | 查原状态、重验旧结果、明确新请求边界 |
| [15 Recall 恢复](15_recall_recovery.excalidraw) | 查证原结果、安全继续或真实失败 |
| [03 Operate](03_operate.excalidraw)、[12 协作](12_operate_links.excalidraw) | 历史与当前状态、算法、动作与对账 |
| [17 真实执行](17_real_execution.excalidraw) | 真实目标、准确读取、Unknown 与独立清理 |
| [18 预测增强](18_prediction.excalidraw) | FR16 后续 P1，复用执行闭环，不阻塞 MVP |
| [04 Embedding](04_embedding.excalidraw) | 编码、空间绑定、推理与证据 |
| [06 公共恢复](06_recovery.excalidraw) | 事务、任务、事件、租约和领域恢复 |
| [07 Trace](07_trace.excalidraw)、[08 检测](08_health.excalidraw) | 节点记录、能力探测、告警、恢复及复查 |
| [09 部署与责任](09_deployment.excalidraw) | Azure、持久化、AKS 演进与分工 |

## 三项关键纠正

- **历史结果重新获取是需求。** FR11 要求查询原处理不隐式创建新业务，旧正文返回前重验当前资格。`final_guard` 是本方案接口名。
- **恢复或明确失败是需求。** FR18、12.7 要求安全继续原业务或给出真实失败及可采取动作。PRD 没有要求所有中断都终止，也没有强制逐节点原地续跑。
- **完整冲突组是需求。** FR09/FR10 要求保留有效分歧及来源，整组装入；放不下继续尝试后组。“目前只支持单条组包”已移到实现差距表。

## 跨流程边界

B 提供正文、版本、来源、替代/冲突关系和使用资格。关系及成员补全能力属于待补齐的目标契约，不表示现有 MemoryReadPort 已全部提供。A 使用这些材料构造完整 ContextPack，候选阶段、提交前和旧结果重取均检查资格；可选 CrossEncoder 排序不能裁决事实真伪。

B 调用 A 负责的共享编码与向量能力，但由 B 核验 ready。B/A 轨迹经 RF 交给 C；成功 read 去重计热、packed 不重复计热是当前算法方案，PRD 只规定阶段可区分、不得重复计数。C 向 B 核验资格、向真实执行端查询状态和证据。RF 提供公共机制，领域定义业务恢复判定。

## RRF 说明

RRF（倒数排名融合）只合并来源排名。图11示例：Working 返回 A、B，长期返回 B、C，每路按 `1/(60+rank)` 加分，名次从1开始；B≈0.0325、A≈0.0164、C≈0.0161，合并为 B、A、C。60是本例平滑参数。

同一记忆按完整标识与版本合并；关系级去重、冲突组和资格检查还需继续执行。RRF 不调用大模型，也不替代整个 Recall。条数、token预算、tokenizer及模型维度均是实现配置，不是 PRD 固定值。

## 查看与检查

将图集或分图拖入 [Excalidraw](https://excalidraw.com/)。点击分区顶部名称后按 Shift+2 放大，Esc 取消选择，空格拖动画布。网页保留旧彩色图，黑白目标图放在独立区域。

19张图检查了元素唯一性、分区与箭头引用、文字宽度、直角路径及连接不穿过非目标节点。13中的虚线表示还有候选时继续尝试的回路。未实施业务运行或云部署验证；指标执行暂缓，不删除 PRD 产品要求。
