# 若依 Azure 候选部署

应用源码构建上下文为 `AgentJYS-main`。四个 Dockerfile 分别构建 Java、Vue、Python 平台和 P3 应用镜像；最终运行制品必须使用真实 ACR 推送返回的摘要。P3 复用既有不可变镜像，并安装当前声明的核心、ONNX、文档和 Ceph 依赖，将当前应用源码烘焙进新镜像，不从 PVC 加载源码。候选保留现有 `rerank_policy=disabled`，未安装 PyTorch reranker；若开启 rerank，必须补齐依赖并重新验收。

`scripts/ruoyi/Build-Push.ps1` 默认只构建；显式 `-Push` 推送并将摘要记录写到指定项目外工作区。脚本计算实际构建输入的内容摘要，默认使用 `source-摘要前缀` 标签，镜像带 `org.aether.source.sha256` 标签。未提交修改不会冒用 Git HEAD 作为构建版本。`render_candidate.py` 读取四个组件的 `image@sha256:...` 映射，默认生成副本数为 0 的独立候选资源；由操作者完成状态迁移、私有配置和内部验收后显式启用。过程输出和私有 Secret 文件必须位于项目外。

候选资源统一使用 `aether-ruoyi-` 前缀。新建 MySQL、Redis、Python PostgreSQL、P3 状态和备份卷，不挂载旧业务卷，不修改现有 Deployment、Service、Secret 或公网入口。数据库与内部管理接口均为 ClusterIP。`Caddyfile.candidate-routes` 只是增量路由片段，需在集成验收后由发布人合并到已备份的现有 HTTPS 站点；不要将片段直接覆盖整个 Caddyfile。

必须预先创建以下同前缀 Secret：

- `mysql`：`MYSQL_ROOT_PASSWORD`、`MYSQL_DATABASE`、`MYSQL_USER`、`MYSQL_PASSWORD`；在新卷内初始化并导入经过账户迁移和默认账户处理的若依数据。
- `redis`：`redis.conf`，开启 AOF 与密码认证，`dir /data`；禁止使用无密码公开服务。
- `platform-database`：`POSTGRES_USER`、`POSTGRES_PASSWORD`、`POSTGRES_DB`；从旧平台数据库的一致性快照恢复。业务进程仅使用专用业务角色，恢复演练权限限定到隔离目标。
- `backend-config`：`application-cloud.yaml`；设置候选 MySQL/Redis、服务端租户身份策略、原生 OAuth 客户端与固定 Python 内部地址。关闭 Druid/Swagger 公网暴露，不启用 Mock。
- `backend-environment`：上述 cloud profile 所需的数据库、Redis、管理入口和内部运维 API 环境变量，以及通知服务密钥；经 `envFrom` 注入。
- `platform-config`：`private.json`；`auth_provider=ruoyi`，`path_prefix=/ruoyi-agent`，明确不可变旧业务 ID 映射、候选 P3 内部地址及候选业务数据库。
- `client`：`ruoyi-client-secret`，挂载 `/run/secrets/ruoyi-client-secret`；仅 Java 登记的服务客户端使用。
- `p3-config`：`service.yaml`、身份映射及所需模型配置/CA 文件；指向独立 P3 元数据命名空间、状态目录和 Temporal 命名空间/部署 ID。完整迁移须包含旧元数据和对象引用的核对证据，不能把新建空状态写成历史迁移成功。
- `p3-environment`：运行所需提供方凭据环境变量，不将旧 `/work/src` 或 `/work/deps` 注入新镜像。

发布顺序：验证构建和身份/租户/命令测试；实际 ACR 推送；生成 0 副本清单；备份旧资源及迁移快照；准备专用数据库、PVC 和 Secret；启动内部候选；验证角色登录、跨租户拒绝、旧 ID 归属、Agent/P3/LLM/保存召回和运维闭环；增量添加候选 HTTPS 路由；完成公网和重启后状态验收。原 Budibase/Keycloak 只有接替验收通过后才能停用。失败时保留原入口与状态，候选回退为 0 副本，不删除 PVC。

数据库、Redis、P3 对象/向量、Temporal 的备份恢复应分别登记范围与证据。平台 PostgreSQL 的一次成功恢复不能代替整个系统灾备验收；单副本、Ready 和 HTTP 200 也不能代替业务、HA 或长稳验收。
