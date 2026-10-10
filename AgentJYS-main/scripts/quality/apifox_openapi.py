"""Prepare observed service schemas for the actual Aether gateway topology."""
import argparse
import copy
import hashlib
import json
from pathlib import Path

ORIGIN = 'https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com'
METHODS = {'get','post','put','patch','delete','head','options'}


def folder(path):
    if '/client-runs/' in path:
        return '06_客户端持久状态与恢复'
    if '/admin/' in path:
        return '05_管理诊断与人工处置'
    if '/operate/' in path:
        return '03_冷热分层与调度'
    if '/recall' in path:
        return '02_Recall与结果回读'
    if any(x in path for x in ['/remember','/sources/','/documents/','/mutation-receipts/']):
        return '01_Remember与生命周期'
    if any(x in path for x in ['/tasks','/operations','/operation-requests','/controls','/recovery']):
        return '04_异步任务与原请求查询'
    if any(x in path for x in ['/configuration','/backups','/restore','/maintenance','/periodic','/incidents']):
        return '07_运维配置与恢复演练'
    return '00_运行状态与身份'


def prepare(source, kind):
    result=copy.deepcopy(source)
    titles={'ruoyi':'Aether_云端管理API','p3':'Aether_记忆核心P3',
            'platform':'Aether_Agent工作流','operations':'Aether_平台内部运维'}
    urls={'ruoyi':ORIGIN+'/ruoyi-api','platform':ORIGIN+'/ruoyi-agent',
          'p3':'http://127.0.0.1:14881','operations':'http://127.0.0.1:14882'}
    if kind not in titles:
        raise ValueError('unknown service kind')
    result['info']['title']=titles[kind]
    result['info']['description']=(
        'Aether 部署后测试接口目录。接口存在与功能验收分别统计。'
        '每次运行固定候选版本、镜像摘要、环境与 run_id。'
        '凭据仅填写本地值；生产环境只执行获准的只读检查。'
        '异步受理不代表完成，保留原 operation/job ID 并轮询原任务。'
        '正文、版本、来源、权限和任务终态必须有语义断言。'
        '\n\nP3 与平台内部运维没有公网路由，请使用测试集群内部 Runner 或受控本机端口转发；不要放开生产网络。'
        '\n\n模型来自服务 OpenAPI；Agent 模型来自已部署源码签名，未声明的响应不虚构为强类型。')
    result['info']['x-source-sha256']=hashlib.sha256(json.dumps(source,sort_keys=True).encode()).hexdigest()
    result['servers']=[{'url':urls[kind],'description':'已核验拓扑；测试环境需独立账号与部署版本'}]
    paths={}
    for original,value in source['paths'].items():
        if kind=='ruoyi' and not original.startswith('/admin-api/'):
            continue
        if kind=='platform' and not original.startswith('/agent/'):
            continue
        if kind=='operations' and not original.startswith('/platform-ops/'):
            continue
        path=original.removeprefix('/admin-api') if kind=='ruoyi' else original.removeprefix('/agent') if kind=='platform' else original
        item=copy.deepcopy(value)
        for method,operation in item.items():
            if method not in METHODS:
                continue
            risk='read' if method in {'get','head','options'} else 'write'
            operation['x-aether-risk']=risk
            operation['x-aether-original-path']=original
            operation['x-apifox-status']='testing'
            if kind=='p3':
                operation['tags']=[folder(path)]
            elif kind=='platform':
                operation['tags']=['01_登录与会话' if '/auth/' in path else '02_对话与附件' if '/chat-api/' in path else '03_记忆与任务']
            elif kind=='operations':
                operation['tags']=['内部运维网关']
            operation['x-apifox-folder']=operation.get('tags',[titles[kind]])[0]
            extra='\n\n**测试级别：** '+('只读，仍需遵守对象权限。' if risk=='read' else '有副作用；只对本轮创建的测试对象执行，保留清理清单。')
            if kind=='ruoyi':
                extra+=' HTTP 200 仍须检查 CommonResult.code；code=0 才表示业务成功。'
            if kind=='p3' and risk=='write':
                extra+=' 使用唯一 X-Operation-ID；REQUEST_IN_PROGRESS 时查询原 job/result，不以新 ID 重发。'
            operation['description']=operation.get('description','')+extra
        paths[path]=item
    result['paths']=paths
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    for kind in ['ruoyi','p3','platform','operations']:
        source=json.loads((args.input/('platform-openapi.json' if kind=='operations' else kind+'-openapi.json')).read_text(encoding='utf-8'))
        prepared=prepare(source,kind)
        if kind=='p3':
            error=Path(__file__).resolve().parents[2]/'contracts/p3/schemas/runtime.ErrorResponse.json'
            prepared['components']['schemas']['ErrorResponse']=json.loads(error.read_text(encoding='utf-8'))
        target=args.output/(prepared['info']['title']+'.openapi.json')
        target.write_text(json.dumps(prepared,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(kind, sum(k in METHODS for v in prepared['paths'].values() for k in v),'operations',len(prepared.get('components',{}).get('schemas',{})),'models')
