# P3 四模块架构与子图目录

[完整可编辑图集](P3_整体架构与完整流程.excalidraw) · [当前 Working / Lite 独立图](../../../AgentJYS-main/docs/p3/architecture/Working与Milvus_当前流程.svg) · [独立图可编辑源](../../../AgentJYS-main/docs/p3/architecture/Working与Milvus_当前流程.drawio) · [独立图 PNG](../../../AgentJYS-main/docs/p3/architecture/Working与Milvus_当前流程.png)

2026-10-01 更新：同步修正 Remember、Recall 和完整图中的 Working 路由。Working 的语义召回同样经过 Query 编码与向量检索；图中不再存在 Working 直接跳入 ContextPack 的旁路。真实本地实现与验证范围见[开发指南](../../../AgentJYS-main/docs/p3/development/Working与Milvus_本地开发指南.md)和[验收报告](../../../AgentJYS-main/docs/p3/development/Working与Milvus_验收报告.md)。

图集基底仍为 2026-09-21 的整体设计，保留原有 P2、缓存、LangMem、关系组和公共底座内容。此次只更新 Working 相关节点与连线；其他目标设计不因此成为当前已验收能力。需要了解本次实际链路时，优先阅读上方独立图。

## 四模块与本次变更

| 模块 | 入口与范围 |
|---|---|
| P3 整体 | [00_overview](00_overview.excalidraw)：系统、授权、事务、追踪、健康与部署；本次未重画 |
| Remember | [01_remember](01_remember.excalidraw)：B.3 现为保存确认 → Temporal 投影任务 → Passage 编码 → Milvus 写入 → B 发布 Ready；B.6/B.10 包含 Working 投影 |
| Recall | [02_recall](02_recall.excalidraw)：仅 Working 路由接回 A.1a 编码与搜索；来源过滤先于 Top K，候选再进入正文读取与组包 |
| Operate | [03_operate](03_operate.excalidraw)：事件、调度、核对与跨模块交接；本次未重画 |

完整图与两张独立图的相关节点使用相同 ID，保留原文件格式、既有内容和版本历史字段；修改的元素递增版本。Recall 阅读顺序为 A.1a → A.1c ↔ A.1b → A.1d。

## Working 当前边界

1. 保存回执与向量 Ready 分开；索引 pending/failed 必须计入来源覆盖。无可交付内容时，不能把索引尚未完成伪装成正常 empty。
2. Working 和长期的 Query/Passage 均绑定兼容模型空间；Working 使用向量相关性候选，不再使用旧词法/时间排序旁路。指定 Working 不自动改查长期。
3. 来源、租户/授权范围和模型空间在向量 Top K 前过滤；Remember 再核验对象资格、版本、generation 和正文指纹。
4. 按已知 ID + 精确版本授权读取正文是独立能力。它不依赖 Query 编码，但不能代替 Recall 的向量候选发现。
5. Milvus 中的向量与引用不替代权威正文；历史结果重取也按当前授权和生命周期核验。更正、撤权、删除及显式到期会使旧结果失效。
6. 官方 Lite 使用回环监听、单服务工作线程和 P3 写串行；本地功能验证不代表生产集群、性能或长稳验收。

## 设计与历史参考

- [块命中、记忆候选与完整正文组包规则](../P3_块命中到记忆候选与正文组包规则_V0.1.md)：仍为设计提案，Working 的旧旁路描述已校正。
- [A.1c 候选聚合历史设计预览](A1c_记忆候选聚合.png)、[A.1d ContextPack 历史设计预览](A1d_ContextPack组装.png)：2026-09-21 局部图，不是本次当前流程导出。
- [公共底座详细参考](P3_公共底座详细参考.excalidraw)、[故障时间线与代码对应表](P3_公共底座详解_故障时间线与代码对应表.md)：保持原有批次范围。
- [旧 basic Recall 参考](../Recall处理流程/README.md)：包含当时的词法 Working，已明确标为历史。
- [19 分图历史](../../历史交付/P3全景流程_19分图_20260921/README.md)、[精简四图历史](../../历史交付/P3四张主图_20260921/README.md)。

Excalidraw 原生文件可在编辑器中打开；选中模块后按 Shift+2 聚焦。当前独立图提供 draw.io、SVG 和 PNG，文字和连线保持可编辑。目录未提供的完整图/Recall SVG、PNG 不再列为链接。原始渲染与检查记录存于外部过程目录，未替换用户浏览器中的未保存画布。
