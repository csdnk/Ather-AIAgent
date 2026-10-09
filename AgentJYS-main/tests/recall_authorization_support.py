"""Reusable AET-8 HTTP observation at the AET-22 agreed seam.

No admission retries, database reads, auth overrides or fabricated backend evidence.
"""

import json
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

F1_TEXTS = ("用户喝咖啡不加糖。", "用户喜欢乌龙茶。")


class SafeEvidence:
    """Allowlisted evidence only. Raw content is asserted, never persisted here."""

    def __init__(self, case_id: str, variant: str) -> None:
        self.data: dict[str, Any] = {
            "case_id": case_id,
            "variant": variant,
            "responses": [],
            "blockers": [],
        }

    def response(self, method: str, path: str, response: httpx.Response) -> None:
        body = response.json()
        self.data["responses"].append(
            {
                "method": method,
                "path": path,
                "status": response.status_code,
                "code": body.get("code"),
                "trace_id": response.headers.get("X-Trace-ID"),
                "request_id": response.headers.get("X-Request-ID"),
                "job_id": response.headers.get("X-P3-Job-ID"),
            }
        )

    def blocked(self, category: str, requirement: str) -> None:
        assert category in {"blocked_requirement", "blocked_fixture"}
        self.data["blockers"].append({"status": category, "requirement": requirement})

    def write(self, directory: Path) -> None:
        project = Path(__file__).resolve().parents[2]
        directory = directory.resolve()
        if directory.is_relative_to(project):
            raise ValueError("execution evidence must live outside the project")
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.data['case_id']}-{self.data['variant']}.json"
        # A rerun must not destroy the previous execution's evidence.
        with path.open("x", encoding="utf-8") as stream:
            json.dump(self.data, stream, ensure_ascii=False, indent=2)


def assert_denied(response: httpx.Response, status: int, code: str) -> None:
    """Inspect raw payload before redaction; an error code alone proves nothing."""
    assert response.status_code == status, "denial HTTP contract differs"
    payload = response.json()
    assert payload.get("code") == code, "denial error contract differs"

    def inspect(value: Any) -> None:
        if isinstance(value, dict):
            forbidden = {
                "pack",
                "groups",
                "items",
                "candidates",
                "rendered_context",
                "content",
                "body",
                "memory_id",
                "memory",
                "result",
                "result_ref",
                "selected_sources",
            }
            assert not forbidden.intersection(value), "content/candidate/Pack leak"
            for child in value.values():
                inspect(child)
        elif isinstance(value, list):
            for child in value:
                inspect(child)
        elif isinstance(value, str):
            assert not any(text.rstrip("。") in value for text in F1_TEXTS), "F1 body leak"

    inspect(payload)


class RecallHTTP:
    def __init__(self, client: httpx.Client, maintainer: str, timeout: float = 120) -> None:
        self.client = client
        self.maintainer = maintainer
        self.timeout = timeout

    def get(self, path: str, token: str | None = None, **kwargs: Any) -> httpx.Response:
        return self.client.get(
            path,
            headers={
                "Authorization": f"Bearer {token or self.maintainer}",
            },
            **kwargs,
        )

    def prepare_f1(self, session_id: str) -> tuple[dict[str, Any], ...]:
        """Prepare exact receipt references, not the first two catalog entries."""
        prepared = []
        for index, text in enumerate(F1_TEXTS):
            receipt, _ = self.command(
                "/p3/remember",
                {
                    "source": {
                        "kind": "text",
                        "external_id": f"{session_id}-{index}",
                        "external_version": "1",
                        "occurred_at": "2026-10-09T00:00:00.000Z",
                    },
                    "selection": {"session_id": session_id},
                    "content": {"kind": "text", "text": text},
                },
                f"{session_id}-prepare-{index}",
                self.maintainer,
            )
            assert receipt["saved"] and len(receipt["memories"]) == 1
            ref = receipt["memories"][0]
            deadline = time.monotonic() + self.timeout
            while True:
                response = self.get(f"/p3/remember/{ref['memory_id']}")
                assert response.status_code == 200, "F1 snapshot inspection failed"
                snapshot = response.json()
                assert snapshot["ref"] == ref and ref["version"] == 1, "F1 version changed"
                assert snapshot["kind"] == "working", "F1 must remain Working"
                if snapshot["projection_state"] == "ready":
                    break
                assert snapshot["projection_state"] != "failed", "F1 projection failed"
                assert time.monotonic() < deadline, "F1 did not reach Ready"
                time.sleep(min(0.05, max(0, deadline - time.monotonic())))
            proof = self.verify_body(ref, text)
            proof.update(projection_state="ready", model_space=snapshot["model_space"])
            prepared.append(proof)
        return tuple(prepared)

    def recall_tasks(self) -> dict[str, dict[str, Any]]:
        """Complete authorized maintenance view, including pagination."""
        found = {}
        cursor = None
        seen = set()
        while True:
            response = self.get(
                "/p3/tasks",
                params={
                    "limit": 100,
                    **({"cursor": cursor} if cursor else {}),
                },
            )
            assert response.status_code == 200, "maintenance task view unavailable"
            page = response.json()
            for task in page["items"]:
                if task["kind"] == "recall.execute":
                    found[task["task_id"]] = task
            cursor = page["next_cursor"]
            if not cursor:
                return found
            assert cursor not in seen, "maintenance pagination did not advance"
            seen.add(cursor)

    def command(
        self, path: str, payload: dict[str, Any], operation_id: str, token: str
    ) -> tuple[dict[str, Any], str]:
        response = self.client.post(
            path,
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "X-Operation-ID": operation_id,
            },
        )
        assert response.status_code in {200, 202}, "command not accepted"
        location = response.headers.get("Location", "")
        job_id = response.headers.get("X-P3-Job-ID") or location.removeprefix("/p3/operations/")
        assert job_id and "/" not in job_id, "original job binding missing"
        job = self.poll_job(job_id, token)
        assert job["state"] == "succeeded", "original command did not succeed"
        result = self.client.get(
            f"/p3/operations/{job_id}/result", headers={"Authorization": f"Bearer {token}"}
        )
        assert result.status_code == 200, "original result retrieval failed"
        if response.status_code == 200:
            assert result.json() == response.json(), "original synchronous result changed"
        return result.json(), job_id

    def verify_body(self, ref: dict[str, Any], expected: str) -> dict[str, Any]:
        body = self.client.post(
            "/p3/remember/body",
            json=ref,
            headers={
                "Authorization": f"Bearer {self.maintainer}",
            },
        )
        assert body.status_code == 200, "F1 body inspection failed"
        value = body.json()
        digest = sha256(expected.encode()).hexdigest()
        assert value["outcome"] == "read" and value["content"] == expected, "F1 body differs"
        assert value["memory"] == value["guard"]["memory"] == ref, "F1 body Ref differs"
        assert value["guard"]["body_hash"] == value["location"]["content_hash"] == digest, (
            "F1 body hash differs"
        )
        assert value["location"]["generation"], "F1 body generation missing"
        return {"ref": ref, "body_hash": digest, "generation": value["location"]["generation"]}

    def poll_job(self, job_id: str, token: str | None = None) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            response = self.client.get(
                f"/p3/operations/{job_id}",
                headers={"Authorization": f"Bearer {token or self.maintainer}"},
            )
            assert response.status_code == 200, "original job observation failed"
            job: dict[str, Any] = response.json()
            if job["state"] in {"succeeded", "failed", "attention_required", "cancelled"}:
                return job
            time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        raise AssertionError("original job did not reach a terminal state")
