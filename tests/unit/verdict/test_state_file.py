"""``.mutineer/last-run.json``: saving, loading and unreadable files"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mutineer import state
from mutineer.errors import MutineerError
from mutineer.verdict import decide


def sample(build: Any) -> state.State:
    q = build.quote
    run = build.run(
        functions=[build.fn(q)],
        results=[build.mutant(q, 1, "survived", "survived", 3)],
        tests={q: [build.rule_test]},
    )
    return state.State(
        base_ref="main",
        base="a" * 40,
        survivor_names=[f"{q}__mutmut_1"],
        engine_run=run,
        verdict=decide([build.finding("warn")], base="a" * 40, head="WORKTREE"),
    )


def test_round_trip(tmp_path: Path, build: Any) -> None:
    saved = sample(build)
    path = state.save(tmp_path, saved)
    assert path == tmp_path / ".mutineer" / "last-run.json"
    assert state.load(tmp_path) == saved
    assert [p.name for p in path.parent.iterdir()] == ["last-run.json"]


def test_no_state_file_loads_as_none(tmp_path: Path) -> None:
    assert state.load(tmp_path) is None


@pytest.mark.parametrize("text", ["{not json", '{"version": 1}', '{"version": 99, "x": 1}'])
def test_unreadable_state_says_to_run_again(tmp_path: Path, text: str) -> None:
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / ".mutineer" / "last-run.json").write_text(text)
    with pytest.raises(MutineerError, match="run `mutineer run` again"):
        state.load(tmp_path)


def test_other_state_version_says_to_run_again(tmp_path: Path, build: Any) -> None:
    state.save(tmp_path, sample(build).model_copy(update={"version": 2}))
    with pytest.raises(MutineerError, match="another Mutineer version"):
        state.load(tmp_path)
