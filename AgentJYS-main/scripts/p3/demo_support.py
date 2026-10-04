"""Configured development scenarios with durable admission evidence."""

import argparse
import asyncio
import json
from pathlib import Path
from uuid import uuid4

from aether_agent_memory.runtime.flows.application import Service
from aether_agent_memory.runtime.flows.config import ServiceConfiguration
from aether_agent_memory.runtime.foundation.common import now


def provider_name(provider):
    return f"{type(provider).__module__}.{type(provider).__qualname__}"


class DemoRun:
    def __init__(self, service, token, directory):
        self.service, self.token, self.directory = service, token, directory
        directory.mkdir(parents=True, exist_ok=False)
        self.journal = directory / "admissions.json"
        self.run_id, self.started_at = "demo-" + uuid4().hex, now()
        self.contexts, self.commands = {}, {}
        self.record()

    async def start(self):
        execution = self.service.execution
        await execution.start()
        try:
            async with asyncio.timeout(30):
                while execution.ready()["readiness"] != "ready":
                    await asyncio.sleep(0.1)
        except TimeoutError as error:
            raise TimeoutError(f"Temporal startup not ready: {execution.state}") from error
        execution.require_ready()

    def context(self, operation="inspect"):
        return self.service.runtime.foundation.identity.context(
            self.token, timeout_seconds=300, operation_id=self.run_id + "-" + operation
        )

    def record(self):
        data = {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "commands": self.commands,
            "production_acceptance": False,
        }
        temporary = self.journal.with_suffix(".pending")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(self.journal)

    async def command(self, operation, kind, payload):
        ctx = self.contexts[operation] = self.context(operation)
        entry = {"operation_id": ctx.operation_id, "kind": kind, "payload": payload}
        if operation in self.commands:
            raise ValueError("command already admitted; inspect its original job")
        self.commands[operation] = entry
        # Retain the original operation even if admission itself loses its reply.
        self.record()
        try:
            execution = self.service.execution
            job = execution.accept(ctx, kind, payload)
            entry["job_id"] = job.job_id
            self.record()
            result = await execution.await_result(ctx, job.job_id, 120)
            entry["result_available"] = True
            self.record()
            return job, result
        except BaseException as error:
            entry["error_type"] = type(error).__name__
            self.record()
            raise

    def providers(self):
        runtime = self.service.runtime
        return {
            "metadata": provider_name(runtime.foundation.uow),
            "objects": provider_name(runtime.remember.bodies.p2),
            "vectors": provider_name(runtime.vectors),
            "cache": provider_name(runtime.remember.bodies.cache),
            "models": provider_name(runtime.remember.extraction),
        }

    def finish(self, result):
        evidence = {
            **result,
            "production_acceptance": False,
            "backend": "temporal",
            "providers": self.providers(),
            "admissions": str(self.journal),
            "job_ids": [entry["job_id"] for entry in self.commands.values() if "job_id" in entry],
        }
        path = self.directory / "evidence.json"
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return {**evidence, "evidence": str(path)}


async def run_configured(directory, config_path, credential_file, scenario):
    config = ServiceConfiguration.load(config_path)
    if config.profile not in {"development", "test"}:
        raise ValueError("mutating demos require a dedicated development/test deployment")
    if directory.exists():
        raise FileExistsError("demo evidence already exists; inspect the original admissions")
    token = credential_file.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("demo credential file is empty")
    service = Service(config)
    try:
        return await scenario(DemoRun(service, token, directory))
    finally:
        await service.close()


def arguments(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--credential-file", required=True, type=Path)
    parser.add_argument("--directory", required=True, type=Path)
    return parser.parse_args()
