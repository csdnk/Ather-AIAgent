# P3 可校验契约

2026-09-22：五个投影模型的规范名称迁至 remember；ProjectionPort/GenerationProjectionPort 与 VectorSearchPort/GenerationSearchPort 分离。旧Python导入保留别名，Schema规范文件名使用新所有者。对象仍118个、方法仍68个，正式接口组24组。

2026-09-22：RF新监测接口及公共对象已有本地服务接入，见[RF运行服务](../../docs/p3/development/08_RF运行服务与HTTP接入.md)。B/A/C新接口仍待业务接线。下方2026-09-21说明及catalog中的contract_only为原契约批次范围。

当前需求基线及V1.3租户增量见[prd-baseline.yaml](prd-baseline.yaml)。既有字段契约并不代表新增需求全部实现；原V1.2追踪和测试保留其历史范围。

既有`p3/1`模型已有本地服务消费者，公开HTTP目录仍是设计映射。本包不替换`contracts/p3-northbound-v1.json`。2026-09-21新增的46个foundation对象与13个接口方法已通过契约校验，尚未接入当前Host；详见[接入说明](../../docs/p3/development/07_对象契约接入.md)。

当前先搭建系统，产品指标暂缓；product-acceptance profile是PRD参考，`enforce_now=false`，不是本轮或F1基座的性能门禁。foundation profile仅提供机制验证初值。

- Python字段/枚举来源：各流程`contracts/models.py`与`contracts/foundation.py`；同进程接口：`ports.py`。
- [object-catalog.yaml](object-catalog.yaml)：新增对象的事实归属、消费者、流程节点和实现状态。
- `schemas/`：自动生成Draft2020-12 JSON Schema；不手工编辑。
- [http-api.yaml](http-api.yaml)：待实现公开接口，仅Remember/Recall与配套查询。
- [interface-catalog.yaml](interface-catalog.yaml)：逐方法Owner、消费者、签名及样例。
- [events.yaml](events.yaml)：事件封套内载荷的领域模型与消费规则。
- [fixtures/cases.json](fixtures/cases.json)：可复用正反样例；`valid`是Pydantic语义校验预期，`schema_valid`是JSON Schema结构校验预期，两者不必相同。
- [foundation profile](profiles/foundation.yaml) 与 [产品验收 profile](profiles/product-acceptance.yaml)：工程初值与PRD指标分开。

模型允许的结构不代表权限成立、正文真实或副作用已经发生。诸如完整冲突组、未知效果分类、真实/模拟证据匹配由Python语义校验补充；服务实现仍需执行当前授权、内容、时间、Provider和事务核验。

运行：`python scripts/p3/validate_contracts.py`。更新模型后先运行`python scripts/p3/generate_schemas.py`，再校验。校验不启动应用、不调用模型或数据库。

向后兼容：未知字段拒绝；p3-demo/1与v1字段需显式适配。不允许把旧缺失的身份或版本补成默认值后声称兼容。每组应使用本包样例写真实消费者测试，独立实现后的运行测试另行报告。
