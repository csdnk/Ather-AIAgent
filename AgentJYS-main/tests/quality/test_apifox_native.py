import importlib.util
from pathlib import Path

PATH = Path(__file__).parents[2] / "scripts/quality/apifox_native.py"


def load():
    spec = importlib.util.spec_from_file_location("native", PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guard_does_not_activate_a_target_without_environment():
    guard = load().guard("private_readonly", "/p3/live", "p3_base")
    assert guard.index("127.0.0.1:1/BLOCKED") < guard.index("environment_kind")
    assert "skipRequest" in guard
    assert "new URL" in guard


def test_environment_never_exports_credentials():
    env = load().environment("isolated_acceptance", {"123": "http://127.0.0.1:1"})
    for var in env["variables"]:
        if any(key in var["name"] for key in ["password", "token"]):
            assert var["value"] == var["initialValue"] == ""
            assert not var["isSync"]


def test_native_step_contains_independent_assertions():
    step = load().step("probe", "get", "/p3/live", 123, "", "pm.test('alive',()=>{});")
    assert step["httpApiCase"]["path"] == "http://127.0.0.1:1/BLOCKED"
    assert step["httpApiCase"]["postProcessors"][0]["data"].startswith("pm.test")
    assert not step["bind"]
