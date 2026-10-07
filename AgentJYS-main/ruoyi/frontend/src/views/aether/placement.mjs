export const tierText = (value) =>
  ({ cold: '冷层（Ceph 原文）', warm: '温层（历史记录）', hot: '热层（Redis 副本）' })[value] || '层级未记录'
export const triggerText = (value) =>
  ({
    'memory.changed': '记忆新增或状态变更',
    'recall.access': '记忆被成功读取',
    periodic: '到期自动检查',
    new_input: '记忆内容或访问次数发生变化',
    threshold_crossing: '访问热度预计降至阈值，到期重新评估',
    upgrade: '调度策略升级后重新评估',
    capability_change: '存储调度能力恢复或发生变化，重新评估',
    pending_completion: '等待上次调度完成后重新检查',
    temporary_failure: '上次执行暂时失败，按退避间隔重试',
    placement_unconfirmed: '上次存储状态尚未确认，重新核验',
    due: '到期自动检查'
  })[value] || '触发来源未记录'

export const resultLabels = {
  succeeded: '成功，已验证可读',
  failed: '执行失败',
  pending: '等待提交',
  running: '执行中',
  unconfirmed: '结果待确认',
  simulated: '模拟完成',
  cancelled: '已取消'
}
export const resultText = (value) => resultLabels[value] || '结果待确认'
export const resultType = (value) =>
  ({ succeeded: 'success', failed: 'danger', unconfirmed: 'warning' })[value] || 'info'
export function decisionText(row) {
  if (row.decision_reason === 'heat_policy' && Number.isFinite(row.heat)) {
    const legacy = row.policy_version === 'continuous_heat_v1'
    const detail = legacy
      ? '这是旧版三层策略的历史动作。'
      : row.desired_tier === 'hot'
        ? '达到升温条件后建立 Redis 副本，Ceph 原文始终保留。'
        : '核验 Ceph 原文可读后移除 Redis 副本。'
    return `综合访问热度为 ${row.heat.toFixed(6)}，策略目标为${tierText(row.desired_tier)}；${detail}`
  }
  return (
    {
      create_hot_replica: '检测到成功读取，保留 Ceph 原文并建立 Redis 热副本。',
      remove_hot_replica: '核验 Ceph 原文可读后移除 Redis 热副本。',
      new_memory: '历史三层策略：新记忆进入温层。',
      successful_read: '历史三层策略：检测到成功读取，将温层副本提升到热层。',
      no_reads: '历史三层策略：无成功读取时从热层降到温层。'
    }[row.decision_reason] || '这条记录未记录可解释的决策依据。'
  )
}
export const durationText = (seconds) =>
  Number.isFinite(seconds) ? `${Number(seconds.toFixed(3))} 秒` : '未取得完成回执'
export const cleanupText = (value) =>
  ({
    not_required: '不需要清理',
    pending: '等待清理',
    completed: '清理完成',
    failed: '清理失败',
    unknown: '清理结果待确认'
  })[value] || '未记录'
