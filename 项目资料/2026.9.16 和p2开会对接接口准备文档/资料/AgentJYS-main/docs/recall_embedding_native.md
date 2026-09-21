# Recall 真实 Embedding 迁移

2026-09-15：真实 CPU 推理代码已迁入 `recall/embedding/`。新能力在进程内直接加载模型，不调用 B1 HTTP 或 B1 Python 模块。

## 代码入口

| 文件 | 职责 |
|---|---|
| `recall/embedding/backends.py` | 从旧 B1 迁入的 FastEmbed/ONNX、OpenVINO、IPEX 后端；保留已有模型加载、量化、微批和 OpenVINO 异步推理能力 |
| `recall/embedding/native.py` | 新 `EmbeddingBackendPort` 适配器；绑定真实模型、tokenizer、用途与预处理版本，检查输入/向量并保存推理证据 |
| `recall/embedding/service.py` | 共享 Query/Passage 执行、准入、重试、结果绑定与复用 |
| `recall/embedding/__main__.py` | 独立模型加载与部署信息检查入口 |
| `examples/recall_embedding.py` | 通过共享服务执行真实模型的本地验证；授权和记录存储使用测试替身 |

旧 `b1/backends.py` 和 `b1/fastembed_client.py` 向新实现单向兼容。旧 Sidecar 的 HTTP、动态请求合批和部署入口仍服务旧接口；此次没有把旧 `/v1/intercept`、分块流程或 HTTP Host 注册为新 Recall 的入口。

## 安装与运行

在项目根目录、Python 3.13 环境中：

```bash
pip install -e ".[embedding-onnx]"
python -m aether_agent_memory.recall.embedding
python examples/recall_embedding.py --output .aether/recall/embedding/smoke.json
```

首次运行默认下载 `BAAI/bge-small-zh-v1.5` 的 ONNX 模型，缓存位于 `.aether/recall/embedding/models/`。这些模型文件不提交到 Git。检查入口输出实际权重 SHA-256、tokenizer/预处理摘要、维度、精度和推理运行时版本。

本次开发环境的可直接运行命令：

```powershell
& ../outputs/recall-venv/Scripts/python.exe examples/recall_embedding.py
```

可用 `--config settings.json` 指定 `NativeEmbeddingSettings`，例如：

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

其他后端使用 `embedding-openvino`、`embedding-int8` 或 `embedding-ipex` 可选依赖。IPEX 还需要对应平台的 Intel wheel；新原生适配要求其 `model_path` 指向本地权重目录，以计算真实权重摘要。当前原生模型配置仅支持 BGE small Chinese v1.5；其他模型需补充并验证各自的 tokenizer、pooling 和用途语义。

## 服务端装配

生产装配传入批准的 `EmbeddingModelBinding`、RF `AtomicRecordStore` 和可信 `EmbeddingInputPort`：

```python
from aether_agent_memory.recall.embedding.native import NativeEmbeddingBackend
from aether_agent_memory.recall.embedding.service import EmbeddingPolicy, SemanticEmbeddingService

# 在应用启动时加载，在整个应用生命周期内复用。
query_backend = NativeEmbeddingBackend(settings)
passage_backend = NativeEmbeddingBackend(settings)
embedding = SemanticEmbeddingService(
    rf_records,
    approved_input_port,
    query_backend.bind(approved_binding, records=rf_records),
    passage_backend.bind(approved_binding, records=rf_records),
    EmbeddingPolicy(query_concurrency=1, passage_concurrency=1),
)
# 将 embedding 注入 RecallQueryService；Remember 使用批准的 Passage 输入。
# 应用退出时依次 await embedding.close()、query_backend.close()、passage_backend.close()。
```

`bind` 核对真实模型摘要、512 维、float32、schema 和预处理摘要，拒绝不一致配置；同一实例不能改绑检索空间或记录存储。`describe()` 只返回观测信息，不会自动批准检索空间。示例自行生成的绑定仅限隔离的本地诊断空间。

默认 Query 加检索前缀，Passage 保留原文；token 计数使用实际加载 tokenizer 的副本，包含前缀和特殊 token，关闭截断后计数，超限即拒绝。向量统一采用 CLS、L2、float32。后端、精度、库版本、tokenizer、前缀或长度策略变化会改变预处理摘要，因此不能未经验证沿用旧检索空间；已有向量需按 Remember/P2 的投影流程完成匹配或重建。

原生适配器不自动重试，也不调用后端 fallback chain。共享服务统一决定可重试操作；原生推理异常不被猜测为安全可重试。每个模型实例有一个工作线程，Query/Passage 独立实例和配额。调用方取消或超时后，CPU 内核可能仍在运行，此时保留实际占用，后续调用收到 `EMBEDDING_BUSY`，直到内核退出；关闭接口等待实际线程结束。需要更高吞吐时再接入受控的模型实例池或动态合批，不把旧性能数据视为新链路性能。

推理证据通过 RF 记录 Port 写入 `semantic-backend-evidence`，含来源摘要、输入绑定、模型绑定、实际部署信息及向量摘要。没有新增数据库或持久化驱动；真实进程恢复、证据保留与清理由 RF 联调验收。

## 验证

- 真实 FastEmbed 0.8.0 + ONNX Runtime 1.30.0 CPU 推理通过：Query 一次、Passage 两次，均返回 512 维、L2 范数约为 1 的有限向量；同文 Query/Passage 因前缀不同得到不同向量，不同 Passage 也得到不同向量。
- 实测模型 SHA-256：`1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38`。
- 全新进程禁止导入 B1/B2/B3 及旧 memory 包后，同一真实推理示例通过。
- 新单元测试验证绑定不符、超长与原文摘要不符、非法向量、取消后实际占用、推理期间 deadline 到期、证据记录和旧模块兼容。
- 全部单元测试：599 项通过、1 项跳过（缺少可选 python-docx）；新增原生后端测试 16 项。`ruff check src tests scripts examples/recall_admission.py examples/recall_embedding.py` 和 Recall 全模块及测试替身的 mypy 检查通过。
- OpenVINO/IPEX 的实现已迁入，但本机未安装这两套运行时，本次没有宣称完成它们的真实推理或性能验收。

真实推理验证仍使用授权/记录测试替身，不代表 Recall 后续候选、Context 组装及生产 RF/P2 联调已经完成。
