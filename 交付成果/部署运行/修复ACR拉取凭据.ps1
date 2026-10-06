# 在本机 PowerShell 运行。密码通过隐藏提示输入，不写入命令历史或文件。
$ErrorActionPreference = 'Stop'
$P3Kubeconfig = 'E:/projects/codex/.agent-work/aether/workspace-support/aks-demo-20261001/kubeconfig'
$P3Namespace = 'aether-p3-demo'
$P3RegistryHost = 'aetherp3acr-a0bne7gpetbpdcbq.azurecr.io'
$P3Username = 'p3-pull-scope'

if (-not (Test-Path -LiteralPath $P3Kubeconfig)) {
    throw '未找到本机 AKS 凭据文件，请保留现有配置并联系助手核查。'
}
Get-Command kubectl -ErrorAction Stop | Out-Null
$P3PasswordSecure = Read-Host '请输入 ACR 令牌 p3-pull-scope 的 password1（输入不显示）' -AsSecureString
$P3PasswordPointer = [IntPtr]::Zero
$P3PreviousOutputEncoding = $OutputEncoding
try {
    $OutputEncoding = [Text.UTF8Encoding]::new($false)
    $P3PasswordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($P3PasswordSecure)
    $P3PasswordText = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($P3PasswordPointer)
    if ([string]::IsNullOrWhiteSpace($P3PasswordText)) {
        throw '密码为空，未修改 Secret。请运行脚本并粘贴已生成的令牌密码。'
    }
    $P3Auth = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($P3Username + ':' + $P3PasswordText))
    $P3DockerConfig = @{ auths = @{ $P3RegistryHost = @{ auth = $P3Auth } } } | ConvertTo-Json -Depth 6 -Compress
    $P3SecretText = kubectl --kubeconfig $P3Kubeconfig get secret acr-pull -n $P3Namespace -o json
    if ($LASTEXITCODE -ne 0) { throw '无法读取现有 Secret，未继续修改。' }
    $P3ExistingSecret = ($P3SecretText -join "`n") | ConvertFrom-Json
    $P3ExistingSecret.type = 'kubernetes.io/dockerconfigjson'
    $P3ConfigEncoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($P3DockerConfig))
    $P3ExistingSecret.data | Add-Member -NotePropertyName '.dockerconfigjson' -NotePropertyValue $P3ConfigEncoded -Force
    $P3ExistingSecret | ConvertTo-Json -Depth 30 -Compress | kubectl --kubeconfig $P3Kubeconfig replace -f -
    if ($LASTEXITCODE -ne 0) { throw 'Secret 更新失败，未重启 Temporal。' }
} finally {
    $OutputEncoding = $P3PreviousOutputEncoding
    if ($P3PasswordPointer -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($P3PasswordPointer)
    }
    $P3PasswordText = $null
    $P3Auth = $null
    $P3DockerConfig = $null
    $P3SecretText = $null
    $P3ExistingSecret = $null
    $P3ConfigEncoded = $null
    $P3PasswordSecure.Dispose()
}

kubectl --kubeconfig $P3Kubeconfig rollout restart deployment/temporal -n $P3Namespace
if ($LASTEXITCODE -ne 0) { throw 'Secret 已更新，但 Temporal 重启请求失败，请保留错误输出。' }
Write-Host '凭据已更新，Temporal 正在重新拉取镜像。请告知助手脚本已执行，无需发送密码。'
