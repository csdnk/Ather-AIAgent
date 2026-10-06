[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet('backend','frontend','platform','p3')][string]$Component,
    [ValidatePattern('^[a-zA-Z0-9][a-zA-Z0-9_.-]+$')][string]$Tag,
    [Parameter(Mandatory)][string]$EvidenceDirectory,
    [string]$Registry = 'aetherp3acr-a0bne7gpetbpdcbq.azurecr.io',
    [switch]$Push
)
$ErrorActionPreference = 'Stop'
$sourceRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$evidenceRoot = [IO.Path]::GetFullPath($EvidenceDirectory)
if ($evidenceRoot.StartsWith($sourceRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build evidence must be outside the source workspace.'
}
New-Item -ItemType Directory -Force -Path $evidenceRoot | Out-Null
$dockerfile = "deploy/ruoyi/Dockerfile.$Component"
[string[]]$sourcePaths = switch ($Component) {
    'backend' { @('ruoyi/backend') }
    'frontend' { @('ruoyi/frontend', 'deploy/ruoyi/nginx.conf') }
    'platform' { @('platform-web', 'src/aether_platform', 'src/aether_agent_memory', 'scripts/platform/build_web.mjs', 'scripts/platform/run_cloud.py', 'deploy/platform/requirements-cloud.txt') }
    'p3' { @('src', 'pyproject.toml') }
}
Push-Location $sourceRoot
try {
    # Include nonsecret .env build inputs even if the repository's broad ignore
    # rule excludes them; never traverse dependency/build caches or junctions.
    $sourceFiles = & rg --files --hidden --no-ignore -g '!**/node_modules/**' -g '!**/target/**' -g '!**/dist*/**' -g '!**/__pycache__/**' -g '!**/.git/**' @sourcePaths $dockerfile "$dockerfile.dockerignore"
} finally { Pop-Location }
if ($LASTEXITCODE -ne 0 -or -not $sourceFiles) { throw 'Cannot enumerate build inputs.' }
$inputHashes = foreach ($relative in ($sourceFiles | Sort-Object -Unique)) {
    $filePath = Join-Path $sourceRoot $relative
    if (Test-Path -LiteralPath $filePath -PathType Leaf) {
        "$relative $((Get-FileHash -LiteralPath $filePath -Algorithm SHA256).Hash.ToLowerInvariant())"
    }
}
$sourceHash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData([Text.Encoding]::UTF8.GetBytes(($inputHashes -join "`n")))).ToLowerInvariant()
if (-not $Tag) { $Tag = 'source-' + $sourceHash.Substring(0,16) }
$image = "$Registry/aether/ruoyi-${Component}:$Tag"
$inputHashes | Set-Content -LiteralPath (Join-Path $evidenceRoot "$Component-source-inputs.txt")
$buildLog = Join-Path $evidenceRoot "$Component-build.log"
& docker build --progress plain --platform linux/amd64 --label "org.aether.source.sha256=$sourceHash" --file (Join-Path $sourceRoot $dockerfile) --tag $image $sourceRoot *> $buildLog
if ($LASTEXITCODE -ne 0) { throw "Image build failed; see $buildLog" }
if ($Push) {
    $pushLog = Join-Path $evidenceRoot "$Component-push.log"
    & docker push $image *> $pushLog
    if ($LASTEXITCODE -ne 0) { throw "Image push failed; see $pushLog" }
    $digestLine = Get-Content -LiteralPath $pushLog | Select-String 'digest: (sha256:[a-f0-9]{64})' | Select-Object -Last 1
    if (-not $digestLine) { throw 'Push did not return a digest.' }
    $digest = $digestLine.Matches[0].Groups[1].Value
    [ordered]@{component=$Component;image=$image;digest=$digest;source_sha256=$sourceHash;image_ref="$Registry/aether/ruoyi-${Component}@$digest";created_at=[DateTimeOffset]::UtcNow.ToString('o')} |
        ConvertTo-Json | Set-Content -LiteralPath (Join-Path $evidenceRoot "$Component-image.json")
    Write-Output "$image@$digest"
}
