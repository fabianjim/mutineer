"""The pytest plugin's ``--mutineer-rulemap`` option, run in-process with pytester."""

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
