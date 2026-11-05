"""Engine helpers that don't need mutmut to run: status map, -m rewrite, line mapping."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from mutineer.engine import _launch
from mutineer.engine.mutmut_engine import (
    locate,
    output_tail,
    render_anchors,
    rule_pass_args,
    status_for_exit_code,
)
from mutineer.models import FunctionRef, MutantStatus


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (1, "killed"),
        (3, "killed"),
        (0, "survived"),
        (5, "no_tests"),
        (33, "no_tests"),
        (34, "skipped"),
        (35, "suspicious"),
        (36, "timeout"),
        (24, "timeout"),
        (-24, "timeout"),
        (152, "timeout"),
        (255, "timeout"),
        (37, "caught_by_type_check"),
        (-11, "segfault"),
        (-9, "segfault"),
        (2, "interrupted"),
        (None, "not_checked"),
        (4, "suspicious"),
        (-15, "suspicious"),
        (99, "suspicious"),
    ],
)
def test_status_for_exit_code(code: int | None, status: str) -> None:
    assert status_for_exit_code(code) == MutantStatus(status)


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ([], ["-m", "rule"]),
        (["-x"], ["-x", "-m", "rule"]),
        (["-m", "not slow"], ["-m", "(not slow) and rule"]),
        (["-mnot slow", "-x"], ["-m", "(not slow) and rule", "-x"]),
        (["-m", "a", "-m", "b"], ["-m", "a", "-m", "(b) and rule"]),
        (["--maxfail", "1"], ["--maxfail", "1", "-m", "rule"]),
    ],
)
def test_rule_pass_args_combines_marker_expressions(args: list[str], expected: list[str]) -> None:
    assert rule_pass_args(args) == expected
    assert rule_pass_args(tuple(args)) == expected


def test_output_tail_strips_spinners_and_keeps_last_lines() -> None:
    text = "".join(f"line {i}\n" for i in range(100))
    text += "\x1b[32m⠋ 0/7\r⠙ 3/7\r7/7 done\x1b[0m\n\n"
    tail = output_tail(text)
    lines = tail.splitlines()
    assert len(lines) == 60
    assert lines[-1] == "7/7 done"
    assert lines[0] == "line 41"


def _ref(start: int, end: int, qualname: str = "f") -> FunctionRef:
    return FunctionRef(
        mutmut_name=f"m.x_{qualname}",
        file="m.py",
        qualname=qualname,
        start_line=start,
        end_line=end,
    )


HEAD = textwrap.dedent(
    """\
    X = 1


    # comment above
    def f(total):
        # inner
        if total >= 50:
            return 0
        return 5


    class C:
        @staticmethod
        def s(x):
            return x - 2
    """
).splitlines()


def test_render_anchors_start_at_comment_or_decorator() -> None:
    anchors = render_anchors("\n".join(HEAD) + "\n")
    assert anchors == {"f": 4, "C.s": 13}


def test_locate_uses_the_rendered_start_not_the_def_line() -> None:
    diff = textwrap.dedent(
        """\
        --- m.py
        +++ m.py
        @@ -1,6 +1,6 @@
         # comment above
         def f(total):
             # inner
        -    if total >= 50:
        +    if total > 50:
                 return 0
             return 5"""
    )
    d = locate(diff, HEAD, _ref(5, 9), anchor=4)
    assert (d.line, d.original, d.mutated) == (7, "if total >= 50:", "if total > 50:")


def test_locate_with_hunk_offset_and_method() -> None:
    diff = "@@ -2,2 +2,2 @@\n def s(x):\n-    return x - 2\n+    return x + 2"
    d = locate(diff, HEAD, _ref(14, 15, "C.s"), anchor=13)
    assert (d.line, d.original, d.mutated) == (15, "return x - 2", "return x + 2")


def test_locate_falls_back_to_matching_text_in_the_function() -> None:
    diff = "@@ -1,2 +1,2 @@\n-        return 5\n+        return 6"
    # a wrong anchor: the formula lands on line 5, but the text is on line 9
    d = locate(diff, HEAD, _ref(5, 9), anchor=5)
    assert d.line == 9


def test_locate_reports_no_line_when_text_is_absent() -> None:
    diff = "@@ -1,1 +1,1 @@\n-    nowhere()\n+    None"
    d = locate(diff, HEAD, _ref(5, 9), anchor=4)
    assert d.line is None
    assert (d.original, d.mutated) == ("nowhere()", "None")


def test_locate_multiline_mutation_joins_lines() -> None:
    diff = "@@ -1,3 +1,2 @@\n def f():\n-    a(\n-      1)\n+    a()"
    d = locate(diff, ["def f():", "    a(", "      1)"], _ref(1, 3), anchor=1)
    assert (d.line, d.original, d.mutated) == (2, "a(\n1)", "a()")


@pytest.mark.parametrize(
    ("version", "ok"),
    [("3.8.1", True), ("3.8.0", True), ("3.9.0", False), ("3.7.9", False), ("4.0", False)],
)
def test_launcher_version_range(version: str, ok: bool) -> None:
    assert _launch.version_ok(version) is ok


def test_launcher_rejects_bad_overrides() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "mutineer.engine._launch", "not json", "run"],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == _launch.EXIT_USAGE
    assert "not JSON" in proc.stderr


def test_preflight_without_source_paths_says_what_to_fix(tmp_path: Path) -> None:
    from mutineer.engine.mutmut_engine import MutmutEngine
    from mutineer.errors import EngineError

    with pytest.raises(EngineError, match="source_paths"):
        MutmutEngine().preflight(tmp_path)


def test_preflight_with_missing_source_path(tmp_path: Path) -> None:
    from mutineer.engine.mutmut_engine import MutmutEngine
    from mutineer.errors import EngineError

    (tmp_path / "pyproject.toml").write_text('[tool.mutmut]\nsource_paths = ["nope"]\n')
    with pytest.raises(EngineError, match="'nope' does not exist"):
        MutmutEngine().preflight(tmp_path)


def test_preflight_reads_the_projects_mutmut_config(tmp_path: Path) -> None:
    from mutineer.engine.mutmut_engine import MutmutEngine

    (tmp_path / "src/pkg").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mutmut]\nsource_paths = ["src/pkg"]\ndo_not_mutate = ["src/pkg/gen_*.py"]\n'
        'pytest_add_cli_args = ["-m", "not slow"]\n'
        'pytest_add_cli_args_test_selection = ["tests/unit"]\n'
    )
    cfg = MutmutEngine().preflight(tmp_path)
    assert cfg.source_paths == ["src/pkg"]
    assert cfg.do_not_mutate == ["src/pkg/gen_*.py"]
    assert cfg.pytest_add_cli_args == ["-m", "not slow"]
    assert cfg.pytest_add_cli_args_test_selection == ["tests/unit"]
    assert cfg.version.startswith("3.8")
