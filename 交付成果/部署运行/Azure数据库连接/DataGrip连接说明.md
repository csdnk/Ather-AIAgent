# 在本机 DataGrip 连接 Azure PostgreSQL

2026-10-03 已使用本机 DataGrip 2024.2.2 自带 Java 和 PostgreSQL JDBC 42.7.13 完成实际只读查询：数据库 `agent`、用户 `p3admin`、TLS 1.3、`verify-full` 证书和域名校验。已读取 `public`、`p3_temporal`、`p3_temporal_visibility` 和 `p3_local_live_20261003` schema。

该结果证明本机 JDBC 已连通 Azure；DataGrip 窗口内的连接需要导入下述配置并点击 Test Connection。P3 和本地 Temporal 保持停止，本次连接操作没有修改数据库记录或开放 Azure PostgreSQL 的公网访问。

## 最快操作

在 PowerShell 执行：

```powershell
& 'E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/连接Azure数据库.ps1' -Action Start
& 'E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/连接Azure数据库.ps1' -Action CopyConfig
```

在 DataGrip 左侧 **Database** 面板空白处按 **Ctrl+V**，导入 `Azure-P3-agent`。如果当前界面没有从剪贴板导入功能，使用下面的手动填写方式。

双击新连接打开属性。若提示 **Download missing driver files**，点击下载 PostgreSQL 官方驱动。

再执行：

```powershell
& 'E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/连接Azure数据库.ps1' -Action CopyPassword
```

把密码粘贴到 DataGrip 的 **Password** 字段，按需选择保存时长，再点击 **Test Connection**。脚本不会把密码打印到终端；导入 XML 也不含密码。使用后可自行覆盖剪贴板。

## 手动填写

在 DataGrip 选择 **+ → Data Source → PostgreSQL**，填写：

| 项目 | 内容 |
| --- | --- |
| Name | `Azure-P3-agent` |
| Host | `postgresp3.postgres.database.azure.com` |
| Port | `45432` |
| Database | `agent` |
| User | `p3admin` |
| Password | 使用上方 `CopyPassword` 命令复制，不填 Azure 登录密码 |

**Advanced → VM options** 填入以下整行；这是驱动进程专用配置，不修改 Windows hosts：

```text
-Djdk.net.hosts.file=E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/azure-pg.hosts
```

Advanced 的驱动属性表中填写：

| Name | Value |
| --- | --- |
| sslmode | `verify-full` |
| sslrootcert | `E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/azure-root-ca.pem` |
| connectTimeout | `10` |
| socketTimeout | `30` |

这里使用已建立的 Kubernetes 转发，不需要另外勾选 SSH tunnel。Host 保留 Azure 域名，用于核验证书。不要改成 `127.0.0.1` 后关闭证书校验。

2026-10-03 已同时把相同 CA 文件放到 PostgreSQL 驱动的 Windows 默认路径 `C:/Users/27921/AppData/Roaming/postgresql/root.crt`。如果 DataGrip 没有传入 `sslrootcert` 属性，也能从默认位置加载证书；已用不指定 `sslrootcert` 的真实 JDBC 查询验证，仍使用 `verify-full` 和 TLS 1.3。

如果 DataGrip 自行下载驱动失败，已下载并校验的官方驱动在：

```text
E:/projects/codex/.agent-work/aether/workspace-support/azure-storage-interface-20261003/datagrip-01/postgresql-42.7.13.jar
```

在 PostgreSQL 驱动的 **Driver Files → + → Custom JARs** 中选取该文件，Class 为 `org.postgresql.Driver`。

## 连接后查看内容

在 **Schemas** 中勾选需要查看的 schema；可以打开本目录 [连接后检查.sql](连接后检查.sql) 执行只读检查。`p3_temporal` 和 `p3_temporal_visibility` 是此前已创建但迁移尚未完成的 Temporal schema；能查看它们不代表 Temporal 已部署成功。`p3_local_live_20261003` 是本次本地试运行准备的独立 schema，P3 还没有启动写入业务表。

本次实时只读核查：`agent.public` 为 0 张表，`p3_local_live_20261003` 为 0 张表，`p3_temporal` 为 40 张表，`p3_temporal_visibility` 为 3 张表。连接属性中的 **架构 / Schemas** 勾选后两个 schema，点击应用并刷新。表数量不代表业务记录数量；当前 `agent` 库还没有 P3 业务记录的运行验收。右侧 `pg_default` 是系统表空间定义，不是表数据。

## 持续连接与启停

Azure PostgreSQL 当前关闭公网访问。连接路径为：本机 DataGrip → 本机 45432 → AKS 内 `aether-desktop-db-relay` Deployment → Azure PostgreSQL。TLS 一直由客户端校验至 Azure PostgreSQL。

2026-10-03 已按用户要求去掉三小时限制。Azure 内的转发使用 Deployment 管理，容器会自动重启，Pod 丢失后会由 Deployment 重建；不设置 `activeDeadlineSeconds`。

`-Action Start` 在本机启动持续连接进程，同时转发 PostgreSQL、Redis、Milvus；进程退出会重试，PostgreSQL TLS 协商连续两次失败也会重建通道。执行 `-Action Stop` 停止本机转发和自动重连，Azure 中的小型转发 Deployment 保留供下次使用。反复 Start 不会重复启动多个连接进程。保持本机后台进程运行即可持续使用，关闭启动它的 PowerShell 窗口不会主动停止它。

持续连接不等于网络永不中断。本机休眠、网络断开、Azure 登录过期仍可能暂时中断连接。通道恢复后，在 DataGrip 重新连接；不会自动重发 SQL 或恢复旧事务。电脑重启后需要重新执行 Start；本次没有安装开机任务。该连接用于开发管理，生产服务应直接连接 Azure 私网数据库。

本机直连 AKS 已实测出现 TLS 握手超时，现有 `127.0.0.1:3067` 代理路径验证成功。连接脚本优先使用已有 `HTTPS_PROXY`，否则在现有代理监听时，仅为 kubectl 子进程设置该代理，Windows 全局代理保持原设置。保持现有代理可用。北京时间 22:26 的断线恢复验证中，约 9.7 秒重建通道，随后三个后端全部只读查询成功；网络持续故障时重试可能更久。

```powershell
# 查看通道
& 'E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/连接Azure数据库.ps1' -Action Status
# 实测三个后端，报告数据库/集合数量，不读取业务正文
& 'E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/连接Azure数据库.ps1' -Action Verify -Target All
# 只实测 PostgreSQL
& 'E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/连接Azure数据库.ps1' -Action Verify
# 停止本任务的本机连接通道，不删除数据库或云端数据
& 'E:/projects/codex/aether/交付成果/部署运行/Azure数据库连接/连接Azure数据库.ps1' -Action Stop
```

官方设置说明：[Data Sources and Drivers — Advanced](https://www.jetbrains.com/help/datagrip/data-sources-and-drivers-dialog.html#advancedTab)。

其他 PostgreSQL 数据库、Redis、Milvus 的配置见 [其他数据库连接说明](其他数据库连接说明.md)。
