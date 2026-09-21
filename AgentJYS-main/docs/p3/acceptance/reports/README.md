# 最终校验摘要

本目录保留历史批次摘要；2026-09-20 Recall/CrossEncoder/Milvus适配与后续协作基线验收位于交付包 `交付成果/测试与验收/`。不能把下面的旧批次当成当前完整状态。

此前维护批次：[旧代码清理验收](cleanup-delivery.md)，包含删除前检查、逐文件清单、清理后现有流程回归及真实 Embedding 验证。本次不增加业务能力，也不改变以下批次各自的通过范围。

此前运行批次：[真实 Embedding 接入三流程验收](native-embedding-delivery.md)，包括真实 BGE CPU 模型、SQLite 投影和三流程联调。此前批次为[日志追踪与健康检测验收](observability-delivery.md)、[三个流程基础实现验收](flows-delivery.md)及[公共底座首批验收](foundation-delivery.md)。各批次分别记录；真实 Embedding 通过不代表 LangMem、Milvus、生产存储及完整 PRD 验收。

本目录仅保存正式交付摘要。[自动校验报告](contract-validation.json)记录本次自动检查结果、环境和交付文件摘要；[交付说明](delivery.md)说明实际通过范围与未执行项。

原始工具输出、临时报告、提取文本和环境缓存放在仓库外的工作目录。各报告的通过范围以对应批次实际运行证据为准；设计／契约通过不能代替运行测试，单机运行通过不能代替 Azure 或产品验收。
