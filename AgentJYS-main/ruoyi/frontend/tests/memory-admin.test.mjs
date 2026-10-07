import test from 'node:test'
import assert from 'node:assert/strict'
import { operationsError } from '../src/config/axios/operationsError.mjs'
import {
  canManageMemory,
  editableBody,
  commandTerminal,
  pendingReference,
  commandErrorText,
  settleCommand,
  abandonEnvelope,
  recoveredPending,
  mutationParameters,
  memoryMutation
} from '../src/views/aether/memoryAdmin.mjs'

test('memory operations require content read, operations read, general execution and action permission', () => {
  const permissions = [
    'aether:ops:read',
    'aether:content:read',
    'aether:memories:execute',
    'aether:memory:update'
  ]
  assert.equal(canManageMemory({ permissions }, 'memory_update'), true)
  for (const missing of permissions)
    assert.equal(
      canManageMemory({ permissions: permissions.filter((p) => p !== missing) }, 'memory_update'),
      false
    )
  assert.equal(canManageMemory({ permissions }, 'memory_delete'), false)
  assert.equal(canManageMemory({ permissions: ['*:*:*'] }, 'memory_update'), false)
})

test('only an authoritative missing lookup enables sealing the original request id', () => {
  const reference = {
    command_id: 'original-id',
    user_id: 'u1',
    action: 'memory_update',
    status: 'unknown'
  }
  assert.equal(abandonEnvelope(reference), null)
  const envelope = abandonEnvelope({ ...reference, unaccepted: true, text: 'private body' })
  assert.deepEqual(envelope, {
    command_id: 'original-id',
    resource: 'memories',
    action: 'memory_abandon',
    parameters: { user_id: 'u1', original_action: 'memory_update' }
  })
  assert.equal(recoveredPending(reference, { status: 'abandoned' }), null)
  assert.equal(recoveredPending(reference, {}).status, 'unknown')
  assert.equal(recoveredPending(reference, { status: 'pending' }).status, 'pending')
  assert.equal(recoveredPending(reference, { status: 'succeeded' }).status, 'succeeded')
  assert.equal(commandTerminal('unknown'), false)
  assert.equal(commandTerminal('abandoned'), true)
})

test('every existing-memory mutation carries the read object revision and refuses missing CAS data', () => {
  const detail = { memory: { ref: { version: 7 }, object_revision: 3 } }
  assert.deepEqual(mutationParameters(detail, { text: 'updated body' }), {
    text: 'updated body',
    expected_object_revision: 3
  })
  assert.deepEqual(mutationParameters(detail), { expected_object_revision: 3 })
  assert.throws(() => mutationParameters({ memory: { ref: { version: 7 } } }))
  assert.throws(() => mutationParameters({ memory: { object_revision: 3 } }))
})

test('ready-index metadata revision does not replace the content reference version in outgoing mutations', () => {
  const detail = {
    memory: { ref: { version: 1 }, revision: 2, object_revision: 2, projection_state: 'ready' }
  }
  assert.deepEqual(memoryMutation(detail, { text: '修正后的正文' }), {
    expected_version: 1,
    parameters: { text: '修正后的正文', expected_object_revision: 2 }
  })
  assert.deepEqual(memoryMutation(detail), {
    expected_version: 1,
    parameters: { expected_object_revision: 2 }
  })
  assert.throws(() => memoryMutation({ memory: { revision: 2, object_revision: 2 } }))
})

test('editing only uses confirmed body and never summary or human-readable failure text', () => {
  assert.equal(
    editableBody({ body: { outcome: 'read', content: { text: '真实正文' } } }),
    '真实正文'
  )
  assert.equal(
    editableBody({ body: { outcome: 'excluded', content: '旧正文' }, summary: '摘要' }),
    null
  )
  assert.equal(
    editableBody({ status: 'forbidden', body: { outcome: 'read', content: '旧正文' } }),
    null
  )
  assert.equal(editableBody({ summary: '摘要' }), null)
})

test('recovery references retain only actor-scoped command metadata, never memory body or result', () => {
  const reference = pendingReference({
    command_id: 'c1',
    action: 'memory_create',
    parameters: { user_id: 'u1', text: 'secret' },
    result: { content: 'secret' }
  })
  assert.deepEqual(reference, { command_id: 'c1', action: 'memory_create', user_id: 'u1' })
  assert.equal(commandTerminal('pending'), false)
  assert.equal(commandTerminal('unknown'), false)
  assert.equal(commandTerminal('succeeded'), true)
})

test('network uncertainty and nonterminal provider receipt never claim success', () => {
  assert.equal(settleCommand({ status: 'pending', result: { outcome: 'saved' } }).status, 'pending')
  assert.equal(settleCommand({}).status, 'unknown')
  assert.match(commandErrorText({ code: 'VERSION_CONFLICT' }), /刷新/)
  assert.doesNotMatch(commandErrorText({ message: 'provider secret token' }), /secret/)
})

test('safe memory error codes survive transport without provider exception text', () => {
  const error = operationsError(409, 'MEMORY_REVISION_CONFLICT private provider details')
  assert.equal(error.memoryCode, 'MEMORY_REVISION_CONFLICT')
  assert.match(commandErrorText(error), /刷新/)
  assert.equal(operationsError(500, 'PRIVATE_PROVIDER_FAILURE secret').memoryCode, undefined)
  assert.doesNotMatch(error.message, /private|provider/)
})
