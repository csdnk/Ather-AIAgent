"""Public HTTP smoke for an already running service, with an explicit local credential.

Writes one uniquely identified test memory. Reports IDs/states, never credentials.
"""

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
from http_operation import confirmed_request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=90)
    args = parser.parse_args()
    operation = "smoke_" + uuid4().hex
    content = "P3 smoke verification code " + operation
    with httpx.Client(
        base_url=args.url,
        timeout=30,
        headers={
            "Authorization": "Bearer " + args.credential_file.read_text("utf-8").strip(),
        },
    ) as client:
        client.get("/p3/live").raise_for_status()
        confirmed_request(
            client,
            "POST",
            "/p3/remember",
            timeout=args.timeout,
            headers={"X-Operation-ID": operation},
            json={
                "source": {
                    "kind": "conversation",
                    "external_id": operation,
                    "external_version": "1",
                    "occurred_at": datetime.now(UTC)
                    .isoformat(timespec="milliseconds")
                    .replace("+00:00", "Z"),
                },
                "selection": {"session_id": operation},
                "content": {"kind": "text", "text": content},
            },
        )
        # Explicitly close this smoke batch rather than changing deployment policy.
        confirmed_request(
            client, "POST", "/p3/remember/consolidate", timeout=args.timeout,
            headers={"X-Operation-ID": operation + "_consolidate"},
            json={"session_id": operation},
        )
        until = time.monotonic() + args.timeout
        while time.monotonic() < until:
            recalled = confirmed_request(
                client,
                "POST",
                "/p3/recall",
                timeout=args.timeout,
                json={
                    "query": content,
                    "selection": {"session_id": operation},
                    "sources": "long_term",
                    "token_budget": 1000,
                },
            )
            if operation in recalled["rendered_context"]:
                rid = recalled["recall_id"]
                client.get(f"/p3/recalls/{rid}/result").raise_for_status()
                print(json.dumps({"passed": True, "operation_id": operation, "recall_id": rid}))
                return 0
            time.sleep(0.5)
    raise TimeoutError("long-term smoke did not converge; inspect processing and task diagnostics")


if __name__ == "__main__":
    raise SystemExit(main())
