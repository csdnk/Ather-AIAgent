param(
    [string]$PythonVersion = "3.13",
    [string]$ModelName = "BAAI/bge-small-zh-v1.5",
    [switch]$SkipModelDownload,
    [switch]$RecreateEnvironment
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = Split-Path -Parent $PSScriptRoot
$VenvPath = Join-Path $RepoRoot ".venv-b1"
$PythonPath = Join-Path $VenvPath "Scripts\python.exe"
$CachePath = Join-Path $RepoRoot ".aether\b1\models"

function Assert-LastExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE"
    }
}

$UvCommand = Get-Command uv -ErrorAction SilentlyContinue
if ($null -eq $UvCommand) {
    Write-Host "uv was not found. Installing uv from the official Astral installer..."
    Invoke-RestMethod "https://astral.sh/uv/install.ps1" | Invoke-Expression
    $UvCandidates = @(
        (Join-Path $env:USERPROFILE ".local\bin\uv.exe"),
        (Join-Path $env:USERPROFILE ".cargo\bin\uv.exe")
    )
    $UvPath = $UvCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if ($null -eq $UvPath) {
        $UvCommand = Get-Command uv -ErrorAction SilentlyContinue
        if ($null -eq $UvCommand) {
            throw "uv installation finished but uv.exe could not be located. Reopen PowerShell and retry."
        }
        $UvPath = $UvCommand.Source
    }
}
else {
    $UvPath = $UvCommand.Source
}

Write-Host "Installing/locating Python $PythonVersion..."
& $UvPath python install $PythonVersion
Assert-LastExitCode "Python installation"

if ($RecreateEnvironment -and (Test-Path -LiteralPath $VenvPath)) {
    $ResolvedRepo = (Resolve-Path -LiteralPath $RepoRoot).Path
    $ResolvedVenv = (Resolve-Path -LiteralPath $VenvPath).Path
    if (-not $ResolvedVenv.StartsWith($ResolvedRepo, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a virtual environment outside the repository: $ResolvedVenv"
    }
    Remove-Item -LiteralPath $ResolvedVenv -Recurse -Force
}

if (-not (Test-Path -LiteralPath $PythonPath)) {
    & $UvPath venv --python $PythonVersion $VenvPath
    Assert-LastExitCode "Virtual environment creation"
}

Push-Location $RepoRoot
try {
    & $UvPath pip install --python $PythonPath -e ".[b1-sidecar,b1-dashboard]"
    Assert-LastExitCode "B1 dependency installation"

    if (-not $SkipModelDownload) {
        $env:AETHER_B1_MODEL_NAME = $ModelName
        $env:AETHER_B1_CACHE_DIR = $CachePath
        & $PythonPath -m aether_agent_memory.b1.model_download `
            --model-name $ModelName `
            --cache-dir $CachePath `
            --threads 1
        Assert-LastExitCode "Model download and verification"
    }
}
finally {
    Pop-Location
}

Write-Host ""
Write-Host "B1 installation completed."
Write-Host "Environment: $VenvPath"
Write-Host "Model cache: $CachePath"
Write-Host "Start Sidecar: .\scripts\start_b1_sidecar.ps1"
Write-Host "Start dashboard in another PowerShell: .\scripts\start_b1_dashboard.ps1"
