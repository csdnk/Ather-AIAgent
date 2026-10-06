# P3 四模块架构与子图目录

[当前完整可编辑图集](P3_整体架构与完整流程.excalidraw) · [当前 Working / Lite 独立图](../../../AgentJYS-main/docs/p3/architecture/Working与Milvus_当前流程.svg) · [独立图可编辑源](../../../AgentJYS-main/docs/p3/architecture/Working与Milvus_当前流程.drawio) · [独立图 PNG](../../../AgentJYS-main/docs/p3/architecture/Working与Milvus_当前流程.png)

**[完整可编辑图集 V1.4](P3_整体架构与完整流程_Working向量化_V1.4.excalidraw)** · [V1.4 完整图 SVG](P3_整体架构与完整流程_Working向量化_V1.4.svg) · [V1.4 完整图 PNG](P3_整体架构与完整流程_Working向量化_V1.4.png)：保留 2026-09-30 的需求设计批次及其导出。

2026-10-01 更新：同步修正 `01_remember.excalidraw`、`02_recall.excalidraw` 和 `P3_整体架构与完整流程.excalidraw` 中的 Working 路由。Working 的语义召回同样经过 Query 编码与向量检索；这三份当前原生图中不再存在 Working 直接跳入 ContextPack 的旁路。真实本地实现与验证范围见[官方 Milvus Lite 本地开发指南](../../../AgentJYS-main/docs/p3/development/Working与Milvus_本地开发指南.md)和[验收报告](../../../AgentJYS-main/docs/p3/development/Working与Milvus_验收报告.md)。

2026-09-30 的 V1.4 设计批次按已确认的“先可靠保存、异步向量化”方案修订 Working，红色文字与连线标明该批次变化。完整方案见[Working 记忆向量召回变更设计](../Working记忆向量召回变更设计_20260930.md)，需求见[PRD V1.4](../../PRD版本/AetherBrain_P3_PRD_Working向量化版_V1.4.docx)。V1.4 图中节点表达目标设计，不表示业务代码或真实环境已验收。

图集基底仍为 2026-09-21 的整体设计，保留原有 P2、缓存、LangMem、关系组和公共底座内容。2026-10-01 更新集中于 Working 相关节点与连线；其他目标设计不因此成为当前已验收能力。需要了解本次实际链路时，优先阅读上方 Working / Lite 独立图。

## 当前四模块

| 一级模块 | 当前图与 V1.4 设计图 | 阅读内容 |
|---|---|---|
| P3 整体 | [00_overview](00_overview.excalidraw) | P3.1 系统交接；P3.2 租户授权；P3.3 事务、任务、事件与恢复；P3.4 日志与 Trace；P3.5 健康与恢复；P3.6 数据与部署。本模块沿用原图，2026-10-01 未重画。 |
| Remember | [当前 01_remember](01_remember.excalidraw)；[V1.4 源图](01_remember_Working向量化_V1.4.excalidraw) · [SVG](01_remember_Working向量化_V1.4.svg) · [PNG](01_remember_Working向量化_V1.4.png) | B.1 保存；B.2 内容与记录；B.3 正文缓存；B.4 后台加工；B.5 LangMem 提取；B.6 Working/E/S 向量索引；B.7 生命周期；B.8 异常恢复；B.9 能力；B.10 交接。当前 B.3 为保存确认 → Temporal 投影任务 → Passage 编码 → Milvus 写入 → B 发布 Ready；B.6/B.10 包含 Working 投影。 |
| Recall | [当前 02_recall](02_recall.excalidraw)；[V1.4 源图](02_recall_Working向量化_V1.4.excalidraw) · [SVG](02_recall_Working向量化_V1.4.svg) · [PNG](02_recall_Working向量化_V1.4.png) | A.1 主流程；A.1a Query 编码与向量查询；A.1c 记忆候选聚合；A.1b 权威正文读取；A.1d ContextPack 组装；A.2 历史结果；A.3 恢复；A.4 能力；A.5 交接。当前仅 Working 路由接回 A.1a 编码与搜索，来源过滤先于 Top K，候选再进入正文读取与组包。 |
| Operate | [03_operate](03_operate.excalidraw) | C.1 聚合准入；C.2 对账调度；C.3 执行核验与地址交接；C.4 内部能力；C.5 跨模块交接。本模块沿用原图，2026-10-01 未重画。 |

2026-10-01 更新的完整图与 Remember、Recall 两张独立图，其相关节点使用相同 ID，保留原文件格式、既有内容和版本历史字段；修改的元素递增版本。V1.4 批次的 Remember、Recall 独立图与 V1.4 完整图也使用相同元素 ID 和内容，仅整体坐标不同。两个批次分别保留，不能将其中一批的导出当成另一批的当前导出。Recall 沿用 A.1b 原编号，实际阅读顺序为 **A.1a → A.1c ↔ A.1b → A.1d**。

## V1.4 设计变化

| 位置 | V1.4 设计 |
|---|---|
| B.1、B.3 | 正文、来源和元数据可靠保存后返回 saved，同事务登记 Working 向量任务。正文缓存成功不代表向量 ready。 |
| B.6、B.10 | Working 与 E/S 共用编码和向量能力；Working 任务不等待长期提取阈值。全部预期块、当前版本、类型、模型空间与指纹核验后发布 ready。 |
| B.7、B.8 | 更正、摘要更新、删除及撤权先影响资格；物理清理异步完成。历史 Working 显式、幂等补索引，迟到任务不得复活旧版本。 |
| A.1 | 仅 Working 分支保留来源及任务限制，并接入统一向量步骤，不再直达正文组包。 |
| A.1a、A.1c | Query 编码后按可信租户、授权、来源类型、会话与任务查询向量，核验 ready 后按记忆聚合候选。 |
| A.1b、A.1d | 读取准确全文；按各来源向量记忆名次排序；交付前再次核验当前 ready、权限、版本、类型与关系。 |

2026-09-30 的 V1.4 图示修订保持全部原元素 ID、用户节点位置及模块布局，定点修改 31 个文本元素，重接 Working 分支的一条箭头并同步绑定。该批次以新文件保留，旧 PRD 保留；当时未覆盖用户可能仍在编辑的画布。未带 V1.4 后缀的完整图、Remember 和 Recall 已在 2026-10-01 更新，现应按其当前 Working 向量化内容阅读。

## 关键设计与当前 Working 边界

1. Working、Episodic、Semantic 均建立向量检索表示。Working 进入向量库不改变任务范围、生命周期和业务类型，也不自动成为长期事实。
2. saved 只确认可靠保存；ready 确认当前版本的完整索引通过核验。Working 向量准备独立于长期事实提取，长文先完成适用的正文整理或工作摘要。索引 pending/failed 必须计入来源覆盖；无可交付内容时，不能把索引尚未完成伪装成正常 empty。
3. 仅 Working 表示只搜索 Working，仍执行 Query 编码和向量查询；不自动改查长期。Working 使用向量相关性候选，不再使用旧词法/时间排序旁路。单来源按向量记忆名次组织，多来源在记忆层融合。时间窗限定范围，不能代替向量匹配。
4. Working 和长期的 Query/Passage 均绑定兼容模型空间。来源、可信租户/授权范围和模型空间在向量 Top K 前过滤；Remember 再核验对象资格、版本、generation 和正文指纹。
5. chunk 用于匹配；主候选按记忆 ID 计数。旧版本和未发布批次不参与合格候选评分。关系补全成员单独记录，全部正文计入 ContextPack 预算。
6. 读取使用 ID 和精确版本，缓存缺失可回源。按已知 ID + 精确版本授权读取正文是独立能力，不依赖 Query 编码；正文回源与按 ID 查看获准内容均不能代替 Recall 的语义候选检索。基础正文读取及后台加工资格不以 ready 为前提。
7. 没有匹配、索引未就绪和依赖故障分别表达。未就绪或故障不得静默使用词法或直接列表路径报告成功。存在可信部分则说明覆盖限制；没有可信输出则明确原因。
8. 完整 Memory 或必需冲突组用于装包。预算不足则整组跳过，不静默截断；最终核验覆盖全部成员及关系修订，条件提交结果和事件。
9. 更正、摘要更新、归档、到期、删除及撤权约束索引发布、候选、回填和交付。Milvus 中的向量与引用不替代权威正文；历史重新获取同样重验当前资格。更正、撤权、删除及显式到期会使旧结果失效。
10. read、packed 分别记录真实阶段；向量命中不等于实际读正文，packed 不重复计热。日志不替代持久业务事实。
11. 官方 Lite 使用回环监听、单服务工作线程和 P3 写串行。本地功能验证不代表生产 Milvus Standalone/集群、性能、长稳或产品验收。

## 设计与历史参考

- [当前完整图](P3_整体架构与完整流程.excalidraw)、[当前 Remember](01_remember.excalidraw)、[当前 Recall](02_recall.excalidraw)：沿用 2026-09-21 底稿并已在 2026-10-01 修正 Working 路由；不再作为 Working 旁路的旧原稿阅读。
- [V1.4 完整设计图](P3_整体架构与完整流程_Working向量化_V1.4.excalidraw)、[V1.4 Remember](01_remember_Working向量化_V1.4.excalidraw)、[V1.4 Recall](02_recall_Working向量化_V1.4.excalidraw)：2026-09-30 的需求设计批次及红色变更标记。
- [A.1c 候选聚合历史设计预览](A1c_记忆候选聚合.png)、[A.1d ContextPack 历史设计预览](A1d_ContextPack组装.png)：2026-09-21 局部图，不是 2026-10-01 当前流程导出；当前 Working 路径以更新后的 Recall 原生图和 Working / Lite 独立图为准，V1.4 设计细节见对应设计图。
- [块命中到记忆候选与正文组包规则](../P3_块命中到记忆候选与正文组包规则_V0.1.md)：仍为设计提案，Working 的旧旁路及时间排序描述已校正。
- [公共底座详细参考](P3_公共底座详细参考.excalidraw)、[底座故障时间线与代码对应](P3_公共底座详解_故障时间线与代码对应表.md)：历史设计/代码说明，保持原有批次范围。旧时间线“保存后 Working 可用”不能代替自身向量 ready 条件。
- [Azure 监测与恢复方案](../Azure监测与恢复接入方案_20260921.md)
- [PRD V1.3](../../PRD版本/AetherBrain_P3_PRD_租户补全版_V1.3.docx)、[V1.3 流程一致性检查](../P3_流程图与PRD_V1.3一致性检查_20260921.md)：历史基线及当时的差异记录。
- [开发协同规范](../../开发协作/02_协同开发规范.md)、[当前实现登记](../../开发协作/01_当前实现与待办.md)
- [旧 basic Recall 参考](../Recall处理流程/README.md)：包含当时的词法 Working，已明确标为历史。
- [19 分图历史](../../历史交付/P3全景流程_19分图_20260921/README.md)、[精简四图历史](../../历史交付/P3四张主图_20260921/README.md)。

## 历史导出检查与使用

2026-09-30 的 V1.4 三个图源已检查 ID 集合、连线引用及节点位置；该批次 SVG 的文字边界、重叠、箭头穿字和实际中文字体检查通过，并目视核对保存、Working 分支、向量发布、正文读取与组包区域。上述是 V1.4 历史批次的图示检查，不是对 2026-10-01 更新的原生图或本次文档合并重新执行的验证。过程记录保存在项目外工作区。

V1.4 Excalidraw 保留原生元素与字体设置。该批次 SVG 保留可编辑文字和路径，使用微软雅黑；静态导出保留节点位置和连接，采用规则线条。V1.4 PNG 为透明背景，尺寸分别为：完整图 11450×17936、Remember 5496×7716、Recall 5496×12236。完整图尺寸较大，可优先阅读独立 SVG 或在 Excalidraw 选中模块后按 Shift+2 聚焦。

2026-10-01 当前 Working / Lite 独立图提供 draw.io、SVG 和 PNG，文字和连线保持可编辑。未带 V1.4 后缀的完整图与 Recall 原生图已更新，但目录未提供对应本次更新的完整图/Recall SVG、PNG，因此不将旧导出列为当前入口。原始渲染与检查记录存于外部过程目录，未替换用户浏览器中的未保存画布。

V1.4 批次为需求和图示修订。业务实现、本地验证、真实 P2/Milvus 接入与产品验收分别记录，不能以设计文件或图形检查通过替代；2026-10-01 的真实本地实现及验收范围以开发指南和验收报告为准。
