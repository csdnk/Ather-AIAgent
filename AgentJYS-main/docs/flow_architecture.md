# Recall / Remember / Operate 当前代码结构

更新：2026-10-05。P3 本机开发、AKS 开发和部署使用一套源码；后端、身份、模型和运行目录由配置选择。旧日期报告保留其当时的验证范围。

```text
src/aether_agent_memory/
├── __main__.py            # python -m aether_agent_memory
├── remember/
│   ├── contracts/         # 记忆、来源、版本、投影与最终资格
│   ├── basic/             # 真实正文、加工、投影、生命周期与清理任务
│   └── local.py           # 组件装配工厂；要求明确的对象和缓存 provider
├── recall/
│   ├── contracts/         # Recall、Embedding、向量与 ContextPack
│   ├── basic/             # 发现、资格核验、融合、预算、最终提交
│   ├── embedding/         # 原生 BGE 与 P3 适配
│   └── vector_projection/ # 有效投影契约
├── operate/
│   ├── contracts/         # 动作、执行、观察与恢复契约
│   └── basic/             # 事件消费、热副本操作、原意图对账与修复
└── runtime/
    ├── contracts/         # 可信上下文、事务、任务、事件
    ├── foundation/        # PostgreSQL 事务、任务/事件、身份和持久日志
    ├── storage/           # Ceph 对象、Redis 热副本、Azure Milvus
    ├── temporal/          # 准入、Workflow/Activity、Worker、恢复和周期计划
    └── flows/             # CLI → application/Service → HTTP 与 Workers
```

## 运行入口和数据职责

当前入口为 `python -m aether_agent_memory`，子命令使用 `--config <service.yaml绝对路径>`。运行配置来自仓库外目录；不再使用旧 `--db` / `--cache-root` 服务入口。

```text
python -m aether_agent_memory check-config --config /absolute/deployment/service.yaml
python -m aether_agent_memory serve --config /absolute/deployment/service.yaml --require-profile development
```

`check-config` 只检查结构和身份配置。`serve` 必须连接明确的 PostgreSQL、Redis、Milvus、Ceph、原生 BGE、结构化模型和独立 Temporal。数据库保存权威元数据、幂等回执、任务、身份和日志；Ceph 保存原输入和正文；Redis 保存可重建热副本；Milvus 保存版本绑定的向量。运行数据、证书、模型及密钥放仓库外。

健康就绪、Worker 运行、保存/长期化/召回、原结果重取及恢复分别验证。现有 AKS 服务正在运行不代表它已部署本次源码；完整测试、正式 Temporal/HTTPS、HA 和灾备结论见[源码清理与部署就绪验收](../../交付成果/测试与验收/P3_源码清理与部署就绪验收_20261004.md)。

## 业务边界与仍有效的契约

Remember 拥有正文、来源、生命周期和候选资格；Recall 消费这些接口并提供共享 Embedding 和上下文；Operate 根据当前事实执行及核对原动作；RF 与 Temporal 共同保持身份、准入、事务、幂等、任务和恢复边界。`saved` 与向量 `ready` 分别取证，持久化成功不自动等于可召回。

顶层 Recall 受理/执行契约与 `examples/recall_admission.py` 仍有有效消费者，属于组件示例。它使用真实 PostgreSQL 和部署方提供的可信授权快照，只推进受理与执行状态，不生成 ContextPack；不会恢复已删除的生产 mocks。当前服务装配走 `runtime.flows`，不把组件示例当作服务启动方式。

旧 Python app、Sidecar、Celery、SQLite 适配和重复实现已退役。Rust P2 原型与生成协议仍属其他组件；正式 P2 adapter 和 Nginx gateway 另行联调，不能从 Ceph 测试推导 P2 已验收。

## 开发者及 AI 配置

完整 Azure 登录、RBAC、网络/TLS、个人数据库与存储空间、密钥注入、本机启动、AKS 测试和部署步骤见[Azure 开发环境与 AI 配置指南](../../交付成果/部署运行/P3_Azure开发环境与AI配置指南_20261004.md)。

`scripts/p3/run_aks_tests.py` 将源码快照上传独有 AKS Pod，pytest 在 AKS 内连接真实四后端。环境凭据从仓库外文件注入；测试日志和原始证据放仓库外。测试有实际执行、零 failure/error/skip、源码 hash 未变并取得终态，才能报告该次通过。全量与定点测试分开记录。
