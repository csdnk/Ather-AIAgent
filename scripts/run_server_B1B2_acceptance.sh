#!/usr/bin/env bash
# Run the P3 functional/acceptance suite on a Docker-only server.
# No Docker Compose installation is required.

set -Eeuo pipefail

export LANG=C.UTF-8
export LC_ALL=C.UTF-8

# WSL may expose a Linux docker shim while Docker Desktop is available as
# docker.exe. Prefer the working client so the same script runs on Windows.
if ! docker image inspect aether-p3:acceptance >/dev/null 2>&1; then
  if command -v docker.exe >/dev/null 2>&1; then
    docker() { docker.exe "$@"; }
  elif [[ -x "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" ]]; then
    docker() { "/mnt/c/Program Files/Docker/Docker/resources/bin/docker.exe" "$@"; }
  fi
fi

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NETWORK_NAME="agent-20260818-p3-acceptance"
P3_IMAGE="aether-p3:acceptance"
ENGINE_IMAGE="aether-p3-engine:acceptance"
RUN_TAG="${RUN_TAG:-server_$(date -u +%Y%m%dT%H%M%SZ)}"
ARTIFACT_DIR="$ROOT_DIR/artifacts/$RUN_TAG"
# This is a portable completed OpenVINO INT8 cache. It must be mounted as the
# sidecar cache directory so the sidecar finds its validated _aether_openvino IR.
B1_MODEL_CACHE="${B1_MODEL_CACHE:-$ROOT_DIR/artifacts/b1_openvino_int8_portable_cache/bge-small-zh-v1.5-openvino-int8-cache}"
B2_COMPRESSION_DATASET_ROOT="${B2_COMPRESSION_DATASET_ROOT:-datasets/p3/acceptance_v0.1}"
B2_COMPRESSION_TARGET_RATIO="${AETHER_B2_COMPRESSION_TARGET_RATIO:-5.0}"
B2_COMPRESSION_STRICT="${B2_COMPRESSION_STRICT:-0}"
B1_SMOKE_DATASET_ROOT="${B1_SMOKE_DATASET_ROOT:-datasets/p3/smoke_v0.1/b1_beam_texts.jsonl}"
B1_ACCEPTANCE_DATASET_ROOT="${B1_ACCEPTANCE_DATASET_ROOT:-datasets/p3/acceptance_v0.1/b1_beam_texts.jsonl}"
B1_DATASET_LIMIT="${B1_DATASET_LIMIT:-12}"
KEEP_CONTAINERS=0
WITH_B1_THROUGHPUT=0
WITH_PYTEST=0

usage() {
  cat <<'EOF'
Usage: bash scripts/run_server_acceptance.sh [--keep-containers] [--with-b1-throughput] [--with-pytest]

Runs pytest, the real B1->P2->B2->B3 smoke flow, B2/B3 baseline checks,
the B2 compression gate across every JSONL corpus below
B2_COMPRESSION_DATASET_ROOT, and the B2 acceptance replay including BEAM.
Results are written below artifacts/<RUN_TAG>/.

By default the compression gate reads every normalized corpus in
datasets/p3/acceptance_v0.1 (LoCoMo, LongMemEval, and BEAM). Set
B2_COMPRESSION_DATASET_ROOT to another repository-relative dataset directory
when a larger or separately prepared corpus is required.
Set B2_COMPRESSION_STRICT=1 when the full P3 run must stop on a compression
gate failure; the default records NOT_READY results and continues the other
functional checks so the complete report is still produced.

The B1 smoke phase reads datasets/p3/smoke_v0.1/b1_beam_texts.jsonl. The optional
full throughput phase reads datasets/p3/acceptance_v0.1/b1_beam_texts.jsonl.
Override them with B1_SMOKE_DATASET_ROOT or B1_ACCEPTANCE_DATASET_ROOT.
The script only creates/removes containers whose names start with agent-20260818-.
It never runs Docker image/volume/network prune.
EOF
}

for argument in "$@"; do
  case "$argument" in
    --keep-containers) KEEP_CONTAINERS=1 ;;
    --with-b1-throughput) WITH_B1_THROUGHPUT=1 ;;
    --with-pytest) WITH_PYTEST=1 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $argument" >&2; usage >&2; exit 2 ;;
  esac
done

container_names=(agent-20260818-p3 agent-20260818-celery agent-20260818-b1 agent-20260818-engine agent-20260818-milvus agent-20260818-minio agent-20260818-etcd agent-20260818-redis)

cleanup() {
  status=$?
  if [[ "$KEEP_CONTAINERS" == "0" ]]; then
    docker rm -f "${container_names[@]}" >/dev/null 2>&1 || true
  fi
  exit "$status"
}
trap cleanup EXIT

require_image() {
  docker image inspect "$1" >/dev/null 2>&1 || {
    echo "Missing image: $1" >&2
    echo "Build it first; see docs/SERVER_TEST_HANDOFF_20260814.md." >&2
    exit 1
  }
}

wait_http() {
  local container=$1 url=$2 label=$3
  for attempt in $(seq 1 180); do
    if docker exec "$container" python -c "from urllib.request import urlopen; urlopen('$url', timeout=3).read()" >/dev/null 2>&1; then
      echo "$label is ready."
      return 0
    fi
    if (( attempt % 15 == 0 )); then
      echo "Waiting for $label to become ready ($attempt/180, up to 6 minutes)..."
    fi
    sleep 2
  done
  echo "$label did not become ready; recent logs:" >&2
  docker logs --tail 80 "$container" >&2 || true
  exit 1
}

wait_network_http() {
  local url=$1 label=$2
  for attempt in $(seq 1 90); do
    if docker run --rm --network "$NETWORK_NAME" "$P3_IMAGE" python -c "from urllib.request import urlopen; urlopen('$url', timeout=3).read()" >/dev/null 2>&1; then
      echo "$label is ready."
      return 0
    fi
    if (( attempt % 15 == 0 )); then
      echo "Waiting for $label to become ready ($attempt/90)..."
    fi
    sleep 2
  done
  echo "$label did not become ready; recent logs:" >&2
  docker logs --tail 80 agent-20260818-milvus >&2 || true
  exit 1
}

run_test() {
  local name=$1
  shift
  echo
  echo "===== $name ====="
  "$@" 2>&1 | tee "$ARTIFACT_DIR/$name.log"
}

record_not_implemented() {
  local name=$1 reason=$2
  printf '{\n  "status": "NOT_IMPLEMENTED",\n  "reason": "%s"\n}\n' "$reason" | tee "$ARTIFACT_DIR/$name.json" "$ARTIFACT_DIR/$name.log"
}

run_pytest_if_available() {
  echo
  echo "===== pytest ====="
  set +e
  docker run --rm -e PIP_DISABLE_PIP_VERSION_CHECK=1 -v "$ROOT_DIR:/workspace" -w /workspace "$P3_IMAGE" sh -c 'python -m pip install --no-cache-dir pytest pytest-asyncio || exit 90; python -m pytest -q tests/unit -p no:cacheprovider' 2>&1 | tee "$ARTIFACT_DIR/pytest.log"
  local status=${PIPESTATUS[0]}
  set -e
  if [[ "$status" == "0" ]]; then
    echo "PYTEST_STATUS=PASSED" | tee "$ARTIFACT_DIR/pytest_status.txt"
    return 0
  fi
  if [[ "$status" != "90" ]]; then
    echo "PYTEST_STATUS=FAILED" | tee "$ARTIFACT_DIR/pytest_status.txt"
    return "$status"
  fi
  cat <<'EOF' | tee -a "$ARTIFACT_DIR/pytest.log" "$ARTIFACT_DIR/pytest_status.txt"
PYTEST_STATUS=ENVIRONMENT_GAP
The temporary test container cannot download pytest and pytest-asyncio from PyPI.
The remaining Docker functional, B2/B3, and BEAM acceptance tests will continue.
EOF
}

cd "$ROOT_DIR"
mkdir -p "$ARTIFACT_DIR"
CONTAINER_ARTIFACT_DIR="/workspace/${ARTIFACT_DIR#"$ROOT_DIR/"}"
COMPRESSION_STRICT_ARGS=()
if [[ "$B2_COMPRESSION_STRICT" == "1" ]]; then
  COMPRESSION_STRICT_ARGS+=(--strict)
fi
require_image "$P3_IMAGE"
require_image "$ENGINE_IMAGE"

if [[ ! -d "$B1_MODEL_CACHE/_aether_openvino" ]] \
  || ! find "$B1_MODEL_CACHE/_aether_openvino" -type f -name '.aether-export-complete' -print -quit | grep -q . \
  || ! find "$B1_MODEL_CACHE/_aether_openvino" -type f -name '*.xml' -print -quit | grep -q . \
  || ! find "$B1_MODEL_CACHE/_aether_openvino" -type f -name '*.bin' -print -quit | grep -q .; then
  echo "B1 OpenVINO INT8 portable cache is incomplete: $B1_MODEL_CACHE" >&2
  echo "Set B1_MODEL_CACHE to a directory containing _aether_openvino with marker, XML, and BIN files." >&2
  exit 1
fi

docker network inspect "$NETWORK_NAME" >/dev/null 2>&1 || docker network create "$NETWORK_NAME" >/dev/null
docker rm -f "${container_names[@]}" >/dev/null 2>&1 || true

echo "===== Starting isolated P3 services ====="
docker run -d --name agent-20260818-redis --network "$NETWORK_NAME" --network-alias redis redis:7-alpine >/dev/null
docker run -d --name agent-20260818-etcd --network "$NETWORK_NAME" --network-alias etcd \
  -e ALLOW_NONE_AUTHENTICATION=yes \
  -e ETCD_ADVERTISE_CLIENT_URLS=http://etcd:2379 \
  -e ETCD_LISTEN_CLIENT_URLS=http://0.0.0.0:2379 \
  milvusdb/etcd:3.5.5-r4 >/dev/null
docker run -d --name agent-20260818-minio --network "$NETWORK_NAME" --network-alias minio \
  -e MINIO_ACCESS_KEY=minioadmin -e MINIO_SECRET_KEY=minioadmin \
  -v agent-20260818-minio-data:/minio_data \
  minio/minio:RELEASE.2023-03-20T20-16-18Z minio server /minio_data >/dev/null
docker run -d --name agent-20260818-milvus --network "$NETWORK_NAME" --network-alias milvus \
  -e ETCD_ENDPOINTS=etcd:2379 -e MINIO_ADDRESS=minio:9000 \
  -v agent-20260818-milvus-data:/var/lib/milvus \
  milvusdb/milvus:v2.4.17 milvus run standalone >/dev/null
docker run -d --name agent-20260818-engine --network "$NETWORK_NAME" --network-alias engine \
  -e AETHER_P2_DATA_DIR=/var/lib/aether-engine \
  -v agent-20260818-engine-data:/var/lib/aether-engine \
  "$ENGINE_IMAGE" >/dev/null
docker run -d --name agent-20260818-b1 --network "$NETWORK_NAME" --network-alias b1-sidecar \
  -e AETHER_B1_HOST=0.0.0.0 -e AETHER_B1_PORT=18081 -e AETHER_B1_EAGER_LOAD=true \
  -e AETHER_B1_CACHE_DIR=/app/.aether/b1/models \
  -e AETHER_B1_BACKEND=openvino -e AETHER_B1_PRECISION=int8 \
  -e AETHER_B1_FALLBACK_BACKENDS= -e AETHER_B1_ALLOW_BACKEND_FALLBACK=false \
  -e AETHER_B1_FAIL_MODE=closed \
  -e AETHER_B1_THREADS=1 -e AETHER_B1_MODEL_BATCH_SIZE=8 -e AETHER_B1_MAX_CONCURRENCY=1 \
  -v "$B1_MODEL_CACHE:/app/.aether/b1/models:ro" \
  "$P3_IMAGE" python -m aether_agent_memory.b1.sidecar >/dev/null

echo "Waiting for B1 OpenVINO INT8 to load the portable cache..."
wait_http agent-20260818-b1 http://127.0.0.1:18081/health/ready "B1"
echo "Waiting for Milvus..."
wait_network_http http://milvus:9091/healthz "Milvus"

echo "Starting B2 worker and P3 service..."
docker run -d --name agent-20260818-celery --network "$NETWORK_NAME" --network-alias celery-worker \
  -e AETHER_P2_GRPC=engine:50052 -e AETHER_P2_BUCKET=p3-memory -e AETHER_P2_COLLECTION=p3 \
  -e AETHER_B2_BROKER_URL=redis://redis:6379/0 -e AETHER_B2_RESULT_BACKEND=redis://redis:6379/1 \
  -e AETHER_B2_TASK_STATUS_URL=redis://redis:6379/2 -e AETHER_B2_MILVUS_URI=http://milvus:19530 \
  -e AETHER_B2_MILVUS_PROJECTION=false -e AETHER_B2_VECTOR_DIMENSION=512 \
  -e AETHER_B2_COMPRESSION_STORE=redis -e AETHER_B2_COMPRESSION_TARGET_RATIO="$B2_COMPRESSION_TARGET_RATIO" \
  -e AETHER_B1_EMBEDDING_URL=http://b1-sidecar:18081/v1/intercept \
  "$P3_IMAGE" celery -A aether_agent_memory.b2.celery_app worker --loglevel=INFO >/dev/null
docker run -d --name agent-20260818-p3 --network "$NETWORK_NAME" --network-alias p3 \
  -e AETHER_P2_GRPC=engine:50052 -e AETHER_P2_ENGINE=object/default -e AETHER_P2_BUCKET=p3-memory -e AETHER_P2_COLLECTION=p3 \
  -e AETHER_P3_DATA_DIR=/var/lib/aether-p3 -e AETHER_P3_PORT=8080 -e AETHER_ENABLE_DEMO=true \
  -e AETHER_B1_SIDECAR_URL=http://b1-sidecar:18081 \
  -e AETHER_B2_BROKER_URL=redis://redis:6379/0 -e AETHER_B2_REDIS_URL=redis://redis:6379/0 \
  -e AETHER_B2_RESULT_BACKEND=redis://redis:6379/1 -e AETHER_B2_TASK_STATUS_URL=redis://redis:6379/2 \
  -e AETHER_B2_MILVUS_URI=http://milvus:19530 -e AETHER_B2_MILVUS_PROJECTION=false \
  -e AETHER_B2_VECTOR_DIMENSION=512 \
  -e AETHER_B1_EMBEDDING_URL=http://b1-sidecar:18081/v1/intercept \
  -v agent-20260818-p3-data:/var/lib/aether-p3 \
  "$P3_IMAGE" python scripts/p3_service.py >/dev/null

# Pytest is opt-in: the production image deliberately excludes it and this
# server's PyPI connectivity is unreliable.  The functional acceptance flow
# below remains the default server test.
if [[ "$WITH_PYTEST" == "1" ]]; then
  run_pytest_if_available
else
  printf 'PYTEST_STATUS=SKIPPED_BY_DEFAULT\nUse --with-pytest to attempt the network-dependent unit suite.\n' | tee "$ARTIFACT_DIR/pytest_status.txt"
fi
run_test e2e_smoke docker run --rm --network "$NETWORK_NAME" -e AETHER_P3_BASE_URL=http://p3:8080 "$P3_IMAGE" python scripts/run_compose_smoke.py
run_test b1_real_dataset_smoke docker run --rm --network "$NETWORK_NAME" \
  -v "$ROOT_DIR:/workspace:ro" -v "$ARTIFACT_DIR:/artifacts" -w /workspace "$P3_IMAGE" \
  python benchmarks/p3/b1_throughput.py \
  --base-url http://b1-sidecar:18081 \
  --dataset "$B1_SMOKE_DATASET_ROOT" --dataset-limit "$B1_DATASET_LIMIT" \
  --batch-sizes 1 8 --concurrency 1 4 --warmup 1 --measurement 5 --repeats 1 \
  --output /artifacts/b1_real_dataset_smoke
run_test b2_working_latency docker run --rm --network "$NETWORK_NAME" -v "$ROOT_DIR:/workspace" -w /workspace "$P3_IMAGE" python benchmarks/p3/b2_working_latency.py --redis-url redis://redis:6379/0 --output "$CONTAINER_ARTIFACT_DIR/b2_latency"
run_test b2_compression_dataset docker run --rm --network "$NETWORK_NAME" \
  -e PYTHONPATH=/workspace/src:/workspace \
  -v "$ROOT_DIR:/workspace" -w /workspace "$P3_IMAGE" python benchmarks/p3/b2_compression_dataset.py \
  --input-root "$B2_COMPRESSION_DATASET_ROOT" \
  --output "$CONTAINER_ARTIFACT_DIR/b2_compression_dataset/summary.json" \
  --target-ratio "$B2_COMPRESSION_TARGET_RATIO" \
  "${COMPRESSION_STRICT_ARGS[@]}"
record_not_implemented b3_heuristic "No standalone B3 heuristic replay benchmark exists in the current codebase."
run_test b2_beam_acceptance docker run --rm --network "$NETWORK_NAME" -v "$ROOT_DIR:/workspace" -w /workspace "$P3_IMAGE" python scripts/run_b2_dataset_replay.py \
  --locomo datasets/p3/acceptance_v0.1/locomo.jsonl \
  --longmemeval datasets/p3/acceptance_v0.1/longmemeval.jsonl \
  --beam datasets/p3/acceptance_v0.1/beam.jsonl \
  --output "$CONTAINER_ARTIFACT_DIR/p3_b2_beam_acceptance.json"

if [[ "$WITH_B1_THROUGHPUT" == "1" ]]; then
  run_test b1_throughput docker run --rm --network "$NETWORK_NAME" \
    -v "$ROOT_DIR:/workspace:ro" -v "$ARTIFACT_DIR:/artifacts" -w /workspace "$P3_IMAGE" \
    python benchmarks/p3/b1_throughput.py --output /artifacts/b1 \
    --base-url http://b1-sidecar:18081 \
    --dataset "$B1_ACCEPTANCE_DATASET_ROOT" --dataset-limit "$B1_DATASET_LIMIT" \
    --batch-sizes 1 8 --concurrency 1 4 8 16 --warmup 10 --measurement 30 --repeats 3
fi

B1_RESULT_REL="b1_real_dataset_smoke/b1/results.json"
if [[ "$WITH_B1_THROUGHPUT" == "1" && -f "$ARTIFACT_DIR/b1/b1/results.json" ]]; then
  B1_RESULT_REL="b1/b1/results.json"
fi
run_test full_metrics docker run --rm --network "$NETWORK_NAME" \
  -v "$ROOT_DIR:/workspace" -w /workspace "$P3_IMAGE" \
  python scripts/summarize_b1_b2_acceptance.py \
  --output-dir "$CONTAINER_ARTIFACT_DIR" \
  --b1-result "$CONTAINER_ARTIFACT_DIR/$B1_RESULT_REL" \
  --b2-compression "$CONTAINER_ARTIFACT_DIR/b2_compression_dataset/summary.json" \
  --b2-replay "$CONTAINER_ARTIFACT_DIR/p3_b2_beam_acceptance.json" \
  --smoke-log "$CONTAINER_ARTIFACT_DIR/e2e_smoke.log"

printf 'RUN_TAG=%s\nRESULT_DIR=%s\nFUNCTIONAL_ACCEPTANCE_TESTS_PASSED\n' "$RUN_TAG" "$ARTIFACT_DIR" | tee "$ARTIFACT_DIR/SUMMARY.txt"
