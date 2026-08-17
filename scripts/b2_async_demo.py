"""Run the B2 long-text API demo against the composed local stack."""

from __future__ import annotations

import json
import time
from urllib.request import Request, urlopen


BASE_URL = "http://localhost:8080"


def request(path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = Request(f"{BASE_URL}{path}", data=data, headers={"Content-Type": "application/json"})
    with urlopen(req) as response:  # noqa: S310 - fixed localhost demo URL
        return json.loads(response.read())


if __name__ == "__main__":
    created = request(
        "/api/v1/b2/long-text",
        {
            "tenant_id": "demo-tenant",
            "user_id": "demo-user",
            "agent_id": "demo-agent",
            "session_id": "demo-session",
            "source_id": "demo-nanjing-trip",
            "text": "下周前往南京出差，请记得带上已经签好的合同、身份证和电脑电源。" * 80,
        },
    )
    task_id = str(created["task_id"])
    print(f"submitted task_id={task_id}")
    for _ in range(60):
        status = request(f"/api/v1/b2/tasks/{task_id}")
        print(f"status={status['state']}")
        if status["state"] == "SUCCEEDED":
            break
        if status["state"] == "FAILED":
            raise RuntimeError(str(status))
        time.sleep(1)
    else:
        raise TimeoutError("B2 task did not finish within 60 seconds")
    results = request(
        "/api/v1/b2/search",
        {
            "tenant_id": "demo-tenant",
            "user_id": "demo-user",
            "agent_id": "demo-agent",
            "query": "南京出差需要带哪些东西？",
        },
    )
    print(json.dumps(results, ensure_ascii=False, indent=2))
