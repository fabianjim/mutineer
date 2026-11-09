"""``run`` reuses the last verdict while ``fresh`` says so, on the shop fixture (slow)

``agent-gap`` has rule-covered survivors, so both mutation passes run. A sentinel file in
``mutants/`` tells a reused run (``mutants/`` untouched) from a real one (deleted).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mutineer.cli import main

pytestmark = pytest.mark.slow

SENTINEL = "mutants/sentinel.txt"


def without_duration(report: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in report.items() if k != "duration_s"}


def put_sentinel(root: Path) -> Path:
    path = root / SENTINEL
    path.parent.mkdir(exist_ok=True)
    path.write_text("left by the test\n")
    return path


def fresh(root: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any) -> tuple[int, str]:
    monkeypatch.chdir(root)
    capsys.readouterr()
    code = main(["fresh"])
    return code, capsys.readouterr().out


def test_run_reuses_the_verdict_until_something_changes(
    shop_copy: Any, mutineer: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    root = shop_copy.path
    shop_copy.git("checkout", "-q", "agent-gap")
    assert fresh(root, monkeypatch, capsys)[0] == 1
    code, first = mutineer(root, "run")
    assert (code, first["status"]) == (0, "warn")
    assert first["rule_score"] is not None

    # nothing changed: fresh, and the next run reuses
    assert fresh(root, monkeypatch, capsys) == (0, "fresh\n")
    sentinel = put_sentinel(root)
    code, second = mutineer(root, "run")
    assert code == 0
    assert sentinel.exists()
    assert without_duration(second) == without_duration(first)

    # --no-reuse runs for real and gives the same report
    code, third = mutineer(root, "run", "--no-reuse")
    assert code == 0
    assert not sentinel.exists()
    assert without_duration(third) == without_duration(first)
    assert fresh(root, monkeypatch, capsys) == (0, "fresh\n")

    # an edited source file: stale, the run is real, then fresh again
    shipping = root / "src/shop/shipping.py"
    shipping.write_text(shipping.read_text() + "\n# reviewed\n")
    code, out = fresh(root, monkeypatch, capsys)
    assert code == 1 and "src/shop/shipping.py" in out
    sentinel = put_sentinel(root)
    mutineer(root, "run")
    assert not sentinel.exists()
    assert fresh(root, monkeypatch, capsys) == (0, "fresh\n")

    # after a recheck the next run is real
    mutineer(root, "recheck")
    code, out = fresh(root, monkeypatch, capsys)
    assert code == 1 and out.startswith("stale:")
    sentinel = put_sentinel(root)
    mutineer(root, "run")
    assert not sentinel.exists()
    assert fresh(root, monkeypatch, capsys) == (0, "fresh\n")
