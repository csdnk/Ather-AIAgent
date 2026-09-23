# 2026-09-23 Recall / Embedding A 侧增量

新 A 实现完成候选资格消费、记忆级 Top K、完整正文组包、关系/发布批次复核及事务提交。真实 B 提供方尚未接入新多块流程，当前新流程验收使用严格 B 测试替身；现有基础 B 链路另行通过真实 BGE、SQLite 和 TCP HTTP 验收。

入口为 `recall/basic/candidates.py`、`generation_search.py`、`assembly.py`、`generation.py`。Embedding 继续复用 `recall/embedding/p3.py`，完整空间解析和输出核验位于 `embedding/spaces.py`。

运行默认保持现有基础链路。在 Host 启动服务前，使用 `ThreeFlows.enable_generation_recall` 显式注入 B 的 MemoryReadPort、MemoryQualificationPort、MemoryFoundationPort、MemoryContextGuardPort 与兼容 EmbeddingSpace。接口不全、已有运行请求或空间不兼容均拒绝切换。未提供集成探针时，新依赖健康为 unknown。

新增 B 契约是 CandidateQualificationTarget、CandidateQualificationResult、ContextGuardRequest、MemoryRelationSnapshot，以及 qualify、relations、revalidate_context；这里只定义契约，不实现 B 服务。Python 为唯一字段来源，Schema 已同步。接口目录的 implemented=false 保留其历史契约层含义，实际接线状态以本说明及本轮报告为准。

新增只读 HTTP 路由：`GET /p3/recalls/{recall_id}`、`GET /p3/recalls/{recall_id}/result`。现有 `POST /p3/recall` 格式保持不变。新结果必须经过新守卫复核，默认旧服务不能在重启后降级读取它。

复验命令：

```text
python scripts/p3/validate_collaboration.py --report <外部工作目录>/gate.json
python scripts/p3/validate_native_flows.py --config <模型配置.json> --report <外部工作目录>/native.json
python scripts/p3/validate_native_http.py --directory <新的外部运行目录> --config <模型配置.json> --report <外部工作目录>/http.json
```

配置 TEMP/TMP、PYTHONPYCACHEPREFIX、TIKTOKEN_CACHE_DIR 到外部工作区。真实 HTTP 脚本使用现有 B，不能作为新多块 B 联调证据。

用户可读完整流程、字段、分工和验收报告在工作区的 `交付成果/开发协作/13_Recall与Embedding_A侧实现与接入_20260923.md` 与 `交付成果/测试与验收/Recall与Embedding_A侧工程验收_20260923.md`。业务代码、必要测试与复验脚本仍保留在本仓库。
