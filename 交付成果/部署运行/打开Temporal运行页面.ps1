# Windows PowerShell 5.1：打开 Azure 页面，不创建本机隧道。
$ErrorActionPreference = 'Stop'
$TemporalWebUrl = 'https://aether-legacy-c50c3827.southeastasia.cloudapp.azure.com/temporal/namespaces/aether-agent-20261005/workflows'
Write-Host '请先以 platform_admin 登录管理后台，再在同一浏览器打开 Temporal；无需 P3 令牌。'
Start-Process $TemporalWebUrl
