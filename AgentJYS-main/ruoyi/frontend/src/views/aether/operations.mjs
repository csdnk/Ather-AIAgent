export const displayValue = (value) =>
  value == null
    ? '未知'
    : typeof value === 'boolean'
      ? value
        ? '是'
        : '否'
      : typeof value === 'object'
        ? JSON.stringify(value)
        : String(value)
export const normalizeRows = (data) =>
  Array.isArray(data?.items)
    ? data.items.map(({ payload, ...row }) => ({ ...(payload || {}), ...row }))
    : []
export const maySubmit = (pending) =>
  !pending || ['complete', 'succeeded', 'failed', 'rejected'].includes(pending.state)
export const createCommand = (resource, action, target_id, expected_version, parameters) => ({
  command_id: crypto.randomUUID(),
  resource,
  action,
  target_id,
  expected_version,
  parameters
})

export const commandLookupParams = (command_id) => ({ command_id })
