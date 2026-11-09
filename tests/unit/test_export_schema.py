"""scripts/export_schema.py writes both schemas and ``--check`` names a stale one"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

# mutmut runs the tests from mutants/, which has no scripts/, so look upward for it
SCRIPT = next(
    p / "scripts" / "export_schema.py"
    for p in Path(__file__).resolve().parents
    if (p / "scripts" / "export_schema.py").is_file()
)


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("export_schema", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_writes_both_schemas_and_check_passes(tmp_path: Path) -> None:
    script = load_script()
    assert script.main([], tmp_path) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"verdict.schema.json", "agent.schema.json"}
    assert script.main(["--check"], tmp_path) == 0


@pytest.mark.parametrize("name", ["verdict.schema.json", "agent.schema.json"])
@pytest.mark.parametrize("stale", ["edited", "missing"])
def test_check_fails_naming_the_stale_file(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], name: str, stale: str
) -> None:
    script = load_script()
    script.main([], tmp_path)
    capsys.readouterr()
    if stale == "missing":
        (tmp_path / name).unlink()
    else:
        (tmp_path / name).write_text("{}\n", encoding="utf-8")
    assert script.main(["--check"], tmp_path) == 1
    err = capsys.readouterr().err
    assert name in err
    other = ({"verdict.schema.json", "agent.schema.json"} - {name}).pop()
    assert other not in err


def test_checked_in_schemas_are_current() -> None:
    assert load_script().main(["--check"]) == 0
