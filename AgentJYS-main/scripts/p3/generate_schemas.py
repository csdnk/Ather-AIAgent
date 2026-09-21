"""Generate committed JSON schemas, or check them without modifying files."""

import argparse
import json

from catalog import models, schema_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    mismatches = []
    for key, model in models().items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["x-contract-model"] = key
        output = json.dumps(schema, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        path = schema_path(key)
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != output:
                mismatches.append(key)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(output, encoding="utf-8")
    if mismatches:
        print("Schema drift: " + ", ".join(mismatches))
        return 1
    print(f"{len(models())} schemas {'checked' if args.check else 'generated'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
