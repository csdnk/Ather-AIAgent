param(
    [string]$ListenHost = "127.0.0.1",
    [int]$Port = 18081,
    [int]$Threads = 1,
    [ValidateSet("onnx", "openvino", "ipex")]
    [string]$Backend = "onnx",
    [string]$ModelName = "BAAI/bge-small-zh-v1.5",
    [ValidateSet("open", "closed")]
    [string]$FailMode = "open"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $RepoRoot ".venv-b1\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "B1 environment is missing. Run .\scripts\install_b1.ps1 first."
}
if ($Port -lt 1 -or $Port -gt 65535) {
    throw "Port must be between 1 and 65535."
}
if ($Threads -le 0) {
    throw "Threads must be positive."
}

$env:AETHER_B1_HOST = $ListenHost
$env:AETHER_B1_PORT = $Port.ToString()
$env:AETHER_B1_THREADS = $Threads.ToString()
$env:AETHER_B1_BACKEND = $Backend
$env:AETHER_B1_MODEL_NAME = $ModelName
$env:AETHER_B1_CACHE_DIR = Join-Path $RepoRoot ".aether\b1\models"
$env:AETHER_B1_FAIL_MODE = $FailMode
$env:AETHER_B1_MODEL_BATCH_SIZE = "8"
$env:AETHER_B1_MAX_CONCURRENCY = "1"
$env:AETHER_B1_QUEUE_TIMEOUT_SECONDS = "5"
$env:AETHER_B1_BACKEND_TIMEOUT_SECONDS = "120"
$env:OMP_NUM_THREADS = $Threads.ToString()
$env:OPENBLAS_NUM_THREADS = $Threads.ToString()
$env:MKL_NUM_THREADS = $Threads.ToString()
$env:NUMEXPR_NUM_THREADS = $Threads.ToString()

Push-Location $RepoRoot
try {
    & $PythonPath -m aether_agent_memory.b1.sidecar
}
finally {
    Pop-Location
}
