param(
    [string]$BaseUrl = "http://127.0.0.1:18081",
    [double]$RefreshSeconds = 1.0
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$PythonPath = Join-Path $RepoRoot ".venv-b1\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "B1 environment is missing. Run .\scripts\install_b1.ps1 first."
}
if ($RefreshSeconds -le 0) {
    throw "RefreshSeconds must be positive."
}

Push-Location $RepoRoot
try {
    & $PythonPath -m aether_agent_memory.b1.dashboard `
        --base-url $BaseUrl `
        --refresh-seconds $RefreshSeconds
}
finally {
    Pop-Location
}
