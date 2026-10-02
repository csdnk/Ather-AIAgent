# Keycloak 本地接入与验证

> 组织模式更新：当前本地部署已改用 [Keycloak 原生组织、成员与权限复用](14_Keycloak原生组织成员与权限复用.md)。本章静态映射与 16 场景属于前一阶段记录；当前成员维护及复验请按第 14 章，不要重跑旧初始化来覆盖组织配置。

日期：2026-10-01。承接 [租户入口与可观测性接入](12_租户入口与可观测性接入.md)。此轮在已有 PyJWT、structlog、OpenTelemetry 接入之上，补齐本地身份平台和浏览器登录。

## 1. 组件边界

- Keycloak 26.8.0：账号认证、授权码、签发与刷新令牌、退出会话。
- keycloak-js 26.2.4：浏览器使用 Authorization Code + PKCE S256；公共客户端，无客户端密钥。
- PyJWT：P3 校验 iss/aud/exp/sub 和签名，从配置的 JWKS 获取公钥。
- P3 身份服务：按服务端 (issuer, sub) 映射 principal_id，加载 home_scope 与 permissions，复核租户状态。JWT 中的租户/角色声明不能自行授予权限。
- structlog、OpenTelemetry：沿用前一阶段的访问日志、节点 Span 和可选 OTLP 导出。
- PostgreSQL 17-alpine：仅供 Keycloak 存储身份平台数据。不是 P2 Provider，也没有替换 P3 现有库。镜像 digest 锁定在准备脚本中。

## 2. 新增配置和接口

service.yaml 可添加：

```yaml
browser_identity:
  url: http://127.0.0.1:18080
  realm: p3-demo
  client_id: p3-monitor
```

浏览器配置使用 HTTPS，本机 loopback 允许 HTTP；拒绝带账号密码、query 或 fragment 的 URL。启动时检查对应 issuer 已配置在 JWT 验证器中，防止网页显示一个未受信任的登录入口。

| 接口 | 认证 | 返回 |
|---|---|---|
| GET /p3/auth/config | 不需要 | enabled 与必要登录参数；不提供密码、密钥、映射表 |
| GET /p3/auth/me | 需要 | P3 确认的 principal_id、scope、permissions |

两者均设置 Cache-Control: no-store。未配置 browser_identity 时，网页登录配置返回 disabled，保留手动 Bearer 登录。旧静态 local_admin 未删除。

演示 JWT 配置：issuer 为 http://127.0.0.1:18080/realms/p3-demo，audience 为 aether-p3，RS256；客户端为 p3-monitor。回调严格配置为 http://127.0.0.1:5173/，不能用 localhost 代替它。账号 demo_a、demo_b 分别映射 tenant_a、tenant_b；unbound 不登记在 P3。

## 3. 页面行为

初始化适配器的 Promise 在页面内复用，避免 React StrictMode 二次初始化。/auth/me 成功后才启用监测数据读取。令牌不写入应用持久存储；官方适配器会临时保存 PKCE 跳转状态。

每 20 秒调用 updateToken(40)，每 20 秒复核 /auth/me。身份确认或续期失败时清除本页连接。Monitor 按 principal 与 scope 建立实例边界：同一身份续期保留采样历史和打开的 Trace 节点，切换租户重建页面状态；查询拒绝时清空受保护的详情数据。测试覆盖这两种差异。浏览器长时间休眠/限频后允许要求重新登录，未实现多标签页会话同步。

退出 Keycloak 会终止会话与刷新能力，但不会立刻撤销所有已签发 access JWT。本地 P3 验签仍受 exp 与 P3 独立租户/权限状态约束。需要阻止某租户继续访问时，修改服务端 enabled 并增加 revision，按既有授权重载机制生效。

## 4. 部署、重启与验证脚本

初始化脚本要求现成的独立 P3 部署目录，里面已有 service.yaml 与 identities.yaml；不会替团队决定 P2 实现。使用已安装项目及 YAML/HTTP 等依赖的 Python 环境。所有生成文件放仓库外。

```powershell
python scripts/p3/keycloak_demo.py --directory <仓库外身份目录> --deployment <已有P3部署目录>
docker compose --project-directory <仓库外身份目录> -f <仓库外身份目录>/compose.yaml up -d
# 启动既有 Temporal 后，运行 P3
python -m aether_agent_memory.runtime.flows serve --config <已有P3部署目录>/service.yaml
# 独立终端在 web 目录运行，缓存和构建输出配置到仓库外
# npm run dev -- --host 127.0.0.1 --port 5173 --strictPort
```

首次准备会生成随机凭据、realm.json、.env、compose.yaml，并合并 P3 身份配置；保留现有身份，首次备份 identities.before.yaml、service.before.yaml。私密文件不打印密码，不提交仓库。本机已经初始化，日常启动只需启动容器和既有服务。

已有 Keycloak realm 不会在重启导入时被覆盖。后续通过管理 API/界面维护账号，并同步 P3 的服务端主体映射；不要通过删除卷“更新账号”。

真实联调脚本：

```powershell
python scripts/p3/verify_keycloak.py --directory <仓库外身份目录> --deployment <已有P3部署目录> --report <仓库外报告文件.json>
```

脚本逐个账号清理测试会话，使用真实授权码与 PKCE。Python CookieJar 与浏览器对 loopback Secure Cookie 的处理不同，脚本只对固定 http://127.0.0.1:18080 登录地址补发符合域名、路径、有效期的 Cookie，不改变 Keycloak 策略，也不向任意主机发送 Cookie。禁止打印令牌或带授权码的异常 URL。

该脚本会产生测试日志，短暂禁用 tenant_a 并在 finally 恢复，增加配置 revision/auth_epoch。只在演示或独立测试环境运行。

## 5. 本次验证结果

- 后端身份/可观测性测试 48 项，已有底座与契约回归 58 项，合计 106 项通过。
- 网页 33 项通过；ESLint、TypeScript 与 Vite build 通过。
- Python 改动通过 Ruff；三个后端接入文件通过 Mypy。
- 真实 Keycloak + PostgreSQL + P3：16 场景通过。包括未认证、两个账号租户映射、授权码防重放、错误 PKCE、双向日志/Trace 隔离、篡改令牌、未绑定账号、错误受众、刷新、禁用/恢复、退出与静态凭据兼容。
- 浏览器实测 A 登录 → 退出 → B 登录 → 查看本租户链路/节点；超过 180 秒访问令牌寿命后仍可访问。修复后监测采样可跨续期保留。

同一套源代码可重新执行：

```powershell
python -m pytest tests/runtime/flows/test_tenant_observability.py tests/runtime/p3/test_foundation.py tests/contracts/p3/test_foundation_contracts.py -q -o cache_dir=<仓库外缓存目录>
# 在 web 目录，配置 AETHER_WEB_CACHE_DIR 和 AETHER_WEB_BUILD_DIR 到仓库外
npm test
npm run lint
npm run build
```

## 6. 本轮代码文件

| 文件 | 修改内容 |
|---|---|
| src/aether_agent_memory/runtime/flows/config.py | 可选 BrowserIdentityConfiguration 及地址校验 |
| src/aether_agent_memory/runtime/flows/application.py | 校验网页登录 issuer 与后端验证配置一致，装配到 HTTP |
| src/aether_agent_memory/runtime/flows/http.py | /p3/auth/config 与 /p3/auth/me |
| scripts/p3/keycloak_demo.py | 仓库外演示配置、账号准备、保留旧身份 |
| scripts/p3/verify_keycloak.py | 16 个真实身份联调场景 |
| web/package.json、package-lock.json | keycloak-js 依赖 |
| web/src/monitoring/identity.ts | 官方适配器单例、PKCE 初始化、身份读取 |
| web/src/monitoring/Console.tsx、console.css | 登录、退出、租户横幅、续期与页面状态隔离 |
| web/src/monitoring/useMonitor.ts | 同一身份续期保留图表历史 |
| web/src/monitoring/identity.test.ts、TraceDetail.test.tsx、useMonitor.test.tsx | 网页身份与续期回归 |
| tests/runtime/flows/test_tenant_observability.py | 在此前测试基础上补充 9 项浏览器配置和身份接口测试 |

其他已修改的 foundation、JWT、observability、依赖和架构文档中包含上一个任务的工作，不应全部算作此轮新增。没有推送 GitHub。

## 7. 当前边界

本机 HTTP / start-dev / PostgreSQL 单实例，不是生产高可用部署。真实 P4 Agent、MCP 授权适配、P2 存储协议和完整出站 Trace 传播未验收。租户配额、自助开户、邀请、计费、集中日志检索平台未新增。Temporal 核心执行/恢复实现未修改。

Keycloak 管理账号和演示账号密码保存在外部 identity/credentials.json；禁止复制到文档、源码和 Git。

官方项目：

- https://github.com/keycloak/keycloak
- https://github.com/keycloak/keycloak-js
- https://github.com/jpadilla/pyjwt
- https://github.com/hynek/structlog
- https://github.com/open-telemetry/opentelemetry-python
- https://github.com/postgres/postgres

官网说明：https://www.keycloak.org/securing-apps/javascript-adapter 、https://www.keycloak.org/server/containers 。
