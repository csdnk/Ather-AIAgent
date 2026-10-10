"""Build traceable Apifox data from frozen public sources, never product state."""

import argparse
import csv
import hashlib
import json
from pathlib import Path

SOURCES = {
    "nasa": (
        "nasa_power_nanjing_202401.json",
        "4a653854f5fb9177735974c3ff6f3f4a49aa2df7656b9dba3d328d18eb956d47",
        "https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M,PRECTOTCORR&community=AG&longitude=118.7969&latitude=32.0603&start=20240101&end=20240131&format=JSON",
    ),
    "worldbank": (
        "worldbank_china_cereal_yield.json",
        "cdb734df05ba7b37d540fd6ae6cad0f12602aa1c337b095f5a06448da96ba584",
        "https://api.worldbank.org/v2/country/CHN/indicator/AG.YLD.CREL.KG?date=2020:2023&format=json",
    ),
    "rfc": (
        "rfc9110.txt",
        "21c1cdce6ab0e5509b04d84a28000836c7a087cf786efe6f04877ebfff47232a",
        "https://www.rfc-editor.org/rfc/rfc9110.txt",
    ),
}


def verify_bytes(data, expected):
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("source hash mismatch; freeze a new dataset version before use")
    return data


def build(source_dir):
    source_dir = Path(source_dir)
    loaded = {
        key: verify_bytes((source_dir / name).read_bytes(), digest).decode("utf-8-sig")
        for key, (name, digest, url) in SOURCES.items()
    }
    items = []
    nasa = json.loads(loaded["nasa"])
    assert nasa["parameters"]["T2M"]["units"] == "C"
    assert nasa["parameters"]["PRECTOTCORR"]["units"] == "mm/day"
    for day in ["20240101", "20240102"]:
        temp = nasa["properties"]["parameter"]["T2M"][day]
        rain = nasa["properties"]["parameter"]["PRECTOTCORR"][day]
        text = f"NASA POWER frozen snapshot: requested Nanjing coordinates 32.0603 N, 118.7969 E; date {day} (LST); T2M {temp} C; PRECTOTCORR {rain} mm/day."  # noqa: E501
        items.append(
            (
                f"nasa-{day}",
                "nasa",
                text,
                f"What is T2M for {day} at the requested Nanjing coordinates?",
                f"{temp} C",
            )
        )
    wb = json.loads(loaded["worldbank"])
    for item in sorted(wb[1], key=lambda x: x["date"]):
        text = f"World Bank frozen snapshot, China, indicator AG.YLD.CREL.KG (cereal yield): year {item['date']}, value {item['value']} kilograms per hectare."  # noqa: E501
        items.append(
            (
                f"worldbank-{item['date']}",
                "worldbank",
                text,
                f"What is the cereal yield in China in {item['date']}?",
                f"{item['value']} kilograms per hectare",
            )
        )
    rfc = loaded["rfc"].replace("\r\n", "\n")
    marker = "9.2.2.  Idempotent Methods"
    start = rfc.index(marker, rfc.index(marker) + 1) if rfc.count(marker) > 1 else rfc.index(marker)
    end = rfc.index("9.2.3.", start)
    excerpt = rfc[start:end].strip()
    assert "PUT, DELETE, and safe request methods" in excerpt
    items.append(
        (
            "rfc-idempotency",
            "rfc",
            excerpt,
            "Which methods does RFC 9110 section 9.2.2 call idempotent?",
            "PUT, DELETE, and safe request methods",
        )
    )
    rows = []
    for account in ["aetherusera", "aetheruserb"]:
        for key, source, text, query, fact in items:
            rows.append(
                {
                    "dataset_id": f"{key}-{account}",
                    "dataset_kind": "real_public_source",
                    "tenant_account": account,
                    "text": text,
                    "question": query,
                    "expected_fact": fact,
                    "source_url": SOURCES[source][2],
                    "source_sha256": SOURCES[source][1],
                    "expected_behavior": "save_read_recall_same_source",
                }
            )
        for length in [0, 1, 512, 8192, 65536]:
            rows.append(
                {
                    "dataset_id": f"length-{length}-{account}",
                    "dataset_kind": "synthetic_boundary",
                    "tenant_account": account,
                    "text": "测" * length,
                    "question": "",
                    "expected_fact": "",
                    "source_url": "",
                    "source_sha256": "",
                    "expected_behavior": "reject_empty_content"
                    if length == 0
                    else "measure_contract_response_no_assumed_maximum",
                }
            )
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = build(args.source_dir)
    args.output.mkdir(parents=True, exist_ok=False)
    for kind, filename in [
        ("real_public_source", "真实公开数据"),
        ("synthetic_boundary", "合成边界数据"),
    ]:
        selected = [row for row in rows if row["dataset_kind"] == kind]
        with (args.output / (filename + ".csv")).open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(selected)
        (args.output / (filename + ".json")).write_text(
            json.dumps(selected, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    manifest = {
        "schema_version": 1,
        "dataset_version": "20261010.1",
        "real_rows": 14,
        "synthetic_rows": 10,
        "status": "DATA_PREPARED_NOT_PRODUCT_EXECUTED",
        "sources": SOURCES,
        "files": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in args.output.iterdir()
        },
    }
    (args.output / "数据清单.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"real_rows": 14, "synthetic_rows": 10, "source_hashes_verified": True}))


if __name__ == "__main__":
    main()
