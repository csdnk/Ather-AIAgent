const actionPermissions = {
  memory_create: 'aether:memory:create',
  memory_update: 'aether:memory:update',
  memory_delete: 'aether:memory:delete',
  memory_archive: 'aether:memory:update',
  memory_activate: 'aether:memory:update',
  recall_scheme_create: 'aether:recall:manage',
  recall_scheme_update: 'aether:recall:manage',
  recall_scheme_delete: 'aether:recall:manage',
  recall_execute: 'aether:recall:execute'
}
export function canReadMemory(identity) {
  return ['aether:ops:read', 'aether:content:read'].every((p) => identity?.permissions?.includes(p))
}
export function canManageMemory(identity, action) {
  return (
    canReadMemory(identity) &&
    ['aether:memories:execute', actionPermissions[action]].every(
      (p) => p && identity?.permissions?.includes(p)
    )
  )
}
export function editableBody(detail) {
  if (
    ['forbidden', 'denied', 'unavailable', 'failed'].includes(detail?.status) ||
    detail?.body?.outcome !== 'read'
  )
    return null
  const content = detail.body.content
  for (const value of [content?.text, content?.body, content])
    if (typeof value === 'string') return value
  return null
}
export const commandTerminal = (status) =>
  ['succeeded', 'failed', 'rejected', 'abandoned'].includes(status)
export const pendingReference = (command) => ({
  command_id: command.command_id,
  action: command.action,
  user_id: command.parameters?.user_id || command.user_id
})
export const settleCommand = (response) => ({ ...response, status: response?.status || 'unknown' })
export function recoveredPending(reference, response) {
  if (response?.status === 'abandoned') return null
  return {
    ...pendingReference(reference),
    status: response?.status || 'unknown',
    code: response?.code || response?.error_code,
    cleanup_state: response?.result?.cleanup_state,
    deletion_blocked: response?.result?.blocked === true
  }
}
export function abandonEnvelope(pending) {
  if (!pending?.unaccepted || commandTerminal(pending.status)) return null
  return {
    command_id: pending.command_id,
    resource: 'memories',
    action: 'memory_abandon',
    parameters: { user_id: pending.user_id, original_action: pending.action }
  }
}
export function mutationParameters(detail, parameters = {}) {
  if (detail?.memory?.ref?.version == null || detail?.memory?.object_revision == null)
    throw new Error('请刷新记忆详情后再操作。')
  return { ...parameters, expected_object_revision: detail.memory.object_revision }
}
export function memoryMutation(detail, parameters = {}) {
  const checked = mutationParameters(detail, parameters)
  return { expected_version: detail.memory.ref.version, parameters: checked }
}
export function commandErrorText(error) {
  const code = String(
    error?.memoryCode || error?.code || error?.error_code || error?.response?.status || ''
  )
  if (/CONFLICT|VERSION|409/.test(code)) return '记录已发生变化，请刷新后重新查看并操作。'
  if (/FORBIDDEN|403/.test(code)) return '当前账号无权执行此操作。'
  if (/VALIDATION|INVALID|422/.test(code)) return '提交内容不符合要求，请检查正文、会话和检索范围。'
  if (/NOT_FOUND|404/.test(code)) return '记录已不存在或不在当前授权范围内，请刷新列表。'
  return '操作未完成，请查询原操作结果；如果已失败，可检查服务状态后重新操作。'
}
export const sourceText = (value) =>
  ({ long_term: '长期记忆', working: '工作记忆', all: '全部记忆', both: '全部记忆' })[value] ||
  '范围未返回'
export const actionText = (value) =>
  ({
    memory_create: '新增记忆',
    memory_update: '修改记忆',
    memory_delete: '删除记忆',
    memory_archive: '归档记忆',
    memory_activate: '恢复记忆',
    recall_scheme_create: '新增方案',
    recall_scheme_update: '修改方案',
    recall_scheme_delete: '删除方案',
    recall_execute: '执行召回'
  })[value] || '操作'
