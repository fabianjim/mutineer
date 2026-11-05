"""Write docs/schema/verdict.schema.json from the models, or check it is current.

uv run python scripts/export_schema.py          # write
uv run python scripts/export_schema.py --check  # exit 1 if stale (CI)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mutineer.models import verdict_json_schema

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "docs" / "schema" / "verdict.schema.json"


def render() -> str:
    return json.dumps(verdict_json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        current = SCHEMA_PATH.read_text(encoding="utf-8") if SCHEMA_PATH.exists() else ""
        if current != text:
            print(
                f"{SCHEMA_PATH.name} is out of date; run `uv run python scripts/export_schema.py`",
                file=sys.stderr,
            )
            return 1
        return 0
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
