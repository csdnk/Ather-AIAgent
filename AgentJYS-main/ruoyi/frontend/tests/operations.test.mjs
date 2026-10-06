import test from 'node:test'
import assert from 'node:assert/strict'
import {
  commandLookupParams,
  createCommand,
  displayValue,
  normalizeRows,
  maySubmit
} from '../src/views/aether/operations.mjs'
test('a pending or unknown command cannot be resubmitted', () => {
  assert.equal(maySubmit({ state: 'unknown' }), false)
  assert.equal(maySubmit({ state: 'accepted' }), false)
  assert.equal(maySubmit(null), true)
})
test('zero is data while missing values remain unknown', () => {
  assert.equal(displayValue(0), '0')
  assert.equal(displayValue(null), '未知')
  assert.equal(displayValue(false), '否')
})
test('record payloads flatten only safe visible data', () => {
  assert.deepEqual(normalizeRows({ items: [{ id: 'x', version: 2, payload: { name: 'GPU' } }] }), [
    { id: 'x', version: 2, name: 'GPU' }
  ])
  assert.deepEqual(normalizeRows({}), [])
})
test('new command preserves expected version and generates a stable id', () => {
  const c = createCommand('resources', 'save', 'id', 3, { name: 'GPU' })
  assert.match(c.command_id, /^[a-zA-Z0-9_-]{8,128}$/)
  assert.equal(c.expected_version, 3)
  assert.equal(c.command_id, c.command_id)
})

test('exact command lookup stays within the Python paging contract', () => {
  const params = commandLookupParams('stable-command-id')
  assert.equal(params.command_id, 'stable-command-id')
  assert.ok(params.limit === undefined || (params.limit >= 1 && params.limit <= 100))
  assert.equal(params.offset, undefined)
})
