"""Prepare locally available B2 datasets into the handoff directory layout."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT.parent / "datasets" / "B2_B3"
TARGET_ROOT = ROOT / "datasets" / "p3"


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def prepare_locomo() -> list[dict]:
    source = SOURCE_ROOT / "locomo" / "locomo.csv"
    rows: list[dict] = []
    with source.open(encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle):
            payload = json.loads(item["turns"])
            turns = [
                {"speaker": speaker, "content": content, "turn_index": index}
                for index, (speaker, content) in enumerate(
                    zip(payload["speaker_role"], payload["utterance"], strict=True)
                )
            ]
            rows.append({"sample_id": f"locomo-{item['dialogue_id']}", "session_id": item["dialogue_id"], "turns": turns})
    return rows


def prepare_longmemeval() -> list[dict]:
    source = SOURCE_ROOT / "longmemeval" / "longmemeval_oracle"
    items = json.loads(source.read_text(encoding="utf-8"))
    normalized = []
    for item in items:
        sessions = []
        for index, session in enumerate(item.get("haystack_sessions", [])):
            session_id = item.get("haystack_session_ids", [])[index]
            session_date = item.get("haystack_dates", [])[index] if index < len(item.get("haystack_dates", [])) else None
            sessions.append({"session_id": session_id, "timestamp": session_date, "turns": session})
        normalized.append({
            "sample_id": item["question_id"],
            "question_id": item["question_id"],
            "question_type": item["question_type"],
            "question": item["question"],
            "answer": item["answer"],
            "evidence_session_ids": item.get("haystack_session_ids", []),
            "sessions": sessions,
        })
    return normalized


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    locomo = prepare_locomo()
    longmemeval = prepare_longmemeval()
    for name, rows in (("locomo", locomo), ("longmemeval", longmemeval)):
        write_jsonl(TARGET_ROOT / "smoke_v0.1" / f"{name}.jsonl", rows[:2 if name == "locomo" else 10])
        write_jsonl(TARGET_ROOT / "acceptance_v0.1" / f"{name}.jsonl", rows[:10 if name == "locomo" else 100])
    beam_ready = all(
        (TARGET_ROOT / subset / "beam.jsonl").is_file()
        for subset in ("smoke_v0.1", "acceptance_v0.1")
    )
    manifest = {
        "source_root": str(SOURCE_ROOT),
        "available": {"locomo": len(locomo), "longmemeval_oracle": len(longmemeval)},
        "missing": (["mem2act"] if beam_ready else ["beam", "mem2act"]),
        "note": "Acceptance files are local subsets; they are not the full official acceptance set.",
    }
    manifest_path = TARGET_ROOT / "LOCAL_DATASET_MANIFEST.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
