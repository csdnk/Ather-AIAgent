import test from 'node:test'
import assert from 'node:assert/strict'
import { isPublicAuthRequest, isLoginPage } from '../src/config/axios/authRequest.mjs'

test('audit log requests always carry authentication, including query strings', () => {
  for (const path of [
    '/system/login-log/page',
    '/system/login-log/get?id=1',
    '/system/login-log/export-excel',
    '/system/operate-log/page'
  ]) {
    assert.equal(isPublicAuthRequest(path), false)
  }
  for (const path of [
    '/aether/identity/login',
    '/system/auth/login',
    '/system/auth/refresh-token?refreshToken=example'
  ]) {
    assert.equal(isPublicAuthRequest(path), true)
  }
})
test('only the login page suppresses a relogin dialog, not login audit routes', () => {
  assert.equal(isLoginPage('/ruoyi/login'), true)
  assert.equal(isLoginPage('/ruoyi/login/'), true)
  assert.equal(isLoginPage('/ruoyi/system/log/login-log'), false)
  assert.equal(isLoginPage('/ruoyi/system/loginlog'), false)
})
