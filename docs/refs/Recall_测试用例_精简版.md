# Recall 测试用例（精简版）

## 请求契约

<a id="case-rc-api-01"></a>

### RC-API-01 Working 请求端到端验证（P0）

- 前置：正常调用方 T-A/App-A/User-A/Agent-A 有 READ；指定真实 session；Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”均为 v1/g1 Ready；query=“用户的咖啡加糖习惯是什么？”，selection.session_id=该会话，sources=working，token_budget=1000 且足够。
- 步骤：提交 `POST /p3/recall`，使用新 X-Operation-ID → 查询原 `/p3/operations/{job_id}` 至终态 → GET 原 Record/result，核对文本、Ref 和版本。
- 预期：目标 Ref 的完整权威正文入包，来源为 working，整包 tokens≤1000；结果关联原请求，Query 编码/搜索/guard 阶段可核验。

<a id="case-rc-api-02"></a>

### RC-API-02 Query 空值与类型分区（P1）

- 前置：Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”，v1/g1 Ready；query 缺失/null/空字符串/纯空白/数字/数组。
- 步骤：各变体只改变 query → 检查响应、接纳与推理次数。
- 预期：按 Schema 校验 query 类型、必填及 trim；拒绝请求不提交 Pack，CPU 不无限运行；空白处理/HTTP 码未明确时标 blocked_requirement。

<a id="case-rc-api-03"></a>

### RC-API-03 Unicode 与多语言 Query 输入（P2）

- 前置：Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”，v1/g1 Ready；中文、英文、混合、emoji、组合 Unicode、换行、引号查询。
- 步骤：各变体使用新 ID 提交请求 → 比对原 query 摘要、模型实际输入和 Pack。
- 预期：输入不能损坏，也不能在未声明的情况下改变含义；长度按模型 token 计量，不按字节计量。记录语义检索结果，不要求不同语言或归一化形式得到相同排名。

<a id="case-rc-api-04"></a>

### RC-API-04 token_budget 合法域与极值（P1）

- 前置：Schema 规定预算最小 m/最大 M；准备短组 S、超预算组 H，用当前 tokenizer 实测整包渲染 T，包含引用/来源/关系/分隔符。
- 步骤：分别使用 m-1/m/m+1/M-1/M/M+1 及 0/-1/小数/字符串/bool/null/极大整数作为输入 → 检查组包。
- 预期：合法边界满足整包预算；非法值不溢出、不绕过上限；m/M 未明确时标 blocked_requirement。

<a id="case-rc-api-05"></a>

### RC-API-05 sources 缺省和非法枚举（P1）

- 前置：Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”，v1/g1 Ready；sources 省略/null/空/未知/大写/数组。
- 步骤：逐项发请求 → 记录 selected_sources 与错误。
- 预期：仅接受契约值；未知值不转为 auto/both；缺省/null/大小写规则未明确时标 blocked_requirement。

<a id="case-rc-api-06"></a>

### RC-API-06 selection 结构与空选择（P1）

- 前置：selection 缺失/空对象/null；合法 session/task；错误字段类型。
- 步骤：逐项提交 → 检查可信 scope、auto 选择和候选。
- 预期：按 Schema 处理默认值/类型；selection 与授权取交集，不扩大 scope、不覆盖可信身份；空选择规则未明确时标 blocked_requirement。

<a id="case-rc-api-07"></a>

### RC-API-07 查询和范围字符串注入（P0）

- 前置：合法目标分数低于外 tenant/user/session/source/space 对象，服务端 K=1；query 或 selection 合法字符串中含引号、过滤表达式、SQL 样式、换行。
- 步骤：在隔离数据中发送无害 canary → 查看过滤计划、返回 ID 及状态。
- 预期：数据按字面处理，不拼接成权限/过滤指令；无越权，无异常数据库动作；错误不泄露 DSN/内部受限 ID。

<a id="case-rc-api-08"></a>

### RC-API-08 客户端伪造内部策略字段（P1）

- 前置：未知 JSON 字段 tenant_id/K_memory/raw_filter/guard/策略版本。
- 步骤：分别添加单字段 → 尝试改已有字段与未知字段组合。
- 预期：未知字段的拒绝或忽略规则以目标规范为准。无论如何处理，都不能覆盖认证、内部 guard 或服务端策略；客户端不能增加 TopK 或将期限设为无限。

<a id="case-rc-api-09"></a>

### RC-API-09 畸形请求与解析失败（P1）

- 前置：截断 JSON、错误 Content-Type、无请求体、嵌套过深；只用测试端点。
- 步骤：逐项发送 → 检查接纳与结果/事件。
- 预期：解析失败无 Pack/packed、无未捕获崩溃或正文泄露；协议错误按当前契约；大小/深度阈值未明确时标 blocked_requirement。

<a id="case-rc-api-10"></a>

### RC-API-10 重复 JSON 键与实际输入绑定（P1）

- 前置：含重复键 query/sources/token_budget 的原始 JSON 文本。
- 步骤：交换重复键顺序 → 比较解析请求与幂等签名。
- 预期：解析/拒绝符合契约；签名绑定实际执行语义，校验值与执行值一致；重复键规则未明确时标 blocked_requirement。

<a id="case-rc-api-11"></a>

### RC-API-11 正式路由和 HTTP 方法（P1）

- 前置：正常 READ 调用方；准备成功 Pack，保存原文本、Ref/version/hash；测试 POST `/p3/recall`、GET `/p3/recalls/{recall_id}` 和 GET `/p3/recalls/{recall_id}/result`。
- 步骤：POST 获得原 recall_id → GET record 和 result → 分别使用不匹配的 HTTP 方法。
- 预期：三条路由按授权工作；GET 不重做 Recall，错误方法无副作用；HTTP 映射未明确时标 blocked_requirement。

<a id="case-rc-api-12"></a>

### RC-API-12 不存在或非法结果标识符（P1）

- 前置：不存在/格式错误/超长 recall_id、job_id；他人有效 ID 作对照。
- 步骤：分别 GET record、result 和原 job → 比较敏感输出。
- 预期：非法/不存在 ID 按契约处理；无他人正文或存在性泄露；无权与不存在的响应策略未明确时标 blocked_requirement。

## 幂等与接纳

<a id="case-rc-ide-01"></a>

### RC-IDE-01 同一 ID、相同请求体的串行重试（P0）

- 前置：Working 咖啡/茶记忆 v1/g1 Ready；同主体、同 X-Operation-ID、相同请求体。
- 步骤：成功 Recall → 串行重发原 POST 两次 → 比较 job/recall/事件。
- 预期：重试返回或关联原操作，不产生重复 Pack 或 packed。重取时仍按当前授权检查，幂等不能让原内容永久可读。

<a id="case-rc-ide-02"></a>

### RC-IDE-02 同一 ID 绑定不同请求（P0）

- 前置：同 ID，query/selection/sources/预算任一不同。
- 步骤：原请求在接纳已持久化、候选开始前或已终态 → 每个变体重用 ID 改一字段。
- 预期：签名不一致时，不能继续原执行或另行生成成功结果，也不能篡改原请求状态和正文。具体冲突码以目标 Schema 为准。

<a id="case-rc-ide-03"></a>

### RC-IDE-03 同一 ID、相同请求体的并发接纳（P0）

- 前置：原请求已接纳持久化，在候选开始前暂停；两个客户端持同 X-Operation-ID 和请求体。
- 步骤：两客户端同步 POST 同 ID/同体 → 释放候选前屏障 → 轮询双方原 job。
- 预期：只接纳一个业务意图，并关联唯一结果。请求执行中应查询原 job；并发请求不能生成两个成功 Pack 或两份 packed。

<a id="case-rc-ide-04"></a>

### RC-IDE-04 跨主体同字面操作 ID（P0）

- 前置：不同 tenant 或同 tenant/app 的不同 user/agent；各有独立 Ready 记忆；使用相同字面的 X-Operation-ID。
- 步骤：交错提交相同字面 ID → 各取结果。
- 预期：不返回其他主体/tenant 的结果、签名或元数据；跨主体幂等命名空间/冲突规则未明确时标 blocked_requirement。

<a id="case-rc-ide-05"></a>

### RC-IDE-05 缺失与畸形操作 ID 头（P1）

- 前置：X-Operation-ID 省略/空/错误格式/重复 header。
- 步骤：逐项发合法体 → 检查 Record 和接纳。
- 预期：有效操作有稳定原 ID，拒绝请求无孤立成功记录；必填/生成/格式/重复头规则未明确时标 blocked_requirement。

<a id="case-rc-ide-06"></a>

### RC-IDE-06 结果提交后响应丢失（P0）

- 前置：Working 记忆 Ready；结果和 Outbox 已提交，在 HTTP 发送前丢弃响应。
- 步骤：丢响应 → 只查询原 job/recall → 同 ID 同体重试。
- 预期：原 Pack/packed 保留，不重复执行或计热。未送出的响应不能记为客户端已读；GET 原结果时仍须复验资格。

<a id="case-rc-ide-07"></a>

### RC-IDE-07 撤权后幂等回放（P0）

- 前置：成功原 Pack 已保存；撤销原发起人的 READ 并等待权威回执。
- 步骤：撤权等待权威回执 → 同 ID 原 POST → GET 原 result。
- 预期：幂等/结果重取路径均不得泄露旧正文；不能回放撤权前 HTTP 缓存绕过当前授权；内部原事实可保留。

<a id="case-rc-ide-08"></a>

### RC-IDE-08 JSON 表达差异与幂等签名（P1）

- 前置：同 ID 同语义 JSON 字段顺序/空白不同；中文不同 Unicode 序列对照。
- 步骤：提交原请求 → 各变体重发 → 比较实际签名。
- 预期：签名与实际执行内容一致，不同输入不意外复用结果；JSON/Unicode 规范化规则未明确时标 blocked_requirement。

## 权限与范围

<a id="case-rc-auth-01"></a>

### RC-AUTH-01 凭据缺失或认证失效（P0）

- 前置：Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”，v1/g1 Ready；无 Bearer/错误凭据/已过期权限主体。
- 步骤：POST → GET 已知原 Record/result → 检查推理/正文调用。
- 预期：认证或有效授权未通过时，不能交付候选或正文，也不能生成成功 Pack。具体 401/403 映射与过期机制以目标接口为准。

<a id="case-rc-auth-02"></a>

### RC-AUTH-02 只有写权限的身份发起召回（P0）

- 前置：调用方同 scope，仅 WRITE、无 READ；维护者准备 Working v1/g1 Ready 记忆。
- 步骤：仅 WRITE 身份 Recall → 检查 result；维护者核对权限检查记录。
- 预期：WRITE 不能隐含 READ；无越权正文或成功 Pack；权限检查证据在合法维护视图。

<a id="case-rc-auth-03"></a>

### RC-AUTH-03 跨租户同名 ID 隔离（P0）

- 前置：T-A/T-B 同字面 user/session/memory_id；合法目标分数低于外 tenant/user/session/source/space 对象，服务端 K=1。
- 步骤：正常调用方（T-A/App-A/User-A/Agent-A，有 READ，指定 session/task）与另一 tenant 的调用方（字面 user/session/memory_id 可相同）同 query → 各取 result。
- 预期：每个请求只返回自己 tenant 授权 Ref；去重/缓存/job 查找不跨 tenant 碰撞；外 tenant 高分不挤掉合法候选。

<a id="case-rc-auth-04"></a>

### RC-AUTH-04 同租户跨应用隔离（P0）

- 前置：同 tenant 不同 application；合法目标分数低于外 tenant/user/session/source/space 对象，服务端 K=1。
- 步骤：正常调用方（T-A/App-A/User-A/Agent-A，有 READ，指定 session/task）查 App-A → selection 尝试选择 App-B。
- 预期：selection 只能收窄；App-B 受限正文不能入候选/模型/Pack；合法显式共享见 [RC-AUTH-09](#case-rc-auth-09)。

<a id="case-rc-auth-05"></a>

### RC-AUTH-05 用户与 Agent 隔离（P0）

- 前置：同 tenant/app 的不同 user/agent；各有高分同主题记忆。
- 步骤：两主体使用同 query → 改变 agent 选择 → 比较候选/模型输入/结果 scope。
- 预期：按当前授权保持 user/agent 边界，不能只过滤 tenant 就混入其他主体。有效 grant 必须有真实资格证据。

<a id="case-rc-auth-06"></a>

### RC-AUTH-06 会话和任务范围交集（P0）

- 前置：同一 READ 调用方的两个 session、两个 task；内容相近，选择范围外对象分数更高。
- 步骤：限定 S1 或 J1 Recall → 去掉/改变 selection 分别测试。
- 预期：结果仅含 selection 与授权交集，不利 Top1 仍发现合法候选；空选择范围未明确时标 blocked_requirement。

<a id="case-rc-auth-07"></a>

### RC-AUTH-07 客户端身份覆盖尝试（P0）

- 前置：任意身份字段/原始 scope 从客户端注入；合法目标分数低于外 tenant/user/session/source/space 对象，服务端 K=1。
- 步骤：在原请求体中加入其他主体的 tenant/user/agent 字段 → 检查认证上下文/过滤。
- 预期：不覆盖 Bearer 主体/home_scope，不接受伪造 grant/auth_epoch；未知字段处理未明确时标 blocked_requirement。

<a id="case-rc-auth-08"></a>

### RC-AUTH-08 索引命中不得授予正文读取权（P0）

- 前置：仅非法索引命中或 payload 伪造授权；Remember 资格核验否决。
- 步骤：使候选命中 → 以 READ+DIAGNOSE 身份检查 Trace/job、资格、正文、事务和事件看 qualify/read/rerank。
- 预期：vector payload 不能授予正文读取权。越权正文不能发送给 reranker，被排除对象的 ID 和原始向量也不能暴露给调用方。

<a id="case-rc-auth-09"></a>

### RC-AUTH-09 有效共享的可发现性（P0）

- 前置：Working v1/g1 Ready 记忆显式共享给独立主体，仅授予指定记忆 READ；另有未共享同主题记忆。
- 步骤：确认有效 grant → 共享主体 Recall 并取 result → 相同 query 使用无 grant 身份对照。
- 预期：可发现/交付被共享的精确当前版本，仅限有效 grant 范围；不扩大到其他记忆/tenant，不把共享当所有权。

<a id="case-rc-auth-10"></a>

### RC-AUTH-10 共享撤销后的新旧读取（P0）

- 前置：[RC-AUTH-09](#case-rc-auth-09)成功后撤销 grant；旧向量/Redis 仍在。
- 步骤：撤销共享并等待权威回执 → 新 Recall → 重取原 result。
- 预期：新请求/已保存结果均阻断撤权材料；副本残留不授权；撤权前真实 read 事实不被改成未发生。

<a id="case-rc-auth-11"></a>

### RC-AUTH-11 租户停用后重新启用的 epoch 校验（P0）

- 前置：已保存成功原 Pack，留存原文本、Ref/version/hash、来源、关系 revision 和 epoch；tenant 启用→停用→重启用，身份 epoch 递增。
- 步骤：停用后 POST/GET → 重启用仍持旧凭据 → 换新 epoch。
- 预期：租户停用后不能读取；重新启用也不能让旧 epoch 恢复有效。新 epoch 按当前资格执行，不能因为 tenant 名称相同而沿用旧授权快照。

<a id="case-rc-auth-12"></a>

### RC-AUTH-12 请求中身份重载和撤权（P0）

- 前置：Working 记忆 Ready；Recall 在组包完成、final_guard 前暂停；身份文件 revision/auth_epoch 可合法递增。
- 步骤：撤销发起者 READ，递增 revision/auth_epoch 并确认重载 → 放行提交。
- 预期：final_guard/Recall 接口层 身份复验阻断旧 Pack，不能仅开头认证一次；已 read 可保留但不成功 packed。

<a id="case-rc-auth-13"></a>

### RC-AUTH-13 重排前撤权阻断模型输入（P0）

- 前置：Working 记忆 Ready，reranker 开启；在完整正文读取后、rerank 前暂停。
- 步骤：撤对象 grant → 放行重排 → 检查模型实际输入摘要。
- 预期：已撤权正文不进入重排模型，也不进入后续 Pack。

<a id="case-rc-auth-14"></a>

### RC-AUTH-14 诊断权限与结果所有权（P0）

- 前置：保存成功原 Pack 及 trace/recall/job；分别准备发起者、READ+DIAGNOSE、仅 DIAGNOSE、同 tenant/app 的另一 user/agent。
- 步骤：各身份 GET 诊断/Record/result → 比较字段。
- 预期：DIAGNOSE 不授予正文 READ 或他人 Trace 权限；非发起者不穿透 scope；Record 可见性/HTTP 映射未明确时标 blocked_requirement。

<a id="case-rc-auth-15"></a>

### RC-AUTH-15 仅权限阻断与正常空区分（P0）

- 前置：一组匹配对象均无 grant；另一组授权范围内无记忆，来源正常且无 pending/failed。
- 步骤：两请求对照 → 观察公开原因与受控阶段。
- 预期：无越权正文，不泄露受限 ID/计数/存在性；权限阻断与正常空的公开响应规则未明确时标 blocked_requirement。

## 来源选择与覆盖

<a id="case-rc-src-01"></a>

### RC-SRC-01 Working 必须真实向量检索（P0）

- 前置：Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”，v1/g1 Ready+长期记忆“用户每周二上午参加项目评审；遇节假日顺延。”，Ready；sources=working。
- 步骤：POST `/p3/recall` → 以 READ+DIAGNOSE 身份检查 Query 编码/搜索来源 → 核对正文。
- 预期：只检索 Working，且实际执行原生 Query 编码与向量搜索。不能按时间直接列出 Working，或查询长期来源补足数量；结果中没有长期来源的贡献或条目。

<a id="case-rc-src-02"></a>

### RC-SRC-02 长期来源独立检索（P0）

- 前置：Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”，v1/g1 Ready+长期记忆“用户每周二上午参加项目评审；遇节假日顺延。”，Ready；sources=long_term。
- 步骤：提交 → 查看 source filter 和 Ref 类型。
- 预期：只用已就绪长期来源；不把 Working 当前正文冒充长期，scope/space/source 先于截断。

<a id="case-rc-src-03"></a>

### RC-SRC-03 both 来源完整执行与融合（P0）

- 前置：Working 和长期记忆均 Ready；同 scope 的固定候选 W=[A v2,B v1]、L=[C v1,A v2]；关闭重排，sources=both。
- 步骤：提交 → 查看两路计划/覆盖/融合。
- 预期：两路都实际执行，并保留各自的来源贡献。只执行一路不能标为完整 both；相同精确 Ref 只入包一次。

<a id="case-rc-src-04"></a>

### RC-SRC-04 auto 有会话或任务（P1）

- 前置：auto；分别有 session、有 task、两者都有。
- 步骤：三个独立请求 → 记录选择原因/selected_sources。
- 预期：三个变体都选 both；原因可追溯；不能由故障/命中数量回写成 working 或 long_term。

<a id="case-rc-src-05"></a>

### RC-SRC-05 auto 无上下文（P1）

- 前置：auto；无 session/task；scope 合法。
- 步骤：提交 → 检查计划和原因。
- 预期：选择 long_term；不能因为最近存在 Working 记忆而自动检索 Working。selected_sources 和策略版本与实际执行一致。

<a id="case-rc-src-06"></a>

### RC-SRC-06 指定 Working 不改查长期（P0）

- 前置：只有长期 Ready，Working 正常空；显式 working。
- 步骤：请求 working → 查看长期调用计数 → 显式 long_term 作对照。
- 预期：working 可正常空，但不得自动改查长期；对照证明长期夹具真实可查。

<a id="case-rc-src-07"></a>

### RC-SRC-07 索引 pending 不伪装空（P0）

- 前置：Working 仅 pending，无可交正文；暂停投影发布。
- 步骤：暂停投影发布 → 发 working → 查询来源与原 job。
- 预期：pending 不返回正常 Empty，不以正文列表兜底；保留索引未就绪状态和原 ID；等待/终态策略未明确时标 blocked_requirement。

<a id="case-rc-src-08"></a>

### RC-SRC-08 索引 failed 不伪装空（P0）

- 前置：所选来源仅 failed，无可交正文；权威存储仍可读。
- 步骤：制造本例投影失败 → 请求所选来源。
- 预期：保留 failed 覆盖状态和实际原因，不能返回正常空或改为词法检索成功。索引失败与依赖不可达应能分别定位。

<a id="case-rc-src-09"></a>

### RC-SRC-09 可用与 pending 混合覆盖（P1）

- 前置：Ready+pending；部分结果允许/禁止两套已确认策略。
- 步骤：各策略新请求 → 检查可用 Pack 和覆盖。
- 预期：pending 可观测；按已确认策略等待或部分交付，不能标为完整覆盖；策略未明确时标 blocked_requirement。

<a id="case-rc-src-10"></a>

### RC-SRC-10 可降级单路故障（P0）

- 前置：both；Working 可用；长期超时/反向变体；策略允许部分。
- 步骤：隔离故障一路 → 提交 → 核对 selected_sources 与 degraded 原因。
- 预期：只交完整有效的正常一路，并明确缺失来源；不称完整 both；不能返回受限/过期候选凑数。

<a id="case-rc-src-11"></a>

### RC-SRC-11 必需来源故障拒绝部分结果（P0）

- 前置：与[RC-SRC-10](#case-rc-src-10)相同，但策略不允许部分。
- 步骤：单路故障 → 有有效另一路也提交。
- 预期：请求失败/不可用按实际契约；不能补满数量掩盖必需来源故障；无成功 packed。

<a id="case-rc-src-12"></a>

### RC-SRC-12 全部向量来源不可用（P0）

- 前置：所选全部来源 Milvus 不可用；Remember 正文接口仍可用。
- 步骤：请求 working/long_term/both 分别 → 检查候选发现路径。
- 预期：各请求按向量依赖失败，不改词法/已知 ID 读取/直接列举；正文可读不能证明语义 Recall 成功。

<a id="case-rc-src-13"></a>

### RC-SRC-13 正常空与空加故障对照（P1）

- 前置：授权范围内无记忆，所选来源正常且无 pending/failed；both 两路均正常结束无有效匹配；另一变体正常空+一路超时。
- 步骤：两变体新请求 → 对照覆盖/原因/Pack。
- 预期：所选来源全部正常结束且无有效匹配才为正常空；正常空加故障保留故障状态，不合并为 Empty。

<a id="case-rc-src-14"></a>

### RC-SRC-14 两路完成顺序与迟到结果（P1）

- 前置：both 固定候选；让 W 先完成/L 先完成轮流。
- 步骤：控制各路返回顺序 → 比较相同最终候选/融合/覆盖。
- 预期：两路完成的先后顺序不能决定来源优先级或融合分数。超过期限的晚到结果按故障或终态规则处理，不能改变已提交 Pack。只有输入和策略固定时，才比较排名是否一致。

## Query Embedding 与模型空间

<a id="case-rc-emb-01"></a>

### RC-EMB-01 原生 Query 向量及空间绑定（P0）

- 前置：Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”，v1/g1 Ready/长期记忆“用户每周二上午参加项目评审；遇节假日顺延。”，Ready；已绑定真 BGE-small-zh-v1.5 空间。
- 步骤：分别发起 working 和长期来源的查询 → 核对向量维度、范数、模型/权重/tokenizer/usage 与输入摘要。
- 预期：当前配置输出 512 维、归一化、有限向量；Query usage/模型/权重/tokenizer/input_hash 正确，与 Passage 空间兼容；范数容差未明确时标 blocked_requirement。

<a id="case-rc-emb-02"></a>

### RC-EMB-02 Query 用途与双重前缀（P0）

- 前置：同模型查询文本；Query/Passage 前缀配置可观测。
- 步骤：记录实际模型输入 → 对照绑定前缀 → 重复 Query。
- 预期：Query 严格按绑定规则格式化，无双重前缀/Passage 误用；不强求同文本两 usage 向量不同；输入指纹稳定。

<a id="case-rc-emb-03"></a>

### RC-EMB-03 模型实际输入长度边界（P0）

- 前置：tokenizer 实际上限 L，配置上限 Lc；含用途前缀后的计数。
- 步骤：构造 L-1/L/L+1 输入 → 逐项编码 → 配置 Lc 高于真实 L 对照。
- 预期：公开实际有效输入上限。超长输入应明确拒绝，不能截断；前缀和模型包装是否计入 L 按当前 tokenizer 规则判断，不按字符数判断。

<a id="case-rc-emb-04"></a>

### RC-EMB-04 相同空间 ID 配置冲突（P1）

- 前置：同 space ID 换权重/tokenizer/前缀/normalization/distance 之一。
- 步骤：在独立副本启动 → 检查 binding 错误 → 原环境重取。
- 预期：同 ID 不同配置拒绝/空间不兼容明确；不改标签冒充原空间；原数据不清空、不重写原索引。

<a id="case-rc-emb-05"></a>

### RC-EMB-05 同维度异模型空间拒绝（P0）

- 前置：新模型与原模型都 512 维、权重不同，旧投影残留。
- 步骤：查询新空间 → 受控返回旧空间块 → 核验结果。
- 预期：维度相同不等于兼容；拒绝旧空间候选，不混排、不关联旧分数与新正文。

<a id="case-rc-emb-06"></a>

### RC-EMB-06 非法向量及归一化（P0）

- 前置：严格编码提供方返回 NaN/+Inf/-Inf/零向量/非单位范数。
- 步骤：每个变体单独请求 → 检查输出验证与下游搜索。
- 预期：非法向量被拒，不发送 Milvus、不生成 Pack；归一化容差未明确时标 blocked_requirement。

<a id="case-rc-emb-07"></a>

### RC-EMB-07 编码结果身份和输入绑定（P0）

- 前置：严格提供方错 operation_id/usage/model_space/input_hash。
- 步骤：仅错一个绑定字段 → 提交 → 查看阶段错误。
- 预期：输出与请求的绑定不匹配时，判为契约错误。不能关联到其他请求，也不能以依赖 fallback 掩盖错误。

<a id="case-rc-emb-08"></a>

### RC-EMB-08 批编码数量索引与维度（P1）

- 前置：批结果缺条/多条/重复 index/跳号/顺序错位/维度 511 或 513。
- 步骤：逐个变体提供非法批 → 比较实际输入/输出核验。
- 预期：核验输出数量、连续 index、输入输出对应关系和维度。不能因 zip 配对而丢失条目，也不能将其他输入的向量配给当前 query。

<a id="case-rc-emb-09"></a>

### RC-EMB-09 模型缺失不词法兜底（P0）

- 前置：模型权重缺失/不可读/不兼容 ONNX 包；已有 binding 不变。
- 步骤：独立部署启动或加载 → 尝试 Recall。
- 预期：明确模型/依赖不可用；无词法向量兜底，无与实际能力不符的健康状态。原 PyTorch 目录不自动当 ONNX 包使用。

<a id="case-rc-emb-10"></a>

### RC-EMB-10 CPU 超时槽位生命周期（P1）

- 前置：慢 CPU 编码已启动、尚未结束；确认推理槽容量 N。
- 步骤：让请求超时但内核仍跑 → 提交另 N 个请求 → 放行内核。
- 预期：请求超时后，仍在运行的内核继续占用真实槽位，不能超容量并行。晚到向量不能改变终态；内核结束后容量可回收，不持续泄漏。

<a id="case-rc-emb-11"></a>

### RC-EMB-11 参数构造已消耗 deadline（P0）

- 前置：请求在参数构造前已耗期限；慢初始化可控。
- 步骤：耗尽剩余 deadline → 到达 native 调用 → 观察阶段/终态。
- 预期：参数构造和初始化耗时计入原 deadline；不能在 CPU 开始时重置为完整预算；过期结果不得提交，阶段与期限证据可追溯。

<a id="case-rc-emb-12"></a>

### RC-EMB-12 撤权删除后迟到编码（P0）

- 前置：Embedding 已启动、CPU 未结束时撤权/删除/期限届满，分别执行。
- 步骤：确认 CPU 已启动 → 分别撤权、删除或耗尽原 deadline，等待变更回执 → 放行晚到向量。
- 预期：晚到编码不能绕过当前资格或复活期限已结束的请求；撤权/删除后不交相关 Pack；期限届满后不提交成功；保留原输入、ID、资格变更时点及实际 CPU 结束证据。

<a id="case-rc-emb-13"></a>

### RC-EMB-13 补取与重试 Query 固定性（P1）

- 前置：同一检索过程发生当前分页补取；both 来源或 Activity 重试另作变体。
- 步骤：记录每次 query 摘要/space/vector → 触发补取/重试。
- 预期：同一分页检索保持 Query/space/vector；跨来源及 Activity 重试的编码复用规则未明确时标 blocked_requirement。

<a id="case-rc-emb-14"></a>

### RC-EMB-14 重复推理及浮点容差（P2）

- 前置：同输入固定权重、硬件/精度/线程均记录。
- 步骤：连续 3 次 Query → 比较绑定与有限向量 → 更换精度独立空间对照。
- 预期：绑定/计数稳定，向量满足契约；不混用空间；浮点容差未明确时标 blocked_requirement。

## 向量搜索与多块候选

<a id="case-rc-sea-01"></a>

### RC-SEA-01 过滤先于 TopK 的不利对照（P0）

- 前置：服务端每来源 K=1；合法目标分数低于范围外 tenant/user/session/source/space 对象。
- 步骤：直接受控查询证明不利 Top1 → 正式 Recall → 记录 filter 与合法候选。
- 预期：scope/model_space/memory_source 在各来源截断前过滤；合法低分候选仍被发现；不先全库 Top1 再丢弃而报空。

<a id="case-rc-sea-02"></a>

### RC-SEA-02 Working 与长期投影搜索 binding（P0）

- 前置：Working/长期共享实际 Milvus 后端与空间，来源内容相同。
- 步骤：W/L 分别请求 → 查看 provider binding、source filter。
- 预期：搜索与 Remember 投影使用同一兼容 binding，来源之间不能混用。分别使用两个不一致的向量副本，即使命中内容相同，也不能判为绑定正确。

<a id="case-rc-sea-03"></a>

### RC-SEA-03 多块按记忆占位与最高分（P0）

- 前置：同 ID 的长正文分成多个合格块，含 BEGIN/MIDDLE/END；另有两个独立 ID；K=3。
- 步骤：固定块命中 → qualify → 候选/正文组包。
- 预期：同一 scope+ID 只占一个主候选，候选分数取最高合格块分数，不累加块数。完整正文只入包一次，块不能作为独立记忆。

<a id="case-rc-sea-04"></a>

### RC-SEA-04 不同版本批次分数隔离（P0）

- 前置：同 ID 的 v1/g1 与 v2/g2 混合命中；当前为 v2/g2。
- 步骤：返回旧高分和新低分块 → 候选核验 → 查看 score 与 Ref。
- 预期：不跨版本/批次加分，旧版本的高分不能用于当前正文；候选只用 B 允许的精确版本与已发布 generation。

<a id="case-rc-sea-05"></a>

### RC-SEA-05 仅合格块参与候选分数（P0）

- 前置：同 ID 合格与被排除块混排；被排除块最高分。
- 步骤：控制 qualify 一块 excluded → 聚合/排序。
- 预期：候选 score 只来自合格块；被排除块不得留下排名优势；不泄露受限块 ID 给调用方。

<a id="case-rc-sea-06"></a>

### RC-SEA-06 不足记忆 K 的有界补取（P1）

- 前置：第一页全被同 ID/旧版本占满，下一页有其他合格 ID。
- 步骤：服务端 K=3 → 控制分页 → 检查补取次数/停止。
- 预期：在当前页、块、轮数和期限限额内补取。第一页长度≥K 不代表已取得 K 条记忆；不能为凑足数量而放宽权限。

<a id="case-rc-sea-07"></a>

### RC-SEA-07 页、块、轮数的独立上限（P1）

- 前置：当前独立页/块/轮限额各 N，K 满足或不足两类。
- 步骤：各限额 N-1/N/N+1 输入 → 单独触发一上限 → 以 READ+DIAGNOSE 身份检查 Trace/job、资格、正文、事务和事件。
- 预期：各上限独立生效，到限停止且原因可查，不超限查询；N/计数规则未明确时标 blocked_requirement。

<a id="case-rc-sea-08"></a>

### RC-SEA-08 重复乱序页与无进展（P1）

- 前置：重复块、重复整页、乱序页面；数据稳定。
- 步骤：重复返回同页直到限额 → 检查不同块数与终态。
- 预期：重复块和页面不重复占位或计分，也不能造成无限循环。记录无进展或触及限额的原因，不将其解释为已遍历全库；重复页不能增加有效数量。

<a id="case-rc-sea-09"></a>

### RC-SEA-09 搜索引用或元数据不合法（P0）

- 前置：hits 缺 Ref/version/hash/generation、错 scope、重复冲突元数据。
- 步骤：逐项返回非法搜索响应 → 检查 qualify/read 调用及阶段错误。
- 预期：格式或身份信息不完整时，不作为可用候选；错 scope 不入 Remember 正文/模型；契约错误不能当普通无匹配。

<a id="case-rc-sea-10"></a>

### RC-SEA-10 非法分数与距离方向（P1）

- 前置：搜索相似度 NaN/Inf；提供方 distance 越小越近。
- 步骤：非法分数 → 固定距离 0.1/0.4 对照。
- 预期：非有限值被拒；若 distance 已绑定则统一相关性次序单调正确；不混不同度量/空间分数；精确转换函数按提供方契约。

<a id="case-rc-sea-11"></a>

### RC-SEA-11 候选同分稳定性（P1）

- 前置：两合格 ID 相同最高分；全输入固定；改变块/页顺序。
- 步骤：各排列检索 → 比较候选排名。
- 预期：同分不重复占位、不串 Ref/版本；固定输入按确认规则排序；破同分规则未明确时标 blocked_requirement。

## Remember 资格消费

<a id="case-rc-qua-01"></a>

### RC-QUA-01 合法资格目标与结果全覆盖（P0）

- 前置：hits 含多个精确 targets；Remember allowed 并给匹配 manifest/guard。
- 步骤：核对 qualify 请求非空/去重/purpose → 检查响应覆盖 → 组包。
- 预期：targets 与 Ref/generation/space/body_hash/chunk_index/vector_id/input_hash 精确绑定。allowed 必须附带完整且匹配的证据；qualify 不产生 read 或热度。

<a id="case-rc-qua-02"></a>

### RC-QUA-02 allowed 证据缺失错配（P0）

- 前置：allowed 但 manifest/guard 缺失或字段不符。
- 步骤：逐项改变证据 → 继续 Recall。
- 预期：证据缺失或错配时，不能作为合格候选。该错误属于契约错误，应与 excluded 区分；不能仅凭 decision=allowed 就读取或交付正文。

<a id="case-rc-qua-03"></a>

### RC-QUA-03 非 allowed 非法附证据（P1）

- 前置：excluded/unverifiable 携带不应有 manifest 或 guard；非法 decision。
- 步骤：每个变体返回 → 查看阶段结果。
- 预期：非法 decision/附证据报契约错误，不改为 allowed；合法 unverifiable 按 [RC-QUA-05](#case-rc-qua-05) 核验。

<a id="case-rc-qua-04"></a>

### RC-QUA-04 资格结果缺项、重复、额外或错配目标（P0）

- 前置：B 响应缺项/重复/额外目标/错目标、顺序变化对照。
- 步骤：给 2 targets → 逐个变体回应。
- 预期：响应须精确覆盖全部目标；缺项、重复和错配应报契约错误。若协议允许乱序，必须按 target 配对，不能按位置 zip 导致错配；顺序要求以实际契约为准。

<a id="case-rc-qua-05"></a>

### RC-QUA-05 excluded 与 unverifiable 区分（P0）

- 前置：allowed+excluded+unverifiable；部分策略允许/禁止。
- 步骤：返回合法三态 → 比较来源覆盖与结果。
- 预期：excluded 是资格否决，unverifiable 是无法核验，后者影响覆盖不能伪装无结果；策略允许才交可信部分；不公开敏感排除原因。

<a id="case-rc-qua-06"></a>

### RC-QUA-06 同记忆矛盾证据有界重验（P0）

- 前置：同批两个块同记忆却给冲突权威 view/版本/guard。
- 步骤：首次冲突 → 重验一次仍冲突 → 记录次数。
- 预期：按已确认的一次重验契约最多重验一次；仍冲突则排除并降低覆盖，不混用 view、不无限重验；次数契约未确认时标 blocked_requirement。

<a id="case-rc-qua-07"></a>

### RC-QUA-07 旧版本未发布墓碑等资格（P0）

- 前置：同 ID v1/g1 残留、v2/g2 当前 Ready，另备 v3 pending 及错 hash/space/generation；旧/pending/failed/墓碑/过期/撤源逐状态。
- 步骤：搜索仍返回该 Ref → 真实 Remember 资格核验 → 检查正文/Pack。
- 预期：各状态按权威资格处理而非索引存在即 Ready；不回退旧版本凑 K；依赖失败不能记成普通资格排除。

<a id="case-rc-qua-08"></a>

### RC-QUA-08 同批权威视图与后续变化（P0）

- 前置：qualify 批处理中可变更 grant/版本；可暂停在 qualify 完成、正文加载前。
- 步骤：控制批内变更 → 查看所有结果 view → 后续读取/最终守卫。
- 预期：同一批次采用一致的权威视图，后续资格变化须重新核验。不能把旧 epoch 与新 revision 的证据拼成一个成功包。

## RRF 与融合

<a id="case-rc-fus-01"></a>

### RC-FUS-01 RRF 的 rank 起点和常数校验（P0）

- 前置：同 scope 的合法列表 W=[A v2,B v1]、L=[C v1,A v2]；关闭重排，预算足够。
- 步骤：运行融合 → 按 1/(60+rank)人工核算 → 比较阶段分数。
- 预期：A 贡献 1/61+1/62，C=1/61，B=1/62；A>C>B；rank 从 1 不是 0，浮点容差契约明确。

<a id="case-rc-fus-02"></a>

### RC-FUS-02 同路重复不得加分或改排名（P0）

- 前置：W=[A,A,B]，L=[C,A]；同 Ref 重复。
- 步骤：执行 W=[A,A,B]、L=[C,A] → 与 W=[A,B]、L=[C,A] 对照 → 核对名次与 RRF 贡献。
- 预期：同路重复不增加 A 的分数，也不把 B 从第二个独立名次移到第三个。跨来源时，每路对同一 Ref 最多贡献一次。

<a id="case-rc-fus-03"></a>

### RC-FUS-03 跨路精确版本融合（P0）

- 前置：W=A v2、L=A v1 旧+A v2；Remember 允 v2。
- 步骤：核验旧 Ref 排除 → 融合。
- 预期：只有 scope、ID 及精确合格 version 相同才合并；v1 分数/来源不并入 v2；同名跨 tenant 由[RC-AUTH-03](#case-rc-auth-03)阻断。

<a id="case-rc-fus-04"></a>

### RC-FUS-04 单路候选名次保持（P1）

- 前置：单路合法记忆列表，多块数量不同。
- 步骤：单来源执行 → 与本路名次比对。
- 预期：单路按该路候选顺序及已确认策略处理。每个 chunk 不能作为独立 RRF 贡献，也不能伪造另一来源。

<a id="case-rc-fus-05"></a>

### RC-FUS-05 资格过滤后再融合（P0）

- 前置：第一名为旧/撤权 Ref，中间混 unverifiable；其他合法。
- 步骤：过滤资格 → 固定候选融合 → 查看阶段排名。
- 预期：只融合合格候选列表，不让非法 Ref 占正式 rank 或贡献；unverifiable 覆盖仍报告；错误来源不能借其分数抬高可读条目。

<a id="case-rc-fus-06"></a>

### RC-FUS-06 相同正文不同 ID 不得错去重（P1）

- 前置：相同正文不同 ID；两个 Ref 字节相同但来源证据不同。
- 步骤：融合/组包 → 检查 ID 与来源引用。
- 预期：不按 body_hash 错合并记忆或丢来源；候选按精确 Ref 去重；最终正文去重策略未明确时标 blocked_requirement。

<a id="case-rc-fus-07"></a>

### RC-FUS-07 融合与并发回调顺序独立（P1）

- 前置：固定合法列表 W=[A v2,B v1]、L=[C v1,A v2]；互换完成顺序，重复回调。
- 步骤：按[RC-SRC-14](#case-rc-src-14)时序返回 → 比较融合分数/Pack。
- 预期：相同固定合法输入下贡献与排名不依完成顺序，重复回调不加分；相同策略与同分规则须已确认。

<a id="case-rc-fus-08"></a>

### RC-FUS-08 相关性不得等同事实真实性（P2）

- 前置：正文含否定“不加糖”、引述“同事说喜欢糖”、假设“如果加班就喝咖啡”、数字“周二10:00”；相关度高。
- 步骤：查询对应主题 → 核对来源/正文/模型标识。
- 预期：保留原文否定、引述、条件、数字及来源；不把相关性分数当事实置信度，不改写为确定事实。

## CrossEncoder 重排

<a id="case-rc-rer-01"></a>

### RC-RER-01 重排关闭及真实阶段记录（P1）

- 前置：同 scope 的固定候选 Working=[A v2,B v1]、长期=[C v1,A v2]；rerank disabled。
- 步骤：运行 → 检查模型调用次数/健康/阶段。
- 预期：不调用 CrossEncoder，阶段记录准确显示 disabled；按融合顺序和当前组包策略处理，不能记为已完成重排。

<a id="case-rc-rer-02"></a>

### RC-RER-02 重排分数与输入位置对应（P1）

- 前置：required；合法固定 scores 令 C>A>B；真模型单独跑对照。
- 步骤：先严格提供方可算排序 → 再真实 CrossEncoder → 比较输入位置/输出对应。
- 预期：固定分数得到 C>A>B；输出与输入 Ref 一一对应，无缺项/串项；真实模型排序符合其实际分数。

<a id="case-rc-rer-03"></a>

### RC-RER-03 required 重排故障（P0）

- 前置：rerank=required；融合候选完整；分别注入模型不可用、推理超时。
- 步骤：分别注入不可用、超时 → 放行完整融合候选 → 检查终态/Pack/降级。
- 预期：必需的重排模型不可用或超时时，请求失败。不能直接回退 RRF 并标为正常成功，也不能将依赖故障解释为空结果或全部候选不相关。

<a id="case-rc-rer-04"></a>

### RC-RER-04 fallback 可恢复故障（P0）

- 前置：fallback；可恢复模型故障或 deadline 超时；融合正常。
- 步骤：注入故障 → 检查 Pack 顺序/降级。
- 预期：只有已确认可降级的错误，才回退到原融合顺序，并记录 degraded 及原因。required 模式失败不能使用 fallback。

<a id="case-rc-rer-05"></a>

### RC-RER-05 非法重排输出不得降级（P0）

- 前置：fallback；NaN/Inf、少/多 score、错输入数量/位置。
- 步骤：逐个非法响应 → 查看契约错误与 Pack。
- 预期：非法分数和数量错配属于契约错误，不能 fallback 成功。不能将错误 score 置为 0、删掉条目或重新配对 Ref。

<a id="case-rc-rer-06"></a>

### RC-RER-06 Query 正文配对长度边界（P0）

- 前置：配对输入有效上限 L；query 与完整 body 合计含模型包装。
- 步骤：构造 L-1/L/L+1 → 查询 → 检查实际模型输入。
- 预期：按 CrossEncoder tokenizer 校验完整配对长度；超限明确失败，不截断 query/body；长文处理规则未明确时标 blocked_requirement。

<a id="case-rc-rer-07"></a>

### RC-RER-07 超时重排内核与容量回收（P1）

- 前置：慢 CPU 在超时后继续；槽容量 N。
- 步骤：超时请求 → 持续提交新请求 → 释放晚计算。
- 预期：实际计算完成前保留槽；活跃内核不超过 N，晚到的 score 不覆盖终态；最终回收；不能假定协程取消后线程也已停止。

<a id="case-rc-rer-08"></a>

### RC-RER-08 重排和完整关系组接线（P0）

- 前置：完整关系组 G={M3,M5}，独立主候选 M1/M4；rerank 开启。
- 步骤：查看输入单元/guard → 合法改组成员排列 → 比较组包。
- 预期：未授权成员不送模型，结果无半组；重排输入单位/补入成员参与/组排序规则未明确时标 blocked_requirement。

## 权威完整正文

<a id="case-rc-body-01"></a>

### RC-BODY-01 中间块命中交完整正文（P0）

- 前置：多块长正文超过 Embedding 单输入限额，含 BEGIN/MIDDLE/END、中文/emoji/换行/否定；仅命中中间块，权威完整正文可读。
- 步骤：发 Recall → 比较 BEGIN/MIDDLE/END 与 Remember 正文 hash → 核对实际读取引用。
- 预期：交付精确版本的权威完整正文，BEGIN/MIDDLE/END 全部保留；不以命中块或块拼接替代全文。

<a id="case-rc-body-02"></a>

### RC-BODY-02 索引 payload 不得替权威正文（P0）

- 前置：Milvus payload 带伪正文，与 Remember 权威正文不同。
- 步骤：受控命中合法 Ref 但 payload 文本 canary 不同 → 查看 Pack/模型输入。
- 预期：只使用 Remember 受控正文，payload 只作候选校验信息；伪正文不入 Pack 或 reranker；绑定 hash 错误按[RC-BODY-04](#case-rc-body-04)处理。

<a id="case-rc-body-03"></a>

### RC-BODY-03 搜索后更正：旧分数不得配新正文（P0）

- 前置：v1 搜索命中并核验后，在正文加载前暂停；可纠错至 v2。
- 步骤：旧候选核验后更正 → 放行 load → 以 READ+DIAGNOSE 身份检查 Trace/job、资格、正文、事务和事件。
- 预期：不把旧分数配新正文；精确版本变化被识别，安全重组/失败按当前策略；不得返回 v1 或混合 v1/v2。

<a id="case-rc-body-04"></a>

### RC-BODY-04 正文指纹、版本或对象修订错配（P0）

- 前置：受控 Remember 返回正文 hash/Ref/version/object_revision 错一项。
- 步骤：逐个变体 load → 最终提交。
- 预期：发现完整性或绑定错误时拒绝该正文，不能正常入包；不能用旧 guard 为新正文的资格作证；错误可定位到 read/qualification 而非无命中。

<a id="case-rc-body-05"></a>

### RC-BODY-05 正文缺失、拒绝读取、损坏或超时（P0）

- 前置：Ceph 对象缺失/拒权限/超时/摘要错；元数据和向量仍 Ready。
- 步骤：分别注入 → 检查来源覆盖/读取错误。
- 预期：存储/完整性故障不伪装正常空、无相关性或可用旧缓存；部分策略只允许交其他完整安全材料，且保留故障原因。

<a id="case-rc-body-06"></a>

### RC-BODY-06 默认无 TTL 与显式到期（P0）

- 前置：分别准备无 expires_at、显式尚未到期、显式已到期的记忆。
- 步骤：未设 TTL 跨测试窗口重查 → 到期前后新/结果重取。
- 预期：无 expires_at 不自动到期；显式到期后阻断新 Recall/结果重取；等号/时钟精度/ε 未明确时标 blocked_requirement。

<a id="case-rc-body-07"></a>

### RC-BODY-07 逻辑删除撤源先于物理清理（P0）

- 前置：逻辑 delete/source revoke 已确认，向量/Redis 物理残留。
- 步骤：新 Recall → GET 原已保存 Pack → 检查 Remember 当前资格。
- 预期：删除/撤源先阻断，新请求和已保存结果均不交残留正文；不等待 Milvus 清理才能生效；已 read 真实事实可保留。

<a id="case-rc-body-08"></a>

### RC-BODY-08 读取成功后下游失败的事实记录（P0）

- 前置：正文已读取成功；可在 rerank、guard、提交阶段注入失败。
- 步骤：确认 read 证据 → 注入后续失败 → 检查事件。
- 预期：保留已实际成功的 read，不因后续失败抹去该事实。不能产生成功 packed 或交付错误正文；read 与 packed 不重复计热。

## 完整关系组

<a id="case-rc-rel-01"></a>

### RC-REL-01 关系补全未命中成员（P0）

- 前置：完整组 G={M3,M5}，关系 r1；M3 命中、M5 未命中；另有独立 M1；预算足够。
- 步骤：核验 M3 及关系 r1 → 补读 M5 → 组包，核对成员和计数。
- 预期：G 包含全部必需有效成员/条件/来源；补入M5不伪造命中分数、不多占独立主候选 K；全部成员分别授权。

<a id="case-rc-rel-02"></a>

### RC-REL-02 缺组成员且有其他安全内容（P0）

- 前置：G={M3,M5}；分别使 M5 无权限、正文缺失、资格失效；另有完整可读独立记忆。
- 步骤：逐缺失原因 → Recall → 查看组与全请求终态。
- 预期：整组跳过，无半组或受限成员泄露；其他完整可信内容可交；无法核验/读取故障保留覆盖原因；公开终态/错误映射未明确时标 blocked_requirement。

<a id="case-rc-rel-03"></a>

### RC-REL-03 唯一必需组缺成员（P0）

- 前置：唯一必需组 G={M3,M5}，M5 缺失；无其他可交材料。
- 步骤：使 M5 缺失或失效并确认回执 → Recall → 检查整个组与终态。
- 预期：整个缺成员组不可交，不单独交命中成员；排除原因/覆盖可查；全部组被排除的终态未明确时标 blocked_requirement。

<a id="case-rc-rel-04"></a>

### RC-REL-04 多个主候选共享同组（P1）

- 前置：两个主候选 M3/M5 均命中同一完整组 G={M3,M5}。
- 步骤：都命中 → relations/组包 → 数正文与 Ref。
- 预期：同一组只输出一次，成员正文不重复。分别记录主候选数与关系补全数；组排序和同分规则由当前策略确认。

<a id="case-rc-rel-05"></a>

### RC-REL-05 关系快照缺项、错配或修订变化（P0）

- 前置：Remember 关系快照 guards 缺/重复/错 Ref，relations_revision 与正文读取后不符。
- 步骤：逐个非法快照 → plan/commit。
- 预期：无法证明完整快照则不交相关材料；必须核对对象、关系、授权修订；仅有旧 GuardStamp 不构成永久授权。

<a id="case-rc-rel-06"></a>

### RC-REL-06 组包后关系新增成员（P0）

- 前置：组包完成、final_guard 前暂停；G={M3,M5} 可新增必需 M6；分别使 M6 导致超预算、无权限。
- 步骤：plan 含 M3/M5 → 发布 r2 加入 M6 → 放行 final_guard。
- 预期：发现关系修订，不只验证旧成员仍存在；原 G 不得按旧预算交付；重组或拒绝按当前规则，不能交半组。

<a id="case-rc-rel-07"></a>

### RC-REL-07 重叠、歧义或循环关系（P1）

- 前置：重叠/歧义冲突组、循环关系；提供方说明必需集合。
- 步骤：构造受控关系 → plan → 检查停止与输出。
- 预期：歧义重叠组不拆分交付，不无限递归；成员数/深度上限未明确时标 blocked_requirement。

## ContextPack 预算

<a id="case-rc-bud-01"></a>

### RC-BUD-01 整包预算 T-1、T、T+1（P0）

- 前置：单完整组，固定 tokenizer 实测整体渲染 token 数 T（含引用/来源/关系/分隔符）；T-1/T/T+1 均为合法预算。
- 步骤：逐预算新请求 → 独立复算 rendered_context tokens。
- 预期：T/T+1 装入完整组；T-1 为预算不足，不为空、不截断；最终整包按实际渲染复算。

<a id="case-rc-bud-02"></a>

### RC-BUD-02 首组超长继续后组（P0）

- 前置：排名首组 H 超预算，后组 S 可装；来源均正常。
- 步骤：固定候选顺序 → 执行 → 检查 budget 排除与 Pack。
- 预期：跳过超预算的 H，继续尝试并交付完整 S，不能遇到首组超长就 break。预算排除不等于来源故障，不因此单独标为 degraded。

<a id="case-rc-bud-03"></a>

### RC-BUD-03 全部完整组装不下（P0）

- 前置：所有有效完整组均超预算。
- 步骤：确认候选/正文真实可用 → 执行。
- 预期：返回 BUDGET_TOO_SMALL 的业务含义，不能返回正常 Empty 或截断后的内容。分别定位“没有匹配”“内容装不下”和“执行时间耗尽”。

<a id="case-rc-bud-04"></a>

### RC-BUD-04 引用分隔符和来源开销（P0）

- 前置：正文自身能装，但加引用序号/来源/分隔符后超限。
- 步骤：将预算设正文 token 数 → 组包 → 按整个实际文本复算。
- 预期：引用、来源和分隔符均计入预算，不能只数正文 token。最终整包超限时不能成功；模板和 tokenizer 固定，引用可追溯。

<a id="case-rc-bud-05"></a>

### RC-BUD-05 完整冲突组预算不拆分（P0）

- 前置：G={M3,M5} 的单成员装得下，含关系说明的完整组装不下；后续独立组 S 可装。
- 步骤：按实测预算 → 组包。
- 预期：完整组整体预算排除，不能拆成员或删关系说明来装入；继续尝试后续独立完整组，后组装得下应交付；预算排除与资格/正文故障分别记录。

<a id="case-rc-bud-06"></a>

### RC-BUD-06 Unicode 按 token 不按字节（P0）

- 前置：中英文/emoji/组合字/换行正文，字节数差异明显。
- 步骤：current tokenizer 复算 → 与字符/UTF8 字节数对照。
- 预期：整包计数符合当前 tokenizer，不按字节或字符数计量。

<a id="case-rc-bud-07"></a>

### RC-BUD-07 Embedding 与包 tokenizer 隔离（P1）

- 前置：o200k_base 与合法本地 HF tokenizer 配置分别启用。
- 步骤：独立目标/新请求按各 tokenizer → 核对边界。
- 预期：每次请求使用固定 ContextPack 计量配置；不能将 Embedding tokenizer 代替包 tokenizer；对每套实际配置独立复算最终整包边界。

<a id="case-rc-bud-08"></a>

### RC-BUD-08 tokenizer 缺失不字节兜底（P1）

- 前置：tokenizer 文件缺失/损坏/未安装，encoding 未知。
- 步骤：各独立配置启动/Recall → 健康与错误。
- 预期：明确报告预算计量能力不可用，不能改按字节计量，也不能报告 tokenizer 可用。未经实际计数，不能发布超限包。

<a id="case-rc-bud-09"></a>

### RC-BUD-09 整包计数不逐段相加（P1）

- 前置：逐段 token 数之和≠串联整体计数的已验证样本。
- 步骤：记录 A/B 单段与组合计数 → 把预算放差额边界 → 执行。
- 预期：使用整个临时/最终 Pack 计数而非简单相加；既不能发布超限 Pack，也不能因错误合计而跳过实际装得下的组。

<a id="case-rc-bud-10"></a>

### RC-BUD-10 最终条数与关系补入边界（P1）

- 前置：确认最终条数上限 M、关系成员计数规则；准备足够合格短记忆、多块记忆及完整关系组；预算足够。
- 步骤：分别准备 0/1/M-1/M/M+1 个短独立记忆 → 另准备完整关系组令补入成员跨过计数边界 → 新请求组包。
- 预期：最终条数/关系成员计数符合策略，组完整且不超预算；M/计数规则未明确时标 blocked_requirement。

<a id="case-rc-bud-11"></a>

### RC-BUD-11 包预算与下游窗口分配（P1）

- 前置：Caller 另有 system/query/回答预留；提供 ContextPack 专用预算 B。
- 步骤：以 ContextPack 专用预算 B 请求 → 复算最终整包 token 数。
- 预期：整包 token 数≤B；不将调用方 system/query/回答预留或整个模型窗口计入 Pack 额度。

## 已保存结果与 Record 重取

<a id="case-rc-res-01"></a>

### RC-RES-01 有效原包重取不重新搜索（P0）

- 前置：原 Pack 已持久化，全部成员当前有效；保存原文本/Ref/version/hash；可观察 Embedding/搜索调用次数。
- 步骤：GET record → GET result 两次 → 比较原文本/Ref/摘要。
- 预期：返回原 Pack，经现权限复验；不重新 Embedding/向量检索、不随机改顺序/正文；Record 查询不产生新 Recall。

<a id="case-rc-res-02"></a>

### RC-RES-02 纠错后旧包失效不换正文（P0）

- 前置：原 Pack 含两条记忆；其中一条由 v1 纠错为 v2，保存原文本/Ref/version/hash。
- 步骤：纠错确认 → GET 原 result → 新 Recall 对照。
- 预期：原结果 RESULT_INVALIDATED 语义/新请求提示；不能换 v2 正文或裁掉 v1 仍称原包有效；新 Recall 可按当前 v2 独立执行。

<a id="case-rc-res-03"></a>

### RC-RES-03 任一成员失效不得裁剪（P0）

- 前置：原 Pack 含两条；分别删除、显式到期、撤来源、撤共享其中一条。
- 步骤：逐项提交单一失效变更并等待权威回执 → GET 原 result。
- 预期：任一必需项失效阻断整个原包；不能裁去失效条目后仍返回成功、不泄露失效正文；原 Record 中保存的事实不意味着仍有正文读取权限。

<a id="case-rc-res-04"></a>

### RC-RES-04 关系修订后原包复核（P0）

- 前置：原 Pack 包含 G={M3,M5}、关系 r1；可发布 r2 新增必需成员或修改关联。
- 步骤：改变关系 → GET 原 result。
- 预期：复核全部关系/成员与修订，旧组完整性不能靠旧快照延续；不重搜/补成员改写成“原结果”。

<a id="case-rc-res-05"></a>

### RC-RES-05 结果重取时校验当前身份 epoch（P0）

- 前置：已保存成功原 Pack，留存原文本、Ref/version/hash、来源、关系 revision 和 epoch；auth_epoch/tenant 停用变化。
- 步骤：使用旧凭据 GET → 新 epoch 但对象无权 GET。
- 预期：原发起人不永久拥有结果正文的读取权。重取时同时复验当前身份和对象资格，不能只校验 recall.owner_id。

<a id="case-rc-res-06"></a>

### RC-RES-06 绑定稳定时，P3 重启后恢复原包（P0）

- 前置：已保存成功原 Pack，留存原文本、Ref/version/hash、来源、关系 revision 和 epoch；稳定配置和依赖，只重启本例 P3。
- 步骤：保存原 ID/Pack hash → 重启 → 原 Record/result 与新 Recall 各测。
- 预期：重启后原 Pack 恢复且重新授权，失败 job 保持原终态；新 Recall 独立成功，原结果不代替新请求。

<a id="case-rc-res-07"></a>

### RC-RES-07 在途、失败或未知状态下读取结果（P1）

- 前置：原 Recall 仍 running/failed/未知；保留原 job。
- 步骤：各状态 GET Record/result → 直到真实终态。
- 预期：running 不返回未提交 Pack；failed 不以其他成功包替代；未知状态不能解释为失败或未执行。具体 GET 业务码以目标契约为准，不预设 404/202。

<a id="case-rc-res-08"></a>

### RC-RES-08 结果重取并发撤权线性化（P0）

- 前置：原 Pack 当前有效；GET result 可暂停在资格复验和发送之间。
- 步骤：开始 GET 并确认守卫时点 → 撤权 → 放行。
- 预期：按确认的 guard/条件交付协议处理并发撤权，不泄露应阻断正文；GET 线性化点未明确时标 blocked_requirement。

## 最终提交与持久一致性

<a id="case-rc-tx-01"></a>

### RC-TX-01 结果、终态与 packed 原子提交（P0）

- 前置：完整 plan，guard 有效；可观察 guard 后、事务提交前；记录原 operation。
- 步骤：正常提交 → 读取 Pack/Record/packed Outbox 与原 operation。
- 预期：实际渲染结果、终态及 packed 条件原子提交，不能出现成功但无包，或有包却没有 packed 意图的状态。事务内不调用网络 Provider，也不运行 CPU 推理。

<a id="case-rc-tx-02"></a>

### RC-TX-02 最终 guard 与提交间资格变化（P0）

- 前置：Working 记忆 Ready；Recall 可在 guard 后、事务提交前暂停；另一客户端可撤权或删除记忆。
- 步骤：在 guard 后、事务提交前暂停，记录 revision/auth_epoch 和锁 → 独立客户端分别撤权、删除并记录权威提交时刻 → 分别执行资格变更先提交、Recall 先提交的顺序，保留锁阻塞证据 → 核对 Pack/终态/Outbox 及变更后的 GET result。
- 预期：资格变更先提交时阻断旧 Pack；Recall 先提交时保留事实，变更后重取不泄露失效正文；结果/终态/Outbox 条件原子提交。

<a id="case-rc-tx-03"></a>

### RC-TX-03 内部计划与原请求签名绑定（P0）

- 前置：plan 内部受控副本：改主体、ID、query、selection、sources、预算、输入签名、scope、deadline 之一。
- 步骤：逐字段篡改受控计划 → commit。
- 预期：与 Recall 接口层原始请求比对，全部绑定条件复验；篡改不能被当公开新输入或提交他人 Pack；原请求/结果不被改写。

<a id="case-rc-tx-04"></a>

### RC-TX-04 并发 commit 及旧修订拒绝（P0）

- 前置：两 commit worker 持同 plan/revision；另有已完成请求。
- 步骤：两 worker 同步到 guard 后、事务提交前 → 并发 commit → 旧 worker 再提交。
- 预期：通过 Recall 修订条件保证结果与 packed 唯一。拒绝旧 revision 提交，不能重复完成或覆盖原包；合法重试可取得同一原事实。

<a id="case-rc-tx-05"></a>

### RC-TX-05 事务中途失败整包回滚（P0）

- 前置：UoW 写 Pack/终态/Outbox 任一点异常，提交前。
- 步骤：各点注入 PG 失败 → 重启/查询原 ID → 检查三类事实。
- 预期：整事务没有部分成功；已经独立发生的 read 不抹去；恢复沿原意图，不能新 ID 制造重复副作用。

<a id="case-rc-tx-06"></a>

### RC-TX-06 DB 提交成功 ACK 丢失（P0）

- 前置：DB 实际 commit 成功但调用方丢 ACK/进程退出。
- 步骤：提交响应隔离丢失 → 查询原 job/Pack/outbox → 恢复原执行。
- 预期：不能因 timeout 推断事务未提交，也不能重复生成 packed。证据确认前保留未知状态；已 commit 的事实不能回滚，也不能换新 ID 重做。

<a id="case-rc-tx-07"></a>

### RC-TX-07 最终 guard 返回全成员证据（P1）

- 前置：Remember 返回最终 guards 缺项/重复/错 epoch/relation_revision 或发布 manifest。
- 步骤：逐非法最终返回 → commit → 查询原 Record。
- 预期：最终守卫证据须覆盖全部成员，并与长期发布清单匹配。拒绝缺项或错配的证据，不能只验证已返回项而遗漏未返回成员。

<a id="case-rc-tx-08"></a>

### RC-TX-08 守卫请求跨租户重复多版本（P0）

- 前置：内部 ContextGuardRequest 含重复 Ref、跨 tenant、同 ID 多 version；另有长期成员 manifest 缺项。
- 步骤：严格控制面逐项构造 → 交 Recall 提交 → 查询结果与事件。
- 预期：请求约束与全部成员清单均验证，不能只依单成员 allowed；非法守卫请求不成功提交，不制造跨 tenant 或同记忆两版本包。

## Temporal、HTTP 观察与恢复

<a id="case-rc-asy-01"></a>

### RC-ASY-01 HTTP 等待与原 job 异步观察（P0）

- 前置：Working 咖啡记忆“用户喝咖啡不加糖。”及茶记忆“用户喜欢乌龙茶。”，v1/g1 Ready；http_wait 小于正常 Workflow 收敛时间。
- 步骤：提交 → 收到 400 REQUEST_IN_PROGRESS，保存 Location/X-P3-Job-ID → 查询原 `/p3/operations/{job_id}` 至终态。
- 预期：原 job 可查询，HTTP 等待结束不取消 Workflow/不表示失败；终态与结果以持久事实为准；无新 operation。

<a id="case-rc-asy-02"></a>

### RC-ASY-02 客户端断开不取消 Workflow（P0）

- 前置：接纳已持久化、候选开始前/Embedding 已启动、CPU 未结束/完整正文读取后、rerank/组包前时 HTTP 客户端主动断开，各独立请求。
- 步骤：断 TCP → 保持 Workers → 另一客户端查询原 ID。
- 预期：断开连接不等于取消业务，Workflow 按当前策略继续。保留原请求关联；未真正发送的包不能记为客户端已读。

<a id="case-rc-asy-03"></a>

### RC-ASY-03 Temporal 不可达与新接纳（P0）

- 前置：Temporal 在本例环境不可达，HTTP 进程仍活。
- 步骤：断独有 Temporal 连接 → live/readyz → 新 Recall。
- 预期：HTTP 进程可存活；readyz 返回 503 并暂停新接纳；不绕过 Temporal 接纳新的 Recall；在途任务依持久恢复证据观察，不因连接故障直接假定已失败。

<a id="case-rc-asy-04"></a>

### RC-ASY-04 P3 在不同提交边界退出后的恢复（P0）

- 前置：分别在模型返回前、模型返回后提交前、Pack 提交后终止 P3；保存原 ID 和配置 binding。
- 步骤：各边界终止 P3 → 使用原 binding 重启 → 查询原 job → GET 原 Record/result。
- 预期：分类恢复原输入/任务/已提交包，不重复成功事实；未提交迟到结果须当前资格复核；启动不等任务完成。

<a id="case-rc-asy-05"></a>

### RC-ASY-05 Worker 失租后的晚到提交（P0）

- 前置：Worker 旧租约已失，新 Worker 拿新 revision。
- 步骤：暂停旧 Worker 至失租 → 让新 Worker 完成 → 放回旧结果。
- 预期：拒绝旧 fence 的提交，不能覆盖新结果或终态。远端残留不因此成为合格投影；租约、重试和事件 ID 均有证据。

<a id="case-rc-asy-06"></a>

### RC-ASY-06 执行预算与内容预算区分（P0）

- 前置：原 job 执行预算耗尽；另有小 token 预算请求。
- 步骤：分别触发执行 BUDGET_EXHAUSTED 和内容不足 → 查看阶段/终态。
- 预期：执行 BUDGET_EXHAUSTED 与内容 BUDGET_TOO_SMALL 分别定位；超时不记为空；failed/no_effect 与持久事实一致。

<a id="case-rc-asy-07"></a>

### RC-ASY-07 持久周期和 deployment 绑定（P0）

- 前置：原 Pack 已保存且有在途任务；固定 deployment/namespace/queue/periodic binding。
- 步骤：改本例持久 periodic 间隔重启 → 恢复原配置重启 → 原 ID 查询。
- 预期：绑定不一致明确拒绝/readyz503；恢复原绑定能接原任务，不生成新意图；改 request_timeout 不等修 periodic；不修改共享计划。

## 并发竞态与资源

<a id="case-rc-con-01"></a>

### RC-CON-01 搜索后更正版本，新索引仍为 pending（P0）

- 前置：v2/g2 当前 Ready，旧投影仍在；在 Milvus 返回块后、qualify 前暂停；可更正至 v3 pending。
- 步骤：纠错提交 v3 → 放行旧 hits → 最终核验。
- 预期：不能使用 v2 的高分交付 v3 正文、不回退旧版本；新 v3 未发布按真实覆盖处理；末端 guard 也须检查再次发生的版本变更。

<a id="case-rc-con-02"></a>

### RC-CON-02 各阶段删除与迟到投影（P0）

- 前置：Milvus 返回块后、qualify 前/qualify 完成、正文加载前/组包完成、final_guard 前各阶段 delete，另有迟到投影。
- 步骤：等屏障 → delete 回执 → 放行迟到 hit/投影/Recall。
- 预期：迟到投影不能复活已删记忆；新请求和结果重取均受墓碑约束；物理向量残留不改变资格；各阶段分别执行，不能只测一个时点。

<a id="case-rc-con-03"></a>

### RC-CON-03 排队、推理或组包期间撤权（P0）

- 前置：分别在接纳后排队、完整正文读取后重排前、CPU 推理中、组包完成守卫前暂停；可撤销 grant/权限。
- 步骤：每时点独立副本撤权 → 放行 → 检查模型输入/Pack。
- 预期：撤权前真实 read/已开始内核不可事后撤回，后续资格复验禁止新越权交付；完整正文读取后、rerank/组包前撤权禁止送模型；epoch 不在线程切换丢失。

<a id="case-rc-con-04"></a>

### RC-CON-04 关系变化覆盖各读取阶段（P0）

- 前置：关系在读取快照后/正文加载后/组包后分别新增成员。
- 步骤：同步发布 r2 → 放行 → 比较 guard/预算。
- 预期：发现 relations_revision 变化，按当前策略重组并重新计量或拒绝，不沿旧组提交、不无限重组；终态/重组次数未明确时标 blocked_requirement。

<a id="case-rc-con-05"></a>

### RC-CON-05 真实 PG 锁与事件循环心跳（P0）

- 前置：目标部署的真实独有 PG database，锁 owner、任务与心跳均可观察。
- 步骤：受控持锁 1 秒并记录 owner → 同时 Recall/heartbeat/health → 释放并查询原 job。
- 预期：无错误授权/成功，遵守原 deadline；记录最大 pulse gap、锁等待、heartbeat、剩余期限及原 job 终态。

<a id="case-rc-con-06"></a>

### RC-CON-06 并发请求的执行上下文隔离（P0）

- 前置：同 tenant/app 的不同 user/agent 并发请求；各自 scope/epoch/trace/deadline 不同；CPU 推理或重排存在异步边界。
- 步骤：在排队及推理期间交错两请求 → 撤销其中一个请求的权限或使其期限届满 → 放回两份计算输出 → 检查阶段/提交上下文。
- 预期：主体/scope/trace/期限/fence 始终绑定各自请求；不能混淆主体、epoch 或正文；已失效请求晚到输出不成功提交，另一请求依自身资格正常处理。

<a id="case-rc-con-07"></a>

### RC-CON-07 独立操作并发与资源回收（P1）

- 前置：独立操作 ID 并发 1/N/N+1（N=已确认推理/连接容量），同与不同 scope。
- 步骤：同时提交并记录活跃资源 → 撤其中一个权限 → 全部收敛。
- 预期：无串正文、死锁、重复提交或越权；容量失败原因可查，资源最终回收；记录排队时间及终态。

<a id="case-rc-con-08"></a>

### RC-CON-08 热副本变动中的权威读取（P0）

- 前置：Recall 在 load 时 Redis 被本例 Operate 热副本替换/清理。
- 步骤：并发改缓存版本/删除副本 → 读完整正文 → final_guard。
- 预期：缓存变动不改变权威版本/授权；无混合新旧字节，hash/guard 匹配；回源/失败规则未明确时标 blocked_requirement。

<a id="case-rc-con-09"></a>

### RC-CON-09 到期边界与阶段跨时（P1）

- 前置：显式 expiry=t；在 t-ε/t/t+ε读取；权限 expiry 另变体。
- 步骤：固定可控服务时钟精度 → 各阶段跨到期 → 结果重取。
- 预期：t 前后资格按当前规则，最终 guard 跨到期不得发失效 Pack；t 等号与ε取值须约定，不用本机 sleep/时区猜测；未设 expiry 不受默认 TTL。

## 后端与热副本依赖

<a id="case-rc-dep-01"></a>

### RC-DEP-01 必需后端配置与连接失败（P0）

- 前置：PG/Milvus/Redis/Ceph 各自缺少一项实际必需的凭据或权限；TLS 后端另测 CA；独立环境。
- 步骤：逐依赖配置 → check-config/serve/health/业务区分。
- 预期：必需依赖失败可定位，无错误成功或词法兜底；响应脱敏，不写入共享 public/schema。

<a id="case-rc-dep-02"></a>

### RC-DEP-02 TLS 证书和 SNI 校验（P0）

- 前置：当前 PG/Redis/Milvus TLS 连接：过期证书、错误 CA、错 hostname 或 SNI；正确连接为对照。
- 步骤：连接并发 Recall → 用正确证书作对照。
- 预期：校验证书链和服务名，拒绝不可信 TLS 连接，不能自动关闭校验或降为明文。正确配置应能完成 Recall；失败配置不能产生错误成功结果。

<a id="case-rc-dep-03"></a>

### RC-DEP-03 冷集合探针与首次业务（P1）

- 前置：新 Milvus 集合尚不存在；有/无创建加载权限两变体。
- 步骤：只探 health 记录 unknown → 首次实际投影/查询 → 再看 health/Recall。
- 预期：探针只读且返回 unknown；有权限时首次业务准备/加载集合并校验结构，无权限时失败且不报 ready。

<a id="case-rc-dep-04"></a>

### RC-DEP-04 后端绑定不静默切换（P0）

- 前置：已有 binding；改 namespace/database/logical collection/space 之一。
- 步骤：独立候选启动/请求 → 比较真实 provider binding 和原数据。
- 预期：拒绝不兼容的绑定，或要求明确迁移或使用新环境。不能只修改标签后查询空集，就认定迁移完成；不能泄露或清除原数据，也不能仅凭 profile 已修改认定隔离有效。

<a id="case-rc-dep-05"></a>

### RC-DEP-05 精准热副本和坏缓存（P0）

- 前置：分别准备 Redis 精确热副本、缺副本、旧版、错 hash、半正文；Ceph 权威正文完整。
- 步骤：确认缓存状态 → Recall → 检查权威 read/最终 Pack。
- 预期：不交错版本、坏摘要或半正文；按确认策略从 Ceph 精确引用回源或失败；回源规则未明确时标 blocked_requirement。

<a id="case-rc-dep-06"></a>

### RC-DEP-06 缓存 scope 配额与 Recall 输出（P1）

- 前置：Redis scope 配额 C=min(两配置)；完整副本大小 C-1/C/C+1；准备并发副本，Ceph 正文完整。
- 步骤：准备本例缓存准入 → Recall → 比较权威正文与准入状态。
- 预期：按完整 scope 原子计量配额，不使用实例总容量；缓存拒绝后仍保证 Pack 完整正确；回源/失败规则未明确时标 blocked_requirement。

<a id="case-rc-dep-07"></a>

### RC-DEP-07 后端断连恢复各阶段（P0）

- 前置：独有 Milvus/Ceph/PG 连接在 search/read/commit 阶段断后恢复。
- 步骤：各阶段单依赖断连 → 保留原 ID → 恢复查询、新业务对照。
- 预期：来源/读取/提交故障分别真实定位；恢复后仍复验当前版本/授权，不能旧 Pack 冒充新成功；Milvus 重启需实际 collection 加载证据。

## Recall 事实与下游事件

<a id="case-rc-evt-01"></a>

### RC-EVT-01 候选资格不得计正文 read（P0）

- 前置：多块 hits/qualify 成功但不 load（后续故障）。
- 步骤：让请求止于候选 → 只读查看 read/packed/热度输入水位。
- 预期：候选和资格检查不产生正文成功 read/热度；不能以 chunk 数增热；若已发生 body read 须按实际阶段另算。

<a id="case-rc-evt-02"></a>

### RC-EVT-02 重复精确 Ref 读取计热去重（P0）

- 前置：同 recall、同精确 Ref 重复加载/多块/重试。
- 步骤：制造重复读取 → 检查事件与 Operate 接口原输入。
- 预期：成功 read 按该 Ref 版本只计一次热度；read/packed/delivered 不重复累加；事件类型、原 event ID/水位可追溯。

<a id="case-rc-evt-03"></a>

### RC-EVT-03 读过但未入包的条目及 packed/delivered 记录（P0）

- 前置：读取成功但预算排除；另一条入包；结果与 Outbox 提交后、HTTP 发送前丢发送。
- 步骤：固定 S 可装、H 超预算 → 读取后组包 → 在 HTTP 发送前丢响应 → 核对 read/packed/delivered。
- 预期：已读但未入包的条目保留 read，没有 packed；入包条目有 packed。delivered 只表示已交给传输层，不表示客户端已阅读或 LLM 已采用。

<a id="case-rc-evt-04"></a>

### RC-EVT-04 Outbox 提交后退出重投（P0）

- 前置：Pack+Outbox 提交后、投递前退出。
- 步骤：结果和 Outbox 提交后、投递前退出 → 重启 → 检查重投及下游 Inbox。
- 预期：使用原 event_id 重投，已 commit 的事实不丢失。下游对至少一次投递去重，不能重新生成不同 event 造成重复效果。

<a id="case-rc-evt-05"></a>

### RC-EVT-05 消费已提交 ACK 丢失去重（P0）

- 前置：下游消费效果已提交，在 ACK 前丢弃确认。
- 步骤：重投原 read/packed 事件 → 检查 Inbox/热度输入水位。
- 预期：Inbox 与消费效果原子提交；原事件重投获确认但不再次计热。

<a id="case-rc-evt-06"></a>

### RC-EVT-06 跨版本读取与结果重取的事件语义（P1）

- 前置：同 recall、同 ID 两个版本均合法读成功；成功原 Pack 可重复 GET result。
- 步骤：按批准场景读两版本 → 重复 GET 已保存结果 → 观察 read 与 delivery。
- 预期：同精确版本重复读取去重；跨版本/GET result 的 read/delivery 键与计热规则未明确时标 blocked_requirement。

<a id="case-rc-evt-07"></a>

### RC-EVT-07 下游不可用 Outbox 积压（P1）

- 前置：下游 Operate 暂不可达/慢，Recall 已 commit 事实。
- 步骤：停本例消费者或隔离其连接 → Recall → 恢复投递。
- 预期：已提交事实/Outbox 持久，积压可查；恢复投递不重复消费；下游等待对前台终态的影响未明确时标 blocked_requirement。

## 健康与诊断

<a id="case-rc-obs-01"></a>

### RC-OBS-01 Trace 阶段和请求关联（P1）

- 前置：正常、单路故障、预算不足、guard 失效各一请求。
- 步骤：同 scope、有 READ+DIAGNOSE 的诊断身份查受控 Trace/阶段 → 对照原 operation/job/recall。
- 预期：Trace 正确关联编码/候选/资格/读取/融合/重排/预算/guard/终态及原 operation/job/recall；故障不记为正常空；字段映射未明确时标 blocked_requirement。

<a id="case-rc-obs-02"></a>

### RC-OBS-02 日志响应秘密与正文隔离（P0）

- 前置：query/body/header 放无敏感 canary；故障异常堆栈。
- 步骤：运行与失败 → 检查普通响应、技术日志、诊断权限。
- 预期：不默认输出正文、Bearer、DSN 密码、model key；普通调用方无受限 ID/计数；摘要不能轻易泄露完整秘密。

<a id="case-rc-obs-03"></a>

### RC-OBS-03 重排 tokenizer 健康状态（P1）

- 前置：rerank disabled/unknown/loaded/required 不可用；tokenizer 未加载。
- 步骤：各状态真实探测 → 对照实际模型调用/能力。
- 预期：健康状态准确区分 disabled、unknown 和可用。必需重排不可用时，不能报告完整召回能力可用；尚未加载时不能只凭配置报告可用。

<a id="case-rc-obs-04"></a>

### RC-OBS-04 健康检查末端的 PG 读取与并发（P0）

- 前置：慢 PG 锁下 health/runtime 与 Recall 并发。
- 步骤：持本例锁 → 发诊断/Recall → 释放并查心跳。
- 预期：健康读取保持正确上下文，失败可定位、无越权；并发 Recall 不错误成功或失心跳；记录实际等待/心跳/终态。

## 真实 Recall 全链与恢复

<a id="case-rc-e2e-01"></a>

### RC-E2E-01 真实依赖下 Recall 完整链与恢复（P0）

- 前置：固定目标源码 SHA/镜像，当前真实四后端与 Temporal；本例 PG 锁/阶段可观察。
- 步骤：通过 Remember 保存并确认 Working Ready → working Recall 沿原 job 观测 → 准备长期 Ready 并独立 long_term Recall → GET 原 result → 重启本例 P3 后重取，并再发新 Recall。
- 预期：新请求收敛并交付完整合格正文；恢复原结果仍复验资格；各阶段/终态证据齐全，无心跳丢失、锁等待失控或 assemble 故障。

## 配置与路径绑定

<a id="case-rc-cfg-01"></a>

### RC-CFG-01 配置未知字段及重排冲突（P1）

- 前置：Recall 配置未知键、不一致 rerank 参数，合法配置对照。
- 步骤：逐字段修改独立配置 → check-config 与 serve。
- 预期：拒绝未知配置字段和不一致的重排参数，不能忽略后导致策略与阶段记录不符。字段名以当前模板为准。

<a id="case-rc-cfg-02"></a>

### RC-CFG-02 Azure 拒第二套 Milvus 连接（P0）

- 前置：Azure 模式 service 有 Milvus binding，Recall 配置又给第二套 Milvus 连接。
- 步骤：独立配置同时指定 → 启动与 binding 诊断。
- 预期：明确拒绝第二套配置，防止 B 写入与 A 搜索连接不同数据库。错误可定位，不能用某个配置静默覆盖另一个。

<a id="case-rc-cfg-03"></a>

### RC-CFG-03 相对路径和容器模型挂载（P1）

- 前置：service 文件相对身份/Recall/Embedding/CA 路径；模型路径独立规则。
- 步骤：同一配置从两个 cwd 启动 → 容器挂载绝对路径对照。
- 预期：service 路径按配置目录解析，模型按后端实际规则；不能因 cwd 不同而误读另一身份或模型；只读模型与可写缓存分开，缺路径明确失败。
