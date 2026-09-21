# Recall 原生 Embedding 入口

原生模型能力已接入新 P3 三流程，统一维护位置：

- [真实 Embedding 接入与旧实现清理](p3/development/07_真实Embedding接入与旧实现清理.md)
- [技术决策 ADR-P3-004](p3/adr/ADR-P3-004_复用原生Embedding接入三流程.md)
- [实际原生推理校验](p3/acceptance/reports/native-embedding-validation.json)

旧的独立示例已由 scripts/p3/validate_native_flows.py 替代。新流程默认使用真实 BGE CPU 模型；不再把测试身份及测试存储示例当作端到端接入证据。
