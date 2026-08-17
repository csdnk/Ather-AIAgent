# B1 Sidecar Windows 手动安装与运行手册

更新时间：2026-08-03  
适用目录：`D:\Apps\Python\Program\aether-agent-b1\aether-agent-memory`

## 1. 本次 B1 的准确范围

B1 当前只完成以下闭环：

```text
业务写入点调用 B1 Sidecar
  -> 校验请求
  -> 可选长文本分块
  -> CPU Embedding
  -> FP32 向量 L2 归一化
  -> 返回向量、状态、指标和流程事件
```

当前不包含：

- 向量查询与图查询融合；
- HNSW、IVF-PQ、图检索或重排；
- 向量数据库持久化；
- 对任意进程流量的操作系统级透明代理；
- P2 Segment、Migration 或 Route 的控制逻辑。

这里的“拦截”是应用层 Hook：原业务在准备写入文本时同步调用
`POST /v1/intercept`。Sidecar 返回向量或结构化失败，调用方再按 fail-open 或
fail-closed 策略决定是否继续原业务。Sidecar 不会自动接管其他程序的网络流量。

## 2. 已实现状态

| 能力 | 当前状态 | 说明 |
| --- | --- | --- |
| Sidecar HTTP 服务 | 已实现 | FastAPI + Uvicorn，默认 `127.0.0.1:18081` |
| CPU Embedding | 已实现 | FastEmbed + ONNX Runtime `CPUExecutionProvider` |
| 默认模型 | 已实现 | `BAAI/bge-small-zh-v1.5`，实测为 512 维 FP32 |
| 单条/批量拦截 | 已实现 | 单批默认最多 32 项 |
| 长文本分块 | 已实现 | 自然边界、重叠、原文字符偏移 |
| 向量校验 | 已实现 | 数量、维度、有限值、非零范数、一致性、L2 归一化 |
| 幂等 | 已实现 | 进程内、租户 + request_id；含批内和并发重复请求 |
| 限流和超时 | 已实现 | 并发信号量、排队超时、后端执行超时 |
| 实时流程界面 | 已实现 | Textual 文字仪表盘，读取真实服务端点 |
| JSON/JSONL/CSV/TSV 数据集 | 已实现 | 字段名可配置；逐批调用真实 Sidecar |
| OpenVINO 后端 | 预留，未实现 | 配置、能力状态和替换接口已保留 |
| IPEX 后端 | 预留，未实现 | 配置、能力状态和替换接口已保留 |
| 显式 AVX2 预研内核 | 已实现 | 仅用于矩阵算子预研，不替代 ONNX Runtime |
| AVX-512/AMX 内核 | 预留，未实现 | 能力检测和策略位置已保留 |

## 3. 目录和生成内容

源码位于统一仓库，不再依赖旧 `b1` 目录：

```text
src/aether_agent_memory/b1/
  sidecar.py             HTTP 服务、拦截、分块、指标、异常策略
  backends.py            可替换后端契约及 ONNX 实现
  capabilities.py        CPU/SIMD/ONNX Runtime 能力检测
  dashboard.py           实时文字仪表盘
  dataset_runner.py      数据集读取和实时执行
  model_download.py      模型下载及真实预热校验
examples/
  b1_intercept_sample.py 真实拦截示例
  b1_dataset.jsonl       最小数据集示例
benchmarks/b1/           NumPy、Rust、Sidecar 预研基准源码
scripts/                 Windows 安装和启动脚本
```

安装后才会生成以下本地内容，均已被 `.gitignore` 排除：

- `.venv-b1\`：B1 Python 虚拟环境；
- `.aether\b1\models\`：下载的模型缓存；
- `benchmarks\b1\rust_matmul\target\`：Rust 编译结果；
- `runtime\b1\`：可选的压测输出。

仓库交付物不包含虚拟环境、模型、缓存和压测结果。

## 4. Windows 前置条件

推荐环境：

- Windows 10/11 x64；
- PowerShell 5.1 或 PowerShell 7；
- 可访问 PyPI、Astral Python 下载源和模型下载源；
- 至少 2 GB 可用磁盘空间，实际占用取决于依赖缓存和模型版本；
- 默认模型首次下载需要网络，后续可离线使用本地缓存；
- Microsoft Visual C++ 2015-2022 Redistributable x64。若 ONNX Runtime 提示 DLL
  缺失，请从 Microsoft 官方页面安装后重开 PowerShell。

当前统一仓库要求 Python `>=3.13`。不要使用旧 B1 文档中的 Python 3.11 命令。

## 5. 从零手动安装

### 5.1 打开 PowerShell 并进入仓库

```powershell
cd D:\Apps\Python\Program\aether-agent-b1\aether-agent-memory
$env:PYTHONUTF8 = "1"
```

以下命令都在这个目录执行。

### 5.2 安装 uv

先检查：

```powershell
uv --version
```

若提示找不到命令，使用 Astral 官方安装器：

```powershell
Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
```

关闭并重新打开 PowerShell，再执行 `uv --version`。企业网络环境应按内部软件源和
脚本审计规则安装，不要绕过公司的下载策略。

### 5.3 安装 Python 3.13

```powershell
uv python install 3.13
uv python find 3.13
```

第二条命令应输出实际的 Python 3.13 路径。

### 5.4 创建独立虚拟环境

```powershell
uv venv --python 3.13 .venv-b1
.\.venv-b1\Scripts\python.exe --version
```

输出必须是 `Python 3.13.x`。

### 5.5 安装 Sidecar 和文字界面依赖

```powershell
uv pip install --python .\.venv-b1\Scripts\python.exe -e ".[b1-sidecar,b1-dashboard]"
```

关键依赖包括：FastAPI、Uvicorn、FastEmbed、ONNX Runtime、NumPy、HTTPX、
Textual 和 threadpoolctl。`pyproject.toml` 是唯一依赖清单，不再使用旧
`b1\requirements.txt`。

检查依赖：

```powershell
.\.venv-b1\Scripts\python.exe -c "import fastapi, fastembed, onnxruntime, textual; print('OK')"
```

### 5.6 下载并实推校验模型

```powershell
$env:AETHER_B1_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
$env:AETHER_B1_CACHE_DIR = "$PWD\.aether\b1\models"
.\.venv-b1\Scripts\python.exe -m aether_agent_memory.b1.model_download `
  --model-name $env:AETHER_B1_MODEL_NAME `
  --cache-dir $env:AETHER_B1_CACHE_DIR `
  --threads 1
```

该命令不是只检查文件名。它会让 FastEmbed 下载 ONNX 模型，通过
`CPUExecutionProvider` 执行一次真实预热推理，并输出模型名、维度、缓存目录、文件数和
运行时 Provider。正常结果中的 `status` 为 `downloaded_and_verified`，默认模型维度应为
512。

如需使用预先复制的完整 FastEmbed 模型目录，可设置：

```powershell
$env:AETHER_B1_MODEL_PATH = "D:\models\bge-small-zh-v1.5"
```

目录必须包含 FastEmbed 所需的 ONNX、Tokenizer 和配置文件。只复制一个 `.onnx` 文件
通常不够。使用普通缓存时不要设置 `AETHER_B1_MODEL_PATH`。

## 6. 配置项

所有 Sidecar 配置使用 `AETHER_B1_` 前缀：

| 环境变量 | 默认值 | 含义 |
| --- | ---: | --- |
| `AETHER_B1_BACKEND` | `onnx` | `onnx` 可用；`openvino`、`ipex` 仅预留 |
| `AETHER_B1_MODEL_NAME` | `BAAI/bge-small-zh-v1.5` | FastEmbed 模型标识 |
| `AETHER_B1_CACHE_DIR` | `.aether/b1/models` | 模型缓存目录 |
| `AETHER_B1_MODEL_PATH` | 空 | 可选的完整本地模型目录 |
| `AETHER_B1_HOST` | `127.0.0.1` | 监听地址 |
| `AETHER_B1_PORT` | `18081` | 监听端口，范围 1-65535 |
| `AETHER_B1_THREADS` | `1` | 模型 CPU 线程数 |
| `AETHER_B1_MODEL_BATCH_SIZE` | `8` | 模型内部批大小 |
| `AETHER_B1_MAX_BATCH_ITEMS` | `32` | 一个 HTTP 请求最多项目数 |
| `AETHER_B1_MAX_BODY_BYTES` | `2097152` | HTTP 请求体上限 |
| `AETHER_B1_MAX_INPUT_CHARS` | `32768` | 单项文本字符上限 |
| `AETHER_B1_CHUNK_MAX_CHARS` | `400` | 每块最大字符数 |
| `AETHER_B1_CHUNK_OVERLAP_CHARS` | `40` | 相邻块重叠字符数，必须小于块上限 |
| `AETHER_B1_MAX_CHUNKS_PER_ITEM` | `96` | 单项最大分块数 |
| `AETHER_B1_MAX_METADATA_BYTES` | `16384` | 单项 metadata UTF-8 JSON 字节上限 |
| `AETHER_B1_MAX_CONCURRENCY` | `1` | 同时执行的推理任务数 |
| `AETHER_B1_QUEUE_TIMEOUT_SECONDS` | `5` | 等待推理槽位的上限 |
| `AETHER_B1_BACKEND_TIMEOUT_SECONDS` | `120` | 一次后端推理等待上限 |
| `AETHER_B1_IDEMPOTENCY_CACHE_SIZE` | `1024` | 进程内已完成幂等结果数 |
| `AETHER_B1_EVENT_HISTORY_SIZE` | `200` | 最近流程事件数 |
| `AETHER_B1_METRICS_WINDOW_SECONDS` | `60` | 实时速率/延迟滚动窗口 |
| `AETHER_B1_FAIL_MODE` | `open` | `open` 返回 HTTP 200 + 项目失败；`closed` 返回 503 |
| `AETHER_B1_EAGER_LOAD` | `true` | 启动时加载模型；关闭后首次有效请求加载 |
| `AETHER_B1_SIDECAR_URL` | 空 | 仅由 P3Runtime 使用；非空时切换到 B1 Sidecar adapter |
| `AETHER_B1_SIDECAR_TIMEOUT_SECONDS` | `120` | P3Runtime 调用 Sidecar 的 HTTP 超时 |
| `AETHER_B1_TENANT_ID` | `p3-runtime` | P3Runtime 发给 Sidecar 的租户标识 |

单核验证建议同时设置：

```powershell
$env:OMP_NUM_THREADS = "1"
$env:OPENBLAS_NUM_THREADS = "1"
$env:MKL_NUM_THREADS = "1"
$env:NUMEXPR_NUM_THREADS = "1"
```

## 7. 启动 Sidecar

手动配置并启动：

```powershell
$env:AETHER_B1_HOST = "127.0.0.1"
$env:AETHER_B1_PORT = "18081"
$env:AETHER_B1_BACKEND = "onnx"
$env:AETHER_B1_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
$env:AETHER_B1_CACHE_DIR = "$PWD\.aether\b1\models"
$env:AETHER_B1_THREADS = "1"
$env:AETHER_B1_FAIL_MODE = "open"
.\.venv-b1\Scripts\python.exe -m aether_agent_memory.b1.sidecar
```

保持这个 PowerShell 窗口运行。第一次加载模型可能需要几十秒。也可使用已经封装相同配置的
启动脚本：

```powershell
.\scripts\start_b1_sidecar.ps1
```

在第二个 PowerShell 中检查：

```powershell
Invoke-RestMethod http://127.0.0.1:18081/health/live
Invoke-RestMethod http://127.0.0.1:18081/health/ready
Invoke-RestMethod http://127.0.0.1:18081/v1/capabilities
```

`live` 只表示 HTTP 进程活着；`ready` 必须为 `ready` 才表示模型可推理。浏览器还可打开
`http://127.0.0.1:18081/docs` 查看 FastAPI 交互式接口。

## 8. 实现并验证应用层拦截

### 8.1 运行真实样例

Sidecar 保持运行，在第二个 PowerShell 执行：

```powershell
.\.venv-b1\Scripts\python.exe .\examples\b1_intercept_sample.py
```

输出会包含真实 `request_id`、`trace_id`、模型、512 维、L2 范数和向量前 8 项。
查看完整向量：

```powershell
.\.venv-b1\Scripts\python.exe .\examples\b1_intercept_sample.py --show-vector
```

### 8.2 单条请求格式

```json
{
  "request_id": "write-20260803-0001",
  "trace_id": "trace-0001",
  "tenant_id": "tenant-a",
  "source_type": "rag_document",
  "source_id": "document-001",
  "object_id": "object-001",
  "chunk_id": "chunk-001",
  "chunk_text": "需要生成向量的实际文本",
  "embedding_required": true,
  "input_type": "passage",
  "metadata": {"namespace": "knowledge-base-a"}
}
```

规则：

- `request_id`、`tenant_id`、`source_type`、`source_id` 必填；
- `text` 和 `chunk_text` 必须且只能提供一个；
- `input_type` 为 `passage` 或 `query`；知识入库使用 `passage`，查询文本使用 `query`；
- `embedding_required=false` 时返回 `skipped`，不会调用模型；
- 未声明字段会被拒绝，避免拼写错误被静默忽略；
- 同一租户重复使用相同 `request_id` 和相同内容返回幂等重放；内容不同返回
  `B1_IDEMPOTENCY_CONFLICT`。

批量格式只能有一个顶层 `items` 字段：

```json
{"items": [{"request_id": "..."}, {"request_id": "..."}]}
```

### 8.3 返回格式

未分块的成功项同时提供顶层 `vector` 和 `chunks[0].vector`。长文本成功项提供多个
`chunks`，每块包含：

- `chunk_id`、`chunk_index`；
- `start_char`、`end_char`，对应原始输入的 Python 字符下标；
- 归一化 FP32 `vector`。

成功项还返回 `backend`、`engine`、`embedding_model`、`embedding_dim`、
`normalized`、`quantization_type`、延迟、字符数、分块数和向量数。失败项不返回假向量，
而是返回 `error_code`、`error_message` 和空 `chunks`。

## 9. 数据集支持

### 9.1 当前实际使用的“数据集”

B1 Sidecar 是预训练模型推理服务，本项目不会在运行时用数据集训练模型。仓库自带的
`examples/b1_dataset.jsonl` 只有 3 条人工样例，用于验证格式、中文和真实向量返回，不能
代表效果评测集。矩阵报告使用确定性生成的 FP32 矩阵；Sidecar 性能数据使用固定短句，均不
是模型训练数据。

默认模型的训练数据和效果指标应以模型发布者的 Model Card 为准，本仓库不重新声明无法在
本机复现的训练集指标。

### 9.2 支持的文件格式

| 格式 | 扩展名 | 顶层结构 |
| --- | --- | --- |
| JSONL/NDJSON | `.jsonl`、`.ndjson` | 每个非空行一个 JSON 对象 |
| JSON | `.json` | 对象数组，或只包含 `items` 的对象 |
| CSV | `.csv` | 第一行表头，逗号分隔，UTF-8/UTF-8 BOM |
| TSV | `.tsv` | 第一行表头，Tab 分隔，UTF-8/UTF-8 BOM |

默认字段：`text`、`id`、`tenant_id`、`source_type`、`input_type`。其他数据集可通过
`--text-field`、`--id-field`、`--tenant-field`、`--source-type-field`、`--input-type-field`
映射，无需改代码。
文本字段必须是非空字符串。缺少 ID 时自动生成；缺少租户和来源类型时使用命令行默认值。

运行自带数据集：

```powershell
.\.venv-b1\Scripts\python.exe -m aether_agent_memory.b1.dataset_runner `
  .\examples\b1_dataset.jsonl `
  --batch-size 2 `
  --output .\runtime\b1\dataset-result.json
```

字段不同的 CSV：

```powershell
.\.venv-b1\Scripts\python.exe -m aether_agent_memory.b1.dataset_runner `
  D:\data\knowledge.csv `
  --text-field content `
  --id-field document_id `
  --tenant-id tenant-a `
  --source-type rag_document `
  --batch-size 8
```

结果文件默认删除向量数组，只保留状态、维度、偏移和汇总，避免文件过大。确需保存全部向量
时增加 `--include-vectors`。这只是导出结果，不是向量数据库。

### 9.3 运行测试

统一仓库中的旧集成示例位于根目录 `examples/`。Windows PowerShell 从仓库根目录运行全量
测试时设置一次根目录路径：

```powershell
$env:PYTHONPATH = (Get-Location).Path
.\.venv-b1\Scripts\python.exe -m pytest -q
```

Compose 测试需要 Docker Desktop；未启动 Docker 时该测试会跳过或按环境失败，不代表 B1
Sidecar 本地测试失败。B1 本地单元测试不需要 Docker：

```powershell
.\.venv-b1\Scripts\python.exe -m pytest tests\unit\test_b1_sidecar.py tests\unit\test_b1_dataset_runner.py -q
```

## 10. 实时文字界面和指标

先启动 Sidecar，再开第三个 PowerShell：

```powershell
.\scripts\start_b1_dashboard.ps1
```

或：

```powershell
.\.venv-b1\Scripts\python.exe -m aether_agent_memory.b1.dashboard `
  --base-url http://127.0.0.1:18081 `
  --refresh-seconds 1
```

界面实时读取 `/health/ready`、`/metrics`、`/v1/capabilities` 和 `/v1/events`，显示：

- `INTERCEPT -> VALIDATE -> CHUNK -> CPU EMBED -> L2 NORMALIZE -> RETURN`；
- live/ready、Backend、Engine、模型、Provider、维度、FP32；
- ONNX/OpenVINO/IPEX 的实现状态；
- CPU 和检测到的 SSE2、AVX2、AVX-512、AMX 能力；
- 请求数、项目数、成功/跳过/失败、字符/分块/向量；
- 60 秒滚动 RPS、items/s、P50/P95/P99；
- 排队超时、后端超时、错误码；
- 最近请求经过的阶段和失败位置。

指标端点：

```powershell
Invoke-RestMethod http://127.0.0.1:18081/metrics
Invoke-RestMethod "http://127.0.0.1:18081/v1/events?limit=20"
```

这些指标在单个进程内存中累计，服务重启后清零；不是 Prometheus 文本格式。滚动速率按最近
窗口内完成的请求计算，低流量或长时间空闲时不等于正式压测吞吐。

## 11. ONNX、OpenVINO、IPEX 和 SIMD 替换策略

当前生产路径是：

```text
FastEmbed -> ONNX Runtime -> CPUExecutionProvider -> Runtime 自动选择 CPU Kernel
```

代码没有强制 ONNX Runtime 每个算子必须使用某条 AVX2 指令。`/v1/capabilities` 的
“检测到 AVX2”只表示 NumPy/CPU Runtime 报告支持，不证明模型中每个算子实际执行了
AVX2。最终指令选择由 ONNX Runtime 及其 CPU 库完成。

后端替换契约位于 `backends.py`：实现 `SidecarEmbeddingBackend` 的 `load`、`embed` 和
`runtime_details`，再调用 `register_backend()`，HTTP、校验、分块、幂等、指标和界面层不需
修改。OpenVINO 和 IPEX 目前故意返回“预留未实现”，选择它们会让 readiness 失败，不会
静默回退到 ONNX。

显式矩阵预研当前包含 scalar 和 AVX2。当前这台 i5-1135G7 通过 NumPy/Rust 能力检测报告
`AVX512F=true`、`AVX512_VNNI=false`、`AVX512_BF16=false`、AMX 全部为 false；这只表示
CPU/runtime 报告了 AVX512F，不能证明 ONNX Runtime 的每个模型算子实际使用 AVX-512。
AVX-512 专用内核和 AMX BF16/INT8 仍需在对应数据类型、编译器、OS 支持和数值误差标准下
单独实现并验证。

## 12. P2 升级对 B1 的影响

本轮 B1 Sidecar 不直接连接 P2，所以 P2 Kernel 重构不要求改写文本分块、Embedding 或向量
返回逻辑。当前仓库的真实 proto 仍未升级到公告中的完整 CommonRequestHeader、Segment
Manager、Route Epoch 和 Fencing Token，不能提前根据公告猜测字段并破坏现有联调。

升级后的 proto 和服务实现进入仓库后，需要修改的是 P2 适配边界：

1. `p2/client.py` 和 `p2/adapters.py` 构造 CommonRequestHeader；
2. 写向量前查询 P2 实际 Resource/Segment 并校验可写状态；
3. 携带 tenant、namespace、request_id、trace_id、幂等键和 deadline；
4. 映射 Unsupported Backend、Capability 和结构化错误；
5. 由 P2 返回实际 Segment 引用，B1 不自行推导；
6. Route Epoch、Fencing Token 和 Migration 主要影响 P2 适配器/B3 执行器，不进入 B1
   Embedding 算法。

本轮没有把向量查询和图查询引擎融合加入 B1。Graph/Fusion 是未来 B2 召回增强项。

## 13. 边界、异常和审查结论

| 情况 | 当前处理 |
| --- | --- |
| 非 JSON、顶层数组、空批次、混合顶层字段 | HTTP 4xx |
| 缺字段、额外字段、同时给 text/chunk_text | 项目级 `B1_INVALID_REQUEST` |
| 空白文本、文本/metadata/批量/请求体超限 | 明确错误码或 HTTP 413 |
| 长文本 | 自然边界分块、重叠、最大块数限制、保留原文偏移 |
| 后端少返回、多返回、空向量、NaN/Inf、零范数、维度变化 | 拒绝整次后端结果，不返回损坏向量 |
| 队列拥塞 | `B1_BUSY`，计入 `queue_timeouts` |
| 推理超过时间 | `B1_EMBEDDING_TIMEOUT`；底层线程完成前不释放推理槽位 |
| 后端异常/模型缺失 | 结构化失败；readiness 为 503 |
| 同批、已完成、并发重复 request_id | 相同内容重放；不同内容冲突 |
| embedding_required=false | `skipped`，不调用后端 |
| fail-open | HTTP 200，但项目状态仍为 failed；调用方可继续原写入 |
| fail-closed | 后端/排队失败返回 HTTP 503；调用方停止或重试 |

已由 Python 3.13 单元测试覆盖上述主要路径。仍需明确的工程边界：

- 幂等缓存、事件和指标是单进程内存，不跨重启、不跨多 worker；当前固定一个 Uvicorn worker；
- 超时不能强制终止正在本地 C/C++ Runtime 中执行的线程，只能停止等待；槽位会在后台线程真正
  结束后释放；
- 当前没有鉴权、TLS、租户配额和持久化审计，默认只绑定 `127.0.0.1`。暴露到其他机器前必须
  通过受控网关增加这些能力；
- 未完成 72 小时稳定性、节点故障、网络故障、磁盘故障和多进程一致性测试；
- 未验证正式目标 `>= 2,000 QPS`。现有单机顺序 HTTP 实测远低于该数字，不能写成达标；
- 模型语义质量需要用业务标注集评估 Recall@K、MRR、nDCG，不应从矩阵 GFLOPS 推导。

## 14. 停止和清理

Sidecar 或仪表盘窗口按 `Ctrl+C` 停止。只清理本地生成内容：

```powershell
cd D:\Apps\Python\Program\aether-agent-b1\aether-agent-memory
Remove-Item -LiteralPath .\.venv-b1 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\.aether -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\runtime\b1 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\benchmarks\b1\rust_matmul\target -Recurse -Force -ErrorAction SilentlyContinue
```

## 16. 接入现有 P3Runtime（保持原接口）

本次接入只增加 B1 的 Sidecar `EmbeddingClient` 适配，不修改 P3 的 HTTP 路径、Pydantic
请求模型、P2 gRPC 契约、B2/B3 方法或返回结构。`P3Runtime` 只有在它自己的进程设置了
`AETHER_B1_SIDECAR_URL` 时才使用 Sidecar；未设置时继续使用原来的 Mock embedding。

先启动 P2 和 B1 Sidecar，再在运行 P3 服务的 PowerShell 中设置：

```powershell
$env:AETHER_B1_SIDECAR_URL = "http://127.0.0.1:18081"
$env:AETHER_B1_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
$env:AETHER_B1_CHUNK_MAX_CHARS = "400"
$env:AETHER_B1_CHUNK_OVERLAP_CHARS = "40"
.\.venv-b1\Scripts\python.exe .\scripts\p3_service.py
```

接入后的数据流：

```text
P3 B1 EmbeddingPipeline
  -> B1 SidecarEmbeddingClient (POST /v1/intercept)
  -> B1 ONNX CPU embedding
  -> 原有 EmbeddingRecord
  -> 原有 P2VectorSink / P2 E1
```

适配器按最多 32 个 pipeline chunk 分批调用 Sidecar，校验 readiness、返回数量、单 chunk、
向量维度和有限浮点数。网络错误、HTTP 错误或 Sidecar 失败会回到原有
`EmbeddingPipeline` 结构化失败路径。`embed()` 使用 passage，`embed_one()` 使用 query。

这些命令不会删除源码。执行前必须确认当前目录就是统一仓库根目录。

## 15. 常见问题

- `health/live` 正常、`health/ready` 为 503：查看 `load_error`，通常是模型未下载、路径不完整、
  网络失败、VC++ Runtime 缺失或选择了尚未实现的后端。
- 端口占用：使用 `Get-NetTCPConnection -LocalPort 18081`，或设置其他
  `AETHER_B1_PORT`，仪表盘的 `--base-url` 同步修改。
- 模型下载失败：确认代理/证书/公司网络策略；不要把半下载模型提交进仓库。删除对应缓存后重试。
- 返回 `B1_BUSY`：降低调用并发、减小批量、增加队列时间，或在压测后基于 CPU 核数评估线程和
  多实例配置。
- 返回幂等冲突：每次新业务操作使用新的 `request_id`；重试同一业务操作时保持完全相同的请求
  内容。
- PowerShell 中文乱码：设置 `$env:PYTHONUTF8="1"`，并使用 UTF-8 保存数据集。
