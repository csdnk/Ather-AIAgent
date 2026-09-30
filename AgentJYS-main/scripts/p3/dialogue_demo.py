"""预设多轮中文对话的真实 P3 HTTP 验证；不调用或模拟聊天模型。

每次创建独立业务范围，保留测试记忆和诊断记录。对话中的保存、查询、
明确纠错由预设上层应用路由；检索文本、版本、任务及 Trace 均来自服务。
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx


class DialogueDemo:
    def __init__(
        self, client, *, timeout=120, delay=0, preference="可乐", corrected_preference="无糖红茶"
    ):
        self.client, self.timeout, self.delay = client, timeout, delay
        self.preference, self.corrected_preference = preference, corrected_preference
        self.run_id = "dialogue_" + uuid4().hex
        self.session_a = self.run_id + "_a"
        self.session_b = self.run_id + "_b"
        self.operations = {}
        self.checks = []

    def call(self, label, method, path, *, expected=200, **kwargs):
        op = self.run_id + "_" + str(len(self.operations) + 1)
        self.operations[op] = label
        try:
            response = self.client.request(method, path, headers={"X-Operation-ID": op}, **kwargs)
        except httpx.TimeoutException as exc:
            raise TimeoutError(
                f"{label}: {method} {path} 请求超时；"
                "请检查 P3 服务响应。该请求没有自动重试。"
            ) from exc
        if response.status_code != expected:
            raise AssertionError(f"{label}: HTTP {response.status_code}，预期 {expected}")
        return response.json()

    def wait(self, label, probe):
        until = time.monotonic() + self.timeout
        while time.monotonic() < until:
            value = probe()
            if value:
                return value
            time.sleep(0.5)
        raise TimeoutError(f"{label}超时；本次业务范围 {self.run_id}")

    def show(self, speaker, text):
        print(f"\n{speaker}：{text}", flush=True)

    def wait_for_recall(self, label, query, expected):
        last_pack = {}

        def matching():
            nonlocal last_pack
            last_pack = self.recall(query)
            return (
                last_pack
                if last_pack["outcome"] == "available"
                and expected in last_pack["rendered_context"]
                else None
            )

        try:
            return self.wait(label, matching)
        except TimeoutError as exc:
            # Preserve transport timeouts; only enrich an unmet polling condition.
            if isinstance(exc.__cause__, httpx.TimeoutException):
                raise
            raise TimeoutError(
                f"{exc}；预期召回：{expected!r}；"
                f"最近 outcome={last_pack.get('outcome', '未取得响应')}；"
                f"实际召回：{last_pack.get('rendered_context', '')[:500]!r}"
            ) from exc

    def check(self, label, condition):
        if not condition:
            raise AssertionError(label)
        self.checks.append(label)
        print(f"  PASS {label}", flush=True)
        if self.delay > 0:
            time.sleep(self.delay)

    def source(self, suffix):
        return {
            "kind": "conversation",
            "external_id": self.run_id + suffix,
            "external_version": "1",
            "occurred_at": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
        }

    def save(self, text, suffix):
        self.show("用户", text)
        result = self.call(
            "保存：" + text,
            "POST",
            "/p3/remember",
            json={
                "source": self.source(suffix),
                "selection": {"task_id": self.run_id, "session_id": self.session_a},
                "content": {"kind": "text", "text": text},
            },
        )
        self.check("已持久保存这轮用户陈述", result["saved"])

    def recall(self, query, *, working=False, isolated=False):
        selection = {"task_id": self.run_id + "_other" if isolated else self.run_id}
        if working:
            selection["session_id"] = self.session_a
        # Cross-session recall uses the shared business range only, never chat history
        # or a query augmented with the answer. session_b stays client-side metadata.
        return self.call(
            query,
            "POST",
            "/p3/recall",
            json={
                "query": query,
                "selection": selection,
                "sources": "working" if working else "long_term",
                "token_budget": 2000,
            },
        )

    @staticmethod
    def items(pack):
        return [item for group in pack["groups"] for item in group["items"]]

    def show_pack(self, pack):
        self.show("P3 实际召回", pack["rendered_context"] or "（空）")
        print(f"  recall_id: {pack['recall_id']}", flush=True)

    def tasks(self):
        items, cursor = [], None
        while True:
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = self.call("诊断任务目录", "GET", "/p3/tasks", params=params)
            items.extend(
                t for t in page["items"] if t["subject"]["scope"].get("task_id") == self.run_id
            )
            cursor = page["next_cursor"]
            if not cursor:
                return items

    def settled(self):
        tasks = self.tasks()
        failures = [
            t
            for t in tasks
            if t["state"] in {"failed", "cancelled", "attention_required", "recovery_wait"}
        ]
        if failures:
            raise AssertionError(
                "后台任务异常：" + ", ".join(f"{t['task_id']}={t['state']}" for t in failures)
            )
        return tasks if tasks and all(t["state"] == "succeeded" for t in tasks) else None

    def evidence(self, boundary, tasks):
        traces, before = [], None
        task_traces = {t["trace_id"] for t in tasks}
        while True:
            params = {"limit": 100, "flow": "business"}
            if before:
                params["before"] = before
            page = self.call("诊断链路目录", "GET", "/p3/traces", params=params)
            stop = False
            for trace in page["items"]:
                if trace["first_sequence"] <= boundary:
                    stop = True
                    break
                records, after = [], 0
                while True:
                    logs = self.call(
                        "诊断节点记录",
                        "GET",
                        f"/p3/logs/{trace['trace_id']}",
                        params={"limit": 500, "after": after},
                    )
                    records.extend(logs["records"])
                    after = logs["next_after"]
                    if after is None:
                        break
                labels = sorted(
                    {
                        self.operations[r["operation_id"]]
                        for r in records
                        if r["operation_id"] in self.operations
                    }
                )
                if labels or trace["trace_id"] in task_traces:
                    traces.append(
                        {
                            "trace_id": trace["trace_id"],
                            "dialogue_steps": labels,
                            "flows": sorted({r["flow"] for r in records}),
                            "record_count": len(records),
                        }
                    )
            before = page["next_before"]
            if stop or before is None:
                break
        flows = {flow for trace in traces for flow in trace["flows"]}
        self.check(
            "可查询 Remember / Recall / Operate 三流程轨迹",
            {"remember", "recall", "operate"} <= flows,
        )
        return traces

    def run(self):
        self.show("场景", "出差助手：饮品偏好、出差安排、跨会话回忆、明确纠错")
        print(
            f"RUN_ID: {self.run_id}\n会话 A: {self.session_a}\n会话 B: {self.session_b}", flush=True
        )
        print("以下展示服务原始召回证据，不是大模型生成的回答。", flush=True)
        self.call("就绪检查", "GET", "/p3/ready")
        initial = self.call(
            "初始链路边界", "GET", "/p3/traces", params={"limit": 1, "flow": "business"}
        )
        boundary = initial["items"][0]["first_sequence"] if initial["items"] else 0
        self.save(f"我的饮品偏好是{self.preference}。", "饮品偏好".encode().hex())
        self.save("我下周三去上海出差，住在虹桥附近。", "出差安排".encode().hex())
        for query, expected in [
            ("我的饮品偏好是什么？", self.preference),
            ("我下周三去哪里出差？", "上海"),
        ]:
            self.show("用户", query)
            pack = self.recall(query, working=True)
            self.show_pack(pack)
            self.check("本会话召回：" + expected, expected in pack["rendered_context"])

        self.show("应用动作", "结束会话 A，提交长期化，等待真实后台任务完成。")
        self.call(
            "结束会话并长期化",
            "POST",
            "/p3/remember/consolidate",
            json={"task_id": self.run_id, "session_id": self.session_a},
        )
        self.wait("后台长期化", self.settled)
        self.show("会话 B", "清空本轮聊天上下文，只发送新问题；查询不限制旧会话 ID。")
        query = "我的饮品偏好是什么？"
        self.show("用户", query)

        old_pack = self.wait_for_recall("跨会话长期召回", query, self.preference)
        self.show_pack(old_pack)
        self.check("新会话从长期记忆取回偏好", old_pack["outcome"] == "available")
        self.show("用户", "我下周三去哪里出差？")
        trip = self.recall("我下周三去哪里出差？")
        self.show_pack(trip)
        self.check(
            "新会话仍记得上海和虹桥",
            all(word in trip["rendered_context"] for word in ("上海", "虹桥")),
        )

        self.show("用户", f"更正我的饮品偏好：我现在改喝{self.corrected_preference}。")
        target = next(item for item in self.items(old_pack) if self.preference in item["content"])
        mid = target["memory"]["memory_id"]
        current = self.call("读取待纠错版本", "GET", f"/p3/remember/{mid}")
        self.show("应用动作", "预设场景将明确纠错路由到 correct 接口，保留同条记忆的其他内容。")
        self.call(
            "更正饮品偏好",
            "POST",
            f"/p3/remember/{mid}/correct",
            json={
                "expected_version": current["ref"]["version"],
                "content": current["content"].replace(self.preference, self.corrected_preference),
                "source": self.source("_correction"),
                "reason": "用户明确更正饮品偏好",
            },
        )
        self.call(
            "旧结果失效校验", "GET", f"/p3/recalls/{old_pack['recall_id']}/result", expected=410
        )
        self.check("旧召回结果已失效（预期 HTTP 410）", True)

        query = "再确认一下，我的饮品偏好是什么？"
        self.show("用户", query)
        new_pack = self.wait_for_recall("纠错后索引更新", query, self.corrected_preference)
        self.show_pack(new_pack)
        self.check(
            "只返回新偏好，不返回旧偏好", self.preference not in new_pack["rendered_context"]
        )
        self.check(
            "同一记忆的版本已递增",
            any(
                item["memory"]["memory_id"] == mid
                and item["memory"]["version"] > current["ref"]["version"]
                for item in self.items(new_pack)
            ),
        )
        isolated = self.recall(query, isolated=True)
        self.check("另一个业务范围没有串入本次记忆", isolated["outcome"] == "empty")
        tasks = self.wait("本次对话的后台任务", self.settled)
        self.check(
            "Operate 已实际消费对话读写事件", any(task["owner_flow"] == "operate" for task in tasks)
        )
        for task in tasks:
            self.call("任务进度", "GET", f"/p3/tasks/{task['task_id']}/progress")
        traces = self.evidence(boundary, tasks)
        return {
            "passed": True,
            "run_id": self.run_id,
            "checks": self.checks,
            "sessions": [self.session_a, self.session_b],
            "corrected_memory_id": mid,
            "tasks": [{k: t[k] for k in ("task_id", "kind", "state", "trace_id")} for t in tasks],
            "traces": traces,
            "coverage": "retained_records_only",
            "chat_model_used": False,
            "production_acceptance": False,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=120, help="异步任务每阶段等待秒数")
    parser.add_argument("--step-delay", type=float, default=0, help="展示停顿秒数，默认不停顿")
    parser.add_argument("--preference", default="可乐", help="初始饮品偏好，保存与校验共用")
    parser.add_argument("--corrected-preference", default="无糖红茶", help="纠错后的饮品偏好")
    args = parser.parse_args()
    if args.timeout <= 0 or args.step_delay < 0:
        parser.error("timeout 必须大于 0，step-delay 不能为负数")
    if (
        not args.preference.strip()
        or not args.corrected_preference.strip()
        or args.preference in args.corrected_preference
    ):
        parser.error("饮品偏好不能为空，且新偏好不能包含旧偏好（场景会检查旧偏好消失）")
    try:
        with httpx.Client(
            base_url=args.url,
            timeout=30,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + args.credential_file.read_text("utf-8").strip()},
        ) as client:
            result = DialogueDemo(
                client, timeout=args.timeout, delay=args.step_delay,
                preference=args.preference, corrected_preference=args.corrected_preference,
            ).run()
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (httpx.HTTPError, OSError, AssertionError, TimeoutError) as exc:
        reason = str(exc) if isinstance(exc, (AssertionError, TimeoutError)) else type(exc).__name__
        print(
            json.dumps({"passed": False, "reason": reason}, ensure_ascii=False),
            file=sys.stderr,
            flush=True,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
