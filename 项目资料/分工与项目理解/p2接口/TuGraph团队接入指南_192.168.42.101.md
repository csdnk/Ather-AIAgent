# TuGraph 团队接入指南

团队接入地址：`192.168.42.101`；后端 TuGraph 虚拟机：`192.168.62.40`；默认图项目：`default`。

本文档不记录 TuGraph、Linux 或 SSH 密码。账号应由管理员单独分配，不建议团队成员共用管理员账号。

## 当前部署信息

| 项目 | 当前配置 |
| --- | --- |
| 虚拟机名称 | `tugraph` |
| 虚拟机 IP | `192.168.62.40` |
| 容器名称 | `tugraph` |
| Docker 镜像 | `tugraph/tugraph-runtime-centos7:4.5.2` |
| 容器网络 | `bridge` |
| 重启策略 | `unless-stopped` |
| 数据目录 | `/opt/tugraph/data` |
| 日志目录 | `/opt/tugraph/log` |

根据 2026-09-13 的检查结果，TuGraph 容器正在运行，虚拟机内的 `7070`、`7687`、`9090` 三个端口均已监听，`http://127.0.0.1:7070/` 返回 HTTP 200。

## 可接入端口

| 宿主机端口 | 协议 | 用途 | 团队接入地址 |
| --- | --- | --- | --- |
| `7070` | HTTP REST | Web 管理页面、登录、Cypher 查询 | `http://192.168.42.101:7070` |
| `7687` | Bolt | Neo4j 驱动及兼容 Bolt 的客户端 | `bolt://192.168.42.101:7687` |
| `9090` | TuGraph RPC | TuGraph RPC 客户端 | `192.168.42.101:9090` |

业务系统一般优先选择：

- 通过 HTTP 调用或使用浏览器管理时使用 `7070`；
- 使用 Neo4j/Bolt 驱动时使用 `7687`；
- 使用 TuGraph 原生 RPC SDK 时使用 `9090`。

上述宿主机端口需要由 Windows 端口转发到 `192.168.62.40` 的同名端口。虚拟机内部服务已验证正常；团队正式接入前，应按“宿主机端口转发检查”一节确认 `192.168.42.101` 的入口可用。

## HTTP REST API 与 Web 页面（7070）

Web 页面：

```text
http://192.168.42.101:7070/
```

REST API 使用 JSON。除登录接口外，业务请求需要携带登录后返回的 JWT：

```text
Authorization: Bearer <JWT>
Content-Type: application/json
```

### 常用 API

以下路径均拼接在 `http://192.168.42.101:7070` 后。除 `/login` 外，请求通常需要携带 `Authorization: Bearer <JWT>`。

#### 推荐使用的认证与查询接口

| 功能 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 登录 | `POST` | `/login` | 使用用户名和密码换取 JWT |
| 退出 | `POST` | `/logout` | 注销当前 JWT |
| 刷新令牌 | `POST` | `/refresh` | 刷新登录令牌 |
| 执行 Cypher | `POST` | `/cypher` | 在请求体指定 `graph` 和 `script`，执行查询或写入 |
| 图项目 Cypher | `POST` | `/db/{graph_name}/cypher` | 对路径中指定的图项目执行 Cypher，可传参数 |

对点、边、Label、索引和数据的日常增删改查，建议统一使用 `/cypher`，便于控制事务、参数和兼容性。

#### 文件和批量导入接口

| 功能 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 上传数据文件 | `POST` | `/upload_file` | 上传导入文件；大文件需要按接口要求分片 |
| 校验上传文件 | `POST` | `/check_file` | 按 MD5 或文件大小检查已上传文件 |
| 清理上传缓存 | `POST` | `/clear_cache` | 删除指定文件、指定用户文件或全部缓存；权限受账号限制 |
| 导入 Schema | `POST` | `/import_schema` | 根据 `graph` 和 `description` 创建图 Schema |
| 导入数据 | `POST` | `/import_data` | 异步导入上传的数据，成功后返回 `taskId` |
| 查询导入进度 | `POST` | `/import_progress` | 使用 `taskId` 查询导入状态、进度或失败原因 |

批量导入会修改数据并占用磁盘、CPU 和内存，应先在测试图项目验证 Schema、分隔符、文件编码和错误处理策略。

#### C++ 存储过程接口

| 功能 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 创建/加载 C++ 存储过程 | `POST` | `/db/{graph_name}/cpp_plugin` | 上传名称、说明、代码和只读属性等信息 |
| 列出 C++ 存储过程 | `GET` | `/db/{graph_name}/cpp_plugin` | 返回当前图项目中的过程列表 |
| 查看 C++ 存储过程详情 | `GET` | `/db/{graph_name}/cpp_plugin/{plugin_name}` | 查看指定过程信息 |
| 调用 C++ 存储过程 | `POST` | `/db/{graph_name}/cpp_plugin/{plugin_name}` | 传入调用数据和超时等参数 |
| 删除 C++ 存储过程 | `DELETE` | `/db/{graph_name}/cpp_plugin/{plugin_name}` | 删除指定过程 |

#### Python 存储过程接口

| 功能 | 方法 | 路径 | 说明 |
| --- | --- | --- | --- |
| 创建/加载 Python 存储过程 | `POST` | `/db/{graph_name}/python_plugin` | 上传 Python 过程定义 |
| 列出 Python 存储过程 | `GET` | `/db/{graph_name}/python_plugin` | 返回当前图项目中的过程列表 |
| 查看 Python 存储过程详情 | `GET` | `/db/{graph_name}/python_plugin/{plugin_name}` | 查看指定过程信息 |
| 调用 Python 存储过程 | `POST` | `/db/{graph_name}/python_plugin/{plugin_name}` | 传入调用数据和超时等参数 |
| 删除 Python 存储过程 | `DELETE` | `/db/{graph_name}/python_plugin/{plugin_name}` | 删除指定过程 |

只读存储过程应正确声明为只读。加载、替换或删除存储过程需要较高权限，普通业务账号通常只应获得调用权限。

#### 旧版兼容 API（不建议新业务依赖）

TuGraph 4.5 官方文档把除登录、查询和存储过程以外的一批旧 REST 接口标记为 Deprecated。当前版本可能仍能使用，但后续版本可能删除或改变行为。下表用于维护旧系统和排查问题；新业务优先使用 Cypher、Bolt 或 RPC。

| 分类 | 方法 | 路径 | 主要用途 |
| --- | --- | --- | --- |
| 用户管理 | `POST`、`GET` | `/user` | 创建用户、列出用户 |
| 用户管理 | `PUT`、`DELETE` | `/user/{user_name}` | 修改用户或删除用户 |
| 查询权限 | `GET` | `/acl/?user={user_name}&graph={graph_name}` | 查询用户在图项目上的权限 |
| 设置权限 | `PUT` | `/acl` | 设置 `NONE/READ/WRITE/FULL` 权限 |
| 删除权限 | `DELETE` | `/acl/?user={user_name}&graph={graph_name}` | 将用户对图项目的权限移除 |
| 修改服务配置 | `PUT` | `/config` | 动态修改部分数据库服务配置，风险较高 |
| 服务综合信息 | `GET` | `/info` | 查询版本、CPU、磁盘、内存、空间和运行时间等 |
| CPU 状态 | `GET` | `/info/cpu` | 查询 TuGraph 与宿主系统 CPU 使用情况 |
| 磁盘状态 | `GET` | `/info/disk` | 查询磁盘读写状态 |
| 内存状态 | `GET` | `/info/memory` | 查询进程和系统内存信息 |
| 数据库空间 | `GET` | `/info/db_space` | 查询数据库占用空间 |
| 数据库配置 | `GET` | `/info/db_config` | 查询 REST/RPC、认证、HA 等配置 |
| HA 节点 | `GET` | `/info/peers` | 查询 HA 节点列表；仅 HA 模式有效 |
| HA Leader | `GET` | `/info/leader` | 查询当前 Leader；仅 HA 模式有效 |
| 运行任务 | `GET` | `/task` | 查看正在运行的长任务 |
| 中止任务 | `DELETE` | `/task/{task_id}` | 中止指定长任务 |
| 创建图项目 | `POST` | `/db` | 创建子图/图项目 |
| 图项目列表 | `GET` | `/db` | 列出所有图项目 |
| 删除图项目 | `DELETE` | `/db/{graph_name}` | 删除指定图项目及数据，风险很高 |
| 创建 Label | `POST` | `/db/{graph_name}/label` | 创建点或边的 Label |
| 查询 Label | `GET` | `/db/{graph_name}/label/{type}` | 查询点或边的 Label 列表，`type` 为 `node` 或 `relationship` |
| Label 详情/删除 | `GET`、`DELETE` | `/db/{graph_name}/label/{type}/{label_name}` | 查看或删除指定 Label |
| 创建点 | `POST` | `/db/{graph_name}/node` | 按 Label 和属性创建点 |
| 点详情/更新/删除 | `GET`、`PUT`、`DELETE` | `/db/{graph_name}/node/{vertex_id}` | 操作指定点 |
| 点的全部属性 | `GET` | `/db/{graph_name}/node/{vertex_id}/property` | 查询点的全部属性 |
| 点的指定属性 | `GET` | `/db/{graph_name}/node/{vertex_id}/property/{field}` | 查询点的指定字段 |
| 创建边 | `POST` | `/db/{graph_name}/relationship` | 在两个点之间创建边 |
| 边详情/更新/删除 | `GET`、`PUT`、`DELETE` | `/db/{graph_name}/relationship/{euid}` | 使用边唯一标识操作指定边 |
| 边的全部属性 | `GET` | `/db/{graph_name}/relationship/{euid}/property` | 查询边的全部属性 |
| 边的指定属性 | `GET` | `/db/{graph_name}/relationship/{euid}/property/{field}` | 查询边的指定字段 |
| 创建索引 | `POST` | `/db/{graph_name}/index` | 为 Label 字段创建普通、唯一或组合唯一索引 |
| 索引列表 | `GET` | `/db/{graph_name}/index` | 列出图项目的全部索引 |
| Label 索引 | `GET` | `/db/{graph_name}/index/{label}` | 列出指定 Label 的索引 |
| 删除索引 | `DELETE` | `/db/{graph_name}/index/{label}/{field}` | 删除指定字段索引 |
| 按索引查点 | `GET` | `/db/{graph_name}/index/{label}/?field={field}&value={value}` | 根据索引值查询点 ID |
| 文本 Schema 导入 | `POST` | `/db/{graph_name}/schema/text` | 向指定图项目导入 Schema 描述 |
| 文本数据导入 | `POST` | `/db/{graph_name}/import/text` | 直接提交文本数据进行在线导入，有请求体大小限制 |

旧版接口中的写入、删除、权限、配置和任务终止操作可能立即改变服务或数据状态，调用前必须确认账号权限、目标图项目和备份情况。

### 登录示例

请将占位符替换为管理员分配的账号和密码：

```bash
curl -X POST "http://192.168.42.101:7070/login" \
  -H "Content-Type: application/json" \
  -d '{"user":"<username>","password":"<password>"}'
```

登录成功后，从响应中取得 `jwt`，后续请求通过 `Authorization` 请求头携带。

### Cypher 查询示例

下面是只读查询示例，默认图项目为 `default`：

```bash
curl -X POST "http://192.168.42.101:7070/cypher" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <JWT>" \
  -d '{"graph":"default","script":"MATCH (n) RETURN n LIMIT 10"}'
```

写入、删除、创建索引或修改图结构会改变数据库内容，应先在测试图项目验证，并使用团队分配的最小权限账号。

## Bolt 接入（7687）

Bolt 地址：

```text
bolt://192.168.42.101:7687
```

可使用兼容 Bolt 协议的 Neo4j 驱动或客户端连接。以 Python 驱动为例：

```python
from neo4j import GraphDatabase

driver = GraphDatabase.driver(
    "bolt://192.168.42.101:7687",
    auth=("<username>", "<password>"),
)

with driver.session() as session:
    result = session.run("MATCH (n) RETURN count(n) AS count")
    print(result.single()["count"])

driver.close()
```

TuGraph 对 Bolt/Neo4j 驱动的支持范围可能与原生 Neo4j 服务不同。业务联调时应先验证所用驱动版本、Cypher 语法和事务行为。

## RPC 接入（9090）

RPC 地址：

```text
192.168.42.101:9090
```

该端口用于 TuGraph 原生 RPC 客户端，不是 HTTP 地址，不能直接在浏览器中打开。RPC 客户端登录后可以执行 Cypher、调用存储过程等操作。使用前应根据团队项目语言选择相应的 TuGraph 客户端，并确认客户端版本与服务器 `4.5.2` 兼容。

## Prometheus 监控说明

TuGraph 业务端口没有直接作为 Prometheus target 使用。当前在同一台虚拟机中部署了自定义 exporter：

```text
Exporter：http://127.0.0.1:9111/metrics
Prometheus job：tugraph
Prometheus：http://127.0.0.1:19090
```

该 exporter 供虚拟机内的 Prometheus 使用，不需要向团队局域网额外开放 `9111`。主要指标包括：

| 指标 | 含义 |
| --- | --- |
| `tugraph_up` | TuGraph 容器及三个服务端口的综合状态 |
| `tugraph_container_running` | TuGraph 容器是否运行 |
| `tugraph_http_up` | HTTP/REST 端口 `7070` 是否正常 |
| `tugraph_bolt_up` | Bolt 端口 `7687` 是否正常 |
| `tugraph_rpc_up` | RPC 端口 `9090` 是否正常 |
| `tugraph_process_cpu_seconds_total` | TuGraph 进程累计 CPU 时间 |
| `tugraph_process_resident_memory_bytes` | TuGraph 进程常驻内存 |

## 宿主机端口转发检查

在 Hyper-V 宿主机 `192.168.42.101` 上以管理员身份打开 PowerShell，首先查看现有转发：

```powershell
netsh interface portproxy show all
```

正确配置应包含：

```text
0.0.0.0:7070 -> 192.168.62.40:7070
0.0.0.0:7687 -> 192.168.62.40:7687
0.0.0.0:9090 -> 192.168.62.40:9090
```

如果缺少相应条目，可在宿主机管理员 PowerShell 中逐条创建：

```powershell
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=7070 connectaddress=192.168.62.40 connectport=7070
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=7687 connectaddress=192.168.62.40 connectport=7687
netsh interface portproxy add v4tov4 listenaddress=0.0.0.0 listenport=9090 connectaddress=192.168.62.40 connectport=9090
```

放行局域网访问：

```powershell
New-NetFirewallRule -DisplayName "Aether TuGraph TCP 7070 7687 9090" -Direction Inbound -Action Allow -Protocol TCP -LocalPort 7070,7687,9090 -RemoteAddress LocalSubnet
```

如果同名防火墙规则已经存在，不需要重复创建。

## 团队电脑连接检查

在需要接入的 Windows 电脑上执行：

```powershell
Test-NetConnection 192.168.42.101 -Port 7070
Test-NetConnection 192.168.42.101 -Port 7687
Test-NetConnection 192.168.42.101 -Port 9090
```

三项的 `TcpTestSucceeded` 均应为 `True`。然后继续验证 Web 页面：

```powershell
curl.exe --connect-timeout 5 http://192.168.42.101:7070/
```

如果 TCP 连接成功但 HTTP 无响应，应在宿主机检查 `portproxy` 后端地址，并确认虚拟机仍为 `192.168.62.40`。

## 运维检查

通过宿主机 SSH 转发连接虚拟机：

```powershell
ssh -p 2223 tugraph@192.168.42.101
```

进入虚拟机后可执行：

```bash
sudo docker ps --filter name=tugraph
sudo docker logs --tail 100 tugraph
curl -I http://127.0.0.1:7070/
ss -lnt | grep -E '7070|7687|9090'
systemctl status tugraph-exporter
```

## 使用注意事项

1. 不要在文档、代码仓库、聊天记录或截图中保存真实密码和 JWT。
2. 应为不同系统或团队创建独立账号，并根据用途授予最小权限。
3. `7070` 是 HTTP/Web 入口，`7687` 是 Bolt，`9090` 是 RPC，三者不能混用。
4. 团队统一使用宿主机地址 `192.168.42.101`，不要依赖 Hyper-V 内部地址 `192.168.62.40`。
5. 如果宿主机物理网卡 IP 变化，需要同步更新团队接入地址；虚拟机 IP 变化时需要更新 Windows `portproxy` 后端地址。
6. 批量导入、大规模写入、删除和索引构建可能明显增加 CPU、内存和磁盘负载，执行前应与运维人员确认。
7. 生产业务接入前，应分别完成登录、只读查询、受控写入、事务和断线重连测试。

## 官方参考

- [TuGraph 4.5 RESTful API](https://tugraph-db.readthedocs.io/en/v4.5.0/7.client-tools/7.restful-api.html)
- [TuGraph 4.5 RESTful API Legacy](https://tugraph-db.readthedocs.io/en/v4.5.0/7.client-tools/9.restful-api-legacy.html)
- [TuGraph 4.5 Quick Start](https://tugraph-db.readthedocs.io/en/v4.5.0/3.quick-start/1.preparation.html)
