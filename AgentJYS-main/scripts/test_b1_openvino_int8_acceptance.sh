#!/usr/bin/env bash
# shellcheck shell=bash

# Reproduce the P3 B1 E03/E05/E06 OpenVINO INT8 acceptance method on Linux.
# All mutable Python, model, cache, temporary, and result data stays inside
# the repository. The existing Windows PowerShell runner remains independent.

set -Eeuo pipefail

PYTHON_VERSION="3.13"
MODEL_NAME="BAAI/bge-small-zh-v1.5"
MODEL_CACHE_ARCHIVE=""
ISOLATED_WORK_DIRECTORY="./runtime/b1/openvino-int8-acceptance-linux"
OUTPUT_DIRECTORY=""
START_PORT=18181
EXPECTED_DIMENSION=512
STARTUP_TIMEOUT_SECONDS=600
WARMUP_SECONDS=10
MEASUREMENT_SECONDS=30
REPEATS=3
SINGLE_CONCURRENCY=(1 4 8 16)
CLUSTER_CONCURRENCY=0
CLUSTER_BATCH_SIZES=(1 2 4 8 16 32 64)
SINGLE_REQUEST_MODE="single-item"
CLUSTER_REQUEST_MODE="single-item"
CONTRACT_TARGET_QPS=2000
SKIP_ENVIRONMENT_SETUP=false
SKIP_SINGLE_INSTANCE=true
SKIP_MULTI_INSTANCE=false
FAIL_ON_CONTRACT_TARGET=false

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"

WORK_ROOT=""
OUTPUT_ROOT=""
VENV_ROOT=""
PYTHON_PATH=""
MODEL_CACHE=""
TEMP_ROOT=""
UV_CACHE=""
UV_PYTHON_ROOT=""

CPU_IDS=()
ALLOWED_CPU_IDS=()
LOAD_CPU_IDS=()
SIDECAR_PIDS=()
SIDECAR_URLS=()
SIDECAR_LOG_DIRS=()
SAMPLER_PID=""
RUN_STATUS="FAILED"
RUN_ERROR=""
SUMMARY_ENABLED=false

usage() {
    cat <<'EOF'
Usage:
  bash ./scripts/test_b1_openvino_int8_acceptance.sh [options]

Options:
  --python-version VERSION
  --model-name NAME
  --model-cache-archive PATH     Import a validated portable OpenVINO IR cache
  --work-dir PATH
  --output-dir PATH
  --start-port PORT
  --expected-dimension N
  --startup-timeout SECONDS
  --warmup SECONDS
  --measurement SECONDS
  --repeats N
  --single-concurrency CSV       Default: 1,4,8,16
  --cluster-concurrency N        Default: detected physical cores x 4
  --cluster-batch-sizes CSV      Default: 1,2,4,8,16,32,64
  --single-request-mode MODE     batch or single-item. Default: single-item
  --cluster-request-mode MODE    batch or single-item. Default: single-item
  --contract-target-qps QPS
  --skip-environment-setup
  --skip-single-instance
  --skip-multi-instance
  --fail-on-contract-target
  -h, --help

Formal full run:
  bash ./scripts/test_b1_openvino_int8_acceptance.sh

Multi-instance only, reusing the isolated environment and model cache:
  bash ./scripts/test_b1_openvino_int8_acceptance.sh \
    --skip-environment-setup \
    --skip-single-instance

Short pipeline check (not a formal performance result):
  bash ./scripts/test_b1_openvino_int8_acceptance.sh \
    --warmup 1 --measurement 5 --repeats 1 \
    --single-concurrency 1,4
EOF
}

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

warn() {
    printf '[%s] WARNING: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
}

die() {
    printf '[%s] ERROR: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >&2
    return 1
}

require_value() {
    local option="$1"
    local count="$2"
    (( count >= 2 )) || die "$option requires a value."
}

parse_arguments() {
    while (( $# > 0 )); do
        case "$1" in
            --python-version)
                require_value "$1" "$#"
                PYTHON_VERSION="$2"
                shift 2
                ;;
            --model-name)
                require_value "$1" "$#"
                MODEL_NAME="$2"
                shift 2
                ;;
            --model-cache-archive)
                require_value "$1" "$#"
                MODEL_CACHE_ARCHIVE="$2"
                shift 2
                ;;
            --work-dir)
                require_value "$1" "$#"
                ISOLATED_WORK_DIRECTORY="$2"
                shift 2
                ;;
            --output-dir)
                require_value "$1" "$#"
                OUTPUT_DIRECTORY="$2"
                shift 2
                ;;
            --start-port)
                require_value "$1" "$#"
                START_PORT="$2"
                shift 2
                ;;
            --expected-dimension)
                require_value "$1" "$#"
                EXPECTED_DIMENSION="$2"
                shift 2
                ;;
            --startup-timeout)
                require_value "$1" "$#"
                STARTUP_TIMEOUT_SECONDS="$2"
                shift 2
                ;;
            --warmup)
                require_value "$1" "$#"
                WARMUP_SECONDS="$2"
                shift 2
                ;;
            --measurement)
                require_value "$1" "$#"
                MEASUREMENT_SECONDS="$2"
                shift 2
                ;;
            --repeats)
                require_value "$1" "$#"
                REPEATS="$2"
                shift 2
                ;;
            --single-concurrency)
                require_value "$1" "$#"
                IFS=',' read -r -a SINGLE_CONCURRENCY <<<"$2"
                shift 2
                ;;
            --cluster-concurrency)
                require_value "$1" "$#"
                CLUSTER_CONCURRENCY="$2"
                shift 2
                ;;
            --cluster-batch-sizes)
                require_value "$1" "$#"
                IFS=',' read -r -a CLUSTER_BATCH_SIZES <<<"$2"
                shift 2
                ;;
            --single-request-mode)
                require_value "$1" "$#"
                SINGLE_REQUEST_MODE="$2"
                shift 2
                ;;
            --cluster-request-mode)
                require_value "$1" "$#"
                CLUSTER_REQUEST_MODE="$2"
                shift 2
                ;;
            --contract-target-qps)
                require_value "$1" "$#"
                CONTRACT_TARGET_QPS="$2"
                shift 2
                ;;
            --skip-environment-setup)
                SKIP_ENVIRONMENT_SETUP=true
                shift
                ;;
            --skip-single-instance)
                SKIP_SINGLE_INSTANCE=true
                shift
                ;;
            --skip-multi-instance)
                SKIP_MULTI_INSTANCE=true
                shift
                ;;
            --fail-on-contract-target)
                FAIL_ON_CONTRACT_TARGET=true
                shift
                ;;
            -h|--help)
                usage
                exit 0
                ;;
            *)
                die "Unknown option: $1"
                ;;
        esac
    done
}

is_nonnegative_number() {
    awk -v value="$1" 'BEGIN { exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value + 0 >= 0) }'
}

is_positive_number() {
    awk -v value="$1" 'BEGIN { exit !(value ~ /^[0-9]+([.][0-9]+)?$/ && value + 0 > 0) }'
}

validate_parameters() {
    [[ "$START_PORT" =~ ^[0-9]+$ ]] && (( START_PORT >= 1 && START_PORT <= 65535 )) ||
        die "--start-port must be between 1 and 65535."
    [[ "$EXPECTED_DIMENSION" =~ ^[0-9]+$ ]] && (( EXPECTED_DIMENSION > 0 )) ||
        die "--expected-dimension must be positive."
    [[ "$STARTUP_TIMEOUT_SECONDS" =~ ^[0-9]+$ ]] && (( STARTUP_TIMEOUT_SECONDS > 0 )) ||
        die "--startup-timeout must be positive."
    is_nonnegative_number "$WARMUP_SECONDS" || die "--warmup must be non-negative."
    is_positive_number "$MEASUREMENT_SECONDS" || die "--measurement must be positive."
    [[ "$REPEATS" =~ ^[0-9]+$ ]] && (( REPEATS > 0 )) || die "--repeats must be positive."
    [[ "$CLUSTER_CONCURRENCY" =~ ^[0-9]+$ ]] || die "--cluster-concurrency must be zero or positive."
    is_positive_number "$CONTRACT_TARGET_QPS" || die "--contract-target-qps must be positive."
    [[ "$SINGLE_REQUEST_MODE" == "batch" || "$SINGLE_REQUEST_MODE" == "single-item" ]] ||
        die "--single-request-mode must be batch or single-item."
    [[ "$CLUSTER_REQUEST_MODE" == "batch" || "$CLUSTER_REQUEST_MODE" == "single-item" ]] ||
        die "--cluster-request-mode must be batch or single-item."
    (( ${#SINGLE_CONCURRENCY[@]} > 0 )) || die "At least one single concurrency value is required."
    (( ${#CLUSTER_BATCH_SIZES[@]} > 0 )) || die "At least one cluster batch size is required."
    local value
    for value in "${SINGLE_CONCURRENCY[@]}"; do
        [[ "$value" =~ ^[0-9]+$ ]] && (( value > 0 )) ||
            die "Single concurrency values must be positive integers."
    done
    for value in "${CLUSTER_BATCH_SIZES[@]}"; do
        [[ "$value" =~ ^[0-9]+$ ]] && (( value > 0 )) ||
            die "Cluster batch sizes must be positive integers."
    done
    if [[ "$SKIP_SINGLE_INSTANCE" == true && "$SKIP_MULTI_INSTANCE" == true ]]; then
        die "At least one acceptance phase must be enabled."
    fi
}

resolve_repo_path() {
    local raw_path="$1"
    local label="$2"
    local candidate
    if [[ "$raw_path" == /* ]]; then
        candidate="$(realpath -m -- "$raw_path")"
    else
        candidate="$(realpath -m -- "$REPO_ROOT/$raw_path")"
    fi
    if [[ "$candidate" != "$REPO_ROOT" && "$candidate" != "$REPO_ROOT/"* ]]; then
        die "$label must be inside the repository: $candidate"
        return 1
    fi
    printf '%s\n' "$candidate"
}

expand_cpu_list() {
    local specification="$1"
    local part start end cpu
    local parts=()
    IFS=',' read -r -a parts <<<"$specification"
    for part in "${parts[@]}"; do
        if [[ "$part" == *-* ]]; then
            start="${part%-*}"
            end="${part#*-}"
            for (( cpu=start; cpu<=end; cpu++ )); do
                printf '%s\n' "$cpu"
            done
        elif [[ "$part" =~ ^[0-9]+$ ]]; then
            printf '%s\n' "$part"
        fi
    done
}

detect_cpu_topology() {
    local allowed_spec=""
    if [[ -r /proc/self/status ]]; then
        allowed_spec="$(awk '/^Cpus_allowed_list:/ { print $2; exit }' /proc/self/status)"
    fi
    if [[ -n "$allowed_spec" ]]; then
        mapfile -t ALLOWED_CPU_IDS < <(expand_cpu_list "$allowed_spec")
    else
        local logical_count
        logical_count="$(nproc)"
        mapfile -t ALLOWED_CPU_IDS < <(seq 0 $(( logical_count - 1 )))
    fi
    (( ${#ALLOWED_CPU_IDS[@]} > 0 )) || die "No available Linux CPU was detected."

    declare -A allowed=()
    declare -A seen_cores=()
    local cpu core socket online key
    for cpu in "${ALLOWED_CPU_IDS[@]}"; do
        allowed["$cpu"]=1
    done

    CPU_IDS=()
    if command -v lscpu >/dev/null 2>&1; then
        while IFS=',' read -r cpu core socket online; do
            [[ "$cpu" =~ ^[0-9]+$ ]] || continue
            [[ -n "${allowed[$cpu]:-}" ]] || continue
            [[ -z "$online" || "${online^^}" == "Y" ]] || continue
            key="${socket}:${core}"
            if [[ -z "${seen_cores[$key]:-}" ]]; then
                seen_cores["$key"]=1
                CPU_IDS+=("$cpu")
            fi
        done < <(lscpu -p=CPU,CORE,SOCKET,ONLINE 2>/dev/null | awk '!/^#/')
    fi

    if (( ${#CPU_IDS[@]} == 0 )); then
        warn "lscpu physical-core topology was unavailable; using all allowed logical CPUs as replicas."
        CPU_IDS=("${ALLOWED_CPU_IDS[@]}")
    fi

    declare -A replica_cpu=()
    for cpu in "${CPU_IDS[@]}"; do
        replica_cpu["$cpu"]=1
    done
    LOAD_CPU_IDS=()
    for cpu in "${ALLOWED_CPU_IDS[@]}"; do
        if [[ -z "${replica_cpu[$cpu]:-}" ]]; then
            LOAD_CPU_IDS+=("$cpu")
        fi
    done
}

join_by() {
    local delimiter="$1"
    shift
    local first=true
    local value
    for value in "$@"; do
        if [[ "$first" == true ]]; then
            printf '%s' "$value"
            first=false
        else
            printf '%s%s' "$delimiter" "$value"
        fi
    done
}

configure_paths() {
    WORK_ROOT="$(resolve_repo_path "$ISOLATED_WORK_DIRECTORY" "--work-dir")"
    if [[ -n "$OUTPUT_DIRECTORY" ]]; then
        OUTPUT_ROOT="$(resolve_repo_path "$OUTPUT_DIRECTORY" "--output-dir")"
    else
        OUTPUT_ROOT="$REPO_ROOT/artifacts/b1/p3_b1_openvino_int8_linux_$(date '+%Y%m%d-%H%M%S')"
    fi
    VENV_ROOT="$WORK_ROOT/.venv"
    PYTHON_PATH="$VENV_ROOT/bin/python"
    MODEL_CACHE="$WORK_ROOT/models"
    TEMP_ROOT="$WORK_ROOT/temp"
    UV_CACHE="$WORK_ROOT/uv-cache"
    UV_PYTHON_ROOT="$WORK_ROOT/python"
    mkdir -p -- "$WORK_ROOT" "$MODEL_CACHE" "$TEMP_ROOT" "$UV_CACHE" "$UV_PYTHON_ROOT" "$OUTPUT_ROOT"
    SUMMARY_ENABLED=true
}

set_runtime_environment() {
    export UV_CACHE_DIR="$UV_CACHE"
    export UV_PYTHON_INSTALL_DIR="$UV_PYTHON_ROOT"
    export TMPDIR="$TEMP_ROOT"
    export TMP="$TEMP_ROOT"
    export TEMP="$TEMP_ROOT"
    export PYTHONUTF8=1
    export PYTHONIOENCODING="utf-8:replace"
    export PYTHONNOUSERSITE=1
    export HF_HOME="$WORK_ROOT/hf-home"
    export HF_HUB_CACHE="$MODEL_CACHE"
    export HUGGINGFACE_HUB_CACHE="$MODEL_CACHE"
    export HF_HUB_DISABLE_SYMLINKS_WARNING=1
    export HF_HUB_ETAG_TIMEOUT=60
    export HF_HUB_DOWNLOAD_TIMEOUT=600
    export TOKENIZERS_PARALLELISM=false
    export CUDA_VISIBLE_DEVICES=""
    if [[ -n "${NO_PROXY:-}" ]]; then
        export NO_PROXY="127.0.0.1,localhost,$NO_PROXY"
    else
        export NO_PROXY="127.0.0.1,localhost"
    fi
    if [[ -n "${no_proxy:-}" ]]; then
        export no_proxy="127.0.0.1,localhost,$no_proxy"
    else
        export no_proxy="127.0.0.1,localhost"
    fi

    export AETHER_B1_HOST="127.0.0.1"
    export AETHER_B1_MODEL_NAME="$MODEL_NAME"
    export AETHER_B1_MODEL="$MODEL_NAME"
    unset AETHER_B1_MODEL_PATH || true
    export AETHER_B1_CACHE_DIR="$MODEL_CACHE"
    export AETHER_B1_BACKEND="openvino"
    export AETHER_B1_PRECISION="int8"
    export AETHER_B1_FALLBACK_BACKENDS=""
    export AETHER_B1_ALLOW_BACKEND_FALLBACK=false
    export AETHER_B1_EAGER_LOAD=true
    export AETHER_B1_FAIL_MODE=closed
    export AETHER_B1_SIMD_ENABLED=true
    export AETHER_B1_PROFILE_TIMING=true
    export AETHER_B1_THREADS=1
    export AETHER_B1_INTER_OP_THREADS=1
    export AETHER_B1_MAX_CONCURRENCY=1
    export AETHER_B1_MODEL_BATCH_SIZE=8
    export AETHER_B1_DYNAMIC_BATCHING="${AETHER_B1_DYNAMIC_BATCHING:-true}"
    export AETHER_B1_DYNAMIC_MAX_BATCH_ITEMS="${AETHER_B1_DYNAMIC_MAX_BATCH_ITEMS:-64}"
    export AETHER_B1_DYNAMIC_MAX_BATCH_TOKENS="${AETHER_B1_DYNAMIC_MAX_BATCH_TOKENS:-8192}"
    export AETHER_B1_DYNAMIC_MAX_WAIT_MS="${AETHER_B1_DYNAMIC_MAX_WAIT_MS:-2}"
    export AETHER_B1_DYNAMIC_QUEUE_SIZE="${AETHER_B1_DYNAMIC_QUEUE_SIZE:-4096}"
    export AETHER_B1_LENGTH_BUCKETS="${AETHER_B1_LENGTH_BUCKETS:-32,64,128,256,512}"
    export AETHER_B1_OPENVINO_ASYNC="${AETHER_B1_OPENVINO_ASYNC:-true}"
    export AETHER_B1_OPENVINO_INFER_REQUESTS="${AETHER_B1_OPENVINO_INFER_REQUESTS:-0}"
    export AETHER_B1_OPENVINO_NUM_STREAMS="${AETHER_B1_OPENVINO_NUM_STREAMS:-AUTO}"
    export OMP_NUM_THREADS=1
    export OPENBLAS_NUM_THREADS=1
    export MKL_NUM_THREADS=1
    export NUMEXPR_NUM_THREADS=1
    export BLIS_NUM_THREADS=1
}

enable_offline_model_cache() {
    local cache_root="$MODEL_CACHE/_aether_openvino"
    local marker=""
    if [[ -d "$cache_root" ]]; then
        marker="$(find "$cache_root" -type f -name '.aether-export-complete' -print -quit 2>/dev/null || true)"
    fi
    if [[ -n "$marker" ]]; then
        # Optimum Intel 2.1 treats an absolute local model path as a Hub model
        # ID when Transformers offline mode is enabled. A validated local IR
        # needs no network access, but these flags must remain unset so Optimum
        # recognizes the directory before resolving Hub cache refs.
        unset HF_HUB_OFFLINE || true
        unset TRANSFORMERS_OFFLINE || true
        log "Using completed local OpenVINO cache: $(dirname -- "$marker")"
    fi
}

import_model_cache_archive() {
    [[ -n "$MODEL_CACHE_ARCHIVE" ]] || return 0
    command -v tar >/dev/null 2>&1 || die "tar is required to import --model-cache-archive."
    local archive
    archive="$(resolve_repo_path "$MODEL_CACHE_ARCHIVE" "--model-cache-archive")"
    [[ -f "$archive" ]] || die "Model cache archive does not exist: $archive"

    local entries=()
    mapfile -t entries < <(tar -tzf "$archive")
    (( ${#entries[@]} > 0 )) || die "Model cache archive is empty: $archive"
    local entry normalized
    for entry in "${entries[@]}"; do
        normalized="${entry#./}"
        if [[ "$normalized" == /* || "/$normalized/" == *"/../"* ]]; then
            die "Unsafe path in model cache archive: $entry"
        fi
        case "$normalized" in
            _aether_openvino|_aether_openvino/*) ;;
            *) die "Unexpected path in model cache archive: $entry" ;;
        esac
    done

    log "Refreshing the portable OpenVINO INT8 cache from $archive"
    tar -xzf "$archive" --no-same-owner --no-same-permissions -C "$MODEL_CACHE"
    local marker xml bin
    marker="$(find "$MODEL_CACHE/_aether_openvino" -type f -name '.aether-export-complete' -print -quit 2>/dev/null || true)"
    xml="$(find "$MODEL_CACHE/_aether_openvino" -type f -name '*.xml' -print -quit 2>/dev/null || true)"
    bin="$(find "$MODEL_CACHE/_aether_openvino" -type f -name '*.bin' -print -quit 2>/dev/null || true)"
    [[ -n "$marker" && -n "$xml" && -n "$bin" ]] ||
        die "Imported model cache is incomplete; marker, XML, or BIN is missing."
}

initialize_isolated_environment() {
    command -v uv >/dev/null 2>&1 ||
        die "uv is required. Install uv for this user, reopen the shell, and run the script again."
    log "Installing isolated Python $PYTHON_VERSION under $UV_PYTHON_ROOT"
    uv python install "$PYTHON_VERSION"
    if [[ ! -x "$PYTHON_PATH" ]]; then
        log "Creating isolated virtual environment at $VENV_ROOT"
        uv venv --python "$PYTHON_VERSION" "$VENV_ROOT"
    fi
    log "Installing the project and OpenVINO INT8 dependencies into the isolated environment"
    (
        cd -- "$REPO_ROOT"
        uv pip install --python "$PYTHON_PATH" -e '.[b1-accelerated]'
    )
}

json_python() {
    if [[ -x "$PYTHON_PATH" ]]; then
        printf '%s\n' "$PYTHON_PATH"
    elif command -v python3 >/dev/null 2>&1; then
        command -v python3
    else
        return 1
    fi
}

write_environment_json() {
    local single_concurrency_csv cluster_batch_sizes_csv
    local cpu_ids_csv allowed_cpu_ids_csv load_cpu_ids_csv python_version
    single_concurrency_csv="$(join_by , "${SINGLE_CONCURRENCY[@]}")"
    cluster_batch_sizes_csv="$(join_by , "${CLUSTER_BATCH_SIZES[@]}")"
    cpu_ids_csv="$(join_by , "${CPU_IDS[@]}")"
    allowed_cpu_ids_csv="$(join_by , "${ALLOWED_CPU_IDS[@]}")"
    load_cpu_ids_csv="$(join_by , "${LOAD_CPU_IDS[@]}")"
    python_version="$($PYTHON_PATH --version 2>&1)"
    ENV_OUTPUT_PATH="$OUTPUT_ROOT/environment.json" \
    ENV_REPO_ROOT="$REPO_ROOT" \
    ENV_PYTHON_PATH="$PYTHON_PATH" \
    ENV_PYTHON_VERSION="$python_version" \
    ENV_MODEL_NAME="$MODEL_NAME" \
    ENV_MODEL_CACHE="$MODEL_CACHE" \
    ENV_PHYSICAL_COUNT="${#CPU_IDS[@]}" \
    ENV_LOGICAL_COUNT="${#ALLOWED_CPU_IDS[@]}" \
    ENV_CPU_IDS="$cpu_ids_csv" \
    ENV_ALLOWED_CPU_IDS="$allowed_cpu_ids_csv" \
    ENV_LOAD_CPU_IDS="$load_cpu_ids_csv" \
    ENV_SINGLE_CONCURRENCY="$single_concurrency_csv" \
    ENV_CLUSTER_CONCURRENCY="$RESOLVED_CLUSTER_CONCURRENCY" \
    ENV_CLUSTER_BATCH_SIZES="$cluster_batch_sizes_csv" \
    ENV_SINGLE_REQUEST_MODE="$SINGLE_REQUEST_MODE" \
    ENV_CLUSTER_REQUEST_MODE="$CLUSTER_REQUEST_MODE" \
    ENV_WARMUP="$WARMUP_SECONDS" \
    ENV_MEASUREMENT="$MEASUREMENT_SECONDS" \
    ENV_REPEATS="$REPEATS" \
    "$PYTHON_PATH" - <<'PY'
import json
import os
from datetime import UTC, datetime
from pathlib import Path

def int_list(name: str) -> list[int]:
    value = os.environ.get(name, "")
    return [int(item) for item in value.split(",") if item]

payload = {
    "generated_at": datetime.now(UTC).isoformat(),
    "platform": "linux",
    "repository": os.environ["ENV_REPO_ROOT"],
    "python_executable": os.environ["ENV_PYTHON_PATH"],
    "python_version": os.environ["ENV_PYTHON_VERSION"],
    "model": os.environ["ENV_MODEL_NAME"],
    "model_cache": os.environ["ENV_MODEL_CACHE"],
    "backend": "openvino",
    "precision": "INT8",
    "fallback_enabled": False,
    "physical_cpu_count": int(os.environ["ENV_PHYSICAL_COUNT"]),
    "logical_cpu_count": int(os.environ["ENV_LOGICAL_COUNT"]),
    "replica_cpu_ids": int_list("ENV_CPU_IDS"),
    "allowed_cpu_ids": int_list("ENV_ALLOWED_CPU_IDS"),
    "load_generator_cpu_ids": int_list("ENV_LOAD_CPU_IDS"),
    "single": {
        "instances": 1,
        "threads_per_instance": 1,
        "max_concurrency_per_instance": 1,
        "batch_sizes": [1, 8],
        "request_mode": os.environ["ENV_SINGLE_REQUEST_MODE"],
        "http_concurrency": int_list("ENV_SINGLE_CONCURRENCY"),
        "warmup_seconds": float(os.environ["ENV_WARMUP"]),
        "measurement_seconds": float(os.environ["ENV_MEASUREMENT"]),
        "repeats": int(os.environ["ENV_REPEATS"]),
    },
    "multi": {
        "instances": int(os.environ["ENV_PHYSICAL_COUNT"]),
        "threads_per_instance": 1,
        "max_concurrency_per_instance": 1,
        "batch_sizes": int_list("ENV_CLUSTER_BATCH_SIZES"),
        "request_mode": os.environ["ENV_CLUSTER_REQUEST_MODE"],
        "http_concurrency": int(os.environ["ENV_CLUSTER_CONCURRENCY"]),
        "warmup_seconds": float(os.environ["ENV_WARMUP"]),
        "measurement_seconds": float(os.environ["ENV_MEASUREMENT"]),
        "repeats": int(os.environ["ENV_REPEATS"]),
    },
}
path = Path(os.environ["ENV_OUTPUT_PATH"])
path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

write_summary_json() {
    [[ "$SUMMARY_ENABLED" == true ]] || return 0
    local interpreter
    interpreter="$(json_python)" || {
        warn "No Python interpreter is available to write acceptance_summary.json."
        return 0
    }
    SUMMARY_OUTPUT_PATH="$OUTPUT_ROOT/acceptance_summary.json" \
    SUMMARY_STATUS="$RUN_STATUS" \
    SUMMARY_ERROR="$RUN_ERROR" \
    SUMMARY_MODEL="$MODEL_NAME" \
    SUMMARY_WORK_ROOT="$WORK_ROOT" \
    SUMMARY_OUTPUT_ROOT="$OUTPUT_ROOT" \
    SUMMARY_SINGLE_RESULT="$OUTPUT_ROOT/single/b1/results.json" \
    SUMMARY_MULTI_RESULT="$OUTPUT_ROOT/multi/b1_max_hardware/results.json" \
    "$interpreter" - <<'PY'
import json
import os
from datetime import UTC, datetime
from pathlib import Path

def read_result(name: str):
    path = Path(os.environ[name])
    if not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return value if isinstance(value, dict) else None

payload = {
    "generated_at": datetime.now(UTC).isoformat(),
    "reference": "artifacts/p3_acceptance_env_v0.1/P3_EXPERIMENT_HANDOFF_REPORT.md E03/E05/E06",
    "platform": "linux",
    "backend": "openvino",
    "precision": "INT8",
    "model": os.environ["SUMMARY_MODEL"],
    "isolated_work_directory": os.environ["SUMMARY_WORK_ROOT"],
    "output_directory": os.environ["SUMMARY_OUTPUT_ROOT"],
    "single": read_result("SUMMARY_SINGLE_RESULT"),
    "multi": read_result("SUMMARY_MULTI_RESULT"),
    "status": os.environ["SUMMARY_STATUS"],
    "completed_at": datetime.now(UTC).isoformat(),
}
error = os.environ.get("SUMMARY_ERROR", "")
if error:
    payload["error"] = error
Path(os.environ["SUMMARY_OUTPUT_PATH"]).write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
PY
}

stop_process_tree() {
    local pid="$1"
    [[ "$pid" =~ ^[0-9]+$ ]] || return 0
    kill -0 "$pid" 2>/dev/null || return 0
    local children=()
    if command -v pgrep >/dev/null 2>&1; then
        mapfile -t children < <(pgrep -P "$pid" 2>/dev/null || true)
    fi
    local child
    for child in "${children[@]}"; do
        stop_process_tree "$child"
    done
    kill -TERM "$pid" 2>/dev/null || true
    local attempt
    for (( attempt=0; attempt<20; attempt++ )); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
    done
    kill -KILL "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
}

stop_cpu_sampler() {
    if [[ -n "$SAMPLER_PID" ]]; then
        kill -TERM "$SAMPLER_PID" 2>/dev/null || true
        wait "$SAMPLER_PID" 2>/dev/null || true
        SAMPLER_PID=""
    fi
}

cleanup_processes() {
    stop_cpu_sampler
    local index
    for (( index=${#SIDECAR_PIDS[@]}-1; index>=0; index-- )); do
        stop_process_tree "${SIDECAR_PIDS[$index]}"
    done
}

cleanup_stale_acceptance_processes() {
    command -v pgrep >/dev/null 2>&1 || return 0
    local candidates=()
    mapfile -t candidates < <(
        pgrep -f 'aether_agent_memory[.]b1[.]sidecar|benchmarks/p3/b1_.*throughput[.]py' \
            2>/dev/null || true
    )
    local pid command_line
    for pid in "${candidates[@]}"; do
        [[ "$pid" =~ ^[0-9]+$ ]] || continue
        (( pid != $$ && pid != BASHPID )) || continue
        [[ -r "/proc/$pid/cmdline" ]] || continue
        command_line="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
        if [[ "$command_line" == *"$WORK_ROOT/"* ]] ||
            [[ "$command_line" == *"$REPO_ROOT/benchmarks/p3/"* ]]; then
            log "Stopping stale acceptance process $pid: $command_line"
            stop_process_tree "$pid"
        fi
    done
}

port_is_available() {
    local port="$1"
    "$PYTHON_PATH" - "$port" <<'PY'
import socket
import sys

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError:
    raise SystemExit(1)
finally:
    sock.close()
PY
}

assert_required_ports_available() {
    local ports=("$START_PORT")
    local index
    if [[ "$SKIP_MULTI_INSTANCE" == false ]]; then
        for (( index=0; index<${#CPU_IDS[@]}; index++ )); do
            ports+=("$(( START_PORT + 1 + index ))")
        done
    fi
    local busy_ports=()
    local port attempt
    for (( attempt=0; attempt<=20; attempt++ )); do
        busy_ports=()
        for port in "${ports[@]}"; do
            if ! port_is_available "$port"; then
                busy_ports+=("$port")
            fi
        done
        (( ${#busy_ports[@]} == 0 )) && return 0
        (( attempt == 20 )) || sleep 0.5
    done
    if (( ${#busy_ports[@]} > 0 )); then
        RUN_ERROR="Required localhost port(s) are already in use: $(join_by , "${busy_ports[@]}")"
        warn "$RUN_ERROR"
        if command -v ss >/dev/null 2>&1; then
            ss -ltnp 2>/dev/null | grep -E ":($(join_by '|' "${busy_ports[@]}"))([[:space:]]|$)" >&2 || true
        fi
        return 1
    fi
}

on_error() {
    local code="$?"
    local line="$1"
    local command="$2"
    if [[ -z "$RUN_ERROR" ]]; then
        RUN_ERROR="line $line failed with exit code $code: $command"
    fi
    return "$code"
}

on_exit() {
    local code="$?"
    trap - ERR EXIT INT TERM
    cleanup_processes
    if (( code != 0 )) && [[ -z "$RUN_ERROR" ]]; then
        RUN_ERROR="Linux acceptance script exited with code $code."
    fi
    write_summary_json || warn "Failed to write acceptance_summary.json."
    if [[ "$RUN_STATUS" == "COMPLETED" ]]; then
        log "OpenVINO INT8 Linux acceptance completed: $OUTPUT_ROOT"
    else
        warn "OpenVINO INT8 Linux acceptance failed: $RUN_ERROR"
    fi
    exit "$code"
}

start_sidecar() {
    local name="$1"
    local port="$2"
    local log_directory="$3"
    local cpu_id="${4:--1}"
    mkdir -p -- "$log_directory"
    (
        export AETHER_B1_PORT="$port"
        if (( cpu_id >= 0 )) && command -v taskset >/dev/null 2>&1; then
            exec taskset -c "$cpu_id" "$PYTHON_PATH" -m aether_agent_memory.b1.sidecar
        fi
        exec "$PYTHON_PATH" -m aether_agent_memory.b1.sidecar
    ) >"$log_directory/sidecar.stdout.log" 2>"$log_directory/sidecar.stderr.log" &
    local pid="$!"
    SIDECAR_PIDS+=("$pid")
    SIDECAR_URLS+=("http://127.0.0.1:$port")
    SIDECAR_LOG_DIRS+=("$log_directory")
    LAST_SIDECAR_PID="$pid"
    LAST_SIDECAR_URL="http://127.0.0.1:$port"
    if (( cpu_id >= 0 )); then
        if command -v taskset >/dev/null 2>&1; then
            log "Started $name on port $port, PID $pid, CPU $cpu_id"
        else
            warn "taskset is unavailable; $name remains enabled but Linux will schedule its CPU affinity."
        fi
    else
        log "Started $name on port $port, PID $pid"
    fi
}

wait_sidecar_ready() {
    local url="$1"
    local pid="$2"
    local log_directory="$3"
    local started_at="$SECONDS"
    local deadline=$(( SECONDS + STARTUP_TIMEOUT_SECONDS ))
    local next_progress=$(( SECONDS + 60 ))
    local response_path="$log_directory/readiness.last.json"
    local response_temp="$response_path.tmp"
    local curl_log="$log_directory/readiness.curl.log"
    local http_code="000"
    local health_status=""
    local load_error=""

    probe_health() {
        : >"$response_temp"
        if http_code="$(
            curl --noproxy '*' -sS --connect-timeout 2 --max-time 5 \
                -o "$response_temp" -w '%{http_code}' "$url/health/ready" \
                2>"$curl_log"
        )"; then
            :
        else
            http_code="${http_code:-000}"
        fi
        if [[ -s "$response_temp" ]]; then
            mv -f -- "$response_temp" "$response_path"
        fi
        health_status=""
        load_error=""
        if [[ -s "$response_path" ]]; then
            local fields=()
            mapfile -t fields < <(
                "$PYTHON_PATH" - "$response_path" <<'PY'
import json
import sys
from pathlib import Path

try:
    value = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except Exception:
    value = {}
print(str(value.get("status") or ""))
print(str(value.get("load_error") or "").replace("\r", " ").replace("\n", " "))
PY
            )
            health_status="${fields[0]:-}"
            load_error="${fields[1]:-}"
        fi
    }

    while (( SECONDS < deadline )); do
        if ! kill -0 "$pid" 2>/dev/null; then
            RUN_ERROR="Sidecar $url exited before it became ready."
            warn "Sidecar $url exited before it became ready."
            tail -n 80 "$log_directory/sidecar.stderr.log" >&2 || true
            return 1
        fi
        probe_health
        if [[ "$http_code" == "200" && "$health_status" == "ready" ]]; then
            return 0
        fi
        if [[ -n "$load_error" ]]; then
            RUN_ERROR="Sidecar $url backend load failed: $load_error"
            warn "$RUN_ERROR"
            warn "Full readiness response: $response_path"
            tail -n 80 "$log_directory/sidecar.stderr.log" >&2 || true
            return 1
        fi
        if (( SECONDS >= next_progress )); then
            log "Sidecar $url is still loading after $(( SECONDS - started_at )) seconds (HTTP $http_code, status=${health_status:-unavailable})"
            next_progress=$(( SECONDS + 60 ))
        fi
        sleep 2
    done

    # Model export can complete exactly at the configured deadline. Give the
    # readiness endpoint a short final grace period before declaring failure.
    local grace_deadline=$(( SECONDS + 10 ))
    while (( SECONDS <= grace_deadline )); do
        probe_health
        if [[ "$http_code" == "200" && "$health_status" == "ready" ]]; then
            return 0
        fi
        if [[ -n "$load_error" ]]; then
            RUN_ERROR="Sidecar $url backend load failed: $load_error"
            warn "$RUN_ERROR"
            return 1
        fi
        sleep 1
    done
    RUN_ERROR="Sidecar $url was not ready within $STARTUP_TIMEOUT_SECONDS seconds (HTTP $http_code, status=${health_status:-unavailable})."
    warn "Sidecar $url was not ready within $STARTUP_TIMEOUT_SECONDS seconds."
    [[ ! -s "$response_path" ]] || warn "Last readiness response: $response_path"
    tail -n 80 "$log_directory/sidecar.stderr.log" >&2 || true
    return 1
}

assert_openvino_int8_health() {
    local url="$1"
    curl --noproxy '*' -fsS --connect-timeout 2 --max-time 30 "$url/health/ready" |
        "$PYTHON_PATH" -c '
import json, sys
value = json.load(sys.stdin)
errors = []
if value.get("status") != "ready": errors.append("status is not ready")
if str(value.get("backend", "")).lower() != "openvino": errors.append("backend is not openvino")
if str(value.get("precision", "")).upper() != "INT8": errors.append("precision is not INT8")
if value.get("fallback_used"): errors.append("backend fallback was used")
if int(value.get("threads", 0)) != 1: errors.append("inference threads is not 1")
if errors:
    print("; ".join(errors), file=sys.stderr)
    raise SystemExit(1)
'
}

invoke_preflight() {
    local url="$1"
    local destination="$2"
    local label="$3"
    mkdir -p -- "$destination"
    PREFLIGHT_URL="$url" \
    PREFLIGHT_DESTINATION="$destination" \
    PREFLIGHT_LABEL="$label" \
    PREFLIGHT_EXPECTED_DIMENSION="$EXPECTED_DIMENSION" \
    PREFLIGHT_MODEL_CACHE="$MODEL_CACHE" \
    "$PYTHON_PATH" - <<'PY'
import base64
import json
import math
import os
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

base_url = os.environ["PREFLIGHT_URL"].rstrip("/")
destination = Path(os.environ["PREFLIGHT_DESTINATION"])
label = os.environ["PREFLIGHT_LABEL"]
expected_dimension = int(os.environ["PREFLIGHT_EXPECTED_DIMENSION"])
text = base64.b64decode(
    "UDPor63kuYnlsYLmraPlnKjpqozor4HnnJ/lrp7kuK3mlofmlofmnKznmoTlkJHph4/ljJbjgIHorrDlv4bliIbnsbvkuI7lvILmraXosIPluqbpk77ot6/jgII="
).decode("utf-8")

def request(path: str, method: str = "GET", body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    value = urllib.request.Request(base_url + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(value, timeout=120) as response:
        return response.status, json.loads(response.read().decode("utf-8"))

_, health = request("/health/ready")
request_id = f"openvino-int8-{label}-{uuid.uuid4().hex}"
body = {
    "request_id": request_id,
    "trace_id": f"trace-{request_id}",
    "tenant_id": "p3-baseline",
    "source_type": "benchmark",
    "source_id": f"openvino-int8-{label}",
    "chunk_id": "preflight",
    "chunk_text": text,
    "embedding_required": True,
    "input_type": "passage",
    "metadata": {"baseline": "adapted-v0.1-linux"},
}
http_status, response = request("/v1/intercept", "POST", body)
result = response["results"][0]
vector = result.get("vector") or []
norm = math.sqrt(math.fsum(float(item) * float(item) for item in vector))
_, capabilities_response = request("/v1/capabilities")
capabilities = capabilities_response["current_backend"]
int8_constants = int(capabilities.get("int8_weight_constants") or 0)

checks = {
    "result_success": result.get("status") == "success",
    "dimension": len(vector) == expected_dimension,
    "normalization": abs(norm - 1.0) < 0.0001,
    "health_backend": str(health.get("backend", "")).lower() == "openvino",
    "health_precision": str(health.get("precision", "")).upper() == "INT8",
    "no_fallback": not bool(health.get("fallback_used")),
    "one_thread": int(health.get("threads") or 0) == 1,
    "result_backend": str(result.get("backend", "")).lower() == "openvino",
    "result_precision": str(result.get("quantization_type", "")).upper() == "INT8",
    "quantization_mode": result.get("quantization_mode") == "openvino-nncf-int8-weight-only",
    "quantization_verified": bool(capabilities.get("quantization_verified")),
    "int8_constants": int8_constants > 0,
}
passed = all(checks.values())
payload = {
    "generated_at": datetime.now(UTC).isoformat(),
    "status": "PASS" if passed else "FAIL",
    "checks": checks,
    "validation_text": text,
    "validation_text_utf8_bytes": len(text.encode("utf-8")),
    "http_status": http_status,
    "ready": health,
    "capabilities": capabilities,
    "request_id": request_id,
    "embedding_model": result.get("embedding_model"),
    "dimension": len(vector),
    "vector_norm": norm,
    "vector_sample": vector[:8],
    "actual_backend": result.get("backend"),
    "precision": result.get("quantization_type"),
    "quantization_mode": result.get("quantization_mode"),
    "int8_weight_constants": int8_constants,
    "model_hash": result.get("model_hash"),
    "model_cache": os.environ["PREFLIGHT_MODEL_CACHE"],
    "fallback_disabled": True,
}
(destination / "b1_preflight.json").write_text(
    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
)
if not passed:
    failed = ", ".join(name for name, value in checks.items() if not value)
    raise SystemExit(f"Preflight failed: {failed}")
PY
}

start_cpu_sampler() {
    local pid="$1"
    local output_path="$2"
    local log_path="$3"
    mkdir -p -- "$(dirname -- "$output_path")"
    (
        printf 'timestamp_utc,process_id,cpu_percent,working_set_bytes\n' >"$output_path"
        while kill -0 "$pid" 2>/dev/null; do
            local_values="$(ps -p "$pid" -o %cpu=,rss= 2>/dev/null | awk 'NF >= 2 { print $1 "," $2 * 1024 }' || true)"
            if [[ -n "$local_values" ]]; then
                printf '%s,%s,%s\n' "$(date -u '+%Y-%m-%dT%H:%M:%S.%3NZ')" "$pid" "$local_values" >>"$output_path"
            fi
            sleep 1
        done
    ) >"$log_path" 2>&1 &
    SAMPLER_PID="$!"
}

check_result() {
    local path="$1"
    local label="$2"
    RESULT_PATH="$path" \
    RESULT_LABEL="$label" \
    RESULT_FAIL_ON_CONTRACT="$FAIL_ON_CONTRACT_TARGET" \
    "$PYTHON_PATH" - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["RESULT_PATH"])
if not path.is_file():
    raise SystemExit(f"{os.environ['RESULT_LABEL']} did not create {path}")
value = json.loads(path.read_text(encoding="utf-8"))
if value.get("status") != "COMPLETED":
    raise SystemExit(f"{os.environ['RESULT_LABEL']} was not completed: {value.get('reason')}")
if os.environ["RESULT_FAIL_ON_CONTRACT"] == "true":
    if (value.get("contract") or {}).get("status") != "PASS":
        raise SystemExit(f"{os.environ['RESULT_LABEL']} contract target was not reached")
PY
}

run_single_instance() {
    local destination="$OUTPUT_ROOT/single"
    start_sidecar "single-instance" "$START_PORT" "$destination" -1
    local pid="$LAST_SIDECAR_PID"
    local url="$LAST_SIDECAR_URL"
    wait_sidecar_ready "$url" "$pid" "$destination"
    assert_openvino_int8_health "$url"
    enable_offline_model_cache
    invoke_preflight "$url" "$destination" "single"

    start_cpu_sampler "$pid" "$destination/b1/cpu_samples.csv" "$destination/b1_cpu_sampler.log"
    local command=(
        "$PYTHON_PATH" "$REPO_ROOT/benchmarks/p3/b1_throughput.py"
        --output "$destination"
        --base-url "$url"
        --batch-sizes 1 8
        --request-mode "$SINGLE_REQUEST_MODE"
        --concurrency "${SINGLE_CONCURRENCY[@]}"
        --warmup "$WARMUP_SECONDS"
        --measurement "$MEASUREMENT_SECONDS"
        --repeats "$REPEATS"
        --text-class medium
        --expected-dimension "$EXPECTED_DIMENSION"
    )
    local exit_code=0
    log "Running the single-instance HTTP benchmark"
    if "${command[@]}" 2>&1 | tee "$destination/b1_benchmark.log"; then
        exit_code=0
    else
        local pipeline_status=("${PIPESTATUS[@]}")
        exit_code="${pipeline_status[0]}"
    fi
    printf '%s\n' "$exit_code" >"$destination/b1_benchmark.exit_code"
    stop_cpu_sampler
    (( exit_code == 0 )) || die "Single-instance benchmark exited with $exit_code."
    check_result "$destination/b1/results.json" "Single-instance benchmark"
    stop_process_tree "$pid"
}

prepare_model_for_multi() {
    local destination="$OUTPUT_ROOT/multi/model_preparation"
    log "Validating the OpenVINO INT8 model cache before starting CPU-bound replicas"
    start_sidecar "multi-model-preparation" "$START_PORT" "$destination" -1
    local pid="$LAST_SIDECAR_PID"
    local url="$LAST_SIDECAR_URL"
    wait_sidecar_ready "$url" "$pid" "$destination"
    assert_openvino_int8_health "$url"
    enable_offline_model_cache
    stop_process_tree "$pid"
}

write_multi_summary() {
    local cluster_directory="$1"
    SUMMARY_CLUSTER_DIRECTORY="$cluster_directory" \
    SUMMARY_BATCH_SIZES="$(join_by , "${CLUSTER_BATCH_SIZES[@]}")" \
    SUMMARY_CONCURRENCY="$RESOLVED_CLUSTER_CONCURRENCY" \
    SUMMARY_TARGET_QPS="$CONTRACT_TARGET_QPS" \
    "$PYTHON_PATH" - <<'PY'
import csv
import html
import json
import math
import os
from collections import defaultdict
from pathlib import Path

root = Path(os.environ["SUMMARY_CLUSTER_DIRECTORY"])
batch_sizes = [int(value) for value in os.environ["SUMMARY_BATCH_SIZES"].split(",") if value]
concurrency = int(os.environ["SUMMARY_CONCURRENCY"])
target_qps = float(os.environ["SUMMARY_TARGET_QPS"])
payloads = []
rows = []
for batch_size in batch_sizes:
    path = root / f"batch_{batch_size:02d}" / "results.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    payloads.append(value)
    for row in value.get("results", []):
        item = dict(row)
        item["batch_size"] = batch_size
        item["concurrency"] = concurrency
        rows.append(item)

def values(group, key):
    return [float(row[key]) for row in group if row.get(key) is not None]

groups = defaultdict(list)
for row in rows:
    groups[int(row["batch_size"])].append(row)

summary = []
for batch_size in sorted(groups):
    group = groups[batch_size]
    def mean(key):
        items = values(group, key)
        return sum(items) / len(items) if items else None
    def minimum(key):
        items = values(group, key)
        return min(items) if items else None
    def maximum(key):
        items = values(group, key)
        return max(items) if items else None
    summary.append({
        "batch_size": batch_size,
        "concurrency": concurrency,
        "repeat_count": len(group),
        "request_qps_mean": mean("request_qps"),
        "item_per_second_mean": mean("item_per_second"),
        "effective_item_qps_mean": mean("effective_item_qps"),
        "vector_qps_mean": mean("vector_qps"),
        "server_batch_items_avg_mean": mean("server_batch_items_avg"),
        "server_batch_items_p95_max": maximum("server_batch_items_p95"),
        "server_batch_tokens_avg_mean": mean("server_batch_tokens_avg"),
        "server_queue_wait_p95_ms_max": maximum("server_queue_wait_p95_ms"),
        "server_backend_inference_p95_ms_max": maximum("server_backend_inference_p95_ms"),
        "p50_ms_mean": mean("p50_ms"),
        "p95_ms_mean": mean("p95_ms"),
        "p99_ms_mean": mean("p99_ms"),
        "p50_ms_min": minimum("p50_ms"),
        "p50_ms_max": maximum("p50_ms"),
        "p95_ms_min": minimum("p95_ms"),
        "p95_ms_max": maximum("p95_ms"),
        "p99_ms_min": minimum("p99_ms"),
        "p99_ms_max": maximum("p99_ms"),
        "error_rate_mean": mean("error_rate"),
        "error_rate_max": maximum("error_rate"),
    })

result_fields = [
    "instance_count", "request_mode", "batch_size", "request_items", "concurrency", "repeat", "duration_s",
    "successful_requests", "failed_requests", "request_qps", "successful_items",
    "item_per_second", "effective_item_qps", "successful_vectors", "vector_qps",
    "server_batch_items_avg", "server_batch_items_p95", "server_batch_tokens_avg",
    "server_queue_wait_p95_ms", "server_backend_inference_p95_ms",
    "p50_ms", "p95_ms", "p99_ms", "error_rate",
]
summary_fields = [
    "batch_size", "concurrency", "repeat_count", "request_qps_mean",
    "item_per_second_mean", "effective_item_qps_mean", "vector_qps_mean",
    "server_batch_items_avg_mean", "server_batch_items_p95_max",
    "server_batch_tokens_avg_mean", "server_queue_wait_p95_ms_max",
    "server_backend_inference_p95_ms_max", "p50_ms_mean", "p95_ms_mean", "p99_ms_mean",
    "p50_ms_min", "p50_ms_max", "p95_ms_min", "p95_ms_max",
    "p99_ms_min", "p99_ms_max", "error_rate_mean", "error_rate_max",
]
with (root / "results.csv").open("w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.DictWriter(handle, fieldnames=result_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
with (root / "latency_summary.csv").open("w", newline="", encoding="utf-8-sig") as handle:
    writer = csv.DictWriter(handle, fieldnames=summary_fields, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(summary)

width, height = 1400, 720
left, right, top, bottom = 80, 35, 70, 65
plot_width = width - left - right
plot_height = height - top - bottom
metrics = [("p50_ms_mean", "P50 latency (ms)", "#2563eb"),
           ("p95_ms_mean", "P95 latency (ms)", "#dc2626"),
           ("p99_ms_mean", "P99 latency (ms)", "#059669")]
def x_for(batch):
    if len(batch_sizes) <= 1:
        return left + plot_width / 2
    return left + (batch - batch_sizes[0]) * plot_width / (batch_sizes[-1] - batch_sizes[0])
def text(x, y, value, size=12, anchor="start"):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial,sans-serif" '
            f'font-size="{size}px" text-anchor="{anchor}" fill="#1f2937">'
            f'{html.escape(str(value))}</text>')

lines = [
    f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
    '<rect width="100%" height="100%" fill="white"/>',
    text(width / 2, 30, "OpenVINO INT8 multi-instance latency by batch size", 20, "middle"),
    text(width / 2, 52, f"HTTP concurrency={concurrency}; mean across repeats", 13, "middle"),
    f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="#f8fafc" stroke="#cbd5e1"/>',
]
all_values = [float(row[metric]) for row in summary for metric, _, _ in metrics
              if row.get(metric) is not None and math.isfinite(float(row[metric]))]
max_value = max(all_values, default=1.0) * 1.1
max_value = max(max_value, 1.0)
for tick in range(5):
    value = max_value * tick / 4
    y = top + plot_height - value / max_value * plot_height
    lines.append(f'<line x1="{left}" y1="{y:.1f}" x2="{left + plot_width}" y2="{y:.1f}" stroke="#e2e8f0"/>')
    lines.append(text(left - 8, y + 4, f"{value:.0f}", 11, "end"))
for batch in batch_sizes:
    x = x_for(batch)
    lines.append(f'<line x1="{x:.1f}" y1="{top}" x2="{x:.1f}" y2="{top + plot_height}" stroke="#f1f5f9"/>')
    if batch in (batch_sizes[0], batch_sizes[-1]) or batch % 8 == 0:
        lines.append(text(x, top + plot_height + 20, batch, 11, "middle"))
for metric, label, color in metrics:
    points = []
    for row in summary:
        value = row.get(metric)
        if value is None or not math.isfinite(float(value)):
            continue
        x = x_for(int(row["batch_size"]))
        y = top + plot_height - min(float(value), max_value) / max_value * plot_height
        points.append(f"{x:.1f},{y:.1f}")
        lines.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="2.5" fill="{color}"/>')
    if points:
        lines.append(f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" stroke-width="2"/>')
    legend_x = left + 20 + metrics.index((metric, label, color)) * 180
    lines.append(f'<line x1="{legend_x}" y1="{top - 25}" x2="{legend_x + 18}" y2="{top - 25}" stroke="{color}" stroke-width="2"/>')
    lines.append(text(legend_x + 24, top - 21, label, 11))
lines.append(text(width / 2, height - 17, "batch_size", 13, "middle"))
lines.append("</svg>\n")
(root / "batch_latency_percentiles.svg").write_text("\n".join(lines), encoding="utf-8")

contract = next((value.get("contract") for value in payloads if value.get("batch_size") == 1), None)
if contract is None:
    contract = payloads[0].get("contract", {}) if payloads else {}
final = {
    "status": "COMPLETED",
    "method": "round-robin HTTP load across real B1 Sidecar replicas",
    "health": payloads[0].get("health") if payloads else [],
    "batch_sizes": batch_sizes,
    "http_concurrency": concurrency,
    "contract": contract,
    "results": rows,
    "summary": summary,
    "artifacts": {
        "results_csv": str(root / "results.csv"),
        "latency_summary_csv": str(root / "latency_summary.csv"),
        "latency_plot_svg": str(root / "batch_latency_percentiles.svg"),
        "profiling_summary_csv": str(root / "profiling_summary.csv"),
        "profiling_summary_json": str(root / "profiling_summary.json"),
        "profiling_breakdown_svg": str(root / "profiling_breakdown.svg"),
    },
}
(root / "results.json").write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

write_profiling_summary() {
    local cluster_directory="$1"
    log "Writing per-stage latency profiling summary"
    "$PYTHON_PATH" "$REPO_ROOT/scripts/profile_b1_results.py" \
        --root "$cluster_directory"
}

run_multi_instance() {
    local destination="$OUTPUT_ROOT/multi"
    local instances_directory="$destination/instances"
    local multi_urls=()
    local index port instance_directory pid url
    mkdir -p -- "$instances_directory"

    for (( index=0; index<${#CPU_IDS[@]}; index++ )); do
        port=$(( START_PORT + 1 + index ))
        printf -v instance_directory '%s/%02d' "$instances_directory" "$index"
        start_sidecar "multi-instance-$(printf '%02d' "$index")" "$port" "$instance_directory" "${CPU_IDS[$index]}"
        pid="$LAST_SIDECAR_PID"
        url="$LAST_SIDECAR_URL"
        multi_urls+=("$url")
    done

    local base_index=$(( ${#SIDECAR_PIDS[@]} - ${#CPU_IDS[@]} ))
    for (( index=0; index<${#CPU_IDS[@]}; index++ )); do
        pid="${SIDECAR_PIDS[$(( base_index + index ))]}"
        url="${multi_urls[$index]}"
        instance_directory="${SIDECAR_LOG_DIRS[$(( base_index + index ))]}"
        wait_sidecar_ready "$url" "$pid" "$instance_directory"
        assert_openvino_int8_health "$url"
    done

    invoke_preflight "${multi_urls[0]}" "$destination" "multi"
    local cluster_directory="$destination/b1_max_hardware"
    mkdir -p -- "$cluster_directory"
    local batch_size batch_directory command stdout_path stderr_path exit_code load_cpu_list
    : >"$cluster_directory/benchmark.log"
    log "Running ${#CPU_IDS[@]} OpenVINO INT8 replicas at HTTP concurrency $RESOLVED_CLUSTER_CONCURRENCY for batch sizes $(join_by , "${CLUSTER_BATCH_SIZES[@]}")"
    for batch_size in "${CLUSTER_BATCH_SIZES[@]}"; do
        printf -v batch_directory '%s/batch_%02d' "$cluster_directory" "$batch_size"
        mkdir -p -- "$batch_directory"
        command=(
            "$PYTHON_PATH" -u "$REPO_ROOT/benchmarks/p3/b1_cluster_throughput.py"
            --output "$batch_directory"
            --base-urls "${multi_urls[@]}"
            --batch-size "$batch_size"
            --request-mode "$CLUSTER_REQUEST_MODE"
            --concurrency "$RESOLVED_CLUSTER_CONCURRENCY"
            --warmup "$WARMUP_SECONDS"
            --measurement "$MEASUREMENT_SECONDS"
            --repeats "$REPEATS"
            --text-class medium
            --expected-dimension "$EXPECTED_DIMENSION"
            --target-qps "$CONTRACT_TARGET_QPS"
            --expected-backend openvino
            --expected-precision INT8
        )
        if (( ${#LOAD_CPU_IDS[@]} > 0 )) && command -v taskset >/dev/null 2>&1; then
            load_cpu_list="$(join_by , "${LOAD_CPU_IDS[@]}")"
            command=(taskset -c "$load_cpu_list" "${command[@]}")
        fi
        stdout_path="$batch_directory/benchmark.stdout.log"
        stderr_path="$batch_directory/benchmark.stderr.log"
        log "Running multi-instance batch_size=$batch_size"
        if "${command[@]}" >"$stdout_path" 2>"$stderr_path"; then
            exit_code=0
        else
            exit_code="$?"
        fi
        cat -- "$stdout_path" "$stderr_path" >>"$cluster_directory/benchmark.log"
        printf '%s\n' "$exit_code" >"$batch_directory/exit_code"
        (( exit_code == 0 )) || die "Multi-instance benchmark failed for batch_size=$batch_size with exit code $exit_code."
        check_result "$batch_directory/results.json" "Multi-instance batch_size=$batch_size"
    done
    write_multi_summary "$cluster_directory"
    write_profiling_summary "$cluster_directory"
    printf '%s\n' 0 >"$cluster_directory/exit_code"
    check_result "$cluster_directory/results.json" "Multi-instance benchmark"
}

main() {
    parse_arguments "$@"
    validate_parameters
    command -v realpath >/dev/null 2>&1 || die "realpath is required (GNU coreutils)."
    command -v curl >/dev/null 2>&1 || die "curl is required."
    command -v awk >/dev/null 2>&1 || die "awk is required."
    configure_paths
    trap 'on_error "$LINENO" "$BASH_COMMAND"' ERR
    trap on_exit EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM

    set_runtime_environment
    import_model_cache_archive
    enable_offline_model_cache
    detect_cpu_topology
    local replica_count="${#CPU_IDS[@]}"
    (( START_PORT + replica_count <= 65535 )) || die "Detected replica port range exceeds 65535."
    if (( CLUSTER_CONCURRENCY > 0 )); then
        RESOLVED_CLUSTER_CONCURRENCY="$CLUSTER_CONCURRENCY"
    else
        RESOLVED_CLUSTER_CONCURRENCY=$(( replica_count * 4 ))
    fi

    log "Detected $replica_count available physical CPU core(s) from ${#ALLOWED_CPU_IDS[@]} logical CPU(s)"
    log "Multi-instance concurrency: $RESOLVED_CLUSTER_CONCURRENCY"
    if [[ "$SKIP_ENVIRONMENT_SETUP" == false ]]; then
        initialize_isolated_environment
    fi
    [[ -x "$PYTHON_PATH" ]] ||
        die "Isolated interpreter is missing: $PYTHON_PATH. Run without --skip-environment-setup first."
    cleanup_stale_acceptance_processes
    assert_required_ports_available
    write_environment_json

    if [[ "$SKIP_SINGLE_INSTANCE" == false ]]; then
        run_single_instance
    fi
    if [[ "$SKIP_MULTI_INSTANCE" == false ]]; then
        if [[ "$SKIP_SINGLE_INSTANCE" == true ]]; then
            prepare_model_for_multi
        fi
        run_multi_instance
    fi
    RUN_STATUS="COMPLETED"
}

main "$@"
