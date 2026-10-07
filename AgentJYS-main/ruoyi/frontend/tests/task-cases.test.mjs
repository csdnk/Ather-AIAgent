import test from 'node:test'
import assert from 'node:assert/strict'
import { caseActions, caseError, caseState } from '../src/views/aether/taskCases.mjs'

test('case buttons follow ownership, verification and additional control permission', () => {
  const me = {
    user_id: 'me',
    role_codes: ['aether_platform_admin'],
    permissions: ['aether:ops:read', 'aether:support:execute']
  }
  const record = { state: 'in_progress', owner_id: 'me' }
  const task = {
    state: 'running',
    revision: 3,
    workflow: { status: 'available', state: 'running' }
  }
  assert.ok(caseActions(record, me, task).includes('resolve'))
  assert.ok(!caseActions(record, me, task).includes('reconcile'))
  const operator = { ...me, permissions: [...me.permissions, 'aether:tasks:execute'] }
  assert.ok(caseActions(record, operator, task).includes('reconcile'))
  assert.ok(
    !caseActions({ ...record, control: { status: 'unknown' } }, operator, task).includes(
      'reconcile'
    )
  )
  assert.ok(
    caseActions({ ...record, control: { status: 'unknown' } }, operator, task).includes('poll')
  )
  assert.deepEqual(caseActions(record, { ...me, role_codes: ['aether_tenant_admin'] }, task), [])
  assert.ok(!caseActions({ ...record, owner_id: 'other' }, operator, task).includes('resolve'))
})

test('closed cases reopen but cannot silently edit or repeat commands', () => {
  const me = { user_id: 'me', role_codes: ['aether_platform_admin'], permissions: ['*:*:*'] }
  assert.deepEqual(caseActions({ state: 'closed' }, me, {}), ['reopen'])
  assert.ok(
    !caseActions(
      { state: 'awaiting_verification', owner_id: 'me', resolution: { kind: 'no_longer_needed' } },
      me,
      {}
    ).includes('verify')
  )
  assert.equal(caseState('awaiting_verification'), '待核验')
  assert.match(caseError('Aether operations: CASE_VERIFY_AGAIN'), /重新核验/)
})
