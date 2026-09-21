# 原生 Embedding 接入三流程验收

日期：2026-09-19。范围：将仓库已有 BGE 原生推理接入 PRD V1.2 新三流程，清理已被替代的示例及默认路径。本批不宣称完整 PRD 产品验收。

## 结果与证据

Remember 的 Passage 向量和 Recall 的 Query 向量均已由真实 ONNX CPU 模型生成，共用同一指纹化模型空间，通过 SQLite 完成实际投影与检索。新 Host/CLI 默认 native；无模型回归必须显式选择 lexical。原生推理失败不会回退为词法成功。

[真实验证报告](native-embedding-validation.json)记录：

- 模型 BAAI/bge-small-zh-v1.5，512 维、float32、CLS + L2。
- 实际权重 SHA-256：1294ea4b6331115a353d81f96b85e8c8d7fdcc284453d5b2fab5b016230aad38。
- 10 次成功原生推理尝试和 10 条后端证据；同文 Query/Passage 向量不同，归一化核验通过。
- 真实身份与事务下，写入、投影、跨会话召回、自动缓存调度、纠错、旧结果失效、删除和隔离演示通过。
- 重启后模型绑定一致；真实 Query/Passage 健康检测通过；Trace 可查到原生推理节点。
- 未创建旧 semantic-execution 队列记录，RF 继续作为业务任务唯一执行/恢复权威。

适配器的 8 项自动测试使用确定性后端替身，覆盖用途/模型空间一致、用户隔离、同键异内容拒绝、撤权、超时、超长拒绝、故障不回退、模型变更及误切词法的阻断。真实推理由单独 live 实验验证，二者不混记。

[底座与三流程回归报告](native-foundation-regression.json)覆盖原有 256 项测试、72 个 Schema、类型和代码检查及两套演示。连同本批 8 项适配测试，共 264 项；真实模型 live 实验另外记录。

## 清理交付

已删除 examples/recall_embedding.py，以 scripts/p3/validate_native_flows.py 替代测试身份/存储示例；docs/recall_embedding_native.md 收敛为统一文档入口。新流程移除了默认词法 Embedding 路径和 SQLite 固定 256 维限制。

旧示例删除前 SHA-256：e2a31715210a7a19b360d0026dc1765c7112baabfbb7139c3bc42bf7e0dc9ef0。备份在仓库外工作目录。仍被 Sidecar/旧查询调用的兼容层、有效测试工具、现有数据和模型缓存保留。完整清单见[接入与旧实现清理](../../development/07_真实Embedding接入与旧实现清理.md)。

## 复现与限制

```text
python -m pip install -r scripts/p3/requirements-native.txt
python scripts/p3/demo_flows.py --directory <新的运行目录>
python scripts/p3/validate_native_flows.py --report docs/p3/acceptance/reports/native-embedding-validation.json
python scripts/p3/validate_flows.py --report docs/p3/acceptance/reports/native-foundation-regression.json
```

本机 Windows、Python 3.13.15、FastEmbed 0.8.0、ONNX Runtime 1.30.0 运行通过；Linux/远端 CI/其他推理引擎尚未执行。新增 CI 配置把无权重适配测试与手动触发的真实模型验证区分。

仍未实现 Milvus、真实 LangMem 提取模型、目标生成模型 tokenizer、生产物理分层、HTTP Host 及 Azure 部署。已有不同模型空间的数据库不会自动迁移；明确拒绝混用，需要新运行目录或后续显式重建。264 项测试和本地模型实验均不能代替剩余 PRD 验收。
