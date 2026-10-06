# 若依接替发布与回退

本手册适用于已经完成候选验收后的受控切换。实际镜像摘要、旧资源快照、数据库一致性快照、配置与凭据保存在项目外受限工作区。命令中的文件路径为模板，不包含秘密。所有 Kubernetes 操作必须明确指定受控 kubeconfig 与 `aether-p3-demo` 命名空间。

## 发布门槛

1. 用实际推送的不可变镜像摘要部署候选；核对运行容器 imageID，不以标签推断版本。
2. 完成原生角色登录、租户边界、Agent 对话、记忆写入和召回、配额限流与 CAS、配置快照激活、备份和隔离恢复。Ready 或 TCP 探针不能替代这些验收。
3. 使用非秘密哨兵验证登录、OAuth、刷新等入口不会将请求体或令牌写入日志。若旧版本已有暴露，先修复并部署，再轮换受影响密码、撤销 MySQL 与 Redis 中的旧 access/refresh token；保留旧的失效凭据日志证据，不通过删除日志宣称修复。
4. 停止新的业务测试和旧入口写入。查询原 Temporal 非周期工作流，等待完成并记录原 workflow/run ID；不能换 ID 重提、重写执行绑定或手工把状态改为成功。
5. 再次一致性导出原平台数据库，将各表完整行内容与初始迁移快照比较。仅行数相等无法排除 UPDATE。若发现变化，先将差异合并到接替数据库，保护候选验收之后的新增内容。

## 原 P3 状态接替

接替使用原 PostgreSQL schema、对象/Redis namespace、Milvus collection、Temporal namespace/deployment ID/task queue prefix、原 PVC 和 `/work/runtime`。候选命名空间的快照和执行记录必须留在候选域，不能复制到原域后修改 provenance 伪装迁移。

外部工作区应包含：原 Deployment 和网关快照、原平台 dump、原状态绑定配置 Secret、接替 Deployment patch、恢复候选 PVC 的 patch、镜像摘要及绑定逐项相等的核对结果。接替 patch 的副本数固定为 0，防止准备阶段意外启动第二个 worker。

切换步骤依次执行，不得并行：

1. 暂停旧入口的新写入，完成最后的数据内容比较与工作流查询。
2. 将原 `aether-agent-p3` Deployment 缩至 0，等待其 Pod 完全退出。这同时停止旧 P3 worker 和 identity-projection sidecar。
3. 将候选 `aether-ruoyi-p3` 缩至 0，等待其 Pod 完全退出，保留候选执行域和 PVC。
4. 应用原状态配置 Secret 与接替 patch。确认绑定完全等于旧配置，唯一身份变更为已验收的若依身份来源，配置文件从只读 `/config` 加载，业务代码来自新镜像。
5. 将新 P3 缩放至 1，确认唯一 worker、就绪、原绑定及历史任务查询。不要让新旧 Deployment 同时使用原执行域。
6. 保留迁移后的平台数据库作为业务库，以新身份访问旧用户/租户 ID 和旧会话。完成接替后的对话、写入、查询、召回验收，再切换最终网关。

示例命令只展示动作顺序；文件必须由当前发布证据生成并审查：

```powershell
kubectl -n aether-p3-demo scale deployment/aether-agent-p3 --replicas=0
# 等待原 Pod 完全终止，再执行下一步。
kubectl -n aether-p3-demo scale deployment/aether-ruoyi-p3 --replicas=0
# 等待候选 Pod 完全终止，再应用已核对的文件。
kubectl -n aether-p3-demo apply -f <原状态配置Secret私有文件>
kubectl -n aether-p3-demo patch deployment/aether-ruoyi-p3 --type strategic --patch-file <接替patch文件>
kubectl -n aether-p3-demo scale deployment/aether-ruoyi-p3 --replicas=1
kubectl -n aether-p3-demo rollout status deployment/aether-ruoyi-p3
```

## 公网入口与旧组件

正式 Agent 保持 `/ruoyi-agent/`，与已经验证的前端资源基路径、会话 cookie Path 一致。旧 `/agent` 和其子路径永久重定向到对应新路径，保留查询串；根入口与旧身份、Budibase 页面入口转向 `/ruoyi/`。原生 API 保留 `/ruoyi-api/`，DB 文件下载特殊路径仍须保留。

`/temporal`、`/docs`、`/openapi.json`、`/redoc` 及其斜杠变种关闭公网访问。Temporal 引擎保留，任务入口由原生运维控制台提供。不能把旧 BFF 的 forward_auth 直接替换到没有相应认证端点的新 BFF，也不能删除认证后裸露内部界面。最终 Caddy 配置必须经 Caddy validate、重定向与状态实测，并确认没有旧 Keycloak/Budibase/BFF 上游依赖后，才停用这些旧组件；不删除其数据库或 PVC。

## 回退

回退先判定是否已产生接替后的新写入，并保留故障证据。先将新 `aether-ruoyi-p3` 缩至 0 并等待 Pod 完全退出，再恢复旧 P3 worker；绝不能仅启动旧 worker 而让新 worker 继续占用同一执行域。原 P3 使用同一个持久状态和原绑定恢复，不从较旧快照覆盖新数据。

身份配置版本具有单调约束。实际接替把原身份配置修订号 13 推进到若依配置修订号 14；不能直接加载旧 revision 13 文件进行回退。恢复旧身份提供方之前，必须读取当前修订号，以当前值加 1 生成并审查新的身份配置，核对保留的主体 ID、完整 home_scope（tenant/user/application/agent）和当前 epoch/禁用栅栏。重新启用静态主体时，其 epoch 必须严格高于当前主体 epoch 和适用的 tenant fence，不能恢复过期授权或降低防回退校验。旧 identity-projection sidecar 也不能再写入低版本配置。身份迁移配置未审查通过时保持入口关闭，不启动旧身份流量。

平台数据库回退须单独处理：新平台上线后产生的用户、会话、记忆命令、运维操作等写入不能用旧 dump 覆盖。应先冻结写入，保存新库一致性快照，再通过受控逆向增量同步或经验证的共享数据库切回，使旧应用能看到新内容。若旧应用不兼容新表或语义，应保持维护状态并修复，不能声称恢复旧页面即回退完成。

恢复旧网关时先确认旧身份与旧 BFF 的必要依赖可用，核对当前 ConfigMap 与预期发布版本，避免覆盖并发修改。候选 PVC、原 PVC、数据库快照、镜像和审计记录全部保留。恢复后重新验证身份、历史会话、写入/召回和原 workflow 查询。

本次单副本发布、一次恢复演练和有限业务验收不能替代多实例、长稳、全系统灾备或生产高可用验收。
