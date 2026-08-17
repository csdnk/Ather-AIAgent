# B2 异步长文本处理

## 职责与链路

长文本由 B2 接收和编排，B1 负责唯一的切分与向量化实现：

```text
P3 HTTP -> P2 E2 原文 -> Redis PENDING -> Celery worker -> B1 Sidecar
       -> B1 chunk_id/chunk_text/vector -> P2 E1 -> Redis SUCCEEDED
                                      \-> Milvus（可选投影）
```

`b2/long_text.py` 仅保留给本地辅助和单元测试，不能用于正式写入链路。

## Compose 启动

在项目根目录运行：

```powershell
docker compose up --build
```

默认编排会启动 P2、P3、B1 Sidecar、Redis 和 Celery worker。B1 模型默认是
`BAAI/bge-small-zh-v1.5`，向量维度为 512。启用可选 Milvus 投影时运行：

```powershell
$env:AETHER_B2_MILVUS_PROJECTION = "true"
docker compose --profile milvus up --build
```

该 profile 会额外启动 etcd、MinIO 和 Milvus；此时 `AETHER_B2_VECTOR_DIMENSION` 必须与
B1 `/health/ready` 返回的维度一致。

## 演示

服务启动后，另开一个终端运行：

```powershell
docker compose exec p3 python scripts/b2_async_demo.py
```

脚本提交长文本，轮询 Redis 中的 `PENDING`、`PROCESSING`、`SUCCEEDED` 或 `FAILED`
状态，并使用 B1 的 query 向量从 P2 E1 检索同一 tenant、user、agent 范围内的文本块。

## HTTP 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `POST` | `/api/v1/b2/long-text` | 先将原文写入 P2 E2，再提交异步任务；返回任务标识、P2 object key 和 content ref。 |
| `GET` | `/api/v1/b2/tasks/{task_id}` | 查询 Redis 中的任务状态。 |
| `POST` | `/api/v1/b2/search` | 提交 `query`、`tenant_id`、`user_id`、`agent_id` 和可选 `limit`，检索 P2 E1。 |

P2 E1 按 tenant、user、agent 和向量维度映射到稳定集合，返回结果仍会再次核对隔离元数据。
`AETHER_B2_MILVUS_PROJECTION=true` 时，Worker 才会额外写 Milvus。

## B1 协议适配

B2 使用 `AETHER_B1_EMBEDDING_URL`，Compose 默认值为
`http://b1-sidecar:18081/v1/intercept`。适配器会将 B2 的 `memory_id` 放入 B1 的
`metadata`，因为 Sidecar 顶层采用严格字段白名单；再把 Sidecar 的 `results[].chunks[]`
转换为 B2 的 `records[]`，包含 `chunk_id`、`chunk_text`、`vector`、偏移和模型名。
