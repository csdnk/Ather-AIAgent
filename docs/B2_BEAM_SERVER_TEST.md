# B2 BEAM 数据补齐与服务器复测

BEAM 使用公开数据集 `zhangdw/Anchor-benchmarks` 中的官方 BEAM Parquet 文件。
本项目固定使用 100K 与 500K 两档：Smoke 各 1 条，Acceptance 各 10 条；不截断
原始对话。输入数据为 CC BY-SA 4.0，须保留 `BEAM_DATASET_MANIFEST.json` 记录的来源和
SHA256。

## 1. 启动服务

在项目根目录执行：

```bash
docker compose --profile milvus up -d --build redis etcd minio milvus b1-sidecar engine p3 celery-worker
docker compose ps
```

确认 `redis` 和 `milvus` 已经健康后再继续。

## 2. 下载并生成固定子集

以下命令将数据下载到宿主机项目目录的 `datasets/p3/source/beam`，并生成 B2 使用的
JSONL。下载 100K 与 500K 原始 Parquet 约 38 MB。

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

生成结果：

```text
datasets/p3/smoke_v0.1/beam.jsonl       # 2 条：100K 1 条 + 500K 1 条
datasets/p3/acceptance_v0.1/beam.jsonl  # 20 条：100K 10 条 + 500K 10 条
datasets/p3/BEAM_DATASET_MANIFEST.json
```

## 3. B2 回放

先使用 Smoke 验证，再运行 Acceptance。回放仅验证 B2 写入和三路召回链路；当前没有
物理压缩产物，因此结果中必须继续标注 `NOT_IMPLEMENTED`，不能据此声明 5x 压缩通过。

```bash
docker compose run --rm --no-deps \
  -v "$PWD:/workspace" -w /workspace \
  p3 python scripts/run_b2_dataset_replay.py \
  --locomo datasets/p3/smoke_v0.1/locomo.jsonl \
  --longmemeval datasets/p3/smoke_v0.1/longmemeval.jsonl \
  --beam datasets/p3/smoke_v0.1/beam.jsonl \
  --output artifacts/p3_b2_beam_smoke.json
```

```bash
docker compose run --rm --no-deps \
  -v "$PWD:/workspace" -w /workspace \
  p3 python scripts/run_b2_dataset_replay.py \
  --locomo datasets/p3/acceptance_v0.1/locomo.jsonl \
  --longmemeval datasets/p3/acceptance_v0.1/longmemeval.jsonl \
  --beam datasets/p3/acceptance_v0.1/beam.jsonl \
  --output artifacts/p3_b2_beam_acceptance.json
```
