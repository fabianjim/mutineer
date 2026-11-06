"""The pytest plugin's ``--mutineer-rulemap`` option, run in-process with pytester"""

from __future__ import annotations

import json

pytest_plugins = ["pytester"]


def _rule_map(pytester, *args):
    out = pytester.path / "map.json"
    result = pytester.runpytest(
        "--collect-only", "-q", "-p", "no:cacheprovider", f"--mutineer-rulemap={out}", *args
    )
    assert result.ret in (0, 5), result.stdout.str()
    return json.loads(out.read_text())


def test_function_class_and_module_marks_are_merged(pytester):
    pytester.makepyfile(
        test_shop="""
        import pytest

        pytestmark = pytest.mark.rule("MOD-001")

        @pytest.mark.rule("FN-001", "FN-002")
        @pytest.mark.rule("FN-003")
        def test_fn():
            pass

        @pytest.mark.rule("CLS-001")
        class TestThing:
            @pytest.mark.rule("FN-001")
            def test_method(self):
                pass

            def test_plain(self):
                pass
        """
    )
    assert _rule_map(pytester) == {
        "test_shop.py::test_fn": ["FN-001", "FN-002", "FN-003", "MOD-001"],
        "test_shop.py::TestThing::test_method": ["CLS-001", "FN-001", "MOD-001"],
        "test_shop.py::TestThing::test_plain": ["CLS-001", "MOD-001"],
    }


def test_unmarked_tests_are_left_out(pytester):
    pytester.makepyfile(
        test_a="""
        import pytest

        def test_plain():
            pass

        @pytest.mark.rule("A-001")
        def test_marked():
            pass
        """
    )
    assert _rule_map(pytester) == {"test_a.py::test_marked": ["A-001"]}


def test_parametrized_cases_appear_once_each_with_brackets(pytester):
    pytester.makepyfile(
        test_p="""
        import pytest

        @pytest.mark.rule("P-001")
        @pytest.mark.parametrize("x", [1, 2])
        def test_p(x):
            pass

        @pytest.mark.parametrize("y", [pytest.param(3, marks=pytest.mark.rule("P-002")), 4])
        def test_case_marks(y):
            pass
        """
    )
    assert _rule_map(pytester) == {
        "test_p.py::test_p[1]": ["P-001"],
        "test_p.py::test_p[2]": ["P-001"],
        "test_p.py::test_case_marks[3]": ["P-002"],
    }


def test_deselection_does_not_hide_rule_tests(pytester):
    pytester.makeini("[pytest]\naddopts = -m 'not slow' -k 'not hidden'\n")
    pytester.makepyfile(
        test_d="""
        import pytest

        @pytest.mark.slow
        @pytest.mark.rule("D-001")
        def test_slow_rule():
            pass

        @pytest.mark.rule("D-002")
        def test_hidden_rule():
            pass
        """
    )
    assert _rule_map(pytester) == {
        "test_d.py::test_slow_rule": ["D-001"],
        "test_d.py::test_hidden_rule": ["D-002"],
    }


def test_empty_collection_writes_an_empty_map(pytester):
    assert _rule_map(pytester) == {}


def test_without_the_option_nothing_is_written(pytester):
    pytester.makepyfile(
        test_n="""
        import pytest

        @pytest.mark.rule("N-001")
        def test_n():
            pass
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "--strict-markers")
    result.assert_outcomes(passed=1)
    assert not list(pytester.path.rglob("*.json"))


##
# --mutineer-rule-only: the rule pass's test selection

RULE_ONLY_TESTS = """
import pytest

@pytest.mark.rule("R-001")
def test_rule():
    pass

@pytest.mark.slow
@pytest.mark.rule("R-002")
def test_slow_rule():
    pass

def test_impl():
    pass

@pytest.mark.rule()
def test_empty_marker():
    pass

@pytest.mark.parametrize("x", [pytest.param(1, marks=pytest.mark.rule("R-003")), 2])
def test_cases(x):
    pass
"""


def test_rule_only_runs_just_the_tests_with_a_rule_id(pytester):
    pytester.makepyfile(test_r=RULE_ONLY_TESTS)
    result = pytester.runpytest("-p", "no:cacheprovider", "-v", "--mutineer-rule-only")
    result.assert_outcomes(passed=3, deselected=3)
    out = result.stdout.str()
    for node in ("test_r.py::test_rule", "test_r.py::test_slow_rule", "test_r.py::test_cases[1]"):
        assert f"{node} PASSED" in out
    for node in ("test_impl", "test_empty_marker", "test_cases[2]"):
        assert f"{node} PASSED" not in out


def test_rule_only_keeps_the_projects_marker_filter(pytester):
    pytester.makeini("[pytest]\naddopts = -m 'not slow'\n")
    pytester.makepyfile(test_r=RULE_ONLY_TESTS)
    result = pytester.runpytest("-p", "no:cacheprovider", "-v", "--mutineer-rule-only")
    result.assert_outcomes(passed=2, deselected=4)
    assert "test_slow_rule PASSED" not in result.stdout.str()


def test_rule_only_reports_deselected_tests_to_other_plugins(pytester):
    # mutmut lists tests as collected minus `pytest_deselected`, as for `-m`
    pytester.makepyfile(test_r=RULE_ONLY_TESTS)
    pytester.makeconftest(
        """
        seen = []

        def pytest_deselected(items):
            seen.extend(item.nodeid for item in items)

        def pytest_collection_finish(session):
            print("DESELECTED", sorted(seen))
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "-s", "--mutineer-rule-only")
    result.stdout.fnmatch_lines(
        [
            "DESELECTED ['test_r.py::test_cases[2]', 'test_r.py::test_empty_marker', "
            "'test_r.py::test_impl']"
        ]
    )


def test_rule_map_still_lists_rule_tests_the_rule_only_option_keeps(pytester):
    pytester.makeini("[pytest]\naddopts = -m 'not slow'\n")
    pytester.makepyfile(test_r=RULE_ONLY_TESTS)
    assert _rule_map(pytester, "--mutineer-rule-only") == {
        "test_r.py::test_rule": ["R-001"],
        "test_r.py::test_slow_rule": ["R-002"],
        "test_r.py::test_cases[1]": ["R-003"],
    }
