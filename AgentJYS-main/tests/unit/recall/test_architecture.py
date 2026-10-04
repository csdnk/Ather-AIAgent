"""Canonical flow implementations must work without the legacy algorithm tree."""

import ast
import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from aether_agent_memory.runtime.contract_types import utcnow
from azure_test_runtime import owned

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "src" / "aether_agent_memory"
FORBIDDEN = tuple(f"aether_agent_memory.{name}" for name in ("b1", "b2", "b3", "memory"))


def test_recall_imports_only_canonical_modules():
    for path in (PACKAGE / "recall").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not any(
                name == old or name.startswith(old + ".") for name in names for old in FORBIDDEN
            ), path


def test_flow_runs_with_legacy_packages_blocked(tmp_path):
    # A fresh process prevents sys.modules left by other tests from hiding imports.
    script = """
import asyncio
import importlib.abc
import io
import json
import runpy
import sys

class BlockLegacy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        prefixes = tuple("aether_agent_memory." + n for n in ("b1", "b2", "b3", "memory"))
        if any(fullname == p or fullname.startswith(p + ".") for p in prefixes):
            raise AssertionError("Recall loaded legacy module: " + fullname)
        return None

sys.meta_path.insert(0, BlockLegacy())
from aether_agent_memory.recall.query import RecallQueryService
from aether_agent_memory.recall.vector_projection.ports import VectorProjectionPort
from aether_agent_memory.recall.embedding import SemanticEmbeddingService
from aether_agent_memory.recall.embedding.native import NativeEmbeddingBackend
from aether_agent_memory.recall.embedding.backends import create_backend
from aether_agent_memory.recall.embedding.fastembed_client import FastEmbedClient
from aether_agent_memory.runtime.foundation.postgres import PostgresCapabilityStore
from tests.unit.recall.helpers import Backend, Inputs, runtime

payload=json.loads(sys.stdin.readline())
sys.stdin=io.StringIO(json.dumps(payload))
runpy.run_path("examples/recall_admission.py", run_name="__main__")

async def embed():
    inputs = Inputs()
    query, passage = Backend(), Backend()
    service = SemanticEmbeddingService(
        PostgresCapabilityStore(payload["postgres_dsn"]), inputs, runtime(query), runtime(passage)
    )
    try:
        for usage in ("Query", "Passage"):
            request = inputs.request(usage=usage, caller_request_ref=usage)
            result = await service.embed(request)
            assert result.usage == usage
            assert await service.vector_for(request, result) == [1.0, 2.0, 3.0]
        assert query.calls == passage.calls == 1
    finally:
        await service.close()

asyncio.run(embed())
"""
    snapshots = []
    tenant = "example-" + uuid4().hex
    for name, current, working, long_term in (
        ("combined", True, True, True),
        ("working_only", True, True, False),
        ("long_term_only", False, False, True),
        ("scope_denied", True, False, False),
        ("authority_unknown", True, None, True),
    ):
        snapshots.append(
            dict(
                scenario=name,
                authorization=dict(
                    scope=dict(
                        tenant_id=tenant,
                        project_id=None,
                        agent_id="example-agent",
                        session_id="example-session" if current else None,
                        task_id=None,
                    ),
                    principal_ref="example-principal",
                    evidence_ref="example-proof-" + name,
                    scope_valid=True,
                    working_read=working,
                    long_term_read=long_term,
                    valid_until=(utcnow() + timedelta(seconds=30)).isoformat(),
                ),
            )
        )
    payload = dict(
        postgres_dsn=owned().dsn(tmp_path / "recall-example"), authorization_snapshots=snapshots
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    rows = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    assert [row.get("mode") for row in rows[:3]] == [
        "combined",
        "working_only",
        "long_term_only",
    ]
    assert all(row["state"] == "RUNNING_REQUEST_VALIDATION" for row in rows[:3])
    assert all(not row["context_produced"] for row in rows[:3])
    assert all(row["backend"] == "postgresql" for row in rows[:3])
    assert all("rejected" in row for row in rows[3:]) and len(rows) == 5
