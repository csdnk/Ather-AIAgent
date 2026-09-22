> 测试交接副本：2026-09-22；代码基线 `2ee5b0e`。正文保留原日期及原批次范围，链接已调整。当前可测范围以交接包入口及《11_RF公共底座实现与接入》为准。

# 真实 Embedding 接入与旧实现清理

日期：2026-09-19。当前默认 Embedding 已由词法基线切换为仓库已有的 BGE 中文原生 CPU 推理。此交付接通真实向量计算；提取模型、Milvus、生产存储和 HTTP 服务仍按各自阶段实现。

## 实际调用链

```text
Remember 投影任务 → EmbeddingPort（usage=passage）
Recall 在线查询  → EmbeddingPort（usage=query）
                         ↓
                 NativeP3Embedding
                         ↓
        已有 NativeEmbeddingBackend：Query / Passage 各一个实例
                         ↓
             FastEmbed + ONNX Runtime + BGE 中文权重
                         ↓
        输入绑定、模型指纹、512维、有限值、L2、截止时间核验
                         ↓
      SQLite 向量投影 / 候选检索 → Remember 最终资格核验
```

共享底座继续负责身份、事务、后台任务、恢复、日志和事件。新适配器直接复用 NativeEmbeddingBackend，没有再启动旧 SemanticEmbeddingService 的持久队列。底层推理证据复用已有 RF SQLite 存储，调用前后重新校验当前身份。

代码入口：

| 文件 | 本批变化 |
|---|---|
| `recall/embedding/p3.py` | 新 EmbeddingPort 适配、请求绑定、错误转换、推理尝试记录与健康检查 |
| `recall/embedding/native.py` | 保留原生推理机制，增加同步 Host 关闭入口，等待实际 CPU 工作结束再关数据库 |
| `recall/basic/adapters.py` | SQLiteVectors 按模型空间和维度工作，移除只接受 256 维的硬编码；拒绝非法向量 |
| `runtime/flows/host.py` | 默认 native；共享实例注入 Remember 与 Recall；模型及健康检测装配 |
| `runtime/flows/__main__.py` | 支持 `--embedding-profile` 和 `--embedding-config` |
| `scripts/p3/demo_flows.py` | 默认完整三流程真实 Embedding 演示 |
| `scripts/p3/validate_native_flows.py` | 真实模型＋真实身份/事务/向量投影/三流程/Trace/健康验证 |

Working 的局部候选排序仍采用词法规则，它是明确的独立检索来源，在语义依赖故障时可支撑已有降级策略。长期检索默认使用真实 Embedding，不会因真实模型失败静默切回词法向量。

## 模型空间与数据安全

当前模型为 `BAAI/bge-small-zh-v1.5`，512 维，float32，CLS + L2。Query 使用该模型的查询前缀，Passage 不加查询前缀，两者通过独立模型实例执行，使用同一模型空间。

model_space 由权重 SHA-256、维度、精度、schema 和预处理摘要共同生成。预处理摘要包含 tokenizer、前缀、输入长度规则和运行库版本。首次启动把完整绑定持久化到 p3.db；后续启动核对已有绑定，禁止自动把旧向量改标为新模型空间。

如果已有数据库保存了词法向量，或者模型指纹/预处理版本改变，启动会拒绝混用。使用新的运行目录验证新模型；已有业务数据需要单独进行显式重建/迁移，本批没有自动迁移或删除用户数据库。

输入超过模型实际 tokenizer 上限时拒绝，不静默截断。这里 tokenizer 用于模型输入合法性；ContextPack 的长度预算仍为既有 UTF-8 字节计数，不能将其称为已接通下游生成模型 tokenizer。

同一主体和 scope 下，operation_id＋usage 绑定固定输入及模型空间；同幂等键不同内容拒绝。Query 与 Passage 分开绑定，不共享临时计算身份。重复计算可以发生，但正式记忆提交仍由 Remember 的幂等和租约控制。

每次计算在 native_embedding_attempts 保存状态、trace_id、scope、输入摘要和 evidence_refs；原生后端在 semantic-backend-evidence 保存真实部署及向量摘要。计算记录不是第二套业务 Task。模型调用异常、BUSY、截止时间到期和撤权均返回明确错误，不能伪造向量成功。

## 运行

使用 Python 3.13，在仓库根目录安装：

```text
python -m pip install -r scripts/p3/requirements-native.txt
python scripts/p3/demo_flows.py --directory <新的运行目录>
python scripts/p3/validate_native_flows.py --report docs/p3/acceptance/reports/native-embedding-validation.json
```

首次加载需要模型文件。默认缓存为 `.aether/recall/embedding/models`；本次真实验证复用了现有缓存，没有复制或删除模型。真实验证使用原生模型、RF 身份与数据库，不再使用旧示例中的授权及存储测试替身。

手动运行继续使用已有 CLI。所有进程使用同一个模型配置：

```text
python -m aether_agent_memory.runtime.flows --db <p3.db> --cache-root <cache目录> --embedding-config <配置.json> worker
python -m aether_agent_memory.runtime.flows --db <p3.db> --cache-root <cache目录> --embedding-config <配置.json> recall --query 饮品偏好
python -m aether_agent_memory.runtime.flows --db <p3.db> --cache-root <cache目录> --embedding-config <配置.json> health
```

设置 PYTHONPATH=src，并通过 P3_API_KEY 提供已配置凭证。配置文件为现有 NativeEmbeddingSettings JSON，例如：

```json
{
  "backend": "onnx",
  "model_name": "BAAI/bge-small-zh-v1.5",
  "cache_dir": ".aether/recall/embedding/models",
  "precision": "fp32",
  "threads": 1,
  "max_input_tokens": 512,
  "query_prefix": "为这个句子生成表示以用于检索相关文章：",
  "passage_prefix": ""
}
```

仅运行无模型工程测试时，明确选择 `--embedding-profile lexical`；它使用独立测试数据库。validate_flows.py 的回归演示显式选择 lexical，validate_native_flows.py 才代表真实模型验证。两类证据分别保存。

健康检查在 native 模式下执行固定短输入的 Query 和 Passage 推理，记录 Trace 与推理证据；仍受探测截止时间控制。并发模型占用可能导致探测不可用，不等同于进程必须重启。CPU 调用取消后仍可能继续运行，旧后端保持实际占用直到内核结束；Host 关闭等待原生线程退出后再关闭持久化。

## 清理范围及保留理由

| 对象 | 处理 | 原因 |
|---|---|---|
| `examples/recall_embedding.py` | 已删除 | 使用测试身份/存储的独立烟测被真实 P3 端到端验证替代 |
| `docs/recall_embedding_native.md` | 收敛为新文档入口 | 不再同时维护两套装配说明 |
| 新流程默认 LexicalEmbedding 路径 | 改为显式测试模式 | 默认运行真实模型，禁止失败自动回退 |
| SQLiteVectors 的固定 256 维与固定词法模型限制 | 已移除 | 真实模型为 512 维，必须依配置绑定 |
| B1 兼容模块、旧 SemanticEmbeddingService | 保留 | 仍被旧 Sidecar、旧查询链及测试引用；新三流程不启动其队列 |
| LexicalEmbedding 和 ByteTokenizer | 保留 | 前者用于 Working 排序/显式无模型测试；后者仍是当前 ContextPack 预算规则 |
| 已有数据库、模型缓存、历史验收报告 | 保留 | 属于数据或历史证据，不以“旧”为由删除 |

已删除示例及替换前文档的备份保存在仓库外的 agent 工作目录，不成为正式运行依赖。没有批量删除旧服务；除本次明确替代的入口与文档外，保留其他未提交改动。

当前还有实际调用方的旧服务，应在新 HTTP 接口和其调用方迁移完成后另批移除；删除前需检查入口、配置、部署和测试引用。
