# 按 Recall / Remember / Operate 组织新框架

新业务代码按三个流程组织。Recall 的实现已经迁到顶层 `recall/`，Embedding 是其中的独立共享能力，代码不调用 B1。Remember 提交已批准的 Passage 输入时也使用这个能力，不另建一套推理重试逻辑。

```text
src/aether_agent_memory/
├── recall/                         # A：召回流程
│   ├── admission.py                # 输入、授权、策略绑定与幂等受理
│   ├── routing.py                  # scope_union_v1 自动选路
│   ├── models.py                   # 请求、索引、执行、检查点
│   ├── ports.py                    # 准入、执行存储与重放边界
│   ├── store.py                    # 原子记录 Port 的业务适配；不是数据库
│   ├── execution.py                # 十三态、租约与状态版本推进
│   ├── query.py                    # 已有 Query 实验实现，待接执行骨架
│   ├── embedding_input.py          # 已绑定 Query 的授权与输入解析
│   ├── embedding/
│   │   ├── models.py               # Query/Passage、模型空间、输入/结果绑定
│   │   ├── service.py              # 共享执行、限流、重试、结果核验
│   │   ├── backends.py             # 真实 ONNX / OpenVINO / IPEX 推理
│   │   ├── native.py               # 原生推理绑定、token 校验与证据
│   │   └── __main__.py             # 独立模型加载与检查入口
│   └── vector_projection/         # A：向量投影机制模型与 Port
├── remember/                       # B：流程入口已预留，领域实现待迁移
├── operate/                        # C：已有模型、策略、控制器与 Port
├── runtime/                        # 公共契约和运行时基础设施
├── api/、bootstrap/、adapters/      # 接入、装配与基础设施适配
└── p2/                             # 存储 Provider 的协议与适配
```

## 依赖与职责

- Recall 的 Query 阶段依赖 `SemanticEmbeddingCapability`，实现来自 `recall.embedding`。原有真实引擎已迁到 `recall.embedding.backends`，通过 `NativeEmbeddingBackend` 注入；详见 [真实推理迁移与运行](recall_embedding_native.md)。
- Remember 负责记忆事实、片段、生命周期、Passage 输入批准和投影 Ready 判定。当前仅建立命名空间，没有把旧 B2 包装成新 Remember，也不宣称 Remember 已完成。
- Operate 保留已有流程实现，后续按 C 的流程继续迁移相关调度入口。
- A 负责请求/执行数据定义、恢复规则与存储 Port。事务、数据库、本地持久化和真实重启恢复由 RF 提供与验收；内存测试替身只验证业务规则。
- 公共包采用按需导出。单独导入 Recall、Embedding 和 P2 契约不会初始化 B1/B2/B3。

## 迁移期与最终结构

最终新框架不包含 B1/B2/B3 业务层。当前仓库还承载旧 HTTP v1、部署脚本和测试，因此旧目录暂留，直到对应流程的功能和装配完成替换。

已迁移的 `b1/semantic/models.py`、`b1/semantic/service.py`、`memory/recall/` 和 `memory/vector_projection/` 仅向新路径单向导出兼容名称。新代码直接使用新路径，禁止从新 Recall 回调这些旧路径。旧 `b1/semantic/sidecar.py` 只用于既有 Sidecar 的兼容验证，没有作为新 Recall 的默认推理后端。

后续删除旧目录前，需要完成 Remember/Operate 的剩余迁移及新流程装配，并将旧 API 调用方切换到新实现。独立推理后端已迁入并完成 ONNX 本地验证。本批保留旧接口回归验证，不把旧系统的完整功能完成度算到新框架上。

## 当前运行边界

第一批新入口是 `RecallAdmissionService.admit` → `RecallExecutionService.start`，止于 `RUNNING_REQUEST_VALIDATION`。后续 Query 接入、Working/Vector 候选、正文与 Context 组装仍按 Recall 流程逐阶段开发。已有 Query/Embedding 实验代码迁移后继续保留测试，但尚未接成完整生产 Recall。

```bash
python examples/recall_admission.py
python -m pytest tests/unit/recall
```

`test_architecture.py` 在全新 Python 进程中屏蔽 B1/B2/B3 和旧 memory 包，执行三种选路、拒绝分支及 Query/Passage 测试推理，防止间接依赖回流。
