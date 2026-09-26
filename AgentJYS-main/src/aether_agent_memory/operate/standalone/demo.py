"""Run: python -m aether_agent_memory.operate.standalone.demo --scenario all"""

import argparse
import asyncio
import json
import math
from dataclasses import replace

from .controller import Controller
from .mock_p2 import MockP2
from .models import Feedback, MemoryKey
from .policy import Settings
from .runner import ManualClock, settle

SCENARIOS = (
    "promotion",
    "cooling",
    "lost_response",
    "unknown",
    "capacity",
    "new_access",
    "eviction",
    "tenants",
    "warm_cooling",
    "stats_reclaim",
    "access_during_retention",
    "reenter_after_reclaim",
    "reclaim_unknown",
    "capacity_reclaim",
    "duplicate_delayed",
    "action_failure",
    "false_success",
    "capacity_race",
    "same_memory_concurrency",
    "access_during_verification",
)


def build(
    *,
    hot_capacity: int = 100000,
    small_buffer: bool = False,
    retention_seconds: float = 3600,
    max_memories: int = 10000,
):
    settings = Settings(
        decay_seconds=60,
        audit_seconds=30,
        retry_seconds=1,
        stats_retention_seconds=retention_seconds,
        max_memories=max_memories,
    )
    if small_buffer:
        settings = replace(settings, buffer_limit=5, high_watermark=4, low_watermark=2)
    clock = ManualClock()
    p2 = MockP2(capacities={"hot": hot_capacity})
    controller = Controller(p2, clock, settings=settings)
    return controller, p2, clock


def add(controller, p2, memory_id="M1", *, tenant="T1", base="cold"):
    key = MemoryKey(tenant, memory_id)
    memory = p2.seed(key, "示例记忆正文：用户偏好简洁回答。", base=base)
    controller.register(memory)
    return key


def burst(controller, key, count=12, prefix="access"):
    for i in range(count):
        controller.access(key, f"{prefix}-{i}")


async def show(label, controller, p2, key):
    observed = await p2.observe(key)
    print(label)
    print(
        json.dumps(
            {
                **controller.snapshot(key),
                "base": observed.base if observed and observed.known else "unknown",
                "hot_replica": observed.hot if observed and observed.known else None,
                "actual_known": observed.known if observed else False,
                "actions": len(p2.actions),
                "effects": p2.effects,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


async def run_scenario(name: str) -> None:
    controller, p2, clock = build(
        hot_capacity=0 if name == "capacity" else 100000,
        small_buffer=name == "eviction",
        retention_seconds=10
        if name
        in {
            "stats_reclaim",
            "access_during_retention",
            "reenter_after_reclaim",
            "reclaim_unknown",
            "capacity_reclaim",
            "access_during_verification",
        }
        else 3600,
        max_memories=1 if name == "capacity_reclaim" else 10000,
    )
    key = add(controller, p2)
    print(f"\n===== {name}（模拟时间、内存 P2） =====")
    if name in {"promotion", "cooling", "lost_response", "capacity", "tenants"}:
        burst(controller, key)
    if name == "promotion":
        await settle(controller, p2, clock)
        await show("12 次有效访问：基础层保留 cold，热副本已建立。", controller, p2, key)
        assert p2.objects[key].base == "cold" and p2.objects[key].hot_content is not None
    elif name == "cooling":
        await settle(controller, p2, clock)
        await show("访问停止前", controller, p2, key)
        clock.advance(600)
        await settle(controller, p2, clock)
        await show("没有新访问，时间推进 600 秒：到期对账移除热副本。", controller, p2, key)
        assert p2.objects[key].hot_content is None and controller.store.stats[key].desired == "cold"
    elif name == "lost_response":
        p2.drop_next_response = True
        await controller.tick()
        original = controller.store.pending[key].intent.action_id
        await show("P2 已接收动作，但提交响应丢失：保留原动作编号。", controller, p2, key)
        await settle(controller, p2, clock)
        await show("查询原动作并确认完成：没有重复建立副本。", controller, p2, key)
        assert list(p2.actions) == [original] and p2.effects == 1
    elif name == "unknown":
        burst(controller, key)
        p2.unknown_keys.add(key)
        await controller.tick()
        await show("层级 Unknown：不假设 cold、不生成动作，等待重查。", controller, p2, key)
        assert not p2.actions
        p2.unknown_keys.clear()
        clock.advance(1)
        await settle(controller, p2, clock)
        await show("查询恢复后正常收敛。", controller, p2, key)
        assert p2.objects[key].hot_content is not None
    elif name == "capacity":
        await controller.tick()
        await show("热层容量为 0：保留热目标，暂停提交。", controller, p2, key)
        assert not p2.actions
        p2.capacities["hot"] = 100000
        clock.advance(1)
        await settle(controller, p2, clock)
        await show("模拟容量释放后，重查并建立热副本。", controller, p2, key)
        assert p2.objects[key].hot_content is not None
    elif name == "new_access":
        burst(controller, key, count=1)
        await controller.tick()
        original = controller.store.pending[key].intent.action_id
        burst(controller, key, prefix="during-action")
        await controller.tick()
        assert len(p2.actions) == 1 and controller.store.pending[key].intent.action_id == original
        await show("旧计划仍在执行时新增访问：只更新统计，不并行生成新计划。", controller, p2, key)
        await settle(controller, p2, clock)
        await show("先完成原来的 warm 计划，再读取新统计并建立热副本。", controller, p2, key)
        assert len(p2.actions) == 2 and p2.objects[key].base == "warm"
        assert p2.objects[key].hot_content is not None
    elif name == "eviction":
        burst(controller, key)
        await settle(controller, p2, clock)
        clock.advance(600)
        for i in range(2, 6):
            other = add(controller, p2, f"M{i}")
            controller.access(other, f"new-{i}")
        assert key not in controller.store.buffer and key in controller.store.stats
        await show("缓冲区到达预留水位：M1 退出驻留，但统计与降温任务保留。", controller, p2, key)
        await settle(controller, p2, clock)
        await show("M1 没有新信号，仍完成降温。", controller, p2, key)
        assert p2.objects[key].hot_content is None
    elif name == "tenants":
        other = add(controller, p2, tenant="T2")
        await settle(controller, p2, clock)
        await show("T1 的 M1 被访问，建立热副本。", controller, p2, key)
        await show("T2 同名 M1 没有访问，保持 cold。", controller, p2, other)
        assert p2.objects[key].hot_content is not None and p2.objects[other].hot_content is None
    elif name == "warm_cooling":
        burst(controller, key)
        await settle(controller, p2, clock)
        clock.advance(60)
        await controller.tick()
        assert controller.store.pending[key].intent.operation == "move_base"
        await show("热度降至温层范围：先调整基础层，保留热副本。", controller, p2, key)
        p2.advance()
        clock.advance(1)
        await controller.tick()
        assert key not in controller.store.pending
        assert p2.objects[key].base == "warm" and p2.objects[key].hot_content is not None
        await show("温层已核验，尚未提交移除热副本。", controller, p2, key)
        await settle(controller, p2, clock)
        assert p2.objects[key].base == "warm" and p2.objects[key].hot_content is None
        assert [a.intent.operation for a in p2.actions.values()] == [
            "ensure_hot_replica",
            "move_base",
            "remove_hot_replica",
        ]
        await show("最终为温层基础副本，无热副本；温层记录不进入回收保留期。", controller, p2, key)
    elif name in {
        "stats_reclaim",
        "access_during_retention",
        "reenter_after_reclaim",
        "capacity_reclaim",
    }:
        burst(controller, key)
        await settle(controller, p2, clock)
        other_memory = None
        if name == "capacity_reclaim":
            other_memory = p2.seed(MemoryKey("T1", "M2"), "等待接收的记忆")
            try:
                controller.register(other_memory)
            except BufferError:
                print("检查：统计上限为 1，M1 仍热门，拒绝 M2；不强删 M1。通过。")
            else:
                raise AssertionError("must not discard active statistics for capacity")
            await show("统计已满：仍有热副本的 M1 被保留。", controller, p2, key)
        clock.advance(600)
        await settle(controller, p2, clock)
        assert controller.store.stats[key].cold_since is not None
        await show("冷层已核验、热副本已移除：保留统计 10 秒。", controller, p2, key)
        if name == "access_during_retention":
            clock.advance(9)
            assert await controller.receive_access(key, "return-during-retention")
            assert controller.store.stats[key].cold_since is None
            assert controller.store.stats[key].access_count == 13
            await show(
                "保留期第 9 秒收到新访问：取消回收，次数继续累计到 13。", controller, p2, key
            )
            await settle(controller, p2, clock)
            clock.advance(2)
            await settle(controller, p2, clock)
            assert key in controller.store.stats and controller.store.stats[key].cold_since is None
            await show("越过原回收时刻：记录仍在，最新目标为温层。", controller, p2, key)
        else:
            clock.advance(10)
            await settle(controller, p2, clock)
            assert all(
                key not in index
                for index in (
                    controller.store.stats,
                    controller.store.buffer,
                    controller.store.ready,
                    controller.store.pending,
                    controller.store.timers,
                )
            )
            assert p2.objects[key].base == "cold" and p2.objects[key].hot_content is None
            await show(
                "保留期到期，再核验后回收统计和调度记录；P2 冷层数据仍在。", controller, p2, key
            )
            if name == "reenter_after_reclaim":
                assert not await controller.receive_access(key, "access-0", version=1, event_at=0)
                assert key not in controller.store.stats
                print("检查：回收后重试旧事件，被去重拦截，不重建统计。通过。")
                assert await controller.receive_access(key, "fresh-after-reclaim", version=1)
                assert controller.store.stats[key].access_count == 1
                await settle(controller, p2, clock)
                await show(
                    "新事件到达：从 P2 查询元数据，重新建档；新周期次数为 1。", controller, p2, key
                )
            elif name == "capacity_reclaim":
                assert other_memory is not None
                controller.register(other_memory)
                controller.access(other_memory.key, "first-M2")
                await settle(controller, p2, clock)
                assert len(controller.store.stats) == 1 and key not in controller.store.stats
                await show(
                    "M1 安全回收后，空位接收 M2；没有突破统计上限。",
                    controller,
                    p2,
                    other_memory.key,
                )
    elif name == "reclaim_unknown":
        await settle(controller, p2, clock)
        assert controller.store.stats[key].cold_since is not None
        await show("冷层记录已确认，进入 10 秒保留期。", controller, p2, key)
        p2.unknown_keys.add(key)
        clock.advance(10)
        await settle(controller, p2, clock)
        assert key in controller.store.stats and controller.store.stats[key].cold_since is None
        await show("到期复查为 Unknown：不回收，取消本次保留计时并等待恢复。", controller, p2, key)
        p2.unknown_keys.clear()
        clock.advance(1)
        await settle(controller, p2, clock)
        assert key in controller.store.stats and controller.store.stats[key].cold_since == clock()
        await show("查询恢复：重新确认冷层，从现在重新保留 10 秒。", controller, p2, key)
        clock.advance(10)
        await settle(controller, p2, clock)
        assert key not in controller.store.stats and key in p2.objects
        await show("新的保留期结束，再次核验后安全回收。", controller, p2, key)
    elif name == "duplicate_delayed":
        assert not controller.access(key, "recall-only", entered_context=False)
        assert controller.access(key, "event-A", event_at=0)
        assert not controller.access(key, "event-A", event_at=0)
        assert controller.store.stats[key].access_count == 1
        print("检查：未进入 Context 不计数；同一事件重复发送只计 1 次。通过。")
        await show("第一条有效事件及其重试：累计次数为 1。", controller, p2, key)
        clock.advance(60)
        assert controller.access(key, "event-B", event_at=0)
        stats = controller.store.stats[key]
        assert stats.access_count == 2
        assert math.isclose(stats.access_sum, 2 * math.exp(-1))
        assert stats.last_access == 0
        assert not controller.access(key, "expired", event_at=-100000)
        assert stats.access_count == 2
        print(
            "检查：第二条事件延迟 60 秒到达，仍按发生时间 0 计算；"
            f"衰减累计值 {stats.access_sum:.6f}，最近访问时间 0；过期事件忽略。通过。"
        )
        await settle(controller, p2, clock)
        assert stats.access_count == 2 and p2.objects[key].base == "warm"
        assert key not in controller.store.pending
        await show("有效事件总数 2，完成温层调整；未把延迟事件当作刚发生。", controller, p2, key)
    elif name == "action_failure":
        burst(controller, key)
        await controller.tick()
        original = controller.store.pending[key].intent.action_id
        assert p2.actions[original].reserved > 0
        await show("计划 A 已提交并预留容量，尚未执行。", controller, p2, key)
        p2.fail_next_action = True
        p2.advance(original)
        assert p2.actions[original].feedback.state == "failed"
        assert p2.actions[original].reserved == 0
        assert (await p2.resources())["hot"] == p2.capacities["hot"]
        clock.advance(1)
        await controller.tick()
        assert key not in controller.store.pending and p2.effects == 0
        print("检查：确认计划 A 明确失败，释放计划所有权和容量预留；未产生热副本。通过。")
        await show("明确失败已经确认；动作数 1，成功数 0。", controller, p2, key)
        clock.advance(1)
        await settle(controller, p2, clock)
        assert len(p2.actions) == 2 and p2.effects == 1
        assert p2.actions[original].feedback.state == "failed"
        assert await p2.verify_read(controller.store.stats[key].memory, "hot")
        assert key not in controller.store.pending
        await show("按最新统计重试为计划 B，成功建立热副本。", controller, p2, key)
    elif name == "false_success":
        burst(controller, key)
        await controller.tick()
        original = controller.store.pending[key].intent.action_id
        action = p2.actions[original]
        action.feedback = Feedback(original, "succeeded")
        clock.advance(1)
        await controller.tick()
        assert controller.store.pending[key].intent.action_id == original
        assert p2.objects[key].hot_content is None and p2.effects == 0
        await show("注入假成功：返回成功但热副本缺失，原计划仍保留。", controller, p2, key)
        # Fixture recovery only: let the original action execute, then corrupt its copy.
        print("模拟外部恢复：撤销假成功注入，推进原动作；随后注入热副本内容损坏。")
        action.feedback = Feedback(original, "running")
        p2.advance(original)
        p2.objects[key].hot_content = b"corrupted"
        clock.advance(1)
        await controller.tick()
        assert controller.store.pending[key].intent.action_id == original
        assert "verification failed" in controller.snapshot(key)["reason"]
        assert not await p2.verify_read(controller.store.stats[key].memory, "hot")
        assert len(p2.actions) == 1
        await show("热副本存在但读取校验失败，不能确认计划完成。", controller, p2, key)
        print("模拟外部修复：演示程序恢复正确副本；Operate 本身未执行自动修复。")
        p2.objects[key].hot_content = bytes(p2.objects[key].content)
        clock.advance(1)
        await settle(controller, p2, clock)
        assert key not in controller.store.pending
        assert len(p2.actions) == 1 and p2.effects == 1
        assert await p2.verify_read(controller.store.stats[key].memory, "hot")
        await show("外部恢复后重新核验通过，确认原计划；没有新建第二个动作。", controller, p2, key)
    elif name == "capacity_race":
        other = add(controller, p2, "M2")
        size = controller.store.stats[key].memory.size
        assert size == controller.store.stats[other].memory.size
        p2.capacities["hot"] = size
        burst(controller, key)
        burst(controller, other)
        await controller.tick()
        running = [a for a in p2.actions.values() if a.feedback.state == "running"]
        assert len(running) == 1 and len(p2.actions) == 1
        assert running[0].reserved == size and (await p2.resources())["hot"] == 0
        assert key in controller.store.pending and other not in controller.store.pending
        print("检查：M1、M2 同批申请仅够一条的热层容量；M1 预留后 M2 等待，未超额接受。通过。")
        await show("M1 已提交，热副本尚未建立但容量已经预留。", controller, p2, key)
        await show("M2 的热目标保留；没有可用容量，因此不提交。", controller, p2, other)
        await settle(controller, p2, clock)
        assert p2.objects[key].hot_content is not None and p2.objects[other].hot_content is None
        p2.capacities["hot"] = size * 2
        print("模拟外部扩容：热层从一个位置增加到两个位置，不代表 Operate 自动驱逐其他对象。")
        clock.advance(1)
        await settle(controller, p2, clock)
        assert all(p2.objects[k].hot_content is not None for k in (key, other))
        assert not controller.store.pending and len(p2.actions) == p2.effects == 2
        assert (await p2.resources())["hot"] == 0
        await show("容量增加后 M2 继续执行，两条记忆均有热副本。", controller, p2, other)
    elif name == "same_memory_concurrency":
        burst(controller, key)
        await asyncio.gather(*(controller.tick() for _ in range(10)))
        assert len(p2.actions) == 1 and len(controller.store.pending) == 1
        original = controller.store.pending[key].intent.action_id
        await show(
            "同时发起 10 次调度调用：只提交一个计划，尚未产生执行效果。", controller, p2, key
        )
        for _ in range(10):
            controller.store.wake(key, "duplicate_notification")
        await asyncio.gather(*(controller.tick() for _ in range(10)))
        assert len(p2.actions) == 1 and controller.store.pending[key].intent.action_id == original
        assert controller.store.stats[key].access_count == 12
        print("检查：重复调度通知不增加访问次数，不改变原计划编号，不重复提交。通过。")
        await settle(controller, p2, clock)
        assert len(p2.actions) == p2.effects == 1 and key not in controller.store.pending
        await show("原计划完成；只执行一次，次数仍为 12。", controller, p2, key)
    elif name == "access_during_verification":
        await settle(controller, p2, clock)
        stats = controller.store.stats[key]
        assert stats.cold_since == 0
        await show("冷层已确认，统计保留 10 秒，当前没有访问。", controller, p2, key)
        clock.advance(10)
        original_verify = p2.verify_read
        injected = False

        async def verify_with_access(memory, tier):
            nonlocal injected
            if not injected:
                injected = True
                assert controller.access(key, "access-inside-final-verification", event_at=clock())
                print("故障时序注入：到期对账正在等待读取核验，此时送入新的有效访问。")
            return await original_verify(memory, tier)

        p2.verify_read = verify_with_access
        try:
            await controller.tick()
        finally:
            p2.verify_read = original_verify
        assert injected and controller.store.stats[key] is stats
        assert stats.access_count == 1 and stats.cold_since is None
        assert key in controller.store.ready
        await show(
            "核验期间到达的新访问阻止旧回收判断，原统计保留并重新入队。", controller, p2, key
        )
        await settle(controller, p2, clock)
        assert p2.objects[key].base == "warm" and stats.access_count == 1
        assert stats.cold_since is None and key not in controller.store.pending
        assert len(p2.actions) == p2.effects == 1
        await show("按新访问对账完成温层调整，没有误删统计。", controller, p2, key)
    print("场景检查：通过")


async def main() -> None:
    parser = argparse.ArgumentParser(description="Operate 独立演示，不连接公共 runtime 或真实 P2")
    parser.add_argument("--scenario", choices=("all", *SCENARIOS), default="all")
    args = parser.parse_args()
    for name in SCENARIOS if args.scenario == "all" else (args.scenario,):
        await run_scenario(name)


if __name__ == "__main__":
    asyncio.run(main())
