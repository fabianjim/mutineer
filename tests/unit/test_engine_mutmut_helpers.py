"""Engine helpers that don't need mutmut to run: status maps, line mapping."""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, Literal

import pytest

from mutineer.engine import _launch
from mutineer.engine.mutmut_engine import (
    MutmutEngine,
    locate,
    output_tail,
    render_anchors,
    rule_first_results,
    status_for_exit_code,
)
from mutineer.models import FunctionRef, MutantResult, MutantStatus


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (1, "killed"),
        (40, "killed"),
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


def _fn(name: str = "shop.x_f") -> FunctionRef:
    return FunctionRef(file="f.py", qualname="f", mutmut_name=name, start_line=1, end_line=2)


def _result(n: int, status: MutantStatus, run: Literal["all", "rule"] = "rule") -> MutantResult:
    return MutantResult(name=f"shop.x_f__mutmut_{n}", function="shop.x_f", run=run, status=status)


def test_details_show_each_name_once_for_both_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # killed by an implementation test only: killed in "all", survived in "rule", and both
    # copies need the details of one mutmut show
    (tmp_path / "f.py").write_text("def f(x):\n    return x + 1\n", encoding="utf-8")
    shown: list[list[str]] = []

    def fake_launch(root: Path, overrides: object, args: list[str]) -> Any:
        shown.append(args[1:])
        body = "@@ -1,2 +1,2 @@\n def f(x):\n-    return x + 1\n+    return x - 1\n"
        out = "".join(f"{_launch.RECORD_SEP}{n}\n{body}" for n in args[1:])
        return subprocess.CompletedProcess(args, 0, out, "")

    engine = MutmutEngine()
    monkeypatch.setattr(engine, "_launch", fake_launch)
    results = [
        _result(1, MutantStatus.KILLED, "all"),
        _result(2, MutantStatus.SURVIVED, "all"),
        _result(1, MutantStatus.SURVIVED, "rule"),
        _result(2, MutantStatus.SURVIVED, "rule"),
    ]
    out = engine._with_details(tmp_path, results, [_fn()])
    assert shown == [["shop.x_f__mutmut_1", "shop.x_f__mutmut_2"]]
    assert out[0].mutated is None  # killed results get no details
    for r in out[1:]:
        assert (r.line, r.original, r.mutated) == (2, "return x + 1", "return x - 1")


def test_details_without_a_mutant_to_show(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def no_launch(root: Path, overrides: object, args: list[str]) -> Any:
        raise AssertionError("mutmut show ran")

    engine = MutmutEngine()
    monkeypatch.setattr(engine, "_launch", no_launch)
    results = [_result(1, MutantStatus.KILLED, "all"), _result(1, MutantStatus.KILLED, "rule")]
    assert engine._with_details(tmp_path, results, [_fn()]) == results


K, S = MutantStatus.KILLED, MutantStatus.SURVIVED


@pytest.mark.parametrize(
    ("code", "all_status", "rule_status"),
    [
        (_launch.EXIT_RULE_KILLED, K, K),  # a rule test failed first
        (1, K, S),  # another test failed, every rule test (run first) passed
        (0, S, S),
        (3, K, K),  # pytest's internal error is a kill, as mutmut has it
        (5, MutantStatus.NO_TESTS, MutantStatus.NO_TESTS),
        (33, MutantStatus.NO_TESTS, MutantStatus.NO_TESTS),
        (34, MutantStatus.SKIPPED, MutantStatus.SKIPPED),
        (35, MutantStatus.SUSPICIOUS, MutantStatus.SUSPICIOUS),
        (36, MutantStatus.TIMEOUT, MutantStatus.TIMEOUT),
        (37, MutantStatus.TYPE_CHECK, MutantStatus.TYPE_CHECK),
        (255, MutantStatus.TIMEOUT, MutantStatus.TIMEOUT),
        (-11, MutantStatus.SEGFAULT, MutantStatus.SEGFAULT),
        (2, MutantStatus.INTERRUPTED, MutantStatus.INTERRUPTED),
        (None, MutantStatus.NOT_CHECKED, MutantStatus.NOT_CHECKED),
        (99, MutantStatus.SUSPICIOUS, MutantStatus.SUSPICIOUS),
    ],
)
def test_rule_first_results_derive_both_statuses_from_one_code(
    code: int | None, all_status: MutantStatus, rule_status: MutantStatus
) -> None:
    codes = {"shop.x_f__mutmut_1": code}
    out = rule_first_results(codes, [_fn()], [_fn()])
    assert [(r.name, r.run, r.status) for r in out] == [
        ("shop.x_f__mutmut_1", "all", all_status),
        ("shop.x_f__mutmut_1", "rule", rule_status),
    ]


def test_rule_first_results_only_rule_covered_functions_get_a_rule_result() -> None:
    codes = {
        "shop.x_f__mutmut_2": 1,
        "shop.x_f__mutmut_1": _launch.EXIT_RULE_KILLED,
        "shop.x_g__mutmut_1": 1,
        "shop.x_h__mutmut_1": 0,  # not requested
    }
    out = rule_first_results(codes, [_fn(), _fn("shop.x_g")], [_fn()])
    assert [(r.name, r.run, r.status) for r in out] == [
        ("shop.x_f__mutmut_1", "all", K),
        ("shop.x_f__mutmut_2", "all", K),
        ("shop.x_g__mutmut_1", "all", K),
        ("shop.x_f__mutmut_1", "rule", K),
        ("shop.x_f__mutmut_2", "rule", S),
    ]
