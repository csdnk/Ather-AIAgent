from __future__ import annotations

import json

import pytest

from aether_agent_memory.b1.dataset_runner import _detect_format, iter_records


@pytest.mark.unit
def test_reads_json_array_and_items_wrapper(tmp_path) -> None:
    array_path = tmp_path / "array.json"
    array_path.write_text(json.dumps([{"text": "one"}, {"text": "two"}]), encoding="utf-8")
    wrapper_path = tmp_path / "wrapper.json"
    wrapper_path.write_text(json.dumps({"items": [{"text": "three"}]}), encoding="utf-8")

    assert [record for _, record in iter_records(array_path, "json")] == [
        {"text": "one"},
        {"text": "two"},
    ]
    assert [record for _, record in iter_records(wrapper_path, "json")] == [{"text": "three"}]


@pytest.mark.unit
def test_reads_jsonl_csv_tsv_and_detects_extensions(tmp_path) -> None:
    jsonl_path = tmp_path / "records.ndjson"
    jsonl_path.write_text('{"text":"one"}\n\n{"text":"two"}\n', encoding="utf-8")
    csv_path = tmp_path / "records.csv"
    csv_path.write_text("id,text\n1,one\n", encoding="utf-8")
    tsv_path = tmp_path / "records.tsv"
    tsv_path.write_text("id\ttext\n2\ttwo\n", encoding="utf-8")

    assert _detect_format(jsonl_path, "auto") == "jsonl"
    assert list(iter_records(jsonl_path, "jsonl"))[1][1]["text"] == "two"
    assert list(iter_records(csv_path, "csv"))[0][1] == {"id": "1", "text": "one"}
    assert list(iter_records(tsv_path, "tsv"))[0][1] == {"id": "2", "text": "two"}


@pytest.mark.unit
def test_rejects_unknown_extension_and_non_object_jsonl(tmp_path) -> None:
    unknown = tmp_path / "records.txt"
    unknown.write_text("[]", encoding="utf-8")
    bad_jsonl = tmp_path / "bad.jsonl"
    bad_jsonl.write_text("[]\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cannot detect"):
        _detect_format(unknown, "auto")
    with pytest.raises(ValueError, match="must be a JSON object"):
        list(iter_records(bad_jsonl, "jsonl"))
