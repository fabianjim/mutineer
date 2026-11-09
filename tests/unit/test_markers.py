"""``markers.add_rule_marker``: one rule mark added with libcst, everything else kept"""

from __future__ import annotations

import pytest

from mutineer.errors import RulesError
from mutineer.markers import add_rule_marker
from mutineer.testscan import scan_source


def _rules(text, qualname):
    (test,) = [t for t in scan_source(text, "tests/test_x.py") if t.qualname == qualname]
    return test.rules


def test_plain_function_gets_the_mark_above_the_def():
    src = "import pytest\n\n\n# a comment\ndef test_a():\n    assert 1\n"
    out = add_rule_marker(src, "test_a", "PRICE-001")
    assert out == (
        'import pytest\n\n\n# a comment\n@pytest.mark.rule("PRICE-001")\ndef test_a():\n'
        "    assert 1\n"
    )
    assert _rules(out, "test_a") == ("PRICE-001",)


def test_method_in_a_test_class_gets_the_mark():
    src = (
        "import pytest\n\n\nclass TestA:\n    def test_a(self):\n        assert 1\n\n"
        "    def test_b(self):\n        assert 2\n"
    )
    out = add_rule_marker(src, "TestA::test_b", "PRICE-001")
    assert out == src.replace(
        "    def test_b", '    @pytest.mark.rule("PRICE-001")\n    def test_b'
    )
    assert _rules(out, "TestA::test_a") == ()


def test_the_mark_goes_above_existing_decorators():
    src = (
        "import pytest\n\n\n"
        '@pytest.mark.parametrize("x", [1, 2])\n'
        '@pytest.mark.rule("SHIP-003")\n'
        "def test_a(x):\n    assert x\n"
    )
    out = add_rule_marker(src, "test_a", "PRICE-001")
    assert out == src.replace(
        "@pytest.mark.parametrize", '@pytest.mark.rule("PRICE-001")\n@pytest.mark.parametrize'
    )
    assert _rules(out, "test_a") == ("PRICE-001", "SHIP-003")


@pytest.mark.parametrize(
    "src",
    [
        'import pytest\n\n\n@pytest.mark.rule("PRICE-001")\ndef test_a():\n    pass\n',
        'import pytest\n\n\n@pytest.mark.rule("PRICE-001")\nclass TestA:\n'
        "    def test_a(self):\n        pass\n",
        'import pytest\n\npytestmark = [pytest.mark.rule("PRICE-001")]\n\n\n'
        "def test_a():\n    pass\n",
    ],
)
def test_an_id_that_already_applies_changes_nothing(src):
    qualname = "TestA::test_a" if "class" in src else "test_a"
    assert add_rule_marker(src, qualname, "PRICE-001") is src


def test_an_import_alias_is_reused():
    src = "import pytest as pt\n\n\ndef test_a():\n    pass\n"
    out = add_rule_marker(src, "test_a", "PRICE-001")
    assert out == src.replace("def test_a", '@pt.mark.rule("PRICE-001")\ndef test_a')
    assert _rules(out, "test_a") == ("PRICE-001",)


def test_from_pytest_import_mark_is_reused():
    src = "from pytest import mark\n\n\ndef test_a():\n    pass\n"
    out = add_rule_marker(src, "test_a", "PRICE-001")
    assert out == src.replace("def test_a", '@mark.rule("PRICE-001")\ndef test_a')
    assert _rules(out, "test_a") == ("PRICE-001",)


def test_no_pytest_import_adds_one_after_the_other_imports():
    src = (
        '"""Docstring."""\n\nfrom __future__ import annotations\n\nfrom shop import x\n\n\n'
        "def test_a():\n    assert x\n"
    )
    out = add_rule_marker(src, "test_a", "PRICE-001")
    assert out == (
        '"""Docstring."""\n\nfrom __future__ import annotations\n\n'
        "from shop import x\nimport pytest\n\n\n"
        '@pytest.mark.rule("PRICE-001")\ndef test_a():\n    assert x\n'
    )
    assert _rules(out, "test_a") == ("PRICE-001",)


def test_no_imports_but_a_docstring_and_future_puts_pytest_after_them():
    src = '"""Docstring."""\nfrom __future__ import annotations\n\n\ndef test_a():\n    pass\n'
    out = add_rule_marker(src, "test_a", "PRICE-001")
    assert out.startswith('"""Docstring."""\nfrom __future__ import annotations\nimport pytest\n')
    assert _rules(out, "test_a") == ("PRICE-001",)


def test_everything_else_is_byte_identical():
    src = (
        "import pytest  # keep me\r\n\r\n"
        "def  test_a( x = 1 ):   # odd spacing\r\n"
        "    assert   x==1\r\n"
        "\r\n\r\n\r\n"
        "def test_b():\r\n\tpass\r\n"
    )
    out = add_rule_marker(src, "test_b", "PRICE-001")
    assert out == src.replace("def test_b", '@pytest.mark.rule("PRICE-001")\r\ndef test_b')


def test_an_unknown_test_raises():
    with pytest.raises(RulesError, match="is not a test function"):
        add_rule_marker("def test_a():\n    pass\n", "test_b", "PRICE-001")
