import test from 'node:test'
import assert from 'node:assert/strict'
import {
  canReadOperations,
  canReadTaskDiagnostics,
  readTaskDiagnostics
} from '../src/views/aether/taskAccess.mjs'

test('platform role without current read grant cannot request monitoring data', async () => {
  for (const permissions of [undefined, [], ['aether:tasks:execute']]) {
    const identity = { role_codes: ['aether_platform_admin'], permissions }
    assert.equal(canReadTaskDiagnostics(identity), false)
    let calls = 0
    await assert.rejects(
      readTaskDiagnostics(
        async () => identity,
        async () => {
          calls++
        },
        {}
      ),
      (error) => error.code === 403
    )
    assert.equal(calls, 0)
  }
})

test('homepage requires an administrator role and a current read grant', () => {
  for (const role of ['aether_platform_admin', 'aether_tenant_admin']) {
    assert.equal(canReadOperations({ role_codes: [role], permissions: ['aether:ops:read'] }), true)
    assert.equal(canReadOperations({ role_codes: [role], permissions: [] }), false)
  }
  assert.equal(
    canReadOperations({ role_codes: ['aether_user'], permissions: ['aether:ops:read'] }),
    false
  )
  assert.equal(canReadOperations({}), false)
  assert.equal(
    canReadTaskDiagnostics({
      role_codes: ['aether_tenant_admin'],
      permissions: ['aether:ops:read']
    }),
    false
  )
})
