# 兼容 Windows PowerShell 5.1。只访问本机转发，不读取令牌或修改云端部署。
$ErrorActionPreference = 'Stop'
$P3DocsKubeconfig = 'E:/projects/codex/.agent-work/aether/workspace-support/aks-demo-20261001/kubeconfig'
$P3DocsNamespace = 'aether-p3-demo'
$P3DocsBaseUrl = 'http://127.0.0.1:18080'
$P3DocsUrl = $P3DocsBaseUrl + '/docs'

$P3DocsForwardReady = $false
try {
    $P3OpenApi = Invoke-RestMethod -Uri ($P3DocsBaseUrl + '/openapi.json') -TimeoutSec 3
    $P3DocsForwardReady = $P3OpenApi.info.title -eq 'P3 runtime'
} catch {
    $P3DocsForwardReady = $false
}

if ($P3DocsForwardReady) {
    Start-Process $P3DocsUrl
    Write-Host '已打开 P3 接口文档，复用现有 18080 端口转发；保持原转发窗口运行。'
    return
}

Get-Command kubectl -ErrorAction Stop | Out-Null
if (-not (Test-Path -LiteralPath $P3DocsKubeconfig -PathType Leaf)) {
    throw '未找到本机 AKS 配置文件，请保留现有配置并联系助手核查。'
}
Start-Process $P3DocsUrl
Write-Host '请保持此窗口运行，看到 Forwarding 后刷新 P3 接口文档。浏览文档无需令牌。'
Write-Host '按 Ctrl+C 停止本机转发；不会停止云端 P3。接口试调用需按当前接口要求鉴权。'
& kubectl --kubeconfig $P3DocsKubeconfig -n $P3DocsNamespace port-forward service/p3 18080:8080 --address 127.0.0.1
if ($LASTEXITCODE -ne 0) {
    throw '本机转发未正常启动。请保留上面的错误信息，检查 18080 端口和集群连通性。'
}
