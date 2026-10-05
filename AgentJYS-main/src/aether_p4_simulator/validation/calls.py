"""Safe HTTP observations and the installed P3 route inventory (not API success)."""

import re

from pydantic import BaseModel, ConfigDict

from aether_agent_memory.runtime.contracts.client_runs import ClientOperation

OperationCall = ClientOperation

ROUTES = (
    ("GET", "/p3/client-runs/{run_id}/recoveries/{transfer_id}/definition"),
    ("PUT", "/p3/client-runs/{run_id}/recoveries/{transfer_id}/definition"),
    ("GET", "/p3/client-runs/{run_id}/recoveries/{transfer_id}/inputs/{operation_id}"),
    ("PUT", "/p3/client-runs/{run_id}/recoveries/{transfer_id}/inputs/{operation_id}"),
    ("GET", "/p3/client-runs/{run_id}/recoveries/{transfer_id}/states/{sequence}"),
    ("POST", "/p3/client-runs/{run_id}/recoveries/{transfer_id}"),
    ("GET", "/p3/client-runs/{run_id}/recoveries/{transfer_id}"),
    ("POST", "/p3/client-runs/{run_id}/transfers/{transfer_id}"),
    ("GET", "/p3/client-runs/{run_id}/transfers/{transfer_id}"),
    ("GET", "/p3/auth/config"),
    ("GET", "/p3/auth/me"),
    ("GET", "/p3/live"),
    ("GET", "/p3/readyz"),
    ("GET", "/p3/runtime"),
    ("GET", "/p3/health"),
    ("GET", "/p3/ready"),
    ("GET", "/p3/traces"),
    ("GET", "/p3/tasks"),
    ("GET", "/p3/logs/{trace_id}"),
    ("GET", "/p3/tasks/{task_id}"),
    ("GET", "/p3/tasks/{task_id}/progress"),
    ("POST", "/p3/recovery"),
    ("POST", "/p3/tasks/{task_id}/control"),
    ("GET", "/p3/controls/{operation_id}"),
    ("GET", "/p3/periodic/control"),
    ("POST", "/p3/periodic/control"),
    ("GET", "/p3/incidents"),
    ("POST", "/p3/maintenance/cycle"),
    ("PUT", "/p3/configuration"),
    ("POST", "/p3/backups"),
    ("POST", "/p3/restore-drills"),
    ("POST", "/p3/remember"),
    ("POST", "/p3/recall"),
    ("GET", "/p3/operations/{job_id}"),
    ("GET", "/p3/operation-requests/{operation_id}"),
    ("GET", "/p3/mutation-receipts/{operation_id}"),
    ("GET", "/p3/mutation-receipts/{operation_id}/result"),
    ("GET", "/p3/operations/{job_id}/result"),
    ("GET", "/p3/recalls/{recall_id}"),
    ("GET", "/p3/recalls/{recall_id}/result"),
    ("POST", "/p3/remember/consolidate"),
    ("POST", "/p3/remember/reflection"),
    ("GET", "/p3/remember/reflection"),
    ("POST", "/p3/remember/distill"),
    ("GET", "/p3/remember/{memory_id}"),
    ("GET", "/p3/remember/{memory_id}/processing"),
    ("POST", "/p3/remember/body"),
    ("POST", "/p3/remember/body/range"),
    ("POST", "/p3/sources/read-range"),
    ("POST", "/p3/remember/{memory_id}/correct"),
    ("GET", "/p3/remember/{memory_id}/retention"),
    ("POST", "/p3/remember/{memory_id}/retention"),
    ("POST", "/p3/remember/{memory_id}/lifecycle"),
    ("POST", "/p3/remember/{memory_id}/delete"),
    ("POST", "/p3/remember/{memory_id}/reprocess"),
    ("POST", "/p3/remember/{memory_id}/reindex"),
    ("POST", "/p3/sources/{source_id}/delete"),
    ("POST", "/p3/sources/{source_id}/revoke"),
    ("GET", "/p3/capabilities"),
    ("PUT", "/p3/documents/{document_id}"),
    ("GET", "/p3/memories"),
    ("GET", "/p3/operate/memories/{memory_id}"),
    ("POST", "/p3/client-runs/{run_id}"),
    ("GET", "/p3/client-runs/{run_id}"),
    ("PUT", "/p3/client-runs/{run_id}"),
    ("PUT", "/p3/client-runs/{run_id}/inputs/{operation_id}"),
    ("GET", "/p3/client-runs/{run_id}/inputs/{operation_id}"),
    ("PUT", "/p3/client-runs/{run_id}/definition"),
    ("GET", "/p3/client-runs/{run_id}/definition"),
    ("PUT", "/p3/client-runs/{run_id}/states/{sequence}"),
    ("GET", "/p3/client-runs/{run_id}/states/{sequence}"),
)
_MATCHERS = [
    (method, path, re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", path) + "$"))
    for method, path in ROUTES
    if "{" in path
]


def route_template(method: str, path: str) -> str:
    if (method, path) in ROUTES:
        return path
    return next(
        (
            template
            for verb, template, pattern in _MATCHERS
            if verb == method and pattern.fullmatch(path)
        ),
        "/unlisted",
    )


class ApiCall(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)
    method: str
    path: str
    status_code: int | None
    elapsed_ms: float
    operation_id: str | None = None
