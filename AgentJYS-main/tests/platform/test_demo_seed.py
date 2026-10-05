import importlib.util
import json
import os
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/platform/seed_demo_data.py"


@pytest.mark.parametrize("damaged", [False, True])
def test_real_import_recovers_a_missing_creation_receipt(tmp_path, damaged):
    configured = os.environ.get("AETHER_PLATFORM_LAB_CONFIG")
    if not configured:
        pytest.skip("explicit isolated lab required")
    importer = module()
    original = Path(configured)
    data = SCRIPT.parents[2] / "deploy/platform/demo-data.json"
    importer.seed(original, data)
    config = json.loads(original.read_text(encoding="utf-8"))
    (tmp_path / "private.json").write_text(json.dumps(config), encoding="utf-8")
    receipts = json.loads((original.parent / "created-identities.json").read_text())
    receipts.pop("user_c")
    (tmp_path / "created-identities.json").write_text(
        '{"user_c":' if damaged else json.dumps(receipts), encoding="utf-8"
    )
    (tmp_path / "test-accounts.private.json").write_bytes(
        (original.parent / "test-accounts.private.json").read_bytes()
    )
    assert importer.seed(tmp_path / "private.json", data)["accounts"] == 8
    recovered = json.loads((tmp_path / "created-identities.json").read_text())
    assert recovered["user_c"]


def module():
    assert SCRIPT.is_file(), "Repeatable demo importer is missing"
    spec = importlib.util.spec_from_file_location("demo_seed", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.parametrize(
    "dsn",
    [
        "postgresql://u:p@cloud.example:19432/aether_platform_lab",
        "postgresql://u:p@127.0.0.1:5432/production",
        "postgresql://u:p@127.0.0.1:19432/aether_platform_lab?host=cloud.example",
    ],
)
def test_importer_refuses_non_lab_databases(dsn):
    with pytest.raises(ValueError):
        module().validate_target(
            {"database_dsn": dsn, "issuer": "http://localhost:19080/realms/aether-lab"}
        )


def test_importer_accepts_only_exact_isolated_lab():
    module().validate_target(
        {
            "database_dsn": "postgresql://u:p@127.0.0.1:19432/aether_platform_lab",
            "issuer": "http://localhost:19080/realms/aether-lab",
        }
    )
