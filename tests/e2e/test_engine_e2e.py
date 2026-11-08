"""MutmutEngine end to end on the fixture repo. Slow: runs mutmut.

Mutants are identified by their mutated source text, never by mutmut's numbering.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import textwrap
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mutineer.engine.mutmut_engine import MutmutEngine
from mutineer.models import Diff, EngineRun, MutantResult, MutantStatus, RuleMap

pytestmark = pytest.mark.slow

DiffOf = Callable[..., Diff]
K, S, N = MutantStatus.KILLED, MutantStatus.SURVIVED, MutantStatus.NO_TESTS

FAILING_TEST = "def test_broken():\n    assert 1 == 2\n"


def by_run(run: EngineRun, kind: str) -> list[MutantResult]:
    return [r for r in run.results if r.run == kind]


def survivors(run: EngineRun, kind: str) -> set[tuple[int | None, str | None]]:
    return {(r.line, r.mutated) for r in by_run(run, kind) if r.status == S}


def statuses_by_text(run: EngineRun) -> dict[str, dict[str, MutantStatus]]:
    """mutated text -> {run: status}, for mutants whose text is known in some pass."""
    text = {r.name: r.mutated for r in run.results if r.mutated is not None}
    out: dict[str, dict[str, MutantStatus]] = {}
    for r in run.results:
        if r.name in text:
            out.setdefault(text[r.name] or "", {})[r.run] = r.status
    return out


def summary(run: EngineRun) -> list[tuple[str, str, str, int | None, str | None, str | None]]:
    return sorted(
        (r.run, r.function, r.status.value, r.line, r.original, r.mutated) for r in run.results
    )


# ---- fixture branches ----


def test_agent_branch(
    shop_repo: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_repo.checkout("agent")
    run = engine.run(diff_of(shop_repo.path), ship_rule_map, shop_repo.path)

    assert not run.baseline_failed
    assert [f.mutmut_name for f in run.functions] == [
        "shop.pricing.x_discount",
        "shop.pricing.x_loyalty_points",
    ]
    assert {r.function for r in run.results} == {
        "shop.pricing.x_discount",
        "shop.pricing.x_loyalty_points",
    }
    assert by_run(run, "rule") == []  # no rule test covers pricing
    assert run.unmeasured == []  # added lines 9-10 are blank
    assert run.unreached == []
    assert run.tests_by_function == {
        "shop.pricing.x_discount": ["tests/test_pricing.py::test_member_discount"],
        "shop.pricing.x_loyalty_points": ["tests/test_pricing.py::test_points"],
    }
    assert (14, "return int(total // 11)") in survivors(run, "all")
    for r in run.results:
        if r.status != K:
            assert r.line is not None and r.original and r.mutated is not None


def test_agent_gap_two_passes(
    shop_repo: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_repo.checkout("agent-gap")
    run = engine.run(diff_of(shop_repo.path), ship_rule_map, shop_repo.path)

    assert not run.baseline_failed
    assert [f.mutmut_name for f in run.functions] == ["shop.shipping.x_quote"]
    assert [(u.file, u.line, u.end_line, u.reason) for u in run.unmeasured] == [
        ("src/shop/shipping.py", 3, 3, "module_level")
    ]
    assert run.tests_by_function["shop.shipping.x_quote"] == [
        "tests/rules/test_shipping_rules.py::test_small_order_pays",
        "tests/rules/test_shipping_rules.py::test_well_over_threshold_ships_free",
        "tests/test_express.py::test_express_adds_fee",
    ]
    all_pass, rule_pass = by_run(run, "all"), by_run(run, "rule")
    assert len(all_pass) == len(rule_pass) == 7  # x_quote is rule-covered
    assert {r.name for r in all_pass} == {r.name for r in rule_pass}

    assert survivors(run, "all") == {
        (8, "if total > FREE_SHIPPING_MIN:"),
        (10, "rate = _rate(None)"),
    }
    assert survivors(run, "rule") == {
        (8, "if total > FREE_SHIPPING_MIN:"),
        (10, "rate = _rate(None)"),
        (12, "rate = EXPRESS_FEE"),
        (12, "rate -= EXPRESS_FEE"),
    }
    # the all-pass statuses are the ones observed before the rule pass overwrote mutmut's files
    assert statuses_by_text(run) == {
        "if total > FREE_SHIPPING_MIN:": {"all": S, "rule": S},
        "rate = _rate(None)": {"all": S, "rule": S},
        "rate = EXPRESS_FEE": {"all": K, "rule": S},
        "rate -= EXPRESS_FEE": {"all": K, "rule": S},
    }
    assert sum(r.status == K for r in all_pass) == 5
    assert sum(r.status == K for r in rule_pass) == 3
    express = {r.original for r in rule_pass if r.line == 12}
    assert express == {"rate += EXPRESS_FEE"}


# ---- baseline ----


def test_failing_test_with_fresh_and_warm_mutants(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_copy.checkout("agent")
    root = shop_copy.path
    assert not (root / "mutants").exists()
    shop_copy.write("tests/test_broken.py", FAILING_TEST)
    fresh = engine.run(diff_of(root), ship_rule_map, root)
    assert fresh.baseline_failed
    assert fresh.baseline_output and "test_broken" in fresh.baseline_output
    assert fresh.results == []

    (root / "tests/test_broken.py").unlink()
    green = engine.run(diff_of(root), ship_rule_map, root)
    assert not green.baseline_failed and green.results
    assert (root / "mutants").is_dir()

    shop_copy.write("tests/test_broken.py", FAILING_TEST)
    warm = engine.run(diff_of(root), ship_rule_map, root)
    assert warm.baseline_failed
    assert warm.results == []


def test_failing_test_without_changed_functions(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    root = shop_copy.path
    green = engine.run(diff_of(root), ship_rule_map, root)
    assert green.functions == [] and not green.baseline_failed

    shop_copy.write("tests/test_broken.py", FAILING_TEST)
    run = engine.run(diff_of(root), ship_rule_map, root)
    assert run.functions == [] and run.results == []
    assert run.baseline_failed
    assert run.baseline_output and "test_broken" in run.baseline_output
    assert not (root / "mutants").exists()  # mutmut was never called


# ---- mutmut's clean test run ----

SESSION_LOGGER = """

def pytest_sessionstart(session):
    import os

    with open({log!r}, "a") as f:
        f.write(repr(os.environ.get("MUTANT_UNDER_TEST")) + "\\n")
"""


def set_isolation(root: Path, isolation: str) -> None:
    path = root / "pyproject.toml"
    text = path.read_text(encoding="utf-8")
    text = text.replace("[tool.mutmut]\n", f'[tool.mutmut]\nprocess_isolation = "{isolation}"\n')
    path.write_text(text, encoding="utf-8")


def sessions(log: Path) -> list[str]:
    """``MUTANT_UNDER_TEST`` per pytest session since the last call, then clear the log."""
    out = [eval(line) for line in log.read_text().splitlines()] if log.exists() else []
    log.unlink(missing_ok=True)
    return out


def clean_runs(seen: list[str]) -> int:
    """Clean-test sessions: ``""`` between the stats and the forced-fail session.

    The forkserver's warm-up session also has ``""``, but comes after the forced fail.
    """
    count, after_stats = 0, False
    for value in seen:
        if value == "stats":
            after_stats = True
        elif value == "fail":
            after_stats = False
        elif value == "" and after_stats:
            count += 1
    return count


@pytest.mark.parametrize(("isolation", "expected"), [("fork", 1), ("forkserver", 0)])
def test_clean_test_run_only_where_it_checks_something(
    shop_copy: Any,
    engine: MutmutEngine,
    ship_rule_map: RuleMap,
    diff_of: DiffOf,
    tmp_path: Path,
    isolation: str,
    expected: int,
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    set_isolation(root, isolation)
    log = tmp_path / "sessions.log"
    conftest = root / "tests/conftest.py"
    conftest.write_text(conftest.read_text() + SESSION_LOGGER.format(log=str(log)))

    run = engine.run(diff_of(root), ship_rule_map, root)
    assert not run.baseline_failed
    assert len(survivors(run, "rule")) == 4  # the rule pass ran, results unchanged
    seen = sessions(log)
    assert seen.count("stats") == 2  # the logger sees both passes
    # under fork: the all-tests pass only; the rule pass runs a subset of the same tests
    assert clean_runs(seen) == expected

    names = [r.name for r in by_run(run, "rule") if r.status == S]
    again = engine.rerun(names, ship_rule_map, root)
    assert not again.baseline_failed
    assert {r.status for r in again.results} == {S}
    seen = sessions(log)
    assert seen.count("stats") == 1
    assert clean_runs(seen) == expected  # recheck has no earlier pass to rely on


TEST_LOGGER = """

def pytest_runtest_logstart(nodeid, location):
    import os

    with open({log!r}, "a") as f:
        f.write(repr((os.environ.get("MUTANT_UNDER_TEST"), nodeid)) + "\\n")
"""


def add_test_logger(root: Path, log: Path) -> None:
    conftest = root / "tests/conftest.py"
    conftest.write_text(conftest.read_text() + TEST_LOGGER.format(log=str(log)))


def logged_runs(log: Path) -> list[list[tuple[str | None, str]]]:
    """``(MUTANT_UNDER_TEST, nodeid)`` per started test since the last call, one list per
    ``mutmut run`` (each starts with its stats run), then clear the log."""
    lines = log.read_text().splitlines() if log.exists() else []
    log.unlink(missing_ok=True)
    out: list[list[tuple[str | None, str]]] = []
    previous = None
    for line in lines:
        value, nodeid = ast.literal_eval(line)
        if value == "stats" and previous != "stats":
            out.append([])
        out[-1].append((value, nodeid))
        previous = value
    return out


def started(run_starts: list[tuple[str | None, str]], value: str) -> list[str]:
    return [nodeid for v, nodeid in run_starts if v == value]


@pytest.mark.parametrize("isolation", ["fork", "forkserver"])
def test_clean_and_forced_fail_runs_use_only_covering_tests(
    shop_copy: Any,
    engine: MutmutEngine,
    ship_rule_map: RuleMap,
    diff_of: DiffOf,
    tmp_path: Path,
    isolation: str,
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    set_isolation(root, isolation)
    log = tmp_path / "tests.log"
    add_test_logger(root, log)
    # collected first and covers nothing, so an unscoped forced fail starts it too
    shop_copy.write("tests/rules/test_a_first.py", "def test_first():\n    pass\n")

    run = engine.run(diff_of(root), ship_rule_map, root)
    assert not run.baseline_failed
    assert len(survivors(run, "rule")) == 4  # the rule pass ran, results unchanged
    all_pass, rule_pass = logged_runs(log)
    covering = {t for ts in run.tests_by_function.values() for t in ts}
    rule_covering = {t for t in covering if ship_rule_map.get(t)}
    assert rule_covering

    clean = started(all_pass, "")
    if isolation == "fork":
        assert set(clean) == covering
        assert len(covering) < len(set(started(all_pass, "stats")))
    else:
        assert clean == []
    assert started(rule_pass, "") == []
    for run_starts, tests in ((all_pass, covering), (rule_pass, rule_covering)):
        forced = started(run_starts, "fail")
        assert len(forced) == 1 and forced[0] in tests

    names = [r.name for r in by_run(run, "rule") if r.status == S]
    again = engine.rerun(names, ship_rule_map, root)
    assert not again.baseline_failed
    assert {r.status for r in again.results} == {S}
    (rerun_starts,) = logged_runs(log)
    clean = started(rerun_starts, "")
    assert set(clean) == (rule_covering if isolation == "fork" else set())
    forced = started(rerun_starts, "fail")
    assert len(forced) == 1 and forced[0] in rule_covering


@pytest.mark.parametrize("isolation", ["fork", "forkserver"])
def test_no_covering_tests_skips_clean_and_forced_fail_runs(
    shop_copy: Any,
    engine: MutmutEngine,
    ship_rule_map: RuleMap,
    diff_of: DiffOf,
    tmp_path: Path,
    isolation: str,
) -> None:
    root = shop_copy.path
    set_isolation(root, isolation)
    log = tmp_path / "tests.log"
    add_test_logger(root, log)
    pricing = root / "src/shop/pricing.py"
    # discount next to it is tested, so mutmut does not stop early
    pricing.write_text(pricing.read_text() + "\n\ndef surcharge(total):\n    return total + 3\n")

    run = engine.run(diff_of(root), ship_rule_map, root)
    assert not run.baseline_failed
    assert run.unreached == ["shop.pricing.x_surcharge"]
    assert run.results and all(r.status == N for r in run.results)
    (run_starts,) = logged_runs(log)
    assert started(run_starts, "stats")
    assert started(run_starts, "") == started(run_starts, "fail") == []


SWALLOW_TESTS = """\
from pkg.m import f, g


def test_a():
    assert g(2) == 4


def test_b():
    try:
        f(1)
    except Exception:
        pass
"""


def test_forced_fail_falls_back_when_covering_tests_swallow_it(
    git_repo: Any, engine: MutmutEngine, diff_of: DiffOf
) -> None:
    root = git_repo.path
    git_repo.write(
        "pyproject.toml",
        '[tool.mutmut]\nsource_paths = ["src/pkg"]\nprocess_isolation = "fork"\n\n'
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
    )
    git_repo.write("src/pkg/__init__.py", "")
    git_repo.write("src/pkg/m.py", "def g(x):\n    return x * 2\n")
    git_repo.write("tests/test_m.py", SWALLOW_TESTS)
    git_repo.write(".gitignore", "mutants/\n")
    git_repo.commit("base")
    git_repo.write("src/pkg/m.py", "def g(x):\n    return x * 2\n\n\ndef f(x):\n    return x + 1\n")

    # test_b, the only test covering f, swallows the forced failure; test_a still raises it
    run = engine.run(diff_of(root), {}, root)
    assert not run.baseline_failed
    assert run.tests_by_function == {"pkg.m.x_f": ["tests/test_m.py::test_b"]}
    assert run.results and {(r.function, r.status) for r in run.results} == {("pkg.m.x_f", S)}


TWICE_MODULE = """\
def f(x):
    return x + 1


def g(x):
    return x * 2
"""

TWICE_TESTS = """\
import helper
import pytest
from pkg.m import f, g


def test_a():
    assert g(2) == 4


@pytest.mark.rule("R-1")
def test_b():
    helper.STATE["n"] = helper.STATE.get("n", 0) + 1
    assert helper.STATE["n"] == 1
    f(1)  # result unchecked: every f mutant should survive
"""


@pytest.mark.parametrize("isolation", ["fork", "forkserver"])
def test_suite_that_passes_only_once_per_process(
    git_repo: Any, engine: MutmutEngine, diff_of: DiffOf, isolation: str
) -> None:
    root = git_repo.path
    git_repo.write(
        "pyproject.toml",
        f'[tool.mutmut]\nsource_paths = ["src/pkg"]\nprocess_isolation = "{isolation}"\n\n'
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
    )
    git_repo.write("src/pkg/__init__.py", "")
    git_repo.write("tests/helper.py", "STATE = {}\n")
    git_repo.write("tests/test_m.py", TWICE_TESTS)
    git_repo.write(".gitignore", "mutants/\n")
    git_repo.commit("base")
    git_repo.write("src/pkg/m.py", TWICE_MODULE)
    rule_map: RuleMap = {"tests/test_m.py::test_b": ["R-1"]}

    run = engine.run(diff_of(root), rule_map, root)
    f_names = ["pkg.m.x_f__mutmut_1"]
    if isolation == "fork":
        # mutants fork from the process that ran stats, where test_b can't pass again
        assert run.baseline_failed
        assert run.results == []
    else:
        assert not run.baseline_failed
        status = {(r.run, r.function, r.status) for r in run.results}
        assert status == {
            ("all", "pkg.m.x_f", S),
            ("all", "pkg.m.x_g", K),
            ("rule", "pkg.m.x_f", S),
        }
        f_names = [r.name for r in by_run(run, "rule")]

    again = engine.rerun(f_names, rule_map, root)
    if isolation == "fork":
        assert again.baseline_failed
    else:
        assert not again.baseline_failed
        assert {r.status for r in again.results} == {S}


# ---- reach and measurability ----


EXTRA = """\
def bonus(total):
    if total > 1000:
        return 50
    return 0


class Coupon:
    def apply(self, total):
        return total - 5
"""


def test_untested_new_module_is_unreached(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    root = shop_copy.path
    shop_copy.write("src/shop/extra.py", EXTRA)
    run = engine.run(diff_of(root), ship_rule_map, root)
    assert not run.baseline_failed
    assert run.unreached == ["shop.extra.x_bonus", "shop.extra.xǁCouponǁapply"]
    # mutmut stops early (no changed function has a test), but the mutants still count
    # as no_tests, so untested code lowers diff_score
    assert run.results
    assert all(r.run == "all" and r.status == N for r in run.results)
    assert {r.function for r in run.results} == set(run.unreached)
    assert all(r.line is not None and r.mutated is not None for r in run.results)
    assert (2, "if total > 1001:") in {(r.line, r.mutated) for r in run.results}


def test_untested_module_next_to_tested_change(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_copy.checkout("agent")
    root = shop_copy.path
    shop_copy.write("src/shop/extra.py", EXTRA)
    run = engine.run(diff_of(root), ship_rule_map, root)
    assert run.unreached == ["shop.extra.x_bonus", "shop.extra.xǁCouponǁapply"]
    extra = [r for r in run.results if r.function.startswith("shop.extra.")]
    assert extra and all(r.status == N for r in extra)
    assert all(r.line is not None for r in extra)


def test_changed_property_is_unmeasured(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    root = shop_copy.path
    cart = root / "src/shop/cart.py"
    text = cart.read_text()
    assert "        return sum(self.items)\n" in text
    cart.write_text(text.replace("return sum(self.items)", "return sum(self.items) + 0"))
    run = engine.run(diff_of(root), ship_rule_map, root)
    assert run.functions == []
    assert [(u.file, u.line, u.reason) for u in run.unmeasured] == [
        ("src/shop/cart.py", 12, "decorated")
    ]
    assert not run.baseline_failed


def test_function_without_mutants_is_unmeasured(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    root = shop_copy.path
    shipping = root / "src/shop/shipping.py"
    text = shipping.read_text()
    assert "def _rate(total):\n" in text
    shipping.write_text(text.replace("def _rate(total):\n", "def _rate(amount):\n"))
    run = engine.run(diff_of(root), ship_rule_map, root)
    # _rate has nothing mutmut can mutate, so mutmut is never called
    assert run.functions == []
    assert [(u.line, u.end_line, u.reason) for u in run.unmeasured] == [(12, 12, "no_mutants")]
    assert not (root / "mutants").exists()


# ---- rule pass test selection ----

SLOW_RULE_TEST = """\
import pytest
from shop.shipping import quote


@pytest.mark.slow
@pytest.mark.rule("SHIP-003")
def test_exactly_fifty_ships_free():
    assert quote(50) == 0
"""


def test_rule_pass_keeps_the_projects_marker_filter(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    shop_copy.write("tests/rules/test_slow_rule.py", SLOW_RULE_TEST)
    rule_map = {
        **ship_rule_map,
        "tests/rules/test_slow_rule.py::test_exactly_fifty_ships_free": ["SHIP-003"],
    }
    threshold = (8, "if total > FREE_SHIPPING_MIN:")

    # control: without a filter, the slow rule test runs and kills the threshold mutant
    run = engine.run(diff_of(root), rule_map, root)
    assert threshold not in survivors(run, "all")
    assert threshold not in survivors(run, "rule")

    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            "[tool.mutmut]\n", '[tool.mutmut]\npytest_add_cli_args = ["-m", "not slow"]\n'
        )
    )
    run = engine.run(diff_of(root), rule_map, root)
    # the project's -m keeps the slow rule test out; the rule pass keeps the express test out
    assert threshold in survivors(run, "all")
    assert threshold in survivors(run, "rule")
    assert (12, "rate = EXPRESS_FEE") in survivors(run, "rule")
    assert (12, "rate = EXPRESS_FEE") not in survivors(run, "all")


def test_rule_pass_keeps_a_marker_filter_from_pytest_addopts(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    shop_copy.write("tests/rules/test_slow_rule.py", SLOW_RULE_TEST)
    rule_map = {
        **ship_rule_map,
        "tests/rules/test_slow_rule.py::test_exactly_fifty_ships_free": ["SHIP-003"],
    }
    pyproject = root / "pyproject.toml"
    pyproject.write_text(
        pyproject.read_text().replace(
            "[tool.pytest.ini_options]\n",
            "[tool.pytest.ini_options]\naddopts = \"-m 'not slow'\"\n",
        )
    )
    assert "addopts" in pyproject.read_text()

    run = engine.run(diff_of(root), rule_map, root)
    threshold = (8, "if total > FREE_SHIPPING_MIN:")
    # the slow rule test runs in neither pass, so the passes agree on the threshold mutant
    assert threshold in survivors(run, "all")
    assert threshold in survivors(run, "rule")
    assert (12, "rate = EXPRESS_FEE") in survivors(run, "rule")
    assert (12, "rate = EXPRESS_FEE") not in survivors(run, "all")


# ---- rules about the engine itself ----


def _status_outside_mutineer(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--ignored", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    keep = []
    for line in out.splitlines():
        path = line[3:]
        if path.startswith(("mutants/", ".mutineer/")) or path in ("mutants", ".mutineer"):
            continue
        keep.append(line)
    return keep


@pytest.mark.rule("ENGINE-001")
def test_run_writes_only_under_mutants(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    (root / ".gitignore").write_text(".venv/\n")  # no __pycache__/, no mutants/
    shop_copy.commit("gitignore without __pycache__")
    before = _status_outside_mutineer(root)
    assert before == []

    run = engine.run(diff_of(root), ship_rule_map, root)  # both passes and mutmut show
    assert by_run(run, "rule")
    names = [r.name for r in by_run(run, "rule") if r.status == S]
    engine.rerun(names, ship_rule_map, root)
    no_change = engine.run(diff_of(root, "agent-gap"), ship_rule_map, root)  # pytest baseline
    assert no_change.functions == [] and not no_change.baseline_failed

    assert _status_outside_mutineer(root) == before
    assert not list(root.rglob("__pycache__")) or all(
        "mutants" in p.relative_to(root).parts for p in root.rglob("__pycache__")
    )


STALE_TEST = """\
from shop.pricing import discount, loyalty_points


def test_stale_pins_everything():
    assert discount(101, True) == 90.9
    assert discount(100, True) == 100
    assert discount(150, False) == 150
    assert loyalty_points(0) == 0
    assert loyalty_points(1) == 0
    assert loyalty_points(25) == 2
"""


@pytest.mark.rule("ENGINE-002")
def test_stale_mutants_dir_from_another_branch(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    root = shop_copy.path
    shop_copy.checkout("agent")
    reference = engine.run(diff_of(root), ship_rule_map, root)
    shutil.rmtree(root / "mutants")

    # leave mutmut's cache from agent-gap, plus a stale test copy that would kill every
    # pricing mutant if it were run
    shop_copy.checkout("agent-gap")
    engine.run(diff_of(root), ship_rule_map, root)
    (root / "mutants/tests/test_zzz_stale.py").write_text(STALE_TEST)
    shop_copy.git("checkout", "-q", "-f", "agent")  # keeps the ignored mutants/
    assert (root / "mutants/src/shop/shipping.py.meta").is_file()

    run = engine.run(diff_of(root), ship_rule_map, root)
    assert summary(run) == summary(reference)
    assert not any(r.function == "shop.shipping.x_quote" for r in run.results)
    assert (2, "if member or total > 100:") in survivors(run, "all")
    assert all("stale" not in t for ts in run.tests_by_function.values() for t in ts)


# ---- rerun ----

THRESHOLD_TEST = """\
import pytest
from shop.shipping import quote


@pytest.mark.rule("SHIP-003")
def test_exactly_fifty_ships_free():
    assert quote(50) == 0
"""


def test_rerun_reports_only_requested_names(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    first = engine.run(diff_of(root), ship_rule_map, root)
    rule_survivors = {r.name: r.mutated for r in by_run(first, "rule") if r.status == S}
    assert len(rule_survivors) == 4

    shop_copy.write("tests/rules/test_threshold.py", THRESHOLD_TEST)
    unknown = "shop.shipping.x_nope__mutmut_1"
    again = engine.rerun([*rule_survivors, unknown], ship_rule_map, root)

    assert again.functions == [] and again.unmeasured == []
    assert not again.baseline_failed
    assert {r.name for r in again.results} == {*rule_survivors, unknown}
    assert all(r.run == "rule" for r in again.results)
    status = {rule_survivors.get(r.name, "unknown"): r.status for r in again.results}
    assert status == {
        "if total > FREE_SHIPPING_MIN:": K,
        "rate = _rate(None)": S,
        "rate = EXPRESS_FEE": S,
        "rate -= EXPRESS_FEE": S,
        "unknown": MutantStatus.NOT_CHECKED,
    }
    lines = {r.mutated: r.line for r in again.results if r.status == S}
    assert lines == {"rate = _rate(None)": 10, "rate = EXPRESS_FEE": 12, "rate -= EXPRESS_FEE": 12}


def test_rerun_with_failing_test(
    shop_copy: Any, engine: MutmutEngine, ship_rule_map: RuleMap, diff_of: DiffOf
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    first = engine.run(diff_of(root), ship_rule_map, root)
    names = [r.name for r in by_run(first, "rule") if r.status == S]
    shop_copy.write(
        "tests/rules/test_broken_rule.py",
        'import pytest\n\n\n@pytest.mark.rule("SHIP-003")\ndef test_broken():\n    assert 1 == 2\n',
    )
    again = engine.rerun(names, ship_rule_map, root)
    assert again.baseline_failed
    assert again.baseline_output and "test_broken" in again.baseline_output


# ---- ground truth: predicted functions == what mutmut generates ----

CORPUS = '''\
"""Module docstring."""
import enum
from functools import lru_cache

CONST = 1 + 1


def plain(x):
    return x + 1


# comment above
def commented(x):
    # inner comment
    return x - 1


async def coro(x):
    return x * 2


def gen(x):
    yield x + 1


def outer(x):
    def inner(y):
        return y * 2

    return inner(x) + 1


@lru_cache
def cached(x):
    return x + 3


def noop():
    pass


def returns_name(x):
    return x


class Box:
    size = 3 + 4

    def __init__(self, v):
        self.v = v + 1

    def method(self, x):
        return self.v + x

    @property
    def prop(self):
        return self.v * 2

    @staticmethod
    def stat(x):
        return x - 2

    @classmethod
    def clsm(cls, x):
        return cls(x + 5)

    def __setattr__(self, k, v):
        object.__setattr__(self, k, v + 0 if isinstance(v, int) else v)

    class Inner:
        def m(self):
            return 1 + 1


class Color(enum.Enum):
    RED = 1

    def _generate_next_value_(name, start, count, last):
        return count + 1

    def label(self):
        return self.name + "!"


if CONST > 1:

    def conditional(x):
        return x + 9
'''


def test_predicted_functions_match_what_mutmut_generates(
    git_repo: Any, engine: MutmutEngine, diff_of: DiffOf
) -> None:
    root = git_repo.path
    git_repo.write(
        "pyproject.toml",
        '[tool.mutmut]\nsource_paths = ["src/pkg"]\n\n'
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
    )
    git_repo.write("src/pkg/__init__.py", "")
    git_repo.write(
        "tests/test_plain.py",
        "from pkg.tricky import plain\n\n\ndef test_plain():\n    assert plain(1) == 2\n",
    )
    git_repo.write(".gitignore", "mutants/\n")
    git_repo.commit("base")
    git_repo.write("src/pkg/tricky.py", CORPUS)  # untracked: every line is added

    run = engine.run(diff_of(root), {}, root)
    predicted = {f.mutmut_name for f in run.functions}

    # independently: a plain full mutmut run, then the functions in its results file
    shutil.rmtree(root / "mutants")
    subprocess.run(
        [sys.executable, "-m", "mutineer.engine._launch", "{}", "run"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    meta = json.loads((root / "mutants/src/pkg/tricky.py.meta").read_text())
    actual = {k.partition("__mutmut_")[0] for k in meta["exit_code_by_key"]}

    assert predicted == actual
    assert predicted == {
        f"pkg.tricky.{n}"
        for n in (
            "x_plain",
            "x_commented",
            "x_coro",
            "x_gen",
            "x_outer",
            "xǁBoxǁ__init__",
            "xǁBoxǁmethod",
            "xǁBoxǁstat",
            "xǁBoxǁclsm",
            "xǁColorǁlabel",
        )
    }
    lines = CORPUS.splitlines()

    def reason_of(text: str) -> str:
        line = lines.index(text) + 1
        hits = [
            u.reason
            for u in run.unmeasured
            if u.line is not None and u.line <= line <= (u.end_line or u.line)
        ]
        assert len(hits) == 1, (text, hits)
        return hits[0]

    assert reason_of("CONST = 1 + 1") == "module_level"
    assert reason_of("    return x + 3") == "decorated"
    assert reason_of("        return self.v * 2") == "decorated"
    assert reason_of("            return 1 + 1") == "module_level"
    assert reason_of("        return x + 9") == "module_level"
    assert reason_of("    pass") == "no_mutants"
    assert reason_of("    return x") == "no_mutants"
    assert reason_of("        object.__setattr__(self, k, v + 0 if isinstance(v, int) else v)") == (
        "no_mutants"
    )
    assert reason_of("        return count + 1") == "no_mutants"
    assert all(u.line != 1 for u in run.unmeasured)  # the module docstring is ignored
    assert textwrap.dedent(CORPUS) == CORPUS
