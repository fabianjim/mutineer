"""MutmutEngine end to end on the fixture repo (build spec WP3 acceptance). Slow: runs mutmut.

Mutants are identified by their mutated source text, never by mutmut's numbering.
"""

from __future__ import annotations

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
    # The all-pass statuses are the ones observed before the rule pass overwrote mutmut's files.
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
    # as no_tests, so untested code lowers diff_score.
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

    # Control: without a filter, the slow rule test runs and kills the threshold mutant.
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
    # -m "(not slow) and rule": the slow rule test stays out, and so does the express test.
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

    # Leave mutmut's cache from agent-gap, plus a stale test copy that would kill every
    # pricing mutant if it were run.
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

    # Independently: a plain full mutmut run, then the functions in its results file.
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
