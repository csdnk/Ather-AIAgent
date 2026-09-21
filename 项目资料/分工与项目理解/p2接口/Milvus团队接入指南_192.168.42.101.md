# Milvus 团队接入指南

服务器：`192.168.42.101`，默认数据库：`default`。

## 可接入端口

| 端口 | 协议 | 用途 | 接入地址 |
| --- | --- | --- | --- |
| 19530 | HTTP / gRPC | 业务 API、SDK 接入 | `http://192.168.42.101:19530` |
| 9091 | HTTP | 健康检查、监控、管理页面 | `http://192.168.42.101:9091` |

根据 2026-09-11 的检查记录，两个端口均可连接。业务系统使用 **19530**，运维和监控使用 **9091**。

## 可提供的 API

### 业务 API（19530）

REST v2 前缀：`http://192.168.42.101:19530/v2/vectordb`。下表路径拼接此前缀，方法均为 **POST**，请求体为 JSON。

| 功能 | API 路径 |
| --- | --- |
| 向量相似度搜索 | `/entities/search` |
| 混合搜索 | `/entities/hybrid_search` |
| 条件查询 | `/entities/query` |
| 按主键获取数据 | `/entities/get` |
| 插入数据 | `/entities/insert` |
| 插入或更新数据 | `/entities/upsert` |
| 删除数据 | `/entities/delete` |
| 集合列表、详情、是否存在 | `/collections/list`、`/collections/describe`、`/collections/has` |
| 创建、删除集合 | `/collections/create`、`/collections/drop` |
| 加载、释放集合 | `/collections/load`、`/collections/release` |
| 集合统计、加载状态 | `/collections/get_stats`、`/collections/get_load_state` |
| 索引列表、详情、创建、删除 | `/indexes/list`、`/indexes/describe`、`/indexes/create`、`/indexes/drop` |
| 分区列表、创建、删除 | `/partitions/list`、`/partitions/create`、`/partitions/drop` |
| 数据库列表、详情、创建、删除 | `/databases/list`、`/databases/describe`、`/databases/create`、`/databases/drop` |
| 别名列表、创建、修改、删除 | `/aliases/list`、`/aliases/create`、`/aliases/alter`、`/aliases/drop` |
| 批量导入、任务列表、进度详情 | `/jobs/import/create`、`/jobs/import/list`、`/jobs/import/describe` |

同一端口也提供 **gRPC / SDK** 接口，可执行数据读写、搜索、集合及索引管理等操作；兼容 REST v1 的前缀为 `/v1/vector`。

以上为常用接口。原检查已验证数据库、集合列表等只读接口；写入和搜索尚未完成业务联调。

### 运维 API 与管理页面（9091）

下表路径拼接 `http://192.168.42.101:9091`，方法均为 **GET**。

| 功能 | 路径 |
| --- | --- |
| 服务健康检查 | `/healthz` |
| 进程存活检查 | `/livez` |
| Prometheus 监控指标 | `/metrics` |
| 管理 API 健康检查 | `/api/v1/health` |
| 数据库列表 | `/api/v1/_db/list` |
| 集合列表 | `/api/v1/_collection/list` |
| 管理页面 | [/webui/](http://192.168.42.101:9091/webui/) |

上述运维入口在原检查中均返回 HTTP 200。
