import { queueBusiness } from './console.mjs'

const terminal = new Set(['failed', 'attention_required', 'succeeded', 'cancelled'])
const active = new Set(['pending', 'queued', 'running', 'retry_wait', 'recovery_wait'])
export const taskStateFilter = (value) =>
  typeof value === 'string' && (terminal.has(value) || active.has(value)) ? value : ''
const taskRecord = (data) => ({
  ...(data?.task || data),
  workflow: data?.workflow || data?.task?.workflow
})

export function controlAvailability(data) {
  const task = taskRecord(data)
  if (terminal.has(task.state))
    return { allowed: false, reason: '任务已结束，不能再取消或唤醒原执行。' }
  if (!active.has(task.state) || !Number.isInteger(task.revision) || task.revision < 1)
    return { allowed: false, reason: '尚未取得有效任务状态，请刷新后再判断。' }
  if (task.workflow?.status !== 'available' || task.workflow?.state !== 'running')
    return { allowed: false, reason: '尚未确认原工作流仍在运行，请刷新执行详情。' }
  return {
    allowed: true,
    reason: '可申请取消或核对原执行；核对不会创建新任务，最终是否执行由后端再次校验。'
  }
}

export function taskTriage(data) {
  const task = taskRecord(data)
  const diagnoses = {
    DEPENDENCY_UNAVAILABLE: [
      '依赖服务不可用',
      '运维工程师',
      '检查该步骤依赖的模型、索引或存储服务及连接，确认恢复后再处理原业务。'
    ],
    FORBIDDEN: [
      '执行授权被拒绝',
      '身份与权限管理员',
      '核对所属企业、用户是否启用及原任务的授权范围；只恢复应有权限，不跨租户改归属。'
    ],
    DEADLINE_EXCEEDED: [
      '超过本次处理时限',
      '运维工程师',
      '检查超时前完成了哪些步骤，以及是否有排队、模型或存储响应过慢。'
    ],
    CONTRACT_VIOLATION: [
      '执行结果未通过一致性校验',
      '研发工程师',
      '保留原任务及结果，请研发检查输入、版本和执行回执的一致性。不要强行标记成功。'
    ]
  }
  const known = diagnoses[task.error_code]
  const ended = terminal.has(task.state)
  const cause =
    known?.[0] || (task.error_code ? '原因尚未归类，需核查执行记录' : '任务未记录具体原因')
  const effect =
    task.effect_status === 'confirmed'
      ? '原任务已确认产生业务变更'
      : task.effect_status === 'no_effect'
        ? '原任务回执报告未产生业务变更'
        : task.effect_status === 'not_started'
          ? '原任务回执报告尚未开始业务变更'
          : '原任务业务结果尚未确认'
  const retry = ended
    ? '任务已结束，不会自动重试'
    : task.next_run_at
      ? '系统已安排下一次执行检查'
      : '尚无明确的下一次重试时间'
  const steps = [
    '先核对原会话、记忆与任务结果，确认已完成哪些内容，避免重复写入或重复操作。',
    known?.[2] || '查看执行步骤和关联记录；如仍无明确原因，复制排查信息并转交研发定位。',
    ended
      ? '修复原因后，由业务负责人核对是否仍需办理原请求；已结束任务目前不支持后台原地重启。'
      : '原工作流仍在运行时，可在任务列表申请核对原执行；不再需要执行时再申请取消。'
  ]
  return {
    cause,
    owner: known?.[1] || '运维工程师初查，必要时转研发',
    effect,
    retry,
    steps,
    controls: controlAvailability(data)
  }
}

export function businessQueues(section = {}) {
  const groups = new Map()
  const sampled = Date.parse(section.observed_at)
  function observation(row) {
    if (!row || row.status !== 'available')
      return { label: '未取得数据', backlog: null, recent: null, needsCheck: true }
    const times = (row.pollers || [])
      .map((p) => p.last_access_time)
      .filter((t) => Number.isFinite(Date.parse(t)))
      .sort((a, b) => Date.parse(b) - Date.parse(a))
    const recent = times[0] || null
    const reference = Number.isFinite(sampled) ? sampled : Date.parse(row.observed_at)
    const fresh =
      recent &&
      Number.isFinite(reference) &&
      reference - Date.parse(recent) <= 300000 &&
      reference >= Date.parse(recent)
    return {
      label: fresh ? '5 分钟内有联系' : '未见近期联系',
      recent,
      backlog: Number.isFinite(row.backlog_count_hint) ? row.backlog_count_hint : null,
      needsCheck: !fresh
    }
  }
  for (const row of section.items || []) {
    if (!['workflow', 'activity'].includes(row.task_type)) continue
    if (!groups.has(row.task_queue))
      groups.set(row.task_queue, { key: row.task_queue, label: queueBusiness(row.task_queue) })
    groups.get(row.task_queue)[row.task_type] = observation(row)
  }
  return [...groups.values()].map((row) => {
    const workflow = row.workflow || observation(),
      activity = row.activity || observation()
    const recent = [workflow.recent, activity.recent]
      .filter(Boolean)
      .sort((a, b) => Date.parse(b) - Date.parse(a))[0]
    return {
      ...row,
      workflow,
      activity,
      recent,
      needsCheck:
        workflow.needsCheck ||
        activity.needsCheck ||
        workflow.backlog === null ||
        activity.backlog === null ||
        workflow.backlog > 0 ||
        activity.backlog > 0
    }
  })
}
