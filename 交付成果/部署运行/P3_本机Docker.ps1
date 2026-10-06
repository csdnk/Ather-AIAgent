[CmdletBinding()]
param(
    [ValidateSet('Start','Status','Stop','Verify','CopyCredential','MaintainRelay')][string]$Action='Status',
    [ValidateSet('p3_operator','p3_application','p3_readonly')][string]$Principal='p3_operator'
)
$ErrorActionPreference='Stop'
$TaskWork='E:/projects/codex/.agent-work/aether/workspace-support/local-docker-20261004'
$TaskCompose=Join-Path $TaskWork 'runtime-01/compose.yaml'
$TaskPython='E:/projects/codex/.agent-work/aether/workspace-support/tv-174013/Scripts/python.exe'
$TaskKube='E:/projects/codex/.agent-work/aether/workspace-support/aks-demo-20261001/kubeconfig'
$TaskRelayStop=Join-Path $TaskWork 'docker-relay.stop'
$TaskRelayPid=Join-Path $TaskWork 'docker-relay.pid'
$TaskPorts=@(55432,56380,59530)

function Get-TaskOwnedRelay {
    $children=Get-CimInstance Win32_Process -Filter "Name='kubectl.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Replace('\','/') -like '*aks-demo-20261001/kubeconfig*' -and
        $_.CommandLine -like '*deployment/aether-desktop-db-relay*' -and
        $_.CommandLine -like '*55432:35432*' -and $_.CommandLine -like '*56380:36380*' -and
        $_.CommandLine -like '*59530:39530*'
    }
    return $children | Select-Object -First 1
}

function Get-TaskSupervisor {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {
        $_.CommandLine -and $_.CommandLine.Replace('\','/') -like '*P3_本机Docker.ps1*' -and
        $_.CommandLine -match '-Action\s+MaintainRelay(?:\s|$)'
    } | Select-Object -First 1
}

function Test-TaskPorts {
    foreach ($port in $TaskPorts) {
        $listener=Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
        if (-not $listener) { return $false }
        $owned=Get-TaskOwnedRelay
        if (-not $owned -or $listener.OwningProcess -ne $owned.ProcessId) {
            throw "Docker 专用端口 $port 被其他程序占用，未修改该程序。"
        }
    }
    return $true
}

if ($Action -eq 'MaintainRelay') {
    $taskMutex=New-Object Threading.Mutex($false,'Local\AetherP3DockerRelay20261004')
    $taskAcquired=$false
    try {
        try { $taskAcquired=$taskMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $taskAcquired=$true }
        if (-not $taskAcquired) { return }
        # This relay uses the direct AKS connection verified on this host.
        $env:HTTPS_PROXY=''; $env:HTTP_PROXY=''; $env:ALL_PROXY=''
        while (-not (Test-Path -LiteralPath $TaskRelayStop)) {
            $existing=Get-TaskOwnedRelay
            if ($existing) {
                while ((Get-TaskOwnedRelay) -and -not (Test-Path -LiteralPath $TaskRelayStop)) { Start-Sleep -Seconds 3 }
                continue
            }
            foreach ($port in $TaskPorts) {
                if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "端口 $port 已占用" }
            }
            $taskStamp=Get-Date -Format 'yyyyMMdd-HHmmss'
            $taskLaunch=@{FilePath='kubectl';ArgumentList=@('--kubeconfig',$TaskKube,'-n','aether-p3-demo',
                'port-forward','deployment/aether-desktop-db-relay','55432:35432','56380:36380','59530:39530',
                '--address','127.0.0.1','--request-timeout=0');WindowStyle='Hidden';PassThru=$true;
                RedirectStandardOutput=(Join-Path $TaskWork ($taskStamp+'-relay.log'));
                RedirectStandardError=(Join-Path $TaskWork ($taskStamp+'-relay.error.log'))}
            $taskChild=Start-Process @taskLaunch
            $taskChild.Id | Set-Content -LiteralPath $TaskRelayPid -Encoding ASCII
            while (-not $taskChild.HasExited -and -not (Test-Path -LiteralPath $TaskRelayStop)) { Start-Sleep -Seconds 3 }
            if (-not $taskChild.HasExited) { Stop-Process -Id $taskChild.Id }
            if (-not (Test-Path -LiteralPath $TaskRelayStop)) { Start-Sleep -Seconds 5 }
        }
    } finally {
        if ($taskAcquired) { $taskMutex.ReleaseMutex() }
        $taskMutex.Dispose()
    }
    return
}

if (-not (Test-Path -LiteralPath $TaskCompose)) { throw '本机运行配置不存在，请恢复已交付的部署环境。' }
if ($Action -eq 'CopyCredential') {
    $taskCredentials=Get-Content -LiteralPath (Join-Path $PSScriptRoot 'P3_Azure生产身份_本机私密/service-credentials.json') -Raw -Encoding UTF8 | ConvertFrom-Json
    Set-Clipboard -Value $taskCredentials.tokens.$Principal
    Write-Output "$Principal 凭据已复制到剪贴板；未显示或写入日志。"
    return
}
if ($Action -eq 'Stop') {
    docker compose -f $TaskCompose stop
    if ($LASTEXITCODE -ne 0) { throw '容器停止失败' }
    Set-Content -LiteralPath $TaskRelayStop -Value 'stop' -Encoding ASCII
    $taskRelay=Get-TaskOwnedRelay
    if ($taskRelay) { Stop-Process -Id $taskRelay.ProcessId }
    Write-Output '本套容器和专用转发已停止；数据卷、Azure 数据及原数据库连接保留。'
    return
}
if ($Action -eq 'Start') {
    docker info --format '{{.ServerVersion}}'
    if ($LASTEXITCODE -ne 0) { throw '请先启动 Docker Desktop，并选择 Linux containers。' }
    if (Test-Path -LiteralPath $TaskRelayStop) { Remove-Item -LiteralPath $TaskRelayStop }
    if (-not (Get-TaskSupervisor)) {
        Start-Process -FilePath powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File',('"'+$PSCommandPath+'"'),'-Action','MaintainRelay') -WindowStyle Hidden | Out-Null
    }
    $taskDeadline=(Get-Date).AddSeconds(40)
    while (-not (Test-TaskPorts) -and (Get-Date) -lt $taskDeadline) { Start-Sleep -Seconds 1 }
    if (-not (Test-TaskPorts)) { throw '直连 AKS 转发尚未就绪；检查 Azure 登录、网络及专用转发日志。' }
    docker compose -f $TaskCompose up -d
    if ($LASTEXITCODE -ne 0) { throw '容器启动失败；运行 -Action Status 查看状态。' }
    Write-Output 'P3: http://127.0.0.1:18080；新版 Agent: http://localhost:19010/（使用独立测试环境启动脚本）'
    return
}
if ($Action -eq 'Verify') {
    if (-not (Test-TaskPorts)) { throw '专用 Azure 转发没有运行，请先 Start。' }
    $taskReady=Invoke-RestMethod -Uri 'http://127.0.0.1:18080/p3/readyz' -TimeoutSec 10
    if ($taskReady.readiness -ne 'ready') { throw 'P3 未就绪' }
    docker compose -f $TaskCompose run --rm --no-deps p3 python /deployment/providers_probe.py
    if ($LASTEXITCODE -ne 0) { throw '真实依赖认证验证失败；命令没有修改业务数据。' }
    Write-Output 'P3 就绪与 PG/Redis/Milvus/Ceph/真实模型验证通过；新版 Agent 使用独立测试入口验收。'
    return
}
docker compose -f $TaskCompose ps -a
Write-Output ('专用转发运行：'+[bool](Get-TaskOwnedRelay))
try { Invoke-RestMethod -Uri 'http://127.0.0.1:18080/p3/readyz' -TimeoutSec 5 | ConvertTo-Json -Compress }
catch { Write-Output 'P3 当前未就绪或未启动。' }
