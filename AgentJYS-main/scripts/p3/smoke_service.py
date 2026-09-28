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
        saved = client.post(
            "/p3/remember",
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
        saved.raise_for_status()
        # Explicitly close this smoke batch rather than changing deployment policy.
        client.post("/p3/remember/consolidate", json={"session_id": operation}).raise_for_status()
        until = time.monotonic() + args.timeout
        while time.monotonic() < until:
            recalled = client.post(
                "/p3/recall",
                json={
                    "query": content,
                    "selection": {"session_id": operation},
                    "sources": "long_term",
                    "token_budget": 1000,
                },
            )
            recalled.raise_for_status()
            if operation in recalled.json()["rendered_context"]:
                rid = recalled.json()["recall_id"]
                client.get(f"/p3/recalls/{rid}/result").raise_for_status()
                print(json.dumps({"passed": True, "operation_id": operation, "recall_id": rid}))
                return 0
            time.sleep(0.5)
    raise TimeoutError("long-term smoke did not converge; inspect processing and task diagnostics")


if __name__ == "__main__":
    raise SystemExit(main())
