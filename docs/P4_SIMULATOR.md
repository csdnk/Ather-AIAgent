# P4 Reference Agent Simulator

## 定位

P4 Simulator 是一个独立的上层 Agent/BFF 参考实现，用来验证公司平台、Web、小程序或其他
渠道未来如何接入 P3。它不是 P3 的第四个算法模块，也不导入 P3 Python 实现；P4 与 P3
之间只有 HTTP v1 契约。

```text
Web / 小程序 / 公司平台
          |
          v
P4 Reference Agent :8090
  - Agent 定义
  - Session 与消息
  - 渠道/BFF 适配
  - P3 HTTP Client
          |
          v
AetherBrain P3 :8080
  - Context
  - Memory Event
  - Long-text Task
          |
          v
B1 / B2 / B3 -> P2 / Redis / Milvus / Celery
```

## 本地启动

先启动 P3，再启动 P4：

```powershell
$env:PYTHONPATH='src;.'
python scripts/p3_service.py
```

```powershell
$env:PYTHONPATH='src;.'
$env:AETHER_P3_BASE_URL='http://localhost:8080'
python scripts/p4_simulator.py
```

默认 P4 地址为 `http://localhost:8090`。可使用 `AETHER_P4_PORT` 修改端口，使用
`AETHER_P4_P3_TIMEOUT_SECONDS` 设置 P4 调用 P3 的有界超时。

完整容器模式：

```powershell
docker compose --profile p4 up -d --build
```

## P4 API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | P4 存活与 P3 连通状态 |
| `GET` | `/api/v1/agents` | 查看 P4 Agent |
| `POST` | `/api/v1/agents` | 创建 P4 Agent |
| `POST` | `/api/v1/sessions` | 创建会话 |
| `GET` | `/api/v1/sessions/{session_id}` | 查询会话与消息 |
| `POST` | `/api/v1/sessions/{session_id}/messages` | 执行 Context -> Agent -> Memory 回合 |
| `POST` | `/api/v1/sessions/{session_id}/documents` | 提交 P3 长文本任务 |
| `GET` | `/api/v1/tasks/{task_id}` | 代理查询 P3 任务状态 |

## 两条标准演示路径

### 对话记忆

1. P4 创建 Agent Session。
2. 用户发送问题，P4 先调用 P3 Context。
3. 参考 Agent 基于真实 ContextPack 生成确定性回答；它不冒充外部 LLM。
4. P4 将回合写回 P3 Memory Event。
5. 勾选 Durable Memory 时写入 `user_memory`，新会话仍可按相同 tenant/user/agent 召回。

### 文档记忆

1. P4 提交文本与 `source_id`。
2. P3 返回真实 `task_id` 和 `PENDING` 状态。
3. P4 代理轮询 P3 Task API，原样展示 `PROCESSING`、`SUCCEEDED` 或 `FAILED`。

## 当前限制

- P4 的 Agent 回复是确定性参考回复，不接入外部 LLM。
- Agent 与 Session 暂存在 P4 进程内存中，进程重启后清空。
- 当前没有登录、RBAC、多租户控制台和公司平台适配器；这些属于正式 P4 产品建设。
- P3 降级或失败时 P4 返回真实错误，不自动切换 Mock 数据。
