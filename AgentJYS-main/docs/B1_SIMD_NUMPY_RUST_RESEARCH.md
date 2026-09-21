# B1 单核 SIMD / NumPy / Rust 矩阵算子预研报告

更新时间：2026-08-03  
范围：B1 CPU Embedding 周边的单核算子预研，不代表向量查询引擎或生产向量库性能。

## 1. 结论先行

- 当前生产推理是 FastEmbed + ONNX Runtime `CPUExecutionProvider`，由 ONNX Runtime 和
  底层 CPU 库自动选择 SIMD；Sidecar 没有硬编码“所有算子都使用 AVX2”的错误承诺。
- 预研代码提供 NumPy/OpenBLAS 单线程、Rust 标量和显式 AVX2 三条可重复路径。
- 本次在 Windows x64、Intel Core i5-1135G7 的 release 实测（3 次重复）中，
  NumPy/OpenBLAS 为 58-84 GFLOPS；显式 Rust AVX2 为 11.8-21.3 GFLOPS；Rust 标量为
  1.47-1.86 GFLOPS。此前更长重复次数记录约为 89-97、15-23、1.7-1.9 GFLOPS，说明
  电源、后台负载和重复次数会显著影响绝对值。
- NumPy 数值更高不等于“Python 循环更快”，因为矩阵乘委托给已优化的 OpenBLAS 内核；Rust
  预研内核是朴素 GEMM，目标是观察 SIMD 方向和误差，不是与生产 BLAS 做最终竞品结论。
- B1 真实边界数据运行中，512 维 FP32 ONNX CPU 向量均通过有限值、维度和 L2 归一化校验；
  该测试不是 GEMM GFLOPS 测试。

## 2. 可重复环境

| 项目 | 值 |
| --- | --- |
| OS | Windows x64 |
| CPU | Intel Core i5-1135G7（本机实测记录） |
| Python | 3.13.14 |
| NumPy | 2.5.1 |
| FastEmbed | 0.8.0 |
| ONNX Runtime | 1.28.0 |
| 模型 | `BAAI/bge-small-zh-v1.5` |
| 模型输出 | 512 维、FP32、L2 normalized |
| BLAS 环境 | `OMP_NUM_THREADS=1`、`OPENBLAS_NUM_THREADS=1`、`MKL_NUM_THREADS=1`、`NUMEXPR_NUM_THREADS=1` |
| 亲和性 | Windows 进程尽力绑定 CPU 0；结果 JSON 记录是否成功 |

CPU 0 亲和性是单核实验控制手段，不代表生产部署必须固定 CPU 0。混合架构、系统调度、
电源策略、温度和后台进程都会影响结果。

## 3. 算法和测量方法

### 3.1 矩阵构造

对每个 `N` 构造确定性 `float32` 矩阵，另一个矩阵先转置以便连续读取。理论计算量为：

```text
2 * N^3 floating-point operations
GFLOPS = 2 * N^3 / median_seconds / 1e9
```

每个尺寸先 warm-up，再运行多次，记录 median、min、checksum、finite 和 NumPy BLAS
线程池信息。Rust 记录 scalar/AVX2 的最大绝对误差。

### 3.2 NumPy/OpenBLAS

`benchmarks/b1/numpy_single_core.py` 在导入 NumPy 前设置单线程环境变量，尽力绑定 CPU 0，
并使用 `threadpoolctl` 记录实际线程池。它测量的是 `left @ right`，不是 Python for 循环。

### 3.3 Rust 标量和显式 AVX2

`benchmarks/b1/rust_matmul/src/main.rs` 使用朴素三重循环的 dot product：

- `rust_scalar`：标量 `f32` 乘加，使用 `black_box` 降低编译器自动向量化干扰；
- `rust_explicit_avx2`：`_mm256_loadu_ps`、`_mm256_mul_ps`、`_mm256_add_ps`，8 个
  `f32` lane，尾部用标量处理；
- 启动时检测 AVX2，CPU 不支持则拒绝运行，避免非法指令；
- 输出 scalar 对照的 `max_abs_error_vs_scalar`。

这不是完整优化 GEMM：没有 blocking/tiling、packing、预取、FMA、并行线程、NUMA 或 BLAS
调度。因此结果只回答“当前简单算子中显式 AVX2 相对标量的方向”，不能替代 oneDNN、MKL、
OpenBLAS 或 ONNX Runtime 的生产 benchmark。

## 4. 已有单核结果

以下为本次 release 基准命令（`--repetitions 3`）的 median 结果：

| N | NumPy/OpenBLAS GFLOPS | Rust AVX2 GFLOPS | Rust scalar GFLOPS | AVX2 最大绝对误差 |
| ---: | ---: | ---: | ---: | ---: |
| 128 | 58.50 | 17.90 | 1.55 | `9.54e-7` |
| 256 | 83.93 | 21.31 | 1.86 | `2.146e-6` |
| 512 | 57.83 | 11.82 | 1.47 | `4.292e-6` |

相对 Rust scalar，显式 AVX2 约提升 11.5x、11.4x、8.0x；NumPy/OpenBLAS 约提升
37.6x、45.0x、39.3x。不同电源策略、BLAS wheel、编译器和后台负载会改变绝对值，报告不
把这些数字写成 SLA。

## 5. 真实 Sidecar 吞吐结果

`benchmarks/b1/sidecar_benchmark.py` 是顺序 HTTP 测量，向真实 Sidecar 发送文本并接收完整
JSON 向量，默认单 worker、1 CPU 线程。本次当前代码实测记录（20 次、3 次 warm-up）：

| batch | items/s | P50 | P99 |
| ---: | ---: | ---: | ---: |
| 1 | 58.70 | 15.67 ms | 36.48 ms |
| 8 | 84.78 | 92.22 ms | 127.26 ms |

这些值受模型首次加载、JSON 向量序列化、Windows 调度和 batch 配置影响。它们不是并发压测，
也没有证明 `>=2,000 QPS`；正式目标仍需固定硬件、并发模型、向量是否返回、持续时间和错误率
后再测。

本次真实边界数据运行另外得到：

```text
10 项送入 Sidecar，9 success + 1 B1_TEXT_TOO_LONG
32768 字符项 -> 91 chunks / 91 vectors
总返回向量 111，维度 512，CPUExecutionProvider，normalized=true
客户端包含模型计算和 HTTP JSON 往返，10 项耗时约 14.85 s
```

这组数字用于验证异常和分块边界，不应与短文本吞吐表直接比较。

## 6. 当前 SIMD 能力和生产策略

`/v1/capabilities` 使用 NumPy runtime 的 `__cpu_features__` 尽力报告：SSE2、AVX、FMA3、
AVX2、AVX512F/VNNI/BF16、AMX TILE/INT8/BF16，并列出 ONNX Runtime providers。

当前生产选择：

```text
CPU feature detection (observability)
  -> ONNX Runtime CPUExecutionProvider
  -> ONNX Runtime / oneDNN 等内部 kernel dispatch
```

能力检测不等于指令执行证明。模型中不同算子可能走不同 kernel，也可能因为形状、数据类型、
编译选项或安全策略退回更保守实现。

在 i5-1135G7 上当前预研确认 AVX2 路径可执行；运行时检测报告 `AVX512F=true`，但
`AVX512_VNNI=false`、`AVX512_BF16=false`、AMX 全部为 false。检测到 AVX512F 不等于
模型算子已使用 AVX-512，因此没有把它写成生产执行结论。

## 7. 替换策略和未来实验

### 7.1 后端

`b1/backends.py` 定义 `SidecarEmbeddingBackend`：

```text
load() -> None
embed(texts, input_types, batch_size) -> list[np.ndarray]
runtime_details() -> dict
```

后续 OpenVINO 适配器或 IPEX 适配器只需实现该契约并 `register_backend()`。Sidecar HTTP
校验、分块、归一化、幂等、指标和故障策略保持不变。当前两者明确为
`reserved-not-implemented`，选择后 readiness 失败，避免把未验证实现当成生产后端。

### 7.2 SIMD

预留顺序：

1. scalar fallback，确保任何 CPU 都有可用基线；
2. AVX2/FMA，验证吞吐与误差；
3. AVX-512F/VNNI/BF16，按 FP32/INT8/BF16 分开测试；
4. AMX BF16/INT8，确认 tile 配置、OS 支持、线程绑定和降级路径；
5. 在 ONNX Runtime/OpenVINO/IPEX 三个后端上分别记录“检测到”和“实际 kernel”证据。

必须保留 scalar-compatible fallback。AVX-512 或 AMX 的编译产物不能在不支持的机器上被
无条件加载；应使用运行时 feature gate、独立函数和误差阈值。

## 8. 运行命令

NumPy：

```powershell
$env:OMP_NUM_THREADS="1"
$env:OPENBLAS_NUM_THREADS="1"
$env:MKL_NUM_THREADS="1"
$env:NUMEXPR_NUM_THREADS="1"
.\.venv-b1\Scripts\python.exe benchmarks\b1\numpy_single_core.py `
  --sizes 128 256 512 --repetitions 7 --output runtime\b1\numpy.json
```

Rust：

```powershell
cargo run --release --manifest-path benchmarks\b1\rust_matmul\Cargo.toml -- `
  --sizes 128 256 512 --repetitions 5 --output runtime\b1\rust.json
```

真实 Sidecar：

```powershell
.\.venv-b1\Scripts\python.exe benchmarks\b1\sidecar_benchmark.py `
  --batch-sizes 1 8 --warmup 3 --iterations 20 --output runtime\b1\sidecar.json
```

## 10. Sidecar 接入 P3Runtime 后的边界

本次新增的 `b1/sidecar_client.py` 只实现现有 `EmbeddingClient` 的两个方法：
`embed(texts)` 和 `embed_one(text)`。它不新增 P3/P2 公共接口，也不改变原有
`EmbeddingPipeline -> EmbeddingRecord -> P2VectorSink` 数据结构。

当 P3 服务设置 `AETHER_B1_SIDECAR_URL` 时，实际路径为：

```text
P3Runtime
  -> B1 SidecarEmbeddingClient
  -> HTTP /v1/intercept
  -> FastEmbed + ONNX Runtime CPUExecutionProvider
  -> 原有 EmbeddingPipeline/P2VectorSink
```

适配器按最多 32 个输入分批，使用 pipeline 的 400/40 分块基线，拒绝多 chunk、数量不匹配、
维度变化、NaN/Inf、HTTP 错误和 Sidecar 非 ready 响应。`embed` 发送 passage，`embed_one`
发送 query。P2 collection 的维度从 Sidecar readiness 的实际维度取得，而不是继续硬编码
32；P2 写入契约本身没有改变。

未设置 `AETHER_B1_SIDECAR_URL` 时仍走 Mock 路径，因此旧测试和离线开发不需要启动 Sidecar。
本报告中的 NumPy/Rust GFLOPS 和 `sidecar_benchmark.py` 吞吐，仍分别表示算子基准和 Sidecar
HTTP 基准，不能直接等同于接入 P2 后的端到端 QPS。

所有 JSON 输出都应留在 `runtime/b1`，不提交模型、环境、Rust `target` 或压测产物。

## 9. 风险和未完成验证

- 还没有完成 72 小时连续运行、网络注入、节点故障、磁盘故障和多进程幂等验证；
- 还没有在支持 AVX-512/AMX 的服务器上编译和测量相应 kernel；
- NumPy 和 Rust 算子不是 BGE Transformer 全链路的等价替代，不能从 GFLOPS 推导语义效果；
- 生产 Sidecar 当前默认单 worker、单线程，扩容应通过固定 CPU 亲和性、多个实例和网关限流
  重新测量，而不是直接把单核数字乘以核心数；
- JSON 返回 512 维向量有序列化成本，若未来改为二进制或内部 RPC，必须重新定义测量边界。
