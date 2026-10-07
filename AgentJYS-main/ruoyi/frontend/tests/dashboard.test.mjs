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
    { at: '2026-10-06T00:00:00Z', requests: 0, complete: 0, failed: 0, first_token_p95_ms: null }
  ])
  assert.equal(charts.latency.series[0].data[0], null)
  assert.equal(charts.latency.series[0].connectNulls, false)
})

test('placement intent does not assume an incorrect target tier', () => {
  assert.equal(actionLabel('promote'), '提升存储层级')
  assert.equal(actionLabel('demote'), '降低存储层级')
})
