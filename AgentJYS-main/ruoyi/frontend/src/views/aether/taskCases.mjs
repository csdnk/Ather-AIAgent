import { controlAvailability } from './triage.mjs'
export const caseState = (value) =>
  ({
    open: '待认领',
    in_progress: '处理中',
    escalated: '转研发处理',
    awaiting_verification: '待核验',
    verified: '核验通过，待关闭',
    closed: '已关闭'
  })[value] || '未知'
export const caseAction = (value) =>
  ({
    open: '建立处置工单',
    claim: '认领处理',
    note: '记录处理进展',
    escalate: '转研发处理',
    check: '重新检查',
    resolve: '提交处理结果',
    verify: '核验处理结果',
    close: '关闭工单',
    reopen: '重新打开',
    reconcile: '核对原执行',
    cancel: '申请取消任务',
    control: '提交任务控制',
    poll: '核对并继续原操作'
  })[value] || value
export const resolutionName = (value) =>
  ({
    original_success: '原任务已成功',
    replacement: '关联后续成功任务',
    no_longer_needed: '无需继续执行（人工结案）'
  })[value] || '未提交'
export function caseActions(record, identity, task) {
  const permissions = identity?.permissions || []
  const grant = (p) => permissions.includes('*:*:*') || permissions.includes(p)
  if (
    !identity?.role_codes?.includes('aether_platform_admin') ||
    !grant('aether:ops:read') ||
    !grant('aether:support:execute')
  )
    return []
  if (!record) return ['open']
  if (record.state === 'closed') return ['reopen']
  const mine = String(record.owner_id) === String(identity.case_actor_id || identity.user_id)
  const pending =
    record.control && !['complete', 'failed', 'rejected'].includes(record.control.status)
  const result = ['check']
  if (record.control && grant('aether:tasks:execute')) result.push('poll')
  if (pending) return mine ? [...result, 'note'] : result
  if (record.state === 'open' || (record.state === 'escalated' && !mine)) result.push('claim')
  if (mine && ['in_progress', 'escalated'].includes(record.state)) {
    result.push('note', 'escalate', 'resolve')
    if (grant('aether:tasks:execute') && controlAvailability(task).allowed)
      result.push('reconcile', 'cancel')
  }
  if (mine && ['awaiting_verification', 'verified'].includes(record.state)) result.push('resolve')
  if (
    ['awaiting_verification', 'verified'].includes(record.state) &&
    !(mine && record.resolution?.kind === 'no_longer_needed')
  )
    result.push('verify')
  if (
    record.state === 'verified' &&
    String(record.verification?.reviewer_id) === String(identity.case_actor_id || identity.user_id)
  )
    result.push('close')
  return result
}
export function caseError(error) {
  const message = String(error?.caseCode || error?.message || error?.msg || error || '')
  const labels = {
    CASE_PERMISSION_REQUIRED: '当前账号没有任务处置权限。',
    CASE_VERSION_CONFLICT: '记录已被其他人更新，请刷新后重新操作。',
    CASE_OWNER_REQUIRED: '请由当前负责人处理。',
    CASE_ALREADY_CLAIMED: '这条工单已被认领。',
    CASE_NOTE_REQUIRED: '请填写至少 8 个字的具体处理说明。',
    CASE_CONTROL_PENDING: '原操作结果尚未确定，请查询原操作，不要重复提交。',
    CASE_VERIFY_AGAIN: '业务证据已变化或核验已过期，请重新核验。',
    CASE_INDEPENDENT_REVIEW_REQUIRED: '人工结案需要另一名管理员确认复核。',
    CASE_CONTROL_UNAVAILABLE: '当前任务不支持此操作，请重新检查。',
    CASE_TASK_UNAVAILABLE: '暂时无法读取原任务，未执行此次处置。',
    CASE_TASK_MISMATCH: '任务关联信息不一致，已拒绝处置。',
    CASE_STATE_INVALID: '当前处理阶段不支持此操作，请刷新。',
    CASE_REPLACEMENT_REQUIRED: '请选择与原任务不同的后续任务。',
    CASE_RESOLUTION_REQUIRED: '请选择处理结果。',
    CASE_ALREADY_CLOSED: '工单已关闭，需要先重新打开。'
  }
  return (
    Object.entries(labels).find(([code]) => message.includes(code))?.[1] ||
    '操作结果尚未确认，请查询原操作后再继续。'
  )
}
