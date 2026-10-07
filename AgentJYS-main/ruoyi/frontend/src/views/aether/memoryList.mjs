export function memorySummary(row) {
  if (typeof row.summary === 'string') return row.summary
  if (typeof row.summary?.text === 'string') return row.summary.text
  if (typeof row.summary?.body === 'string') return row.summary.body
  if (row.status === 'deleted') return '已删除，正文不可读取'
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
  return reasons[row.summary_status] || '摘要尚不可用，可打开详情查看原因'
}
