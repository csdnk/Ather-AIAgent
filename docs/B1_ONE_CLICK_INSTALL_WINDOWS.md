# B1 Windows 一键自动安装

更新时间：2026-08-03  
脚本：`scripts/install_b1.ps1`

## 1. 适用范围

脚本会在当前统一仓库中自动完成：

1. 检查 `uv`；缺少时调用 Astral 官方 HTTPS 安装脚本；
2. 安装/定位 Python 3.13；
3. 创建 `.venv-b1`；
4. 安装 `.[b1-sidecar,b1-dashboard]`；
5. 下载默认 `BAAI/bge-small-zh-v1.5`；
6. 使用 ONNX CPU 做一次真实预热推理并输出维度、Provider 和缓存大小。

模型、环境和 Rust 编译输出都在已忽略目录，不写入 Git 源码。

## 2. 执行

在 PowerShell 中：

```powershell
cd D:\Apps\Python\Program\aether-agent-b1\aether-agent-memory
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_b1.ps1
```

脚本结束时会打印环境目录、模型目录和启动命令。网络受限时，先按公司安全流程放行
`uv`、PyPI 和模型下载地址；不要把下载的模型复制进仓库。

## 3. 参数

```powershell
# 换模型名，仍由 FastEmbed 下载并校验
.\scripts\install_b1.ps1 -ModelName "BAAI/bge-small-zh-v1.5"

# 只安装依赖，不下载模型；适合已有本地缓存或离线准备阶段
.\scripts\install_b1.ps1 -SkipModelDownload

# 删除并重建本仓库内 .venv-b1，然后重新安装
.\scripts\install_b1.ps1 -RecreateEnvironment

# 同时指定 Python 小版本
.\scripts\install_b1.ps1 -PythonVersion "3.13"
```

`-RecreateEnvironment` 只允许删除解析后位于仓库根目录下的 `.venv-b1`，脚本会拒绝仓库外
路径，避免误删。

## 4. 启动和看到实时过程

安装完成后开两个 PowerShell 窗口。

窗口一：

```powershell
cd D:\Apps\Python\Program\aether-agent-b1\aether-agent-memory
.\scripts\start_b1_sidecar.ps1
```

窗口二：

```powershell
cd D:\Apps\Python\Program\aether-agent-b1\aether-agent-memory
.\scripts\start_b1_dashboard.ps1
```

仪表盘是文字界面，会每秒显示真实 readiness、后端、模型、CPU/SIMD 能力、阶段链路、
请求/项目计数、成功/失败/跳过、RPS、items/s、P50/P95/P99、字符/分块/向量、错误码和最近事件。

也可在第三个窗口执行实际样例：

```powershell
.\.venv-b1\Scripts\python.exe .\examples\b1_intercept_sample.py
```

## 5. 数据集和边界数据

最小数据集：

```powershell
.\.venv-b1\Scripts\python.exe -m aether_agent_memory.b1.dataset_runner `
  .\examples\b1_dataset.jsonl --batch-size 2
```

生成全面边界数据：

```powershell
.\.venv-b1\Scripts\python.exe .\examples\generate_b1_edge_dataset.py
.\.venv-b1\Scripts\python.exe -m aether_agent_memory.b1.dataset_runner `
  .\runtime\b1\b1-edge-dataset.jsonl `
  --batch-size 4 `
  --output .\runtime\b1\b1-edge-result.json
```

边界生成器包含：1 字符、短 query、400/401 字符分界、自然边界、多段落、无标点、混合
Unicode、32768 字符上限、32769 超限、空/空白文本、非法输入类型、超长 ID 和缺失 ID。

实际验证结果（本机 Python 3.13 + ONNX CPU）：

```text
records=14
local_validation_failures=4
records_sent=10
success=9
failed=1 (B1_TEXT_TOO_LONG)
max accepted input=32768 chars -> 91 chunks / 91 vectors
dimension=512, normalized=true, provider=CPUExecutionProvider
```

数据集运行器支持 `.json`、`.jsonl`/`.ndjson`、`.csv`、`.tsv`，通过
`--text-field`、`--id-field`、`--tenant-field`、`--source-type-field`、
`--input-type-field` 映射字段。

## 6. 检查和问题定位

```powershell
Invoke-RestMethod http://127.0.0.1:18081/health/live
Invoke-RestMethod http://127.0.0.1:18081/health/ready
Invoke-RestMethod http://127.0.0.1:18081/metrics
Invoke-RestMethod http://127.0.0.1:18081/v1/capabilities
```

`live=alive` 只表示进程存在；`ready=ready` 才表示模型已加载。OpenVINO/IPEX 当前是预留
策略，选择后 readiness 会失败并明确返回未实现，不会静默切回 ONNX。

如端口冲突：

```powershell
.\scripts\start_b1_sidecar.ps1 -Port 18082
.\scripts\start_b1_dashboard.ps1 -BaseUrl http://127.0.0.1:18082
```

## 7. 卸载本地生成内容

停止 Sidecar 后，在仓库根目录执行：

```powershell
Remove-Item -LiteralPath .\.venv-b1 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\.aether -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\runtime\b1 -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath .\benchmarks\b1\rust_matmul\target -Recurse -Force -ErrorAction SilentlyContinue
```

## 8. 将 Sidecar 接入现有 P3 服务

一键安装只负责 B1 环境、依赖和模型。要让现有 P3Runtime 使用新 Sidecar，需要把下面的
变量设置在 P3 服务进程中，而不是只设置在 Sidecar 进程中：

```powershell
$env:AETHER_B1_SIDECAR_URL = "http://127.0.0.1:18081"
$env:AETHER_B1_MODEL_NAME = "BAAI/bge-small-zh-v1.5"
$env:AETHER_B1_CHUNK_MAX_CHARS = "400"
$env:AETHER_B1_CHUNK_OVERLAP_CHARS = "40"
.\.venv-b1\Scripts\python.exe .\scripts\p3_service.py
```

不设置 `AETHER_B1_SIDECAR_URL` 时，P3Runtime 保持原来的 Mock embedding 路径。设置后，
现有 `EmbeddingPipeline`、`EmbeddingRecord`、`P2VectorSink` 和所有对外 API 均不变，只有
embedding 实现改为：

```text
P3Runtime -> B1 SidecarEmbeddingClient -> POST /v1/intercept -> P2VectorSink -> P2 E1
```

适配器会分批发送不超过 32 个 chunk，并检查 readiness、HTTP 状态、结果数量、单 chunk、
维度和非有限值。Sidecar 停止或模型未就绪时，P3 embedding 返回原有结构化失败。

脚本不删除 `src/`、`docs/`、`examples/` 或其他 P2/P3 功能。
