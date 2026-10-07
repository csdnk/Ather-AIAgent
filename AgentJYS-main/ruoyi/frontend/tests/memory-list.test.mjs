import test from 'node:test'
import assert from 'node:assert/strict'
import { memorySummary } from '../src/views/aether/memoryList.mjs'

test('memory list distinguishes pending, timeout and invalid sources from body content', () => {
  assert.equal(memorySummary({ summary: '实际正文' }), '实际正文')
  assert.match(memorySummary({ summary_status: 'pending' }), /加载中/)
  assert.match(
    memorySummary({ summary_status: 'unavailable', summary_error_code: 'DEADLINE_EXCEEDED' }),
    /超时/
  )
  assert.match(memorySummary({ summary_status: 'evidence_unsupported' }), /来源证据/)
  assert.match(memorySummary({ summary_status: 'source_deleted' }), /来源已失效/)
  assert.match(memorySummary({ status: 'deleted' }), /已删除/)
  assert.doesNotMatch(memorySummary({}), /点击查看全文/)
})
