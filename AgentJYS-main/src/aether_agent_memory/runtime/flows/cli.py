"""Installable entry point for the unified service and explicit deployment setup."""

import argparse
import json
import os
from pathlib import Path

import yaml

from .config import ServiceConfiguration


def initialize(directory: Path, *, template: Path) -> Path:
    """Create an external deployment from a reviewed complete Azure template."""
    value = yaml.safe_load(template.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("storage_mode") != "azure":
        raise ValueError("init requires an explicit complete Azure storage template")
    if directory.exists() and any(directory.iterdir()):
        raise FileExistsError("deployment directory must be empty")
    configuration = ServiceConfiguration.load(template)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "service.yaml"
    with path.open("x", encoding="utf-8") as output:
        yaml.safe_dump(configuration.model_dump(mode="json"), output, sort_keys=False)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Persistent P3 memory service")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init", help="create a deployment outside the source checkout")
    init.add_argument("--directory", type=Path, required=True)
    init.add_argument(
        "--template", type=Path, required=True, help="reviewed complete Azure configuration"
    )
    serve = commands.add_parser("serve", help="run HTTP and persistent background workers")
    serve.add_argument("--config", type=Path, required=True)
    serve.add_argument(
        "--require-profile",
        choices=["development", "test", "staging", "production"],
        help="refuse startup unless the configuration matches this deployment environment",
    )
    check = commands.add_parser(
        "check-config", help="validate deployment configuration without starting services"
    )
    check.add_argument("--config", type=Path, required=True)
    migration = commands.add_parser(
        "migrate-execution", help="audit pinned bindings; optionally apply hybrid marker"
    )
    migration.add_argument("--config", type=Path, required=True)
    migration.add_argument("--apply", action="store_true")
    worker = commands.add_parser("celery-worker", help="run the separate Linux Remember worker")
    worker.add_argument("--config", type=Path, required=True)
    worker.add_argument("--concurrency", type=int, default=2)
    beat = commands.add_parser("celery-beat", help="run one Remember periodic wakeup owner")
    beat.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "init":
        path = initialize(args.directory, template=args.template)
        print(
            json.dumps({"configuration": str(path), "storage": "azure", "production_ready": False})
        )
        return 0
    config = ServiceConfiguration.load(args.config)
    if args.command == "migrate-execution":
        from aether_agent_memory.runtime.celery.migration import inspect_bindings
        from aether_agent_memory.runtime.foundation.postgres import PostgresUnitOfWork

        if config.azure_storage is None:
            parser.error("migration requires the configured PostgreSQL storage")
        uow = PostgresUnitOfWork(
            config.azure_storage.postgres.resolve_dsn(), config.data_dir / "migration"
        )
        try:
            print(json.dumps(inspect_bindings(uow, config, apply=args.apply)))
        finally:
            uow.close()
        return 0
    if args.command in {"celery-worker", "celery-beat"}:
        os.environ["AETHER_SERVICE_CONFIG"] = str(args.config.resolve())
        from aether_agent_memory.runtime.celery.app import app

        if args.command == "celery-worker":
            app.worker_main(
                ["worker", "--pool=prefork", f"--concurrency={args.concurrency}", "--loglevel=INFO"]
            )
        else:
            app.start(["beat", "--schedule=/tmp/p3-celerybeat", "--loglevel=INFO"])
        return 0
    if args.command == "check-config":
        from .config import IdentityConfiguration

        IdentityConfiguration.model_validate(
            yaml.safe_load(config.identity_file.read_text(encoding="utf-8"))
        )
        print(json.dumps({"valid": True, "profile": config.profile}))
        return 0
    if args.require_profile is not None and config.profile != args.require_profile:
        parser.error(
            f"deployment requires profile {args.require_profile}; "
            f"configuration uses {config.profile}"
        )
    import uvicorn

    os.environ["AETHER_SERVICE_CONFIG"] = str(args.config.resolve())
    uvicorn.run(
        "aether_agent_memory.runtime.flows.application:application",
        factory=True,
        host=config.host,
        port=config.port,
        workers=1,
    )
    return 0
