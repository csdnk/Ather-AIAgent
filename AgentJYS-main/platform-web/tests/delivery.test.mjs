import test from 'node:test';
import assert from 'node:assert/strict';
import {submission, pollDelay, nextUpdate, replyPhase} from '../src/delivery.mjs';

test('ambiguous delivery retries with its original stable identifier', () => {
  const first = submission(null, 'conversation-1', 'hello', null, () => 'turn-1');
  const retry = submission(first, 'conversation-1', 'hello', null, () => 'turn-2');
  assert.equal(retry.turnId, 'turn-1');
  assert.throws(() => submission(first, 'conversation-2', 'hello', null, () => 'turn-2'));
});

test('first text refreshes promptly while memory persistence has its own slower cadence', () => {
  assert.equal(nextUpdate([{status:'pending',phase:'generating',content:'首字'}]),250);
  assert.equal(nextUpdate([{status:'pending',phase:'retrieving'}]),750);
  assert.equal(nextUpdate([{status:'complete',memory_status:'saving'}]),2000);
  assert.equal(nextUpdate([{status:'complete',memory_status:'saved'}]),null);
  assert.equal(replyPhase({phase:'retrieving'}),'正在检索记忆…');
  assert.equal(replyPhase({phase:'generating',content:'首字'}),'正在生成…');
});

test('transient poll errors retry while terminal authentication errors stop', () => {
  assert.equal(pollDelay({status:503}, 1), 1000);
  assert.equal(pollDelay(new Error('network disconnected'), 2), 2000);
  assert.equal(pollDelay({status:401}, 1), null);
  assert.equal(pollDelay({status:403}, 1), null);
  assert.equal(pollDelay({status:503}, 6), null);
});
