# Web 联调仪表盘

浏览器仪表盘由常驻的 `p3` 服务提供，访问地址固定为 [http://localhost:8080](http://localhost:8080)。它包含两个视图，使用同一个端口：

- **运行测试**：触发一条真实的 P2/P3 冒烟链路，并逐步显示组件、输出与耗时。
- **项目流程**：显示 P2/P3 模块架构、最近 100 次运行链路，以及最近 100 条 B3 调度结果。

P2 的 E1 VectorService、E2 ObjectService 在当前冒烟链路中会被实际调用。E3 GraphEngine 已在 P2 工程中存在，但 `ae-grpc` 目前没有暴露 E3 gRPC 服务，因此页面会明确显示为“未接入本链路”。

## 启动

在仓库根目录执行：

```powershell
docker compose up -d --build
docker compose ps
```

正常情况下可看到：

| 服务 | 主机端口 | 职责 |
| --- | --- | --- |
| `engine` | `50052` | P2 E1 向量、E2 对象与 SegmentControl gRPC 服务 |
| `p3` | `8080` | P3 常驻 HTTP 服务、仪表盘与 API |

查看实时日志：

```powershell
docker compose logs -f p3 engine
```

停止服务但保留数据卷：

```powershell
docker compose down
```

## 运行测试说明

点击“执行融合验证”会依次执行：

1. P2 E2 ObjectService 写入原始对象。
2. P3 B1 切分文本并生成 32 维 Mock Embedding。
3. P2 E1 VectorService 持久化该向量。
4. P3 B2 写入工作记忆。
5. P3 B2 召回记忆并构建 Context Pack。
6. P3 B3 HeuristicScheduler 计算分层调度建议。
7. P3 B3 `P2MigrationExecutor` 调用 P2 SegmentControlService 冻结分段并提交确定性逻辑块路由。
8. P2 返回迁移 ID、路由版本、逻辑块路由和幂等标记；失败时 P3 回写失败以释放冻结状态。

其中 `success` 表示 P2 控制面迁移闭环和逻辑路由提交成功。当前执行器不搬运对象字节，因此该结果不代表物理层级迁移已经完成；物理搬运仍由外部迁移组件负责。

## HTTP 接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/` | 仪表盘（`#run` 和 `#flow` 两个视图） |
| `GET` | `/health` | P2 可用性和服务状态 |
| `GET` | `/api/status` | P2 端点、最近一次验证及摘要 |
| `GET` | `/api/flows` | 最近 100 次完整跨模块运行链路 |
| `GET` | `/api/schedules` | 最近 100 条 B3 调度记录 |
| `POST` | `/api/run-smoke` | 触发完整 P2/P3 冒烟验证 |
| `POST` | `/api/demo/session` | 本地演示：知识库上传、用户对话、记忆召回和调度验证 |
| `POST` | `/api/v1/embeddings` | 运行 B1 嵌入管道 |
| `POST` | `/api/v1/memory/events` | 写入 B2 记忆事件 |
| `POST` | `/api/v1/context` | 构建 B2 Context Pack |
| `POST` | `/api/v1/schedules` | 执行 B3 调度并写入调度历史 |

PowerShell 示例：

```powershell
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod -Method Post http://localhost:8080/api/run-smoke -ContentType application/json -Body '{}'
Invoke-RestMethod http://localhost:8080/api/flows
Invoke-RestMethod http://localhost:8080/api/schedules
```

`/api/demo/session` 与 `/api/run-smoke` 仅在 `AETHER_ENABLE_DEMO=true` 时可用。Compose 本地联调默认启用该变量；生产部署不要设置它。详细的表单操作和验证边界见 [MANUAL_SCENARIO_TEST.md](MANUAL_SCENARIO_TEST.md)。

单独调用 B3 调度：

```powershell
$body = @{
  objects = @(@{
    object_id = 'documents/example.txt'
    object_type = 'document'
    current_tier = 'L3'
    access = @{ access_frequency = 1.0; recency_score = 1.0; hit_rate = 1.0 }
    semantic = @{ semantic_relevance = 1.0; importance = 1.0 }
    business_priority = 1.0
  })
} | ConvertTo-Json -Depth 5
Invoke-RestMethod -Method Post http://localhost:8080/api/v1/schedules -ContentType application/json -Body $body
```

## 常见问题

| 现象 | 处理 |
| --- | --- |
| P2 显示不可用 | 执行 `docker compose ps`，再查看 `docker compose logs --tail=200 engine p3`。 |
| 构建阶段无法拉取镜像 | 这是 Docker 访问 Docker Hub 的网络/代理问题，不是 P2/P3 代码不匹配；先执行 `docker pull python:3.13-slim`、`docker pull rust:1.85-bookworm` 和 `docker pull debian:bookworm-slim` 排查。 |
| 8080 被占用 | 修改 `compose.yaml` 的左侧端口，例如 `8081:8080`，再访问 `http://localhost:8081`。 |
| 页面没有历史 | 历史保存在 `p3` 容器内存中，重启 `p3` 容器会清空；P2 和 B2 的数据卷不受此影响。 |
