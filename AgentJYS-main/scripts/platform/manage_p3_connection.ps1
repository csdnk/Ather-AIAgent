param(
    [ValidateSet('Start','Stop','Status')][string]$Action='Status',
    [Parameter(Mandatory=$true)][string]$Config,
    [Parameter(Mandatory=$true)][string]$Python
)
$ErrorActionPreference='Stop'
$settings=Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
if (-not $settings.p3_connection) { return }
$connection=$settings.p3_connection
$script=(Join-Path $PSScriptRoot 'connect_p3_lab.py')
$owned=@(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -like 'python*' -and $_.CommandLine -like '*connect_p3_lab.py*' -and
    $_.CommandLine -like "*$Config*" -and $_.CommandLine -like "*$($connection.directory)*"
})
if ($Action -eq 'Stop') {
    foreach ($process in $owned) {
        $children=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($process.ProcessId)")
        foreach ($child in $children) {
            if ($child.Name -like 'kubectl*' -and $child.CommandLine -like '*deployment/aether-agent-p3*' -and
                $child.CommandLine -like "*$($connection.kubeconfig)*" -and $child.CommandLine -like '*19020:8080*') {
                Stop-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            }
        }
    }
    $owned | Sort-Object ProcessId -Descending | ForEach-Object {
        Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    }
    Write-Output 'Local P3 connection stopped; cloud P3 and data retained.'
    return
}
if ($Action -eq 'Start' -and -not $owned.Count) {
    if (Get-NetTCPConnection -LocalPort 19020 -State Listen -ErrorAction SilentlyContinue) {
        throw 'P3 port is occupied by an unowned process.'
    }
    $env:PYTHONPATH=(Resolve-Path (Join-Path $PSScriptRoot '../../src')).Path
    $env:PYTHONUTF8='1';$env:PYTHONDONTWRITEBYTECODE='1'
    Start-Process -FilePath $Python -ArgumentList @('-u','-B',"`"$script`"",'--platform',"`"$Config`"",
        '--directory',"`"$($connection.directory)`"",'--kubectl',"`"$($connection.kubectl)`"",
        '--kubeconfig',"`"$($connection.kubeconfig)`"") -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $connection.directory 'connection.stdout.log') `
        -RedirectStandardError (Join-Path $connection.directory 'connection.stderr.log') | Out-Null
}
$ready=$false
$attempts=if($Action -eq 'Start'){30}else{1}
for($i=0;$i -lt $attempts;$i++) {
    try { $ready=(Invoke-WebRequest 'http://127.0.0.1:19020/p3/readyz' -TimeoutSec 2).StatusCode -eq 200 } catch {}
    if($ready){break}
    if($Action -eq 'Start'){Start-Sleep -Seconds 1}
}
[PSCustomObject]@{P3Ready=$ready;Endpoint='http://127.0.0.1:19020';Location='Azure AKS, private tunnel'} | Format-List
if($Action -eq 'Start' -and -not $ready){throw 'P3 connection did not become ready; inspect connection logs.'}
