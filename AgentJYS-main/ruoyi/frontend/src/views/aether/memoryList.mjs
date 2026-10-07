export function memorySummary(row) {
  if (typeof row.summary === 'string') return row.summary
  if (typeof row.summary?.text === 'string') return row.summary.text
  if (typeof row.summary?.body === 'string') return row.summary.body
  if (row.status === 'expired') return '已过期，正文不可读取'
  if (row.status === 'superseded') return '已被新版本替代'
  const reasons = {
    pending: '摘要加载中…',
    not_checked_on_list: '摘要尚未加载，请刷新重试',
    unavailable:
      row.summary_error_code === 'DEADLINE_EXCEEDED'
        ? '摘要读取超时，可重试或打开详情'
        : '摘要暂时无法读取，可重试或打开详情',
    source_deleted: '来源已失效，正文不可读取',
    evidence_unsupported: '来源证据不再支持此记忆，正文暂不可读取',
    evidence_needs_revalidation: '来源证据待重新核验，正文暂不可读取',
    derived_evidence_changed: '关联来源已变化，正文暂不可读取',
    changed_during_read: '记忆读取期间发生变化，请刷新',
    old_version_or_inactive: '当前记忆未启用，正文不可读取',
    expired: '已过期，正文不可读取',
    deleted: '已删除，正文不可读取'
  }
  const retained = {
    retained_body_missing: '保留正文已不存在，仅有管理记录',
    retained_body_unavailable: '正文存储暂时无法访问，请稍后重试',
    retained_body_invalid: '保留正文校验失败，暂不能展示'
  }
  return (
    retained[row.summary_status] ||
    reasons[row.summary_status] ||
    (row.status === 'deleted' ? '已逻辑删除，保留正文待读取' : '摘要尚不可用，可打开详情查看原因')
  )
}

export function memoryLifecycleNotice(status) {
  if (status === 'archived') return '已归档：暂停召回使用，正文仍保留，可恢复使用。'
  if (status === 'deleted')
    return '已逻辑删除：保留正文仅供后台只读查看，不再参与召回，不能在此恢复使用。'
  return ''
}

export function memoryLifecyclePrompt(action) {
  if (action === 'memory_delete')
    return '确定逻辑删除这条记忆？删除后停止召回使用，后台仍可只读查看保留正文，但不能在此恢复使用。'
  if (action === 'memory_archive')
    return '确定归档这条记忆？归档会暂停召回使用，保留正文，之后可以恢复使用。'
  return '确定恢复使用这条记忆？恢复后将重新准备检索索引。'
}
