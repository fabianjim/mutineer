"""Static rule-test identity (build spec section 8, WP1: testscan)."""

from __future__ import annotations

import pytest

from mutineer.testscan import is_test_file, scan_source


def _rules(src: str, path: str = "tests/test_x.py") -> dict[str, tuple[str, ...]]:
    return {t.qualname: t.rules for t in scan_source(src, path)}


def test_function_marker():
    src = 'import pytest\n\n@pytest.mark.rule("A-001")\ndef test_a():\n    pass\n'
    assert _rules(src) == {"test_a": ("A-001",)}


def test_from_pytest_import_mark():
    src = 'from pytest import mark\n\n@mark.rule("A-001")\ndef test_a():\n    pass\n'
    assert _rules(src) == {"test_a": ("A-001",)}


def test_mark_without_the_pytest_import_is_not_a_rule_marker():
    src = 'from mylib import mark\n\n@mark.rule("A-001")\ndef test_a():\n    pass\n'
    assert _rules(src) == {"test_a": ()}


def test_class_marker_applies_to_methods():
    src = (
        'import pytest\n\n@pytest.mark.rule("A-001")\nclass TestA:\n'
        '    @pytest.mark.rule("B-002")\n    def test_x(self):\n        pass\n\n'
        "    def test_y(self):\n        pass\n"
    )
    assert _rules(src) == {"TestA::test_x": ("A-001", "B-002"), "TestA::test_y": ("A-001",)}


@pytest.mark.parametrize(
    "value",
    [
        'pytest.mark.rule("A-001")',
        '[pytest.mark.rule("A-001"), pytest.mark.slow]',
        '(pytest.mark.rule("A-001"),)',
    ],
    ids=["single", "list", "tuple"],
)
def test_module_pytestmark(value):
    src = f"import pytest\n\npytestmark = {value}\n\n\ndef test_a():\n    pass\n"
    assert _rules(src) == {"test_a": ("A-001",)}


def test_class_body_pytestmark():
    src = (
        'import pytest\n\nclass TestA:\n    pytestmark = pytest.mark.rule("A-001")\n\n'
        "    def test_x(self):\n        pass\n"
    )
    assert _rules(src) == {"TestA::test_x": ("A-001",)}


def test_several_ids_and_stacked_markers_merge():
    src = (
        'import pytest\n\npytestmark = pytest.mark.rule("C-003")\n\n'
        '@pytest.mark.rule("B-002", "A-001")\n@pytest.mark.rule("A-001")\n'
        "def test_a():\n    pass\n"
    )
    assert _rules(src) == {"test_a": ("A-001", "B-002", "C-003")}


def test_aliased_pytest_import():
    src = 'import pytest as pt\n\n@pt.mark.rule("A-001")\ndef test_a():\n    pass\n'
    assert _rules(src) == {"test_a": ("A-001",)}


def test_what_counts_as_a_test():
    src = (
        "def helper():\n    pass\n\n"
        "def test_a():\n    def test_inner():\n        pass\n\n"
        "async def test_async():\n    pass\n\n"
        "class Helper:\n    def test_not_collected(self):\n        pass\n\n"
        "class TestOuter:\n    def helper(self):\n        pass\n\n"
        "    def test_m(self):\n        pass\n\n"
        "    class TestInner:\n        def test_n(self):\n            pass\n"
    )
    assert sorted(_rules(src)) == [
        "TestOuter::TestInner::test_n",
        "TestOuter::test_m",
        "test_a",
        "test_async",
    ]


def test_nested_class_inherits_outer_marks():
    src = (
        'import pytest\n\n@pytest.mark.rule("A-001")\nclass TestOuter:\n'
        "    class TestInner:\n        def test_n(self):\n            pass\n"
    )
    assert _rules(src) == {"TestOuter::TestInner::test_n": ("A-001",)}


def test_line_is_the_def_line_and_key_uses_the_path():
    src = 'import pytest\n\n\n@pytest.mark.rule("A-001")\ndef test_a():\n    assert 1\n'
    (test,) = scan_source(src, "tests/rules/test_r.py")
    assert test.line == 5
    assert test.key == "tests/rules/test_r.py::test_a"
    assert test.assertions[0].line == 6


def test_non_literal_rule_ids_are_ignored():
    src = 'import pytest\nRID = "A-001"\n\n@pytest.mark.rule(RID)\ndef test_a():\n    pass\n'
    assert _rules(src) == {"test_a": ()}


def test_syntax_error_raises():
    with pytest.raises(SyntaxError):
        scan_source("def test_a(:\n", "tests/test_x.py")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("tests/test_a.py", True),
        ("tests/sub/a_test.py", True),
        ("tests/conftest.py", False),
        ("tests/helpers.py", False),
        ("src/test_a.py", False),
        ("testsuite/test_a.py", False),
    ],
)
def test_is_test_file(path, expected):
    assert is_test_file(path, ["tests"]) is expected


def test_is_test_file_with_dot_test_path():
    assert is_test_file("anywhere/test_a.py", ["."])
