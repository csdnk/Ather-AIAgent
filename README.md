# Aether P2/P3 融合项目

本仓库以 `aether-agent-memory-new` 为根目录，将原本独立的两个单体模块整合为可共同构建、部署和运行的项目：

- `engine/`：P2 Rust 存储基座，包含 E1 向量引擎、E2 对象引擎和 E3 图引擎。
- `src/aether_agent_memory/`：P3 Python 语义层，包含 B1 Sidecar 嵌入、B2 Agent 记忆与 B3 智能调度。

P2/P3 统一使用 [engine/proto/aether_engine.proto](engine/proto/aether_engine.proto) 作为 gRPC 契约。Docker Desktop 运行 Linux 容器；Windows 仅作为开发主机。

> 当前常驻联调实际调用 P2 的 E1 VectorService、E2 ObjectService 和 SegmentControlService。B3 通过 `P2MigrationExecutor` 执行冻结、完成/失败回写、路由版本更新与幂等恢复。P2 E3 GraphEngine 已在工程中，但图数据 API 不在当前冒烟链路中。当前提交的是确定性逻辑块路由，真实对象字节的物理搬运仍由外部迁移组件负责。

## 项目结构

```text
.
├── engine/                         # P2 Rust 工作区
│   ├── engines/e1-vector/          # E1 向量引擎
│   ├── engines/e2-object/          # E2 对象引擎
│   ├── engines/e3-graph/           # E3 图引擎
│   ├── services/ae-server/         # 当前 P2 gRPC 服务（50052）
│   └── proto/aether_engine.proto   # P2/P3 共享契约
├── src/aether_agent_memory/        # P3 B1、B2、B3
│   ├── b1/                          # FastEmbed/ONNX CPU Sidecar 与客户端适配
│   └── b2/                          # 记忆服务、Celery 长文本任务与 Milvus 投影
├── scripts/p3_service.py           # 常驻 HTTP 服务（8080）
├── scripts/dashboard_page.py       # 浏览器仪表盘
├── tests/                          # 单元、集成与 Compose 测试
└── compose.yaml                    # P2/P3 联调编排
```

## 浏览器运行

要求：Docker Desktop 已启动并能拉取 `python:3.13-slim`、`rust:1.85-bookworm`、
`debian:bookworm-slim` 和 Redis 镜像。首次启动 B1 Sidecar 时还会下载
FastEmbed 模型。

在仓库根目录运行：

```powershell
docker compose up -d --build
docker compose ps
```

容器内服务端口保持固定，宿主机映射可通过 `AETHER_HOST_P3_PORT`、
`AETHER_HOST_P2_PORT`、`AETHER_HOST_B1_PORT` 和 `AETHER_HOST_MILVUS_PORT` 调整。
`8080` 只属于 `p3_service.py`；旧 `scripts/dashboard.py` 默认使用 `8081`。

Milvus 投影默认关闭。需要额外验证投影时使用：

```powershell
$env:AETHER_B2_MILVUS_PROJECTION = "true"
docker compose --profile milvus up -d --build
```

打开 [http://localhost:8080](http://localhost:8080)。该地址包含两个页面视图：

- **运行测试**：点击“执行融合验证”，查看 P2 E2、P3 B1、P2 E1、P3 B2、P3 B3 和 `P2MigrationExecutor` 的实际执行结果，包括迁移 ID、路由版本和逻辑块路由。
- **项目流程**：查看完整模块架构、最近 100 次运行链路和最近 100 条 B3 调度记录。也可直接打开 [http://localhost:8080/#flow](http://localhost:8080/#flow)。

查看日志与停止服务：

```powershell
docker compose logs -f p3 b1-sidecar celery-worker engine
docker compose down
```

## HTTP 接口

| 方法 | 地址 | 说明 |
| --- | --- | --- |
| `GET` | `/` | 8080 仪表盘 |
| `GET` | `/health`、`/api/status` | 服务和 P2 连通状态 |
| `GET` | `/api/flows` | 最近完整联调链路 |
| `GET` | `/api/schedules` | 最近 B3 调度记录 |
| `POST` | `/api/run-smoke` | 触发 P2/P3 完整冒烟验证 |
| `POST` | `/api/demo/session` | 本地演示：知识库上传、对话、记忆召回和调度验证 |
| `POST` | `/api/v1/embeddings` | B1 嵌入 |
| `POST` | `/api/v1/memory/events` | B2 记忆写入 |
| `POST` | `/api/v1/b2/long-text` | B2 异步提交长文本 |
| `GET` | `/api/v1/b2/tasks/{task_id}` | 查询 B2 异步任务状态 |
| `POST` | `/api/v1/b2/search` | 以 B1 query 向量检索 P2 E1 记忆块 |
| `POST` | `/api/v1/context` | B2 上下文构建 |
| `POST` | `/api/v1/schedules` | B3 单次调度 |

快速验证：

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod -Method Post http://localhost:8080/api/run-smoke -ContentType application/json -Body '{}'
Invoke-RestMethod http://localhost:8080/api/flows
```

请求体示例和页面说明见 [docs/WEB_DASHBOARD.md](docs/WEB_DASHBOARD.md)，架构和验证边界见 [docs/INTEGRATION.md](docs/INTEGRATION.md)。手动企业知识库测试流程见 [docs/MANUAL_SCENARIO_TEST.md](docs/MANUAL_SCENARIO_TEST.md)。

## 命令行与本地测试

Docker 一次性冒烟验证：

```powershell
docker compose --profile demo up --build --abort-on-container-exit --exit-code-from p3-demo
```

本地开发：

```powershell
uv sync --extra b1-sidecar --group dev --group demo
uv run python scripts/generate_proto.py
uv run pytest
cargo test --manifest-path engine/Cargo.toml --workspace
```

当前 P3 代码要求 Python 3.13；Docker 镜像已包含对应运行环境。

## B1 CPU Embedding Sidecar

B1 是独立的应用层拦截服务：`POST /v1/intercept` 接收文本或文本批次，执行真实
FastEmbed + ONNX Runtime CPU Embedding，返回 512 维归一化 FP32 向量、分块偏移、状态、
指标和流程事件。当前不融合向量查询与图查询。OpenVINO/IPEX 和 AVX-512/AMX 是可替换策略
预留，不是当前已验证的生产后端。

Windows 手动安装与完整边界说明见 [docs/B1_MANUAL_INSTALL_WINDOWS.md](docs/B1_MANUAL_INSTALL_WINDOWS.md)，
一键安装见 [docs/B1_ONE_CLICK_INSTALL_WINDOWS.md](docs/B1_ONE_CLICK_INSTALL_WINDOWS.md)，
单核 SIMD/NumPy/Rust 预研见 [docs/B1_SIMD_NUMPY_RUST_RESEARCH.md](docs/B1_SIMD_NUMPY_RUST_RESEARCH.md)。

快速启动：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\install_b1.ps1
.\scripts\start_b1_sidecar.ps1
# 另开窗口
.\scripts\start_b1_dashboard.ps1
```

边界数据集运行：

```powershell
.\.venv-b1\Scripts\python.exe .\examples\generate_b1_edge_dataset.py
.\.venv-b1\Scripts\python.exe -m aether_agent_memory.b1.dataset_runner `
  .\runtime\b1\b1-edge-dataset.jsonl --batch-size 4
```

## B1/B2 异步记忆集成

合并后的长文本链路将 B1 和 B2 的职责严格分开：B2 接收任务、保存 Redis 状态，并以
P2 E2/E1 作为原文与向量的正式存储；B1 是唯一的文本切分和向量生成实现。Milvus 仅为
可选检索投影，不再是正式数据来源。

```text
POST /api/v1/b2/long-text
  -> P2 E2: 保存长文本原文
  -> Redis: PENDING
  -> Celery worker
  -> B1 Sidecar: POST /v1/intercept
  -> chunk_id + chunk_text + 512-dim vector
  -> P2 E1: 按 tenant/user/agent/dimension 隔离写入
  -> Milvus（可选投影）
  -> Redis: SUCCEEDED / FAILED
```

Compose 中的服务地址已经配置完成：

| 变量 | 使用方 | Compose 默认值 |
| --- | --- | --- |
| `AETHER_B1_SIDECAR_URL` | P3 同步 `EmbeddingPipeline` | `http://b1-sidecar:18081` |
| `AETHER_B1_EMBEDDING_URL` | B2 Celery 与检索 | `http://b1-sidecar:18081/v1/intercept` |
| `AETHER_P2_GRPC` | P3 与 Celery 的 P2 E1/E2 客户端 | `engine:50052` |
| `AETHER_P2_BUCKET` / `AETHER_P2_COLLECTION` | B2 正式对象桶与向量集合前缀 | `p3-memory` / `p3` |
| `AETHER_B2_BROKER_URL` | Celery broker | `redis://redis:6379/0` |
| `AETHER_B2_RESULT_BACKEND` | Celery result backend | `redis://redis:6379/1` |
| `AETHER_B2_TASK_STATUS_URL` | B2 任务状态 | `redis://redis:6379/2` |
| `AETHER_B2_MILVUS_PROJECTION` | 是否额外写入 Milvus | `false` |
| `AETHER_B2_VECTOR_DIMENSION` | 可选 Milvus collection 维度 | `512` |

启用 Milvus 投影时，`AETHER_B2_VECTOR_DIMENSION` 必须和 B1 `/health/ready` 返回的
`dimension` 相同。P2 E1 集合名称自动包含向量维度和租户作用域，替换 B1 模型不会把
新维度向量写入旧集合。

完整启动、接口和演示步骤见 [docs/B2_ASYNC_MEMORY_DEMO.md](docs/B2_ASYNC_MEMORY_DEMO.md)，
B1 请求映射、响应格式和失败语义见 [docs/B2_B1_对接清单.md](docs/B2_B1_对接清单.md)。
`scripts/b2_async_demo.py` 会提交长文本、轮询任务状态并检索写入的分块。B2 的
`long_text.py` 只保留给开发辅助和测试，正式链路不会使用它进行切分。
