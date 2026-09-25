"""Executable regression tests, kept inside Operate to honor the change boundary."""

import asyncio
import unittest
from dataclasses import replace

from .controller import Controller
from .demo import add, build, burst
from .mock_p2 import MockP2
from .models import Feedback, MemoryKey
from .policy import Settings, evaluate
from .runner import ManualClock, settle


class OperateTests(unittest.IsolatedAsyncioTestCase):
    async def test_queries_never_advance_execution(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await c.tick()
        action = c.store.pending[key].intent
        for _ in range(5):
            self.assertEqual((await p.query(action.action_id)).state, "running")
            self.assertFalse((await p.observe(key)).hot)
        self.assertEqual(p.effects, 0)
        p.advance()
        clock.advance(1)
        await settle(c, p, clock)
        self.assertTrue((await p.observe(key)).hot)
        self.assertEqual((await p.observe(key)).base, "cold")

    async def test_warm_base_is_retained_on_promotion(self):
        c, p, clock = build()
        key = add(c, p, base="warm")
        burst(c, key)
        await settle(c, p, clock)
        self.assertEqual(p.objects[key].base, "warm")
        self.assertIsNotNone(p.objects[key].hot_content)

    async def test_lost_submit_response_recovers_original_action(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        p.drop_next_response = True
        await c.tick()
        original = c.store.pending[key].intent.action_id
        await settle(c, p, clock)
        self.assertEqual(list(p.actions), [original])
        self.assertEqual(p.effects, 1)
        self.assertNotIn(key, c.store.pending)

    async def test_missing_and_unknown_placement_never_create_action(self):
        for missing in (False, True):
            c, p, clock = build()
            key = add(c, p)
            burst(c, key)
            if missing:
                del p.objects[key]
            else:
                p.unknown_keys.add(key)
            await c.tick()
            self.assertFalse(p.actions)
            self.assertIn(key, c.store.timers)

    async def test_resource_outage_recovers(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        p.resources_unavailable = True
        await c.tick()
        self.assertFalse(p.actions)
        p.resources_unavailable = False
        clock.advance(1)
        await settle(c, p, clock)
        self.assertIsNotNone(p.objects[key].hot_content)

    async def test_capacity_shortage_then_release(self):
        c, p, clock = build(hot_capacity=0)
        key = add(c, p)
        burst(c, key)
        await c.tick()
        self.assertFalse(p.actions)
        self.assertEqual(c.store.stats[key].desired, "hot")
        p.capacities["hot"] = 10000
        clock.advance(1)
        await settle(c, p, clock)
        self.assertTrue((await p.observe(key)).hot)

    async def test_reservations_prevent_capacity_oversubscription(self):
        c, p, _ = build()
        key = add(c, p)
        other = add(c, p, "M2")
        p.capacities["hot"] = c.store.stats[key].memory.size
        burst(c, key)
        burst(c, other)
        await c.tick()
        self.assertEqual(sum(a.feedback.state == "running" for a in p.actions.values()), 1)
        self.assertEqual((await p.resources())["hot"], 0)

    async def test_new_access_waits_for_original_plan(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key, count=1)
        await c.tick()
        original = c.store.pending[key].intent.action_id
        burst(c, key, prefix="new")
        await c.tick()
        self.assertEqual(len(p.actions), 1)
        self.assertEqual(c.store.pending[key].intent.action_id, original)
        await settle(c, p, clock)
        self.assertEqual(len(p.actions), 2)
        self.assertEqual(p.objects[key].base, "warm")
        self.assertIsNotNone(p.objects[key].hot_content)
        self.assertEqual(c.store.stats[key].access_count, 13)

    async def test_automatic_cooling_without_access(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await settle(c, p, clock)
        clock.advance(600)
        await settle(c, p, clock)
        self.assertEqual(c.store.stats[key].access_count, 12)
        self.assertIsNone(p.objects[key].hot_content)
        self.assertEqual(p.objects[key].base, "cold")

    async def test_eviction_retains_stats_and_wakes_cooling(self):
        c, p, clock = build(small_buffer=True)
        key = add(c, p)
        burst(c, key)
        await settle(c, p, clock)
        clock.advance(600)
        for i in range(2, 6):
            other = add(c, p, f"M{i}")
            c.access(other, str(i))
        self.assertNotIn(key, c.store.buffer)
        self.assertIn(key, c.store.stats)
        self.assertIn(key, c.store.ready)
        self.assertLess(len(c.store.buffer), c.settings.buffer_limit)
        await settle(c, p, clock)
        self.assertIsNone(p.objects[key].hot_content)
        self.assertFalse(c.access(key, "access-0"))
        c.access(key, "return")
        self.assertEqual(c.store.stats[key].access_count, 13)

    async def test_dedup_and_non_context_and_delayed_events(self):
        c, p, clock = build()
        key = add(c, p)
        self.assertFalse(c.access(key, "retrieved", entered_context=False))
        self.assertTrue(c.access(key, "one"))
        self.assertFalse(c.access(key, "one"))
        clock.advance(60)
        c.access(key, "delayed", event_at=0)
        self.assertLess(c.store.stats[key].access_sum, 1)
        self.assertEqual(c.store.stats[key].access_count, 2)
        self.assertFalse(c.access(key, "ancient", event_at=-100000))

    async def test_tenant_and_application_isolation(self):
        c, p, clock = build()
        keys = [
            MemoryKey("T1", "M1"),
            MemoryKey("T2", "M1"),
            MemoryKey("T1", "M1", application="other"),
        ]
        for key in keys:
            c.register(p.seed(key, "content"))
        burst(c, keys[0])
        await settle(c, p, clock)
        self.assertEqual([p.objects[k].hot_content is not None for k in keys], [True, False, False])

    async def test_idempotency_and_conflicting_intent(self):
        c, p, _ = build()
        key = add(c, p)
        burst(c, key)
        await c.tick()
        intent = c.store.pending[key].intent
        await p.submit(intent)
        with self.assertRaises(ValueError):
            await p.submit(replace(intent, epoch=999))
        p.advance()
        await p.submit(intent)
        p.advance()
        self.assertEqual(p.effects, 1)

    async def test_failed_action_releases_reservation_then_replans(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await c.tick()
        p.fail_next_action = True
        p.advance()
        self.assertEqual((await p.resources())["hot"], p.capacities["hot"])
        clock.advance(1)
        await c.tick()
        self.assertNotIn(key, c.store.pending)
        clock.advance(1)
        await settle(c, p, clock)
        self.assertIsNotNone(p.objects[key].hot_content)
        self.assertEqual(p.effects, 1)

    async def test_action_not_found_is_not_permission_to_resubmit(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await c.tick()
        original = c.store.pending[key].intent.action_id
        del p.actions[original]
        for _ in range(3):
            clock.advance(1)
            await c.tick()
        self.assertFalse(p.actions)
        self.assertEqual(c.store.pending[key].intent.action_id, original)
        self.assertIn("not_found", c.snapshot(key)["reason"])

    async def test_query_outage_holds_original_owner(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await c.tick()
        original = c.store.pending[key].intent.action_id
        p.query_unavailable = True
        p.advance()
        clock.advance(1)
        await c.tick()
        self.assertEqual(c.store.pending[key].intent.action_id, original)
        p.query_unavailable = False
        clock.advance(1)
        await settle(c, p, clock)
        self.assertNotIn(key, c.store.pending)

    async def test_false_success_does_not_complete_plan(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await c.tick()
        intent = c.store.pending[key].intent
        p.actions[intent.action_id].feedback = Feedback(intent.action_id, "succeeded")
        clock.advance(1)
        await c.tick()
        self.assertIn(key, c.store.pending)
        self.assertFalse((await p.observe(key)).hot)

    async def test_corrupt_hot_copy_fails_actual_read_verification(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await c.tick()
        p.advance()
        p.objects[key].hot_content = b"corrupted"
        clock.advance(1)
        await c.tick()
        self.assertIn(key, c.store.pending)
        self.assertIn("verification failed", c.snapshot(key)["reason"])

    async def test_memory_revision_changes_before_execution(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await c.tick()
        c.register(p.seed(key, "new content", version=2))
        burst(c, key, prefix="v2")
        await settle(c, p, clock)
        clock.advance(1)
        await settle(c, p, clock)
        self.assertEqual(p.objects[key].memory.version, 2)
        self.assertTrue(await p.verify_read(p.objects[key].memory, "hot"))

    async def test_bounded_statistics_and_dedup_backpressure(self):
        settings = replace(Settings(), max_memories=1, dedup_limit=1)
        p = MockP2()
        c = Controller(p, ManualClock(), settings=settings)
        key = add(c, p)
        c.access(key, "one")
        with self.assertRaises(BufferError):
            c.access(key, "two")
        with self.assertRaises(BufferError):
            add(c, p, "M2")
        self.assertEqual(len(c.store.stats), 1)
        self.assertEqual(c.store.stats[key].access_count, 1)

    async def test_index_size_stays_bounded_when_timer_is_replaced(self):
        c, p, _ = build()
        key = add(c, p)
        for i in range(1000):
            c.store.schedule(key, float(i))
        self.assertLessEqual(len(c.store._heap), 34)
        self.assertEqual(len(c.store.timers), 1)

    async def test_concurrent_ticks_never_double_submit(self):
        c, p, _ = build()
        key = add(c, p)
        burst(c, key)
        await asyncio.gather(*(c.tick() for _ in range(10)))
        self.assertEqual(len(p.actions), 1)

    async def test_multiple_memories_use_worker_concurrency(self):
        class SlowP2(MockP2):
            active = 0
            peak = 0

            async def observe(self, key):
                self.active += 1
                self.peak = max(self.peak, self.active)
                await asyncio.sleep(0.001)
                self.active -= 1
                return await super().observe(key)

        p = SlowP2()
        c = Controller(p, ManualClock(), settings=replace(Settings(), workers=2))
        for name in ("M1", "M2", "M3"):
            key = add(c, p, name)
            burst(c, key)
        await c.tick()
        self.assertEqual(p.peak, 2)
        self.assertEqual(len(p.actions), 2)

    async def test_new_event_during_observation_drops_unsent_old_plan(self):
        class CallbackP2(MockP2):
            callback = None

            async def resources(self):
                if self.callback:
                    callback, self.callback = self.callback, None
                    callback()
                return await super().resources()

        p = CallbackP2()
        c = Controller(p, ManualClock())
        key = add(c, p)
        c.access(key, "first")
        p.callback = lambda: burst(c, key, prefix="during-read")
        await c.tick()
        self.assertFalse(p.actions)
        await c.tick()
        self.assertEqual(next(iter(p.actions.values())).intent.target, "hot")

    async def test_timeout_after_acceptance_keeps_original_action(self):
        class SlowSubmitP2(MockP2):
            async def submit(self, intent):
                await super().submit(intent)
                await asyncio.sleep(60)

        p = SlowSubmitP2()
        clock = ManualClock()
        c = Controller(p, clock, settings=replace(Settings(), io_timeout_seconds=0.01))
        key = add(c, p)
        burst(c, key)
        await c.tick()
        original = c.store.pending[key].intent.action_id
        self.assertEqual(len(p.actions), 1)
        p.advance()
        clock.advance(c.settings.retry_seconds)
        await settle(c, p, clock)
        self.assertNotIn(key, c.store.pending)
        self.assertEqual(list(p.actions), [original])

    async def test_action_journal_limit_is_definite_non_submission(self):
        p = MockP2(action_limit=1)
        clock = ManualClock()
        c = Controller(p, clock)
        key = add(c, p)
        burst(c, key)
        await settle(c, p, clock)
        clock.advance(100000)
        await c.tick()
        self.assertNotIn(key, c.store.pending)
        self.assertIn("journal full", c.snapshot(key)["reason"])
        self.assertIsNotNone(p.objects[key].hot_content)

    async def test_delayed_access_does_not_regress_zero_timestamp(self):
        c, p, clock = build()
        key = add(c, p)
        c.access(key, "first", event_at=0)
        clock.advance(1)
        c.access(key, "older", event_at=-1)
        self.assertEqual(c.store.stats[key].last_access, 0)

    async def test_timer_detects_threshold_without_waiting_for_full_audit(self):
        c, p, clock = build()
        key = add(c, p)
        burst(c, key)
        await settle(c, p, clock)
        # Traverse the next audit, then the computed crossing, without another input.
        for _ in range(10):
            clock.advance(max(0, c.store.timers[key][0] - clock()) + 0.001)
            await settle(c, p, clock)
            if c.store.stats[key].desired != "hot":
                break
        self.assertEqual(c.store.stats[key].desired, "warm")
        self.assertEqual(p.objects[key].base, "warm")
        self.assertIsNone(p.objects[key].hot_content)

    async def test_clock_and_settings_validation(self):
        with self.assertRaises(ValueError):
            Settings(high_watermark=1000)
        clock = ManualClock()
        with self.assertRaises(ValueError):
            clock.advance(-1)
        c, p, clock = build()
        key = add(c, p)
        for _ in range(10):
            evaluate(c.store.stats[key], clock(), c.settings)
        self.assertEqual(c.store.stats[key].desired, "cold")

    async def test_reclaim_after_verified_cold_grace_keeps_p2_data(self):
        c, p, clock = build(retention_seconds=10)
        key = add(c, p)
        burst(c, key)
        await settle(c, p, clock)
        clock.advance(600)
        await settle(c, p, clock)
        self.assertEqual(c.store.stats[key].cold_since, clock())
        clock.advance(9)
        await settle(c, p, clock)
        self.assertIn(key, c.store.stats)
        clock.advance(1)
        await settle(c, p, clock)
        for index in (
            c.store.stats,
            c.store.buffer,
            c.store.ready,
            c.store.pending,
            c.store.timers,
        ):
            self.assertNotIn(key, index)
        self.assertTrue(await p.verify_read(p.objects[key].memory, "cold"))
        self.assertFalse(c.snapshot(key)["tracked"])
        self.assertIsNone(c.snapshot(key)["access_count"])
        self.assertTrue(any(row["state"] == "statistics_reclaimed" for row in c.store.history))

    async def test_new_access_at_reclaim_deadline_keeps_statistics(self):
        c, p, clock = build(retention_seconds=10)
        key = add(c, p)
        await settle(c, p, clock)
        clock.advance(10)
        c.access(key, "at-deadline")
        self.assertIsNone(c.store.stats[key].cold_since)
        await settle(c, p, clock)
        self.assertEqual(c.store.stats[key].access_count, 1)
        self.assertEqual(p.objects[key].base, "warm")

    async def test_access_during_final_read_prevents_stale_reclaim(self):
        class CallbackP2(MockP2):
            callback = None

            async def verify_read(self, memory, tier):
                if self.callback:
                    callback, self.callback = self.callback, None
                    callback()
                return await super().verify_read(memory, tier)

        p = CallbackP2()
        clock = ManualClock()
        c = Controller(p, clock, settings=replace(Settings(), stats_retention_seconds=10))
        key = add(c, p)
        await settle(c, p, clock)
        old = c.store.stats[key]
        clock.advance(10)
        p.callback = lambda: c.access(key, "during-final-read")
        await c.tick()
        self.assertIs(c.store.stats[key], old)
        self.assertEqual(old.access_count, 1)
        self.assertIsNone(old.cold_since)
        await settle(c, p, clock)
        self.assertEqual(p.objects[key].base, "warm")

    async def test_revision_during_final_read_preserves_new_statistics(self):
        class CallbackP2(MockP2):
            callback = None

            async def verify_read(self, memory, tier):
                result = await super().verify_read(memory, tier)
                if self.callback:
                    callback, self.callback = self.callback, None
                    callback()
                return result

        p = CallbackP2()
        clock = ManualClock()
        c = Controller(p, clock, settings=replace(Settings(), stats_retention_seconds=10))
        key = add(c, p)
        await settle(c, p, clock)
        clock.advance(10)
        p.callback = lambda: c.register(p.seed(key, "new revision", version=2))
        await c.tick()
        self.assertEqual(c.store.stats[key].memory.version, 2)
        self.assertIsNone(c.store.stats[key].cold_since)
        await settle(c, p, clock)
        self.assertEqual(c.store.stats[key].cold_since, 10)

    async def test_unknown_resets_grace_and_recovery_starts_full_grace(self):
        c, p, clock = build(retention_seconds=10)
        key = add(c, p)
        await settle(c, p, clock)
        clock.advance(10)
        p.unknown_keys.add(key)
        await settle(c, p, clock)
        self.assertIn(key, c.store.stats)
        self.assertIsNone(c.store.stats[key].cold_since)
        p.unknown_keys.clear()
        clock.advance(1)
        await settle(c, p, clock)
        self.assertEqual(c.store.stats[key].cold_since, 11)
        clock.advance(9)
        await settle(c, p, clock)
        self.assertIn(key, c.store.stats)
        clock.advance(1)
        await settle(c, p, clock)
        self.assertNotIn(key, c.store.stats)

    async def test_missing_or_corrupt_base_never_reclaims(self):
        for missing in (True, False):
            c, p, clock = build(retention_seconds=10)
            key = add(c, p)
            await settle(c, p, clock)
            clock.advance(10)
            if missing:
                del p.objects[key]
            else:
                p.objects[key].content = b"corrupted"
            await settle(c, p, clock)
            self.assertIn(key, c.store.stats)
            self.assertIsNone(c.store.stats[key].cold_since)
            self.assertFalse(p.actions)

    async def test_pending_action_survives_retention_deadline(self):
        c, p, clock = build(retention_seconds=10)
        key = add(c, p)
        await settle(c, p, clock)
        clock.advance(9)
        burst(c, key)
        await c.tick()
        original = c.store.pending[key].intent.action_id
        clock.advance(600)
        await c.tick()
        self.assertEqual(c.store.pending[key].intent.action_id, original)
        self.assertIn(key, c.store.stats)
        self.assertIsNone(c.store.stats[key].cold_since)

    async def test_hot_and_warm_records_not_reclaimed_by_age(self):
        c, p, clock = build(retention_seconds=0.1)
        key = add(c, p)
        burst(c, key)
        await settle(c, p, clock)
        clock.advance(1)
        c.store.wake(key, "audit")
        await settle(c, p, clock)
        self.assertIsNone(c.store.stats[key].cold_since)
        self.assertIsNotNone(p.objects[key].hot_content)
        clock.advance(59)
        await settle(c, p, clock)
        self.assertEqual(p.objects[key].base, "warm")
        self.assertIsNone(p.objects[key].hot_content)
        self.assertIn(key, c.store.stats)
        self.assertIsNone(c.store.stats[key].cold_since)

    async def test_reentry_rejects_duplicate_then_starts_new_count(self):
        c, p, clock = build(retention_seconds=10)
        key = add(c, p)
        burst(c, key)
        await settle(c, p, clock)
        clock.advance(600)
        await settle(c, p, clock)
        clock.advance(10)
        await settle(c, p, clock)
        self.assertNotIn(key, c.store.stats)
        self.assertFalse(await c.receive_access(key, "access-0", version=1, event_at=0))
        self.assertNotIn(key, c.store.stats)
        self.assertTrue(await c.receive_access(key, "new-cycle", version=1))
        self.assertEqual(c.store.stats[key].access_count, 1)
        self.assertFalse(await c.receive_access(key, "new-cycle", version=1))
        self.assertEqual(c.store.stats[key].access_count, 1)
        await settle(c, p, clock)
        self.assertEqual(p.objects[key].base, "warm")

    async def test_reentry_unknown_does_not_lose_or_count_input(self):
        c, p, clock = build(retention_seconds=10)
        key = add(c, p)
        await settle(c, p, clock)
        clock.advance(10)
        await settle(c, p, clock)
        p.unknown_keys.add(key)
        with self.assertRaises(ConnectionError):
            await c.receive_access(key, "retry-me", event_at=10)
        self.assertNotIn(key, c.store.stats)
        self.assertNotIn((key, 1, "retry-me"), c.store.dedup)
        p.unknown_keys.clear()
        self.assertTrue(await c.receive_access(key, "retry-me", event_at=10))
        self.assertEqual(c.store.stats[key].access_count, 1)

    async def test_concurrent_reentry_of_same_event_counts_once(self):
        class SlowP2(MockP2):
            async def observe(self, key):
                await asyncio.sleep(0)
                return await super().observe(key)

        p = SlowP2()
        clock = ManualClock()
        c = Controller(p, clock, settings=replace(Settings(), stats_retention_seconds=10))
        key = add(c, p)
        await settle(c, p, clock)
        clock.advance(10)
        await settle(c, p, clock)
        results = await asyncio.gather(*(c.receive_access(key, "one-event") for _ in range(8)))
        self.assertEqual(sum(results), 1)
        self.assertEqual(c.store.stats[key].access_count, 1)

    async def test_expired_events_and_old_versions_do_not_restore_record(self):
        c, p, clock = build(retention_seconds=10)
        key = add(c, p)
        await settle(c, p, clock)
        clock.advance(10)
        await settle(c, p, clock)
        self.assertFalse(await c.receive_access(key, "ignored", entered_context=False))
        self.assertFalse(await c.receive_access(key, "expired", event_at=-86400))
        p.seed(key, "revision 2", version=2)
        self.assertFalse(await c.receive_access(key, "old-version", version=1))
        self.assertNotIn(key, c.store.stats)
        self.assertTrue(await c.receive_access(key, "new-version", version=2))
        self.assertEqual(c.store.stats[key].memory.version, 2)

    async def test_full_statistics_backpressure_then_reuses_reclaimed_slot(self):
        c, p, clock = build(retention_seconds=10, max_memories=1)
        key = add(c, p)
        await settle(c, p, clock)
        other = p.seed(MemoryKey("T1", "M2"), "second")
        with self.assertRaises(BufferError):
            await c.receive_access(other.key, "first")
        self.assertNotIn((other.key, 1, "first"), c.store.dedup)
        self.assertIn(key, c.store.stats)
        clock.advance(10)
        await settle(c, p, clock)
        self.assertTrue(await c.receive_access(other.key, "first"))
        self.assertEqual(len(c.store.stats), 1)
        self.assertNotIn(key, c.store.stats)

    async def test_repeated_reclamation_keeps_timer_index_bounded(self):
        c, p, clock = build(retention_seconds=1)
        key = add(c, p)
        memory = p.objects[key].memory
        for _ in range(80):
            c.register(memory)
            await settle(c, p, clock)
            # Future replacement entries emulate early wakes followed by reclaim.
            c.store.schedule(key, clock() + 10000)
            clock.advance(1)
            c.store.wake(key, "retention_check")
            await settle(c, p, clock)
            self.assertNotIn(key, c.store.stats)
            self.assertFalse(c.store.timers)
            self.assertLessEqual(len(c.store._heap), 32)
        self.assertFalse(c.store.ready)
        self.assertFalse(c.store.pending)
        self.assertFalse(c.store.buffer)

    async def test_retention_settings_reject_invalid_values(self):
        for value in (0, -1, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                replace(Settings(), stats_retention_seconds=value)


if __name__ == "__main__":
    unittest.main()
