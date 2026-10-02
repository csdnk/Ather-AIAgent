# 租户、日志与 Trace：复用项目已有 PostgreSQL

更新日期：2026-10-02。本次 PR 仅覆盖租户、日志、Trace 及必要兼容，不包含旧 Demo 数据迁移、P2 Mock 存储改造和 Operate 执行库改造。

## 1. 这次解决什么问题

GitHub 项目已有 PostgreSQL 状态存储和节点日志实现。租户身份与 OpenTelemetry 接入应在这套实现上工作，避免再维护另一套分库配置、日志表和数据库适配器。

例如，用户登录组织 A 后发起诊断：P3 验证身份与权限，执行诊断，返回 Trace ID。节点记录写入项目配置的 PostgreSQL；A 可以看到本组织记录，组织 B 无法通过该 Trace ID 读取 A 的记录。

## 2. 每类数据放在哪里

| 数据 | 位置 | 实现 |
|---|---|---|
| P3 身份映射、租户授权及业务状态 | 项目配置的 PostgreSQL | 上游 PostgresUnitOfWork / capability_records |
| 页面节点日志、Trace 列表与详情 | 同一个 PostgreSQL 的 node_logs / log_meta | 上游 PostgresTelemetry；保留原表结构 |
| HTTP 请求结构化日志 | 进程标准输出 | structlog；不会自动把全部控制台输出写入数据库 |
| OpenTelemetry span | SDK 处理器；可选导出到配置的 OTLP 接收端 | 与节点日志关联 Trace ID；不等同于完整 span 已入 PostgreSQL |
| Keycloak 账号、密码、组织成员 | Keycloak 自己配置的数据库 | 不写进 P3 的 node_logs |

状态和节点日志使用同一 PostgreSQL 连接配置，但使用独立连接池和事务。单次日志写入失败不应回滚已提交的业务状态；同时，整库不可用仍可能影响两者。

这里的“项目 PostgreSQL”是项目部署时配置的 PostgreSQL 服务，GitHub 本身不提供正在运行的数据库。

## 3. 如何接入已有项目

保留原有 P2、Temporal、模型、身份文件和运行目录设置，在原部署配置中使用项目已有字段：

```yaml
metadata_backend: postgresql
postgres_dsn_env: AETHER_POSTGRES_DSN
```

AETHER_POSTGRES_DSN 的真实值通过仓库外的环境配置提供。不要把密码写进 YAML、文档或提交到 Git。

- 已使用上游 PostgreSQL 的部署沿用原连接；不要求新建独立 p3_logs 库。
- 仍用上游 SQLite 配置的开发测试继续兼容；这不意味着 PostgreSQL 模式会把节点日志退回 SQLite。
- PostgreSQL 连接缺失时明确报错；不能声称已落库却实际写到另一份本地文件。
- 本次不改变 P2、Operate executor 和 Temporal 的存储选择；不承诺整个项目中没有 SQLite 文件。
- 旧本地 Demo 的 postgresql 四库配置不是上游配置，不能直接换名称后连接旧表。现有数据保持原样，迁移另行处理。

示例见 configs/p3.postgres.example.yaml。它是配置参考，不是自动部署脚本。

## 4. 必要的兼容处理

1. PostgresTelemetry 复用上游写入逻辑，并初始化继承的 tracing 属性，使未挂载或已挂载 OpenTelemetry 的节点 span 都可执行。
2. 健康检查优先调用存储对象自己的 probe，PostgreSQL 下不会把数据库当作 SQLite 文件探测；SQLite 路径保持可用。
3. 注册和登录入口、JWT 校验、Keycloak 组织权限、日志和 Trace 查询使用同一可信身份上下文。
4. 新增 /p3/auth/config 与 /p3/auth/me 后，同步接口清单和相关计数断言，避免五场景覆盖统计把未调用的新接口误报为已验证。
5. 保持上游 capability_records 的 document 投影、状态访问和迁移来源校验，不另写替代存储。

## 5. 如何验证

在独立测试库中设置 P3_TEST_STATE_DSN，数据库名必须以 p3_test_ 开头。测试会重建该测试库 public 下的表，绝不能指向现有 Demo 或生产数据。

```powershell
python scripts/p3/validate_postgres.py --directory '<仓库外测试结果目录>'
```

该入口要求数据库测试全部执行，不接受跳过。覆盖：跨租户查询、重启后日志与状态持久化、日志清理、写日志失败与业务提交隔离、OpenTelemetry 与 HTTP Trace ID 关联、缺失连接拒绝。CI 使用一次性 PostgreSQL 服务。

其他租户/JWT/组织和可观测性测试见 test_tenant_observability.py、test_keycloak_organizations.py、test_observability.py。身份服务使用受控测试响应；这不等同于重新完成真实浏览器注册、邀请、邮件和生产压力验收。

## 6. 本次 PR 不包含

- 不另建本地 PostgreSQL 环境，不迁移或重置现有 Demo 和 Keycloak 数据。
- 不提交本地 P2 PostgreSQL Mock、Operate PostgreSQL executor、旧四库迁移与部署工具。
- 不修改 Remember/Recall/Operate 的业务策略，不验证真实 P2、模型与 Milvus 全链路。
- 不部署 Loki、Tempo 或 OTLP Collector；不承诺集中日志与完整跨服务追踪已上线。
