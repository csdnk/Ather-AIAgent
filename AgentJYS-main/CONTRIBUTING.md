# P3 开发与提交入口

当前源码使用 Azure PostgreSQL、Redis、Milvus、Ceph 和独立 Temporal，同一套源码通过明确配置切换环境。开发者及 AI 助手先读 [Azure 开发环境与 AI 配置指南](../交付成果/部署运行/P3_Azure开发环境与AI配置指南_20261004.md)。旧 SQLite、RF 调度、协作演示脚本及重复依赖清单已退役；旧数据迁移取消。

## 每次改动

1. 明确 B（Remember）、A（Recall/共享计算）、C（Operate）或 RF（公共机制/装配）Owner。
2. 消费其他模块契约，不直接修改其领域状态；公共接口变更由 RF 和受影响消费者共同评审。
3. 复用 TrustedContext、PG UoW、Temporal、Outbox/Inbox、Trace 和原操作恢复，不新建私有调度器。
4. 事务内不做网络调用或跨 await 等待；提交前检查授权、版本、删除和租约。
5. Unknown 查询原 operation/job；保持幂等身份，不重发原写入。
6. 新节点接公共埋点，新依赖提供有界探针；不记录凭据和正文。
7. PR 写明行为、消费者、真实与故障验证、未覆盖项；旧验收报告保留原日期及结论。

每位开发者使用独立 `codex/` 分支，从维护者指定的基线或 reviewed SHA 创建。接口改动由受影响消费者评审；PR 由维护者决定合并。不要修改其他开发者的 dirty 文件、运行服务或共享数据。

## 需求与接口来源

当前需求以 `contracts/p3/prd-baseline.yaml` 登记的 V1.3 与租户增量为准。旧 requirements/source-manifest 的 V1.2 原文索引用于追溯，不能作为 V1.3 全量验收证明。

- [契约入口](contracts/p3/README.md)：Python 类型为字段来源，Schema 自动生成。
- [架构与机制](docs/p3/README.md)：领域职责、状态、事务与恢复。
- [流程责任](docs/p3/development/02_三流程接入与责任表.md)：提供方/消费方及交接条件。
- [当前 CI 检查](docs/p3/development/11_CI检查与复验.md)：静态检查、契约与 AKS 真实后端测试的边界。
- [Recall 冷热地址读取契约](docs/p3/development/17_Recall冷热地址读取契约.md)：现行正文接口、缓存回源规则、模块影响及离线验收入口。

## 当前检查入口

Python 3.13；在 `AgentJYS-main` 执行，venv、模型、缓存、私密配置和日志在仓库外：

```powershell
python -m pip install --upgrade pip
python -m pip install -e '.[embedding-onnx,resource-documents,remember-ceph]' --group dev
python -B scripts/generate_proto.py
python -B -m ruff check src tests scripts
python -B -m mypy src --cache-dir <外部缓存目录>
python -B scripts/p3/generate_schemas.py --check
python -B -m pip wheel --no-deps --wheel-dir <外部wheel目录> .
```

模型字段变更后执行 `generate_schemas.py`，提交生成差异，再以 `--check` 复验。静态检查不能证明数据库或业务已运行。

真实 Python 回归使用 `scripts/p3/run_aks_tests.py`：pytest 在独有 AKS Pod 运行，连接真实 Azure 后端及 test-owned Temporal。完整配置、公有资产和零跳过判定见开发者指南；缺权限或 backend 时明确失败，不切换旧 SQLite/Mock。外部 Rust P2 和 Nginx 网关测试单独配置并报告未执行边界。

Web 用 Node.js 22，执行 `npm ci`、`npm run lint`、`npm test`、`npm run build`；可用 `AETHER_WEB_CACHE_DIR`、`AETHER_WEB_BUILD_DIR` 指定外部输出。Rust `engine` 的格式与测试按其 owner 的接口责任执行，不借清理改写提供方。

## 远端执行与评审

GitHub 的实际入口是仓库根 `.github/workflows/`，嵌套重复工作流已删除。

- `ci.yml`：Python 静态/Schema、冷热地址离线回归、平台测试、wheel，以及 Web 与 Rust 检查。
- `p3-contracts.yml`：独立契约检查。
- `p3-azure-tests.yml`：手动指定 reviewed full SHA，通过 `azure-tests` environment 的受控 Secret 在 AKS 跑真实回归。
- `azure-pipelines.p3.yml`：源码与契约检查入口。

维护者须配置 GitHub environment/Secret、真实执行并核对 checks 后，再决定 required checks/评审规则；YAML 不会自动启用分支保护。403 也不能证明“仅维护者可以合并”的规则已生效。

源码、测试、运行模板和必要文档留 Git。运行数据、凭据、kubeconfig、下载权重、原始日志不提交；面向用户的正式结论放 `交付成果/`，处理记录放项目外。
