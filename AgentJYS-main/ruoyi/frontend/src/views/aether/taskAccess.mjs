export const canReadOperations = (identity) =>
  identity?.role_codes?.some((role) =>
    ['aether_platform_admin', 'aether_tenant_admin'].includes(role)
  ) === true && identity?.permissions?.includes('aether:ops:read') === true

export const canReadTaskDiagnostics = (identity) =>
  canReadOperations(identity) && identity.role_codes.includes('aether_platform_admin')

export async function readTaskDiagnostics(getIdentity, getConsole, params) {
  const identity = await getIdentity()
  if (!canReadTaskDiagnostics(identity)) {
    throw Object.assign(new Error('任务与调度仅供平台管理员查看。'), { code: 403 })
  }
  return getConsole('diagnostics', params)
}
