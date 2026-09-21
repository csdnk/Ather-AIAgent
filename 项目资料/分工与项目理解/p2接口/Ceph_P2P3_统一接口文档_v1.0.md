# Ceph P2/P3 团队统一接口文档

版本：1.0 ｜ 更新日期：2026-09-15 ｜ 面向：同一局域网内的 P2/P3 Windows 应用开发人员

本文依据已提供的部署输出和验收结果编写，不表示本次重新探测过服务器。凭据由负责人填写或另行安全分发。P2/P3 共用数据及业务凭据，允许修改和删除，操作前需协调数据归属。

## 1. 接口总览

| 服务 | 用途 | 团队访问入口 | 认证 | 数据位置 |
|---|---|---|---|---|
| RGW / S3 | 对象上传、下载、列举、删除 | `http://192.168.4.150:8080` | S3 Access Key + Secret Key，SigV4 | 桶 `team-validation` |
| CephFS / Samba | 共享文件和目录 | `\\192.168.4.150\teamshare`，TCP **1445** | Samba 用户 `smbteam` + 密码 | 文件系统 `teamfs`，共享目录 `teamshare` |
| RBD HTTP v0.2 | 镜像查询、创建、删除、原始块读写 | `http://192.168.4.150:8000` | Bearer API Token | 池 `team-rbd` |

三种接口访问不同的数据空间：S3 对象不会自动出现在 CephFS 共享目录，RBD 中的字节也不会自动变成文件。当前没有 SSD/HDD 冷热迁移接口。

### 1.1 验收状态

| 项目 | 已有证据 | 尚需注意 |
|---|---|---|
| RGW | 局域网签名上传、下载和内容校验通过 | 本次未重新探测 |
| CephFS | Windows 映射、读写、服务重启后读取及两端 SHA256 一致 | 最新宿主机转发表截图未列出 1445；使用前检查连通性 |
| RBD 查询 | 其他局域网 Windows 电脑查询通过；无 Token 返回 401 | 使用同一 API Token |
| RBD 创建/读写/删除 | Windows 宿主机 HTTP 端到端验收通过，临时镜像清理成功 | 新增操作在另一台 P2/P3 电脑上的验收尚未提供 |

### 1.2 地址与凭据

`192.168.4.150` 是 Windows 宿主机地址；`192.168.100.11/.12/.13` 是 Ceph 内部虚拟机地址。团队客户端使用上表的宿主机入口，不需要安装原生 Windows RBD 客户端或直接连接 MON/OSD。

| 配置 | 值 |
|---|---|
| S3 Access Key | TM6W0KYUCO6MMPYL1LAT |
| S3 Secret Key | FCRS3ldalOKzWbfjfeixRcFIHTCa2nMcDT5oN0hj |
| S3 Region | `us-east-1` |
| S3 地址模式 | `path` |
| Samba 用户 | `smbteam` |
| Samba 密码 | 00000000 |
| RBD API Token | c9bfbc23e79a6f5e6088f9f36c15a85ce2d09479d61e062f200a2b979da5bbe8 |

三套凭据不能混用。HTTP 入口当前没有 TLS，只用于受控局域网；不要将含真实凭据的文档公开发布。API Token 的持有者具有共享 RBD 数据修改、删除权限。

## 2. Windows 连接前检查

以下命令运行在客户端 **PowerShell**，不是 Ubuntu SSH 终端。只复制代码块，不复制提示符。

```powershell
Test-NetConnection 192.168.4.150 -Port 8080
Test-NetConnection 192.168.4.150 -Port 1445
Test-NetConnection 192.168.4.150 -Port 8000
```

对应服务的 `TcpTestSucceeded` 应为 `True`。端口连通只证明 TCP 入口可访问，仍需认证及实际读写验证。

## 3. RGW：S3 对象接口

### 3.1 应用配置及操作

Endpoint：`http://192.168.4.150:8080`；默认共享桶：`team-validation`。应用使用兼容 S3 的 SDK，指定 SigV4 和 path-style，不使用 RBD Bearer Token。

| 操作 | S3 API / SDK 方法 | 关键参数 |
|---|---|---|
| 上传或覆盖对象 | PutObject / `put_object` | Bucket、Key、Body |
| 下载对象 | GetObject / `get_object` | Bucket、Key |
| 查询元信息 | HeadObject / `head_object` | Bucket、Key |
| 按前缀列举 | ListObjectsV2 / `list_objects_v2` | Bucket、Prefix，完整列举使用分页 |
| 删除对象 | DeleteObject / `delete_object` | Bucket、Key |

同一 Key 再次上传会更新该对象；是否保留旧版本取决于桶版本配置，本文不承诺版本恢复。删除返回成功不必然代表历史版本被物理清除。不要将 ETag 一律当作文件 MD5。

### 3.2 Python 完整小文件示例

客户端先安装 Python 和 boto3（如果已有环境则复用）：

```powershell
python -m pip install boto3
```

把以下代码保存为 `s3_check.py`，执行 `python s3_check.py`。示例只删除自己创建的随机 Key，不删除共享桶或其他对象。

```python
import getpass
import hashlib
import uuid
import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3", endpoint_url="http://192.168.4.150:8080",
    aws_access_key_id=input("S3 Access Key: ").strip(),
    aws_secret_access_key=getpass.getpass("S3 Secret Key: "),
    region_name="us-east-1",
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)
bucket = "team-validation"
key = "interface-checks/" + uuid.uuid4().hex + ".txt"
data = b"P2 P3 Ceph S3 validation"
expected = hashlib.sha256(data).hexdigest()
s3.put_object(Bucket=bucket, Key=key, Body=data, Metadata={"sha256": expected})
response = s3.get_object(Bucket=bucket, Key=key)
try:
    actual = response["Body"].read()
finally:
    response["Body"].close()
if actual != data or hashlib.sha256(actual).hexdigest() != expected:
    raise RuntimeError("Checksum mismatch; object retained: " + key)
print("PASS upload/download:", key, expected)
for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=key):
    print([item["Key"] for item in page.get("Contents", [])])
s3.delete_object(Bucket=bucket, Key=key)
print("Delete request completed for test key")
```

大文件使用 SDK 的流式/分段上传下载能力，避免整个文件读入内存。基础接口验收不等于所有 AWS S3 功能均已验证。SDK 参数参考 [Boto3 S3 文档](https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html)。

## 4. CephFS：Windows SMB 文件接口

Windows 通过 Samba 访问 CephFS，不通过 HTTP。共享名称是 `teamshare`，后端路径为 `/mnt/teamfs/teamshare`。

### 4.1 客户端能力检查与映射

```powershell
(Get-Command New-SmbMapping).Parameters.ContainsKey("TcpPort")
Get-SmbMapping
Test-Path Z:\
```

`TcpPort` 检查必须为 `True`。此前验证的是支持该参数的 Windows 11 build 26200；不要假定所有 Windows 版本均支持。若为 False，需使用支持自定义 SMB 端口的 Windows 客户端或由负责人调整接入方案；安装新版 PowerShell 本身不保证增加此系统能力。参见 [Microsoft 自定义 SMB 端口说明](https://learn.microsoft.com/en-us/windows-server/storage/file-server/smb-ports)。

如果 Z: 尚未被占用，在应用运行的同一 Windows 用户会话中执行：

```powershell
$sec = Read-Host "Samba password for smbteam" -AsSecureString
$cred = [System.Management.Automation.PSCredential]::new("smbteam", $sec)
New-SmbMapping -LocalPath "Z:" -RemotePath "\\192.168.4.150\teamshare" -TcpPort 1445 -Credential $cred -Persistent $true
Get-ChildItem Z:\
```

UNC 路径以两个反斜杠开头；不要把端口写成 `\\192.168.4.150:1445\teamshare`。Z: 已占用时选择其他盘符或复用已有正确映射。持久映射不保证重启后无需凭据即可恢复；Windows 服务账户也不会自动继承交互用户的盘符映射。[New-SmbMapping 参数说明](https://learn.microsoft.com/en-us/powershell/module/smbshare/new-smbmapping?view=windowsserver2025-ps)

### 4.2 文件读写、校验及删除

```powershell
$ErrorActionPreference = "Stop"
$file = "Z:\interface-check-$([guid]::NewGuid().ToString('N')).txt"
$expected = "P2 P3 CephFS validation"
[System.IO.File]::WriteAllText($file, $expected, [System.Text.Encoding]::UTF8)
$actual = [System.IO.File]::ReadAllText($file, [System.Text.Encoding]::UTF8)
if ($actual -ne $expected) { throw "Content mismatch" }
Get-FileHash -LiteralPath $file -Algorithm SHA256
Remove-Item -LiteralPath $file
```

应用完成映射后用普通文件 API 访问，例如 Python `open(r"Z:\dataset\a.bin", "rb")` 或 .NET `FileStream`。目录创建、文件覆盖、重命名、删除遵循 SMB/文件系统权限；同一文件的并发修改由应用协调。不要将此共享误写成 S3 桶。

## 5. RBD HTTP v0.2

### 5.1 认证及公共接口

Base URL：`http://192.168.4.150:8000`。所有接口包括健康检查都要求：

```text
Authorization: Bearer <API_TOKEN>
```

JSON 写入请求还需 `Content-Type: application/json`。

| 方法 | 路径 | 响应说明 |
|---|---|---|
| GET | `/healthz` | 进程存活，`ceph_checked=false`，不代表 Ceph 就绪 |
| GET | `/readyz` | 实际查询池成功后返回 `status=ready`、`pool=team-rbd` |
| GET | `/api/v1/capabilities` | 当前模式、操作列表、块大小限制；migration=false |
| GET | `/api/v1/openapi.json` | 带认证的 OpenAPI 描述；没有公开 /docs 页面 |

### 5.2 镜像与块接口

下表以 `P=/api/v1/rbd/pools/team-rbd/images` 缩写；请求时用完整路径替换 P。

| 方法 | 路径 | 输入 | 成功响应 |
|---|---|---|---|
| GET | P | 无 | 200：pool、images 数组 |
| GET | P/{image} | 镜像名 | 200：pool、image、info；ID 在 info.id |
| GET | P/{image}/status | 镜像名 | 200：pool、image、status，含使用状态 |
| POST | P | JSON：name、size_bytes | 201：image、id、size_bytes |
| DELETE | P/{image}?expected_image_id={id} | 当前 ID | 200：image、id、deleted=true |
| GET | P/{image}/blocks?offset={n}&length={n} | 字节偏移及长度 | 200：image、id、offset、length、sha256、data_base64 |
| PUT | P/{image}/blocks | JSON：offset、data_base64、expected_image_id | 200：image、id、offset、length、sha256 |

`size_bytes` 必须为整数，范围 1,048,576～107,374,182,400（1 MiB～100 GiB）。创建采用稀疏分配，不等于预留物理容量。

镜像名允许 ASCII 字母、数字、下划线、点、横线，共 1～128 字符，首字符为字母或数字。本接口只允许 `team-rbd`，不支持命名空间或快照路径。列表查询中容量字段是 `size`，创建响应中是 `size_bytes`，单位均为字节。

块偏移从 0 开始，非负；每次长度为 1～1,048,576 字节，不能超出镜像容量；PUT 的长度由 Base64 解码后字节数决定。JSON 请求体上限 1,500,000 字节。

写入示例：

```json
{"offset":8192,"data_base64":"YWJj","expected_image_id":"填写镜像id"}
```

该请求写入三个字节 abc。成功前后端执行 flush 和读回比对。它不会创建分区、格式化或挂载文件系统，不能把原始块写入当作往文件系统复制文件。

### 5.3 Windows 完整调用示例

在同一 PowerShell 会话按顺序执行。只创建和删除随机验收镜像，不操作 test-disk。

```powershell
$ErrorActionPreference = "Stop"
$sec = Read-Host "RBD API Token" -AsSecureString
$token = [System.Net.NetworkCredential]::new("", $sec).Password
$headers = @{ Authorization = "Bearer $token" }
$base = "http://192.168.4.150:8000"
$root = "$base/api/v1/rbd/pools/team-rbd/images"
Invoke-RestMethod "$base/readyz" -Headers $headers
Invoke-RestMethod $root -Headers $headers | ConvertTo-Json -Depth 10

$name = "doc-check-" + [guid]::NewGuid().ToString("N")
$createBody = @{ name=$name; size_bytes=1048576 } | ConvertTo-Json
$created = Invoke-RestMethod -Method Post -Uri $root -Headers $headers -ContentType "application/json" -Body $createBody
$bytes = [System.Text.Encoding]::UTF8.GetBytes("P2 P3 RBD validation")
$writeBody = @{ offset=8192; data_base64=[Convert]::ToBase64String($bytes); expected_image_id=$created.id } | ConvertTo-Json
$written = Invoke-RestMethod -Method Put -Uri "$root/$name/blocks" -Headers $headers -ContentType "application/json" -Body $writeBody
$read = Invoke-RestMethod -Uri "$root/$name/blocks?offset=8192&length=$($bytes.Length)" -Headers $headers
$sha = [System.Security.Cryptography.SHA256]::Create()
try {
    $expected = [BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "").ToLowerInvariant()
    $actual = [BitConverter]::ToString($sha.ComputeHash([Convert]::FromBase64String($read.data_base64))).Replace("-", "").ToLowerInvariant()
} finally { $sha.Dispose() }
if ($expected -ne $actual -or $written.sha256 -ne $expected) { throw "SHA256 mismatch; image retained: $name" }
Write-Host "PASS write/read SHA256=$actual"
Invoke-RestMethod -Method Delete -Uri "$root/${name}?expected_image_id=$($created.id)" -Headers $headers
$remaining = Invoke-RestMethod -Uri $root -Headers $headers
if (@($remaining.images | Where-Object { $_.image -eq $name }).Count) { throw "Image still listed" }
Write-Host "PASS create/write/read/delete"
```

无 Token 检查：

```powershell
curl.exe -i http://192.168.4.150:8000/readyz
```

应返回 401。本文脚本只使用 ASCII 提示文字；其他包含中文的 .ps1 保存为 UTF-8 with BOM，以兼容 Windows PowerShell 5.1。

### 5.4 并发、删除和重试约定

- 块读写和删除操作期间，目标镜像必须停止外部客户端访问；禁止另一台电脑或虚拟机同时重新挂载、修改或重建同名镜像。
- 后端检查 watchers；发现使用者返回 409。该检查是瞬时状态，不构成对外部客户端的强制隔离。写入还要求 exclusive-lock 特性并获取 managed lock。
- 写入和删除需要当前镜像 ID，防止名称复用后的误操作；核验不是跨外部客户端的原子事务。
- 删除为永久删除；已有快照会被拒绝，不强制解锁，不连带删除快照。
- 当前单 worker 后端一次执行一个创建/删除/块操作；忙时返回 503。多个分块请求不构成事务，不支持整镜像原子写入。
- 超时或网络中断可能发生在操作完成之后。创建先查同名镜像，删除先查列表，写入先读回核验。不要把超时当成未执行并盲目重试。

### 5.5 错误响应

通常为 `{"detail":"错误代码"}`；422 参数校验响应的 detail 是结构化数组。

| HTTP 状态 | 含义 |
|---|---|
| 400 | 镜像名或 Base64 无效 |
| 401 | Token 缺失或错误 |
| 403 | 请求非 team-rbd 池 |
| 404 | 新增块操作/删除目标不存在 |
| 409 | 镜像已存在、占用、ID 不匹配、有快照或缺少 exclusive-lock |
| 413 | 请求体超过上限 |
| 416 | 块范围越界或解码长度不合法 |
| 422 | 必填参数缺失或类型/范围错误 |
| 502 | Ceph/worker 查询或执行失败；写入可能已发生，需检查状态 |
| 503 | 服务忙或 worker 不可用 |
| 504 | 操作超时，结果可能未知 |

旧版列表/详情/状态查询沿用原错误处理，镜像不存在也可能返回 502。

## 6. 常见故障和负责人检查

| 现象 | 优先检查 |
|---|---|
| TCP False | 宿主机地址、客户端网络、虚拟机运行状态、端口转发和防火墙 |
| S3 403/签名错误 | Access/Secret、签名参数、Windows 时间；根路径匿名 200 不代表有私有数据权限 |
| CephFS Z: 不存在 | 同一用户会话是否已映射；1445 是否连通；客户端是否支持 TcpPort |
| RBD 401 | 使用 API Token，而非 S3/Samba 凭据 |
| RBD 409 | 查询镜像状态与 ID；停止其他客户端使用，禁止强制绕过 |

仅负责人在 Windows 宿主机检查转发：

```powershell
netsh interface portproxy show all
Get-NetTCPConnection -State Listen -LocalPort 8080,1445,8000 -ErrorAction SilentlyContinue
```

预期对应后端：8080 → 192.168.100.11:8080；1445 → 192.168.100.11:445；8000 → 192.168.100.11:8000。缺少 1445 时先排查并恢复既有配置，不要让成员改用默认 445 猜测连接。

仅负责人在 ceph01 Ubuntu 检查：

```bash
sudo ceph -s
findmnt --mountpoint /mnt/teamfs
systemctl is-active cephfs-teamfs smbd ceph-rbd-http
sudo journalctl -u ceph-rbd-http -n 40 --no-pager
```

## 7. 本次不包含的能力

未提供 SSD/HDD 冷热迁移调度接口、跨 S3/CephFS/RBD 数据转换、RBD 扩容/快照管理或 Windows 原生磁盘映射。即使已添加真实 HDD，仍需核验 OSD、CRUSH 规则和冷热池后单独实现迁移。
