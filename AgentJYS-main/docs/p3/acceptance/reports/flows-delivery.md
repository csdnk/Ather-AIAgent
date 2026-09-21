# 三流程基础实现验收

交付：在现有公共底座上接入Remember、Recall与内部自动Operate。正式依据仍为PRD V1.2；本报告只评价`basic_local`实现，指标与生产验收继续暂缓。

## 已运行范围

- Remember保存来源与Working记录，持久形成长期事件和投影；纠错、归档、删除、来源删除与加工恢复。
- Recall使用本地词法检索，按当前身份、来源和版本形成受预算约束的ContextPack；历史结果重新获取会拒绝失效内容。
- Operate自动消费存储/访问事件，在本地缓存目录实际执行副本迁移和读取核验；响应丢失时查询原动作，证据丢失时保留Unknown。
- 统一Task、Outbox、Inbox、租约、恢复和追溯仍由RF提供，未修改旧业务路由。

## 检查结果

已通过22项三流程测试、33项公共底座测试、189项契约测试、72个Schema校验、静态类型及代码检查，并运行完整三流程演示。实际结果见[flows-validation.json](flows-validation.json)，不把静态契约通过代替运行验证。

三流程测试覆盖：正常写入/召回/自动缓存执行；纠错后旧向量过滤；历史Pack失效；删除及来源删除；同租户不同用户及跨租户隔离；依赖中断的降级/失败；完整条目预算；重复事件不重复计热；反馈丢失的原ID对账；归档/激活；删除时旧提取结果阻断；只读用户召回；删除Working后的派生失效；服务重启；执行端历史丢失；Embedding绑定不符；LangMem候选伪证据；原始CRLF字节校验；CLI请求重试。

这些测试使用真实SQLite、本地文件系统及独立动作数据库；LangMem的候选边界测试使用manager替身。没有运行真实LangMem模型、训练Embedding模型、Milvus或生产存储迁移。本地cache的real标记仅表示真实文件操作，不能作为PRD物理分层验收。

## 复现与后续

```text
python -m pip install -r scripts/p3/requirements.txt
python scripts/p3/validate_flows.py --report docs/p3/acceptance/reports/flows-validation.json
python scripts/p3/demo_flows.py --directory <新的运行目录>
```

运行方式、代码责任及下一批接入事项见[三流程基础实现与联调](../../development/05_三个流程基础实现与联调.md)。下一批需要真实模型与Milvus适配、完整冲突/文档/压缩规则、正式HTTP Host与Azure部署。删除源正文和历史记录的最终保留/物理擦除也未完成，删除回执保留pending而非伪造completed。

CI调用同一校验入口，远端CI与Linux/Azure运行尚未执行。历史设计与底座报告保留为各批次证据；本报告不代表全部PRD验收或团队评审已签署。
