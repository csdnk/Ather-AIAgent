"""Run Remember -> Recall -> Operate over a running service and print monitor IDs.

Creates one isolated test session; keeps its memory and diagnostic records for review.
Uses the public HTTP API only. Does not change runtime policy or manually drain workers.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from http_operation import confirmed_request


def run(client, *, timeout=120.0, step_delay=1.0):
    run_id = "monitor_" + uuid4().hex
    content = "P3 monitoring verification code " + run_id
    selection = {"session_id": run_id}
    operations = set()
    print(f"RUN_ID / SESSION_ID: {run_id}", flush=True)

    def request(method, path, **kwargs):
        operation = run_id + "_" + str(len(operations) + 1)
        operations.add(operation)
        return confirmed_request(
            client, method, path, timeout=timeout,
            headers={"X-Operation-ID": operation}, **kwargs,
        )

    def passed(label):
        print(f"PASS {label}", flush=True)
        time.sleep(step_delay)

    def eventually(label, probe):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            value = probe()
            if value:
                return value
            time.sleep(0.5)
        raise TimeoutError(f"{label}; inspect session {run_id} in the monitor")

    def recall(sources):
        return request(
            "POST",
            "/p3/recall",
            json={
                "query": content,
                "selection": selection,
                "sources": sources,
                "token_budget": 1000,
            },
        )

    def require_hit(pack):
        if run_id not in pack["rendered_context"] or pack["outcome"] != "available":
            raise AssertionError("test memory was not returned as an available context")
        return pack

    request("GET", "/p3/ready")
    # Record the existing catalog boundary to avoid scanning older runs' traces.
    initial = request("GET", "/p3/traces", params={"limit": 1, "flow": "business"})
    boundary = initial["items"][0]["first_sequence"] if initial["items"] else 0
    saved = request(
        "POST",
        "/p3/remember",
        json={
            "source": {
                "kind": "conversation",
                "external_id": run_id,
                "external_version": "1",
                "occurred_at": datetime.now(UTC)
                .isoformat(timespec="milliseconds")
                .replace("+00:00", "Z"),
            },
            "selection": selection,
            "content": {"kind": "text", "text": content},
        },
    )
    if not saved["saved"]:
        raise AssertionError("save was not confirmed")
    passed("1/7 Remember: durable save")
    require_hit(recall("working"))
    passed("2/7 Recall: working memory read")
    consolidated = request("POST", "/p3/remember/consolidate", json=selection)
    required_tasks = set(saved["task_ids"]) | set(consolidated["task_ids"])

    def long_term():
        pack = recall("long_term")
        return pack if run_id in pack["rendered_context"] else None

    pack = require_hit(eventually("long-term indexing timed out", long_term))
    memory_ids = {
        item["memory"]["memory_id"]
        for group in pack["groups"]
        for item in group["items"]
        if run_id in item["content"]
    }
    if not memory_ids:
        raise AssertionError("no matching long-term memory reference")
    passed("3/7 Remember: background consolidation and long-term indexing")
    require_hit(request("GET", f"/p3/recalls/{pack['recall_id']}/result"))
    passed("4/7 Recall: long-term context and result revalidation")

    # Default continuous_heat_v1 promotes on repeated real successful reads.
    # This deliberately exercises the public read path instead of injecting heat.
    for index in range(16):
        require_hit(recall("long_term"))
        print(f"  successful read {index + 1}/16", flush=True)
        time.sleep(step_delay)

    def promoted():
        result = []
        for memory_id in sorted(memory_ids):
            placement = request("GET", f"/p3/operate/memories/{memory_id}")
            result.extend(
                {
                    "action_id": action["intent"]["action_id"],
                    "memory_id": memory_id,
                    "state": action["state"],
                    "target_tier": "hot",
                }
                for action in placement["actions"]
                if action["state"] == "succeeded"
                and action["intent"]["decision"]["target_tier"] == "hot"
            )
        return result

    actions = eventually("Operate hot placement timed out", promoted)
    passed("5/7 Operate: automatic hot placement succeeded")
    require_hit(recall("long_term"))
    passed("6/7 Recall: still readable after placement")

    def finished():
        states = [request("GET", f"/p3/tasks/{tid}") for tid in sorted(required_tasks)]
        failures = [
            t
            for t in states
            if t["state"] in {"failed", "cancelled", "attention_required", "recovery_wait"}
        ]
        if failures:
            raise AssertionError(
                "processing tasks did not succeed: "
                + ", ".join(f"{t['task_id']}={t['state']}" for t in failures)
            )
        return states if states and all(t["state"] == "succeeded" for t in states) else None

    eventually("processing tasks timed out", finished)

    def session_finished():
        tasks, cursor = [], None
        while True:
            params = {"limit": 100}
            if cursor:
                params["cursor"] = cursor
            page = request("GET", "/p3/tasks", params=params)
            tasks.extend(
                t for t in page["items"] if t["subject"]["scope"].get("session_id") == run_id
            )
            cursor = page["next_cursor"]
            if not cursor:
                break
        failures = [
            t
            for t in tasks
            if t["state"] in {"failed", "cancelled", "attention_required", "recovery_wait"}
        ]
        if failures:
            raise AssertionError(
                "session task failed: "
                + ", ".join(f"{t['task_id']}={t['state']}" for t in failures)
            )
        return tasks if tasks and all(t["state"] == "succeeded" for t in tasks) else None

    tasks = eventually("session background tasks timed out", session_finished)
    task_traces = {t["trace_id"] for t in tasks if t["trace_id"]}
    traces, flows, before = [], set(), None
    while True:
        params = {"limit": 100, "flow": "business"}
        if before:
            params["before"] = before
        page = request("GET", "/p3/traces", params=params)
        reached_boundary = False
        for trace in page["items"]:
            if trace["first_sequence"] <= boundary:
                reached_boundary = True
                break
            records, after = [], 0
            while True:
                logs = request(
                    "GET", f"/p3/logs/{trace['trace_id']}", params={"limit": 500, "after": after}
                )
                records.extend(logs["records"])
                after = logs["next_after"]
                if after is None:
                    break
            if trace["trace_id"] in task_traces or any(
                r.get("operation_id") in operations for r in records
            ):
                trace_flows = sorted({r["flow"] for r in records})
                flows.update(trace_flows)
                traces.append(
                    {
                        "trace_id": trace["trace_id"],
                        "flows": trace_flows,
                        "records": len(records),
                        "entry_node": trace["entry_node"],
                    }
                )
        before = page["next_before"]
        if reached_boundary or before is None:
            break
    if not {"remember", "recall", "operate"} <= flows:
        raise AssertionError(f"missing three-flow trace coverage: {sorted(flows)}")
    if not any(t["owner_flow"] == "operate" and t["state"] == "succeeded" for t in tasks):
        raise AssertionError("no succeeded Operate task found for this session")
    for task in tasks:
        request("GET", f"/p3/tasks/{task['task_id']}/progress")
    passed("7/7 Monitor: task progress and all three flows' retained traces readable")
    return {
        "passed": True,
        "session_id": run_id,
        "operation_id": saved["operation_id"],
        "memory_ids": sorted(memory_ids),
        "recall_id": pack["recall_id"],
        "operate_actions": actions,
        "tasks": [{k: t[k] for k in ("task_id", "kind", "state", "trace_id")} for t in tasks],
        "traces": traces,
        "coverage": "retained_records_only",
        "production_acceptance": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument(
        "--timeout", type=float, default=120, help="maximum wait in seconds per asynchronous stage"
    )
    parser.add_argument(
        "--step-delay",
        type=float,
        default=1,
        help="pause between stages/reads to observe the live monitor",
    )
    args = parser.parse_args()
    if args.timeout <= 0 or args.step_delay < 0:
        parser.error("timeout must be positive and step-delay must be nonnegative")
    try:
        with httpx.Client(
            base_url=args.url,
            timeout=30,
            follow_redirects=False,
            headers={"Authorization": "Bearer " + args.credential_file.read_text("utf-8").strip()},
        ) as client:
            result = run(client, timeout=args.timeout, step_delay=args.step_delay)
        print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)
        return 0
    except (httpx.HTTPError, OSError, AssertionError, TimeoutError) as error:
        # Do not print headers, credentials, server payloads or a credential-bearing URL.
        if isinstance(error, httpx.HTTPStatusError):
            reason = f"HTTP {error.response.status_code}; check service and credential"
        elif isinstance(error, (AssertionError, TimeoutError)):
            reason = str(error)
        else:
            reason = type(error).__name__ + "; check service address and credential file"
        print(json.dumps({"passed": False, "reason": reason}), file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
