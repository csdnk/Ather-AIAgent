"""Attach directory display names without changing ownership or authorization."""


def attach_names(data, resource, actor, directory):
    rows = list(data.get("items", []))
    rows += data.get("alerts", [])
    usage = data.get("usage", {})
    rows += usage.get("items", []) + usage.get("model_usage", [])
    rows += data.get("model_usage", [])
    tenant_ids = {r.get("tenant_id") for r in rows if r.get("tenant_id")}
    if resource == "quotas":
        tenant_ids.update(r["id"] for r in rows if r.get("id"))
    user_ids = {r[k] for r in rows for k in ("user_id", "actor_id") if r.get(k)}
    if not tenant_ids and not user_ids:
        return data
    tenants, users = {}, {}
    scope = (actor.role == "platform_admin", actor.tenant_id)
    with directory.connection() as conn:
        if tenant_ids:
            tenants = {
                r["id"]: r["name"]
                for r in conn.execute(
                    "SELECT id,name FROM tenants WHERE id=ANY(%s) AND (%s OR id=%s)",
                    (list(tenant_ids), *scope),
                ).fetchall()
            }
        if user_ids:
            users = {
                r["id"]: r["display_name"]
                for r in conn.execute(
                    "SELECT id,display_name FROM users WHERE id=ANY(%s) AND (%s OR tenant_id=%s)",
                    (list(user_ids), *scope),
                ).fetchall()
            }
    for row in rows:
        tenant_id = row.get("id") if resource == "quotas" else row.get("tenant_id")
        if tenant_id in tenants:
            row["tenant_name"] = tenants[tenant_id]
        for field, label in (("user_id", "user_name"), ("actor_id", "actor_name")):
            if row.get(field) in users:
                row[label] = users[row[field]]
    return data
