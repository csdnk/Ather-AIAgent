// Pure VM tests of the generated native HTTP step configuration.
// Real full-stack/performance admission constants remain false and are tested.
// Full-stack response tests below are explicitly POST-ONLY unit simulations:
// they seed synthetic state, never run full-stack preconditions, and do not
// establish environment admission or end-to-end execution readiness.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {execFileSync} = require('node:child_process');

const directory = path.resolve(__dirname, '../../scripts/quality');
const engine = fs.readFileSync(path.join(directory, 'apifox-journey-step.js'), 'utf8');
const definitions = JSON.parse(execFileSync('python', ['-B', '-c',
  'import importlib.util,json,sys; s=importlib.util.spec_from_file_location("journeys",sys.argv[1]); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print(json.dumps(m.definitions()))',
  path.join(directory, 'apifox_journeys.py')], {encoding: 'utf8'}));
const clone = value => JSON.parse(JSON.stringify(value));
const flow = name => definitions.find(f => f.key === name);
const RUN = 'synthetic-journey-1';
const home = {tenant_id: 'tenant-a', user_id: 'user-a', application_id: 'app-a',
  agent_id: 'agent-a', session_id: null, task_id: null};
const accounts = {aetherusera: ['50004', '501'], aetherusera2: ['50005', '501'], aetheruserb: ['50006', '502']};

function createHarness(selected, options = {}) {
  const variables = new Map();
  const stateKey = 'aether_journey_' + selected.key;
  const env = {environment_kind: selected.environment, max_requests: '40',
    base_url: 'http://127.0.0.1:14880', auth_base: 'http://127.0.0.1:14880',
    p3_base: 'http://127.0.0.1:14881', public_origin: selected.stages[0].publicOrigin,
    expected_user_id: '50004', expected_tenant_id: '501', expected_p3_user_id: 'user-a',
    expected_p3_tenant_id: 'tenant-a', test_username: 'aetherusera', test_password: 'synthetic-password',
    aetherusera_password: 'synthetic-password', aetherusera2_password: 'synthetic-password',
    aetheruserb_password: 'synthetic-password', ...options.env};
  const assertions = [], requests = [], logs = [];
  let now = Date.parse('2026-10-09T00:00:00.000Z');
  class Clock extends Date {
    constructor(...args) {super(...(args.length ? args : [now]));}
    static now() {return now;}
  }
  const state = () => JSON.parse(variables.get(stateKey) || '{}');
  const seed = value => variables.set(stateKey, JSON.stringify(value));
  function phase(cfg, phaseName, outgoing, response = {code: 200, body: {}}) {
    let skipped = false;
    const pm = {
      environment: {get: name => env[name]},
      variables: {get: name => variables.has(name) ? variables.get(name) : options.data?.[name], set: (name, value) => variables.set(name, value),
        replaceIn: () => RUN},
      request: {url: {update: value => {outgoing.url = value;}},
        body: {update: value => {outgoing.body = JSON.parse(value);}},
        headers: {upsert: ({key, value}) => {outgoing.headers[key] = value;}}},
      response: {code: response.code, json: () => clone(response.body), responseTime: 3,
        headers: {get: key => response.headers?.[key] || null}},
      execution: {skipRequest: () => {skipped = true;}},
      expect: value => ({to: {eql: expected => assert.deepEqual(value, expected)}}),
      test: (name, callback) => {
        try {callback(); assertions.push({step: cfg.index, name, passed: true});}
        catch (error) {assertions.push({step: cfg.index, name, passed: false, error: error.message});}
      },
      sendRequest: () => {throw new Error('Hidden HTTP subrequest is forbidden');}
    };
    let error;
    try {
      vm.runInNewContext(engine, {pm, cfg: clone(cfg), phase: phaseName, Date: Clock,
        console: {log: value => logs.push(value)}}, {timeout: 1000});
    } catch (caught) {error = caught;}
    return {skipped, error, outgoing};
  }
  function responseFor(cfg, outgoing) {
    if (cfg.action === 'login') {
      const [id] = accounts[outgoing.body.username];
      return {code: 200, body: {code: 0, data: {userId: Number(id), accessToken: 'synthetic-token-' + id}}};
    }
    if (cfg.action === 'identity') {
      const id = outgoing.headers.Authorization.replace('Bearer synthetic-token-', '');
      const tenant = Object.values(accounts).find(a => a[0] === id)[1];
      return {code: 200, body: {code: 0, data: {user_id: id, tenant_id: tenant,
        user_enabled: true, tenant_enabled: true, role_codes: ['aether_user']}}};
    }
    if (cfg.action === 'logout') return {code: 200, body: {code: 0, data: true}};
    if (cfg.action === 'denied') return {code: cfg.httpCode, body: {code: cfg.businessCode, data: null}};
    if (cfg.action === 'http401') return {code: 401, body: {detail: 'Authentication required'}};
    if (cfg.action === 'health') return {code: 200, body: {[cfg.field]: cfg.value}};
    throw new Error('Unexpected actual request in synthetic admitted flow: ' + cfg.action);
  }
  function step(cfg) {
    const outgoing = {url: 'http://127.0.0.1:1/BLOCKED', headers: {}};
    const pre = phase(cfg, 'pre', outgoing);
    if (pre.skipped || pre.error) return pre;
    requests.push({cfg, ...clone(outgoing)});
    const response = responseFor(cfg, outgoing);
    options.editResponse?.(cfg, response, outgoing);
    return phase(cfg, 'post', outgoing, response);
  }
  function postOnly(cfg, body, code = 200) {
    // No pre execution: synthetic response semantics only, not gate bypass.
    const current = state(); current.requests++; seed(current);
    return phase(cfg, 'post', {url: 'http://127.0.0.1:1/NOT-SENT', headers: {}}, {code, body});
  }
  return {selected, variables, state, seed, env, assertions, requests, logs,
    phase, step, postOnly, advance: ms => {now += ms;},
    run: () => selected.stages.forEach(step), failures: () => assertions.filter(a => !a.passed)};
}

test('real dataset initializes its own fact, source and account before the closed admission gate', () => {
  const selected=flow('memory-real-data');
  assert.ok(selected, 'real-data journey required');
  const data={dataset_id:'nasa-test-b',dataset_kind:'real_public_source',tenant_account:'aetheruserb',text:'NASA T2M 2.22 C',question:'What is T2M?',expected_fact:'2.22 C',source_url:'https://power.larc.nasa.gov/',source_sha256:'a'.repeat(64),expected_behavior:'save_read_recall_same_source'};
  const h=createHarness(selected,{data}); h.step(selected.stages[0]);
  assert.equal(h.state().text,data.text); assert.equal(h.state().fact,data.expected_fact);
  assert.equal(h.state().account,'aetheruserb'); assert.equal(h.state().question,data.question);
  assert.equal(h.state().dataset.source_sha256,data.source_sha256);
  assert.equal(h.requests.length,0); // no admission bypass
  const failure=JSON.parse(h.logs.find(x=>x.startsWith('AETHER_JOURNEY_FAILURE ')).slice('AETHER_JOURNEY_FAILURE '.length));
  assert.equal(failure.dataset.id,data.dataset_id);
  assert.equal(failure.account,data.tenant_account);
  assert.ok(!h.logs.join('').includes(data.text));
});
test('real-data journey refuses absent data instead of silently testing the synthetic 17-day fact', () => {
  const selected=flow('memory-real-data'); assert.ok(selected);
  const h=createHarness(selected); h.step(selected.stages[0]);
  assert.ok(h.failures().some(a=>a.name.includes('真实数据')));
  assert.equal(h.requests.length,0);
});

for (const name of ['aetherusera', 'aetherusera2', 'aetheruserb']) {
  test(name + ': five visible steps preserve identity, reject access and verify revocation', () => {
    const h = createHarness(flow(name)); h.run();
    assert.deepEqual(h.failures(), []);
    assert.deepEqual(h.requests.map(r => r.cfg.action), ['login', 'identity', 'denied', 'logout', 'denied']);
    const bearer = 'Bearer synthetic-token-' + accounts[name][0];
    assert.ok(h.requests.slice(1).every(r => r.headers.Authorization === bearer));
    assert.equal(h.requests.at(-1).cfg.businessCode, 401);
    assert.equal(h.state().done, 4);
    assert.deepEqual(h.state().tokens, {});
    assert.ok(h.logs.some(line => line.startsWith('AETHER_JOURNEY ')));
    assert.ok(!h.logs.join('\n').includes('synthetic-token'));
  });
}
test('negative login sequence sends every literal boundary payload then revokes its valid token', () => {
  const h = createHarness(flow('identity-input-boundaries')); h.run();
  assert.deepEqual(h.failures(), []);
  const invalid = h.requests.filter(r => r.cfg.payload);
  assert.ok(invalid.length >= 10);
  for (const request of invalid) assert.deepEqual(request.body, request.cfg.payload);
  assert.deepEqual(h.state().tokens, {});
});
test('forged claims remain separate from the valid token and invalid authorization is sent literally', () => {
  const h = createHarness(flow('identity-forged-claims')); h.run();
  assert.deepEqual(h.failures(), []);
  const custom = h.requests.filter(r => r.cfg.headers);
  assert.ok(custom.length >= 2);
  for (const request of custom) for (const [key, value] of Object.entries(request.cfg.headers)) {
    assert.equal(request.headers[key], value);
  }
  assert.equal(h.requests.at(-2).headers.Authorization, 'Bearer synthetic-token-50004');
});
test('negative login must fail if it issues a credential', () => {
  const h = createHarness(flow('identity-input-boundaries'), {editResponse: (cfg, response) => {
    if (cfg.payload) response.body.data = {accessToken: 'synthetic-unexpected-token'};
  }}); h.run();
  assert.ok(h.failures().some(a => a.name === '精确拒绝访问且不泄露数据'));
});
test('forged role headers cannot silently add an administrator role', () => {
  const h = createHarness(flow('identity-forged-claims'), {editResponse: (cfg, response) => {
    if (cfg.action === 'identity' && cfg.headers) response.body.data.role_codes.push('aether_platform_admin');
  }}); h.run();
  assert.ok(h.failures().some(a => a.name === '伪造声明不能改变实际角色集合'));
});
test('two-tenant sequence preserves separate A and B tokens through visible logout checks', () => {
  const h = createHarness(flow('tenant-switch')); h.run();
  assert.deepEqual(h.failures(), []);
  assert.equal(h.requests.length, 9);
  assert.equal(h.requests[4].headers.Authorization, 'Bearer synthetic-token-50004');
  assert.equal(h.requests[5].headers.Authorization, 'Bearer synthetic-token-50006');
  assert.equal(h.requests[7].headers.Authorization, 'Bearer synthetic-token-50004');
});
test('public sequence uses exactly the approved origin and sends no credential', () => {
  const h = createHarness(flow('public-chain')); h.run();
  assert.deepEqual(h.failures(), []);
  assert.equal(h.requests.length, 3);
  assert.ok(h.requests.every(r => r.url.startsWith(h.env.public_origin + '/')));
  assert.ok(h.requests.every(r => !r.headers.Authorization && !r.body));
});
test('public sequence blocks an unapproved origin before any request', () => {
  const h = createHarness(flow('public-chain'), {env: {public_origin: 'https://unapproved.invalid'}}); h.run();
  assert.equal(h.requests.length, 0);
  assert.ok(h.failures().length > 0);
});
test('public anonymous check rejects a response containing a credential', () => {
  const h = createHarness(flow('public-chain'), {editResponse: (cfg, response) => {
    if (cfg.action === 'http401') response.body.accessToken = 'synthetic-leaked-token';
  }}); h.run();
  assert.ok(h.failures().some(a => a.name === '匿名 HTTP 401 且不返回令牌'));
  assert.equal(h.requests.length, 2);
});
test('internal visible health and anonymous-authentication steps complete independently', () => {
  const h = createHarness(flow('internal-chain')); h.run();
  assert.deepEqual(h.failures(), []);
  assert.equal(h.requests.length, 3);
});
test('wrong environment cannot activate identity requests or transmit a password', () => {
  const h = createHarness(flow('aetherusera'), {env: {environment_kind: 'public_readonly'}}); h.run();
  assert.equal(h.requests.length, 0);
});
test('starting at identity or skipping an intermediate step cannot continue business requests', () => {
  const a = createHarness(flow('aetherusera')); a.step(a.selected.stages[1]);
  assert.equal(a.requests.length, 0);
  const b = createHarness(flow('aetherusera')); b.step(b.selected.stages[0]); b.step(b.selected.stages[2]);
  assert.equal(b.requests.length, 1);
  assert.ok(b.failures().length > 0);
});
test('failed identity stops business steps but still sends visible logout with the issued token', () => {
  const h = createHarness(flow('aetherusera'), {editResponse: (cfg, response) => {
    if (cfg.action === 'identity') response.body.data.tenant_id = 'wrong-tenant';
  }}); h.run();
  assert.deepEqual(h.requests.map(r => r.cfg.action), ['login', 'identity', 'logout']);
  assert.equal(h.requests.at(-1).headers.Authorization, 'Bearer synthetic-token-50004');
  assert.equal(h.state().failed, true);
});
test('a wrong login identity still preserves the issued token for cleanup', () => {
  const h = createHarness(flow('aetherusera'), {editResponse: (cfg, response) => {
    if (cfg.action === 'login') response.body.data.userId = 99999;
  }}); h.run();
  assert.deepEqual(h.requests.map(r => r.cfg.action), ['login', 'logout']);
  assert.equal(h.requests.at(-1).headers.Authorization, 'Bearer synthetic-token-50004');
  assert.equal(h.state().failed, true);
});
test('missing login password sends no request and does not fabricate a cleanup token', () => {
  const h = createHarness(flow('aetherusera'), {env: {aetherusera_password: ''}}); h.run();
  assert.equal(h.requests.length, 0);
  assert.deepEqual(h.state().tokens, {});
});
test('business deadline does not prevent visible logout after an earlier login', () => {
  const h = createHarness(flow('aetherusera')); h.step(h.selected.stages[0]); h.advance(61000);
  h.selected.stages.slice(1).forEach(h.step);
  assert.deepEqual(h.requests.map(r => r.cfg.action), ['login', 'logout']);
});
test('a five-request budget permits the complete five-step identity scenario', () => {
  const h = createHarness(flow('aetherusera'), {env: {max_requests: '5'}}); h.run();
  assert.deepEqual(h.failures(), []);
  assert.equal(h.requests.length, 5);
});
for (const name of ['memory-lifecycle', 'memory-correct', 'perf-internal-chain', 'perf-aetherusera', 'perf-memory-lifecycle']) {
  test(name + ': immutable admission gate blocks all visible requests even with environment flags', () => {
    const h = createHarness(flow(name), {env: {allow_writes: 'true', isolation_verified: 'true',
      performance_enabled: 'true', max_model_cost: '100', max_resource_cost: '100'}}); h.run();
    assert.equal(h.requests.length, 0);
    assert.ok(h.failures().some(a => /G0|性能/.test(a.name)));
  });
}

function responseHarness(name = 'memory-lifecycle') {
  const h = createHarness(flow(name));
  h.seed({run: RUN, started: Date.parse('2026-10-09T00:00:00.000Z'), done: 2, requests: 3,
    tokens: {A: 'synthetic-token-50004'}, home: clone(home), completed: {}, jobs: {},
    timings: {}, failed: false, fact: 17, text: 'Synthetic fact: exactly 17 days.'});
  return h;
}
function saved(kind = 'save', version = 1) {
  return {saved: true, operation_id: (kind === 'replay' ? 'save' : kind) + '_' + RUN,
    phase: 'ready', task_ids: [], memories: [{memory_id: 'memory-a', version,
      scope: {...home, session_id: RUN}}],
    source: {source_id: 'source-a', source_version: version, content_hash: 'a'.repeat(64), locator: 'source:memory-a'}};
}
function stage(h, action, predicate = () => true) {return h.selected.stages.find(s => s.action === action && predicate(s));}
function seedSaved(h) {h.postOnly(stage(h, 'save'), saved()); assert.deepEqual(h.failures(), []);}
function pack(h) {
  const s = h.state();
  return {scope: clone(s.memory.scope), outcome: 'available', tokens_used: 10, token_budget: 1024,
    coverage: {working: 'complete'}, groups: [{items: [{memory: clone(s.memory), content: '17 days', sources: [clone(s.source)]}]}]};
}
test('POST-ONLY helpers validate save, replay, body, recall and exact logical deletion', () => {
  const h = responseHarness(); seedSaved(h);
  h.postOnly(stage(h, 'replay'), saved('replay'));
  const s = h.state();
  h.postOnly(stage(h, 'body'), {outcome: 'read', content: s.text, memory: s.memory, sources: [s.source]});
  h.postOnly(stage(h, 'recall'), pack(h));
  h.postOnly(stage(h, 'snapshot'), {ref: s.memory, object_revision: 7});
  h.postOnly(stage(h, 'delete'), {blocked: true, operation_id: 'delete_' + RUN, cleanup_state: 'pending'});
  h.postOnly(stage(h, 'deleted'), {outcome: 'excluded', reason_code: 'deleted', content: null, memory: s.memory});
  h.postOnly(stage(h, 'recall_deleted'), {scope: s.memory.scope, outcome: 'empty', tokens_used: 0,
    token_budget: 1024, coverage: {working: 'complete'}, groups: []});
  assert.deepEqual(h.failures(), []);
  assert.equal(h.state().cleanup, 'pending');
  assert.equal(h.requests.length, 0); // These are deliberately NOT admitted pre/post journeys.
});
test('POST-ONLY correction requires the same object and exactly the next version', () => {
  const good = responseHarness('memory-correct'); seedSaved(good);
  good.postOnly(stage(good, 'correct'), saved('correct', 2));
  assert.deepEqual(good.failures(), []);
  assert.equal(good.state().memory.version, 2);
  const bad = responseHarness('memory-correct'); seedSaved(bad);
  bad.postOnly(stage(bad, 'correct'), saved('correct', 3));
  assert.ok(bad.failures().some(a => a.name === '更正只递增同一对象版本'));
});
for (const field of ['tenant_id', 'user_id', 'application_id', 'agent_id', 'session_id', 'task_id']) {
  test('POST-ONLY save rejects foreign ' + field, () => {
    const h = responseHarness(), receipt = saved(); receipt.memories[0].scope[field] = 'foreign';
    h.postOnly(stage(h, 'save'), receipt);
    assert.ok(h.failures().length > 0);
  });
}
for (const extra of ['foreign-memory', 'foreign-source']) {
  test('POST-ONLY Recall rejects correct evidence accompanied by ' + extra, () => {
    const h = responseHarness(); seedSaved(h); const value = pack(h);
    if (extra === 'foreign-memory') {
      const leaked = clone(value.groups[0].items[0]); leaked.memory.scope.tenant_id = 'foreign';
      value.groups[0].items.push(leaked);
    } else {
      const unrelated = clone(value.groups[0].items[0].sources[0]); unrelated.source_id = 'foreign';
      value.groups[0].items[0].sources.push(unrelated);
    }
    h.postOnly(stage(h, 'recall'), value);
    assert.ok(h.failures().some(a => a.name === '召回只含当前版本与真实来源'));
  });
}
test('POST-ONLY deletion rejects unavailable even though content is null', () => {
  const h = responseHarness(); seedSaved(h);
  h.postOnly(stage(h, 'deleted'), {memory: h.state().memory, outcome: 'unavailable', reason_code: 'deleted', content: null});
  assert.ok(h.failures().some(a => a.name === '删除后正文因 deleted 被排除'));
});
test('POST-ONLY third and final poll accepts a completed original operation', () => {
  const h = responseHarness();
  h.postOnly(stage(h, 'save'), {code: 'REQUEST_IN_PROGRESS', job_id: 'job-a'}, 400);
  const polls = h.selected.stages.filter(s => s.action === 'poll' && s.kind === 'save');
  for (const cfg of polls.slice(0, -1)) h.postOnly(cfg, {code: 'REQUEST_IN_PROGRESS', job_id: 'job-a'}, 400);
  h.postOnly(polls.at(-1), saved());
  assert.deepEqual(h.failures(), []);
  assert.equal(h.state().completed.save, true);
  assert.equal(h.state().jobs.save, 'job-a');
});
test('POST-ONLY poll rejects a replacement job ID and preserves original evidence', () => {
  const h = responseHarness();
  h.postOnly(stage(h, 'save'), {code: 'REQUEST_IN_PROGRESS', job_id: 'job-a'}, 400);
  h.postOnly(stage(h, 'poll', s => s.kind === 'save'), {code: 'REQUEST_IN_PROGRESS', job_id: 'job-b'}, 400);
  assert.ok(h.failures().some(a => a.name === '原任务身份不可改变'));
  assert.equal(h.state().jobs.save, 'job-a');
});
test('POST-ONLY final pending poll fails instead of claiming asynchronous completion', () => {
  const h = responseHarness();
  h.postOnly(stage(h, 'save'), {code: 'REQUEST_IN_PROGRESS', job_id: 'job-a'}, 400);
  h.postOnly(stage(h, 'poll', s => s.kind === 'save' && s.finalPoll),
    {code: 'REQUEST_IN_PROGRESS', job_id: 'job-a'}, 400);
  assert.ok(h.failures().some(a => a.name === '业务必须在本轮观察窗口完成'));
  assert.notEqual(h.state().completed.save, true);
  assert.equal(h.state().jobs.save, 'job-a');
});
