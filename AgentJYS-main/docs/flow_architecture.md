# Recall / Remember / Operate 当前代码结构

更新：2026-09-20。本页替换原受理骨架阶段的结构说明；旧运行证据仍保留在各日期报告中。

```text
src/aether_agent_memory/
├── remember/
│   ├── contracts/          # 记忆、来源、版本、最终资格
│   └── basic/              # 实际文本生命周期、加工/投影/清理任务
├── recall/
│   ├── contracts/          # Recall、Embedding、向量和ContextPack
│   ├── basic/              # 检索、融合、重排序、token预算、Milvus适配
│   ├── embedding/          # 原生BGE与当前P3适配p3.py
│   └── vector_projection/  # 保留的投影机制契约
├── operate/
│   ├── contracts/          # 决策、动作、执行与观察
│   └── basic/              # 事件消费、自动缓存调度、原动作对账
├── runtime/
│   ├── contracts/          # 可信上下文、事务、任务、事件
│   ├── foundation/         # SQLite事务、任务/事件、日志和恢复
│   └── flows/              # 当前Host/CLI、探针与三流程装配
└── api/、bootstrap/、b1/等  # 有消费者的兼容路径，不能直接删除
```

## 当前运行方式

正式本地入口是 `python -m aether_agent_memory.runtime.flows`，必须提供 `--db` 和 `--cache-root`；身份由部署配置提供。复现示例和参数见[三流程运行说明](p3/development/05_三个流程基础实现与联调.md)。

默认真实 BGE 512 维 Embedding、SQLite 向量和实际 tokenizer。`configs/recall.rerank.local.json` 显式启用真实重排序；Milvus SDK 适配已实现但无真实服务联调证据。Remember 默认仍为 LiteralExtraction；Operate 实际操作本地缓存文件，未完成生产介质分层。

## 边界与兼容

B 拥有记忆资格；A 消费 B 的读取接口并提供共享 Embedding/VectorPort；C 消费两侧事件、依据当前事实执行及对账；RF 提供共同运行机制和 Host 装配。具体见[协作责任](p3/development/02_三流程接入与责任表.md)。

顶层 Recall 受理/执行骨架及 `examples/recall_admission.py` 保留其消费者和回归，不是当前 runtime.flows 的装配链。旧 memory/recall 等兼容导出与旧 HTTP v1 仍需单独回归。不要把当前 basic 流程迁回旧骨架，也不要把旧路由的产品能力算作新 Host 已交付。

## 开发验证

```text
python scripts/p3/validate_collaboration.py
```

[贡献规范](../CONTRIBUTING.md)规定契约、日志、健康和恢复接入，所有组共用。新 HTTP Host、Azure/AKS、OTel 导出及生产验收仍是后续工作。
