"""Write the JSON schemas in docs/schema/ from the models, or check they are current.

uv run python scripts/export_schema.py          # write
uv run python scripts/export_schema.py --check  # exit 1 if any is stale (CI)
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mutineer.models import agent_json_schema, verdict_json_schema

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "docs" / "schema"
SCHEMAS: dict[str, Callable[[], dict[str, Any]]] = {
    "verdict.schema.json": verdict_json_schema,
    "agent.schema.json": agent_json_schema,
}


def render(schema: dict[str, Any]) -> str:
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str], schema_dir: Path = SCHEMA_DIR) -> int:
    texts = {schema_dir / name: render(build()) for name, build in SCHEMAS.items()}
    if "--check" in argv:
        stale = [
            path.name
            for path, text in texts.items()
            if (path.read_text(encoding="utf-8") if path.exists() else "") != text
        ]
        for name in stale:
            print(
                f"{name} is out of date; run `uv run python scripts/export_schema.py`",
                file=sys.stderr,
            )
        return 1 if stale else 0
    schema_dir.mkdir(parents=True, exist_ok=True)
    for path, text in texts.items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
