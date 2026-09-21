# Recall 与 Embedding 流程实现说明

日期：2026-09-20。本文描述本次实际代码增量。业务代码保存在 `AgentJYS-main`，原始测试记录与模型下载保存在项目外工作区。

## 已实现的流程

```text
可信上下文与请求幂等
    ↓
选择 Working / 长期来源
    ↓
独立来源检索（并发、来源超时、范围过滤）
    ↓
确切版本加载与投影匹配；有界补取被旧向量挤占的候选
    ↓
按记忆引用与版本去重，RRF 融合
    ↓
重排序前重新核验授权与有效性
    ↓
CrossEncoder 重排序（disabled / required / fallback 策略）
    ↓
真实 tokenizer 计数、完整记忆装配、最多 5 条
    ↓
最终授权、版本、删除及来源有效性核验
    ↓
同事务提交 ContextPack、请求完成状态与实际访问事件
```

本次实现借鉴了 Retriever、候选融合、后处理和 CrossEncoder 的模块边界；没有把 LlamaIndex 或 Haystack 加成新的主编排依赖。原有真实 BGE Embedding 和 RF 任务、事件、恢复、Trace 继续复用。

## 模块与责任

以下链接直接指向可维护源码：

| 模块 | 责任 |
|---|---|
| [retrievers.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/retrievers.py) | Working 与长期候选发现；默认各取 20，长期必要时扩大到最多 100；来源超时和覆盖状态 |
| [components.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/components.py) | RRF 融合及上下文装配；同一路重复候选不增加分数，也不改变后续独立候选的排名 |
| [reranking.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/reranking.py) | Sentence Transformers CrossEncoder CPU 推理；输出与输入位置一一对应；有限并发、迟到计算管理 |
| [tokenization.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/tokenization.py) | o200k_base 等 tiktoken encoding 或本地 Hugging Face tokenizer.json；关闭静默截断 |
| [milvus.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/milvus.py) | Milvus VectorPort：范围过滤、投影意图、远程写入、查询核验、删除确认、健康探测 |
| [config.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/config.py) | 服务端配置，拒绝未知字段和不一致的重排序参数 |
| [service.py](../../AgentJYS-main/src/aether_agent_memory/recall/basic/service.py) | 请求身份、流程装配、阶段状态、最终核验、结果与事件原子提交 |
| [host.py](../../AgentJYS-main/src/aether_agent_memory/runtime/flows/host.py) | provider 装配、模型/存储绑定、健康探测和资源退出 |

Remember 的向量清理也已补充结果检查：只有 VectorPort 返回 `absent` 才记录清理完成；未知或尚存在的投影继续走原有任务恢复机制。

## 配置与使用

| 配置文件 | 行为 |
|---|---|
| [recall.local.json](../../AgentJYS-main/configs/recall.local.json) | 真实 token 预算；保留本地 SQLite 向量检索；明确关闭重排序 |
| [recall.rerank.local.json](../../AgentJYS-main/configs/recall.rerank.local.json) | SQLite 向量检索＋真实 BGE CrossEncoder；重排序为必需步骤 |
| [recall.milvus.json](../../AgentJYS-main/configs/recall.milvus.json) | Milvus＋真实 CrossEncoder；Milvus 地址和集合名由服务端配置 |

业务默认入口仍保持可运行的本地配置，不会静默引入一个未配置的外部 Milvus。启用完整重排序链需要显式选择配置文件。配置中的 BGE 重排序权重固定到 commit；模型下载和首次加载时间不计作日常请求的正常响应时间，应先预热。

从代码仓库根目录，在 Python 3.13 环境中安装验证依赖：

```text
python -m pip install -r scripts/p3/requirements-recall.txt
```

验证真实 Embedding＋重排序＋tokenizer＋本地三流程：

```text
python scripts/p3/validate_recall.py --recall-config configs/recall.rerank.local.json
```

已有 Milvus 时，修改 `configs/recall.milvus.json` 的地址，再执行：

```text
python scripts/p3/validate_recall.py --recall-config configs/recall.milvus.json
```

验证脚本自动创建隔离的临时业务库与演示身份，验证写入、召回、纠错、删除、用户隔离和自动缓存动作，不覆盖已有数据库。首次运行需要取得配置对应的真实模型。`--report` 可输出原始机器证据，建议指定项目外工作区。

正常服务入口增加全局参数 `--recall-config`；原有 `remember`、`recall`、`worker`、`trace`、`health` 等命令继续使用。新增 `warmup` 命令，在已有有效维护凭证下加载重排序模型并准备向量集合。Milvus 凭证从 `P3_MILVUS_TOKEN` 读取，不写入配置文件或日志。

## 行为与故障约束

- **重排序策略：** `disabled` 明确记录未启用；`required` 失败则请求失败；`fallback` 仅对可恢复依赖失败或重排序超时回退到原融合顺序，并返回 `degraded` 及原因。非法分数、数量不匹配等契约错误不能静默回退。
- **真实预算：** 默认使用 `o200k_base` 计算整个 `rendered_context`，包括引用序号和分隔符。预算是调用方分配给 ContextPack 的预算，不包含下游消息模板和模型回答预留量。切换接收模型后，应配置其对应 encoding 或 tokenizer.json。
- **推理输入：** CrossEncoder 检查查询与候选配对后的 token 长度；超过配置上限明确失败，不静默截断。CPU 推理超时后，实际计算仍可能继续，该计算完成前不会释放其占用的推理槽位。
- **来源失败：** 一路超时不伪装为该路“无结果”；其他来源有有效内容且策略允许时，返回有原因的降级结果；所有可用来源均不足时失败。
- **候选旧版本：** 回 Remember 读取确切版本、匹配内容 hash 和投影状态；必要时有界扩大候选数。不会为了凑足结果而放宽 scope 或接纳旧版本。
- **取消与重启：** 调用方取消会落库为失败；进程中断后，原有过期扫描终止未完成请求。不会重放旧正文冒充新的成功结果。
- **Milvus 一致性：** SQLite 保存投影意图，Milvus 写入与 SQLite 不是跨库原子事务。写入响应丢失返回 Unknown；原有恢复 Handler 按原目标查询。只有精确 payload 和查询可见性均核验通过，才能标记投影可用。
- **删除：** 逻辑删除与版本核验先阻断召回；Milvus 删除需查询确认不存在。持久删除标记阻止后续旧投影任务重新提交。外部迟到写入仍需按持久状态对账，不能把单次删除响应当作永久物理清理证明。
- **存储切换：** 记录向量后端绑定。已有 SQLite 投影的业务库不能直接改配置切到空 Milvus；必须显式迁移/重建或使用新库。本次未实现已有库的一键迁移工具。

## 日志、Trace 与阶段证据

`trace_id` 继续覆盖 Recall、来源检索、Embedding、VectorPort 与重排序调用。成功、失败、输入输出摘要进入已有独立日志库；模型文本不默认写入技术日志。

新增 `recall_stages` 持久记录包含候选引用、排除数量、融合/重排序分数、模型标识、tokenizer 标识与结果状态。通过已有授权 Trace 查询的 `durable_facts.recall_stages` 返回；只有原发起者且具有诊断权限才可查询，不绕过对象 scope。阶段表保留当前阶段结论，技术日志保留执行过程。

健康检测增加 tokenizer 和 reranker 项。重排序未启用显示 disabled；尚未加载显示 unknown；加载后才报告可用。必需重排序不可用时，不将相应召回能力显示为完整可用。

## 本次边界

本次补齐 Recall/Embedding 的模块实现，不替换 Remember 的 LiteralExtraction，不宣称真实 LangMem、Azure/AKS、TEI 或完整生产验收已经完成。Milvus 真实服务验证与替身协议测试分开记账；执行结果以[本批验收记录](../测试与验收/Recall流程实现验收_20260920.md)为准。
