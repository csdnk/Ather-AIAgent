export const sectionItems = (section) =>
  Array.isArray(section) ? section : Array.isArray(section?.items) ? section.items : []
export function tableEmptyText(data, loading) {
  if (loading) return '数据读取中，请稍候'
  if (['forbidden', 'denied'].includes(data?.status)) return '当前账号无权读取此项数据'
  if (['unavailable', 'failed', 'error', 'timeout'].includes(data?.status))
    return '数据加载失败，当前无法确认是否有记录'
  if (!data?.status) return '尚未取得查询结果'
  return '当前查询没有记录'
}
export const taskAudience = (task) =>
  [task?.tenant_name, task?.user_name]
    .filter((name) => typeof name === 'string' && name.trim())
    .join(' · ')
export function observation(data) {
  const status = data?.status || data?.state
  if (['forbidden', 'denied'].includes(status)) return '当前账号无权读取此项数据'
  if (['unavailable', 'failed', 'error', 'timeout'].includes(status))
    return '采集失败，暂时无法读取；不能据此判断没有异常'
  if (['stale', 'expired'].includes(status)) return '采样已过期，请刷新后核实'
  if (['disabled', 'not_enabled'].includes(status)) return '未启用此项能力'
  if (status === 'not_bound') return '当前部署尚未绑定此项工作流'
  if (status === 'no_records') return '尚无实际维护记录'
  if (!status && data?.observed_at && data?.worker && data?.temporal)
    return '已读取执行器与调度观测；各项状态见下方'
  if (!status || ['unknown', 'not_collected', 'not_configured'].includes(status))
    return '尚未采集到可靠数据'
  if (status === 'partial') return '只取得部分数据，请检查详情中的缺失项'
  if (Array.isArray(data?.items) && !data.items.length) return '当前授权范围与查询条件下没有记录'
  return '已读取本次观测；业务效果请查看各项回执'
}
export function memoryText(data) {
  if (['forbidden', 'denied'].includes(data?.status)) return '当前账号无权读取正文'
  const retainedReason = data?.body?.reason_code || data?.reason_code
  if (retainedReason === 'retained_body_missing') return '保留正文已不存在，当前仅保留操作记录。'
  if (retainedReason === 'retained_body_unavailable') return '正文存储暂时无法访问，请稍后重试。'
  if (retainedReason === 'retained_body_invalid') return '保留正文校验失败，暂不能展示。'
  if (['deleted', 'expired', 'superseded'].includes(data?.status))
    return '内容已删除、过期或被替代，不能从历史记录恢复'
  if (['failed', 'unavailable', 'error'].includes(data?.status)) return '正文读取失败，请刷新后核实'
  if (data?.body && typeof data.body === 'object') return memoryText(data.body)
  if (data?.outcome === 'excluded') return '当前生命周期或权限下正文不可读取；不从历史缓存恢复'
  for (const candidate of [
    data?.body,
    data?.text,
    data?.content?.body,
    data?.content?.text,
    data?.content
  ]) {
    if (typeof candidate === 'string') return candidate || '正文为空'
  }
  return '服务未返回正文；不代表没有记忆内容'
}
export function recallEvidence(data) {
  if (data?.record?.state === 'failed') return '本次召回执行失败，请查看失败原因；不能当作空结果'
  if (['unavailable', 'failed', 'excluded', 'invalidated'].includes(data?.result_status))
    return '召回结果当前无法读取，不能当作空结果'
  if (['failed', 'unavailable', 'error'].includes(data?.status))
    return '召回读取失败，无法判断是否检索到记忆'
  const rows = recallItems(data)
  if (data?.result_status === 'available' && data?.result?.outcome === 'empty')
    return '本次检索未返回记忆'
  if (data?.result?.outcome === 'degraded')
    return `只获得部分召回结果（${rows.length} 条），覆盖不完整；请核对降级原因。`
  if (!rows.length)
    return ['ok', 'complete', 'completed'].includes(data?.status)
      ? '本次检索未返回记忆'
      : '尚未获得完整召回结果'
  return `检索返回 ${rows.length} 条记忆。上下文组装与发送给模型需要单独证据，不能据此判断模型采用了这些内容。`
}
export function recallItems(data) {
  const result = data?.result || data
  return Array.isArray(result?.groups)
    ? result.groups.flatMap((group) => sectionItems(group))
    : sectionItems(result)
}
export const memoryId = (item) =>
  item?.ref?.memory_id || item?.memory?.memory_id || item?.memory_id || item?.id
export const projectionText = (row) =>
  ({
    building: '正在建立检索索引',
    excluded: '当前不可检索',
    ready: '检索索引已就绪',
    available: '检索索引可用',
    pending: '等待建立检索索引',
    failed: '建立检索索引失败',
    stale: '检索索引已过期'
  })[row?.projection_state] || '检索就绪状态未确认'
export function taskOutcome(data) {
  const effect =
    data?.effect_status ??
    data?.effect_state ??
    data?.effect?.state ??
    data?.task?.effect_status ??
    data?.task?.effect_state
  if (effect === 'confirmed') return '业务效果已确认'
  if (effect === 'no_effect') return '未产生业务变更'
  return '业务效果尚未确认；工作流结束不能替代业务回执'
}
export function pipelineRows(data) {
  if (data?.task_summary) {
    return [
      { key: 'remember', label: '记忆写入与长期整理' },
      { key: 'recall', label: '后台召回任务' },
      { key: 'operate', label: '存储维护与生命周期' },
      { key: 'runtime', label: '运行维护任务' }
    ].map((item) => ({
      ...item,
      summary:
        data.task_summary.by_flow?.[item.key] == null
          ? '该业务任务数未返回'
          : `本部署累计 ${data.task_summary.by_flow[item.key]} 条任务`
    }))
  }
  const tasks = sectionItems(data?.tasks)
  return [
    { key: 'save', label: '记忆写入', prefixes: ['save.'] },
    { key: 'recall', label: '记忆召回', prefixes: ['recall.'] },
    { key: 'remember', label: '长期整理与提炼', prefixes: ['remember.', 'reflect.'] },
    { key: 'operate', label: '存储维护与生命周期', prefixes: ['operate.'] }
  ].map((business) => {
    const matched = tasks.filter((task) =>
      business.prefixes.some((prefix) => String(task.kind || '').startsWith(prefix))
    )
    const failed = matched.filter((task) =>
      ['failed', 'attention_required'].includes(task.state)
    ).length
    const pending = matched.filter((task) =>
      ['accepted', 'running', 'queued', 'retry_wait', 'recovery_wait'].includes(task.state)
    ).length
    return {
      ...business,
      summary: !data?.tasks
        ? '尚未取得任务观测'
        : !matched.length
          ? '本页未见此类任务；不代表未启用或没有故障'
          : `本页 ${matched.length} 条 · ${failed} 条失败或需人工处理 · ${pending} 条处理中或等待`
    }
  })
}
export function targetQuery(userId, paging = {}) {
  if (!userId) throw new Error('请先选择用户')
  return { ...paging, user_id: userId }
}
export const memoryKind = (value) =>
  ({
    working: '工作记忆 · 近期上下文',
    episodic: '情景记忆 · 具体经历',
    semantic: '语义记忆 · 稳定知识',
    procedural: '过程记忆 · 操作经验'
  })[value] || '类型尚未标明'
export const queueBusiness = (queue) =>
  ({
    engineering: '运行协调',
    maintenance: '周期维护',
    periodic: '周期任务调度',
    io: '存储读写',
    model: '模型调用',
    remember: '记忆写入与整理',
    recall: '记忆召回',
    operate: '存储维护与生命周期',
    remember_index: '记忆检索索引',
    remember_ingress: '记忆接收'
  })[
    String(queue || '')
      .split('.')
      .pop()
  ] || '所属业务待核实'
export const errorMessage = (error) =>
  [401, 403].includes(Number(error?.code ?? error?.response?.status))
    ? '当前登录身份无权读取，请联系管理员核对内容查看权限。'
    : '暂时无法读取，请检查服务连接后刷新；当前结果不能视为没有记录。'
export const scopeText = (value) => {
  if (!value) return '尚未返回覆盖范围'
  if (/[\u3400-\u9fff]/.test(value)) return value
  if (/deployment.*task|bound tasks/.test(value)) return '本页为当前部署绑定任务，不能视为全部统计'
  if (/local.*process|remote pollers/.test(value))
    return '仅反映当前 P3 进程；远端执行器以实际轮询者观测为准'
  if (/deployment queues|backlog/.test(value))
    return '当前部署的队列；积压为服务端估计值，缺少采样时保持未知'
  if (/chat|receipt/.test(value)) return '平台对话回执的持续采样，不覆盖全部 P3 业务'
  return '本次观测仅覆盖当前授权范围，具体覆盖项尚未说明'
}
