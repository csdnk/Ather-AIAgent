"""Business acceptance scenarios with native visible HTTP steps and frozen public facts.

Build incrementally from an actual native project export. No credentials included.
The source admission remains closed until the functional environment is verified.
"""

import argparse
import copy
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import apifox_native as native

ENGINE = Path(__file__).with_name("apifox-business-step.js").read_text(encoding="utf-8")
FOLDER = "08_真实业务_记忆调度召回"
SUITE = "Aether_真实业务总回归_记忆调度召回"


def walk(items):
    for item in items:
        yield item
        yield from walk(item.get("children", []))


def definitions(data):
    def st(action, name, path, method="get", **kw):
        return dict(action=action, name=name, path=path, method=method, **kw)

    def begin():
        return [
            st(
                "login",
                "正式账号登录",
                "/admin-api/aether/identity/login",
                "post",
                base="auth_base",
                auth=False,
            ),
            st("identity", "核验P3用户租户映射", "/p3/auth/me"),
            st("capabilities", "核验真实模型、存储及Temporal执行器", "/p3/capabilities"),
        ]

    def operation(action, path, label, **kw):
        operation_key = (
            "save"
            if action == "replay"
            else action + ("_" + kw.get("tag", "") if action == "recall" else "")
        )
        kind = (
            "recall.execute"
            if action == "recall"
            else "remember.correct"
            if action == "correct"
            else "remember.save"
        )
        cfg = {k: v for k, v in kw.items() if k != "tag"}
        return [
            st(action, label, path, "post", operation=operation_key, **cfg),
            st(
                "lookup",
                "按原操作ID查找唯一任务",
                "/p3/operation-requests/{operation_id}?kind=" + kind,
                sameJob=action == "replay",
            ),
        ] + [
            st(
                "result",
                f"{label} 原任务结果 {i}/20",
                "/p3/operations/{job_id}/result",
                kind=action,
                final=i == 20,
                **cfg,
            )
            for i in range(1, 21)
        ]

    def save():
        return operation("save", "/p3/remember", "写入有来源的真实材料")

    def recall(**kw):
        return operation("recall", "/p3/recall", "按冻结问题召回并核验事实", **kw)

    def snapshot():
        return st("snapshot", "读取实际版本与并发修订", "/p3/remember/{memory_id}")

    def body():
        return st("body", "核对完整原文", "/p3/remember/body", "post")

    def end():
        return [
            st("logout", "注销本轮令牌", "/admin-api/system/auth/logout", "post", base="auth_base")
        ]

    def delete():
        return [
            snapshot(),
            st(
                "delete",
                "删除本轮对象并记录清理状态",
                "/p3/remember/{memory_id}/delete",
                "post",
                operation="delete",
            ),
            st("excluded", "删除后正文不可读取", "/p3/remember/body", "post", reason="deleted"),
        ]

    flows = []

    def add(key, name, ids, steps, index=0, cleanup=True):
        seq = begin() + save() + steps + (delete() if cleanup else []) + end()
        datum = copy.deepcopy(data[index])
        for i, s in enumerate(seq):
            s.update(key=key, index=i, first=i == 0, last=i == len(seq) - 1, data=datum)
        flows.append(dict(key=key, name=name, cases=ids, data=datum, steps=seq))

    add(
        "public-working",
        "REM-01/02 REC-01 气象顾问_保存回读与同会话召回",
        ["REM-01", "REM-02", "REC-01"],
        [body()] + recall(),
    )
    add(
        "idempotent-scheduling",
        "REM-02 REC-06 采集网络重试_原任务与唯一保存",
        ["REM-02", "REC-06"],
        operation("replay", "/p3/remember", "同操作ID和原输入重试")
        + [st("task", "核对原任务成功及副作用确认", "/p3/tasks/{job_id}"), body()],
    )
    processing = [
        st(
            "consolidate",
            "通过正式接口提交长期加工",
            "/p3/remember/consolidate",
            "post",
            operation="consolidate",
        )
    ] + [
        st(
            "processing",
            f"检查派生对象及加工任务 {i}/20",
            "/p3/remember/{memory_id}/processing",
            final=i == 20,
        )
        for i in range(1, 21)
    ]
    add(
        "longterm-new-session",
        "REM-03/06 REC-02 农业研究_长期加工后新会话召回",
        ["REM-03", "REM-06", "REC-02"],
        processing
        + recall(longterm=True)
        + [
            st("source-meta", "清理前读取来源实际修订", "/p3/sources/{source_id}"),
            st(
                "revoke",
                "撤销本轮来源及全部派生材料",
                "/p3/sources/{source_id}/revoke",
                "post",
                operation="revoke",
            ),
        ]
        + recall(longterm=True, excludedSource=True, tag="cleanup"),
        index=2,
        cleanup=False,
    )
    add(
        "correction",
        "REM-07 REC-01 知识维护_版本更正与当前事实召回",
        ["REM-07", "REC-01"],
        [snapshot()]
        + operation("correct", "/p3/remember/{memory_id}/correct", "依据冻结来源提交新版本")
        + [body()]
        + recall(),
        index=4,
    )
    add(
        "archive-activate",
        "REM-11 季节资料_归档停用与重新启用",
        ["REM-11"],
        [
            snapshot(),
            st(
                "lifecycle",
                "归档资料",
                "/p3/remember/{memory_id}/lifecycle",
                "post",
                target="archived",
                operation="archive",
            ),
        ]
        + recall(empty=True, tag="archived")
        + [
            snapshot(),
            st(
                "lifecycle",
                "重新启用资料",
                "/p3/remember/{memory_id}/lifecycle",
                "post",
                target="active",
                operation="activate",
            ),
            body(),
        ]
        + recall(tag="active"),
        index=1,
    )
    add(
        "legal-hold",
        "REM-11 资料保留_设置法律保留并回读策略",
        ["REM-11"],
        [
            snapshot(),
            st(
                "retention",
                "设置保留策略",
                "/p3/remember/{memory_id}/retention",
                "post",
                operation="retention",
            ),
            st("retention-read", "回读保留策略", "/p3/remember/{memory_id}/retention"),
            body(),
        ],
        index=3,
    )
    add(
        "source-revoke",
        "REM-12 SEC-05 知识撤回_来源撤销与历史ContextPack失效",
        ["REM-12", "SEC-05"],
        recall()
        + [
            st("source-meta", "读取来源实际修订", "/p3/sources/{source_id}"),
            st(
                "revoke",
                "撤回本轮来源",
                "/p3/sources/{source_id}/revoke",
                "post",
                operation="revoke",
            ),
            st("invalidated", "旧召回结果不得再次提供", "/p3/recalls/{recall_id}/result"),
        ]
        + recall(empty=True, tag="revoked"),
        cleanup=False,
    )
    add(
        "cross-tenant",
        "SEC-02 咨询隔离_租户B不能读取租户A资料",
        ["SEC-02"],
        [
            st(
                "login",
                "租户B正式登录",
                "/admin-api/aether/identity/login",
                "post",
                base="auth_base",
                auth=False,
                slot="B",
            ),
            st("cross-body", "B携带A真实对象引用必须拒绝", "/p3/remember/body", "post", slot="B"),
            st(
                "logout",
                "注销租户B令牌",
                "/admin-api/system/auth/logout",
                "post",
                base="auth_base",
                slot="B",
            ),
            body(),
        ],
    )
    add(
        "empty-new-session",
        "REC-03 咨询边界_新会话没有Working上下文",
        ["REC-03"],
        recall(empty=True, emptySession=True),
    )
    add("token-budget", "REC-05 大材料问答_极小预算不得溢出", ["REC-05"], recall(budget=1), index=6)
    add(
        "source-read",
        "REC-09 合规溯源_按来源回读完整原始材料",
        ["REC-09"],
        [st("source-body", "来源全文与冻结公开材料一致", "/p3/sources/read-range", "post"), body()],
        index=6,
    )
    add(
        "scheduler-auth",
        "SEC-06 普通业务账号不能读取调度内部诊断",
        ["SEC-06"],
        [st("denied", "诊断权限与普通读权限分离", "/p3/operate/memories/{memory_id}"), body()],
    )
    add(
        "hot-scheduling",
        "OPS-01/03 频繁咨询_自动升温与真实热副本回读",
        ["OPS-01", "OPS-03"],
        recall(tag="hot1")
        + recall(tag="hot2")
        + [
            st(
                "login",
                "登录已获该对象诊断权限的测试身份",
                "/admin-api/aether/identity/login",
                "post",
                base="auth_base",
                auth=False,
                slot="D",
            )
        ]
        + [
            st(
                "placement",
                f"核查自动升温动作与提供方回读证据 {i}/20",
                "/p3/operate/memories/{memory_id}",
                slot="D",
                final=i == 20,
            )
            for i in range(1, 21)
        ]
        + [
            st(
                "logout",
                "注销诊断身份",
                "/admin-api/system/auth/logout",
                "post",
                base="auth_base",
                slot="D",
            ),
            body(),
        ],
    )
    return flows


def build(source, data):
    package = copy.deepcopy(source)
    modules = {m["name"]: int(m["id"]) for m in source["moduleSettings"]}
    mids = {modules["Aether_记忆核心P3"], modules["Aether_云端管理API"]}
    apis = {
        (int(f["moduleId"]), a["api"]["method"], a["api"]["path"]): a["api"]
        for f in source["apiCollection"]
        for a in native.flatten([f])
    }
    old = {
        s["name"]: s
        for folder in walk(source["apiTestCaseCollection"])
        for s in folder.get("items", [])
    }
    scenarios = []
    for flow in definitions(data):
        steps = []
        for i, cfg in enumerate(flow["steps"]):
            mid = modules[
                "Aether_记忆核心P3" if cfg["path"].startswith("/p3/") else "Aether_云端管理API"
            ]
            route = cfg["path"].split("?")[0].removeprefix("/admin-api")
            api = apis[(mid, cfg["method"], route.replace("/tasks/{job_id}", "/tasks/{task_id}"))]
            header = "const cfg=" + json.dumps(cfg, ensure_ascii=False) + ";\n"
            step = native.step(
                cfg["name"],
                cfg["method"],
                route,
                mid,
                header + "const phase='pre';\n" + ENGINE,
                header + "const phase='post';\n" + ENGINE,
                {} if cfg["method"] == "post" else None,
                api["id"],
            )
            step["httpApiCase"]["id"] = 920000000 + len(scenarios) * 200 + i
            steps.append(step)
        s = native.scenario(
            flow["name"],
            steps,
            "真实公开业务材料，非真实客户私有数据。独立run_id；正式登录/写入建立对象；原任务20次查询、加工20次观察，未完成不计通过。G0当前BLOCKED：缺独立P3、Ceph及零新增费用模型；禁止仅改变量绕过准入。删除/撤销后记录清理状态；失败保留对象供对账。"
            + json.dumps(
                {
                    "catalog": flow["cases"],
                    "source": flow["data"]["source_url"],
                    "dataset_id": flow["data"]["dataset_id"],
                },
                ensure_ascii=False,
            ),
        )
        s["options"] = dict(
            environmentId=49504582,
            useDataSetId=-1,
            iterationCount=1,
            threadCount=1,
            runnerId=0,
            onError="ignore",
            delayItem=250,
            saveReportDetail="none",
            saveVariables=False,
            readGlobalCookie=False,
            saveGlobalCookie=False,
        )
        if s["name"] in old:
            s["id"] = old[s["name"]]["id"]
        scenarios.append(s)
    folder = {"name": FOLDER, "children": [], "items": scenarios}
    oldfolder = next(
        (f for f in walk(source["apiTestCaseCollection"]) if f["name"] == FOLDER), None
    )
    if oldfolder:
        folder["id"] = oldfolder["id"]
    package["apiTestCaseCollection"] = [dict(id=0, name="Root", items=[], children=[folder])]
    package["moduleSettings"] = [m for m in source["moduleSettings"] if int(m["id"]) in mids]
    for name in [
        "apiCollection",
        "schemaCollection",
        "responseCollection",
        "securitySchemeCollection",
    ]:
        package[name] = [f for f in source.get(name, []) if int(f.get("moduleId", 0)) in mids]
    for name in [
        "environments",
        "docCollection",
        "socketCollection",
        "customEndpointCollection",
        "webSocketCollection",
        "socketIOCollection",
        "mcpClientCollection",
        "requestCollection",
        "databaseConnections",
        "globalVariables",
        "commonScripts",
        "testCaseReferences",
        "apiTestDataCollection",
        "testSuiteCollection",
    ]:
        package[name] = []
    if all("id" in s for s in scenarios):
        suite = dict(
            name=SUITE,
            description="13条真实业务回归。完整环境未准入则BLOCKED，不能把请求连接失败算作产品失败。仅功能测试，不自动执行压力或故障注入。",
            priority=1,
            ordering=12,
            folderId=0,
            tags=[],
            items=[
                dict(
                    id=str(uuid.uuid5(uuid.NAMESPACE_URL, SUITE)),
                    name="真实业务",
                    type="STATIC_TEST_SCENARIO",
                    testScenarios=[dict(id=s["id"], options={}) for s in scenarios],
                    options={},
                )
            ],
            options=dict(runMode="serial", runnerId=0, environmentId=49504582),
        )
        oldsuite = next(
            (
                x
                for f in walk(source.get("testSuiteCollection", []))
                for x in f.get("items", [])
                if x["name"] == SUITE
            ),
            None,
        )
        if oldsuite:
            suite["id"] = oldsuite["id"]
        package["testSuiteCollection"] = [dict(id=0, name="Root", children=[], items=[suite])]
    return package


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("source")
    p.add_argument("dataset")
    p.add_argument("output")
    a = p.parse_args()
    package = build(
        json.loads(Path(a.source).read_text(encoding="utf-8")),
        json.loads(Path(a.dataset).read_text(encoding="utf-8")),
    )
    Path(a.output).write_text(json.dumps(package, ensure_ascii=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "scenarios": len(package["apiTestCaseCollection"][0]["children"][0]["items"]),
                "steps": sum(
                    len(s["steps"])
                    for s in package["apiTestCaseCollection"][0]["children"][0]["items"]
                ),
            }
        )
    )
