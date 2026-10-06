import test from 'node:test'
import assert from 'node:assert/strict'
import {
  observation,
  memoryText,
  recallEvidence,
  taskOutcome,
  targetQuery,
  sectionItems
} from '../src/views/aether/console.mjs'

test('missing, failed, disabled and empty observations cannot imply healthy zero', () => {
  assert.match(observation({}), /尚未采集/)
  assert.match(observation({ status: 'unavailable', items: [] }), /无法读取/)
  assert.match(observation({ status: 'disabled' }), /未启用/)
  assert.match(observation({ status: 'ok', items: [] }), /没有记录/)
  assert.match(observation({ status: 'stale' }), /过期/)
})
test('a sampled runtime is not missing data and explicit failures remain authoritative', () => {
  const runtime = {
    worker: 'running',
    temporal: 'available',
    reason_code: 'READY',
    observed_at: '2026-10-06T07:19:40Z'
  }
  assert.match(observation(runtime), /已读取/)
  assert.match(observation({ ...runtime, status: 'unavailable' }), /采集失败/)
})
test('memory content distinguishes denied bodies from actual empty text', () => {
  assert.match(memoryText({ status: 'forbidden', body: 'cached secret' }), /无权/)
  assert.match(memoryText({ status: 'deleted', body: 'cached secret' }), /删除/)
  assert.equal(memoryText({ body: '用户真实偏好' }), '用户真实偏好')
  assert.match(memoryText({}), /未返回/)
})
test('recall membership never implies that a model used a memory', () => {
  assert.match(recallEvidence({ items: [{ id: 'm' }] }), /检索返回/)
  assert.doesNotMatch(recallEvidence({ items: [{ id: 'm' }] }), /模型已采用/)
  assert.match(recallEvidence({ status: 'failed', items: [] }), /失败/)
  assert.match(recallEvidence({ status: 'ok', items: [] }), /未返回记忆/)
})
test('workflow completion does not establish business effect', () => {
  assert.match(taskOutcome({ workflow: { status: 'completed' } }), /尚未确认/)
  assert.match(taskOutcome({ effect_state: 'confirmed' }), /已确认/)
  assert.match(taskOutcome({ effect_state: 'no_effect' }), /未产生/)
})
test('selected user scopes paging and opaque cursor remains unchanged', () => {
  assert.deepEqual(targetQuery('alice', { limit: 20, cursor: 'a+/=' }), {
    user_id: 'alice',
    limit: 20,
    cursor: 'a+/='
  })
  assert.throws(() => targetQuery('', {}))
  assert.deepEqual(sectionItems({ items: [{ id: 1 }] }), [{ id: 1 }])
  assert.deepEqual(sectionItems({ status: 'unavailable' }), [])
})

test('P3 body exclusion is authoritative even when cached text exists', () => {
  assert.match(
    memoryText({ body: { outcome: 'excluded', content: 'stale private body' } }),
    /不可读取/
  )
  assert.equal(memoryText({ body: { outcome: 'available', content: '原文正文' } }), '原文正文')
})
test('P3 ContextPack groups expose real recall content and incomplete coverage', () => {
  assert.match(
    recallEvidence({
      status: 'ok',
      result: { outcome: 'degraded', groups: [{ items: [{ content: 'x' }] }] }
    }),
    /部分/
  )
  assert.match(recallEvidence({ status: 'ok', result_status: 'unavailable', result: null }), /无法/)
})

test('task sample summaries expose counts only for returned business records', async () => {
  const { pipelineRows } = await import('../src/views/aether/console.mjs')
  assert.match(pipelineRows({})[0].summary, /尚未/)
  const rows = pipelineRows({
    status: 'available',
    tasks: { items: [{ kind: 'remember.extract', state: 'failed' }] }
  })
  assert.match(rows.find((r) => r.key === 'remember').summary, /1/)
  assert.match(rows.find((r) => r.key === 'operate').summary, /本页未见/)
})

test('deployment summary overrides current page and does not invent missing flow counts', async () => {
  const { pipelineRows } = await import('../src/views/aether/console.mjs')
  const rows = pipelineRows({
    task_summary: { total: 200, by_flow: { remember: 150, recall: 0 } },
    tasks: { items: [{ kind: 'remember.extract' }] }
  })
  assert.match(rows.find((row) => row.key === 'remember').summary, /150/)
  assert.match(rows.find((row) => row.key === 'recall').summary, /0/)
  assert.match(rows.find((row) => row.key === 'operate').summary, /未返回/)
})

test('available empty ContextPack is a confirmed empty recall, not missing evidence', () => {
  assert.match(
    recallEvidence({
      status: 'available',
      result_status: 'available',
      result: { outcome: 'empty', groups: [] }
    }),
    /未返回记忆/
  )
  assert.match(
    recallEvidence({ status: 'available', result_status: 'not_available', result: null }),
    /尚未/
  )
})

test('task audience presents resolved names without exposing internal subject IDs', async () => {
  const { taskAudience } = await import('../src/views/aether/console.mjs')
  assert.equal(taskAudience({ tenant_name: '示例企业', user_name: '王小明' }), '示例企业 · 王小明')
  assert.equal(
    taskAudience({ subject: { scope: { tenant_id: 'private-tenant', user_id: 'private-user' } } }),
    ''
  )
  assert.equal(taskAudience({ tenant_name: '示例企业' }), '示例企业')
})
