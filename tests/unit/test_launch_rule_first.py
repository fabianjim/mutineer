"""The launcher's rule-first pytest plugin, run in-process with pytester, without mutmut.

mutmut runs every mutant's tests with ``-x``, so these sessions do too.
"""

from __future__ import annotations

import pytest

from mutineer.engine import _launch

pytest_plugins = ["pytester"]

TESTS = """\
import pytest


def test_impl_a():
    pass


@pytest.mark.rule("R-1")
def test_rule_fn():
    pass


@pytest.mark.parametrize("n", [1])  # a mark with args, but no rule mark
def test_impl_b(n):
    pass


@pytest.mark.rule()
def test_empty_mark():
    pass


@pytest.mark.rule("R-2")
class TestRuleClass:
    def test_in_class(self):
        pass
"""

MODULE_MARKED = """\
import pytest

pytestmark = pytest.mark.rule("R-3")


def test_module_marked():
    pass
"""


def _run(pytester: pytest.Pytester, *args: str) -> pytest.HookRecorder:
    return pytester.inline_run(
        "-x", "-p", "no:cacheprovider", *args, plugins=[_launch._rule_first_plugin()]
    )


def _started(rec: pytest.HookRecorder) -> list[str]:
    reports = rec.getreports("pytest_runtest_logreport")
    return [r.nodeid.split("::", 1)[1] for r in reports if r.when == "setup"]


def test_rule_tests_run_first_in_their_own_order(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_a=TESTS, test_b=MODULE_MARKED)
    rec = _run(pytester)
    assert rec.ret == 0
    assert _started(rec) == [
        "test_rule_fn",
        "TestRuleClass::test_in_class",
        "test_module_marked",
        "test_impl_a",
        "test_impl_b[1]",
        "test_empty_mark",  # an empty rule mark carries no id
    ]


def test_the_projects_own_reordering_comes_first(pytester: pytest.Pytester) -> None:
    pytester.makeconftest("def pytest_collection_modifyitems(items):\n    items.reverse()\n")
    pytester.makepyfile(test_a=TESTS, test_b=MODULE_MARKED)
    rec = _run(pytester)
    assert _started(rec) == [
        "test_module_marked",
        "TestRuleClass::test_in_class",
        "test_rule_fn",
        "test_empty_mark",
        "test_impl_b[1]",
        "test_impl_a",
    ]


FAILING = """\
import pytest


@pytest.fixture
def broken():
    raise RuntimeError("setup")


def test_impl():
    assert {impl}


@pytest.mark.rule("R-1")
def test_rule():
    assert {rule}


@pytest.mark.rule("R-1")
@pytest.mark.xfail(strict=False)
def test_rule_xfail():
    assert {xfail}


@pytest.mark.rule("R-1")
def test_rule_setup({fixture}):
    pass
"""


@pytest.mark.parametrize(
    ("impl", "rule", "xfail", "fixture", "code"),
    [
        (True, True, True, "tmp_path", 0),
        (False, False, True, "tmp_path", _launch.EXIT_RULE_KILLED),  # a rule test first
        (False, True, True, "tmp_path", 1),  # every rule test passed
        (True, False, True, "tmp_path", _launch.EXIT_RULE_KILLED),
        (False, True, True, "broken", _launch.EXIT_RULE_KILLED),  # a rule test's setup
        (False, True, False, "tmp_path", 1),  # an xfail isn't a failure
    ],
)
def test_exit_40_only_when_the_first_failure_is_a_rule_tests(
    pytester: pytest.Pytester, impl: bool, rule: bool, xfail: bool, fixture: str, code: int
) -> None:
    pytester.makepyfile(FAILING.format(impl=impl, rule=rule, xfail=xfail, fixture=fixture))
    rec = _run(pytester)
    assert rec.ret == code


def test_other_exit_codes_pass_unchanged(pytester: pytest.Pytester) -> None:
    pytester.makepyfile('import pytest\n\n\n@pytest.mark.rule("R-1")\ndef test_x():\n    pass\n')
    assert _run(pytester, "-k", "nothing_matches").ret == pytest.ExitCode.NO_TESTS_COLLECTED
