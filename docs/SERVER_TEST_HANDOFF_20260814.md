# P3 B2 服务器测试与交接说明

> 版本：v0.1  
> 日期：2026-08-14  
> 适用代码：当前 `agent-bug` 目录  
> 目标：在服务器上复现 B1 → P2 → B2 → B3 一次性闭环、Redis Working Memory 基准和含 BEAM 的 B2 数据集回放。

## 1. 测试范围与判定

| 编号 | 测试 | 通过条件 | 证据 |
|---|---|---|---|
| S00 | 代码与数据预检 | Docker、Compose、BEAM/LoCoMo/LongMemEval 文件齐全 | 命令输出、Git commit |
| S01 | 单元回归 | `tests/unit` 全部通过 | pytest 输出 |
| S02 | B1 → P2 → B2 → B3 闭环 | 输出 `COMPOSE_SMOKE_PASSED`，容器正常退出 | Compose 日志 |
| S03 | Working Memory 基准 | 输出并保存并发 1/8/32 的 P50/P95/P99 | JSON 结果文件 |
| S04 | B2 Acceptance 回放 | LoCoMo 10、LongMemEval 100、BEAM 20 共 130 样本完成并生成 JSON | JSON 结果、控制台输出 |

说明：S03 的 Redis Working Memory P99 是合同短期工作记忆接口指标的参考证据；Context Pack、Milvus、Celery 和完整回放的延迟必须单独报告，不能混为同一指标。

## 2. 服务器前置条件

- Linux 服务器已安装 Docker Engine 与 Docker Compose v2；执行用户有 Docker 权限，或命令前加 `sudo`。
- 代码目录完整包含 `datasets/p3/`、`scripts/`、`compose.yaml` 和 `engine/`。
- 首次构建镜像需要访问 Python 包源与 Docker 镜像源；已构建过时可离线复用本地镜像。
- Docker 需至少有 Redis、Milvus、B1 Sidecar、P2 Engine、P3 与 Celery Worker 的运行资源。

以下命令以项目目录为例。请先替换为服务器上的真实路径：

```bash
export PROJECT_DIR=/path/to/agent-bug
cd "$PROJECT_DIR"
```

不要把服务器密码、令牌或私有镜像凭据写入此文档、脚本或 Git。

## 3. 预检与结果目录

```bash
cd "$PROJECT_DIR"

git rev-parse HEAD
docker version --format '{{.Server.Version}}'
docker compose version

test -f datasets/p3/smoke_v0.1/locomo.jsonl
test -f datasets/p3/smoke_v0.1/longmemeval.jsonl
test -f datasets/p3/smoke_v0.1/beam.jsonl
test -f datasets/p3/acceptance_v0.1/locomo.jsonl
test -f datasets/p3/acceptance_v0.1/longmemeval.jsonl
test -f datasets/p3/acceptance_v0.1/beam.jsonl

export RUN_TAG="server_$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "artifacts/$RUN_TAG"
```

如任一 `test -f` 失败，先补齐数据集，不要跳过后继续将结果标记为正式 Acceptance。

## 4. S01：单元回归

使用项目 Python 环境时：

```bash
python -m pytest -q tests/unit | tee "artifacts/$RUN_TAG/pytest_unit.log"
```

若服务器只使用 Docker，则先构建一次镜像，再在容器内执行：

```bash
docker compose build p3
docker compose run --rm --no-deps -v "$PROJECT_DIR:/workspace" -w /workspace \
  p3 python -m pytest -q tests/unit | tee "artifacts/$RUN_TAG/pytest_unit.log"
```

## 5. S02：真实 B1 → P2 → B2 → B3 一次性闭环

```bash
docker compose --profile demo up --build --abort-on-container-exit \
  --exit-code-from p3-demo 2>&1 | tee "artifacts/$RUN_TAG/compose_smoke.log"
```

通过时日志必须同时包含：

```text
P2 object=...
vectors=1
B2 memories=2
B3 action=...
COMPOSE_SMOKE_PASSED
```

该命令中的 `p3-demo` 是一次性 runner，会调用 P3 `/api/run-smoke`，再验证：

```text
P2 E2 对象写入
→ B2 长文本任务投递到 Redis/Celery
→ B1 Sidecar 向量化
→ P2 E1 向量写入与检索
→ B3 调度动作
→ runner 退出
```

测试结束后停止容器但保留数据卷，便于定位问题或继续 S03/S04：

```bash
docker compose --profile demo down --remove-orphans
```

不要在常规测试结束时使用 `--volumes`；除非确认需要删除测试数据卷。

## 6. 启动 S03/S04 所需依赖

```bash
docker compose --profile milvus up -d redis etcd minio milvus
docker compose ps
```

确认 Redis 与 Milvus 就绪后再继续。若需要实际 B1/P2/Celery 链路，请使用第 5 节的闭环命令；S03 与 S04 是 B2 存储和数据集回放测试。

## 7. S03：Redis Working Memory 基准

```bash
docker compose run --rm --no-deps \
  -v "$PROJECT_DIR:/workspace" -w /workspace p3 \
  python benchmarks/p3/b2_working_latency.py \
  --redis-url redis://redis:6379/0 \
  --samples 10000 \
  --preload 256 \
  --output "artifacts/$RUN_TAG/b2_working_latency.json" \
  | tee "artifacts/$RUN_TAG/b2_working_latency.log"
```

记录所有并发档位的 P50/P95/P99、吞吐与错误。合同短期工作记忆指标为 P99 `< 10 ms`；服务器结果应以本次输出为准，不能直接引用本地结果。

## 8. S04：含 BEAM 的完整 B2 Acceptance 回放

```bash
docker compose run --rm --no-deps \
  -v "$PROJECT_DIR:/workspace" -w /workspace p3 \
  python scripts/run_b2_dataset_replay.py \
  --locomo datasets/p3/acceptance_v0.1/locomo.jsonl \
  --longmemeval datasets/p3/acceptance_v0.1/longmemeval.jsonl \
  --beam datasets/p3/acceptance_v0.1/beam.jsonl \
  --output "artifacts/$RUN_TAG/b2_acceptance_beam.json" \
  | tee "artifacts/$RUN_TAG/b2_acceptance_beam.log"
```

预期规模：LoCoMo 10 条、LongMemEval 100 条、BEAM 20 条，共 130 条样本。应保存以下指标：

- Working/Episodic/Semantic 写入量；
- Working/Episodic/Semantic 写入 P50/P99；
- Context recall P50/P99；
- 命令退出码、异常栈与结果 JSON。

注意：若主体统计已经输出但命令在最后的 Milvus 投影清理阶段失败，应记录为 `ENVIRONMENT` 或 `MILVUS_CLEANUP_FAILURE`，不能直接写成“Acceptance 完整通过”；同时保留 JSON 和完整日志供后续判断。

## 9. 服务器结果报告模板

每次服务器测试后，在 `artifacts/$RUN_TAG/SERVER_TEST_REPORT.md` 写入：

```markdown
# P3 服务器测试报告

- 测试时间（UTC）：
- 服务器系统 / CPU / 内存：
- Docker / Compose 版本：
- Git commit：
- 数据集版本与 SHA256：

| 测试 | 结果 | 关键指标 | 证据 |
|---|---|---|---|
| S01 单元回归 | PASS/FAIL | passed / failed | pytest_unit.log |
| S02 端到端闭环 | PASS/FAIL | vectors, B2 memories, B3 action | compose_smoke.log |
| S03 Working 基准 | PASS/FAIL | 各并发 P99 | b2_working_latency.json |
| S04 BEAM Acceptance | PASS/FAIL/PARTIAL | samples, Context P99 | b2_acceptance_beam.json |

## 异常与结论

- 环境异常：
- 代码异常：
- 数据异常：
- 是否满足合同 Working Memory P99 < 10ms：
- 物理压缩状态：
```

## 10. 常见问题

| 现象 | 分类 | 处理 |
|---|---|---|
| `p3-demo` 长时间不退出 | 编排/代码 | 确认当前代码含 `scripts/run_compose_smoke.py`，并重新 `docker compose build p3-demo` |
| B1 未就绪 | 环境 | 查看 `docker compose logs b1-sidecar`，确认模型下载、CPU 内存和健康检查 |
| Celery 任务一直 `PENDING` | 环境 | 查看 `docker compose logs celery-worker redis`，确认 worker 与 Redis broker 同网络 |
| P2 E1 无检索结果 | 接口/数据 | 查看 P3、engine 日志与 collection、tenant/user/agent metadata |
| Milvus 清理连接失败 | 环境 | 保存结果 JSON 和日志，检查 `docker compose ps`、Milvus 日志后再单独清理 |
| BEAM 数据缺失 | 数据 | 使用 `docs/B2_BEAM_SERVER_TEST.md` 下载/准备，记录来源与 SHA256 |

## 11. 结果管理

1. 每次服务器执行使用新的 `RUN_TAG`，禁止覆盖历史 JSON 或日志。
2. 原始 BEAM Parquet 与下载缓存不提交 Git。
3. 报告必须同时记录代码 commit、镜像版本、数据集版本、完整命令和退出码。
4. 将环境问题、代码问题和数据问题分开标记；不要把未启动的依赖记为业务性能失败。
