"""Additive native Apifox package for bounded component benchmarks; no secrets."""

import argparse
import copy
import json
from pathlib import Path

from apifox_native import environment, scenario, step

MODULE = 8677586
FOLDER = "07_Embedding与压缩专项"


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get("items", []))
        yield from walk(node.get("children", []))


def build(source):
    package = {key: [] for key, value in source.items() if isinstance(value, list)}
    package.update(
        apifoxProject=source["apifoxProject"],
        info={"name": "Aether 组件专项自动化"},
        **{"$schema": source["$schema"]},
    )
    package["moduleSettings"] = [
        copy.deepcopy(x) for x in source["moduleSettings"] if int(x["id"]) == MODULE
    ]
    env = environment("quality_identity", {})
    env.update(name="Aether_组件专项_本机执行器", baseUrl="http://127.0.0.1:14884", ordering=20)
    existing_env = next((e for e in source["environments"] if e["name"] == env["name"]), None)
    if existing_env:
        env["id"] = existing_env["id"]
    env["variables"] = [
        {
            "name": "benchmark_base",
            "value": "http://127.0.0.1:14884",
            "initialValue": "http://127.0.0.1:14884",
            "isSync": True,
            "securityType": "default",
        },
        {
            "name": "benchmark_token",
            "value": "",
            "initialValue": "",
            "isSync": False,
            "securityType": "secret",
            "description": "仅本地值；启动脚本生成；不得同步或上传",
        },
        {
            "name": "benchmark_assert_release",
            "value": "false",
            "initialValue": "false",
            "isSync": True,
            "securityType": "default",
            "description": "当前只验计算；正式门槛未冻结。设true时正式门禁BLOCKED必失败",
        },
    ]
    package["environments"] = [env]
    request_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["profile", "request_id"],
        "properties": {
            "profile": {"type": "string", "enum": ["embedding", "compression"]},
            "request_id": {"type": "string", "pattern": "^[a-zA-Z0-9_-]{1,80}$"},
        },
    }
    report_schema = {
        "type": "object",
        "required": ["job_id", "request_id", "profile", "status"],
        "properties": {
            "job_id": {"type": "string", "format": "uuid"},
            "request_id": {"type": "string"},
            "profile": {"type": "string"},
            "status": {"type": "string", "enum": ["RUNNING", "PASS", "FAIL", "BLOCKED"]},
            "report": {
                "type": "object",
                "properties": {
                    "run_id": {"type": "string"},
                    "status": {"type": "string"},
                    "release_gate": {"type": "string"},
                    "component_gate": {
                        "type": "object",
                        "properties": {
                            "status": {"type": "string"},
                            "checks": {"type": "array"},
                            "scope": {"type": "string"},
                        },
                    },
                    "reason": {"type": "string"},
                    "metrics": {
                        "type": "object",
                        "properties": {
                            key: {"type": ["number", "null"]}
                            for key in [
                                "completed_valid",
                                "failed",
                                "not_completed",
                                "valid_items_per_second",
                                "valid_tokens_per_second",
                                "p50_ms",
                                "p95_ms",
                                "p99_ms",
                            ]
                        },
                    },
                    "compression": {"type": "object"},
                    "model": {"type": "object"},
                    "candidate": {"type": "string"},
                    "dataset_sha256": {"type": "string"},
                },
            },
        },
    }
    package["schemaCollection"] = [
        {
            "name": FOLDER,
            "moduleId": MODULE,
            "items": [
                {
                    "name": "BenchmarkSubmit",
                    "schema": {"jsonSchema": request_schema},
                    "description": "固定配置任务；不接受命令、路径、并发或目标地址。",
                },
                {
                    "name": "BenchmarkJobReport",
                    "schema": {"jsonSchema": report_schema},
                    "description": "计算与正式门禁分别判定；RUNNING不计成功。",
                },
            ],
        }
    ]
    routes = [
        ("执行器能力与限额", "get", "/v1/capabilities"),
        ("提交固定专项任务", "post", "/v1/jobs"),
        ("轮询原专项任务", "get", "/v1/jobs/{job_id}"),
        ("读取专项终态报告", "get", "/v1/jobs/{job_id}/report"),
    ]
    apis = []
    existing_apis = {
        x["api"]["path"]: x["api"]
        for x in walk(source["apiCollection"])
        if "api" in x and int(x["api"].get("moduleId", 0)) == MODULE
    }
    for name, method, path in routes:
        api = {
            "method": method,
            "path": path,
            "name": name,
            "moduleId": MODULE,
            "status": "testing",
            "tags": [FOLDER],
            "auth": {"type": "bearer", "bearer": {"token": "{{benchmark_token}}"}},
            "parameters": {
                "path": [{"name": "job_id", "required": True, "type": "string", "enable": True}]
                if "{job_id}" in path
                else [],
                "query": [],
                "cookie": [],
                "header": [],
            },
            "requestBody": {
                "type": "application/json",
                "jsonSchema": request_schema,
                "parameters": [],
            }
            if method == "post"
            else {"type": "none", "parameters": []},
            "responses": [
                {
                    "id": "200",
                    "code": "200",
                    "name": "任务或能力",
                    "jsonSchema": report_schema if "jobs" in path else {"type": "object"},
                    "contentType": "json",
                }
            ],
            "description": "测试专用本机入口 http://127.0.0.1:14884；与产品API分开。POST202仅受理；必须查询原job_id并核验report。1并行/每次启动最多3任务/2小时有效，真实计算在现有测试命名空间。",
            "preProcessors": [],
            "postProcessors": [],
            "cases": [],
        }
        if path in existing_apis:
            api["id"] = existing_apis[path]["id"]
        apis.append({"name": name, "api": api})
    package["apiCollection"] = [{"name": FOLDER, "moduleId": MODULE, "items": apis}]
    common = """pm.request.url.update('http://127.0.0.1:1/BLOCKED');
function block(m){pm.test('BLOCKED: '+m,()=>{throw new Error(m)});if(pm.execution&&pm.execution.skipRequest)pm.execution.skipRequest();throw new Error(m);}
if(pm.environment.get('benchmark_base')!=='http://127.0.0.1:14884')block('请选择组件专项本机环境');
const token=pm.environment.get('benchmark_token');if(!token)block('请启动本机执行器并填写token本地值');
pm.request.headers.upsert({key:'Authorization',value:'Bearer '+token});
pm.request.headers.upsert({key:'Content-Type',value:'application/json'});
"""  # noqa: E501
    scenes = []
    for kind, case_id in [("embedding", "PERF-02"), ("compression", "PERF-04")]:
        steps = []

        def add(name, method, path_expr, after, body=None, extra="", target_steps=steps):
            before = (
                common
                + extra
                + '\npm.request.url.update("http://127.0.0.1:14884"+'
                + path_expr
                + ");"
            )
            route = (
                "/v1/jobs"
                if method == "post"
                else "/v1/capabilities"
                if "capabilities" in path_expr
                else "/v1/jobs/{job_id}/report"
                if "/report" in path_expr
                else "/v1/jobs/{job_id}"
            )
            api_id = existing_apis.get(route, {}).get("id")
            target_steps.append(step(name, method, "", MODULE, before, after, body, api_id))

        add(
            "检查受控执行器与限额",
            "get",
            "'/v1/capabilities'",
            "pm.test('受控单任务执行器',()=>{pm.response.to.have.status(200);const r=pm.response.json();pm.expect(r.max_parallel).eq(1);pm.expect(r.new_paid_resources).eq(false)});",  # noqa: E501
            extra="pm.variables.set('benchmark_request_id',pm.variables.replaceIn('{{$guid}}'));pm.variables.unset('benchmark_job_id');pm.variables.set('benchmark_terminal','false');",
        )
        submit_after = """const j=pm.response.json();pm.test('保存本次原任务身份',()=>{pm.expect([200,202]).includes(pm.response.code);pm.expect(j.request_id).eq(pm.variables.get('benchmark_request_id'));pm.expect(j.job_id).match(/^[a-f0-9-]{36}$/)});if(j.job_id){pm.variables.set('benchmark_job_id',j.job_id);pm.variables.set('benchmark_terminal',String(j.status!=='RUNNING'));}"""  # noqa: E501
        body = {"profile": kind, "request_id": "{{benchmark_request_id}}"}
        add("提交真实" + kind + "计算任务", "post", "'/v1/jobs'", submit_after, body)
        add(
            "相同请求ID重试必须返回原任务",
            "post",
            "'/v1/jobs'",
            "const j=pm.response.json();pm.test('不重复计算',()=>{pm.expect([200,202]).includes(pm.response.code);pm.expect(j.job_id).eq(pm.variables.get('benchmark_job_id'))});",  # noqa: E501
            body,
            "if(!pm.variables.get('benchmark_job_id'))block('提交失败，禁止重试创建');",
        )
        for index in range(40):
            extra = "if(!pm.variables.get('benchmark_job_id'))block('缺少原任务ID');"
            add(
                f"等待原任务完成 {index + 1}/40（最多20秒）",
                "get",
                "'/v1/jobs/'+pm.variables.get('benchmark_job_id')+'?wait=20'",
                "const j=pm.response.json();pm.test('原任务状态合法',()=>{pm.response.to.have.status(200);pm.expect(j.job_id).eq(pm.variables.get('benchmark_job_id'));pm.expect(['RUNNING','PASS','FAIL','BLOCKED']).includes(j.status)});pm.variables.set('benchmark_terminal',String(j.status!=='RUNNING'));",  # noqa: E501
                extra=extra,
            )
        assertion = """const j=pm.response.json();const r=j.report||{};
pm.test('取得原任务终态报告',()=>{pm.response.to.have.status(200);pm.expect(j.job_id).eq(pm.variables.get('benchmark_job_id'));pm.expect(['PASS','FAIL','BLOCKED']).includes(j.status)});
pm.test('真实计算成功（BLOCKED须补依赖，不能豁免）',()=>{pm.expect(j.status,r.reason||JSON.stringify(r.diagnostics||[])).eq('PASS');pm.expect(r.run_id).a('string');pm.expect(r.metrics.completed_valid).above(0);pm.expect(r.metrics.failed).eq(0);pm.expect(r.metrics.not_completed).eq(0);pm.expect(r.metrics.valid_items_per_second).above(0)});
if(pm.environment.get('benchmark_assert_release')==='true'){pm.test('正式专项门禁',()=>{pm.expect(r.component_gate.status).eq('PASS');pm.expect(r.release_gate).eq('PASS')});}
console.log('AETHER_COMPONENT_REPORT '+JSON.stringify({job_id:j.job_id,run_id:r.run_id,status:j.status,metrics:r.metrics,compression:r.compression,component_gate:r.component_gate,release_gate:r.release_gate,reason:r.reason}));
"""  # noqa: E501
        add(
            "读取报告并断言有效样本、错误率和指标",
            "get",
            "'/v1/jobs/'+pm.variables.get('benchmark_job_id')+'/report'",
            assertion,
            extra="if(!pm.variables.get('benchmark_job_id'))block('缺少原任务ID');",
        )
        scene = scenario(
            f"{case_id} {kind} 提交_幂等_轮询_指标报告",
            steps,
            "44个原生HTTP步骤：执行器检查、提交、幂等、40次原任务查询、报告断言。在途查询最多等待20秒，终态后立即返回，并继续验证原任务可重复读取。请选择组件专项本机环境、1线程1轮、请求超时30秒以上。真实现有本地模型；不调用收费模型。组件计算成功不等于正式性能门禁；压缩为预处理产物，不代表物理存储或语义效果。",
        )
        scene["options"] = {
            "iterationCount": 1,
            "threadCount": 1,
            "runnerId": 0,
            "onError": "ignore",
            "saveVariables": False,
            "useDataSetId": -1,
        }
        if existing_env:
            scene["options"]["environmentId"] = int(existing_env["id"])
        old = next(
            (x for x in walk(source["apiTestCaseCollection"]) if x.get("name") == scene["name"]),
            None,
        )
        if old:
            scene["id"] = old["id"]
        scenes.append(scene)
    package["apiTestCaseCollection"] = [{"name": FOLDER, "children": [], "items": scenes}]
    if all(s.get("id") for s in scenes):
        package["testSuiteCollection"] = [
            {
                "name": "Root",
                "children": [],
                "items": [
                    {
                        "name": "Aether_组件专项_Embedding与压缩",
                        "description": "每次涉及模型/分块/压缩变更后运行；计算结果和发布门禁分别判定。",  # noqa: E501
                        "priority": 1,
                        "items": [
                            {
                                "name": "两个固定专项",
                                "type": "STATIC_TEST_SCENARIO",
                                "testScenarios": [{"id": s["id"], "options": {}} for s in scenes],
                                "options": {},
                            }
                        ],
                        "options": {
                            "runMode": "serial",
                            "runnerId": 0,
                            "environmentId": int(existing_env["id"]),
                        },
                        "tags": [],
                    }
                ],
            }
        ]
        existing_suite = next(
            (
                x
                for x in walk(source.get("testSuiteCollection", []))
                if x.get("name") == "Aether_组件专项_Embedding与压缩"
            ),
            None,
        )
        if existing_suite:
            package["testSuiteCollection"][0]["items"][0]["id"] = existing_suite["id"]
    package["docCollection"] = [
        {
            "name": FOLDER,
            "moduleId": MODULE,
            "children": [],
            "items": [
                {
                    "name": "Embedding与压缩专项运行说明",
                    "content": "# 组件专项运行\n\n启动交付目录的本机专项执行器，然后选择 Aether_组件专项_本机执行器 环境，将启动时生成的 token 填入本地值，不同步。运行本文件夹的两个44步场景。网页版连接本机可能受浏览器策略影响，建议桌面版；连接错误不是业务断言失败。\n\n每次启动最多3个新任务，1个并行，2小时自动停止；固定CPU500m，embedding内存1Gi、压缩3Gi；每任务真实计算上限180秒。重复请求不会重复计算。原任务结果保存在项目外工作区，重启后原在途任务报告FAIL。\n\n读取report中的有效样本、失败、吞吐、P50/P95/P99与压缩比；接口responseTime是编排接口耗时，不能当embedding内核耗时。正式性能门槛尚未冻结，release_gate保持BLOCKED。模型缺失时compression为BLOCKED，Apifox最终断言显示失败并给出原因。该报告不代表全项目验收。",  # noqa: E501
                }
            ],
        }
    ]
    return package


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.write_text(
        json.dumps(
            build(json.loads(args.source.read_text(encoding="utf-8"))), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
