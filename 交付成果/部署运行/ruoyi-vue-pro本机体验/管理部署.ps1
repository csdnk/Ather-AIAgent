param([ValidateSet('Start','Stop','Status','Verify')][string]$Action='Status')
$ErrorActionPreference='Stop'
$runtimeEnv='E:/projects/codex/.agent-work/aether/workspace-support/ruoyi-local-20261006/private/runtime.env'
if (!(Test-Path -LiteralPath $runtimeEnv)) { throw 'Missing local runtime.env' }
$composeArgs=@('compose','--env-file',$runtimeEnv,'-f',(Join-Path $PSScriptRoot 'compose.yaml'))
switch ($Action) {
 'Start' { & docker @composeArgs up -d }
 'Stop' { & docker @composeArgs stop }
 'Status' { & docker @composeArgs ps }
 'Verify' {
  & docker @composeArgs ps
  $page=Invoke-WebRequest -Uri 'http://127.0.0.1:18888/' -TimeoutSec 20
  $tenant=Invoke-RestMethod -Uri ('http://127.0.0.1:18888/admin-api/system/tenant/get-id-by-name?name='+[uri]::EscapeDataString('芋道源码')) -TimeoutSec 20
  if ($page.StatusCode -ne 200 -or $tenant.code -ne 0 -or !$tenant.data) { throw 'Verification failed' }
  Write-Host ('HTTP 200; tenant ID: '+$tenant.data)
 }
}
if ($LASTEXITCODE -ne 0) { throw 'Docker command failed' }
