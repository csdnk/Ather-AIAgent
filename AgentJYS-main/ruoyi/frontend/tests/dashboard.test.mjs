import test from 'node:test'
import assert from 'node:assert/strict'
import {
  contractCards,
  schedulingCards,
  businessCharts,
  actionLabel
} from '../src/views/aether/dashboard.mjs'

test('missing contract evidence never looks like zero or passing', () => {
  const cards = contractCards({})
  assert.equal(cards.length, 5)
  assert.ok(cards.every((card) => card.state !== 'success'))
  assert.ok(cards.every((card) => card.value !== '0'))
})

test('text compression does not claim full physical acceptance', () => {
  const card = contractCards({ compression: { ratio: 6, samples: 2 } })[2]
  assert.equal(card.value, '6.00 倍')
  assert.notEqual(card.state, 'success')
  assert.match(card.note, /正文/)
})

test('home cards display measured values and keep unavailable metrics unknown', () => {
  const cards = contractCards({
    embedding: { status: 'available', rate: 12.345, items: 25, batch_count: 5 },
    working_memory: { status: 'available', p99_ms: 19.872, samples: 30 },
    compression: { ratio: 2.125, samples: 4 }
  })
  assert.equal(cards[0].value, '12.35 条/秒')
  assert.equal(cards[1].value, '19.87 毫秒')
  assert.equal(cards[2].value, '2.13 倍')
  assert.ok(cards.every((card) => card.state !== 'success'))
  const failed = contractCards({
    embedding: { status: 'unavailable' },
    working_memory: { status: 'no_samples' }
  })
  assert.equal(failed[0].value, '暂时无法读取')
  assert.equal(failed[1].value, '暂无读取样本')
})

test('compression no-sample states explain the actual processing stage', () => {
  assert.equal(contractCards({ compression: { reason: 'not_triggered' } })[2].value, '尚未触发压缩')
  assert.equal(contractCards({ compression: { reason: 'pending' } })[2].value, '压缩处理中')
  assert.equal(contractCards({ compression: { reason: 'failed' } })[2].value, '压缩处理失败')
})

test('scheduling uses global summary and does not invent unknown queue metrics', () => {
  const cards = schedulingCards({
    task_summary: { by_state: { running: 3, queued: 7, recovery_wait: 2 } },
    tasks: { items: [] },
    queue_metrics: { items: [{ status: 'unavailable' }] }
  })
  assert.equal(cards[0].value, '7')
  assert.equal(cards[1].value, '3')
  assert.equal(cards[2].value, '2')
  assert.equal(cards[3].value, '尚未确认')
})

test('latency chart preserves missing samples as gaps', () => {
  const charts = businessCharts([
    { at: '2026-10-06T00:00:00Z', first_token_p95_ms: 9999, recall_return_p95_ms: null },
    { at: '2026-10-06T00:01:00Z', first_token_p95_ms: 9999, recall_return_p95_ms: 250 }
  ])
  assert.equal(charts.latency.series[0].data[0], null)
  assert.equal(charts.latency.series[0].data[1], 250)
  assert.equal(charts.latency.series[0].connectNulls, false)
})

test('placement intent does not assume an incorrect target tier', () => {
  assert.equal(actionLabel('promote'), '提升存储层级')
  assert.equal(actionLabel('demote'), '降低存储层级')
})
