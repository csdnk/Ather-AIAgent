param(
    [ValidateSet('Start', 'Stop', 'Status', 'Seed', 'Accounts')]
    [string]$Action = 'Status',
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)][string]$Python,
    [string]$Docker = 'docker'
)
$ErrorActionPreference = 'Stop'
$settings = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$runtime = Split-Path -Parent (Resolve-Path -LiteralPath $Config).Path
$codeRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$serverScript = Join-Path $PSScriptRoot 'run_identity_bff.py'
$processFile = Join-Path $runtime 'bff-process.json'
$identityCompose = Join-Path $settings.identity_lab_directory 'compose.yaml'
$dataCompose = Join-Path $runtime 'compose.yaml'
$env:PYTHONPATH = Join-Path $codeRoot 'src'
$env:PYTHONDONTWRITEBYTECODE = '1'

function Get-OwnedBff {
    if (-not (Test-Path -LiteralPath $processFile)) { return $null }
    $record = Get-Content -LiteralPath $processFile -Raw | ConvertFrom-Json
    $candidate = Get-CimInstance Win32_Process -Filter "ProcessId = $($record.pid)"
    if ($null -eq $candidate) { return $null }
    if ($candidate.CreationDate.ToUniversalTime().Ticks -ne ([datetime]$record.created).ToUniversalTime().Ticks -or
        $candidate.CommandLine -notlike "*$serverScript*" -or
        $candidate.CommandLine -notlike "*$Config*") {
        throw 'PID ownership does not match this lab; no process was stopped.'
    }
    return $candidate
}

switch ($Action) {
    'Accounts' {
        # Only this explicit action displays passwords in the user's terminal.
        $accounts = Get-Content -LiteralPath (Join-Path $runtime 'test-accounts.private.json') -Raw |
            ConvertFrom-Json
        $accounts.PSObject.Properties | ForEach-Object {
            [PSCustomObject]@{ Username = $_.Name; Password = $_.Value }
        } | Format-Table -AutoSize
        return
    }
    'Seed' {
        & $Python -B (Join-Path $PSScriptRoot 'seed_demo_data.py') --config $Config
        if ($LASTEXITCODE -ne 0) { throw 'Demo import failed.' }
        return
    }
    'Start' {
        & $Docker compose -f $identityCompose start
        if ($LASTEXITCODE -ne 0) { throw 'Identity containers did not start.' }
        & $Docker compose -f $dataCompose start
        if ($LASTEXITCODE -ne 0) { throw 'Platform database did not start.' }
        & (Join-Path $PSScriptRoot 'manage_p3_connection.ps1') -Action Start -Config $Config -Python $Python
        if ($null -eq (Get-OwnedBff)) {
            if (Get-NetTCPConnection -LocalPort 19010 -State Listen -ErrorAction SilentlyContinue) {
                throw 'Port 19010 is occupied by another process.'
            }
            $launched = Start-Process -FilePath $Python -ArgumentList @(
                '-B', "`"$serverScript`"", '--config', "`"$Config`""
            ) -WorkingDirectory $codeRoot -WindowStyle Hidden -PassThru `
                -RedirectStandardOutput (Join-Path $runtime 'bff.stdout.log') `
                -RedirectStandardError (Join-Path $runtime 'bff.stderr.log')
            $owned = Get-CimInstance Win32_Process -Filter "ProcessId = $($launched.Id)"
            @{ pid = $launched.Id; created = $owned.CreationDate.ToUniversalTime().ToString('o') } |
                ConvertTo-Json | Set-Content -LiteralPath $processFile -Encoding utf8
        }
    }
    'Stop' {
        $owned = Get-OwnedBff
        if ($null -ne $owned) {
            Get-CimInstance Win32_Process -Filter "ParentProcessId=$($owned.ProcessId)" |
                Where-Object { $_.CommandLine -like "*$serverScript*" -and $_.CommandLine -like "*$Config*" } |
                ForEach-Object { Stop-Process -Id $_.ProcessId }
            Stop-Process -Id $owned.ProcessId -ErrorAction SilentlyContinue
        }
        & (Join-Path $PSScriptRoot 'manage_p3_connection.ps1') -Action Stop -Config $Config -Python $Python
        # Container data is retained; stop only this new BFF. Use Compose stop
        # separately when the identity lab is no longer needed by other tests.
        Write-Output 'The owned BFF has stopped. Identity/database containers and data were retained.'
        return
    }
}

$healthy = $false
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    try {
        $response = Invoke-WebRequest -Uri 'http://127.0.0.1:19010/' -TimeoutSec 2
        if ($response.StatusCode -eq 200 -and $response.Content -like '*Aether*') {
            $healthy = $true
            break
        }
    } catch { }
    if ($Action -eq 'Status') { break }
    Start-Sleep -Milliseconds 500
}
[PSCustomObject]@{
    BffResponding = $healthy
    Agent = 'http://localhost:19010/'
    Management = 'http://localhost:19000/app/default%20workspace/aether-admin'
    Mode = 'Local Agent UI + isolated Azure P3; full account provisioning pending'
} | Format-List
if ($Action -eq 'Start' -and -not $healthy) { throw 'BFF did not become reachable.' }
