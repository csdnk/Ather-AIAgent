# Aether native management integration

Upstream revisions and licenses are recorded in `AETHER-UPSTREAM.json` and `LICENSE` in each source tree. Source imports include tracked upstream files only. The frontend attendance constants are a local build repair matching the native Java enums.

## Build

Backend requires JDK 17 and Maven 3.9. From this directory run:

```sh
mvn -B -ntp -pl yudao-server -am -Dtest=Aether*Test -Dsurefire.failIfNoSpecifiedTests=false package
```

The executable output is `yudao-server/target/yudao-server.jar`. Container build/runtime belongs to `AgentJYS-main/deploy/ruoyi`. `application-cloud.yaml` reads only deployment environment credentials for its MySQL and Redis connections. Default mock authentication is explicitly disabled in the cloud profile. Quartz automatic startup is disabled; its scheduler is not the Agent/P3 execution engine.

## Native identity and permission boundary

- `POST /admin-api/aether/identity/login`: native account/password/captcha request, tenant resolved by globally unique username. Multiple matches are denied. Returns native token response. Authentication input and output bodies are never access-logged.
- `GET /admin-api/aether/identity/self`: bearer token, native admin user type, live user/tenant/role/menu queries. Returns snake-case `user_id`, `tenant_id`, `username`, `display_name`, `user_enabled`, `tenant_enabled`, `role_codes`, `permissions`.
- `POST /admin-api/aether/identity/status`: HTTP Basic for configured `aether-python` service client, form `user_id`, `tenant_id`, optional `token_hash`. Direct client status/secret validation. A supplied SHA-256 token fingerprint must match a live unexpired access-token record. No raw token is persisted by this bridge.
- Native `/system/oauth2/check-token` accepts Basic without a tenant header; request/response body logging is disabled there too.
- Aether roles are explicitly `aether_platform_admin`, `aether_tenant_admin`, and `aether_user`. Platform authority is honored only for `aether.identity.platform-tenant-id` (default 1). Ordinary users receive no operations permissions. Native role assignment validates both target user and requested role IDs within the current tenant.
- Native administrative tenant visiting remains guarded by `system:tenant:visit`; Aether calls do not forward browser tenant or visit-tenant headers. Python derives business scope from authenticated identity independently.

## Operations contract

`GET /admin-api/aether/ops/{resource}` wraps the fixed Python `/platform-ops/v1/{resource}` response in native `CommonResult`. Resources: overview, requests, tasks, memories, incidents, configuration, backups, usage, resources, support, audit, commands, rules, quotas. Lists contain `items`, `total`, `observed_at`, and `status`; partial sources remain explicit. Task paging uses `cursor` and `next_cursor`; other lists use offset. Exact command queries preserve original `command_id`.

`POST /admin-api/aether/ops/commands` forwards the stable command envelope `{command_id,resource,action,target_id,expected_version,parameters}` after requiring `aether:ops:read` and `aether:{resource}:execute`. Fixed origin configuration has no path, credentials, redirects, arbitrary URL proxying, cookie forwarding, or retries. Ambiguous write outcomes are reported as unknown. The frontend retains the original command in session storage and blocks another submission until its original receipt is terminal.

## Native notifications

`POST /admin-api/aether/notifications/alert` accepts only `alert_id`, `metric`, `tenant_id`, `state`, `value`, `threshold`. It requires a nonblank constant-time-checked `X-Aether-Notification-Key` and sends native `aether_ops_alert` station messages to configured fixed admin IDs. Unique `(alert_id,state)` reservation and native message creation share one MySQL transaction; replay returns the stored receipt. Credentials and notification request/response bodies are not logged.

## Initialization

Apply native upstream SQL to a new candidate database only. `sql/mysql/aether-platform.sql` is additive Aether setup and requires eight independent external BCrypt session variables (`@aether_password_hash_50001` ... `_50008`). It never embeds passwords and preserves existing user passwords on rerun. The managed account namespace is protected by a generated-column unique index with a default-on scope flag and auto-increment floor 50009. This makes concurrent duplicate creation fail in MySQL while retaining old disabled sample records. The global application precheck also covers those old names. `tests/verify_managed_login_mysql.py` verifies concurrent duplicate rejection, case folding, repeatable DDL, preserved samples and the ID floor against a disposable MySQL database.

Disable all upstream sample accounts, sample OAuth clients, sample jobs and development settings before exposing a candidate. Configure only the dedicated Python introspection client and native default login client with deployment-owned secrets.

The controlled identity IDs are user 50001–50008, platform tenant 1 and business tenants 501–503. Existing Python business identifiers are mapped separately by its identity adapter, never inferred from matching display names. Tenant C and user 50008 remain disabled for rejection tests.

## Evidence boundary

Java focused tests and production frontend builds verify source behavior and packaging. Cloud authentication, actual provider execution, data migration ownership, backup restore, reboot persistence and release digest checks require the end-to-end deployment acceptance. The memories view displays the receipts returned by its API and labels any catalog limitation. Model usage shows missing values explicitly; it does not synthesize historical metering. Configuration management records and actual P3 activation are separate actions. Backup scope comes from the executor response.
