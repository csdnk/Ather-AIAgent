# 本机运行与Azure部署就绪检查

2026-10-04；**清理前基线与后续可执行步骤；清理尚未执行**。本文件不是Azure正式部署成功报告，也不是清理后验收。与[清理审批报告](清理审批报告.md)和[删除清单](删除与待确认清单.md)配套。

结论：P3业务源码可由本机与Azure共用；本机当前以development profile使用真实Azure后端，Temporal在本机Docker PG持久化。目标采用单Ubuntu Azure VM+离线镜像传输，复用PG/Redis/Milvus/Ceph/OpenAI。**仍有配置/发布脚本与完整包缺口，权限补齐后也不能直接把现有模板一键激活。** 以下列出应在云权限到位前完成的准备和权限到位后的顺序。

## 当前保留的可运行版本与启动入口

正式本机脚本：[P3_本机Docker.ps1](<E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker.ps1>)；原指南：[本机Docker运行指南](<E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker运行指南_20261004.md>)。当前已运行，本轮只执行Status/只读检查，不执行下面Start/Stop/Verify。

```powershell
# 当前服务状态与就绪检查；不停止容器。
& 'E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker.ps1' -Action Status

# Docker Desktop为Linux containers；服务需要启动时使用，含依赖/schema检查。
& 'E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker.ps1' -Action Start

# 现有Verify会真实认证后端并请求少量Azure OpenAI tokens，不执行P4故事。
& 'E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker.ps1' -Action Verify

# 从现有私密成品把operator/application凭据复制到剪贴板；不打印token。
& 'E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker.ps1' -Action CopyCredential -Principal p3_operator

# 仅用户明确需要停机时使用；会停止本套容器与专用relay，保留volumes/云数据。
& 'E:/projects/codex/aether/交付成果/部署运行/P3_本机Docker.ps1' -Action Stop
```

| 本机入口 | 地址/行为 |
|---|---|
| P3就绪 | <http://127.0.0.1:18080/p3/readyz> |
| API文档 | <http://127.0.0.1:18080/docs> |
| 当前Web | <http://127.0.0.1:18081> |
| 监测控制台 | <http://127.0.0.1:18081/?view=monitor> |
| P4 | 容器仍启用；故事/排障暂停，不按旧指南“开始演示”操作 |

实际启动链：`__main__.py → runtime/flows/cli.py:main → application → Service`；现行镜像命令 `python -m aether_agent_memory serve --config /deployment/service.yaml --require-profile development`。源码统一后仍沿用该框架入口，不以旧app.py或旧Compose作为当前生产入口。

当前runtime在 `E:/projects/codex/.agent-work/aether/workspace-support/local-docker-20261004/runtime-01`：p3/p4配置、private env/服务凭据、models、Temporal/mTLS、pg-init及schema/namespace/init全部保护；其RO配置挂载到/deployment、模型到/models，可写持久运行挂载到/runtime。P3业务元数据位于Azure agent.p3_docker_final_20261004；Temporal位于本机temporal/public v1.19及temporal_visibility/public v1.14；两者不做误迁移。

本机依赖专用AKS relay55432/56380/59530、有效kubeconfig和隐藏维护进程；用户数据库连接另用45432/46380/49530。脚本依赖TV Python及三个旧名解释器/依赖目录，按主报告保留。该本机脚本不是复制到另一电脑即部署完成的通用安装器。

## 已验证、历史验证和未验证分别列明

| 证据 | 结果 | 能说明什么/限制 |
|---|---|---|
| 本轮容器/HTTP检查 | 五个常驻healthy；P3 readyz200/READY；Web/monitor200；匿名直连/代理401 | 当前HTTP、Bearer边界及readiness可用；非完整业务/浏览器交互验收 |
| 本轮四后端只读认证 | PG TLS、Redis TLS PING、Milvus TLS列集合、Ceph SigV4 HEAD4/4 | 当前实际后端与scope认证可用；Ceph为已授权HTTP；未新跑模型/写业务 |
| 本轮Temporal检查 | mTLS CLI SERVING；PG actual role/schema读取成功 | 当前Server可用与持久schema存在；未新作Worker任务/恢复实验 |
| 本轮镜像/源码对应 | build-10与已运行镜像465/465；active464/465，仅P4 import排序 | 识别当前真实来源；不扩大为未来统一代码运行通过 |
| 原AZ-M01 | 模块73通过/0失败/0跳过 | 真实Azure后端模块阶段验收，见[Azure模块联通阶段验收](<E:/projects/codex/aether/交付成果/测试与验收/P3_Azure模块联通阶段验收_20261004.md>)；非目标VM验收 |
| 原DK-03至09 | 保存、提取/Working/Semantic投影、跨会话召回、更正/失效、删除、P3/Temporal及整套停启通过 | 绑定核心验收镜像09，见[本机Docker验收](<E:/projects/codex/aether/交付成果/测试与验收/P3_本机Docker运行验收_20261004.md>)；当前11无全流程新验收 |
| 原DK-18/20 | 763通过/2失败/549跳过，中断；10ms来源预算独立失败 | 不是全项目通过；参考本地冷热场景中断、真实P2 fixture缺失仍列问题 |
| 本轮静态部署解析 | 3 Bash语法、9 PowerShell解析、15 YAML解析通过 | 只能证明语法；文件缺值、错误probe、跨Docker摘要及真实网络仍需解决 |

P4原run=`51b8b476-9f9b-41fa-9533-1fabc6b5a909`，job=`8af64f2ba5a136a6af95033c0f3232e8472bb831766136a915c592f58ab8a372`，attention_required/effect_status=unknown。本次只保留标识和原证据，不读/展示payload、不重发。`production_ready=false`不能通过改标志制造验收；真实production_p2、冷热迁移、PG全量恢复、长期稳定、多实例及完整P4均未通过。

## 本机与Azure可以只改什么配置

| 项 | 本机实际配置 | Azure VM目标 | 需要代码吗 |
|---|---|---|---|
| 业务源码/镜像 | 当前P3/Web/Temporal已安装镜像 | 同一业务源码、linux/amd64镜像，按发布摘要固定 | 不为换宿主复制业务代码 |
| profile | development | production及require-profile production | 切配置，不自动完成其他依赖/验收 |
| storage/data_dir | azure、/runtime | azure、/runtime；VM1000:1000可写 | 原业务代码支持，修模板值 |
| 后端连接 | desktop relay/host.docker.internal端口 | VNet可达PG5432、Redis6380、Milvus私网入口、Ceph/模型endpoint | 切连接配置并真实验网络 |
| Temporal | 本机PG+Compose p3-temporal+mTLS | Azure既有agent下两schema的正式Server、VM网络名+mTLS | 补部署配置/renderer；不用改现有TLS客户端 |
| namespace/deployment/task queue | p3-docker-final-20261004 | 选定生产namespace/deployment_id/task queues与业务scope | 切配置，明确迁移或新scope策略；不复制测试数据替代迁移 |
| 身份/密钥 | 私密env/files，p3_operator等 | 受控Secret文件/运行角色、版本化身份/授权 | 配置与密钥分发；撤销/轮换需目标验收 |
| 模型 | /models/bge只读；tiktoken离线cache；写cache在/runtime | 同一模型hash/分词器来源及离线路径 | 切路径/包，不能仅保留模型名而丢实体文件 |
| 发布安装 | 本机镜像及脚本 | 离线images.tar+可移植清单+安装/激活+完整配置模型 | 脚本/打包需补齐，见AZ-D03/05/06/07 |
| 监督/恢复 | Docker restart与relay，电脑关机停服务 | Docker/systemd、HTTPS/Monitor、隔离恢复 | 运维脚本/配置及验收；单VM不等于HA |

## Azure必须补齐的15项（不是一律权限问题）

代码/模板以当前最新工作树AgentJYS-main为准，统一后迁到主目录。管理员证据参考[20261004权限与配置汇总](<E:/projects/codex/aether/交付成果/部署运行/P3_Azure权限与配置汇总_20261004.md>)和[当前权限配置脚本](<E:/projects/codex/aether/交付成果/部署运行/P3_Azure权限配置_20261004.ps1>)；本轮未重跑Azure管理查询，因此资源/权限状态须在实施前读回。

| 编号 | 类型 | 精确位置/文件 | 当前缺口 | 可执行处理 | 外部依赖 |
|---|---|---|---|---|---|
| AZ-D01 | 配置 | `configs/p3.production.example.yaml；runtime/flows/config.py；deploy/vm/compose.yaml` | data_dir相对值transient/production落入只读/deployment；PG本地anchor会尝试创建目录。 | VM service.yaml设data_dir=/runtime，容器1000:1000可写；模板默认路径一并修。 | 无 |
| AZ-D02 | 配置/证书 | `configs/p3.production.example.yaml；runtime/temporal/config.py/gateway.py` | temporal.internal与Compose p3-temporal不匹配；模板没有mTLS文件字段。 | 设p3-temporal:7233；注入CA/client cert/key，匹配SAN和server_name；客户端现有代码可用。 | 目标证书与网络 |
| AZ-D03 | 部署脚本/配置 | `deploy/vm/compose.yaml；deploy/azure/temporal.yaml；旧prepare-persistent-temporal.py` | 缺VM专用Temporal production/dynamic配置或renderer，旧AKS的POD_IP/FQDN不可直接复制。 | 补无密钥VM模板/受控渲染，membership/cluster/frontend/publicClient使用容器可达名；SQL两schema、CA验证与运行role固定。 | Azure DB运行角色/证书 |
| AZ-D04 | AKS YAML代码 | `deploy/azure/temporal.yaml；deploy/vm/Dockerfile.temporal` | AKS官方server镜像没有probe所调用的temporal CLI。 | 若保留AKS可执行路线，换含CLI验证镜像或专用probe；VM已用含CLI镜像，不能删除该构建文件。 | AKS镜像/资源仅选该路线时 |
| AZ-D05 | 发布打包 | `deploy/vm/install-release.sh；旧vm-offline-images-01` | 旧包只有images.tar/manifest.json，缺installer必需文件、当前镜像及完整模型/配置。 | 补images.tsv/SHA256SUMS/release.env/Compose/Caddy/两脚本、来源/依赖/模型清单与版本化deployment/models。 | 生成包无需云写权限 |
| AZ-D06 | 发布脚本代码 | `deploy/vm/install-release.sh/activate-release.sh` | 只比Docker .Id，跨Docker存储后端可能把index与config误判不一致。 | 分别锁OCI manifest/config、架构/必要diffIDs；按可移植内容校验并验证离线导入，不忽略不一致放行。 | 目标Docker导入验证 |
| AZ-D07 | 依赖/可重建性 | `pyproject.toml；Dockerfile.p3；web/Dockerfile/package-lock.json` | Python依赖是范围而非完整生产lock，Web base标签未固定digest。 | 保留实际已安装镜像/依赖清单；补constraints/lock与所有base摘要、模型hash；VM不在线pip install。 | 无 |
| AZ-D08 | 管理员扩展/schema升级 | `P3_Azure权限与配置汇总_20261004.md；temporal-schema-job.yaml/initialize-temporal.sh` | 既有Azure agent.p3_temporal v1.19、visibility v1.1未达目标，缺BTREE_GIN允许/安装。 | 管理员允许并安装BTREE_GIN；备份后以upgrade续跑官方schema，核版本/role/search_path；不initialize/reset既有数据。 | PostgreSQL参数与扩展权限 |
| AZ-D09 | Azure资源/权限 | `P3_Azure虚拟机创建_20261003.ps1；20261004权限汇总` | 目标VM/NIC/IP/NSG/Disk尚未形成；现账号缺Compute/Network写权限。 | 管理员代建或授对应范围权限，重核SSH来源CIDR、image、quota/Policy与同名残留资源，再交实际资源身份。 | Compute/Network及关联资源权限 |
| AZ-D10 | 网络/配置 | `runtime-01/compose.yaml；VM Compose；权限汇总` | 桌面55432/56380/59530 relay及host.docker.internal不可直接带入VM；Milvus ClusterIP不保证VNet可路由。 | VM直连PG5432/Redis6380，提供VM可达的Milvus私网入口/DNS/路由，保留Milvus CA/server_name，再从VM真实认证。 | VNet/DNS/路由/Milvus入口 |
| AZ-D11 | 现有Ceph边界 | `runtime/storage/configuration.py/objects.py；Azure存储运行指南` | 现有HTTP经用户授权并真实使用，SigV4/完整性不等于传输加密。 | 沿用已授权HTTP时写明边界；如需HTTPS，补RGW域名/证书/CA/网络并验原bucket/prefix，不只替换URL。 | HTTPS资源仅采用时 |
| AZ-D12 | 身份配置 | `configs/p3.production.example.yaml；identity配置；lifecycle.py` | 模板bootstrap_admin未证明在现行身份存在；当前maintenance是p3_operator。 | 整体保留identity/token摘要、revision/auth_epoch与租户scope；采用已存在application/readonly/operator，VM上验授权/撤销。 | 正式身份分发/可达服务 |
| AZ-D13 | 恢复脚本/运维验收 | `runtime/foundation/lifecycle.py；VM Compose` | 应用backup/restore API明确拒PG；restart/PVC不证明PG+Temporal+Ceph恢复。 | 补pg_dump/restore、Temporal状态、对象/配置/模型/证书/Caddy恢复并隔离业务复验；如要求应用API则另补代码。 | 受控备份位置/目标恢复环境 |
| AZ-D14 | HTTPS/监督/验收 | `activate-release.sh；Caddyfile；observability.py` | up --wait/readyz无公网TLS、鉴权业务或告警验收；OTLP需显式配置。 | 验证公网DNS/TLS/跳转/匿名401/权限撤销、OTel/Monitor告警/Unknown和长稳；单VM按单实例，扩容前另验CAS/租约。 | 域名/TLS/Monitor与目标VM |
| AZ-D15 | 业务代码缺项；P4暂停 | `runtime/temporal/ingress.py/registry.py/workflows.py；P4 validation/operations.py` | 30秒foreground预算与P4单读10秒上限仍在；原真实P4 job attention_required/effect unknown。 | 本清理保留原job/run且不重发；恢复P4时另处理分阶段预算和原副作用。本次不改这些行为。 | 不是权限缺口；需另行业务修复/验收 |

**离线VM路线不要求AcrPush。** 两个已记录管理员事项是BTREE_GIN参数/扩展与VM/附属网络资源；其余脚本、包、配置及本机隔离验证可先准备，不需等权限到位才重新整理。AKS仅作为保留的替代部署/现有schema Job能力，选VM不启动第二份业务代码；旧AKS公网服务/Secret不在本次清理远端范围。

## 权限到位前必须形成的完整离线包

```text
/srv/aether/releases/<RELEASE_ID>/
  install-release.sh  activate-release.sh  compose.yaml  Caddyfile
  images.tar  images.tsv  SHA256SUMS  release.env
  source-manifest.json  dependency-manifest.json  model-manifest.json
/srv/aether/deployment/<CONFIG_REVISION>/
  p3/
    service.yaml  identities.yaml  embedding.json  recall.json  runtime.env
  p3/certificates/{Azure-CA,Milvus-CA,Temporal-CA,client-cert,client-key}
  temporal/
    production.yaml  dynamic.yaml
  temporal-tls/{CA,server-cert,server-key,client-cert,client-key}
  certificates/ca.pem
/srv/aether/models/<MODEL_REVISION>/
  bge/{config.json,model_optimized.onnx,special_tokens_map.json,tokenizer.json,tokenizer_config.json}
  tiktoken/fb374d419588a4632f3f557e76b4b70aebbca790
/srv/aether/runtime/  # 容器1000:1000可写
Docker volumes: caddy-data,caddy-config
```

以上是目标闭包，**当前尚未生成新完整发布包**，不是现成目录可直接上传。production/profile、maintenance、mTLS、私网连接、模型离线路径和配置权限均需按目标真实值渲染；release.env只放非敏感镜像/path/domain，runtime.env和SQL运行密码受控，管理员密码仅schema升级时使用，CA签发私钥不上传服务。身份、模型/配置hash与revision写入发布检查，不能只检images.tar。

本机current P3/Web/Temporal及Caddy可以作为候选镜像，保留各自index与config digest和amd64架构；测试镜像不作为运行镜像。VM如在主机执行schema升级，还需固定Temporal admin-tools离线工具镜像；如在现有AKS Job执行，不把SQL管理员secret送入P3。

## 权限补齐后按此顺序部署

1. **发布准备**：完成获准源码收敛、AZ-D01/02/03/05/06/07/12与恢复脚本/清单，冻结最终来源、模型和配置revision；构建/离线导入/隔离验收通过。记录尚未通过的P2/冷热/P4/完整回归，不能删测试隐去缺口。
2. **管理员交接**：读回目标订阅/RG/账号，允许并安装BTREE_GIN；代建VM/NIC/IP/NSG/Disk或授具体范围权限。创建前重核SSH CIDR、image/配额/同名残留资源，沿现有VNet，不提交私钥。用20261004脚本核对，旧20261003管理员脚本的账号ID与当前不同，不直接All重跑。
3. **目标宿主只读核验**：SSH指纹、cloud-init、Docker/Compose、amd64、UID/GID、磁盘、DNS/路由；从VM认证PG TLS/Redis TLS/Milvus TLS/Ceph。不得携带desktop relay/host-gateway地址冒充生产连接。
4. **Temporal schema受控升级**：先备份既有Azure两schema，核当前版本/extension/role/search_path，以P3_SCHEMA_MODE=upgrade续跑官方目标；不initialize/reset现有数据库或丢旧history。升级失败保留证据，不从头清库。
5. **正式Temporal配置**：VM production/dynamic与mTLS/SAN匹配；运行SQL角色最小权限，frontend/publicClient指p3-temporal；核schema后启动，mTLS health与namespace读回，再按所选生产scope明确创建namespace。
6. **安装但不隐式迁移数据**：向受控release/deployment/models目录传已验包，核SHA256/OCI可移植内容/配置模型revision、文件权限和Compose静态配置。installer仅load/核验，不代替DB升级或业务scope迁移。
7. **激活单实例**：填生产profile=/runtime、正确mTLS/四后端scope/维护主体、域名与Caddy持久volume，再activate。当前脚本的up --wait/readyz只能算容器就绪，先修摘要问题后使用。
8. **公网与身份验收**：可信HTTPS/DNS/HTTP跳转，匿名或无效token401，application/readonly/operator权限，私密路由404、Web代理、凭据撤销/轮换；不公网开放Temporal、数据库和维护接口。
9. **独立业务scope验收**：保存→异步READY→新会话召回→更正→旧结果失效→删除→原job/回执复查；真实Azure OpenAI+BGE/Milvus与Temporal Worker。超时/Unknown只回查原job，不重发制造“成功”；P4故事保持暂停。
10. **持久化/恢复/运行交付**：在隔离目标恢复PG+Temporal+Ceph+配置/模型/证书/Caddy并业务复验；监测/告警、Unknown处理、长稳与容量，给Start/Status/Stop/Verify/rollback明确入口。现行本机停启实验另说明影响并取得确认。

脚本实际源入口：[VM安装器](<C:/Users/27921/.codex/worktrees/p3-production-closure/aether/AgentJYS-main/deploy/vm/install-release.sh>)、[VM激活器](<C:/Users/27921/.codex/worktrees/p3-production-closure/aether/AgentJYS-main/deploy/vm/activate-release.sh>)、[VM Compose](<C:/Users/27921/.codex/worktrees/p3-production-closure/aether/AgentJYS-main/deploy/vm/compose.yaml>)、[Temporal schema Job](<C:/Users/27921/.codex/worktrees/p3-production-closure/aether/AgentJYS-main/deploy/azure/temporal-schema-job.yaml>)。它们是待补齐/核验的实现，不把当前语法检查当可直接执行的生产完成证明。

## 清理后本机与部署准备验收计划

| 检查 | 执行方式 | 通过条件/不通过时动作 |
|---|---|---|
| 来源与依赖闭包 | 唯一源码、生成proto、wheel/import、Web lock/build、全部部署文件与模型hash核验 | 构建输入可恢复；必要模块无missing；旧检出来源已保全 |
| 代码关键回归 | 现有periodic守卫/新旧Temporal兼容、identity/storage/Recall/当前Web相关测试 | 不删除失败测试制造通过；真实P2无fixture标未验证 |
| 不停机健康 | 当前Status、readyz/Web/anonymous401、四后端只读认证、Temporal mTLS | 当前入口和认证scope保持；无新失效引用/空壳 |
| 隔离关键业务 | 独立metadata schema/namespace/collection/prefix，保存/READY/召回/更正/失效/删除 | Worker真正接收执行、原job/回执可回查，不污染生产/用户数据 |
| 持久化与恢复 | 先隔离Server/Worker重启与PG/对象恢复；现行服务停启另征确认 | 原job/run/回执/版本/删除状态保持；不能用新提交代替恢复 |
| Azure准备门禁 | release完整性、可移植镜像导入、配置/Secrets/models/storage/network/升级/安装顺序 | 权限未到位时可标准备验证通过；目标VM真实连接/部署仍单列未验证 |
| 清理彻底性 | 按获准清单复核旧树/脚本/缓存不存在、当前入口无断链，剩余N项理由清楚 | 不在项目换目录留旧可编辑代码；有效历史与恢复保护可追溯 |

本轮没有执行以上清理后构建/新写业务/重启实验。实际实施完成后必须把每项结果与镜像/配置revision绑定；最终报告不能把镜像09的旧验收直接替代新统一版本或目标VM的验收。
