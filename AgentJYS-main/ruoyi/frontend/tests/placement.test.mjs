import test from 'node:test'
import assert from 'node:assert/strict'
import {
  tierText,
  decisionText,
  resultText,
  triggerText,
  durationText
} from '../src/views/aether/placement.mjs'

test('tier monitoring explains policy and event separately in Chinese', () => {
  assert.equal(triggerText('periodic'), '到期自动检查')
  assert.match(
    decisionText({ decision_reason: 'heat_policy', heat: 0.9, desired_tier: 'hot' }),
    /0.900000.*热层/
  )
  assert.match(decisionText({ decision_reason: 'successful_read' }), /成功读取/)
  assert.match(triggerText(null), /未记录/)
  assert.match(decisionText({}), /未记录/)
})

test('simulation and missing confirmation never look like a successful real migration', () => {
  assert.equal(resultText('succeeded'), '成功，已验证可读')
  assert.equal(resultText('simulated'), '模拟完成')
  assert.equal(resultText('unconfirmed'), '结果待确认')
  assert.equal(durationText(null), '未取得完成回执')
  assert.equal(durationText(0), '0 秒')
})


test('two-tier placement describes replicas and preserves historical warm labels', () => {
  assert.equal(tierText('cold'), '冷层（Ceph 原文）')
  assert.equal(tierText('hot'), '热层（Redis 副本）')
  assert.match(tierText('warm'), /历史记录/)
  assert.match(decisionText({ decision_reason: 'create_hot_replica' }), /保留 Ceph 原文.*Redis 热副本/)
  assert.match(decisionText({ decision_reason: 'remove_hot_replica' }), /核验 Ceph 原文可读后移除/)
  assert.match(decisionText({ decision_reason: 'heat_policy', policy_version: 'ceph_redis_heat_v2', heat: 0.4, desired_tier: 'cold' }), /核验 Ceph 原文可读后移除 Redis 副本/)
  assert.match(decisionText({ decision_reason: 'heat_policy', policy_version: 'continuous_heat_v1', heat: 0.4, desired_tier: 'warm' }), /旧版三层策略的历史动作/)
})
