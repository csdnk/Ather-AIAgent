import test from 'node:test'
import assert from 'node:assert/strict'
import {
  memorySummary,
  memoryLifecycleNotice,
  memoryLifecyclePrompt
} from '../src/views/aether/memoryList.mjs'
import { memoryText } from '../src/views/aether/console.mjs'

test('memory list distinguishes pending, timeout and invalid sources from body content', () => {
  assert.equal(memorySummary({ summary: '实际正文' }), '实际正文')
  assert.match(memorySummary({ summary_status: 'pending' }), /加载中/)
  assert.match(
    memorySummary({ summary_status: 'unavailable', summary_error_code: 'DEADLINE_EXCEEDED' }),
    /超时/
  )
  assert.match(memorySummary({ summary_status: 'evidence_unsupported' }), /来源证据/)
  assert.match(memorySummary({ summary_status: 'source_deleted' }), /来源已失效/)
  assert.match(memorySummary({ status: 'deleted' }), /逻辑删除/)
  assert.doesNotMatch(memorySummary({}), /点击查看全文/)
})

test('retained content stays visible and lifecycle effects are explicit', () => {
  assert.equal(memorySummary({ status: 'deleted', summary: '保留正文' }), '保留正文')
  assert.match(memorySummary({ status: 'deleted', summary_status: 'pending' }), /加载中/)
  assert.match(
    memorySummary({ status: 'deleted', summary_status: 'retained_body_missing' }),
    /不存在/
  )
  assert.match(memoryLifecycleNotice('archived'), /恢复使用/)
  assert.match(memoryLifecycleNotice('deleted'), /只读/)
  assert.match(memoryLifecyclePrompt('memory_delete'), /保留正文/)
  assert.match(memoryLifecyclePrompt('memory_archive'), /暂停/)
  assert.equal(
    memoryText({ status: 'available', body: { content: '保留正文', outcome: 'read' } }),
    '保留正文'
  )
  assert.match(
    memoryText({ status: 'unavailable', body: { reason_code: 'retained_body_missing' } }),
    /不存在/
  )
})
