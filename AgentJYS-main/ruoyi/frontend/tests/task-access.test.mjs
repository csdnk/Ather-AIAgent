import test from 'node:test'
import assert from 'node:assert/strict'
import { readTaskDiagnostics } from '../src/views/aether/taskAccess.mjs'
import { operationsError } from '../src/config/axios/operationsError.mjs'
import { errorMessage, tableEmptyText } from '../src/views/aether/console.mjs'

test('tenant and unknown identities never request deployment-wide diagnostics', async () => {
  for (const identity of [{ role_codes: ['aether_tenant_admin'] }, {}]) {
    let calls = 0
    await assert.rejects(
      readTaskDiagnostics(
        async () => identity,
        async () => {
          calls++
        },
        {}
      ),
      (error) => error.code === 403 && /平台管理员/.test(error.message)
    )
    assert.equal(calls, 0)
  }
})

test('platform identity reads real diagnostics with the exact filters and cursor', async () => {
  const params = { limit: 30, state: 'failed', cursor: 'original-cursor' }
  const expected = { tasks: { items: [{ task_id: 'existing-task' }] }, status: 'available' }
  const actual = await readTaskDiagnostics(
    async () => ({ role_codes: ['aether_platform_admin'] }),
    async (resource, query) => {
      assert.equal(resource, 'diagnostics')
      assert.deepEqual(query, params)
      return expected
    },
    params
  )
  assert.equal(actual, expected)
})

test('HTTP 200 containing business code 403 remains a permission failure', () => {
  const error = operationsError(403)
  assert.equal(error.code, 403)
  assert.match(errorMessage(error), /无权/)
  assert.doesNotMatch(errorMessage(error), /服务连接/)
  assert.match(operationsError(503).message, /暂时无法读取/)
})

test('failed, forbidden and loading tables never report no records', () => {
  assert.match(tableEmptyText({ status: 'unavailable' }, false), /加载失败/)
  assert.match(tableEmptyText({ status: 'forbidden' }, false), /无权/)
  assert.match(tableEmptyText({}, true), /读取中/)
  assert.match(tableEmptyText({ status: 'ok', items: [] }, false), /没有记录/)
})
