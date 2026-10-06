# AKS 测试与部署就绪检查

日期：2026-10-04。对象：拟清理的 `E:/projects/codex/aether/AgentJYS-main`。状态：清理前验证与实施门禁；项目内未删/同步，清理后验收待确认实施。

## 1. 本轮新实测

| 证据 | 代码来源/运行环境 | 结果 | 能说明什么 |
|---|---|---|---|
| NC-PG01 | 主目录冻结625文件；现有AKS独立Pod；AzurePG test DB、每例新schema | 2通过/2失败/0跳过 | 首次宿主缺OTel exporter依赖；保存原失败，不修改业务断言 |
| NC-PG02 | 同一主目录快照；独立deps-v2补声明依赖；真实AzurePG verify-full | **4通过/0失败/0错误/0跳过**，pytest2.87秒 | 租户/日志/trace持久重开、不创建SQLite、日志失败不回滚业务、OTel trace、缺配置拒回退四个原测试 |
| NC-SRC01 | 上传前/测试后/审批前hash检查 | **625文件字节未变** | 此次真实测试确实针对主目录，不是现部署工作树 |
| NC-LOCAL01 | 审批前本机5容器、P3/Web/正式Temporal只读检查 | 5healthy、P3 readyz200/Web200、Temporal mTLS health exit0 | 容器ID/镜像/启动时间/restart_count与扫描前一致；本轮未重启、未删持久化数据 |

基线Pod只复用已有P3镜像作Python依赖宿主，`PYTHONPATH`显式指向上传的主目录快照；未把宿主镜像所带源码算主目录。测试没有执行主目录危险的DROP-public fixture，项目外harness调用4个原断言，仅替换隔离schema夹具。凭据经既有私密文件和stdin注入，不在命令/报告展示。临时Pod仅本任务所有，证据取回校验后回收。

**4/4是清理前PG基线，不是主目录全量Azure验收。** 此次没有重跑主目录Redis/Milvus/Ceph/model/完整Temporal业务；也未验证清理后版本启动、备份恢复或性能。

既有[Azure模块联通阶段验收](../../测试与验收/P3_Azure模块联通阶段验收_20261004.md)含73/73真实四后端/模型/独立HTTP进程恢复证据，但来源为生产闭环工作树，不能作为本轮清理后的主目录验收。既有全量回归出现2失败/549跳过且中断，仍保留该限制，不因清理删除有效失败测试。

## 2. 清理后的 AKS 必跑门禁

| 门禁 | 必须实际覆盖 | 失败/跳过处理 |
|---|---|---|
| NC-AKS-META | PG事务多写可见、commit guard rollback、scope/CAS/tombstone、持久日志重开/trace脱敏 | 配置缺失直接失败；不能skip或用SQLite代替 |
| NC-AKS-CACHE | Redis TLS、环境/租户隔离、TTL/容量并发、损坏拒绝、失ACK原回执回查、purge late-publication fence | 只清本次prefix；不flush共享DB；warm/cold明确未支持 |
| NC-AKS-OBJECT | 真实Ceph签名、immutable原字节、range/hash、幂等/冲突、并发单赢家、失响应回查、删除 | 单独prefix；保留用户已授权HTTP配置并披露，不能假称HTTPS |
| NC-AKS-VECTOR | TLS Milvus provider、scope/source/model过滤、exact payload、失ACK、reindex、删除late-write barrier | 单独collection/namespace；损坏注入仅任务数据 |
| NC-AKS-MODEL | 真实AzureOpenAI + BGE512维、保存/异步ready/跨会话Recall/correction/delete | 模型输出不满足断言必须报告失败；不能换Mock让结果好看 |
| NC-AKS-RECOVERY | 同一job在独立P3进程退出后续跑、原operation结果回查、更正失效/删除后再次重启无复活 | 独立测试服务/身份/namespace；不杀生产进程/重新提交旧故事 |
| NC-AKS-TEMPORAL | workflow→activity→metadata/model/object/vector→guard→checkpoint→result；原start/control ACK丢失、deadline/撤销/worker重连、replay | 测试独有Temporal服务与正式AzureTemporal分别记录；正式依赖未齐不能改为成功 |
| NC-LOCAL-IMAGE | 清理主目录构建镜像、同配置模式下本机隔离端口启动、健康与原回执持久化 | 不重启当前五容器；只启动任务隔离环境，不使用生产schema |
| NC-PG-RESTORE | pg_dump指定任务schema；pg_restore独立目标；核验记录摘要/CAS/tombstone/job/inboxes | 无覆盖生产恢复；工具缺失则列为未验证，不把Git恢复包算DB恢复 |

所有保留业务测试在AKS执行，真实后端参数通过Secret/file/stdin注入。纯DTO/静态合同/replay/failure-injection可同时在AKS执行；受控响应仍需标明测试边界，不能称全项目无Mock。生产业务验收禁 `sqlite3.connect`，离线迁移/只读旧source proof使用单独明确场景，不允许业务fallback。

## 3. 同一源码的配置差异与仍需代码动作

| 内容 | 仅切换配置即可 | 仍需动作 |
|---|---|---|
| P3业务 | storage_mode=azure，PGschema/Redis prefix/Milvus DB+collection/Ceph prefix/identity/native/model配置 | 主目录先同步当前providers，迁有效测试、清旧实现/默认reference；不能仅改metadata_backend字段完成 |
| 本机访问Azure | 现有桌面relay+可信hostname/CA；保留现运行配置 | 清理不改现部署/转发；单独镜像验收后再决定服务切换 |
| AKS测试网络 | 在现集群直连私网PG/Redis/Milvus/Ceph，模型走HTTPS | 每轮独立测试prefix与TLS/env校验；按冻结主目录来源运行 |
| Azure正式Temporal | 改endpoint/namespace/deployment ID、mTLS、PG配置 | 安装/允许BTREE_GIN、续跑schema升级、启动正式server并验证历史/重启 |
| Azure VM发布 | 同linux/amd64应用镜像、外部deployment/model/cert配置、VNet/DNS/端口 | VM/NIC/IP/NSG/Disk待建；加载镜像和HTTPS服务需在新主机实测 |
| P2旧reference入口 | 无可通过配置保留可写SQLite的方案 | 开发reference部署入口退出；P2合同/client/engine不删，接口测试显式Azure PG；production_p2事务适配未完成不宣称生产可用 |
| warm/cold、HA、多实例 | 不是补权限就可完成 | 当前没有真实tier运动实现；保留明确不支持结论，性能/HA另待验 |

## 4. Azure 部署准备状态

| 项目 | 现有内容 | 准备状态/剩余 |
|---|---|---|
| 应用构建 | Dockerfile.p3、pyproject、CPU/native/rerank/Ceph依赖、proto生成 | 参考版本齐全；纳入主目录后需重构建并固定digest，不重标已有运行镜像 |
| VM发布 | deploy/vm/compose、Dockerfile.temporal、Caddyfile、install/activate-release | 参考版本齐全，待限定同步；目标主机尚未创建 |
| AKS Temporal | deploy/azure/temporal.yaml、schema-job、initialize-temporal.sh | 续跑升级模式已准备；权限缺失不执行伪initialize |
| 环境变量 | PG DSN、Redis/Milvus、Ceph签名与LLM key；示例以变量名称注入 | secret外置；缺值启动拒绝；示例placeholder不能当真实部署配置 |
| 持久存储 | 真实AzurePG/Redis/Milvus/Ceph、现本机Temporal PG volumes | 保留绑定；AKS/P3运行anchor/模型挂载及Temporal数据必须按正式模板检查 |
| 网络 | 内部PG5432/Redis6380/Milvus19530/Ceph已授权HTTP、模型443；公开80/443 | 现AKS可用于测试；VM VNet/private DNS/NSG仍需创建后实际认证检查 |
| 身份 | 已有P3应用/只读/operator/service-role与撤销约束 | 隔离测试合成身份不替代正式账号HTTP轮换/撤销验收 |
| 数据恢复 | 离线SQLite→PG迁移源校验、Temporal迁移/replay保留 | PostgreSQL正式dump/restore独立目标仍需清理后验证，不能用SQLite快照API替代 |

正式管理员事项见[Azure权限与配置汇总](../../部署运行/P3_Azure权限与配置汇总_20261004.md)及[管理员脚本](../../部署运行/P3_Azure权限配置_20261004.ps1)。本轮未重复改云参数/权限，沿用同日已实查阻塞：`postgresp3.azure.extensions` 缺BTREE_GIN及其参数写权限，目标 `aether-p3-service-01` 与附属Compute/Network资源尚未创建。

AKS测试位置已获用户明确选择；**Azure正式服务部署目标仍保留原VM路线**。选在AKS跑测试不会自动把正式部署改成AKS，也不意味着需要ACR推送权限。VM当前离线镜像路线不要求AcrPush；若后续改ACR正式发布，需要另补具体发布权限。

## 5. 本机入口与启动方式

当前本机入口保留：P3 `http://127.0.0.1:18080`、就绪 `/p3/readyz`、Web `http://127.0.0.1:18081`。现运行源码由镜像提供，项目内清理不会自动发布。

```powershell
# 现有服务状态；本轮只读检查已通过。
& 'E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker.ps1' -Action Status

# 需要用户之后启动时使用；本轮不执行停启。
& 'E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker.ps1' -Action Start
```

详细[本机Docker运行指南](../../部署运行/P3_本机Docker运行指南_20261004.md)原位保留。该脚本依赖现机器已有私密配置、模型、9个volumes与工具环境，不当作跨机器零准备发布脚本。清理后新源码镜像验收使用独立任务配置/端口；正式服务切换需说明停机/重启影响后再执行。

## 6. 权限补齐后部署顺序

1. 确认BTREE_GIN允许列表、在既有agent中安装扩展；在现有Temporal schema版本上执行upgrade，保留已完成结构/data，不reset/initialize。
2. 创建已指定VM或补对应Compute/Network权限；从该主机验证VNet/private DNS/TLS/auth到四存储和AzureOpenAI。
3. 将已清理并通过AKS门禁的主目录源码构建最终应用/Temporal/Web镜像，固定SHA256与linux/amd64平台；离线发布包SHA256SUMS/images.tsv核对。
4. 外部deployment目录注入profile、schema/namespace、身份、CA/mTLS、model路径和变量；先check-config、Compose config，再install-release。
5. 启动正式持久化Temporal，核验namespace/gRPC/mTLS/schema/histories；再启动P3/Web/Caddy HTTPS，内部维护/数据库/Temporal端口保持私有。
6. 使用正式服务身份验证保存、异步ready、Recall、更正、删除、原job恢复和撤销；独立备份恢复演练并报告长期/性能/多实例边界。

缺权限时第1/2步阻塞；应用依赖、部署脚本、变量注入及测试源码应在此次清理就整理齐全。完成资源创建不能直接跳到“上线验收通过”。
