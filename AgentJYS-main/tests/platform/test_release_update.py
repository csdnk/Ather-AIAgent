"""Release cutover preserves recovery files and refuses unrelated PVC consumers."""

import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "platform_release", Path(__file__).parents[2] / "deploy/platform/update_release.py"
)
assert SPEC and SPEC.loader
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


def layout(tmp_path):
    root = tmp_path / "release"
    stage = root / "releases" / "new"
    for base, label in [(root, "old"), (stage, "new")]:
        for name in ("app", "deps"):
            (base / name).mkdir(parents=True)
            (base / name / (label + ".py")).write_text(label)
        (base / "files.sha256.json").write_text(label)
    return root, stage


def test_cutover_removes_obsolete_code_and_dependencies_without_deleting_backup(tmp_path):
    root, stage = layout(tmp_path)
    release.cutover(root, stage)
    for name in ("app", "deps"):
        assert list((root / name).iterdir()) == [root / name / "new.py"]
        assert (stage / "previous" / name / "old.py").read_text() == "old"
    release.restore(root, stage)
    assert (root / "app" / "old.py").read_text() == "old"
    assert (root / "deps" / "old.py").read_text() == "old"
    assert (root / "files.sha256.json").read_text() == "old"
    assert (stage / "failed-app" / "new.py").read_text() == "new"


def test_partial_cutover_can_restore_when_second_rename_fails(tmp_path, monkeypatch):
    root, stage = layout(tmp_path)
    original = Path.rename

    def interrupted(path, target):
        if path == stage / "app":
            raise OSError("simulated interruption after moving old app")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", interrupted)
    with pytest.raises(OSError):
        release.cutover(root, stage)
    release.restore(root, stage)
    for name in ("app", "deps"):
        assert (root / name / "old.py").read_text() == "old"
    assert (root / "files.sha256.json").read_text() == "old"


def test_missing_stage_part_does_not_touch_active_release(tmp_path):
    root, stage = layout(tmp_path)
    (stage / "files.sha256.json").unlink()
    with pytest.raises(RuntimeError, match="Incomplete"):
        release.cutover(root, stage)
    assert (root / "app" / "old.py").exists()
    assert (root / "deps" / "old.py").exists()


def pod(name, owner):
    return {
        "metadata": {"name": name, "ownerReferences": [{"uid": owner}]},
        "spec": {"volumes": [{"persistentVolumeClaim": {"claimName": "aether-platform-release"}}]},
    }


def test_unrelated_pvc_consumer_is_rejected_even_with_matching_application_name():
    with pytest.raises(RuntimeError, match="another consumer"):
        release.check_consumers([pod("aether-platform-other", "other-rs")], {"platform-rs"})
    release.check_consumers([pod("platform", "platform-rs")], {"platform-rs"})


def test_final_cutover_check_requires_all_application_pods_gone():
    with pytest.raises(RuntimeError):
        release.check_consumers([pod("platform", "platform-rs")], set(), "updater")
    release.check_consumers([pod("updater", "")], set(), "updater")
