export const canReadTaskDiagnostics = (identity) =>
  identity?.role_codes?.includes('aether_platform_admin') === true

export async function readTaskDiagnostics(getIdentity, getConsole, params) {
  const identity = await getIdentity()
  if (!canReadTaskDiagnostics(identity)) {
    throw Object.assign(new Error('任务与调度仅供平台管理员查看。'), { code: 403 })
  }
  return getConsole('diagnostics', params)
}
