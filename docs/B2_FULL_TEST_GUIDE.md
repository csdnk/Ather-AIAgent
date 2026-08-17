# B2 完整测试与服务器复测指南

本文用于开发人员在提交 B2 改动后，完成本地回归、BEAM 数据准备、Redis/Milvus
联调、Working Memory 性能基线和公开数据回放。所有命令均从项目根目录执行。

## 1. 测试范围与成功标准

| 阶段 | 覆盖内容 | 成功标准 |
|---|---|---|
| 本地单元测试 | B1/B2/B3 模型、记忆管理、状态机、Context、存储适配 | `153 passed` 或更高 |
| 本地模拟集成 | P3 闭环、统一数据流、TUI 四场景 | 3 项通过 |
| Docker 闭环 | B1 -> P2 -> B2 -> B3 示例 | Compose 集成测试通过 |
| B2 Working 基线 | Redis 后端、1 KiB、10,000 次、并发 1/8/32 | 最大 P99 小于 10 ms 才可标记为达到该基线 |
| B2 数据回放 | LoCoMo、LongMemEval、BEAM | 生成 JSON 结果；延迟结果不得替代 Recall@K 或压缩率 |

本项目当前没有物理压缩产物。因此无论 BEAM 是否成功回放，压缩率均必须报告为
`NOT_IMPLEMENTED`，不得写为通过 5x 压缩验收。

## 2. 当前已验证状态

2026-08-14 在 Windows 本地 Python 3.13.14 环境中已执行：

```text
tests/unit: 153 passed, 1 warning
tests/integration/test_p3_closed_loop.py: passed
tests/integration/test_unified_flow_smoke.py: passed
tests/integration/test_tui_four_scenarios.py: passed
prepare_beam_dataset.py --help: passed
run_b2_dataset_replay.py --help: passed
```

本机 Docker 引擎未启动，所以 Docker、Redis、Milvus、Celery 和实际 BEAM 回放必须在
服务器执行。

## 3. 开始前检查

```bash
git status --short
git rev-parse HEAD
python --version
docker version
docker compose version
```

将上述输出、当前 commit 和执行日期写到新建的结果目录，禁止覆盖既有实验结果。

```bash
RUN_ID="p3_b2_$(date +%Y%m%d_%H%M%S)"
mkdir -p "artifacts/${RUN_ID}"
git rev-parse HEAD > "artifacts/${RUN_ID}/git_commit.txt"
docker version > "artifacts/${RUN_ID}/docker_version.txt"
```

## 4. 本地快速回归

```bash
python -m pytest -q tests/unit

python -m pytest -q \
  tests/integration/test_p3_closed_loop.py \
  tests/integration/test_unified_flow_smoke.py \
  tests/integration/test_tui_four_scenarios.py

python -m py_compile \
  scripts/prepare_beam_dataset.py \
  scripts/run_b2_dataset_replay.py \
  benchmarks/p3/b2_working_latency.py
```

完整 Docker 闭环测试需要 Docker Engine 正常运行：

```bash
python -m pytest -q tests/integration/test_compose_stack.py
```

## 5. 启动服务器依赖

BEAM 与数据回放依赖 Redis 和 Milvus；B1、P2、Celery 一并启动，以保证完整 P3 环境可用。

```bash
docker compose --profile milvus up -d --build \
  redis etcd minio milvus b1-sidecar engine p3 celery-worker

docker compose ps
```

继续前确认 Redis、Milvus 和 P3 处于运行或健康状态。若 Milvus 尚在启动，等待其健康检查
通过后再开始回放。测试结束后可执行：

```bash
docker compose --profile milvus down
```

不要在未确认目标项目目录的情况下附加 `--volumes`，避免删除他人保留的实验数据。

## 6. 准备正式 BEAM 数据

BEAM 数据来源为 `zhangdw/Anchor-benchmarks`，使用官方的 100K 与 500K Parquet 文件。
固定选择规则为：Smoke 各 1 条；Acceptance 各 10 条。生成脚本会记录输入 SHA256、来源和
选择规则到 `datasets/p3/BEAM_DATASET_MANIFEST.json`。

```bash
mkdir -p datasets/p3/source/beam

docker compose run --rm --no-deps \
  -v "$PWD:/workspace" -w /workspace \
  p3 hf download zhangdw/Anchor-benchmarks \
  --repo-type dataset \
  --local-dir datasets/p3/source/beam \
  BEAM/data/100K-00000-of-00001.parquet \
  BEAM/data/500K-00000-of-00001.parquet

docker compose run --rm --no-deps \
  -v "$PWD:/workspace" -w /workspace \
  p3 python scripts/prepare_beam_dataset.py \
  --beam-100k datasets/p3/source/beam/BEAM/data/100K-00000-of-00001.parquet \
  --beam-500k datasets/p3/source/beam/BEAM/data/500K-00000-of-00001.parquet
```

生成后必须确认：

```bash
wc -l datasets/p3/smoke_v0.1/beam.jsonl
wc -l datasets/p3/acceptance_v0.1/beam.jsonl
cat datasets/p3/BEAM_DATASET_MANIFEST.json
```

预期行为：Smoke 为 2 行，Acceptance 为 20 行。原始 Parquet 和缓存不提交 Git；保留 manifest
作为实验可追溯证据。

## 7. Redis Working Memory 验收基线

该测试只衡量 `WorkingMemoryManager -> RedisMemoryStore`。它不包含 Milvus、B1、B3 或完整
Context Pack，因此不能用来代表完整 Agent 推理延迟。

```bash
docker compose run --rm --no-deps \
  -v "$PWD:/workspace" -w /workspace \
  p3 python benchmarks/p3/b2_working_latency.py \
  --redis-url redis://redis:6379/0 \
  --samples 10000 \
  --preload 256 \
  --output "artifacts/${RUN_ID}/b2_working_latency.json"
```

检查输出是否同时包含：

- 并发 `1`、`8`、`32`；
- `write`、`read`、`mixed_50_50` 三种场景；
- P50、P95、P99 与吞吐；
- 固定 1 KiB 文本、每场景 10,000 次、预置 256 条的配置。

最大 P99 小于 10 ms 时，才可写“达到 Redis Working Memory 本次服务器基线”；否则必须记录
实际数值与服务器资源情况，不能套用本地结果。

## 8. B2 公开数据 Smoke 回放

先运行小样本。该脚本不调用 B1 HTTP 推理，也不运行 B3 调度；它测试 Redis、Milvus、三类
Memory Manager 和三路召回的数据链路与延迟。

```bash
docker compose run --rm --no-deps \
  -v "$PWD:/workspace" -w /workspace \
  p3 python scripts/run_b2_dataset_replay.py \
  --locomo datasets/p3/smoke_v0.1/locomo.jsonl \
  --longmemeval datasets/p3/smoke_v0.1/longmemeval.jsonl \
  --beam datasets/p3/smoke_v0.1/beam.jsonl \
  --output "artifacts/${RUN_ID}/b2_replay_smoke.json"
```

先检查脚本退出码为 0，再打开 JSON，确认 `samples`、写入数、查询数以及各项 P50/P99 均非空。
Smoke 失败时不要直接运行 Acceptance；保留容器日志、JSON 和 BEAM manifest 后定位问题。

## 9. B2 公开数据 Acceptance 回放

Smoke 通过后运行：

```bash
docker compose run --rm --no-deps \
  -v "$PWD:/workspace" -w /workspace \
  p3 python scripts/run_b2_dataset_replay.py \
  --locomo datasets/p3/acceptance_v0.1/locomo.jsonl \
  --longmemeval datasets/p3/acceptance_v0.1/longmemeval.jsonl \
  --beam datasets/p3/acceptance_v0.1/beam.jsonl \
  --output "artifacts/${RUN_ID}/b2_replay_acceptance.json"
```

当前回放输出的是数据链路和延迟结果。它不是以下质量验收的替代品：

- Recall@1、Recall@5、Recall@10；
- evidence session 命中率；
- knowledge-update、temporal-reasoning、abstention 判断；
- 每条召回的 `memory_id`、`source_id`、`score`、`trace_id` 明细；
- BEAM/LoCoMo 的真实压缩率。

### 当前已知结果：默认配置会在 BEAM Acceptance 失败

截至 2026-08-14，正式 20 条 BEAM Acceptance 在当前实现下触发了 Redis 操作超时：

```text
TimeoutError: RedisMemoryStore.list() 的全量 MGET 超过 0.5 秒
```

原因是 Working/Episodic 召回会扫描命名空间中的全部记录；BEAM 长会话写入后，单次扫描
规模超出默认操作超时。该失败应作为性能与可扩展性缺陷记录，不能通过单纯增大 timeout
后宣称默认实现通过。修复方向是按 tenant/user/agent/session 建立 Redis 范围索引并分页查询，
修复后从 Smoke 重新执行本章测试。

## 10. 结果提交要求

每次服务器复测至少保留：

```text
artifacts/<RUN_ID>/
  git_commit.txt
  docker_version.txt
  b2_working_latency.json
  b2_replay_smoke.json
  b2_replay_acceptance.json
datasets/p3/BEAM_DATASET_MANIFEST.json
```

报告必须区分以下指标：

| 指标 | 可否与其他指标混用 |
|---|---|
| Redis Working Memory P99 | 不可替代完整 Context Pack 延迟 |
| 完整 B2 回放延迟 | 不可替代 Recall@K 或证据命中率 |
| 数据链路成功 | 不可替代物理压缩率 |
| SQLite 旧基线 | 不可与 Redis 新基线直接比较 |

## 11. 常见故障

| 现象 | 排查与处理 |
|---|---|
| `docker` 无法连接 daemon | 启动 Docker Engine，或在服务器确认 Docker 服务和权限后重试。 |
| `hf download` 失败 | 检查服务器到 Hugging Face 的网络、代理或缓存；不要用随机文本替代 BEAM。 |
| 缺少 `pyarrow` / `hf` | 重新执行 `docker compose ... up -d --build`，P3 镜像已启用 `b2-dataset` 可选依赖。 |
| `redis` 或 `milvus` 无法解析 | 必须使用 `docker compose run`，使测试容器加入 Compose 网络；不要直接在宿主机运行回放脚本。 |
| Milvus 未就绪 | 等待健康检查通过再运行；不要把连接失败结果记录为性能数据。 |
| BEAM JSONL 行数不对 | 停止回放，检查输入 Parquet、`BEAM_DATASET_MANIFEST.json` 与准备脚本的选择规则。 |
| 回放有延迟但没有压缩率 | 这是当前预期限制，报告 `NOT_IMPLEMENTED`。 |
