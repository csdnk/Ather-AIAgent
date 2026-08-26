# P3 服务器回归记录（2026-08-25）

## 1. 结论口径

本记录把三类证据严格分开：

1. 当前框架代码的真实依赖功能回归；
2. 2026-08-19 在服务器隔离环境完成的 OpenVINO INT8 吞吐记录；
3. 2026-08-25 在共享服务器、存在其他训练任务时进行的复测。

本地单元测试不替代服务器实验。当前框架的功能闭环已经在服务器验证；代码层已补齐
OpenVINO INT8 B1 Adapter 与 production compose 绑定，但服务器当前运行实例仍需重新部署并在
CPU 独占窗口复测。因此历史 2000+ Effective QPS 仍不能直接标记为当前框架验收通过。

## 2. 服务器环境

- 验证工作副本：`/home/root-nuist/wps/agent-20260818-framework-20260825`
- Conda 环境：`aether`（Python 3.13.15）
- CPU：Intel Xeon w7-3555，28 物理核 / 56 逻辑核，单 NUMA
- 指令集：AVX2、AVX-512、VNNI、BF16、AMX
- 当前功能 B1：BAAI/bge-small-zh-v1.5，FastEmbed/ONNX Runtime CPU，512 维
- P2：复用冻结的 `aether-p3-engine:acceptance` 镜像，端口 50052
- 真实依赖：Redis、Milvus、Celery、P2 gRPC、B1 Sidecar
- 运行 Profile：`production`，Demo 关闭，B3 Shadow Mode 开启

当前服务器没有 Docker Compose V2，因此 Compose 集成测试按环境能力明确跳过；服务通过
Docker CLI 和 Host 进程启动。该跳过项不能视为 Compose 部署验收通过。

## 3. 当前框架真实功能回归

### 3.1 健康状态

`GET /health` 返回 `P3_NORMAL`，P3、B1、P2、B3、Redis、Milvus、Celery 均为
`HEALTHY`。B1 模型挂载恢复后，生产 Profile 再次返回 `ready=true`。

### 3.2 B1

- 通过 P3 `POST /api/v1/embeddings` 真实调用 B1 Sidecar；
- 模型：`BAAI/bge-small-zh-v1.5`；
- 输出维度：512；
- 恢复后冒烟单次后端延迟：28.184 ms；
- 返回真实非零向量，不使用 Mock。

证据：
`artifacts/server_20260825_framework/post_restore_embedding_smoke.json`。

### 3.3 B2 长文本闭环

真实执行结果：

- Task：`PROCESSING -> SUCCEEDED`；
- 生成 2 个 Chunk；
- B1 Embedding 成功；
- P2 写入 2 个向量；
- Milvus 投影 2 个向量；
- 在线压缩比：5.018622x（5390 bytes -> 1074 bytes）；
- P2 Search 返回 2 条；
- Context 返回 2 条记忆，77 / 512 tokens，无降级；
- 持久化 Memory 的 embedding/vector projection/compression 状态分别为
  `succeeded/succeeded/compressed`。

证据：

- `artifacts/server_20260825_framework/b2_long_text_flow_retry.json`
- `artifacts/server_20260825_framework/b2_context_after_long_text.json`

### 3.4 B3

真实调度请求返回 `heuristic-v1`、Heat Score 0.66380835、Action `KEEP`。由于
B3 运行在 Shadow Mode，执行反馈为 `SHADOW_MODE/skipped`，只证明控制面决策闭环，不能描述为
物理迁移完成。

证据：`artifacts/server_20260825_framework/p3_live_flow.json`。

## 4. 服务器回归新增发现及修复

此前仅在本地执行的工程化纠偏不能覆盖以下真实环境问题。本次已修复并增加回归测试：

1. `AETHER_RUNTIME_PROFILE` 的 production 绑定已在服务器现场验证；
2. Milvus 健康探针错误调用 `connections.has_collection`，改为
   `utility.has_collection(..., using=alias)`；
3. Celery Redis Store 在不同 `asyncio.run()` 中操作和关闭连接，真实任务触发
   `RuntimeError: Event loop is closed`，现改为同一事件循环内完成；
4. P2/Milvus 召回的长文档被重新包装为 `pending`，现保持
   `embedding_status/vector_projection_status=succeeded`；
5. 源码归档依赖 `.gitignore` 才跳过生成 protobuf，Ruff 现显式排除生成目录；
6. Compose 测试只判断 Docker CLI 存在，现同时探测 Compose V2；
7. 全量 `mypy src` 原有 235 个错误未被“选定模块通过”覆盖，现明确排除机器生成 gRPC
   文件，并修复业务代码中的无效 ignore；
8. B1 容器重启后模型挂载路径漂移，已从历史模型目录恢复真实 ONNX 模型，不使用在线下载
   或 Mock 兜底。

## 5. 工程门禁

服务器最终结果：

```text
python -m pytest -q                              -> 235 passed, 2 skipped
python -m ruff check src tests scripts benchmarks -> All checks passed
python -m mypy src                               -> 118 source files, no issues
```

两个 pytest skip 分别包括当前服务器缺少 Docker Compose V2 的 Compose 测试，以及可选依赖
相关收集跳过。唯一 warning 来自 FastAPI/Starlette TestClient 对 `httpx` 的弃用提示，不影响
本轮行为验证，但应在依赖升级任务中处理。

本地复验：232 passed、1 skipped；Ruff 全量通过；Mypy 118 个源码文件通过。

## 6. B1 OpenVINO 历史记录与本次复测

QPS 口径固定为：1 条成功完成向量化的文本 Item = 1 个 Effective Query；Batch 是内部优化，
不得对已经按 Item 归一化的吞吐再次乘 Batch Size。

### 6.1 2026-08-19 隔离结果

- 运行时：OpenVINO INT8；
- 模型：BAAI/bge-small-zh-v1.5；
- 模型哈希：`0fe9def0d2bbec61cdeda7821d5c0c16ae264ba4e9f4a9be648cde2b506588ad7`；
- 28 replicas，每 replica 1 thread，HTTP concurrency 112；
- Batch 32：平均 2134.5223、最低 2127.8437、最高 2143.3185 item/s；
- Request QPS：平均 66.7038 req/s；
- 错误率：0。

按当前冻结的 Effective Item QPS 口径，该历史隔离实验达到 2000 QPS。但它属于历史
OpenVINO acceptance source/runtime，不属于当前框架 B1 Adapter 的直接结果。

原始证据：
`/home/root-nuist/wps/agent-20260818.backup-20260819-144024/artifacts/`
`p3_b1_openvino_int8_linux_20260819-111841/multi/b1_max_hardware/results.json`。

### 6.2 2026-08-25 共享服务器复测

使用同一历史运行时和完整 profile（warmup 10 s、measure 30 s、3 repeats）复测。Batch 32
平均 1301.1258 item/s，错误率 0；Batch 64 为不支持路径，错误率 1，与历史行为一致。

复测期间服务器存在连续 `lf38rec` 训练任务，占用约 2.3-5.5 个 CPU 核并造成明显抖动，
因此本结果标记为 `NOT_FORMAL_ACCEPTANCE`，不能用它推翻历史隔离结果，也不能用它宣告当前
代码通过合同指标。

证据：
`artifacts/server_20260825_framework/b1_openvino_effective_qps_assessment.json`。

## 7. 当前真实阻断项

### 7.1 OpenVINO 绑定跟进（2026-08-25 16:24）

已在服务器新部署目录
`/home/root-nuist/wps/agent-20260818-openvino-bind-20260825` 做并行验证，未覆盖现有
18080/18081 服务：

- B1 OpenVINO Sidecar：`127.0.0.1:18082`，使用历史 isolated venv 的 OpenVINO 运行时，
  通过 `PYTHONPATH` 指向当前部署源码；
- P3 Production Host：`127.0.0.1:18084`，使用 `aether` 环境，指向 18082 B1；
- B1 capability：`selected=openvino`、`actual=openvino`、`precision=INT8`、
  `fallback_used=false`、`quantization_verified=true`、`int8_weight_constants=54`；
- 模型哈希：
  `0fe9def0d2bbec61cdeda7821d5c0c16ae264ba4e9f4a9be648cde2b50658ad7`；
- P3 health：`production`、`P3_NORMAL`、ready=true，B1/P2/B3/Redis/Milvus/Celery 均
  `HEALTHY`；
- P3 -> B1 embedding smoke：`backend=openvino`、`engine=optimum-intel-openvino`、
  `quantization_type=INT8`、512 维、normalized=true。

同时修复当前 B1 Adapter 对“已量化 OpenVINO IR”的处理：当 `AETHER_B1_MODEL_PATH`
直接指向已有 OpenVINO IR 时，按 immutable artifact 加载，不再二次传入
`quantization_config`；加载后通过 INT8 常量检查 fail-closed。该修复避免 Optimum Intel 报
“model already optimized”。

证据：
`artifacts/server_20260825_openvino_bind/openvino_binding_smoke.json`。

该结果只是**当前代码绑定 OpenVINO INT8 的功能 smoke**，不是正式 2000 QPS 验收。

### 7.2 剩余阻断项

1. **P0：安排 CPU 独占窗口重跑正式吞吐验收**。冻结模型哈希、数据集哈希、CPU 亲和性、
   28x1 replicas、112 concurrency 和 Batch 32，记录系统背景负载；
2. **P0：统一 RetrievalService 生产接线**。当前 `/context` 仍是旧 Builder 加长文档追加召回，
   不是唯一检索入口；
3. **P1：为服务器部署固化模型卷和启动编排**。避免 B1 重启后模型路径漂移；
4. **P1：补齐 Outbox/Reconciler、原文单一所有权和 B3 物理迁移执行器**。

只有完成正式吞吐验收后，才能把“历史 OpenVINO 达到 2000+”升级为“当前框架正式验收通过”。
