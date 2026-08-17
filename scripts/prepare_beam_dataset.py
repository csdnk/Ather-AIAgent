"""Create the B2 BEAM smoke and acceptance JSONL subsets from official Parquet files.

The fixed selection matches the P3 handoff scope: one 100K and one 500K
conversation for smoke, then ten conversations from each size for acceptance.
Input files are the official BEAM 100K and 500K Parquet shards.  No text is
truncated; the output keeps the original chat and probing-question payloads.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
TARGET_ROOT = ROOT / "datasets" / "p3"


def _load_parquet(path: Path) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as parquet
    except ImportError as exc:  # pragma: no cover - depends on optional data tooling
        raise SystemExit(
            "BEAM preparation requires pyarrow. Install it in the test environment with "
            "`python -m pip install pyarrow` and run this command again."
        ) from exc
    return [dict(row) for row in parquet.read_table(path).to_pylist()]


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _decode_questions(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def _first_question(value: Any) -> str:
    value = _decode_questions(value)
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("question", "content", "query", "text"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    if isinstance(value, list):
        for item in value:
            question = _first_question(item)
            if question:
                return question
    return ""


def _turns(items: Iterable[Any]) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        turns.append(
            {
                "role": str(item.get("role", "unknown")),
                "content": content,
                "turn_index": index,
                "time_anchor": item.get("time_anchor"),
                "question_type": item.get("question_type"),
            }
        )
    return turns


def _sessions(chat: Any, conversation_id: str) -> list[dict[str, Any]]:
    entries = _as_list(chat)
    if entries and all(isinstance(item, dict) for item in entries):
        entries = [entries]
    sessions: list[dict[str, Any]] = []
    for index, session in enumerate(entries):
        turns = _turns(_as_list(session))
        if turns:
            sessions.append(
                {
                    "session_id": f"{conversation_id}:session:{index}",
                    "timestamp": turns[0].get("time_anchor"),
                    "turns": turns,
                }
            )
    return sessions


def normalise(row: dict[str, Any], scale: str) -> dict[str, Any]:
    conversation_id = str(row.get("conversation_id") or row.get("id") or "unknown")
    questions = _decode_questions(row.get("probing_questions"))
    return {
        "sample_id": f"beam-{scale}-{conversation_id}",
        "dataset": "BEAM",
        "context_scale": scale,
        "conversation_id": conversation_id,
        "question": _first_question(questions),
        "sessions": _sessions(row.get("chat"), conversation_id),
        "metadata": {
            "conversation_seed": row.get("conversation_seed"),
            "probing_questions": questions,
            "source_format": "official_beam_parquet",
        },
    }


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--beam-100k", type=Path, required=True)
    parser.add_argument("--beam-500k", type=Path, required=True)
    parser.add_argument("--target-root", type=Path, default=TARGET_ROOT)
    args = parser.parse_args()

    beam_100k = [normalise(row, "100K") for row in _load_parquet(args.beam_100k)]
    beam_500k = [normalise(row, "500K") for row in _load_parquet(args.beam_500k)]
    if len(beam_100k) < 10 or len(beam_500k) < 10:
        raise SystemExit("BEAM input must contain at least 10 conversations at both 100K and 500K.")

    smoke = [beam_100k[0], beam_500k[0]]
    acceptance = beam_100k[:10] + beam_500k[:10]
    write_jsonl(args.target_root / "smoke_v0.1" / "beam.jsonl", smoke)
    write_jsonl(args.target_root / "acceptance_v0.1" / "beam.jsonl", acceptance)

    manifest_path = args.target_root / "BEAM_DATASET_MANIFEST.json"
    manifest = {
        "source": "zhangdw/Anchor-benchmarks (BEAM, CC BY-SA 4.0)",
        "input": {
            "100K": {"path": str(args.beam_100k), "sha256": sha256(args.beam_100k)},
            "500K": {"path": str(args.beam_500k), "sha256": sha256(args.beam_500k)},
        },
        "selection": {"smoke": {"100K": 1, "500K": 1}, "acceptance": {"100K": 10, "500K": 10}},
        "outputs": {"smoke": "smoke_v0.1/beam.jsonl", "acceptance": "acceptance_v0.1/beam.jsonl"},
    }
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
