[CmdletBinding()]
param([string]$Database='')
$ErrorActionPreference='Stop'
$taskPython='E:/projects/codex/.agent-work/aether/workspace-support/tv-174013/Scripts/python.exe'
$taskSource=@'
import json,sys
from pathlib import Path
from pymilvus import MilvusClient
base=Path('E:/projects/codex/.agent-work/aether/workspace-support')
private=json.loads((base/'production-closure-20261002-192000/azure-credentials.json').read_text('utf8'))
client=MilvusClient(uri='https://127.0.0.1:49530',token='root:'+private['milvus-credentials']['root-password'],
    secure=True,server_pem_path=str(Path(sys.argv[2])/'milvus-ca.pem'),server_name='milvus.internal',timeout=10)
try:
    databases=client.list_databases(timeout=10)
    chosen=[sys.argv[1]] if sys.argv[1] else databases
    if not set(chosen).issubset(databases):
        raise ValueError('Unknown Milvus database')
    result=[]
    for name in chosen:
        client.use_database(name,timeout=10)
        result.append({'database':name,'collections':client.list_collections(timeout=10)})
    print(json.dumps(result,ensure_ascii=False,indent=2))
except Exception as e:
    print(json.dumps({'connected':False,'errorType':type(e).__name__}))
    raise SystemExit(1)
finally:
    client.close()
'@
& $taskPython -B -X utf8 -c $taskSource $Database $PSScriptRoot
if ($LASTEXITCODE -ne 0) { throw 'Milvus 查询失败，请先执行连接脚本 Start 或 Verify。' }
