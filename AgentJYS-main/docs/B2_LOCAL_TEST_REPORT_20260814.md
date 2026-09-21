# B2 本地完整测试报告（2026-08-14）

## 1. 结论

本次完成了单元测试、模拟集成测试、Redis Working Memory 基准、正式 BEAM 数据下载与
转换，以及含 BEAM 的 B2 Smoke/Acceptance 回放。

- 单元、模拟集成、Redis Working Memory 基准和 BEAM Smoke：通过。
- 最新 BEAM Acceptance：130 条样本的主体回放已完成，原 Redis 全量 `MGET` 超时未再出现；
  结果文件已生成。测试命令最终因 Milvus 清理阶段连接中断返回非零，属于环境清理异常，
  不影响已输出的回放指标。
- 物理压缩：仍为 `NOT_IMPLEMENTED`。

## 2. 环境

| 项目 | 本次环境 |
|---|---|
| 日期 | 2026-08-14 |
| 主机 | Windows 本地 Docker Desktop |
| Docker Engine | 29.6.1 |
| Redis | `redis:7-alpine` |
| Milvus | `milvusdb/milvus:v2.4.17` |
| BEAM 来源 | `zhangdw/Anchor-benchmarks` |
| BEAM 100K SHA256 | `c0519be25907005ba873c927c50877471d550873039d96c041554d0075a78ace` |
| BEAM 500K SHA256 | `af05921c979355038e1761b7cde3d2dd713200dd3071b278de0200f6c7f30122` |

## 3. 回归结果

| 测试 | 结果 | 耗时 |
|---|---:|---:|
| `tests/unit` | 153 passed，1 warning | 1.74 s |
| 3 个不依赖 Docker 的集成测试 | 3 passed | 2.05 s |
| 脚本语法与参数校验 | 通过 | - |
| Docker B1 -> P2 -> B2 -> B3 闭环 | 通过：一次性 smoke runner 正常退出 | P2 对象/向量、B2 异步任务、E1 检索、B3 动作均验证 |

此前超时的根因是 `p3-demo` 没有指定一次性命令，继承 Dockerfile 的常驻
`p3_service.py`，因此 Compose 一直等待其退出。现已改为 `run_compose_smoke.py`：它调用
P3 的 `/api/run-smoke`，验证 Redis/Celery → B1 Sidecar → P2 E1 写入与检索，以及 B3 动作后
正常退出。最新一次实测输出为 `vectors=1`、`B2 memories=2`、`B3 action=prefetch` 与
`COMPOSE_SMOKE_PASSED`。

## 4. Redis Working Memory 基准

口径：固定 1 KiB 文本；每场景 10,000 次；预置 256 条；并发 1/8/32；写、读、50/50 混合；
`MockWorkingMemoryManager -> RedisMemoryStore`。

| 并发 | 场景 | P50 | P95 | P99 | 吞吐 |
|---:|---|---:|---:|---:|---:|
| 1 | 写 | 0.097 ms | 0.219 ms | 0.302 ms | 8,292.843 ops/s |
| 1 | 读 | 0.170 ms | 0.373 ms | 0.495 ms | 4,859.574 ops/s |
| 1 | 混合 | 0.157 ms | 0.311 ms | 0.442 ms | 5,988.621 ops/s |
| 8 | 写 | 0.766 ms | 1.050 ms | 1.548 ms | 9,920.930 ops/s |
| 8 | 读 | 1.237 ms | 1.667 ms | 2.226 ms | 6,088.302 ops/s |
| 8 | 混合 | 1.237 ms | 1.627 ms | 1.959 ms | 7,494.762 ops/s |
| 32 | 写 | 3.360 ms | 4.055 ms | 5.193 ms | 9,224.413 ops/s |
| 32 | 读 | 5.635 ms | 6.751 ms | 8.171 ms | 5,528.075 ops/s |
| 32 | 混合 | 5.183 ms | 6.848 ms | 7.611 ms | 6,758.172 ops/s |

最大 P99 为 `8.171 ms`，在本机 Docker 条件下低于 10 ms。该指标仅代表 Redis
Working Memory 基线，不能替代完整 Context Pack 延迟。

原始结果：[p3_b2_working_latency_local_20260814.json](../artifacts/p3_b2_working_latency_local_20260814.json)。

## 5. 含 BEAM 的 Smoke 回放

输入包括 LoCoMo Smoke 2 条、LongMemEval Smoke 10 条、BEAM Smoke 2 条，合计 14 条。

| 指标 | 结果 |
|---|---:|
| Working 写入 | 2,136 |
| Episodic 写入 | 2,136 |
| Semantic 写入 / 查询 | 14 / 14 |
| Working 写入 P50 / P99 | 0.116 / 0.306 ms |
| Episodic 写入 P50 / P99 | 0.127 / 0.322 ms |
| Semantic 写入 P50 / P99 | 3.435 / 6.520 ms |
| 三路 Context 召回 P50 / P99 | 155.843 / 1,779.418 ms |

BEAM 的长上下文使三路召回 P99 显著升高；它不属于 Redis Working Memory 单项 P99。

原始结果：[p3_b2_beam_smoke_local_20260814.json](../artifacts/p3_b2_beam_smoke_local_20260814.json)。

## 6. BEAM Acceptance 回放：最新结果

初始 Acceptance 回放曾在约 161 秒后因 Redis 全量 `MGET` 超过默认 0.5 秒操作超时而失败。
修复范围索引与 256 条分批读取后，最新回放包含 LoCoMo 10 条、LongMemEval 100 条、
BEAM 20 条，共 130 条样本，主体流程完成并生成结果文件。

| 指标 | 最新结果 |
|---|---:|
| Working 写入 | 20,072 条 |
| Episodic 写入 | 20,072 条 |
| Semantic 写入 | 130 条 |
| 查询数 | 130 |
| Working 写入 P99 | 0.380 ms |
| Episodic 写入 P99 | 0.410 ms |
| Semantic 写入 P99 | 6.878 ms |
| Context 召回 P50 | 690.319 ms |
| Context 召回 P99 | 2,442.773 ms |

原 Redis 读取超时未再出现。命令在最后删除 Milvus 投影时，因 Milvus 服务连接中断而返回非零；
该异常发生在统计结果已输出之后，未改变上述回放数据。最新原始结果：
[p3_b2_acceptance_scoped_20260814.json](../artifacts/p3_b2_acceptance_scoped_20260814.json)。

注意：该回放中 tenant/agent 基本共用，因此它验证了“无界 Redis 单次读取已消除”，但不能作为
多 tenant/user/agent 隔离带来性能收益的证明。物理压缩仍为 `NOT_IMPLEMENTED`。

## 7. BEAM 数据产物

| 文件 | 内容 |
|---|---|
| `datasets/p3/smoke_v0.1/beam.jsonl` | 2 条：100K 1 条 + 500K 1 条 |
| `datasets/p3/acceptance_v0.1/beam.jsonl` | 20 条：100K 10 条 + 500K 10 条 |
| `datasets/p3/BEAM_DATASET_MANIFEST.json` | 来源、SHA256 与固定选择规则 |

BEAM 原始 Parquet 位于 `datasets/p3/source/beam/`，属于下载缓存，不应提交 Git。
