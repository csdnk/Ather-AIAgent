# Prometheus 接入 Ceph 三节点、Milvus、TuGraph及 Grafana 部署说明

## 1. 文档目的

本文档记录当前环境中 Prometheus、Grafana、Ceph 三节点集群、Milvus 和 TuGraph 的实际部署及监控接入方式，包括：

- 各服务所在机器和 IP 地址；
- 各数据库提供给 Prometheus 的指标端点；
- 当前使用的主要指标；
- Prometheus 和 Grafana 的部署及配置路径；
- Hyper-V 宿主机、当前管理电脑和同一局域网其他电脑的访问方式。

本文档不记录 Linux、Grafana 或 SSH 密码。

> 当前配置状态（2026-09-11）：Ceph 指标隧道已迁移到始终开机的 Ceph 宿主机，Prometheus 不再依赖当前管理电脑在线。

## 2. 当前部署拓扑

| 角色 | 名称/IP | 主要服务 |
| --- | --- | --- |
| Hyper-V 宿主机 | `192.168.42.101` | 承载 TuGraph、Milvus 虚拟机；SSH 转发入口 |
| TuGraph/监控虚拟机 | `192.168.62.40` | TuGraph、TuGraph exporter、Prometheus、node_exporter、Grafana |
| Milvus 虚拟机 | `192.168.62.97` | Milvus 2.6.20、Milvus `/metrics` |
| Ceph 节点 1 | `ceph01` / `192.168.100.11` | Ceph MON/MGR/OSD |
| Ceph 节点 2 | `ceph02` / `192.168.100.12` | Ceph MON/MGR/OSD |
| Ceph 节点 3 | `ceph03` / `192.168.100.13` | Ceph MON/OSD |
| Ceph 宿主机/指标转发入口 | `192.168.4.150` | 暴露两个 Ceph MGR 指标，并建立常驻 SSH 反向隧道 |
| 当前管理电脑 | 当前为 `192.168.4.67` | 仅建立 Grafana/Prometheus 本地访问隧道，不再参与指标采集 |

整体监控链路：

```text
Milvus 192.168.62.97:9091/metrics ───────────────────────────┐
                                                              │
TuGraph exporter 127.0.0.1:9111/metrics ─────────────────────┤
                                                              ├─> Prometheus 192.168.62.40:19090
Ceph MGR 192.168.4.150:19283/19284                            │             │
        └─ Ceph 宿主机常驻 SSH 反向隧道                       │             └─> Grafana 192.168.62.40:3000
           └─ 经 192.168.42.101:2223 到达监控虚拟机           │
              └─> 127.0.0.1:29283/29284 ──────────────────────┘
```

## 3. Ceph 三节点集群接入

### 3.1 节点和 MGR 说明

Ceph 集群由 `ceph01`、`ceph02`、`ceph03` 三个节点组成。Prometheus 只配置两个 Ceph MGR 指标端点：

| Prometheus 标签 | 原始指标地址 | 说明 |
| --- | --- | --- |
| `mgr_node="ceph01"` | `http://192.168.4.150:19283/metrics` | 第一个 MGR 指标端点 |
| `mgr_node="ceph02"` | `http://192.168.4.150:19284/metrics` | 第二个 MGR 指标端点 |

三节点 Ceph 只有两个 MGR 指标端点是正常设计：MGR 通常采用一个 active、一个 standby，不需要为 `ceph03` 单独增加第三个 Prometheus target。两个 MGR 返回的是同一个 Ceph 集群的集群级指标，因此 Grafana 查询通常使用 `max()` 或按守护进程去重，避免重复计算。

### 3.2 当前 Prometheus 实际采集地址

TuGraph/监控虚拟机无法直接路由到 `192.168.4.0/24`，因此由 Ceph 宿主机 `192.168.4.150` 建立常驻 SSH 反向隧道。隧道通过 Hyper-V 宿主机的 `192.168.42.101:2223` 进入 TuGraph/监控虚拟机。

为避免与旧管理电脑隧道使用的端口冲突，新隧道在监控虚拟机中使用 `29283` 和 `29284`。Prometheus 实际采集：

```text
http://127.0.0.1:29283/metrics  ->  192.168.4.150:19283
http://127.0.0.1:29284/metrics  ->  192.168.4.150:19284
```

Ceph 宿主机上的常驻隧道配置：

```text
脚本：C:\ProgramData\AetherMonitoring\ceph-metrics-tunnel.ps1
私钥：C:\ProgramData\AetherMonitoring\ceph-host-tunnel-ed25519
主机密钥：C:\ProgramData\AetherMonitoring\known_hosts
日志：C:\ProgramData\AetherMonitoring\ceph-metrics-tunnel.log
计划任务：Aether Ceph Metrics Tunnel
运行账户：NT AUTHORITY\SYSTEM
触发方式：Windows 启动时自动运行
```

私钥只允许 `SYSTEM` 访问，因此管理员账户直接执行 `icacls` 时可能显示“拒绝访问”，这是当前安全配置下的正常现象。隧道成功运行时，日志会停留在一条 `starting SSH tunnel`，不会每隔 5 秒出现 `tunnel stopped`。

当前管理电脑已经不再参与 Ceph 指标转发。管理电脑关机、未登录或离开 `192.168.4.xxx` 网段，不会影响 Prometheus 对 Ceph、Milvus 和 TuGraph 的采集。Ceph 采集持续工作的条件是：Ceph 宿主机保持开机、计划任务正常运行，并且能访问 `192.168.42.101:2223`。

### 3.3 Ceph 主要指标

| 指标 | 含义 |
| --- | --- |
| `up{job="ceph"}` | Prometheus 是否成功抓取 Ceph MGR 指标 |
| `ceph_health_status` | 集群健康状态；通常 `0` 为 OK、`1` 为 WARN、`2` 为 ERR |
| `ceph_osd_up` | OSD 进程是否运行 |
| `ceph_osd_in` | OSD 是否加入数据分布 |
| `ceph_cluster_total_bytes` | Ceph 集群总容量 |
| `ceph_cluster_total_used_bytes` | Ceph 集群已使用容量 |
| `ceph_pool_stored` | Pool 中实际存储的数据量 |
| `ceph_pool_objects` | Pool 对象数量 |
| `ceph_pool_rd`、`ceph_pool_wr` | Pool 读写操作累计值，具体是否出现取决于 Ceph 版本和当前负载 |

2026-09-11 迁移后的验收状态为：`ceph01` 和 `ceph02` 两个 MGR target 均为 `UP`，两个 `/metrics` 端点均返回 HTTP 200；最近一次已知 OSD 状态为 `3 Up / 3 In`。

## 4. Milvus 接入

### 4.1 服务信息

```text
虚拟机名称：milvus
虚拟机 IP：192.168.62.97
Milvus 版本：2.6.20
业务端口：19530
Prometheus 指标端口：9091
指标地址：http://192.168.62.97:9091/metrics
```

Milvus 原生提供 Prometheus 格式的 `/metrics`，不需要单独安装 exporter。TuGraph/监控虚拟机和 Milvus 虚拟机位于同一个虚拟网络，Prometheus 直接采集 `192.168.62.97:9091`。

### 4.2 Milvus 主要指标

| 指标 | 含义 |
| --- | --- |
| `up{job="milvus"}` | Prometheus 是否成功抓取 Milvus 指标 |
| `milvus_proxy_req_count` | Proxy 请求累计数，可按 `function_name`、`status` 等标签筛选 |
| `milvus_proxy_req_latency_bucket/count/sum` | Proxy 请求延迟直方图 |
| `milvus_proxy_grpc_latency_bucket/count/sum` | gRPC 请求延迟直方图 |
| `milvus_wal_insert_rows_total` | WAL 写入行数累计值 |
| `milvus_wal_insert_bytes` | WAL 写入字节累计值 |
| `milvus_querycoord_collection_num` | QueryCoord 管理的 Collection 数量 |
| `milvus_querycoord_partition_num` | QueryCoord 管理的 Partition 数量 |
| `milvus_querycoord_querynode_num` | QueryNode 数量 |
| `go_*`、`process_*` | Go 运行时、CPU、内存和垃圾回收指标 |

当前 Milvus 2.6.20 不再提供旧仪表盘使用的：

```text
milvus_proxy_search_vectors_count
milvus_proxy_insert_vectors_count
```

因此 Grafana 已将搜索/写入面板适配为：

```promql
sum(rate(milvus_proxy_req_count{
  job="milvus",
  status="total",
  function_name=~"Search|HybridSearch"
}[5m])) or vector(0)
```

```promql
sum(rate(milvus_wal_insert_rows_total{job="milvus"}[5m])) or vector(0)
```

无业务流量时显示 `0`，产生搜索或写入请求后显示相应速率。

## 5. TuGraph 接入

### 5.1 服务信息

```text
虚拟机名称：tugraph
虚拟机 IP：192.168.62.40
Docker 容器名称：tugraph
镜像：tugraph/tugraph-runtime-centos7:4.5.2
HTTP/Web 端口：7070
Bolt 端口：7687
RPC 端口：9090
```

TuGraph、Prometheus 和 Grafana 部署在同一台虚拟机中。因为 TuGraph 已占用 `9090`，Prometheus 没有使用默认端口 `9090`，而是改为 `19090`。

### 5.2 TuGraph exporter

当前 TuGraph 未直接提供本项目所需的 Prometheus `/metrics`，因此在同一虚拟机上部署了轻量 Python exporter：

```text
程序：/opt/tugraph-prometheus-exporter/tugraph_prom_exporter.py
systemd 服务：/etc/systemd/system/tugraph-exporter.service
监听地址：http://127.0.0.1:9111/metrics
```

exporter 主要指标：

| 指标 | 含义 |
| --- | --- |
| `tugraph_up` | TuGraph 容器及三个服务端口的综合状态 |
| `tugraph_container_running` | TuGraph Docker 容器是否运行 |
| `tugraph_http_up` | HTTP/Web 端口 `7070` 是否可访问 |
| `tugraph_bolt_up` | Bolt 端口 `7687` 是否可访问 |
| `tugraph_rpc_up` | RPC 端口 `9090` 是否可访问 |
| `tugraph_process_cpu_seconds_total` | TuGraph 进程累计 CPU 时间 |
| `tugraph_process_resident_memory_bytes` | TuGraph 进程 RSS 内存 |
| `tugraph_exporter_scrape_duration_seconds` | exporter 单次检查耗时 |

## 6. Prometheus 部署

### 6.1 部署位置和方式

```text
所在虚拟机：tugraph
虚拟机 IP：192.168.62.40
安装方式：Ubuntu 软件包 + systemd
当前版本：2.31.2
监听地址：0.0.0.0:19090
数据保留时间：15 天
采集间隔：15 秒
```

主要路径：

| 路径 | 用途 |
| --- | --- |
| `/etc/prometheus/prometheus.yml` | Prometheus 主配置和 scrape jobs |
| `/etc/systemd/system/prometheus.service` | Prometheus systemd 服务 |
| `/var/lib/prometheus` | Prometheus 时序数据库 |
| `/usr/local/bin/prometheus` | Prometheus 启动程序 |

服务已设置开机自动启动：

```bash
systemctl status prometheus
systemctl status prometheus-node-exporter
systemctl status tugraph-exporter
```

### 6.2 当前核心 scrape 配置

```yaml
scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: ["127.0.0.1:19090"]

  - job_name: node
    static_configs:
      - targets: ["127.0.0.1:9100"]

  - job_name: milvus
    metrics_path: /metrics
    static_configs:
      - targets: ["192.168.62.97:9091"]
        labels:
          service: milvus
          remote_instance: 192.168.62.97

  - job_name: tugraph
    metrics_path: /metrics
    static_configs:
      - targets: ["127.0.0.1:9111"]
        labels:
          service: tugraph
          remote_instance: 192.168.62.40

  - job_name: ceph
    metrics_path: /metrics
    scrape_timeout: 10s
    static_configs:
      - targets: ["127.0.0.1:29283"]
        labels:
          service: ceph
          cluster: ceph-three-node
          mgr_node: ceph01
      - targets: ["127.0.0.1:29284"]
        labels:
          service: ceph
          cluster: ceph-three-node
          mgr_node: ceph02
```

Prometheus 会为每个 target 自动生成 `up` 指标：

```text
up = 1：采集成功
up = 0：采集失败、连接超时或指标接口返回错误
```

## 7. Grafana 部署

### 7.1 部署位置和方式

```text
所在虚拟机：tugraph
虚拟机 IP：192.168.62.40
部署方式：Docker
镜像：grafana/grafana:13.2.0
容器名称：grafana
网络模式：host
监听端口：3000
重启策略：unless-stopped
```

Grafana 数据保存在 Docker volume `grafana-storage` 中。主要挂载文件：

| 路径 | 用途 |
| --- | --- |
| `/opt/grafana/provisioning/datasources/prometheus.yml` | Prometheus 数据源自动配置 |
| `/opt/grafana/provisioning/dashboards/aether.yml` | Dashboard 自动加载配置 |
| `/opt/grafana/dashboards/aether-databases.json` | Milvus、TuGraph、Ceph 综合 Dashboard |
| `/opt/grafana/admin-password` | Grafana 管理员密码文件，禁止外传 |

Grafana 使用的数据源：

```text
http://127.0.0.1:19090
```

Grafana 不直接采集三个数据库，而是通过 PromQL 向 Prometheus 查询数据。

## 8. Dashboard 访问地址

Dashboard UID 为 `aether-databases`，完整路径为：

```text
/d/aether-databases/aether-engine-milvus-tugraph-and-ceph
```

### 8.1 Hyper-V 宿主机自己访问

在 Hyper-V 宿主机 `192.168.42.101` 的浏览器中，可以直接访问虚拟机 IP：

```text
http://192.168.62.40:3000/d/aether-databases/aether-engine-milvus-tugraph-and-ceph
```

Prometheus 页面：

```text
http://192.168.62.40:19090
```

### 8.2 当前管理电脑访问

当前管理电脑已经通过持久 SSH 隧道映射本地端口：

```text
Grafana：http://127.0.0.1:13000/d/aether-databases/aether-engine-milvus-tugraph-and-ceph
Prometheus：http://127.0.0.1:19090
```

这些 `127.0.0.1` 地址只允许当前管理电脑自己访问。

管理电脑上的脚本为：

```text
C:\Users\liujiazheng\AppData\Local\AetherMonitoring\ceph-tunnel.ps1
```

该脚本目前只包含 Grafana 的本地转发 `13000 -> 3000` 和 Prometheus 的本地转发 `19090 -> 19090`，已经删除 Ceph 的两条 `-R` 反向转发。因此，管理电脑关机只会使本机的上述两个 `127.0.0.1` 地址暂时无法访问，不会停止远端 Prometheus、Grafana 或三个数据库的指标采集。

### 8.3 同一局域网的其他电脑访问

Hyper-V 宿主机当前已经配置端口转发：

```text
0.0.0.0:13000 -> 192.168.62.40:3000
```

与 Hyper-V 宿主机网络互通并被 Windows 防火墙允许的电脑，可以访问：

```text
http://192.168.42.101:13000/d/aether-databases/aether-engine-milvus-tugraph-and-ceph
```

如需重新创建该转发，可在宿主机以管理员身份执行：

```powershell
netsh interface portproxy add v4tov4 `
  listenaddress=0.0.0.0 listenport=13000 `
  connectaddress=192.168.62.40 connectport=3000

New-NetFirewallRule `
  -DisplayName "Aether Grafana TCP 13000" `
  -Direction Inbound -Action Allow -Protocol TCP `
  -LocalPort 13000 -RemoteAddress LocalSubnet
```

如果宿主机的物理网卡 IP 发生变化，应把网址中的 `192.168.42.101` 替换为宿主机新的局域网 IP。

## 9. 常用检查命令

在 TuGraph/监控虚拟机中执行：

```bash
# 查看 Prometheus、node_exporter、TuGraph exporter
systemctl status prometheus prometheus-node-exporter tugraph-exporter

# 查看 Grafana 和 TuGraph 容器
sudo docker ps --filter name=grafana --filter name=tugraph

# 检查本机 TuGraph exporter
curl http://127.0.0.1:9111/metrics

# 检查 Milvus 原生指标
curl http://192.168.62.97:9091/metrics

# 检查经 Ceph 宿主机隧道转发的 Ceph 指标
curl http://127.0.0.1:29283/metrics
curl http://127.0.0.1:29284/metrics

# 检查 Prometheus
curl http://127.0.0.1:19090/-/ready

# 检查 Grafana
curl http://127.0.0.1:3000/api/health
```

在 Ceph 宿主机的管理员 PowerShell 中检查常驻隧道：

```powershell
Get-ScheduledTask -TaskName "Aether Ceph Metrics Tunnel" | Select-Object TaskName,State
Get-Content "C:\ProgramData\AetherMonitoring\ceph-metrics-tunnel.log" -Tail 20
Test-NetConnection 192.168.42.101 -Port 2223
```

正常情况下，计划任务状态为 `Running`，日志中只有最新的 `starting SSH tunnel` 且没有反复出现 `tunnel stopped`，端口检测结果为 `TcpTestSucceeded : True`。

Prometheus 中常用查询：

```promql
up{job=~"ceph|milvus|tugraph"}
max(ceph_health_status{job="ceph",cluster="ceph-three-node"})
sum(max by (ceph_daemon) (ceph_osd_up{job="ceph",cluster="ceph-three-node"}))
sum(max by (ceph_daemon) (ceph_osd_in{job="ceph",cluster="ceph-three-node"}))
tugraph_up{job="tugraph"}
```

## 10. 注意事项

1. TuGraph RPC 已占用 `9090`，Prometheus 使用 `19090`，不要改回默认端口造成冲突。
2. Ceph 两个 MGR target 返回同一集群的指标，统计 OSD 和容量时应使用 `max()` 或按 `ceph_daemon` 去重。
3. Ceph 监控依赖 Ceph 宿主机上的计划任务 `Aether Ceph Metrics Tunnel`，不再依赖当前管理电脑。Ceph 宿主机停机、计划任务退出或无法访问 `192.168.42.101:2223` 时，Ceph targets 会显示 `DOWN`；Milvus 和 TuGraph 监控不受影响。
4. Milvus 2.6.20 与旧版指标名称不同，不能继续使用已不存在的两个 `*_vectors_count` 指标。
5. 浏览器自动翻译可能修改 Grafana DOM 并触发 `insertBefore` 页面错误。建议关闭该站点的自动翻译，使用 Grafana 自带语言设置。
6. 不要复制或提交 SSH 私钥、Grafana 管理员密码文件及 Prometheus 历史数据目录。
7. Prometheus 配置迁移前的备份为 `/etc/prometheus/prometheus.yml.bak-before-ceph-host-tunnel-20260911`。如需回退，应先确认旧隧道可用，再恢复旧端口配置。
