# Keycloak 账号自助与邀请复用

本地增量说明。沿用已有 Keycloak 26.8.0、keycloak-js、组织目录同步和 P3 权限校验；在当前工作区中补齐账号自助入口。身份配置写入已有 Keycloak 存储，邮件演示由本地 Mailpit 接收。

## 1. 本轮选择复用什么

| 能力 | 复用方式 | 结果与边界 |
|---|---|---|
| 注册、邮箱验证、初始密码 | keycloak-js register + Keycloak 原生页面 | 真实账号流程；不在 P3 自建密码库 |
| 登录与换账号 | keycloak-js login，prompt=login | 沿用现有 PKCE 登录与服务端身份复核 |
| 找回密码 | 原生 reset-credentials 邮件流程 | 本地邮件重置及新密码登录已联调 |
| 组织邀请 | 平台管理员使用原生组织后台/API | 用户明确接受；接受后还要分配组织内角色 |
| 个人账号 | accountManagement | 原生账号控制台入口已接通；各设置操作需分项验收 |
| 密码规则、失败防护 | realm 原生策略 | 弱密码拒绝已验；实际锁定与恢复待补测 |
| 身份审计 | Keycloak 用户事件、管理员事件 | 用户注册/验证/登录/改密事件已验；不混入 P3 业务 Trace |
| 演示邮件 | Mailpit v1.31.3 | 本地 SMTP 接收与网页查看，无真实外发配置 |

选择这些能力，是因为它们直接属于身份平台：密码校验、邮件动作链接和登录会话由 Keycloak 维护。P3 继续负责自己的租户准入、业务权限和数据范围，避免同一规则分散到两个账号系统。

## 2. 用户看到的入口

五场景演示与监测台使用同一个顶部组件 IdentityActions：

- 注册账号：未登录时显示，打开原生注册页；已登录时隐藏，初始化恢复期间不闪现注册按钮。
- 登录 / 切换账号：未登录时显示此文案；已登录改为“切换账号”。顶部与连接弹窗保持一致，仍要求主动完成登录。
- 邀请成员：展示三步说明并进入平台管理员组织后台。
- 个人账号：打开原生账号控制台。

沿用五场景演示的绿色风格，窄屏自动换行；登录入口不再只藏在“配置连接”里。未授权用户的身份提示解释组织和角色条件。

平台管理链接使用 master 登录入口，并将目标指向 p3-demo 的组织页面。前端只提供导航，不保存平台管理员凭据，不向 P3 组织管理员增加全局权限。

### 2.1 刷新为什么能恢复登录

2026-10-02 增量：浏览器适配器初始化使用 check-sso，通过 public/silent-check-sso.html 的同源回调恢复仍有效的 Keycloak 会话；浏览器限制静默检查时，使用 SDK 的标准页面跳转回退。令牌仍只保留在内存中，不写入 localStorage 或 sessionStorage。

账号按钮订阅同一个认证状态，登录成功、刷新令牌及退出时同步更新。页面刷新后，监测台仍调用 /p3/auth/me 重新确认 P3 的组织与权限。P3 拒绝访问只清除监测身份和数据，不据此把仍有效的 Keycloak 登录清掉，避免“已登录”和“已获 P3 授权”混淆。

回跳只保留 view=demo 或 view=monitor，不把 OAuth code、state 等回调参数继续带入下一次跳转；指定演示页时，身份回调后仍留在演示页。连接弹窗的手动粘贴凭据保持原来的内存生命周期。

本机 p3-monitor 的登录回调白名单包含以下四项：

- http://127.0.0.1:5173/
- http://127.0.0.1:5173/?view=demo
- http://127.0.0.1:5173/?view=monitor
- http://127.0.0.1:5173/silent-check-sso.html

退出回调白名单包含上述前三项，在客户端 post.logout.redirect.uris 中用 ## 分隔。没有增加通配符。现有本地客户端已增量更新并读回验证；私有 realm.json 导入配置和 keycloak_demo.py 的新部署生成配置同步更新。不要为补这几项而重跑用户初始化。

会话已到期、已退出或身份服务不可用时不能保证恢复；所选组织、监测子页与演示进度不在本次持久化范围。操作验收见手册 T25。

## 3. 一个新成员的实际流程

1. 自助注册：填写用户名、测试邮箱、名和姓。
2. 在本地 Mailpit 打开验证邮件，验证邮箱；随后设置满足策略的初始密码。
3. 此时账号能完成身份登录，但未加入受信组织，P3 返回 401。
4. 平台管理员向该邮箱发送原生组织邀请。
5. 用户打开邀请邮件，在原生页面明确接受，成为组织成员。
6. 还未分配组织内角色时，P3 仍返回 401。
7. 管理员将用户加入该组织“只读成员”等分组；目录同步成功后，P3 授予对应角色与范围。
8. 即使伪造另一个 tenant_id，也不能获得未授权组织的访问权。

注册、成员资格、组织角色、P3 受信组织绑定是不同条件。新建任意 Keycloak 组织不会自动开通 P3 租户。

当前真实邀请测试使用“先注册的已有账号”；直接邀请不存在的用户及 managed 用户生命周期需单独验收。删除 managed 成员可能删除对应账号，不能一律视为只撤销关系。

## 4. 为什么邀请仍由平台管理员发起

当前已安装版本与既有委派配置下，P3 组织管理员没有完整的成员邀请/分组维护自助权限。本轮使用经过验证的平台管理员原生路径。若要交付组织管理员自助操作，需要另行设计受组织范围限制的管理入口和服务端鉴权，并验收跨组织管理拒绝。

“网页有邀请按钮”不等于所有登录用户能发送邀请。按钮说明明确了操作者、目标组织和后续授权步骤。

## 5. 增量配置

脚本 scripts/p3/keycloak_self_service.py：

- 读取现有 realm，仅更新相关选项，保留组织、用户和客户端。
- 将改动前的相关字段备份到仓库外私有目录 backups。
- 配置前检查 SMTP：发现另一套 SMTP 时拒绝覆盖。
- 生成独立 Mailpit Compose 文件并连接已有 Keycloak Docker 网络。
- 读回核对策略和 SMTP，更新私有 realm.json 中的对应导入字段。
- 不重跑旧的用户/组织初始化，不承诺失败自动回滚。

当前选项：

| 配置 | 值 |
|---|---|
| registrationAllowed / resetPasswordAllowed / verifyEmail | true |
| 密码策略 | 至少 12 位、大写、小写、数字 |
| bruteForceProtected / permanentLockout | true / false |
| failureFactor / waitIncrementSeconds / maxFailureWaitSeconds | 5 / 60 / 900 |
| 用户事件 | 开启，eventsExpiration=604800 秒 |
| 管理员事件 | 开启，详细表示数据关闭；保留周期待单独确认 |
| SMTP | mailpit:1025，本地无鉴权测试收件服务 |
| Mailpit 网页 / SMTP 主机端口 | 127.0.0.1:18025 / 127.0.0.1:11025 |

Mailpit 最多保留 200 封邮件，没有持久卷，未配置 relay 或转发。网页可看到该测试环境所有邮件，只用于虚构测试地址；不能用于真实多租户邮箱隔离。生产 SMTP、TLS、邮件限流和反滥用另行配置。

在仓库根目录执行（private-directory 指向既有仓库外身份配置目录）：

```powershell
python scripts/p3/keycloak_self_service.py --directory <private-directory>
```

日常恢复邮箱只需：

```powershell
docker compose -f <private-directory>/compose.self-service-mail.yaml up -d mailpit
```

具体本机路径与完整手动流程见交付区《P3租户功能测试操作手册_20261001.md》V1.2，第 7 节。

## 6. 验证记录与未完成范围

2026-10-02 刷新恢复增量：88 项前端测试、3 项身份配置工具测试、ESLint、Ruff、TypeScript 和构建通过；真实浏览器验证账号刷新恢复、演示页回跳、已登录按钮隐藏/文案、连接弹窗文案、切换账号入口、退出返回和退出后刷新。此次没有重跑下列历史后端与组织全套联调。

此前账号自助增量记录：

- 后端相关回归：128 项通过，含新增 3 项增量配置测试。
- 前端：80 项测试、ESLint、TypeScript 和构建通过；构建仍有已有的包体积提示。
- 原有组织真实联调：20 场景重新通过，动态修改已恢复。
- 原生账号自助真实联调：10 场景通过，专用用户和测试邮件已清理。
- 浏览器：桌面/窄屏按钮、注册和登录页面、邀请管理入口与个人账号入口跳转已检查。

自助验证脚本：

```powershell
python scripts/p3/verify_keycloak_self_service.py --directory <private-directory> --report <external-report.json>
```

脚本先写 running 报告，所有验证和清理成功才写 passed；异常/中断不能沿用旧成功结论。仅使用本地 Mailpit 和本机身份服务，生成随机专用账号，不改现有 demo 用户。强制中断仍可能需要人工清理。

**保留的失败**：额外运行的 15 项 HTTP/可观测性测试中，14 项通过；原有 test_process_kill_leaves_open_span_not_success 在终止子进程并重开 SQLite 日志库时出现 disk I/O error，单独复跑仍失败。本轮未修改该恢复逻辑，不能宣称全仓库回归通过。

仍需后续验收或建设：组织管理员完整成员自助、任意新组织自动开通、直接邀请未注册用户、个人资料/会话/OTP操作、强制 MFA、实际登录锁定与恢复、生产邮件及高可用。组织级别的数据共享、配额和资源公平性也不由原生注册功能自动解决。

## 7. 文件索引

| 文件 | 作用 |
|---|---|
| web/src/monitoring/IdentityActions.tsx | 公共账号按钮与邀请说明 |
| web/src/monitoring/IdentityActions.test.tsx | 入口跳转、管理边界、错误状态测试 |
| web/src/App.tsx、App.test.tsx | 公共入口接入两个工作空间 |
| web/src/workspace.css | 顶部按钮与窄屏排布 |
| web/src/monitoring/identity.ts | SSO 会话恢复、认证状态发布、身份复核和固定回跳地址 |
| web/public/silent-check-sso.html | 静默会话检查的同源回调页面 |
| scripts/p3/keycloak_demo.py | 新部署的精确登录/退出回调配置生成 |
| scripts/p3/keycloak_self_service.py | 原生账号功能与本地邮件增量配置 |
| scripts/p3/verify_keycloak_self_service.py | 10 个真实流程与清理 |
| tests/runtime/flows/test_keycloak_self_service.py | 保留配置、备份、SMTP边界回归 |

此前工作区已包含租户、日志、Trace 改动；不能把所有 Git 状态变化都归入本轮。

## 8. 官方资料

- Keycloak 源码：https://github.com/keycloak/keycloak
- keycloak-js：https://github.com/keycloak/keycloak-js
- 原生账号、组织与事件说明：https://www.keycloak.org/docs/latest/server_admin/index.html
- 浏览器适配器：https://www.keycloak.org/securing-apps/javascript-adapter
- Mailpit：https://github.com/axllent/mailpit

具体可用性以本机已安装版本与上述真实联调为准，不将 latest 文档直接当作本地验收结论。
