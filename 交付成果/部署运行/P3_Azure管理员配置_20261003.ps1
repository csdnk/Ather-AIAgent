param(
    [ValidateSet('Inspect', 'EnableTemporalExtension', 'GrantImagePush', 'All')]
    [string]$Action = 'Inspect',
    [string]$AssigneeObjectId = '421c967e-e6fc-43c0-bf73-3b54b688e4bd'
)

$ErrorActionPreference = 'Stop'
$subscription = 'c50c3827-2bb7-43bd-a2e9-76137e2a180e'
$resourceGroup = 'aetherstore-p3'
$server = 'postgresp3'
$registryScope = "/subscriptions/$subscription/resourceGroups/$resourceGroup/providers/Microsoft.ContainerRegistry/registries/aetherp3acr"

function Invoke-AzureJson {
    param([string[]]$Arguments)
    $response = & az @Arguments --subscription $subscription --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw 'Azure CLI operation failed; no permission bypass or automatic retry.' }
    return ($response | ConvertFrom-Json)
}

$parameter = Invoke-AzureJson -Arguments @('postgres', 'flexible-server', 'parameter', 'show', '--resource-group', $resourceGroup, '--server-name', $server, '--name', 'azure.extensions')
$extensions = @($parameter.value -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
Write-Host "PostgreSQL server: $server; existing extension allow-list: $($extensions -join ',')"
Write-Host "Image publisher object ID: $AssigneeObjectId"
Write-Host "ACR role scope: $registryScope"

if ($Action -eq 'Inspect') {
    Write-Host 'Inspection only. Use -Action All to enable BTREE_GIN and grant AcrPush to the named publisher.'
    return
}

if ($Action -in @('EnableTemporalExtension', 'All')) {
    if ($extensions -notcontains 'BTREE_GIN') {
        $updated = (@($extensions) + 'BTREE_GIN' | Select-Object -Unique) -join ','
        $null = Invoke-AzureJson -Arguments @('postgres', 'flexible-server', 'parameter', 'set', '--resource-group', $resourceGroup, '--server-name', $server, '--name', 'azure.extensions', '--value', $updated)
    }
    $verified = Invoke-AzureJson -Arguments @('postgres', 'flexible-server', 'parameter', 'show', '--resource-group', $resourceGroup, '--server-name', $server, '--name', 'azure.extensions')
    if (@($verified.value -split ',' | ForEach-Object { $_.Trim() }) -notcontains 'BTREE_GIN') {
        throw 'BTREE_GIN was not confirmed in the extension allow-list.'
    }
    Write-Host 'BTREE_GIN allow-list confirmed. No database was created, reset, or restarted.'
}

if ($Action -in @('GrantImagePush', 'All')) {
    $existing = @(Invoke-AzureJson -Arguments @('role', 'assignment', 'list', '--scope', $registryScope, '--assignee', $AssigneeObjectId))
    if (-not ($existing | Where-Object { $_.roleDefinitionName -eq 'AcrPush' -and $_.scope -eq $registryScope })) {
        $null = Invoke-AzureJson -Arguments @('role', 'assignment', 'create', '--assignee-object-id', $AssigneeObjectId, '--assignee-principal-type', 'User', '--role', 'AcrPush', '--scope', $registryScope)
    }
    Write-Host 'AcrPush assignment requested for the specified registry only; propagation may take several minutes.'
}
