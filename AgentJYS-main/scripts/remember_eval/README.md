# Remember 历史评测工具（归档）

本目录不是当前 Remember 流程的验收入口。`paired_benchmark.py` 保留用于追溯历史实验，其要求的 `p3_pipeline:run` 已不在仓库中，不能直接运行示例并据此判断当前产品是否通过。

历史接口使用 `precompression_enabled`、Celery 专用 Worker、LLM 前置压缩及额外审查阶段。这些均不代表当前生产机制。当前流程使用现有 Outbox、任务执行和 Temporal；长 Working 默认先经 LLMLingua 生成派生物，再进入 LangMem 两阶段。短消息直接整理，长消息也可显式选择 `long_memory_route=direct`。旧布尔开关不能选择这两条路径。

## 若后续恢复对照评测

需要先重写生产管线适配及脚本的路线参数，不能仅补回同名 hook：

1. 每个样本、每条路线建立独立隔离作用域，使用相同原始输入、模型和整理参数，只显式切换 `long_memory_route`。
2. 使用真实保存入口和现有 Temporal 任务，等待全部输入块处理完成。LLMLingua 只作用于达到 `compression_min_bytes` 的单条原文，不能人为把所有短消息变成长文本。
3. 从存储读回已提交的 semantic/episodic 正文及来源归属；不能把候选结果、未发布派生物或任务失败当作成功。
4. 分别记录 LLMLingua 的中间压缩比，以及原始 Working 字节数除以其最终长期记忆正文总字节数。5X 指后者；空输出单列，不当作无限压缩成功。不足 5X 也不删除事实。
5. 分开报告短消息、长消息两条路线、重试及失败。禁止失败后静默换路线。评测使用的独立事实评分不接入生产压缩审查。
6. 保留 QA、事实标签的来源和局限；QA 得分不是穷尽事实准确率，测试替身也不能证明真实模型质量。

评测应在 Docker/Azure 的隔离环境运行。临时端点和密钥仅通过环境或秘密挂载注入，不写入源码、镜像或结果。旧脚本的 `--dry-run` 只能校验历史数据格式，不能证明当前链路可运行。

现行流程和保留兼容项见 [Remember 历史能力退出说明](../../docs/p3/remember_legacy_retirement.md)。
