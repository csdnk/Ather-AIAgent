"""Build native Apifox scenarios from a verified project export, without secrets.

Input is the user-owned project's native export. Import the result using Apifox's
official native importer; bind existing module IDs instead of duplicating APIs.
"""

import argparse
import copy
import json
from pathlib import Path

ORIGIN = "https://aether-p3-demo-c50c3827.southeastasia.cloudapp.azure.com"


def processor(script, timing):
    return {
        "type": "customScript",
        "data": script,
        "executionTiming": timing,
        "defaultEnable": True,
        "enable": True,
    }


def guard(kind, path, base_var):
    return rf"""pm.request.url.update('http://127.0.0.1:1/BLOCKED');
function block(message) {{
  pm.request.url.update('http://127.0.0.1:1/BLOCKED');
  pm.test('BLOCKED: '+message,()=>{{throw new Error(message);}});
  if(pm.execution && pm.execution.skipRequest) pm.execution.skipRequest();
  throw new Error('BLOCKED: '+message);
}}
if(pm.environment.get('environment_kind')!=={json.dumps(kind)}) block('select {kind} environment');
const base=pm.environment.get({json.dumps(base_var)});
let target;
try{{target=new URL(base);}}catch(e){{block('missing or invalid endpoint');}}
if(target.username || target.password || target.search || target.hash) block('invalid endpoint');
if({json.dumps(kind)}==='public_readonly') {{
  if(base!=={json.dumps(ORIGIN)}) block('unverified public origin');
}} else if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base||'')) block('use an owned loopback forward');
pm.variables.set('run_id',pm.variables.replaceIn('{{{{$guid}}}}'));
pm.request.url.update(base+{json.dumps(path)});
"""


def environment(kind, bases):
    values = {
        "environment_kind": kind,
        "allow_writes": "false",
        "public_origin": ORIGIN,
        "base_url": "http://127.0.0.1:14880",
        "quality_namespace": "aether-quality-20261009",
        "p3_base": "http://127.0.0.1:14881",
        "auth_base": "http://127.0.0.1:14880",
        "agent_base": "http://127.0.0.1:14882/ruoyi-agent",
        "candidate_sha": "",
        "image_digest": "",
        "max_requests": "40",
        "request_timeout_ms": "10000",
        "max_poll_attempts": "20",
        "test_username": "",
        "test_password": "",
        "expected_user_id": "",
        "expected_tenant_id": "",
        "isolation_verified": "false",
        "max_model_cost": "0",
        "max_resource_cost": "0",
        "latency_budget_ms": "",
        "performance_enabled": "false",
    }
    if kind == "isolated_acceptance":
        # Fail closed until a matching full P3 + auth deployment exists.
        values.update(p3_base="", auth_base="", agent_base="")
    for account in ["aetherusera", "aetherusera2", "aetheruserb", "aetheruserc", "aetherdisabled"]:
        values[account + "_password"] = ""
    variables = []
    for name, value in values.items():
        secret = "password" in name or "token" in name
        variables.append(
            {
                "name": name,
                "value": value,
                "initialValue": value,
                "isSync": not secret,
                "securityType": "secret" if secret else "default",
                "description": "仅填本地值，禁止提交到 Git 或报告" if secret else "",
            }
        )
    return {
        "name": {
            "private_readonly": "Aether_内部只读检查",
            "quality_identity": "Aether_隔离身份回归",
            "isolated_acceptance": "Aether_完整业务验收_待接入",
        }[kind],
        "variables": variables,
        "parameters": {"cookie": [], "query": [], "header": [], "body": []},
        "type": "normal",
        "visibility": "protected",
        "ordering": 10,
        "tags": [],
        "advancedSettings": {},
        "requestProxyAgentSettings": {},
        "baseUrl": "http://127.0.0.1:1",
        "baseUrls": bases,
        "socketioBaseUrls": {},
        "websocketBaseUrls": {},
    }


def step(name, method, path, module, before, after, body=None, api_id=None):
    case = {
        "name": name,
        "method": method,
        "path": "http://127.0.0.1:1/BLOCKED",
        "type": "DEBUG_CASE",
        "auth": {"type": "noauth"},
        "parameters": {"query": [], "header": [], "cookie": [], "path": []},
        "requestBody": {
            "type": "none" if body is None else "application/json",
            "data": "" if body is None else json.dumps(body),
            "parameters": [],
            "generateMode": "example",
        },
        "preProcessors": [processor(before, "prerequest")],
        "postProcessors": [processor(after, "test")],
        "advancedSettings": {"disabledSystemHeaders": {}},
        "commonParameters": {"query": [], "header": [], "cookie": [], "body": []},
        "inheritPreProcessors": {},
        "inheritPostProcessors": {},
        "inheritPreProcessorsSnapshot": [],
        "inheritPostProcessorsSnapshot": [],
        "moduleId": int(module),
    }
    result = {
        "name": name,
        "number": 1,
        "type": "http",
        "bind": False,
        "disable": False,
        "bindType": "API",
        "syncMode": "MANUAL",
        "httpApiCase": case,
    }
    if api_id:
        case["apiId"] = int(api_id)
        result["bindId"] = int(api_id)
    return result


def scenario(name, steps, description):
    for i, item in enumerate(steps):
        item["number"] = i + 1
    return {
        "name": name,
        "steps": steps,
        "tags": [],
        "options": {},
        "priority": 1,
        "ordering": 0,
        "description": description,
        "preProcessors": [],
        "postProcessors": [],
        "children": [],
        "performanceTestOptions": {},
        "apiTestDataSets": [],
    }


def flatten(nodes):
    for node in nodes:
        for item in node.get("items", []):
            if "api" in item:
                yield item
            else:
                yield from flatten([item])
        yield from flatten(node.get("children", []))


def build(source, docs):
    package = {key: [] for key, value in source.items() if isinstance(value, list)}
    package.update(
        apifoxProject=source["apifoxProject"],
        info={"name": "Aether 持续回归"},
        **{"$schema": source["$schema"]},
    )
    modules = {m["name"]: int(m["id"]) for m in source["moduleSettings"]}
    package["moduleSettings"] = [
        copy.deepcopy(m) for m in source["moduleSettings"] if m["name"].startswith("Aether")
    ]
    apis = {}
    for folder in source["apiCollection"]:
        for item in flatten([folder]):
            if "api" in item:
                api = item["api"]
                apis[(int(api["moduleId"]), api["method"], api["path"])] = api
    identity = modules["Aether_身份回归"]
    p3 = modules["Aether_记忆核心P3"]
    admin = modules["Aether_云端管理API"]
    agent = modules["Aether_Agent工作流"]
    groups = []
    identity_scenarios = []
    for item in [i for f in source["apiCollection"] for i in flatten([f]) if "api" in i]:
        api = item["api"]
        mid, method, path = int(api["moduleId"]), api["method"], api["path"]
        if mid != identity:
            continue
        before = "\n".join(p["data"] for p in api["preProcessors"] if p["type"] == "customScript")
        after = "\n".join(p["data"] for p in api["postProcessors"] if p["type"] == "customScript")
        name = next(
            item["name"]
            for folder in source["apiCollection"]
            for item in flatten([folder])
            if item.get("api", {}).get("id") == api["id"]
        )
        s = step(name, method, path, identity, before, after, api_id=api["id"])
        case = api["cases"][0]
        s["httpApiCase"]["parameters"] = copy.deepcopy(case["parameters"])
        s["httpApiCase"]["requestBody"] = copy.deepcopy(case["requestBody"])
        identity_scenarios.append(
            scenario(
                name,
                [s],
                "隔离后台正式登录。每例独立创建令牌并注销；缺少本地密码为 BLOCKED。SEC-01/05/06 部分覆盖。",  # noqa: E501
            )
        )
    groups.append({"name": "02_身份与权限回归", "children": [], "items": identity_scenarios})
    smoke = []

    def probe(name, module, method, path, kind, base, assertion, body=None):
        api = apis.get((module, method, path))
        fullpath = path
        if kind == "public_readonly":
            fullpath = ("/ruoyi-api" if module == admin else "/ruoyi-agent") + path
        return scenario(
            name,
            [
                step(
                    name,
                    method,
                    path,
                    module,
                    guard(kind, fullpath, base),
                    assertion,
                    body,
                    api["id"] if api else None,
                )
            ],
            "单次独立请求；检查业务语义，不能用于推导完整功能通过。原生运行结果与 CLI 结果分别记录。",  # noqa: E501
        )

    smoke.append(
        probe(
            "DEP-01/公网身份拒绝",
            admin,
            "get",
            "/aether/identity/self",
            "public_readonly",
            "public_origin",
            "pm.test('匿名身份拒绝且不泄露用户',()=>{pm.expect(pm.response.code).to.eql(200);const b=pm.response.json();pm.expect(b.code).to.eql(401);pm.expect(b.data==null).to.eql(true);});",  # noqa: E501
        )
    )
    for path in ["/auth/me", "/chat-api/conversations", "/memory-api/catalog"]:
        if (agent, "get", path) in apis:
            smoke.append(
                probe(
                    "SEC-01/Agent匿名拒绝" + path,
                    agent,
                    "get",
                    path,
                    "public_readonly",
                    "public_origin",
                    "pm.test('匿名返回401且无会话凭据',()=>{pm.expect(pm.response.code).to.eql(401);pm.expect(pm.response.json()).not.to.have.property('accessToken');});",
                )
            )
    groups.append({"name": "01_部署后公网冒烟", "children": [], "items": smoke})
    internal = []
    for path, field, value in [
        ("/p3/live", "liveness", "alive"),
        ("/p3/readyz", "readiness", "ready"),
    ]:
        internal.append(
            probe(
                "DEP-01/" + field,
                p3,
                "get",
                path,
                "private_readonly",
                "p3_base",
                f"pm.test('{field}',()=>{{pm.expect(pm.response.code).to.eql(200);pm.expect(pm.response.json().{field}).to.eql('{value}');}});",
            )
        )
    for path in ["/p3/auth/me", "/p3/memories", "/p3/runtime", "/p3/capabilities", "/p3/health"]:
        if (p3, "get", path) in apis:
            internal.append(
                probe(
                    "SEC-01/P3匿名拒绝" + path,
                    p3,
                    "get",
                    path,
                    "private_readonly",
                    "p3_base",
                    "pm.test('认证边界返回精确401',()=>{pm.expect(pm.response.code).to.eql(401);pm.expect(pm.response.json().code).to.eql('UNAUTHENTICATED');});",
                )
            )
    groups.append({"name": "03_内部健康与认证边界", "children": [], "items": internal})
    lifecycle_before = (
        guard("isolated_acceptance", "/admin-api/aether/identity/login", "auth_base")
        + r"""
// This switch is a source-controlled admission gate, not an environment variable.
// Remove only after full-stack isolation and provider-side budget enforcement
// have evidence and the corresponding readiness checks have been implemented.
const fullStackAdmitted = false;
if(!fullStackAdmitted) block('G0: full P3 isolation and provider budget enforcement are not provisioned');
if(pm.environment.get('allow_writes')!=='true' || pm.environment.get('isolation_verified')!=='true') block('full isolated environment is not admitted');
for(const key of ['candidate_sha','image_digest','test_username','test_password','expected_user_id','expected_tenant_id','expected_p3_user_id','expected_p3_tenant_id']) {
  if(!pm.environment.get(key)) block('missing '+key);
}
for(const key of ['max_requests','max_model_cost','max_resource_cost']) {
  const value=Number(pm.environment.get(key));
  if(!Number.isFinite(value) || value<=0) block('invalid declared budget '+key);
}
if(!/^http:\/\/127\.0\.0\.1:\d+$/.test(pm.environment.get('p3_base')||'')) block('missing isolated P3 forward');
pm.request.body.update(JSON.stringify({username:pm.environment.get('test_username'),password:pm.environment.get('test_password')}));
"""  # noqa: E501
    )
    lifecycle_script = Path(__file__).with_name("apifox-lifecycle.js")
    if lifecycle_script.exists():
        groups.append(
            {
                "name": "04_记忆完整链路_隔离环境必需",
                "children": [],
                "items": [
                    scenario(
                        "REM-01/REC-01/REM-08 保存_回读_召回_删除",
                        [
                            step(
                                "正式登录并执行独立记忆旅程",
                                "post",
                                "/aether/identity/login",
                                admin,
                                lifecycle_before,
                                lifecycle_script.read_text(encoding="utf-8"),
                                {},
                                apis[(admin, "post", "/aether/identity/login")]["id"],
                            )
                        ],
                        "独立合成数据。校验保存回执、正文、同操作ID重试、ContextPack来源、删除后禁止回读；轮询沿用原 job ID。失败保留 run_id 与对象ID供处置。属于部分功能覆盖，不等于五条浏览器E2E通过。",  # noqa: E501
                    )
                ],
            }
        )
    package["apiTestCaseCollection"] = [{"id": 0, "name": "Root", "children": groups, "items": []}]
    blocked = {str(m): "http://127.0.0.1:1" for m in modules.values()}
    for kind in ["quality_identity", "private_readonly", "isolated_acceptance"]:
        bases = blocked.copy()
        if kind == "quality_identity":
            bases[str(identity)] = "http://127.0.0.1:14880"
        if kind == "private_readonly":
            bases[str(p3)] = "http://127.0.0.1:14881"
        package["environments"].append(environment(kind, bases))
    package["docCollection"] = [
        {
            "name": "根目录",
            "moduleId": admin,
            "children": [],
            "items": [
                {
                    "name": p.stem,
                    "sidebarTitle": "",
                    "content": p.read_text(encoding="utf-8"),
                    "type": "",
                    "tags": [],
                    "visibility": "INHERITED",
                    "moduleId": admin,
                }
                for p in sorted(docs.glob("*.md"))
            ],
        }
    ]
    # Native import resolves step API IDs through imported API definitions.
    # Retain the existing definitions/models so these are updated in place.
    mids = {int(m["id"]) for m in package["moduleSettings"]}
    for key in [
        "apiCollection",
        "schemaCollection",
        "securitySchemeCollection",
        "responseCollection",
    ]:
        package[key] = [copy.deepcopy(f) for f in source[key] if int(f.get("moduleId", 0)) in mids]
    existing_groups = source["apiTestCaseCollection"][0].get("children", [])
    existing_scenarios = {s["name"]: s for g in existing_groups for s in g.get("items", [])}
    existing_docs = {
        d["name"]: d for f in source.get("docCollection", []) for d in f.get("items", [])
    }
    case_sequence = 900000001
    for g in groups:
        old = next((f for f in existing_groups if f["name"] == g["name"]), {})
        if "id" in old:
            g["id"] = old["id"]
        for s in g["items"]:
            if s["name"] in existing_scenarios:
                s["id"] = existing_scenarios[s["name"]]["id"]
            for st in s["steps"]:
                st["httpApiCase"]["id"] = case_sequence
                case_sequence += 1
    for i, env in enumerate(package["environments"]):
        env["id"] = str(90001000 + i)
    for d in package["docCollection"][0]["items"]:
        if d["name"] in existing_docs:
            d["id"] = existing_docs[d["name"]]["id"]
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--docs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    package = build(json.loads(args.source.read_text(encoding="utf-8-sig")), args.docs)
    args.output.write_text(json.dumps(package, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "Native scenarios:",
        sum(len(g["items"]) for g in package["apiTestCaseCollection"][0]["children"]),
    )
