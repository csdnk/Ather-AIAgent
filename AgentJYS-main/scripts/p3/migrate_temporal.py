"""Inspect first; explicitly apply only to the stopped original P3 directory."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from aether_agent_memory.runtime.temporal.migration import (  # noqa: E402
    MigrationReport,
    apply_migration,
    inspect_migration,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="read-only inspection; writes report only")
    inspect.add_argument("--config", type=Path, required=True)
    inspect.add_argument("--report", type=Path, required=True)
    apply = sub.add_parser("apply", help="atomic offline handoff to Temporal")
    apply.add_argument("--report", type=Path, required=True)
    apply.add_argument("--deployment-id", required=True)
    apply.add_argument(
        "--old-service-stopped-and-autostart-disabled",
        action="store_true",
        help="attest the original service is stopped; a copy is insufficient",
    )
    args = parser.parse_args()
    if args.command == "inspect":
        result = inspect_migration(args.database, config_path=args.config)
        with args.report.open("x", encoding="utf-8") as output:
            output.write(result.model_dump_json(indent=2))
        print(json.dumps({"blockers": result.blockers, "states": result.state_counts}))
        return 2 if result.blockers else 0
    report = MigrationReport.model_validate_json(args.report.read_text(encoding="utf-8"))
    result = apply_migration(
        args.database,
        report,
        args.deployment_id,
        legacy_service_stopped=args.old_service_stopped_and_autostart_disabled,
    )
    print(result.model_dump_json(indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
