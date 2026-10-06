import test from 'node:test'
import assert from 'node:assert/strict'
import {
  cellText,
  columnsFor,
  explainRow,
  healthItems,
  metricTotal,
  statusText,
  dataNotice
} from '../src/views/aether/presentation.mjs'

test('available console data does not trigger a retry warning while failures remain visible', () => {
  assert.equal(dataNotice({ status: 'available', items: [{ id: 'task' }] }), '')
  assert.match(dataNotice({ status: 'unavailable', items: [] }), /刷新重试/)
})

test('real backend failure and intervention states retain their definite meaning', () => {
  for (const [state, text] of Object.entries({
    save_failed: '记忆保存失败',
    save_queued: '已排队等待保存',
    attention_required: '需要人工处理',
    retry_wait: '等待自动重试',
    recovery_wait: '等待恢复检查',
    not_ready: '暂不能接收业务请求'
  }))
    assert.equal(statusText(state), text)
  assert.match(
    explainRow('memories', { status: 'complete', memory_status: 'save_failed' }),
    /保存失败/
  )
  assert.match(explainRow('tasks', { state: 'attention_required' }), /人工/)
})

test('operator status separates accepted, complete and unconfirmed work', () => {
  assert.equal(statusText('accepted'), '已受理，等待执行结果')
  assert.equal(statusText('unknown'), '结果尚未确认')
  assert.equal(statusText('unfamiliar_new_state'), '状态待核实')
  assert.match(explainRow('tasks', { state: 'failed', error_code: 'BUDGET_EXHAUSTED' }), /预算/)
  assert.doesNotMatch(explainRow('commands', { status: 'accepted' }), /执行成功/)
})

test('fixed business columns do not expose internal identifiers or arbitrary fields', () => {
  for (const resource of ['requests', 'tasks', 'memories', 'audit', 'commands', 'backups']) {
    assert.ok(columnsFor(resource).length > 0)
    assert.ok(
      !columnsFor(resource).some((c) =>
        /^(id|task_id|job_id|fingerprint|payload|result)$/.test(c.key)
      )
    )
  }
  assert.equal(
    cellText('memories', 'memory_evidence', {
      memory_evidence: { status: 'saved', job_id: 'secret-id' }
    }),
    '已保存'
  )
  assert.equal(cellText('requests', 'first_token_ms', { first_token_ms: 0 }), '0 毫秒')
  assert.equal(cellText('requests', 'first_token_ms', {}), '尚无数据')
})

test('missing health or usage is never silently reported as healthy or zero', () => {
  assert.equal(metricTotal({}, 'failed'), '尚无数据')
  assert.equal(metricTotal({ usage: { items: [{ failed: 0 }] } }, 'failed'), 0)
  assert.equal(metricTotal({ usage: { status: 'unavailable', items: [] } }, 'failed'), '尚无数据')
  assert.ok(healthItems({ status: 'unknown' }).every((r) => r.text !== '运行正常'))
})
