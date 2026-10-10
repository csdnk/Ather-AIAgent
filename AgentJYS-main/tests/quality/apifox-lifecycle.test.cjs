// Execute the shipped post-response script with synthetic transport responses.
// No HTTP, credentials, provider calls, generated files, or source rewriting.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const script = fs.readFileSync(path.resolve(__dirname, '../../scripts/quality/apifox-lifecycle.js'), 'utf8');
const RUN = 'regression-session-1';
const TOKEN = 'synthetic-test-token';
const clone = value => JSON.parse(JSON.stringify(value));

function fixtures() {
  const home = {tenant_id: 'tenant-a', user_id: 'user-a', application_id: 'app-a',
    agent_id: 'agent-a', session_id: null, task_id: null};
  const scope = {...home, session_id: RUN};
  const memory = {scope, memory_id: 'memory-a', version: 1};
  const source = {source_id: 'source-a', source_version: 1,
    content_hash: 'a'.repeat(64), locator: 'conversation:regression-session-1'};
  const receipt = {operation_id: 'save_' + RUN, saved: true, source, memories: [memory],
    task_ids: [], phase: 'ready'};
  return clone({
    'auth-self': {code: 0, data: {user_id: '50004', tenant_id: '501',
      user_enabled: true, tenant_enabled: true, role_codes: ['aether_user']}},
    'p3-self': {scope: home}, save: receipt, replay: receipt,
    read: {memory, outcome: 'read', path: 'authority', reason_code: 'read',
      content: 'Aether synthetic regression ' + RUN + '. The retention window is exactly 17 days, not 70 days.',
      sources: [source]},
    recall: {recall_id: 'recall-a', scope, outcome: 'available', selected_sources: ['working'],
      coverage: {working: 'complete', long_term: 'not_requested'},
      groups: [{group_id: 'group-a', items: [{memory, content: 'The retention window is exactly 17 days.',
        sources: [source], representation: 'original'}]}],
      rendered_context: 'The retention window is exactly 17 days.', token_budget: 1024,
      tokens_used: 11, tokenizer_id: 'test-tokenizer', policy_version: 'test-policy',
      degradation_reasons: [], committed_at: '2026-10-09T00:00:00.000Z'},
    snapshot: {ref: memory, object_revision: 7},
    delete: {operation_id: 'delete_' + RUN, blocked: true, cleanup_state: 'pending'},
    deleted: {memory, outcome: 'excluded', path: 'none', reason_code: 'deleted', content: null, sources: []},
    logout: {code: 0, data: true}
  });
}

async function execute({edit = () => {}, limits = {}, pollBodies, expireAfter} = {}) {
  const bodies = fixtures();
  edit(bodies);
  const values = {auth_base: 'http://127.0.0.1:14880', p3_base: 'http://127.0.0.1:14881',
    expected_user_id: '50004', expected_tenant_id: '501',
    expected_p3_user_id: 'user-a', expected_p3_tenant_id: 'tenant-a',
    max_requests: '40', max_poll_attempts: '20', ...limits};
  const variables = new Map([['run_id', RUN]]);
  const assertions = [], requests = [], logs = [];
  let saves = 0, reads = 0, polls = 0, now = Date.parse('2026-10-09T00:00:00.000Z');
  class Clock extends Date {
    constructor(...args) { super(...(args.length ? args : [now])); }
    static now() { return now; }
  }
  const response = (body, code = 200) => ({code, json: () => clone(body), headers: {get: () => null}});
  const pm = {
    environment: {get: key => values[key]},
    variables: {get: key => variables.get(key), set: (key, value) => variables.set(key, value),
      unset: key => variables.delete(key)},
    response: response({code: 0, data: {userId: 50004, accessToken: TOKEN}}),
    expect: actual => ({to: {eql: expected => assert.deepEqual(actual, expected)}}),
    test: (name, callback) => {
      try { callback(); assertions.push({name, passed: true}); }
      catch (error) { assertions.push({name, passed: false, error: error.message}); }
    },
    sendRequest: (request, callback) => {
      const pathname = new URL(request.url).pathname;
      let route;
      if (pathname === '/admin-api/aether/identity/self') route = 'auth-self';
      else if (pathname === '/p3/auth/me') route = 'p3-self';
      else if (pathname === '/p3/remember') route = ++saves === 1 ? 'save' : 'replay';
      else if (pathname === '/p3/remember/body') route = ++reads === 1 ? 'read' : 'deleted';
      else if (pathname === '/p3/recall') route = 'recall';
      else if (pathname === '/p3/remember/memory-a') route = 'snapshot';
      else if (pathname === '/p3/remember/memory-a/delete') route = 'delete';
      else if (pathname === '/admin-api/system/auth/logout') route = 'logout';
      else if (pathname.startsWith('/p3/operations/')) route = 'poll';
      else throw new Error('Unexpected synthetic route: ' + pathname);
      requests.push({route, pathname, method: request.method, headers: clone(request.header)});
      if (route === expireAfter) now += 26000;
      if (route === 'save' && pollBodies) {
        return callback(null, response({code: 'REQUEST_IN_PROGRESS', job_id: 'job-a'}, 202));
      }
      if (route === 'poll') {
        const next = pollBodies[polls++];
        if (!next) throw new Error('Unexpected extra poll');
        return callback(null, response(next === 'success' ? bodies.save : next,
          next === 'success' ? 200 : 202));
      }
      callback(null, response(bodies[route]));
    }
  };
  const context = {pm, Date: Clock, Promise, Number, Math, Error,
    setTimeout: callback => callback(), console: {log: message => logs.push(message)}};
  vm.createContext(context);
  await Promise.resolve(vm.runInContext(script, context, {timeout: 1000}));
  return {assertions, failures: assertions.filter(a => !a.passed), requests, variables, logs};
}

function passed(result, name) {
  assert.ok(result.assertions.some(a => a.name === name && a.passed), 'missing passing assertion: ' + name);
}
function rejected(result, name) {
  assert.ok(result.failures.some(a => a.name === name), 'invalid evidence was accepted: ' + name);
}

test('valid independent journey completes and retains pending physical-cleanup evidence', async () => {
  const result = await execute();
  assert.deepEqual(result.failures, []);
  passed(result, '删除后因删除状态排除正文');
  passed(result, '清理登录令牌');
  assert.equal(result.variables.get('cleanup_state'), 'pending');
  assert.equal(JSON.parse(result.variables.get('cleanup_memory_ref')).memory_id, 'memory-a');
  assert.equal(result.requests.at(-1).route, 'logout');
  assert.ok(!result.logs.join('\n').includes(TOKEN));
});

for (const [key, value] of [['user_id', 'wrong-user'], ['tenant_id', 'wrong-tenant'],
  ['user_enabled', false], ['tenant_enabled', false]]) {
  test('auth identity rejects ' + key + ' mismatch before P3 writes', async () => {
    const result = await execute({edit: b => {b['auth-self'].data[key] = value;}});
    rejected(result, '身份与测试租户一致');
    assert.ok(!result.requests.some(r => r.route === 'save'));
    passed(result, '清理登录令牌');
  });
}
for (const key of ['user_id', 'tenant_id']) {
  test('P3 identity rejects ' + key + ' mismatch before writes', async () => {
    const result = await execute({edit: b => {b['p3-self'].scope[key] = 'foreign';}});
    rejected(result, 'P3映射到预期业务身份');
    assert.ok(!result.requests.some(r => r.route === 'save'));
  });
}

for (const key of ['tenant_id', 'application_id', 'user_id', 'agent_id', 'session_id', 'task_id']) {
  test('new MemoryRef rejects foreign ' + key, async () => {
    const result = await execute({edit: b => {b.save.memories[0].scope[key] = 'foreign';}});
    rejected(result, '新记忆归属当前身份与本次会话');
    assert.ok(!result.requests.some(r => r.route === 'read'));
  });
}

for (const [route, field, name] of [
  ['read', 'memory', '权威正文完整且引用来源一致'],
  ['replay', 'memories', '同操作ID重试不产生新记忆'],
  ['snapshot', 'ref', '删除前取得实际对象版本']
]) {
  test(route + ' rejects a foreign memory reference', async () => {
    const result = await execute({edit: b => {
      const memory = field === 'memories' ? b[route][field][0] : b[route][field];
      memory.scope.tenant_id = 'foreign';
    }});
    rejected(result, name);
  });
}

for (const key of ['source_id', 'source_version', 'content_hash', 'locator']) {
  test('body read rejects mismatched source ' + key, async () => {
    const result = await execute({edit: b => {b.read.sources[0][key] = key === 'source_version' ? 2 : 'foreign';}});
    rejected(result, '权威正文完整且引用来源一致');
  });
}
test('body read rejects an unrelated source beside the expected source', async () => {
  const result = await execute({edit: b => {
    const unrelated = clone(b.read.sources[0]);
    unrelated.source_id = 'unrelated-source';
    b.read.sources.push(unrelated);
  }});
  rejected(result, '权威正文完整且引用来源一致');
});

for (const [name, edit] of [
  ['pack scope', b => {b.recall.scope.tenant_id = 'foreign';}],
  ['item scope', b => {b.recall.groups[0].items[0].memory.scope.user_id = 'foreign';}],
  ['item version', b => {b.recall.groups[0].items[0].memory.version = 2;}],
  ['source identity', b => {b.recall.groups[0].items[0].sources[0].source_id = 'foreign';}]
]) {
  test('Recall rejects mismatched ' + name, async () => {
    rejected(await execute({edit}), 'Recall包含正确版本与来源');
  });
}

test('Recall rejects an additional foreign-tenant item beside the matching memory', async () => {
  const result = await execute({edit: b => {
    const leaked = clone(b.recall.groups[0].items[0]);
    leaked.memory.memory_id = 'foreign-memory';
    leaked.memory.scope.tenant_id = 'foreign-tenant';
    b.recall.groups[0].items.push(leaked);
  }});
  assert.ok(result.failures.length > 0, 'foreign-tenant ContextPack item was accepted');
});
test('Recall rejects an unrelated source mixed with the expected source', async () => {
  const result = await execute({edit: b => {
    const unrelated = clone(b.recall.groups[0].items[0].sources[0]);
    unrelated.source_id = 'unrelated-source';
    b.recall.groups[0].items[0].sources.push(unrelated);
  }});
  assert.ok(result.failures.length > 0, 'unrelated ContextPack source was accepted');
});

for (const outcome of ['unavailable', 'stale', 'missing']) {
  test('deletion cannot pass when subsequent body outcome is ' + outcome, async () => {
    const result = await execute({edit: b => {b.deleted.outcome = outcome;}});
    rejected(result, '删除后因删除状态排除正文');
    assert.ok(result.variables.has('cleanup_memory_ref'));
    assert.notEqual(result.variables.get('cleanup_state'), 'completed');
  });
}
test('deletion rejects exclusion unrelated to deletion', async () => {
  rejected(await execute({edit: b => {b.deleted.reason_code = 'permission_denied';}}), '删除后因删除状态排除正文');
});

test('successful response on the last allowed poll completes the journey', async () => {
  const result = await execute({limits: {max_poll_attempts: '1'}, pollBodies: ['success']});
  assert.deepEqual(result.failures, []);
  assert.equal(result.requests.filter(r => r.route === 'poll').length, 1);
  passed(result, '删除后因删除状态排除正文');
});
test('changed pending job ID is rejected without following the replacement job', async () => {
  const result = await execute({pollBodies: [{code: 'REQUEST_IN_PROGRESS', job_id: 'job-b'}]});
  assert.ok(result.failures.some(a => a.error.includes('original job ID changed')));
  assert.deepEqual(result.requests.filter(r => r.route === 'poll').map(r => r.pathname), ['/p3/operations/job-a/result']);
  assert.equal(result.variables.get('original_job_id'), 'job-a');
  passed(result, '清理登录令牌');
});

test('request limit reserves logout without exceeding the total allowance', async () => {
  const result = await execute({limits: {max_requests: '4'}});
  assert.ok(result.failures.some(a => a.error.includes('budget exhausted')));
  assert.deepEqual(result.requests.map(r => r.route), ['auth-self', 'p3-self', 'logout']);
  assert.equal(result.requests.length + 1, 4); // Includes the initial login response.
  passed(result, '清理登录令牌');
});
test('business deadline stops writes but does not consume the reserved logout', async () => {
  const result = await execute({expireAfter: 'p3-self'});
  assert.ok(result.failures.some(a => a.error.includes('budget exhausted')));
  assert.ok(!result.requests.some(r => r.route === 'save'));
  assert.equal(result.requests.at(-1).route, 'logout');
  passed(result, '清理登录令牌');
});
