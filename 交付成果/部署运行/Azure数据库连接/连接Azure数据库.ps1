[CmdletBinding()]
param(
    [ValidateSet('Start','Status','Stop','CopyPassword','CopyConfig','Verify','Maintain')][string]$Action='Start',
    [ValidateSet('PostgreSQL','Redis','Milvus','All')][string]$Target='PostgreSQL'
)
$ErrorActionPreference='Stop'
$TaskWork='E:/projects/codex/.agent-work/aether/workspace-support/azure-storage-interface-20261003/datagrip-01'
$TaskKube='E:/projects/codex/.agent-work/aether/workspace-support/aks-demo-20261001/kubeconfig'
$TaskState=Join-Path $TaskWork 'connection-state.json'
$TaskStop=Join-Path $TaskWork 'stop-tunnel.request'
$TaskNamespace='aether-p3-demo'
$TaskResource='deployment/aether-desktop-db-relay'
$TaskPython='E:/projects/codex/.agent-work/aether/workspace-support/tv-174013/Scripts/python.exe'
$TaskBase=Split-Path $TaskWork -Parent
$TaskPorts=@{45432=35432;46380=36380;49530=39530}

function Get-TaskTransportProxy {
    if ($env:HTTPS_PROXY) { return $env:HTTPS_PROXY }
    $localProxy=Get-NetTCPConnection -LocalAddress '127.0.0.1' -LocalPort 3067 -State Listen -ErrorAction SilentlyContinue
    if ($localProxy) { return 'http://127.0.0.1:3067' }
    return $null
}

function Get-TunnelSupervisor {
    Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" | Where-Object {
        $_.CommandLine -and
        $_.CommandLine.Replace('\','/') -like '*Azure数据库连接/连接Azure数据库.ps1*' -and
        $_.CommandLine -match '-Action\s+Maintain(?:\s|$)'
    } | Select-Object -First 1
}

function Get-OwnedListener([int]$Port=45432) {
    $listener=Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $listener) { return $null }
    $process=Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
    $command=if ($process.CommandLine) { $process.CommandLine.Replace('\','/') } else { '' }
    if ($process.Name -ne 'kubectl.exe' -or $command -notlike '*aks-demo-20261001/kubeconfig*' -or
        $command -notmatch '(deployment/aether-desktop-db-relay|pod/p3-(local-live-relay-20261003-01|datagrip-relay-[0-9-]+))' -or
        $command -notlike "*${Port}:$($TaskPorts[$Port])*") {
        throw "端口 $Port 被其他进程占用，未终止或修改该进程。"
    }
    return $process
}

function Write-TunnelState([string]$Phase,[int]$Attempt,[int]$ChildId=0) {
    $state=@{mode='persistent';resource=$TaskResource;namespace=$TaskNamespace;phase=$Phase;
        updatedAt=[DateTimeOffset]::UtcNow.ToString('o');supervisorId=$PID;forwardId=$ChildId;attempt=$Attempt;
        localPorts=@(45432,46380,49530)}
    $state | ConvertTo-Json | Set-Content -LiteralPath $TaskState -Encoding UTF8
}

function Test-PostgresRelay {
    $probe=New-Object Net.Sockets.TcpClient
    try {
        $connect=$probe.BeginConnect('127.0.0.1',45432,$null,$null)
        if (-not $connect.AsyncWaitHandle.WaitOne(2500)) { return $false }
        $probe.EndConnect($connect)
        $probe.ReceiveTimeout=4000
        $probe.SendTimeout=2500
        $stream=$probe.GetStream()
        $request=[byte[]](0,0,0,8,4,210,22,47)
        $stream.Write($request,0,$request.Length)
        # 仅请求 TLS 握手协商，未发送账号密码或执行 SQL。
        return ($stream.ReadByte() -eq 83)
    } catch { return $false }
    finally { $probe.Close() }
}

if ($Action -eq 'Maintain') {
    $taskMutex=New-Object Threading.Mutex($false,'Local\AetherAzurePgTunnel45432')
    $taskOwned=$false
    try {
        try { $taskOwned=$taskMutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $taskOwned=$true }
        if (-not $taskOwned) { return }
        $taskAttempt=0
        while (-not (Test-Path -LiteralPath $TaskStop)) {
            $taskAttempt++
            $taskChild=$null
            try {
                Write-TunnelState 'connecting' $taskAttempt
                $prefix=Join-Path $TaskWork ('forward-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+$taskAttempt)
                $launch=@{FilePath='kubectl';ArgumentList=@('--kubeconfig',$TaskKube,'-n',$TaskNamespace,
                    'port-forward',$TaskResource,'45432:35432','46380:36380','49530:39530',
                    '--address','127.0.0.1','--request-timeout=0','--pod-running-timeout=30s');
                    WindowStyle='Hidden';PassThru=$true;RedirectStandardOutput=($prefix+'.log');RedirectStandardError=($prefix+'.error.log')}
                $taskPreviousProxy=$env:HTTPS_PROXY
                try {
                    $env:HTTPS_PROXY=Get-TaskTransportProxy
                    $taskChild=Start-Process @launch
                } finally { $env:HTTPS_PROXY=$taskPreviousProxy }
                $taskStarted=Get-Date
                $taskReady=$false
                $taskHealthAt=Get-Date
                $taskHealthFailures=0
                while (-not $taskChild.HasExited -and -not (Test-Path -LiteralPath $TaskStop)) {
                    if (-not $taskReady) {
                        $taskReady=$true
                        foreach ($port in @(45432,46380,49530)) {
                            $owned=Get-OwnedListener $port
                            if (-not $owned -or $owned.ProcessId -ne $taskChild.Id) { $taskReady=$false; break }
                        }
                        if ($taskReady) { Write-TunnelState 'forwarding' $taskAttempt $taskChild.Id }
                        elseif (((Get-Date)-$taskStarted).TotalSeconds -gt 35) { break }
                    }
                    if ($taskReady -and (Get-Date) -ge $taskHealthAt) {
                        if (Test-PostgresRelay) { $taskHealthFailures=0 }
                        else { $taskHealthFailures++ }
                        $taskHealthAt=(Get-Date).AddSeconds(20)
                        if ($taskHealthFailures -ge 2) { break }
                    }
                    Start-Sleep -Seconds 1
                }
            } catch {
                Write-Host ('连接将重试，错误类型：'+$_.Exception.GetType().Name)
            } finally {
                if ($taskChild) {
                    if (-not $taskChild.HasExited) { $taskChild.Kill(); $taskChild.WaitForExit() }
                    $taskChild.Dispose()
                }
            }
            Write-TunnelState 'reconnecting' $taskAttempt
            for ($taskDelay=0; $taskDelay -lt [Math]::Min(10,$taskAttempt*2); $taskDelay++) {
                if (Test-Path -LiteralPath $TaskStop) { break }
                Start-Sleep -Seconds 1
            }
        }
        Write-TunnelState 'stopped' $taskAttempt
    } finally {
        if ($taskOwned) { $taskMutex.ReleaseMutex() }
        $taskMutex.Dispose()
    }
    return
}

if ($Action -eq 'CopyPassword') {
    if ($Target -eq 'All') { throw '复制密码时请指定 PostgreSQL、Redis 或 Milvus。' }
    $privateData=Get-Content 'E:/projects/codex/.agent-work/aether/workspace-support/production-closure-20261002-192000/azure-credentials.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    switch ($Target) {
        'PostgreSQL' { $privateValue=$privateData.'postgres-credentials'.password }
        'Redis' { $privateValue=$privateData.'redis-credentials'.password }
        'Milvus' { $privateValue=$privateData.'milvus-credentials'.'root-password' }
    }
    Set-Clipboard -Value $privateValue
    $privateValue=$null; $privateData=$null
    Write-Host "$Target 密码已复制，终端不会显示密码。"
    return
}
if ($Action -eq 'CopyConfig') {
    Set-Clipboard -Value (Get-Content (Join-Path $PSScriptRoot 'DataGrip_导入配置.xml') -Raw -Encoding UTF8)
    Write-Host 'PostgreSQL 连接配置已复制；配置不含密码。'
    return
}
if ($Action -eq 'Verify') {
    & $TaskPython -B -X utf8 (Join-Path $TaskBase 'verify-desktop-databases.py') --target $Target
    if ($LASTEXITCODE -ne 0) { throw '真实数据库查询失败，请检查通道、登录或网络。' }
    return
}

$current=Get-OwnedListener
if ($Action -eq 'Status') {
    foreach ($port in @(45432,46380,49530)) {
        $owner=Get-OwnedListener $port
        if ($owner) { Write-Host "127.0.0.1:$port 转发中，进程 $($owner.ProcessId)" }
        else { Write-Host "127.0.0.1:$port 未就绪" }
    }
    if (Get-TunnelSupervisor) { Write-Host '持续连接进程运行中，无三小时截止时间；断线会重连。' }
    else { Write-Host '持续连接进程未运行，请执行 -Action Start。' }
    Write-Host '端口就绪不等于后端查询成功；可用 -Action Verify -Target All 实测。'
    return
}
if ($Action -eq 'Stop') {
    [IO.File]::WriteAllText($TaskStop,'stop')
    for ($taskWait=0; $taskWait -lt 12 -and (Get-TunnelSupervisor); $taskWait++) { Start-Sleep -Seconds 1 }
    $taskSupervisor=Get-TunnelSupervisor
    if ($taskSupervisor) { Stop-Process -Id $taskSupervisor.ProcessId }
    foreach ($port in @(45432,46380,49530)) {
        $owner=Get-OwnedListener $port
        if ($owner) { Stop-Process -Id $owner.ProcessId }
    }
    Write-Host '本机转发和自动重连已停止。Azure 内的转发 Deployment 保留，可随时再次 Start。'
    return
}
if (Get-TunnelSupervisor) {
    if ($current) { Write-Host '持续连接已运行，可在客户端重新连接。' }
    else { Write-Host '持续连接进程正在恢复通道，请执行 Status 或 Verify 查看。' }
    return
}
foreach ($port in @(45432,46380,49530)) {
    if (Get-OwnedListener $port) { throw '发现旧版转发，请先执行 -Action Stop，再执行 -Action Start。' }
}
[IO.Directory]::CreateDirectory($TaskWork) | Out-Null
$taskManifest=Join-Path $PSScriptRoot 'Azure数据库持续连接.yaml'
$taskPreviousProxy=$env:HTTPS_PROXY
try {
    $env:HTTPS_PROXY=Get-TaskTransportProxy
    for ($taskTry=0; $taskTry -lt 3; $taskTry++) {
        & kubectl --kubeconfig $TaskKube apply -f $taskManifest --request-timeout=20s
        if ($LASTEXITCODE -eq 0) { break }
        Start-Sleep -Seconds 2
    }
    if ($LASTEXITCODE -ne 0) { throw '三次配置连接 Deployment 均失败，请检查 Azure 登录和网络。' }
    & kubectl --kubeconfig $TaskKube -n $TaskNamespace rollout status $TaskResource --timeout=45s
    if ($LASTEXITCODE -ne 0) { throw '连接 Deployment 尚未就绪，请稍后再 Start。' }
} finally { $env:HTTPS_PROXY=$taskPreviousProxy }
if (Test-Path -LiteralPath $TaskStop) { Remove-Item -LiteralPath $TaskStop }
$taskScriptPath=$PSCommandPath.Replace('\','/')
$taskProcess=Start-Process 'C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe' -ArgumentList @('-NoProfile','-NonInteractive','-File',('"'+$taskScriptPath+'"'),'-Action','Maintain') -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $TaskWork 'supervisor.log') -RedirectStandardError (Join-Path $TaskWork 'supervisor.error.log')
for ($check=0; $check -lt 45; $check++) {
    Start-Sleep -Seconds 1
    if ($taskProcess.HasExited) { throw '持续连接进程退出，请检查连接脚本日志。' }
    $taskAllReady=$true
    foreach ($port in @(45432,46380,49530)) { if (-not (Get-OwnedListener $port)) { $taskAllReady=$false; break } }
    if ($taskAllReady) { Write-Host '三个本机转发端口已就绪，无三小时限制；断线会重连。'; return }
}
Write-Host '持续连接进程已启动，当前网络仍未连通；请稍后执行 Status 或 Verify。'
