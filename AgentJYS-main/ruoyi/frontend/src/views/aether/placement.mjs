export const tierText = (value) =>
  ({ cold: '冷层', warm: '温层', hot: '热层' })[value] || '层级未记录'
export const triggerText = (value) =>
  ({
    'memory.changed': '记忆新增或状态变更',
    'recall.access': '记忆被成功读取',
    periodic: '到期自动检查'
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
    return `综合访问热度为 ${row.heat.toFixed(6)}，策略判断适合${tierText(row.desired_tier)}；本次只调整相邻一层。`
  }
  return (
    {
      new_memory: '新记忆已持久保存，建立温层副本以便后续读取。',
      successful_read: '检测到成功读取，将温层副本提升到热层以加快访问。',
      no_reads: '当前版本没有成功读取记录，将热层副本降到温层。'
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
