# P3 开发与提交入口

适用 `remember/basic`、`recall/basic`、`operate/basic` 与共享 `runtime`。产品基线为 PRD V1.3；本地基座可运行，完整产品、云部署与真实 Milvus 验收尚未完成。

## 每次改动

1. 明确 B（Remember）、A（Recall/共享计算）、C（Operate）或 RF（公共机制/装配）Owner。
2. 消费其他模块的契约，不直接修改其领域状态；公共契约变更由 RF 和受影响消费者共同评审。
3. 复用 TrustedContext、UoW、Task、Outbox/Inbox、Trace 和恢复机制，不新建私有队列。
4. 事务内不做网络调用或跨 await 等待；提交前检查授权、版本、删除和租约。
5. Unknown 查询原操作，重试保持幂等身份；不得用日志代替业务事实。
6. 新节点接公共埋点，新依赖提供有界探针；记录错误码与摘要，不记录凭证和正文。
7. 填写 PR 模板，列出消费者、正常与故障验证、未覆盖范围。旧报告不覆盖，当前说明同步更新。

各开发者使用独立分支/checkout，分支建议 `p3/<owner>/<issue>-<purpose>`，从仓库实际约定的基线创建。至少一名非作者评审；接口改动须经受影响消费者确认。

## 当前需求版本

采用 `contracts/p3/prd-baseline.yaml` 登记的V1.3与租户增量；旧requirements/source-manifest保留V1.2原文索引，不是V1.3全量验收证明。新增功能须同时落实V13-TEN增量。

## 唯一字段与机制定义

- [契约入口](contracts/p3/README.md)：Python 类型为字段来源，Schema 自动生成。
- [架构与机制](docs/p3/README.md)：领域职责、状态、事务、日志和恢复。
- [流程责任](docs/p3/development/02_三流程接入与责任表.md)：提供方/消费方及交接完成条件。
- [运行接入](docs/p3/development/04_公共底座首批实现与运行.md)：可运行的 sample Handler。

面向团队阅读的完整协作基线位于交付包的 `交付成果/开发协作/`。这些外置交付文档不是测试运行依赖；独立检出本仓库即可运行下面的门禁。

## 本地门禁

Python 3.13；从仓库根目录执行：

```text
python -m pip install -r scripts/p3/requirements-collaboration.txt
python scripts/p3/validate_collaboration.py
```

模型字段变更后先执行 `python scripts/p3/generate_schemas.py`，提交生成差异。校验不需要真实 Milvus 或模型权重；首次 tokenizer 词表下载可能需要网络。受限环境预置 TIKTOKEN_CACHE_DIR。

工程示例：`python scripts/p3/demo_foundation.py --directory <新的运行目录>`。真实 Embedding/重排序实验另见 `scripts/p3/validate_recall.py`，不是本门禁声称通过的内容。

## 远端执行与评审

GitHub：`.github/workflows/p3-collaboration.yml` / job `p3-gate`。
Azure：`azure-pipelines.p3.yml` / job `P3Gate`。两者执行同一门禁命令；真实远端执行结果另验。

YAML 不会自动创建分支保护。维护者需先成功运行流水线，再将其设为目标分支必需检查并启用非作者评审。Azure Repos 用 Branch policies 的 Build validation 绑定流水线。RF 将角色对应到实际账号后配置路径评审；没有账号信息时不创建伪 CODEOWNERS。既有仓库 CI 和分支规则仍须遵守。

源码、测试、必要配置和流水线留仓库；运行数据、凭证、下载权重和原始报告放独立工作目录，不提交到 Git。
