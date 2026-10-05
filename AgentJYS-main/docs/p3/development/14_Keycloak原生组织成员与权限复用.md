# Keycloak 原生组织、成员与权限复用

日期：2026-10-01。承接 [Keycloak 本地接入与验证](13_Keycloak本地接入与验证.md)。本章是当前组织模式的说明，上一章的逐用户静态映射与 16 场景是前一阶段记录。

## 1. 当前实现

Keycloak 26.8.0 原生 Organizations 管组织、Membership 管成员关系、组织内 Groups 管分组、p3-monitor 客户端 Roles 表达业务角色。平台管理员在原生后台维护它们。

P3 保留两项服务端策略：组织 UUID 到 tenant/application/agent 的绑定，以及客户端角色到 P3 permissions 的绑定。P3 不接收浏览器自报的成员身份，也不直接使用 JWT 中的角色/租户声明授权。同一账号在不同组织生成不同 principal，组权限只在对应组织内合并。

现有组织演示浏览器使用 keycloak-js 授权码 + PKCE，P3 使用 PyJWT 验签并校验 issuer/audience/expiration。通过 GET /p3/auth/me 返回当前身份、组织选项和角色；X-P3-Tenant 仅选择已获授权的组织。未指定时必须恰好匹配一个有效身份；多个有效身份返回 FORBIDDEN，不再按 tenant_id 排序选择。已有多组织调用方须显式传入已授权的组织选择。新 Agent 平台由后端业务目录强制普通用户唯一租户，租户对用户不可见；该产品入口将在替换验证后接替组织演示页。


默认 realm: p3-demo；业务 client: p3-monitor；只读目录 client: p3-directory。

## 2. 配置与同步

参考 [组织模式示例](../../../configs/identities.keycloak-organizations.example.yaml)。复制到仓库外并替换占位 UUID/路径，保留已有静态 identities（含 local_admin）。不要把示例空 identities 覆盖到已有部署。

目录客户端需要原生 realm-management 角色 view-organizations、query-organizations、view-clients、view-users；未授予任何 manage/write 角色。它会读取组织成员及其在当前组织内的分组、角色和父分组继承。成员未启用、组织未启用或没有受信角色时不给予 P3 身份。更改角色请维护**组织内分组**，不要用用户全局角色替代组织角色。

同步在独立线程中执行，成功后等待约 5 秒再采集。快照从本轮采集开始算起 20 秒失效；一次采集必须完整成功才原子替换。失败不发布半份成员目录，过期后动态身份返回 503，静态恢复入口仍可使用。正常更新延迟约为采集耗时加轮询间隔，不能声称即时撤权。

派生身份、快照元信息和撤销标记存入既有 P3 Identity 存储，不新增 P3 数据库。角色变化或成员重新加入会递增 auth_epoch，既有重新鉴权点可阻止旧上下文继续；已经提交给外部系统的副作用不会因此自动撤回。

账号、组织和角色原始数据由 Keycloak 的独立 PostgreSQL 保存，与 P2 存储选择无关。本地旧日志库、P2 演示 provider 沿用先前部署，这次没有替换。

## 3. 角色策略

| 角色 | P3 权限 |
|---|---|
| organization-admin | memory:read/write/correct/delete/history；maintenance:diagnose/recover/configure |
| member | memory:read/write/correct/history |
| viewer | memory:read/history；maintenance:diagnose |

这只是当前演示策略。保留原有 user_id/agent_id 范围校验，组织管理员并不自动拥有所有成员数据的读取权。member 没有诊断权限，在监测页看到诊断拒绝是预期结果；viewer 为演示只读运维而获 diagnose。

页面按 principal、scope、permissions 重建身份边界；切换组织先清除旧视图，再确认新身份。令牌和组织选择不保存到应用 localStorage。

## 4. 原生管理的实际边界

平台管理员可以在原生后台管理账号、组织、成员、分组和角色；这条路径已实际查看成员页，并通过管理 API 完成变更/恢复测试。

另为演示管理员配置了原生 FGAP：只允许指定用户 GET/PUT 本组织基本信息，跨组织返回 403。**它不代表已完成组织管理员自助成员管理**：

- 26.8.0 Admin Console 的组织详情路由要求 realm 级 view-organizations，仅有单组织 FGAP 时页面拦截。
- 当前授权下，组织管理员查询成员、变更成员分组返回 403；原生成员 API 还检查用户相关权限。
- 此版本拒绝将组织组直接作为 FGAP group policy，演示采用 native user policy。业务管理员分组与后台 FGAP 授权是两份原生配置；撤销管理员时需要同时处理。移除组织成员只保证 P3 访问被撤销，不自动删除独立 FGAP user policy。
- 没有为绕过限制而授予 realm-admin、manage-users 或全 realm 的 view/manage-organizations。成员维护由平台管理员完成，P3 页面已经明确提示。

若后续要求组织管理员自行邀请、建号、改角色，需要单独设计受组织约束的委派，并验证共享用户不会被另一组织管理员全局修改；本轮不宣称该项已交付。

## 5. 本地准备和复验

已有身份平台/部署基础上准备本地组织演示（仅初始化或重置演示时使用，可能恢复演示分组和权限，不是日常启动命令）：

```powershell
python scripts/p3/keycloak_organizations_demo.py --directory <仓库外身份目录> --deployment <已有P3部署目录>
python -m aether_agent_memory.runtime.flows serve --config <已有P3部署目录>/service.yaml
python scripts/p3/verify_keycloak_organizations.py --directory <仓库外身份目录> --deployment <已有P3部署目录> --report <仓库外验证报告.json>
```

Keycloak / Temporal / P3 / Vite 均复用已有本地实例。组织模式请用新的 verify_keycloak_organizations.py，旧 verify_keycloak.py 全量测试假设静态主体映射，不能直接用来验收组织模式；其中 PKCE 登录辅助函数继续复用。

验证脚本只适合独立演示环境：会暂时改组、移除/加回成员、停用/恢复用户与组织、停用/恢复目录客户端；均有 finally 恢复。保留当前演示的 admin/client/用户配置备份，不对生产环境直接运行。

## 6. 验证结果

- 后端组织 17 项 + 前期租户/可观测性 48 项 + 底座/契约 58 项，另含管理令牌失效恢复脚本 2 项，共 125 项通过。
- 前端 35 项通过，ESLint、TypeScript、Vite build 通过；构建有原有大包体提示，不影响本地运行。
- 真实 Keycloak + PostgreSQL + P3 联调 20 场景通过；覆盖多组织不同权限、选择头伪造、日志/Trace 双向隔离、原生有限管理与成员管理拒绝边界、动态撤权、故障过期拒绝和恢复。
- 浏览器验证 demo_multi 在 A 为组织管理员、B 为只读成员，以及平台管理员原生成员管理页。
- Ruff 检查通过；目录、配置、身份服务、JWT 四个后端文件 Mypy 通过。

## 7. 文件索引

| 文件 | 本轮用途 |
|---|---|
| runtime/flows/config.py | 组织目录配置、组织/角色白名单和安全校验 |
| runtime/flows/keycloak_directory.py | 原生 Admin API 读取、定时同步、完整快照、撤销标记与 epoch |
| runtime/foundation/identity.py | 动态身份、快照有效期、多组织选择与服务端复核 |
| runtime/flows/jwt_auth.py、http.py | 验签后选择组织、X-P3-Tenant、auth/me 组织信息 |
| runtime/flows/application.py | 同步线程生命周期和配置装配 |
| web/src/monitoring/identity.ts、api.ts | 读取组织身份、为每次请求固定组织头 |
| web/src/monitoring/Console.tsx、console.css | 组织切换、角色显示、旧页面状态清理和管理边界提示 |
| scripts/p3/keycloak_organizations_demo.py | 原生组织/分组/角色与只读目录客户端准备 |
| scripts/p3/verify_keycloak_organizations.py | 真实接口集成测试与恢复 |
| scripts/p3/verify_keycloak.py | 兼容组织模式两步登录页的 PKCE 辅助函数 |
| tests/runtime/flows/test_keycloak_demo_tools.py | 管理令牌失效恢复与 403 不重试测试 |
| tests/runtime/flows/test_keycloak_organizations.py | 组织同步、身份撤销与边界单元/接口测试 |
| web/src/monitoring/api.test.ts、identity.test.ts | 组织请求头、请求固定组织及隐私回归 |
| configs/identities.keycloak-organizations.example.yaml | 不含密码、含占位 UUID 的组织配置示例 |

后端路径均位于 src/aether_agent_memory/。Git 中还有前期租户/日志/Trace 改动，不能全部算作本轮修改；本轮未修改 Operate 调度或 Temporal 执行/恢复核心。

## 8. 后续范围

当前全量轮询适合小规模演示，不是大规模目录同步设计；分页、请求次数、总采集时长有上限，超过后拒绝过期身份，需要扩容/优化同步策略。撤销标记为防止旧上下文复活而保留，尚无长期压缩策略。

旧静态映射的 principal/scope 与新组织模式不同，旧业务/日志数据未删除也未自动迁移。

后续本地增量已接通原生自助注册、邮箱验证、平台管理员邀请、找回密码与醒目账号入口，邮件使用本地 Mailpit；详见[账号自助与邀请复用](15_Keycloak账号自助与邀请复用.md)。对应 10 个真实自助场景及重新运行的 20 个组织场景已通过，最新验证数量与保留失败以该文档及交付手册 V1.1 为准。本篇第 6 节数字保留为前一阶段记录。

P4 机器身份接入、组织管理员看全组织数据、组织管理员完整成员自助、真实邮件外发、生产高可用与 HTTPS 仍不是已验收项。当前仍为本机 HTTP/start-dev 演示。

## 9. 官方参考

- Keycloak：https://github.com/keycloak/keycloak
- 管理文档：https://www.keycloak.org/docs/latest/server_admin/index.html
- Admin REST API：https://www.keycloak.org/docs-api/latest/rest-api/index.html
- 固定 26.8.0 组织详情路由：https://github.com/keycloak/keycloak/blob/26.8.0/js/apps/admin-ui/src/organizations/routes/EditOrganization.tsx
- 固定 26.8.0 成员权限检查：https://github.com/keycloak/keycloak/blob/26.8.0/services/src/main/java/org/keycloak/organization/admin/resource/OrganizationMemberResource.java

当前限制以本机 26.8.0 的实际界面/API测试和对应版本源码为准，不能用 latest 文档代替已安装版本验证。
