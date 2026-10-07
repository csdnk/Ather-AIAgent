# Docker 内真实模型配对评测

本目录脚本对固定公开来源分别运行完整原文路径和预压缩路径。必须通过真实 P3 hook 读取已提交 semantic / episodic；没有 adapter-only 或假模型回退。主指标为原文 UTF-8 字节数除以两类最终正文的字节和，空输入或空输出为 null。

临时模型配置仅从 `AETHER_EVAL_TEMP_ENDPOINT`、`AETHER_EVAL_TEMP_KEY` 进程环境读取。禁止在源代码、命令参数、Docker镜像或结果文件写入真实端点/密钥。由调用方用临时环境注入，不提交 `.env`。脚本输出不记录端点或认证头。

Docker中执行示例（路径由部署绑定）：

```sh
python scripts/remember_eval/paired_benchmark.py \
  --dataset /eval_samples/dataset.json \
  --pipeline p3_pipeline:run \
  --model gpt-5.6-luna --samples 12 --qa-limit 12 \
  --max-calls 600 --output /eval/results
```

`--dry-run`同样必须在Docker运行，仅校验输入，不给质量分数。主脚本不会创建Docker容器或自动安装服务。

实际hook已提供为`p3_pipeline:run`。容器的PYTHONPATH需含`/workspace/scripts/remember_eval`和`/workspace/tests`（或相应挂载目录）。须提供`AETHER_EVAL_EMBEDDING_CONFIG`、现有测试资源工厂所需`P3_TEST_*`后端环境及`P3_CELERY_BROKER_URL`。默认启动真实、独立的Celery prefork Worker进程，经Redis broker与SQL调度执行生产阶段；worker重新连接同一隔离后端作用域。没有Temporal回退。两臂共同将compression_min_bytes置1以确保小型真实来源也进入可比较的分支；除precompression_enabled外策略一致。

实际模型HTTP请求经`AETHER_EVAL_CALL_LEDGER`指定的跨进程文件账本记录，Linux flock保护请求预算与条目；Worker没有复用父进程的模型client。不要对外分发运行环境文件、worker启动环境或模型凭据。每次运行需新的输出目录，避免覆盖既有真实请求账本。

## P3生产管线扩展点

提供可导入的 `async def run(*, sample_id, sources, precompression_enabled, model, provider, stage)`。sources只含原始id/text；不会传入问题、答案或gold。该函数必须：

1. 为每个样本/实验臂创建独立、长期记忆为空的真实P3作用域。
2. 把model交给官方LangMem adapter，把provider交给真实ModelCompression、CompressionVerifier及必要的生产事实核验。两者共享真实HTTP计数transport；不可在hook里偷偷创建未计数的新模型client。
3. 除precompression_enabled外使用同一生产策略，完整接纳相同Working、触发真实整理并等到终态。禁止通过替换抽取/质量结果达成成功。
4. 从真实存储读取最终活动semantic/episodic正文及提交凭证。禁止返回未提交的LangMem proposals。失败抛异常，不用空列表冒充成功。
5. 返回 `{"commit_readback": true, "initial_long_term_memory_count": 0, "memories": [{"text": "...", "kind": "semantic"}], "commit_evidence": {...}, "route_observed": "raw|precompress|fallback_to_raw", "compression": [...], "intermediate_compression_ratio": null}`。凭证仅含非秘密任务/记忆标识。
6. 可用 `stage.set("样本/实验臂/compression|verification|official_langmem")` 标记各模型阶段。所有请求（包括失败和内部重试）均占用总预算。

脚本先把读回的最终记忆写为可审计artifact，再测UTF-8大小。QA使用该作用域全部最终活动记忆的穷尽读取，不调用原文兜底。这避免检索漏召回干扰第一轮压缩比较；需要单独研究向量检索时再增加固定检索实验。

## 质量口径限制

原始公开SQuAD的人类QA独立于被测模型。QA EM/F1和答案带精确记忆引文的比率是代理指标，不是穷尽事实召回，也不等同于蕴含证明。当前事实精确率来自另外一次真实模型调用对完整来源审查，明确标为automated，不称人工金标准。试点`squad_1`另有运行前冻结的10个原文事实与4个条件／否定／模态金标，均附实际原文锚点；这些是研究者来源标注，未宣称独立人工裁定。它们的覆盖由另一实际模型调用评分，单列source_annotated指标。其他样本及穷尽事实／条件指标仍为null；不得将缺少标签写成100%。

无供应商可靠价格表时美元成本为null，实际调用/usage/时延仍保留。输出包含失败样本、质量拒绝与undefined比例；不可只摘选压缩成功样本。当前代码的Docker执行状态由实际运行记录说明，编写代码不代表已完成基准。
