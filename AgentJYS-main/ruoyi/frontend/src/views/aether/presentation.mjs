// Presentation only: raw identifiers and provider receipts remain available in technical details.
const states = {
  building: '正在建立检索索引',
  waiting: '等待执行',
  committed: '已提交处理产物',
  read: '正文读取成功',
  empty: '本次结果为空',
  active: '有效',
  deleted: '已删除',
  superseded: '已被新版本替代',
  archived: '已归档',
  excluded: '当前不可读取',
  not_collected: '尚未采集',
  not_bound: '尚未绑定工作流',
  no_records: '尚无维护记录',
  not_available: '结果尚不可读取',
  invalidated: '结果已失效',
  terminated: '已终止',
  timed_out: '已超时',
  not_requested: '本次未请求',
  save_failed: '记忆保存失败',
  save_queued: '已排队等待保存',
  not_connected: '尚无保存回执',
  attention_required: '已中断，待排查',
  retry_wait: '等待自动重试',
  recovery_wait: '等待恢复检查',
  not_ready: '暂不能接收业务请求',
  stopped: '已停止',
  maintenance: '维护中',
  in_progress: '处理中',
  ok: '数据读取正常',
  complete: '已完成',
  completed: '已完成',
  succeeded: '执行成功',
  failed: '处理失败',
  rejected: '操作被拒绝',
  pending: '等待处理',
  running: '处理中',
  accepted: '已受理，等待执行结果',
  submitted: '已提交，等待执行结果',
  submitting: '正在提交',
  unknown: '结果尚未确认',
  unavailable: '暂时无法读取',
  degraded: '部分服务状态待检查',
  available: '可用',
  ready: '可接收业务请求',
  alive: '服务进程在线',
  disabled: '未启用',
  not_configured: '尚未配置',
  configured: '已配置',
  open: '待处理',
  resolved: '已恢复',
  acknowledged: '已认领',
  silenced: '已暂时停止提醒',
  delivered: '已送达',
  saved: '已保存',
  saving: '正在保存',
  not_started: '尚未开始',
  no_effect: '未产生业务变更',
  confirmed: '业务变更已确认',
  cancelled: '已取消',
  canceled: '已取消',
  restored: '数据库恢复完成，业务仍需验收',
  finished: '处理结束',
  partial: '仅有部分调用的计量数据',
  no_provider_usage_yet: '尚未收到模型用量数据',
  retrying: '正在重试',
  queued: '排队等待',
  expired: '已过期',
  timeout: '处理超时',
  stale: '数据已过期，需重新检查',
  skipped: '本次未执行',
  healthy: '运行正常'
}
const terms = {
  conversations: '会话内容',
  users: '用户目录',
  recalls: '记忆召回',
  content_access: '内容访问',
  hot: '热存储',
  warm: '温存储',
  cold: '冷存储',
  validate: '校验请求',
  select: '确定检索范围',
  embed: '生成检索向量',
  discover: '查找候选记忆',
  load: '读取候选内容',
  rank: '候选排序',
  assemble: '组装上下文',
  finalize: '确认最终结果',
  'recall.retrieve': '检索记忆',
  'recall.working': '检索近期记忆',
  'recall.long_term': '检索长期记忆',
  'remember.reflect': '反思与提炼',
  'operate.execute': '执行存储维护',
  'operate.reconcile': '核对存储维护结果',
  workflow: '工作流队列',
  activity: '业务步骤队列',
  requests: '业务请求',
  tasks: '后台任务',
  memories: '记忆保存',
  incidents: '异常告警',
  resources: '服务台账',
  support: '支持工单',
  configuration: '配置记录',
  rules: '告警规则',
  quotas: '租户用量上限',
  backups: '备份与恢复',
  audit: '操作审计',
  commands: '操作记录',
  save: '保存记录',
  cancel: '取消任务',
  reconcile: '核对任务状态',
  acknowledge: '认领告警',
  silence: '暂时停止提醒',
  create: '创建备份',
  restore_drill: '验证备份可恢复性',
  activate: '激活配置快照',
  request_failures_15m: '最近 15 分钟失败请求数',
  oldest_pending_seconds: '最久等待时长（秒）',
  chat_turns_15m: '最近 15 分钟创建的请求数',
  memory_save_failures_15m: '最近 15 分钟创建请求的记忆保存失败数',
  memory_save_pending: '当前等待记忆保存的请求数',
  stale_pending_requests: '当前长时间未结束的请求数',
  dependency_p3: '记忆与任务服务连接',
  dependency_platform: '业务服务连接',
  database: '业务数据库',
  logs: '运行日志',
  executor: '任务执行器',
  extraction: '记忆内容提取',
  embedding: '文本向量生成',
  vectors: '向量检索',
  reranker: '检索结果重排',
  rerank: '检索重排能力',
  USD: '美元',
  CNY: '人民币',
  in_progress: '处理中',
  stopped: '已停止',
  maintenance: '维护中',
  tokenizer: '文本用量计算',
  recall_generation: '记忆召回生成',
  bodies: '原文存储',
  workers: '后台工作进程',
  working_read: '近期记忆读取',
  long_term: '长期记忆',
  scheduling: '任务调度',
  context_budget: '上下文用量控制',
  deployment_dependencies: '部署依赖',
  logging: '日志记录',
  'operate.evaluate': '记忆维护评估',
  'save.extract': '提取待保存的记忆',
  'save.commit': '提交记忆保存',
  'remember.extract': '提取长期记忆',
  'remember.persist': '保存长期记忆',
  'Kubernetes Deployment': '云端应用服务',
  platform_postgresql_database: '业务平台数据库',
  management_record: '运维登记记录',
  checksum_and_database_restore: '文件校验及数据库恢复已验证',
  'platform operations': '平台运维组',
  high: '高',
  medium: '中',
  low: '低',
  critical: '紧急',
  production: '生产环境',
  staging: '预发布环境',
  development: '开发环境',
  'aether-ruoyi-backend': '账号与后台管理服务',
  'aether-ruoyi-frontend': '运维管理网页',
  'aether-ruoyi-platform': '对话业务服务',
  'aether-ruoyi-p3': '记忆与任务服务',
  'aether-ruoyi-mysql': '账号权限数据库',
  'aether-ruoyi-redis': '登录会话缓存',
  'aether-ruoyi-platform-db': '对话业务数据库'
}
const reasons = {
  TEMPORAL_NOT_CONNECTED: '调度服务尚未连接，工作流执行情况暂时未知。',
  TEMPORAL_QUERY_FAILED: '工作流状态读取失败，请检查调度服务连接后刷新。',
  MEMORY_GONE: '记忆已删除或不可读取。',
  RESULT_INVALIDATED: '关联记忆已变化，历史结果失效，不能继续读取旧内容。',
  BUDGET_EXHAUSTED: '本次执行预算已用尽；请检查预算配置和任务尝试记录。',
  TRANSPORT_ERROR: '通知服务连接失败；请检查通知服务和网络连接。',
  DESTINATION_REJECTED: '通知服务拒绝了投递；请核对接收端配置。',
  P3_NOT_CONFIGURED: '尚未配置记忆与任务服务地址，请联系平台管理员。',
  P3_UNAVAILABLE: '暂时无法连接记忆与任务服务，请检查服务状态。',
  VERSION_CONFLICT: '记录已被其他人修改，请刷新后核对最新内容。',
  OK: '处理正常'
}
export const statusText = (value) => (value == null ? '尚无数据' : states[value] || '状态待核实')
export const readable = (value, fallback = '此项说明待补充') => {
  if (value == null || value === '') return '尚无数据'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number')
    return Number.isFinite(value)
      ? value.toLocaleString('zh-CN', { maximumFractionDigits: 2 })
      : '尚无数据'
  if (typeof value === 'object') return '此项暂无可读摘要'
  return (
    terms[value] ||
    states[value] ||
    reasons[value] ||
    (/[\u3400-\u9fff]/.test(value) ? value : fallback)
  )
}
export const formatTime = (value) => {
  if (value == null || value === '') return '尚无记录'
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? '时间待核实'
    : date.toLocaleString('zh-CN', { hour12: false })
}
const definitions = {
  requests: [
    ['tenant_name', '所属租户'],
    ['user_name', '发起人'],
    ['status', '处理进度'],
    ['memory_status', '记忆保存'],
    ['first_token_ms', '开始回复用时'],
    ['explanation', '处理说明'],
    ['created_at', '发起时间']
  ],
  memories: [
    ['tenant_name', '所属租户'],
    ['user_name', '所属用户'],
    ['memory_status', '保存进度'],
    ['explanation', '保存说明'],
    ['created_at', '请求时间']
  ],
  tasks: [
    ['kind', '任务内容'],
    ['state', '处理进度'],
    ['attempt', '已尝试次数'],
    ['effect_status', '业务变更'],
    ['explanation', '处置建议'],
    ['created_at', '创建时间']
  ],
  incidents: [
    ['metric', '异常项目'],
    ['tenant_name', '影响范围'],
    ['state', '处理状态'],
    ['explanation', '情况说明'],
    ['occurrences', '累计触发次数'],
    ['last_seen', '最近触发']
  ],
  usage: [
    ['tenant_name', '所属租户'],
    ['requests', '请求总数'],
    ['complete', '已完成'],
    ['failed', '失败'],
    ['pending', '等待处理'],
    ['saved', '记忆已保存'],
    ['first_token_p95_ms', '95% 请求开始回复用时']
  ],
  resources: [
    ['name', '服务名称'],
    ['purpose', '业务用途'],
    ['status', '登记状态'],
    ['owner', '负责团队'],
    ['environment', '部署环境'],
    ['updated_at', '登记更新时间']
  ],
  support: [
    ['summary', '问题描述'],
    ['severity', '紧急程度'],
    ['state', '处理状态'],
    ['owner', '处理人'],
    ['resolution', '处理结果'],
    ['updated_at', '更新时间']
  ],
  configuration: [
    ['name', '配置名称'],
    ['description', '用途说明'],
    ['value', '当前设置'],
    ['version', '记录版本'],
    ['updated_at', '更新时间']
  ],
  rules: [
    ['metric', '监测项目'],
    ['threshold', '触发上限'],
    ['enabled', '是否启用'],
    ['description', '规则说明'],
    ['updated_at', '更新时间']
  ],
  quotas: [
    ['tenant_name', '所属租户'],
    ['daily_requests', '24 小时请求上限'],
    ['concurrent_turns', '同时处理请求上限'],
    ['observed_token_budget', '已计量文本用量上限'],
    ['updated_at', '更新时间']
  ],
  backups: [
    ['scope', '备份范围'],
    ['status', '当前结果'],
    ['bytes', '备份大小'],
    ['explanation', '恢复与验收说明'],
    ['updated_at', '完成时间']
  ],
  commands: [
    ['actor_name', '操作人'],
    ['resource', '操作对象'],
    ['action', '执行操作'],
    ['status', '处理结果'],
    ['explanation', '结果说明'],
    ['created_at', '提交时间']
  ],
  audit: [
    ['actor_name', '操作人'],
    ['resource', '操作对象'],
    ['action', '执行操作'],
    ['status', '处理结果'],
    ['explanation', '结果说明'],
    ['created_at', '操作时间']
  ]
}
export const columnsFor = (resource) =>
  (definitions[resource] || [])
    .filter(
      ([key]) => !(key === 'created_at' && ['requests', 'tasks', 'memories'].includes(resource))
    )
    .map(([key, label]) => ({
      key,
      label,
      width:
        key === 'explanation'
          ? 270
          : ['tenant_name', 'kind', 'name'].includes(key)
            ? 140
            : key.endsWith('_at') || key === 'last_seen'
              ? 165
              : 110
    }))
export function explainRow(resource, row) {
  const state = row.status || row.state
  const code = row.error_code || row.code || row.result?.code
  if (code && code !== 'OK')
    return reasons[code] || '系统报告异常，具体原因待核实，请联系平台管理员排查。'
  if (resource === 'memories') {
    const memory = row.memory_status || row.memory_evidence?.status
    if (memory === 'saved') return '本次请求的记忆已保存；检索是否可用需另行验证。'
    if (memory === 'saving') return '回复已记录，记忆仍在后台保存，请稍后刷新查看。'
    if (['failed', 'save_failed'].includes(memory))
      return '记忆保存失败，请检查关联任务的执行结果。'
    if (memory === 'save_queued') return '记忆已加入保存队列，请稍后查看后台任务的处理进度。'
    return '尚未取得保存完成的回执，请核对关联任务。'
  }
  if (resource === 'incidents')
    return `${readable(row.metric, '监测项目')}：${statusText(state)}。${state === 'resolved' ? '保留记录供追溯。' : '请检查受影响服务并记录处理情况。'}`
  if (resource === 'backups')
    return row.status === 'restored'
      ? '已完成文件校验及数据库恢复；尚未完成恢复后的业务验收。'
      : '仅覆盖业务平台数据库；账号库、缓存、向量及对象存储需要单独备份。'
  if (['accepted', 'submitted', 'submitting', 'unknown'].includes(state))
    return '执行结果尚未确认，请查询原操作记录，避免重复提交。'
  if (['failed', 'rejected'].includes(state) || row.has_error)
    return '本次处理未成功，请查看异常详情和关联任务后再决定如何处理。'
  if (resource === 'tasks' && state === 'pending')
    return '等待后台工作进程处理；若长时间不变化，请核对任务状态。'
  if (resource === 'tasks' && state === 'attention_required')
    return '此任务需要人工核对业务结果，请查看执行详情后决定如何处理。'
  if (resource === 'tasks' && state === 'retry_wait')
    return '任务正在等待自动重试，请关注下一次执行时间和尝试次数。'
  if (resource === 'tasks' && state === 'recovery_wait')
    return '任务正在等待恢复检查，请结合上次执行结果核对业务状态。'
  if (row.effect_status === 'no_effect')
    return '本次执行未产生业务变更，请结合任务结果判断是否需要处理。'
  if (['complete', 'succeeded', 'finished'].includes(state))
    return resource === 'requests'
      ? '本次回复已完成；记忆保存进度单独显示。'
      : '此项操作已完成，可在详情中查看执行记录。'
  return '请结合当前进度和更新时间判断；完整执行信息可展开查看。'
}
export function cellText(resource, key, row) {
  const value = row[key]
  if (resource === 'resources' && key === 'status')
    return value === 'running' ? '登记为运行中' : statusText(value)
  if (
    [
      'name',
      'owner',
      'purpose',
      'description',
      'summary',
      'resolution',
      'environment',
      'value'
    ].includes(key) &&
    typeof value === 'string'
  )
    return terms[value] || value || '尚未填写'
  if (key === 'explanation') return explainRow(resource, row)
  if (key.endsWith('_name') && ['tenant_name', 'user_name', 'actor_name'].includes(key))
    return (
      value ||
      (key === 'tenant_name' && row.tenant_id == null && resource !== 'quotas'
        ? '平台范围'
        : '名称尚未同步')
    )
  if (key.endsWith('_at') || key === 'last_seen') return formatTime(value)
  if (['status', 'state', 'phase', 'effect_status', 'memory_status'].includes(key))
    return statusText(value)
  if (key === 'memory_evidence') return statusText(value?.status)
  if (key.endsWith('_ms'))
    return value == null
      ? '尚无数据'
      : Number(value) >= 1000
        ? `${readable(Number(value) / 1000)} 秒`
        : `${readable(value)} 毫秒`
  if (key === 'bytes') return value == null ? '不适用' : `${readable(value / 1024)} KB`
  if (key === 'scope' && row.status === 'restored') return '业务平台数据库恢复演练'
  if (key === 'estimated_cost' && value == null) return '尚未配置单价'
  if (key === 'currency' && value == null) return '尚未配置币种'
  if (key === 'model_name') return value || '模型未标明'
  return readable(value)
}
export function healthItems(p3 = {}) {
  if (!Array.isArray(p3.observations))
    return [
      {
        label: '基础服务检查',
        text: statusText(p3.status),
        note: '尚未取得完整检查结果，请刷新后重试。'
      }
    ]
  return p3.observations.map((row) => ({
    label: row.name === 'save' ? '记忆保存' : readable(row.name, '其他服务能力'),
    text:
      row.fresh_until && Date.parse(row.fresh_until) < Date.now()
        ? '检查结果已过期'
        : statusText(row.state),
    note: `检查时间：${formatTime(row.checked_at)}`
  }))
}
export function metricTotal(data, key) {
  const usage = data?.usage
  if (!Array.isArray(usage?.items) || (usage.status && usage.status !== 'ok')) return '尚无数据'
  if (usage.items.some((row) => row[key] == null || !Number.isFinite(Number(row[key]))))
    return '尚无数据'
  return usage.items.reduce((sum, row) => sum + Number(row[key]), 0)
}
export const dataNotice = (data) =>
  data.status && !['ok', 'available'].includes(data.status)
    ? `${statusText(data.status)}。请刷新重试；此时空列表不代表没有业务记录。`
    : ''
