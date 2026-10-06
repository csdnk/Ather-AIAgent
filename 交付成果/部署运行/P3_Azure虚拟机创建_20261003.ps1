param(
    [ValidateSet('Inspect', 'Create')]
    [string]$Action = 'Inspect',
    [string]$AdminSourceCidr = '207.174.28.211/32',
    [string]$SshPublicKey = (Join-Path $PSScriptRoot 'P3_Azure虚拟机_本机私密/azure-vm.pub')
)

$ErrorActionPreference = 'Stop'
$subscription = 'c50c3827-2bb7-43bd-a2e9-76137e2a180e'
$resourceGroup = 'aetherstore-p3'
$scope = "/subscriptions/$subscription/resourceGroups/$resourceGroup"
$location = 'southeastasia'
$vmName = 'aether-p3-service-01'
$vmSize = 'Standard_D4s_v5'
$image = 'Canonical:ubuntu-24_04-lts:server:24.04.202609040'
$subnet = "$scope/providers/Microsoft.Network/virtualNetworks/vnet/subnets/development"
$nsgName = "$vmName-nsg"
$ipName = "$vmName-ip"
$nicName = "$vmName-nic"
$dnsLabel = 'aether-p3-service-01-c50c3827'

function Invoke-AzureJson {
    param([string[]]$Arguments)
    $response = & az @Arguments --subscription $subscription --only-show-errors --output json
    if ($LASTEXITCODE -ne 0) {
        throw 'Azure operation failed. Inspect actual resource state before resuming; no permission changes or fallback identities are attempted.'
    }
    return ($response | ConvertFrom-Json)
}

$permissions = Invoke-AzureJson -Arguments @('rest', '--method', 'get', '--url', "https://management.azure.com$scope/providers/Microsoft.Authorization/permissions?api-version=2022-04-01")
$required = @(
    'Microsoft.Compute/virtualMachines/write',
    'Microsoft.Compute/disks/write',
    'Microsoft.Network/networkInterfaces/write',
    'Microsoft.Network/publicIPAddresses/write',
    'Microsoft.Network/networkSecurityGroups/write',
    'Microsoft.Network/networkSecurityGroups/securityRules/write',
    'Microsoft.Network/virtualNetworks/subnets/join/action'
)
$missing = @()
foreach ($operation in $required) {
    $allowed = $false
    foreach ($permission in $permissions.value) {
        $grant = @($permission.actions | Where-Object { $operation -like $_ }).Count -gt 0
        $excluded = @($permission.notActions | Where-Object { $operation -like $_ }).Count -gt 0
        if ($grant -and -not $excluded) { $allowed = $true }
    }
    if (-not $allowed) { $missing += $operation }
}
$network = Invoke-AzureJson -Arguments @('network', 'vnet', 'subnet', 'show', '--ids', $subnet)
if (@($network.delegations).Count -ne 0) { throw 'The selected existing subnet is delegated; inspect before creating a NIC.' }
$resources = @(Invoke-AzureJson -Arguments @('resource', 'list', '--resource-group', $resourceGroup))
$conflicts = @($resources | Where-Object { $_.name -in @($vmName, $nsgName, $ipName, $nicName) })

[pscustomobject]@{
    Action = $Action; VM = $vmName; Size = $vmSize; Image = $image; Location = $location
    Subnet = $subnet; OsDiskGB = 128; AdminSourceCidr = $AdminSourceCidr
    SshPublicKeyExists = (Test-Path -LiteralPath $SshPublicKey -PathType Leaf)
    MissingPermissions = $missing; ConflictingResources = @($conflicts.name)
} | ConvertTo-Json -Depth 4

if ($Action -eq 'Inspect') { return }
if ($missing.Count) { throw 'Current identity cannot create the VM/network resources. Run with an already authorized resource-management identity. No resources were changed.' }
if ($conflicts.Count) { throw 'Target resources already exist. Inspect them and resume explicitly instead of overwriting or deleting.' }
if ($AdminSourceCidr -notmatch '^([0-9]{1,3}\.){3}[0-9]{1,3}/32$') { throw 'SSH requires an explicit single public IPv4 /32 source.' }
$address = [System.Net.IPAddress]::Parse($AdminSourceCidr.Split('/')[0])
if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork -or [System.Net.IPAddress]::IsLoopback($address) -or $address.IPAddressToString -eq '0.0.0.0') {
    throw 'Invalid SSH source IPv4 address.'
}
if (-not (Test-Path -LiteralPath $SshPublicKey -PathType Leaf)) { throw 'Prepared SSH public key is missing. Do not replace an existing VM key.' }
$publicKey = (Get-Content -LiteralPath $SshPublicKey -Raw).Trim()
if (-not $publicKey.StartsWith('ssh-ed25519 ')) { throw 'An OpenSSH Ed25519 public key is required.' }
$cloudInit = Join-Path $PSScriptRoot 'P3_Azure虚拟机初始化_20261003.yaml'
if (-not (Test-Path -LiteralPath $cloudInit -PathType Leaf)) { throw 'Reviewed cloud-init file is missing.' }

# All cloud mutations start below. Existing databases, AKS and ACR are untouched.
$null = Invoke-AzureJson -Arguments @('network', 'nsg', 'create', '--resource-group', $resourceGroup, '--name', $nsgName, '--location', $location)
$null = Invoke-AzureJson -Arguments @('network', 'nsg', 'rule', 'create', '--resource-group', $resourceGroup, '--nsg-name', $nsgName, '--name', 'SSH-From-Administrator', '--priority', '100', '--direction', 'Inbound', '--access', 'Allow', '--protocol', 'Tcp', '--source-address-prefixes', $AdminSourceCidr, '--destination-port-ranges', '22')
$null = Invoke-AzureJson -Arguments @('network', 'nsg', 'rule', 'create', '--resource-group', $resourceGroup, '--nsg-name', $nsgName, '--name', 'Public-Web', '--priority', '110', '--direction', 'Inbound', '--access', 'Allow', '--protocol', 'Tcp', '--source-address-prefixes', 'Internet', '--destination-port-ranges', '80', '443')
$null = Invoke-AzureJson -Arguments @('network', 'public-ip', 'create', '--resource-group', $resourceGroup, '--name', $ipName, '--location', $location, '--sku', 'Standard', '--allocation-method', 'Static', '--dns-name', $dnsLabel)
$null = Invoke-AzureJson -Arguments @('network', 'nic', 'create', '--resource-group', $resourceGroup, '--name', $nicName, '--location', $location, '--subnet', $subnet, '--network-security-group', $nsgName, '--public-ip-address', $ipName)
$null = Invoke-AzureJson -Arguments @('vm', 'create', '--resource-group', $resourceGroup, '--name', $vmName, '--location', $location, '--size', $vmSize, '--image', $image, '--nics', $nicName, '--admin-username', 'aether', '--authentication-type', 'ssh', '--ssh-key-values', $SshPublicKey, '--os-disk-size-gb', '128', '--storage-sku', 'Premium_LRS', '--security-type', 'TrustedLaunch', '--enable-secure-boot', 'true', '--enable-vtpm', 'true', '--custom-data', $cloudInit)
$vm = Invoke-AzureJson -Arguments @('vm', 'show', '--resource-group', $resourceGroup, '--name', $vmName, '--show-details')
$ip = Invoke-AzureJson -Arguments @('network', 'public-ip', 'show', '--resource-group', $resourceGroup, '--name', $ipName)
$result = [pscustomobject]@{
    VM = $vm.name; ResourceId = $vm.id; PublicIP = $ip.ipAddress; Hostname = $ip.dnsSettings.fqdn
    PrivateIP = $vm.privateIps; PowerState = $vm.powerState; SSHUser = 'aether'
    ApplicationDeployed = $false; Note = 'Verify cloud-init completion and dependency connectivity before deploying the offline release.'
}
$result | ConvertTo-Json -Depth 3
$resultFile = Join-Path $PSScriptRoot 'P3_Azure虚拟机创建结果_20261003.json'
if (Test-Path -LiteralPath $resultFile) { throw 'Result file exists; retain console resource identity and inspect before replacing any prior result.' }
$result | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $resultFile -Encoding utf8
