"""Per-user P3 transport. Authentication and resource scopes never come from the UI."""

import re
import time
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any, cast
from urllib.parse import urlsplit

import httpx

from aether_agent_memory.recall.contracts.models import ContextPack
from aether_agent_memory.runtime.contracts.mutation_receipts import MutationKind, MutationResult
from aether_platform.directory import Actor


class P3Error(RuntimeError):
    def __init__(self, code: str, *, job_id: str | None = None):
        self.code, self.job_id = code, job_id
        super().__init__("P3 " + code)


def identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise P3Error("INVALID_IDENTIFIER")
    return value


class P3Client(AbstractContextManager["P3Client"]):
    def __init__(
        self,
        base_url: str,
        token: str | Callable[[], str],
        actor: Actor,
        *,
        transport: httpx.BaseTransport | None = None,
        wait_seconds: float = 240,
        operator: bool = False,
        admin_read: bool = False,
        admin_write: bool = False,
        trusted_http_host: str | None = None,
    ):
        url = urlsplit(base_url)
        cluster_http = bool(
            trusted_http_host
            and trusted_http_host.endswith(".svc.cluster.local")
            and url.hostname == trusted_http_host
            and url.port == 8080
        )
        if (
            url.scheme not in {"http", "https"}
            or url.username
            or url.password
            or url.query
            or url.fragment
            or (
                url.scheme != "https"
                and url.hostname not in {"localhost", "127.0.0.1", "::1"}
                and not cluster_http
            )
        ):
            raise ValueError(
                "P3 requires HTTPS, loopback, or an explicitly trusted cluster service"
            )
        if admin_read or admin_write:
            if (
                operator
                or (admin_read and admin_write)
                or actor.role not in {"platform_admin", "tenant_admin"}
                or (actor.role == "tenant_admin" and not actor.tenant_id)
            ):
                raise P3Error("USER_IDENTITY_REQUIRED")
        elif (operator and actor.role != "platform_admin") or (
            not operator and (actor.role != "user" or not actor.tenant_id)
        ):
            raise P3Error("USER_IDENTITY_REQUIRED")
        self.admin_read = admin_read
        self.admin_write = admin_write
        self.token = token
        self.actor, self.wait_seconds = actor, wait_seconds
        self.tenant_id = (
            "aether_platform_operations"
            if operator or ((admin_read or admin_write) and actor.role == "platform_admin")
            else actor.tenant_id
        )
        assert self.tenant_id is not None
        self.http = httpx.Client(
            base_url=base_url,
            headers={"X-P3-Tenant": self.tenant_id},
            timeout=65,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def __exit__(self, *args: Any) -> None:
        self.http.close()

    def call(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: dict[str, Any] | None = None,
        operation_id: str | None = None,
        kind: str | None = None,
        content: bytes | None = None,
        media_type: str | None = None,
    ) -> Any:
        if not path.startswith("/p3/") or ".." in path or "?" in path or "#" in path:
            raise P3Error("INVALID_ROUTE")
        if self.admin_read and (method != "GET" or not path.startswith("/p3/admin/")):
            raise P3Error("INVALID_ROUTE")
        if self.admin_write and not (
            (method == "POST" and path == "/p3/admin/memory-commands")
            or (
                method == "GET"
                and re.fullmatch(r"/p3/admin/memory-commands/[A-Za-z0-9_-]{1,128}", path)
            )
        ):
            raise P3Error("INVALID_ROUTE")
        headers = {"X-Operation-ID": identifier(operation_id)} if operation_id else {}
        headers["Authorization"] = "Bearer " + (
            self.token() if callable(self.token) else self.token
        )
        if media_type:
            headers["Content-Type"] = media_type
        try:
            response = self.http.request(
                method,
                path,
                json=body if content is None else None,
                content=content,
                params=params,
                headers=headers,
            )
        except httpx.HTTPError:
            if method in {"POST", "PUT"} and operation_id and kind:
                from typing import get_args

                if kind in get_args(MutationKind):
                    found = self.call(
                        "GET",
                        "/p3/mutation-receipts/" + identifier(operation_id),
                        params={"kind": kind},
                    )
                    if found.get("state") == "committed":
                        result = self.call(
                            "GET",
                            "/p3/mutation-receipts/" + identifier(operation_id) + "/result",
                            params={"kind": kind},
                        )
                        return MutationResult.model_validate(result).response
                    raise P3Error("CONNECTION_UNCONFIRMED") from None
                found = self.call(
                    "GET",
                    "/p3/operation-requests/" + identifier(operation_id),
                    params={"kind": kind},
                )
                if found.get("state") == "found":
                    return self.wait(found["job_id"])
            raise P3Error("CONNECTION_UNCONFIRMED") from None
        try:
            data = response.json()
        except ValueError:
            raise P3Error("INVALID_RESPONSE") from None
        code = data.get("code") if isinstance(data, dict) else None
        if code == "REQUEST_IN_PROGRESS":
            job = response.headers.get("X-P3-Job-ID")
            if not job and operation_id and kind:
                found = self.call(
                    "GET",
                    "/p3/operation-requests/" + identifier(operation_id),
                    params={"kind": kind},
                )
                job = found.get("job_id")
            if job:
                return self.wait(job)
            raise P3Error("REQUEST_IN_PROGRESS")
        if not response.is_success or code:
            raise P3Error(str(code or "HTTP_" + str(response.status_code)))
        return data

    def wait(self, job_id: str) -> Any:
        deadline = time.monotonic() + self.wait_seconds
        while True:
            try:
                response = self.http.get(
                    "/p3/operations/" + identifier(job_id) + "/result",
                    headers={
                        "Authorization": "Bearer "
                        + (self.token() if callable(self.token) else self.token)
                    },
                )
                data = response.json()
            except (httpx.HTTPError, ValueError):
                raise P3Error("RESULT_UNCONFIRMED", job_id=job_id) from None
            code = data.get("code") if isinstance(data, dict) else None
            if response.is_success and not code:
                return data
            if code != "REQUEST_IN_PROGRESS":
                raise P3Error(str(code or "RESULT_FAILED"), job_id=job_id)
            if time.monotonic() >= deadline:
                raise P3Error("REQUEST_IN_PROGRESS", job_id=job_id)
            time.sleep(1)

    def identity(self) -> dict[str, Any]:
        result = self.call("GET", "/p3/auth/me")
        scope = result.get("scope", {})
        if (
            result.get("principal_id") != self.actor.id
            or scope.get("tenant_id") != self.tenant_id
            or scope.get("user_id") != self.actor.id
            or scope.get("application_id") != "agent-platform"
            or scope.get("agent_id") != "aether"
        ):
            raise P3Error("IDENTITY_SCOPE_MISMATCH")
        return cast(dict[str, Any], result)

    def selection(self, session_id: str | None = None) -> dict[str, Any]:
        return {
            "application_id": "agent-platform",
            "agent_id": "aether",
            "user_id": self.actor.id,
            **({"session_id": identifier(session_id)} if session_id else {}),
        }

    def source_excerpts(self, pack: dict[str, Any]) -> list[dict[str, Any]]:
        """Bounded original-text expansion of sources already authorized by Recall."""
        seen: set[str] = set()
        excerpts = []
        for group in pack["groups"]:
            for item in group["items"]:
                for source in item["sources"]:
                    if source["source_id"] in seen:
                        continue
                    seen.add(source["source_id"])
                    result = self.call("POST", "/p3/sources/read-range", body=source)
                    if result["source"] != source:
                        raise P3Error("SOURCE_SCOPE_MISMATCH")
                    excerpts.append(
                        {
                            "source": source,
                            "excerpt": result["content"][:3000],
                            "truncated": not result["is_complete"] or len(result["content"]) > 3000,
                        }
                    )
                    if len(excerpts) >= 3:
                        return excerpts
        return excerpts

    def recall(
        self, query: str, operation_id: str, *, session_id: str | None = None
    ) -> dict[str, Any]:

        data = self.call(
            "POST",
            "/p3/recall",
            body={
                "query": query,
                "selection": self.selection(session_id),
                "sources": "working" if session_id else "long_term",
                "token_budget": 1800,
            },
            operation_id=operation_id,
            kind="recall.execute",
        )
        pack = ContextPack.model_validate(data)
        for scope in [
            pack.scope,
            *[item.memory.scope for group in pack.groups for item in group.items],
        ]:
            if (
                scope.tenant_id != self.actor.tenant_id
                or scope.user_id != self.actor.id
                or scope.application_id != "agent-platform"
                or scope.agent_id != "aether"
            ):
                raise P3Error("RECALL_SCOPE_MISMATCH")
        return pack.model_dump(mode="json")
