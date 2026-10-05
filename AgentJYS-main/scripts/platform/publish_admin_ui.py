"""Build the isolated Budibase app using native editable components and REST queries.

Only the local lab is supported. No user tokens or shared API keys are stored in
screen/query definitions. Budibase supplies each signed-in user's OIDC proof.
"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

import httpx

WORKSPACE = "app_dev_e755bdd95c08474789606efb958ecb13"
APP = "workspace_app_7998ee8be7d34e3ab5714adc008e5520"
API = "http://192.168.65.254:19010/management-api/"


def component(kind, name, **props):
    return {
        "_id": uuid4().hex,
        "_component": "@budibase/standard-components/" + kind,
        "_instanceName": name,
        "_styles": {"normal": {}, "hover": {}, "active": {}, "selected": {}},
        "_children": [],
        **props,
    }


def action(kind, **parameters):
    return {"##eventHandlerType": kind, "parameters": parameters}


def result_notice(success_message):
    condition = "{{#if (eq [actions].[0].[result].[code] 200)}}"
    return action(
        "Show Notification",
        type=condition + "success{{else}}error{{/if}}",
        message=condition
        + success_message
        + "{{else}}操作未完成：请检查账号权限、输入内容或重新登录。{{/if}}",
        autoDismiss=False,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))
    if config["issuer"] != "http://localhost:19080/realms/aether-lab":
        raise ValueError("Only the isolated lab is supported")
    private = json.loads((Path(config["identity_lab_directory"]) / "private.json").read_text())
    with httpx.Client(base_url="http://localhost:19000", trust_env=False, timeout=60) as client:

        def call(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            if response.status_code not in (200, 201):
                raise RuntimeError(f"Budibase {method} {path}: HTTP {response.status_code}")
            return response.json()

        session_path = args.config.parent / "budibase-builder-session.private.json"
        if session_path.exists():
            client.cookies.update(json.loads(session_path.read_text()))
        authenticated = client.get("/api/global/self")
        if (
            authenticated.status_code != 200
            or authenticated.json().get("email") != private["budibase_admin_email"]
        ):
            call(
                "POST",
                "/api/global/auth/default/login",
                json={
                    "username": private["budibase_admin_email"],
                    "password": private["budibase_admin_password"],
                },
            )
            session_path.write_text(json.dumps(dict(client.cookies)), encoding="utf-8")
        me = call("GET", "/api/global/self")
        client.headers.update({"x-csrf-token": me["csrfToken"], "x-budibase-app-id": WORKSPACE})
        sources = call("GET", "/api/datasources")
        source = next(s for s in sources if s["name"] == "Aether Platform API")
        old_queries = {q["name"]: q for q in call("GET", "/api/queries")}

        def query(name, endpoint, columns, *, write=False):
            q = {
                "name": name,
                "datasourceId": source["_id"],
                "queryVerb": "create" if write else "read",
                "readable": True,
                "parameters": [],
                "schema": {col: {"type": "string", "name": col} for col in columns},
                "fields": {
                    "path": API + endpoint,
                    "headers": {
                        "Authorization": "Bearer {{[user].[oauth2].[accessToken]}}",
                        "Content-Type": "application/json",
                    },
                    "enabledHeaders": {"Authorization": True, "Content-Type": True},
                },
            }
            if write:
                names = ("username",) if endpoint == "disable" else ("username", "display_name")
                q["parameters"] = [{"name": n, "default": ""} for n in names]
                q["fields"].update(
                    bodyType="json",
                    requestBody=json.dumps({n: "{{ " + n + " }}" for n in names}),
                )
            if name in old_queries:
                q.update({k: old_queries[name][k] for k in ("_id", "_rev")})
            return call("POST", "/api/queries", json=q)

        users = query("AetherUsers", "users", ["账号", "姓名", "角色", "租户", "状态"])
        tenants = query("AetherTenants", "tenants", ["租户名称", "状态", "用户数"])
        audit = query("AetherAudit", "audit", ["时间（UTC）", "目标账号", "操作", "同步状态"])
        edit = query("AetherEditProfile", "profile", [], write=True)
        disable = query("AetherDisableUser", "disable", [], write=True)
        old_screens = {
            s["routing"]["route"]: s
            for s in call("GET", "/api/screens")
            if s.get("workspaceAppId") == APP
        }
        backup = args.config.parent.parent / "ui-redesign" / "budibase-before-redesign.private.json"
        if not backup.exists():
            backup.write_text(
                json.dumps(
                    {"screens": old_screens, "app": call("GET", "/api/workspaceApp/" + APP)},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        links = []
        for route, title, description, q in [
            ("/", "用户管理", "管理账号资料与访问状态。系统只返回你有权管理的用户。", users),
            (
                "/tenants",
                "租户目录",
                "平台管理员可查看全部租户；租户管理员仅可查看自己所属租户。",
                tenants,
            ),
            (
                "/audit",
                "变更记录",
                "追踪账号资料变更与访问状态调整。",
                audit,
            ),
        ]:
            root = component(
                "container",
                title,
                layout="flex",
                direction="column",
                hAlign="stretch",
                vAlign="top",
                gap="M",
                size="grow",
            )
            root["_styles"]["normal"] = {
                "padding": "24px",
                "background": "#f5f7fa",
                "min-height": "100vh",
            }
            root["_children"] += [
                component(
                    "textv2", "当前位置", text="管理中心 / " + title, size="12px", color="#7c8797"
                ),
                component("textv2", "标题", text="**" + title + "**", size="28px", color="#15263a"),
                component("textv2", "说明", text=description, size="14px", color="#64746f"),
            ]
            provider = component(
                "dataprovider",
                title + "数据",
                dataSource={
                    "type": "query",
                    "_id": q["_id"],
                    "fields": {},
                    "parameters": [],
                    "queryParams": {},
                },
                limit=50,
                paginate=False,
            )
            table = component(
                "table",
                title + "列表",
                dataProvider="{{ literal [" + provider["_id"] + "] }}",
                columns=list(q["schema"]),
                size="spectrum--medium",
                rowCount=10,
                quiet=False,
                compact=True,
                allowSelectRows=False,
                noRowsMessage="没有符合条件的记录。请调整筛选条件；登录过期时请重新登录。",
            )
            provider["_children"].append(table)
            root["_children"].append(
                component(
                    "button",
                    "刷新列表",
                    text="刷新列表",
                    type="secondary",
                    onClick=[action("Refresh Data Provider", componentId=provider["_id"])],
                )
            )
            card = component(
                "container",
                "数据卡片",
                layout="flex",
                direction="column",
                hAlign="stretch",
                gap="M",
            )
            card["_styles"]["normal"] = {
                "padding": "24px",
                "background": "#ffffff",
                "border": "1px solid #e5eaf0",
                "border-radius": "12px",
                "box-shadow": "0 2px 8px rgba(24,39,59,.03)",
            }
            card["_children"].append(
                component(
                    "textv2",
                    "列表提示",
                    text="### " + title + (" · 点击用户行编辑资料" if route == "/" else ""),
                    size="16px",
                )
            )
            card["_children"].append(
                component(
                    "filter",
                    "搜索与筛选",
                    targetComponent="{{ literal [" + provider["_id"] + "] }}",
                    showClear=True,
                    persistFilters=False,
                    size="M",
                    buttonText="应用筛选",
                    filterConfig=[
                        {"field": col, "label": col, "active": True, "columnType": "string"}
                        for col in list(q["schema"])
                    ],
                )
            )
            card["_children"].append(provider)
            root["_children"].append(card)
            if route == "/":
                form = component(
                    "form",
                    "维护用户资料",
                    actionType="Create",
                    dataSource={"name": "Custom", "label": "Custom"},
                    size="spectrum--medium",
                )
                form["_children"] = [
                    component(
                        "textv2",
                        "编辑说明",
                        text="## 用户资料\n更新显示名称，或管理该账号的访问状态。",
                        size="14px",
                    ),
                    component(
                        "stringfield",
                        "目标账号",
                        field="username",
                        label="账号",
                        placeholder="请选择用户",
                        disabled=True,
                        defaultValue="{{ [state].[selected_username] }}",
                    ),
                    component(
                        "stringfield",
                        "显示名称",
                        field="display_name",
                        label="显示名称",
                        placeholder="填写新的显示名称",
                        defaultValue="{{ [state].[selected_display_name] }}",
                    ),
                ]
                panel = component("sidepanel", "用户资料面板", size="medium", position="right")
                panel["_styles"]["normal"] = {"padding": "28px"}
                table["onClick"] = [
                    action(
                        "Update State",
                        type="set",
                        key="selected_username",
                        value="{{ [eventContext].[row].[username] }}",
                        persist=False,
                    ),
                    action(
                        "Update State",
                        type="set",
                        key="selected_display_name",
                        value="{{ [eventContext].[row].[display_name] }}",
                        persist=False,
                    ),
                    action("Open Side Panel", id=panel["_id"], size="medium"),
                ]
                form["_children"].append(
                    component(
                        "button",
                        "保存资料",
                        text="保存资料",
                        type="cta",
                        onClick=[
                            action(
                                "Execute Query",
                                datasourceId=source["_id"],
                                queryId=edit["_id"],
                                queryParams={
                                    n: "{{ [" + form["_id"] + "].[" + n + "] }}"
                                    for n in ("username", "display_name")
                                },
                            ),
                            result_notice("用户资料已保存。"),
                            action("Refresh Data Provider", componentId=provider["_id"]),
                        ],
                    )
                )
                form["_children"].append(
                    component(
                        "button",
                        "停用账号",
                        text="停用该账号",
                        type="warning",
                        onClick=[
                            action(
                                "Execute Query",
                                datasourceId=source["_id"],
                                queryId=disable["_id"],
                                queryParams={"username": "{{ [" + form["_id"] + "].[username] }}"},
                                confirm=True,
                                customTitleText="确认停用账号",
                                confirmText="停用后，该账号将无法进入用户平台。当前版本尚未开放重新启用。",
                                confirmButtonText="确认停用",
                                cancelButtonText="取消",
                            ),
                            result_notice("账号已停用。"),
                            action("Refresh Data Provider", componentId=provider["_id"]),
                        ],
                    )
                )
                form["_children"].append(
                    component(
                        "button",
                        "关闭面板",
                        text="关闭",
                        type="secondary",
                        onClick=[action("Close Side Panel")],
                    )
                )
                for child in form["_children"]:
                    child["_styles"]["normal"].update({"margin-bottom": "18px"})
                panel["_children"].append(form)
                root["_children"].append(panel)
            screen = {
                "showNavigation": True,
                "width": "Large",
                "props": root,
                "routing": {"route": route, "roleId": "BASIC", "homeScreen": route == "/"},
                "name": title,
                "workspaceAppId": APP,
            }
            if route in old_screens:
                screen.update({k: old_screens[route][k] for k in ("_id", "_rev")})
            call("POST", "/api/screens", json=screen)
            links.append({"text": title, "url": route, "type": "link", "roleId": "BASIC"})
        app = call("GET", "/api/workspaceApp/" + APP)
        app = app.get("workspaceApp", app)
        payload = {k: app[k] for k in ("_id", "_rev", "name", "url", "navigation")}
        payload.update(
            disabled=False,
            customTheme={
                "fontFamily": "inter",
                "primaryColor": "#4263c7",
                "primaryColorHover": "#3452ab",
                "navBackground": "#ffffff",
                "navTextColor": "#26364a",
            },
        )
        payload["navigation"].update(
            navigation="Left",
            titleSize="M",
            textAlign="Left",
            hideLogo=True,
            showLoginButton=True,
            navLinkActiveBackground="#e9eefc",
            navLinkActiveTextColor="#3b55ad",
            navLinkHoverBackground="#f1f4f9",
            navLinkHoverTextColor="#273b62",
            title="Aether 管理中心",
            links=links,
            navBackground="#ffffff",
            navTextColor="#26364a",
        )
        call("PUT", "/api/workspaceApp/" + APP, json=payload)
        call("POST", "/api/applications/" + WORKSPACE + "/publish", json={})
        (args.config.parent / "admin-ui-manifest.json").write_text(
            json.dumps(
                {
                    "workspace": WORKSPACE,
                    "application": APP,
                    "queries": {
                        q["name"]: q["_id"] for q in (users, tenants, audit, edit, disable)
                    },
                }
            ),
            encoding="utf-8",
        )
        print("Published native Budibase users, tenants and audit screens in the isolated lab.")


if __name__ == "__main__":
    main()
