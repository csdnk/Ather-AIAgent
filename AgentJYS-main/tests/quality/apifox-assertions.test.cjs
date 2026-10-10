const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
function checks() {
  const file = path.resolve(__dirname, '../../scripts/quality/apifox-assertions.js');
  assert.ok(fs.existsSync(file), 'API semantic assertions required');
  const context = {};
  vm.runInNewContext(fs.readFileSync(file, 'utf8'), context);
  return context;
}
test('identity rejects wrong tenant despite HTTP 200', () => {
  const c = checks();
  const data = {code:0,data:{user_id:'50004',tenant_id:'502',user_enabled:true,tenant_enabled:true,role_codes:['aether_user']}};
  assert.throws(() => c.qualityIdentity(200,data,'50004','501'));
  data.data.tenant_id='501';
  c.qualityIdentity(200,data,'50004','501');
});
test('denial rejects transport failures and accidental success', () => {
  const c=checks();
  assert.throws(() => c.qualityDenied(500,{code:401},401));
  assert.throws(() => c.qualityDenied(200,{code:0},401));
  c.qualityDenied(200,{code:401},401);
  c.qualityDenied(401,{code:401},401);
});
test('login requires a real token and expected user', () => {
  const c=checks();
  assert.throws(() => c.qualityLogin(200,{code:0,data:{userId:50004}},'50004'));
  assert.throws(() => c.qualityLogin(200,{code:0,data:{userId:50006,accessToken:'example'}},'50004'));
  c.qualityLogin(200,{code:0,data:{userId:50004,accessToken:'example'}},'50004');
});
