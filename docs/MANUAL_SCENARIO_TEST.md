# 企业知识库手动场景测试

本测试仅用于本地 P2/P3 联调。它不会进入生产业务调用路径，也不会执行真实的 P1/P2 物理分层迁移。

## 启动

本仓库的 `compose.yaml` 为本地联调设置了 `AETHER_ENABLE_DEMO=true`：

```powershell
docker compose up -d --build
docker compose ps
```

打开 `http://localhost:8080`，在“场景测试”页完成下面三项输入：

1. 知识库标题，例如“2026 客户数据安全规范”。
2. 粘贴知识库文本，或选择一个小于 1 MB 的 `.txt`、`.md`、`.csv`、`.json` 文件。
3. 输入用户问题，例如“发生疑似数据泄露后的首要处理要求是什么？”。

点击“运行完整测试”。

## 实际执行内容

一次测试使用同一个 `request_id` 和 `trace_id`，依次产生以下真实调用和记录：

| 顺序 | 模块 | 结果 |
| --- | --- | --- |
| 1 | P2 E2 ObjectService | 知识库文本写入对象存储卷 `engine-data`。 |
| 2 | P3 B1 EmbeddingPipeline | 用当前 Mock Embedding 生成 32 维向量。 |
| 3 | P2 E1 VectorService | 向量写入当前运行中的 E1 索引。 |
| 4 | P3 B2 MemoryService | 文档创建为工作记忆。 |
| 5 | P3 B2 MemoryService | 用户问题创建为语义记忆。 |
| 6 | P3 B2 ContextPackBuilder | 按用户问题召回工作/语义记忆，构建上下文。 |
| 7 | P3 B3 HeuristicScheduler | 以访问频率、相关度、重要度和时效性计算调度评分。 |

预置的高相关度输入通常会产生从 `L3` 到 `L2` 的 `prefetch` 决策。页面显示的 `success` 回执来自 `P2MigrationExecutor`，表示 P2 已完成分段冻结、逻辑块路由提交和路由版本更新；它不移动真实对象字节，也不证明生产存储的物理迁移完成。

“全链路流程”页会展示最近 100 次实际测试的完整步骤和最近 100 条调度记录。历史保存在 `p3` 服务内存中，重启 `p3` 后会清空；B2 SQLite 记忆与 P2 E2 对象卷仍然保留。

## 生产部署边界

生产部署不要设置 `AETHER_ENABLE_DEMO=true`。未启用时，以下测试接口不可访问：

- `POST /api/demo/session`
- `POST /api/run-smoke`

保留的业务能力仍是 B1、B2、B3 与 P2 的独立 HTTP/gRPC API。B3 已接入 P2 SegmentControlService；生产启用物理迁移前，还需接入实际字节搬运组件，并完成鉴权、容量、超时和回滚验收。
