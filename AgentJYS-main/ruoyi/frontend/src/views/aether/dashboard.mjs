import { readable, statusText } from './presentation.mjs'

export function contractCards(metrics = {}) {
  const ratio = metrics.compression?.ratio
  return [
    {
      label: '向量化处理速度',
      value: '待压测',
      target: '合同目标 ≥ 2,000 次/秒',
      note: '旁路拦截与向量化的稳定吞吐能力，需要带负载和环境信息的压测结果。'
    },
    {
      label: '短期记忆响应时间',
      value: '尚未采集',
      target: '合同目标 P99 < 10 毫秒',
      note: '99% 的短期记忆接口请求应在 10 毫秒内完成；需专门采集接口耗时。'
    },
    {
      label: '长期记忆压缩效果',
      value: Number.isFinite(ratio) ? `${ratio.toFixed(2)} 倍` : '暂无有效样本',
      target: '合同目标 物理压缩 ≥ 5 倍',
      note: `已发布正文样本 ${metrics.compression?.samples ?? '未知'} 条；按总原始字节 ÷ 总压缩字节统计，含历史版本，不等同整体存储节省。`
    },
    {
      label: '缓存命中改善',
      value: '待基线验证',
      target: '合同目标 比 MVP 基线提升 10%+',
      note: '需要同一负载下的基线命中率和当前命中率；比较口径尚待验收确认。'
    },
    {
      label: '连续稳定运行',
      value: '待专项验证',
      target: '合同目标 连续无故障 72 小时',
      note: '需要生产级环境连续观测及故障判据；容器启动时长不能作为验收依据。'
    }
  ].map((card) => ({ ...card, state: 'info' }))
}

export function schedulingCards(data = {}) {
  const states = data.task_summary?.by_state
  const count = (...keys) =>
    states ? String(keys.reduce((sum, key) => sum + (states[key] || 0), 0)) : '尚未确认'
  const queues = data.queue_metrics?.items || []
  const observed = queues.filter((row) => row.status === 'available' && Array.isArray(row.pollers))
  return [
    { label: '排队等待', value: count('queued', 'pending'), note: '等待任务执行器接收' },
    { label: '正在执行', value: count('running'), note: '持久任务记录中的执行状态' },
    { label: '等待恢复', value: count('recovery_wait'), note: '等待检查依赖或原操作结果' },
    {
      label: '有轮询者的队列',
      value: observed.length
        ? `${observed.filter((q) => q.pollers.length).length} / ${observed.length}`
        : '尚未确认',
      note: `已成功观测的队列中有执行器轮询的数量；${queues.length - observed.length} 个队列观测未确认`
    }
  ]
}

const chartAxis = (dark) => ({
  axisLabel: { color: dark ? '#cfd3dc' : '#606266' },
  nameTextStyle: { color: dark ? '#cfd3dc' : '#606266' },
  splitLine: { lineStyle: { color: dark ? '#414243' : '#e4e7ed' } }
})

export function categoryChart(values = {}, label = statusText, dark = false) {
  return {
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    grid: { left: 18, right: 30, bottom: 20, top: 15, containLabel: true },
    xAxis: { ...chartAxis(dark), type: 'value', minInterval: 1 },
    yAxis: {
      type: 'category',
      data: Object.keys(values).map(label),
      axisLabel: { ...chartAxis(dark).axisLabel, width: 130, overflow: 'truncate' }
    },
    series: [
      {
        type: 'bar',
        data: Object.values(values),
        barMaxWidth: 24,
        itemStyle: { color: '#409eff', borderRadius: [0, 5, 5, 0] }
      }
    ]
  }
}

export function businessCharts(series = [], dark = false) {
  const axis = series.map((p) =>
    new Date(p.at).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false
    })
  )
  const base = {
    tooltip: { trigger: 'axis', renderMode: 'richText' },
    legend: { bottom: 0, textStyle: { color: dark ? '#cfd3dc' : '#606266' } },
    grid: { left: 20, right: 24, top: 30, bottom: 55, containLabel: true },
    xAxis: { ...chartAxis(dark), type: 'category', data: axis, boundaryGap: false },
    yAxis: { ...chartAxis(dark), type: 'value', min: 0 }
  }
  return {
    requests: {
      ...base,
      yAxis: { ...chartAxis(dark), type: 'value', minInterval: 1 },
      series: [
        { name: '请求总数', key: 'requests', color: '#409eff' },
        { name: '已完成', key: 'complete', color: '#67c23a' },
        { name: '失败', key: 'failed', color: '#f56c6c' }
      ].map((s) => ({
        name: s.name,
        type: 'line',
        showSymbol: false,
        itemStyle: { color: s.color },
        data: series.map((p) => p[s.key])
      }))
    },
    latency: {
      ...base,
      yAxis: { ...chartAxis(dark), type: 'value', name: '毫秒', min: 0 },
      series: [
        {
          name: '首字响应 P95',
          type: 'line',
          showSymbol: false,
          connectNulls: false,
          itemStyle: { color: '#e6a23c' },
          data: series.map((p) => p.first_token_p95_ms ?? null)
        }
      ]
    }
  }
}

export const flowLabel = (value) =>
  ({
    remember: '记忆写入与整理',
    recall: '记忆召回',
    operate: '冷热分层与存储维护',
    runtime: '运行维护'
  })[value] || readable(value)
export const actionLabel = (value) =>
  ({ promote: '提升存储层级', demote: '降低存储层级', keep: '保持当前层级', remove: '移除副本' })[
    value
  ] || statusText(value)
