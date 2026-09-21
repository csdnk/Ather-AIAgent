"""Contract acceptance only; no test here claims database/cloud/Provider execution."""

import ast
import copy
import inspect
import json
import re
from hashlib import sha256
from importlib import import_module

import pytest
import rfc8785
import yaml
from catalog import FLOWS, ROOT, models, schema_path
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import ValidationError

MODELS = models()
CASES = json.loads((ROOT / "contracts/p3/fixtures/cases.json").read_text(encoding="utf-8"))
CASES_BY_ID = {case["id"]: case for case in CASES}


def read_yaml(path):
    return yaml.safe_load((ROOT / path).read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_wire_and_semantic_examples(case):
    model = MODELS[case["model"]]
    payload = json.dumps(case["payload"], ensure_ascii=False)
    schema = json.loads(schema_path(case["model"]).read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema, format_checker=FormatChecker())
    assert validator.is_valid(case["payload"]) == case["schema_valid"]
    if case["valid"]:
        parsed = model.model_validate_json(payload)
        assert model.model_validate_json(parsed.model_dump_json()) == parsed
    else:
        with pytest.raises(ValidationError):
            model.model_validate_json(payload)


def test_every_model_has_positive_and_negative_examples():
    for key in MODELS:
        assert any(c["model"] == key and c["valid"] for c in CASES)
        assert any(c["model"] == key and not c["valid"] for c in CASES)
    assert len(CASES_BY_ID) == len(CASES)


def test_schema_set_is_exact():
    assert {p.stem for p in (ROOT / "contracts/p3/schemas").glob("*.json")} == set(MODELS)
    for path in (ROOT / "contracts/p3/schemas").glob("*.json"):
        Draft202012Validator.check_schema(json.loads(path.read_text(encoding="utf-8")))


def test_every_protocol_method_has_owner_consumers_and_examples():
    actual = {}
    for flow in FLOWS:
        module = import_module(f"aether_agent_memory.{flow}.contracts.ports")
        for name, cls in vars(module).items():
            if not inspect.isclass(cls) or cls.__module__ != module.__name__:
                continue
            for method, fn in vars(cls).items():
                if method.startswith("_") or not inspect.isfunction(fn):
                    continue
                actual[f"{flow}.{name}.{method}"] = str(inspect.signature(fn))
    catalog = read_yaml("contracts/p3/interface-catalog.yaml")["interfaces"]
    assert len(catalog) == len(actual)
    assert {entry["id"]: entry["signature"] for entry in catalog} == actual
    for entry in catalog:
        assert entry["provider"] and entry["consumers"] and entry["rules"]
        assert entry["examples"] and all(x in CASES_BY_ID for x in entry["examples"])
        assert entry["implemented"] is False


def test_http_mapping_is_consistent_and_has_no_admin_or_scheduling_product():
    api = read_yaml("contracts/p3/http-api.yaml")
    ports = {x["id"] for x in read_yaml("contracts/p3/interface-catalog.yaml")["interfaces"]}
    keys = set()
    for endpoint in api["endpoints"]:
        key = endpoint["method"], endpoint["path"]
        assert key not in keys
        keys.add(key)
        assert endpoint["port"] in ports
        for field in ("request_model", "response_model", "query_model"):
            if endpoint[field] is not None:
                assert endpoint[field] in MODELS
        assert endpoint["example"] in CASES_BY_ID
        assert endpoint["path_parameters"] == re.findall(r"\{(\w+)\}", endpoint["path"])
        assert not any(x in endpoint["path"] for x in ("/operate", "/admin", "/roles", "/ops"))
    assert ("GET", "/api/v2/recalls/{recall_id}/result") in keys
    from aether_agent_memory.runtime.contracts.models import ErrorCode

    assert {code for codes in api["errors"].values() for code in codes} == set(ErrorCode)


def test_dependency_direction_and_no_runtime_implementations():
    allowed = {
        "runtime": {"runtime"},
        "remember": {"runtime", "remember"},
        "recall": {"runtime", "remember", "recall"},
        "operate": {"runtime", "remember", "operate"},
    }
    for flow in FLOWS:
        directory = ROOT / "src/aether_agent_memory" / flow / "contracts"
        for path in directory.glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    if node.module.startswith("aether_agent_memory."):
                        assert node.module.split(".")[1] in allowed[flow]
                        assert ".contracts." in node.module
                    assert node.module.split(".")[0] not in {"sqlite3", "httpx", "pymilvus"}
            if path.name == "ports.py":
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        assert len(node.body) == 1
                        assert isinstance(node.body[0], ast.Expr)
                        assert isinstance(node.body[0].value, ast.Constant)
                        assert node.body[0].value.value is Ellipsis


def test_state_edges_do_not_resurrect_or_blindly_resubmit():
    from aether_agent_memory.operate.contracts.rules import ACTION_TRANSITIONS
    from aether_agent_memory.recall.contracts.rules import RECALL_TRANSITIONS
    from aether_agent_memory.remember.contracts.rules import MEMORY_TRANSITIONS
    from aether_agent_memory.runtime.contracts.rules import TASK_TRANSITIONS

    assert not MEMORY_TRANSITIONS["deleted"]
    assert "active" not in MEMORY_TRANSITIONS["superseded"]
    assert "active" in MEMORY_TRANSITIONS["archived"]
    assert "generated" not in ACTION_TRANSITIONS["unknown"]
    assert "submitted" not in ACTION_TRANSITIONS["unknown"]
    assert not TASK_TRANSITIONS["succeeded"]
    assert not TASK_TRANSITIONS["attention_required"]
    assert "recovery_wait" in TASK_TRANSITIONS["running"]
    assert not RECALL_TRANSITIONS["completed"]


def test_requirement_traceability_and_deferred_product_metrics():
    data = read_yaml("docs/p3/acceptance/requirements.yaml")
    requirements = data["requirements"]
    scenarios = read_yaml("docs/p3/acceptance/scenarios.yaml")["scenarios"]
    ids = {r["id"] for r in requirements}
    scenario_ids = {s["id"] for s in scenarios}
    inventory = read_yaml("docs/p3/acceptance/source-manifest.yaml")["numbered_sections"]
    assert len(ids) == len(requirements)
    assert {f"FR{i:02d}" for i in range(1, 19)} <= ids
    assert {r["source"]["section"] for r in requirements} == {s["section"] for s in inventory}
    assert data["source"]["version"] == "1.2"
    ports = {x["id"] for x in read_yaml("contracts/p3/interface-catalog.yaml")["interfaces"]}
    for req in requirements:
        assert req["runtime_status"] == "not_run"
        assert req["owner"] in {"RF", "B", "A", "C"}
        assert set(req["acceptance_scenarios"]) <= scenario_ids
        assert req["contract_interfaces"] and set(req["contract_interfaces"]) <= ports
        assert all((ROOT / p).exists() for p in req["design_documents"])
        assert req["source"]["paragraph_start"] <= req["source"]["paragraph_end"]
    for scenario in scenarios:
        assert set(scenario["requirements"]) <= ids
        assert scenario["steps"] and scenario["expected"] and scenario["evidence"]
        assert scenario["status"] == "not_run"
    profile = read_yaml("contracts/p3/profiles/product-acceptance.yaml")
    assert profile["required_execution_mode"] == "real"
    assert profile["enforce_now"] is False
    assert profile["status"] == "deferred_by_user_not_current_gate"


def test_delivered_markdown_links_are_portable_and_resolve():
    paths = list((ROOT / "docs/p3").rglob("*.md")) + [ROOT / "contracts/p3/README.md"]
    for path in paths:
        content = path.read_text(encoding="utf-8")
        assert content.count("```") % 2 == 0, path
        for target in re.findall(r"\]\(([^)]+)\)", content):
            if target.startswith(("https://", "http://", "#")):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (path, target)


def test_event_payload_catalog_and_subject_binding():
    from event_contract import validate_event

    for event in read_yaml("contracts/p3/events.yaml")["events"]:
        payload = copy.deepcopy(CASES_BY_ID[event["example"]]["payload"])
        parsed = MODELS[event["payload_model"]].model_validate_json(json.dumps(payload))
        envelope = copy.deepcopy(CASES_BY_ID["positive-runtime.EventEnvelope"]["payload"])
        envelope.update(event_type=event["type"], producer=event["producer"], payload=payload)
        envelope["payload_hash"] = sha256(rfc8785.dumps(payload)).hexdigest()
        envelope["subject"].update(
            owner=event["subject_owner"],
            object_type=event["subject_type"],
            object_id=(
                parsed.memory.memory_id if event["subject_type"] == "memory" else parsed.action_id
            ),
            scope=parsed.memory.scope.model_dump(mode="json"),
            version=parsed.memory.version if event["subject_version_path"] else None,
        )
        assert validate_event(envelope) == parsed
        assert event["consumers"] and event["idempotency"]
        for field, value in [("object_id", "wrong_object"), ("owner", "runtime")]:
            bad = copy.deepcopy(envelope)
            bad["subject"][field] = value
            with pytest.raises(ValueError):
                validate_event(bad)
        for field, value in [("producer", "unregistered"), ("event_type", "unknown.event")]:
            bad = copy.deepcopy(envelope)
            bad[field] = value
            with pytest.raises(ValueError):
                validate_event(bad)
        bad = copy.deepcopy(envelope)
        bad["subject"]["scope"]["tenant_id"] = "other_tenant"
        with pytest.raises(ValueError):
            validate_event(bad)
        bad = copy.deepcopy(envelope)
        bad["payload"]["memory"]["scope"]["tenant_id"] = "other_tenant"
        with pytest.raises(ValueError):
            validate_event(bad)
        bad["payload_hash"] = sha256(rfc8785.dumps(bad["payload"])).hexdigest()
        with pytest.raises(ValueError):
            validate_event(bad)
