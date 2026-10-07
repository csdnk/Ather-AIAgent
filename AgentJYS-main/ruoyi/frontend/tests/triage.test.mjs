import test from 'node:test'
import assert from 'node:assert/strict'
import {
  taskTriage,
  controlAvailability,
  businessQueues,
  taskStateFilter
} from '../src/views/aether/triage.mjs'

test('terminal failure and attention cannot offer recovery controls even with a running workflow', () => {
  for (const state of ['failed', 'attention_required', 'succeeded', 'cancelled']) {
    assert.equal(
      controlAvailability({
        state,
        revision: 3,
        workflow: { state: 'running', status: 'available' }
      }).allowed,
      false
    )
  }
  const info = taskTriage({
    state: 'attention_required',
    error_code: 'FORBIDDEN',
    effect_status: 'confirmed'
  })
  assert.match(info.retry, /不会自动重试/)
  assert.match(info.steps.join(' '), /权限/)
  assert.match(info.effect, /已确认/)
  assert.match(info.steps.join(' '), /原.*结果|重复/)
})

test('controls need a current running workflow and task revision', () => {
  assert.equal(
    controlAvailability({
      state: 'recovery_wait',
      revision: 3,
      workflow: { state: 'running', status: 'available' }
    }).allowed,
    true
  )
  for (const workflow of [
    undefined,
    { state: 'completed', status: 'available' },
    { state: 'running', status: 'unavailable' }
  ]) {
    assert.equal(controlAvailability({ state: 'running', revision: 3, workflow }).allowed, false)
  }
  assert.equal(
    controlAvailability({ state: 'running', workflow: { state: 'running', status: 'available' } })
      .allowed,
    false
  )
})

test('missing diagnosis and unconfirmed effects never invent cause or safe retry', () => {
  const info = taskTriage({ state: 'failed' })
  assert.match(info.cause, /未记录|未返回/)
  assert.match(info.effect, /未确认/)
  assert.match(info.steps.join(' '), /重复/)
  assert.equal(taskStateFilter('attention_required'), 'attention_required')
  assert.equal(taskStateFilter(['failed']), '')
  assert.equal(taskStateFilter('arbitrary'), '')
})

test('queue rows are grouped by exact business queue, preserving unknown and stale observations', () => {
  const observed = '2026-10-07T05:00:00Z'
  const row = (task_type, extras = {}) => ({
    task_queue: 'agent.a.remember',
    task_type,
    status: 'available',
    pollers: [{ last_access_time: observed }],
    backlog_count_hint: 0,
    ...extras
  })
  const result = businessQueues({
    observed_at: observed,
    items: [row('workflow'), row('activity')]
  })
  assert.equal(result.length, 1)
  assert.equal(result[0].workflow.backlog, 0)
  assert.equal(result[0].activity.backlog, 0)
  assert.equal(result[0].needsCheck, false)
  const partial = businessQueues({
    observed_at: observed,
    items: [
      row('workflow'),
      row('activity', { status: 'unavailable', pollers: undefined, backlog_count_hint: undefined })
    ]
  })[0]
  assert.equal(partial.needsCheck, true)
  assert.equal(partial.activity.backlog, null)
  const stale = businessQueues({
    observed_at: observed,
    items: [row('workflow', { pollers: [{ last_access_time: '2026-10-07T04:00:00Z' }] })]
  })[0]
  assert.equal(stale.needsCheck, true)
  assert.match(stale.workflow.label, /近期/)
  assert.equal(businessQueues({ items: [] }).length, 0)
})
