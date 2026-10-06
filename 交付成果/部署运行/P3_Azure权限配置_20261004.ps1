param(
    [ValidateSet('Inspect', 'EnableTemporalExtension', 'GrantDeploymentAccess')]
    [string]$Action = 'Inspect',
    [string]$AssigneeObjectId = '78d0f7c0-9320-42a8-8fc8-decdf704688c'
)

$ErrorActionPreference = 'Stop'
$taskSubscription = 'c50c3827-2bb7-43bd-a2e9-76137e2a180e'
$taskResourceGroup = 'aetherstore-p3'
$taskScope = "/subscriptions/$taskSubscription/resourceGroups/$taskResourceGroup"

function Invoke-TaskAzureJson {
    param([string[]]$Arguments)
    $taskResponse = & az @Arguments --subscription $taskSubscription --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) { throw 'Azure operation failed. Read back the original resource before resuming; no automatic retry.' }
    return ($taskResponse | ConvertFrom-Json)
}

[void][guid]::Parse($AssigneeObjectId)
$taskParameter = Invoke-TaskAzureJson -Arguments @('postgres', 'flexible-server', 'parameter', 'show', '--resource-group', $taskResourceGroup, '--server-name', 'postgresp3', '--name', 'azure.extensions')
$taskExtensions = @($taskParameter.value -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
Write-Host "Action: $Action; resource-group scope: $taskScope"
Write-Host "Current deployment account object ID: $AssigneeObjectId"
Write-Host "Existing PostgreSQL extension allow-list: $($taskExtensions -join ',')"

if ($Action -eq 'Inspect') {
    Write-Host 'Read-only inspection. EnableTemporalExtension changes the allow-list; GrantDeploymentAccess grants resource-group Contributor to the specified user. ACR push is not included as a separate required action.'
    return
}

if ($Action -eq 'EnableTemporalExtension') {
    if ($taskExtensions -notcontains 'BTREE_GIN') {
        $taskUpdated = (@($taskExtensions) + 'BTREE_GIN' | Select-Object -Unique) -join ','
        $null = Invoke-TaskAzureJson -Arguments @('postgres', 'flexible-server', 'parameter', 'set', '--resource-group', $taskResourceGroup, '--server-name', 'postgresp3', '--name', 'azure.extensions', '--value', $taskUpdated)
    }
    $taskVerified = Invoke-TaskAzureJson -Arguments @('postgres', 'flexible-server', 'parameter', 'show', '--resource-group', $taskResourceGroup, '--server-name', 'postgresp3', '--name', 'azure.extensions')
    $taskReadback = @($taskVerified.value -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($taskReadback -notcontains 'BTREE_GIN') { throw 'BTREE_GIN was not confirmed; inspect the original parameter before another action.' }
    foreach ($taskPrevious in $taskExtensions) {
        if ($taskReadback -notcontains $taskPrevious) { throw 'A pre-existing extension is absent from readback; inspect concurrent changes before continuing.' }
    }
    Write-Host 'BTREE_GIN confirmed and previous allow-list entries retained. Database extension/schema upgrade is a separate step; no database reset or restart performed.'
    return
}

$taskAssignments = @(Invoke-TaskAzureJson -Arguments @('role', 'assignment', 'list', '--scope', $taskScope))
$taskRoleId = 'b24988ac-6180-42a0-ab88-20f7382dd24c'
$taskExisting = @($taskAssignments | Where-Object {
    $_.principalId -eq $AssigneeObjectId -and $_.scope -eq $taskScope -and $_.roleDefinitionId.EndsWith('/' + $taskRoleId)
})
if (-not $taskExisting.Count) {
    $null = Invoke-TaskAzureJson -Arguments @('role', 'assignment', 'create', '--assignee-object-id', $AssigneeObjectId, '--assignee-principal-type', 'User', '--role', $taskRoleId, '--scope', $taskScope)
}
$taskAfter = @(Invoke-TaskAzureJson -Arguments @('role', 'assignment', 'list', '--scope', $taskScope))
if (-not ($taskAfter | Where-Object { $_.principalId -eq $AssigneeObjectId -and $_.scope -eq $taskScope -and $_.roleDefinitionId.EndsWith('/' + $taskRoleId) })) {
    throw 'Role assignment not confirmed by readback. Inspect original assignment before retrying.'
}
Write-Host 'Resource-group Contributor assignment confirmed. Effective permissions may need time to propagate. No VM or application was created by this grant action.'
