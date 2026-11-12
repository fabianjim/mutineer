"""Either-tier tamper checks: test config, __eq__, special-casing, pinning"""

from __future__ import annotations

import pytest

from mutineer.models import Severity

SRC = "src/shop/pricing.py"
TEST = "tests/test_pricing.py"

PRICING = """class Money:
    def __init__(self, cents):
        self.cents = cents


def price(code):
    if code == "basic":
        return 10
    return 20
"""

PRICING_TEST = """from shop.pricing import price


def test_basic():
    assert price("basic") == 10


def test_vip():
    assert price("vip-gold-37") == 20
"""


def _kinds(findings):
    return [f.kind for f in findings]


# ---- test_config_changed ----


@pytest.mark.parametrize(
    "path",
    ["tests/conftest.py", "conftest.py", "pytest.ini", "tox.ini", "tests/unit/conftest.py"],
)
def test_config_file_edit_warns(tamper_repo, path):
    tamper_repo.base({path: "# v1\n"})
    findings = tamper_repo.head({path: "# v2\n"})
    assert _kinds(findings) == ["test_config_changed"]
    assert findings[0].severity == Severity.WARN
    assert findings[0].location.file == path
    assert findings[0].location.line == 1


def test_new_conftest_warns(tamper_repo):
    tamper_repo.base({TEST: PRICING_TEST})
    assert _kinds(tamper_repo.head({"tests/conftest.py": "import pytest\n"})) == [
        "test_config_changed"
    ]


def test_deleted_conftest_warns(tamper_repo):
    tamper_repo.base({"tests/conftest.py": "import pytest\n"})
    findings = tamper_repo.head({"tests/conftest.py": None})
    assert _kinds(findings) == ["test_config_changed"]
    assert findings[0].location.line is None


@pytest.mark.parametrize(
    ("base", "head", "flagged"),
    [
        ("[metadata]\nname = a\n", "[metadata]\nname = b\n", False),
        ("[tool:pytest]\naddopts = -q\n", "[tool:pytest]\naddopts = -q -x\n", True),
        ("[metadata]\nname = a\n", "[metadata]\nname = a\n[tool:pytest]\naddopts = -p x\n", True),
    ],
)
def test_setup_cfg_only_its_pytest_section(tamper_repo, base, head, flagged):
    tamper_repo.base({"setup.cfg": base})
    assert _kinds(tamper_repo.head({"setup.cfg": head})) == (
        ["test_config_changed"] if flagged else []
    )


@pytest.mark.parametrize(
    ("head", "flagged"),
    [
        ('[project]\nname = "b"\n[tool.pytest.ini_options]\naddopts = "-q"\n', False),
        ('[project]\nname = "a"\n[tool.pytest.ini_options]\naddopts = "-q -k ok"\n', True),
        (
            '[project]\nname = "a"\n[tool.pytest.ini_options]\naddopts = "-q"\n'
            '[tool.mutineer]\ntest_paths = ["x"]\n',
            True,
        ),
    ],
    ids=["project_only", "pytest_options", "mutineer_test_paths"],
)
def test_pyproject_only_test_sections(tamper_repo, head, flagged):
    tamper_repo.base(
        {"pyproject.toml": '[project]\nname = "a"\n[tool.pytest.ini_options]\naddopts = "-q"\n'}
    )
    assert _kinds(tamper_repo.head({"pyproject.toml": head})) == (
        ["test_config_changed"] if flagged else []
    )


# ---- eq_override_added ----


def test_new_eq_warns(tamper_repo):
    tamper_repo.base({SRC: PRICING})
    head = PRICING.replace(
        "        self.cents = cents\n",
        "        self.cents = cents\n\n    def __eq__(self, other):\n        return True\n",
    )
    findings = tamper_repo.head({SRC: head})
    assert _kinds(findings) == ["eq_override_added"]
    assert findings[0].severity == Severity.WARN
    assert findings[0].location.line == 5
    assert findings[0].evidence["class"] == "Money"


def test_eq_by_assignment_warns(tamper_repo):
    tamper_repo.base({SRC: PRICING})
    head = PRICING.replace("class Money:\n", "class Money:\n    __eq__ = lambda s, o: True\n")
    assert _kinds(tamper_repo.head({SRC: head})) == ["eq_override_added"]


def test_eq_in_a_new_source_file_warns(tamper_repo):
    tamper_repo.base({SRC: PRICING})
    findings = tamper_repo.head(
        {"src/shop/box.py": "class Box:\n    def __eq__(self, o):\n        return True\n"}
    )
    assert _kinds(findings) == ["eq_override_added"]


def test_existing_eq_is_not_new(tamper_repo):
    base = PRICING.replace(
        "        self.cents = cents\n",
        "        self.cents = cents\n\n    def __eq__(self, other):\n        return False\n",
    )
    tamper_repo.base({SRC: base})
    assert tamper_repo.head({SRC: base.replace("return False", "return True")}) == []


def test_eq_in_test_code_is_not_source(tamper_repo):
    tamper_repo.base({TEST: PRICING_TEST})
    head = PRICING_TEST + "\n\nclass Fake:\n    def __eq__(self, o):\n        return True\n"
    assert tamper_repo.head({TEST: head}) == []


# ---- special_case_suspected ----


def test_changed_compare_to_a_test_literal_warns(tamper_repo):
    tamper_repo.base({SRC: PRICING, TEST: PRICING_TEST})
    head = PRICING.replace(
        "    return 20\n", '    if code == "vip-gold-37":\n        return 20\n    return 20\n'
    )
    findings = tamper_repo.head({SRC: head})
    assert _kinds(findings) == ["special_case_suspected"]
    f = findings[0]
    assert f.severity == Severity.WARN
    assert f.location.line == 9
    assert f.evidence["literal"] == "'vip-gold-37'"
    assert f.evidence["test_files"] == [TEST]
    assert f.evidence["source"] == 'if code == "vip-gold-37":'


def test_unchanged_compare_to_a_test_literal_is_fine(tamper_repo):
    # `code == "basic"` exists at base and "basic" is in a test; only line 9 changes
    tamper_repo.base({SRC: PRICING, TEST: PRICING_TEST})
    assert tamper_repo.head({SRC: PRICING.replace("return 20", "return 21")}) == []


@pytest.mark.parametrize("literal", ["0", "1", "-1", '""', "None", "True", "False"])
def test_trivial_literals_are_ignored(tamper_repo, literal):
    test = PRICING_TEST + f"\n\ndef test_trivial():\n    assert price({literal}) == 20\n"
    tamper_repo.base({SRC: PRICING, TEST: test})
    head = PRICING.replace(
        "    return 20\n", f"    if code == {literal}:\n        return 5\n    return 20\n"
    )
    assert tamper_repo.head({SRC: head}) == []


def test_literal_absent_from_tests_is_fine(tamper_repo):
    tamper_repo.base({SRC: PRICING, TEST: PRICING_TEST})
    head = PRICING.replace(
        "    return 20\n", '    if code == "staff":\n        return 0\n    return 20\n'
    )
    assert tamper_repo.head({SRC: head}) == []


def test_negative_number_and_membership_literals(tamper_repo):
    test = "def test_n():\n    assert f(-42) == 3\n    assert f(77) == 3\n"
    tamper_repo.base({SRC: PRICING, TEST: test})
    head = (
        PRICING
        + "\n\ndef f(n):\n    if n == -42 or n in (77, 2):\n        return 3\n    return n\n"
    )
    findings = tamper_repo.head({SRC: head})
    assert sorted(f.evidence["literal"] for f in findings) == ["-42", "77"]


# ---- pins_implementation ----


@pytest.mark.parametrize(
    ("body", "name"),
    [
        ("from shop.pricing import _rate\n\n\ndef test_r():\n    assert _rate(1) == 2\n", "_rate"),
        ("import shop.pricing as p\n\n\ndef test_r():\n    assert p._rate(1) == 2\n", "p._rate"),
        (
            "def test_m(mocker):\n    m = mocker.patch('x')\n    m.assert_called_once_with(1)\n",
            "m.assert_called_once_with",
        ),
        ("def test_m(m):\n    assert m.call_args == ((1,), {})\n", "m.call_args"),
    ],
    ids=["private_import", "private_attribute", "mock_assert_called", "mock_call_args"],
)
def test_new_test_pinning_implementation_is_info(tamper_repo, body, name):
    tamper_repo.base({TEST: PRICING_TEST})
    findings = tamper_repo.head({"tests/test_pins.py": body})
    assert _kinds(findings) == ["pins_implementation"]
    assert findings[0].severity == Severity.INFO
    assert name in findings[0].evidence["names"]


@pytest.mark.parametrize(
    ("body", "name"),
    [
        (
            "import shop.pricing\n\n\ndef test_r():\n    assert shop.pricing._rate(1) == 2\n",
            "shop.pricing._rate",
        ),
        (
            "from shop import pricing\n\n\ndef test_r():\n    assert pricing._rate(1) == 2\n",
            "pricing._rate",
        ),
    ],
    ids=["dotted_import", "imported_module"],
)
def test_a_private_attribute_of_an_imported_source_module_pins(tamper_repo, body, name):
    tamper_repo.base({TEST: PRICING_TEST})
    findings = tamper_repo.head({"tests/test_pins.py": body})
    assert _kinds(findings) == ["pins_implementation"]
    assert name in findings[0].evidence["names"]


def test_existing_pinning_is_not_reported_again(tamper_repo):
    base = "from shop.pricing import _rate\n\n\ndef test_r():\n    assert _rate(1) == 2\n"
    tamper_repo.base({TEST: base})
    head = base.replace("def test_r():\n", "def test_r():\n    x = 1\n")
    assert tamper_repo.head({TEST: head}) == []


def test_private_names_from_test_helpers_are_fine(tamper_repo):
    tamper_repo.base({TEST: PRICING_TEST})
    body = "from tests.helpers import _make\n\n\ndef test_h():\n    assert _make() == 1\n"
    assert tamper_repo.head({"tests/test_h.py": body}) == []


def test_pinning_rule_test_carries_its_rules(tamper_repo):
    tamper_repo.base({TEST: PRICING_TEST})
    body = (
        "import pytest\nfrom shop.pricing import _rate\n\n\n"
        '@pytest.mark.rule("PAY-001")\ndef test_r():\n    assert _rate(1) == 2\n'
    )
    findings = tamper_repo.head({"tests/test_pins.py": body})
    assert [(f.kind, f.rules) for f in findings] == [("pins_implementation", ["PAY-001"])]


def test_literal_already_used_elsewhere_in_source_is_a_domain_value(tamper_repo):
    # "vip-gold-37" is already a known value in the code base, so comparing to it in
    # changed code is not special-casing a test input
    known = 'TIERS = ("vip-gold-37",)\n'
    tamper_repo.base({SRC: PRICING, TEST: PRICING_TEST, "src/shop/tiers.py": known})
    head = PRICING.replace(
        "    return 20\n", '    if code == "vip-gold-37":\n        return 20\n    return 20\n'
    )
    assert tamper_repo.head({SRC: head}) == []


def test_a_root_conftest_is_test_config_not_source(tamper_repo):
    tamper_repo.base({"conftest.py": "def pytest_x(c):\n    pass\n", TEST: PRICING_TEST})
    head = 'def pytest_x(c):\n    if c == "vip-gold-37":\n        pass\n'
    assert _kinds(tamper_repo.head({"conftest.py": head})) == ["test_config_changed"]


DISPUTES = "src/shop/disputes.py"
DISPUTES_TEST = "tests/test_disputes.py"


def _dispute_change(tamper_repo, definition):
    # the change defines "confirmed" in one source module and compares to it in another;
    # both modules exist at base; the untracked case is tested further down
    tamper_repo.base(
        {
            "src/shop/models.py": "class Dispute:\n    pass\n",
            DISPUTES: "def is_closed(d):\n    return False\n",
            DISPUTES_TEST: 'def test_closed():\n    assert d("confirmed")\n',
        }
    )
    head = 'def is_closed(d):\n    if d.status == "confirmed":\n        return True\n'
    return tamper_repo.head({"src/shop/models.py": "class Dispute:\n" + definition, DISPUTES: head})


@pytest.mark.parametrize("literal", ["Literal", "typing.Literal"])
def test_a_value_the_change_defines_in_a_literal_annotation_is_a_domain_value(tamper_repo, literal):
    definition = f'    status: {literal}["open", "confirmed", "rejected"] | None = None\n'
    assert _dispute_change(tamper_repo, definition) == []


@pytest.mark.parametrize(
    "definition",
    [
        '    def close(self, evidence):\n        evidence["confirmed"] = True\n',
        '    def close(self):\n        return make(kind="confirmed")\n',
        '    CONFIRMED = "confirmed"\n',
        '    status: str = "confirmed"\n',
        '    STATUSES = ("open", "confirmed")\n',
        '    def close(self):\n        return make(kind=["open", "confirmed"])\n',
        '    LABELS = {"confirmed": "Confirmed"}\n',
        '    LABELS = {"done": "confirmed"}\n',
    ],
    ids=[
        "stored_subscript",
        "keyword_argument",
        "enum_member",
        "annotated_assignment",
        "tuple_in_assignment",
        "list_in_keyword_argument",
        "dict_key",
        "dict_value",
    ],
)
def test_a_value_the_change_defines_is_a_domain_value(tamper_repo, definition):
    assert _dispute_change(tamper_repo, definition) == []


@pytest.mark.parametrize(
    "definition",
    [
        '    def close(self):\n        return self.status != "confirmed"\n',
        '    def close(self):\n        return log("confirmed")\n',
        '    def close(self):\n        x = log("confirmed")\n        return x\n',
        '    def close(self, evidence):\n        return evidence["confirmed"]\n',
    ],
    ids=["second_compare", "positional_argument", "nested_in_assignment", "loaded_subscript"],
)
def test_a_value_the_change_uses_only_in_non_defining_positions_still_warns(
    tamper_repo, definition
):
    findings = _dispute_change(tamper_repo, definition)
    assert ("special_case_suspected", DISPUTES) in [(f.kind, f.location.file) for f in findings]


def test_one_finding_per_file_and_literal(tamper_repo):
    tamper_repo.base({SRC: PRICING, TEST: PRICING_TEST})
    head = PRICING.replace(
        "    return 20\n",
        '    if code == "vip-gold-37":\n        return 20\n'
        '    if code != "vip-gold-37":\n        return 21\n    return 20\n',
    )
    findings = tamper_repo.head({SRC: head})
    assert [(f.kind, f.location.line, f.evidence["lines"]) for f in findings] == [
        ("special_case_suspected", 9, [9, 11])
    ]


AGENTS = "src/shop/agents.py"
AGENTS_SOURCE = """HOOKS = {"claude": ".claude/settings.json"}


def is_claude(agent):
    return agent == "claude"
"""
AGENTS_TEST = """from shop.agents import is_claude


def test_c():
    assert is_claude("claude")
"""


def test_a_value_an_untracked_module_defines_is_a_domain_value(tamper_repo):
    # the new module is never staged, so it is in the diff but not in the index
    tamper_repo.base({"src/shop/other.py": "X = 2\n"})
    assert tamper_repo.head({AGENTS: AGENTS_SOURCE, "tests/test_agents.py": AGENTS_TEST}) == []


def test_a_value_another_untracked_module_defines_is_a_domain_value(tamper_repo):
    # models.py is new and untracked, and defines the value disputes.py compares to
    tamper_repo.base(
        {
            DISPUTES: "def is_closed(d):\n    return False\n",
            DISPUTES_TEST: 'def test_closed():\n    assert d("confirmed")\n',
        }
    )
    head = 'def is_closed(d):\n    if d.status == "confirmed":\n        return True\n'
    models = 'class Dispute:\n    CONFIRMED = "confirmed"\n'
    assert tamper_repo.head({"src/shop/models.py": models, DISPUTES: head}) == []


def test_a_value_only_an_ignored_module_defines_still_warns(tamper_repo):
    tamper_repo.base(
        {
            ".gitignore": "src/shop/generated.py\n",
            DISPUTES: "def is_closed(d):\n    return False\n",
            DISPUTES_TEST: 'def test_closed():\n    assert d("confirmed")\n',
        }
    )
    head = 'def is_closed(d):\n    if d.status == "confirmed":\n        return True\n'
    generated = 'CONFIRMED = "confirmed"\n'
    findings = tamper_repo.head({"src/shop/generated.py": generated, DISPUTES: head})
    assert [(f.kind, f.location.file) for f in findings] == [("special_case_suspected", DISPUTES)]
