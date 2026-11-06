"""One test per tamper detection for both tiers"""

from __future__ import annotations

import pytest

from mutineer.models import Severity

RULE_PATH = "tests/test_rules.py"
IMPL_PATH = "tests/test_impl.py"

BODY = """
def test_free():
    total = 80
    assert quote(total) == 0


def test_pays():
    assert quote(10) == 5
    assert quote(20) == 5


def test_raises():
    with pytest.raises(ValueError):
        quote(-1)


class TestUnit(unittest.TestCase):
    def test_equal(self):
        self.assertEqual(quote(10), 5)

    def test_is(self):
        self.assertIs(quote(80), 0)


@pytest.mark.parametrize(("total", "fee"), [(10, 5), (20, 5), (80, 0)])
def test_table(total, fee):
    assert quote(total) == fee
"""

HEADER = "import unittest\n\nimport pytest\nfrom shop import quote\n"
RULE_FILE = HEADER + '\npytestmark = pytest.mark.rule("SHIP-003")\n\n' + BODY
IMPL_FILE = HEADER + "\n" + BODY


def _edit(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new, 1)


def _rule_case(tamper_repo, old: str, new: str):
    tamper_repo.base({RULE_PATH: RULE_FILE})
    return tamper_repo.head({RULE_PATH: _edit(RULE_FILE, old, new)})


def _impl_case(tamper_repo, old: str, new: str):
    tamper_repo.base({IMPL_PATH: IMPL_FILE})
    return tamper_repo.head({IMPL_PATH: _edit(IMPL_FILE, old, new)})


def _only(findings):
    assert len(findings) == 1, findings
    return findings[0]


def _line_of(text: str, needle: str) -> int:
    return next(i for i, line in enumerate(text.splitlines(), 1) if needle in line)


# each case: (old, new, kind, change detail, test name, head line needle)
CASES = {
    "assertion_removed": (
        "    assert quote(10) == 5\n    assert quote(20) == 5",
        "    assert quote(10) == 5",
        "rule_test_weakened",
        "assertion_removed",
        "test_pays",
        None,
    ),
    "operator_changed": (
        "assert quote(20) == 5",
        "assert quote(20) in (4, 5)",
        "rule_test_weakened",
        "operator_changed",
        "test_pays",
        "assert quote(20) in (4, 5)",
    ),
    "truthiness_check": (
        "assert quote(total) == 0",
        "assert quote(total) is not None or True",
        "rule_test_weakened",
        "truthiness_check",
        "test_free",
        "or True",
    ),
    "raises_widened": (
        "with pytest.raises(ValueError):",
        "with pytest.raises(Exception):",
        "rule_test_weakened",
        "raises_widened",
        "test_raises",
        "pytest.raises(Exception)",
    ),
    "assertEqual_to_assertTrue": (
        "self.assertEqual(quote(10), 5)",
        "self.assertTrue(quote(10))",
        "rule_test_weakened",
        "assert_method_weakened",
        "TestUnit::test_equal",
        "self.assertTrue(quote(10))",
    ),
    "assertIs_to_assertIn": (
        "self.assertIs(quote(80), 0)",
        "self.assertIn(quote(80), (0, 1))",
        "rule_test_weakened",
        "assert_method_weakened",
        "TestUnit::test_is",
        "self.assertIn",
    ),
    "parametrize_case_removed": (
        "[(10, 5), (20, 5), (80, 0)]",
        "[(10, 5), (80, 0)]",
        "rule_test_weakened",
        "parametrize_case_removed",
        "test_table",
        "[(10, 5), (80, 0)]",
    ),
    "literal_changed": (
        "assert quote(10) == 5",
        "assert quote(10) == 6",
        "rule_expected_changed",
        "literal_changed",
        "test_pays",
        "assert quote(10) == 6",
    ),
    "assertEqual_literal_changed": (
        "self.assertEqual(quote(10), 5)",
        "self.assertEqual(quote(10), 7)",
        "rule_expected_changed",
        "literal_changed",
        "TestUnit::test_equal",
        "self.assertEqual(quote(10), 7)",
    ),
    "parametrize_case_changed": (
        "[(10, 5), (20, 5), (80, 0)]",
        "[(10, 5), (20, 6), (80, 0)]",
        "rule_expected_changed",
        "parametrize_case_changed",
        "test_table",
        "[(10, 5), (20, 6), (80, 0)]",
    ),
    "skip_marker": (
        "def test_pays():",
        "@pytest.mark.skip(reason='flaky')\ndef test_pays():",
        "rule_test_skipped",
        "skip_added",
        "test_pays",
        "@pytest.mark.skip",
    ),
    "skipif_marker": (
        "def test_pays():",
        "@pytest.mark.skipif(True, reason='later')\ndef test_pays():",
        "rule_test_skipped",
        "skip_added",
        "test_pays",
        "@pytest.mark.skipif",
    ),
    "xfail_marker": (
        "def test_pays():",
        "@pytest.mark.xfail\ndef test_pays():",
        "rule_test_skipped",
        "skip_added",
        "test_pays",
        "@pytest.mark.xfail",
    ),
    "skip_call": (
        "def test_pays():\n    assert quote(10) == 5",
        "def test_pays():\n    pytest.skip('later')\n    assert quote(10) == 5",
        "rule_test_skipped",
        "skip_added",
        "test_pays",
        "pytest.skip('later')",
    ),
}


@pytest.mark.rule("VERDICT-001")
@pytest.mark.parametrize("case", sorted(CASES))
def test_rule_tier_detection_blocks(tamper_repo, case):
    old, new, kind, detail, test, needle = CASES[case]
    finding = _only(_rule_case(tamper_repo, old, new))
    head = (tamper_repo.path / RULE_PATH).read_text()
    assert finding.kind == kind
    assert finding.severity == Severity.BLOCK
    assert finding.audience == "human"
    assert finding.rules == ["SHIP-003"]
    assert finding.evidence["test"] == f"{RULE_PATH}::{test}"
    assert finding.evidence["change"] == detail
    assert finding.location.file == RULE_PATH
    if needle is not None:
        assert finding.location.line == _line_of(head, needle)
    assert finding.next_action


@pytest.mark.parametrize("case", sorted(CASES))
def test_impl_tier_detection_is_one_info(tamper_repo, case):
    old, new, kind, detail, test, needle = CASES[case]
    finding = _only(_impl_case(tamper_repo, old, new))
    head = (tamper_repo.path / IMPL_PATH).read_text()
    assert finding.kind == "impl_test_changed"
    assert finding.severity == Severity.INFO
    assert finding.rules == []
    expected_change = kind.removeprefix("rule_test_").removeprefix("rule_")
    assert finding.evidence["change"] == expected_change
    assert finding.evidence["detail"] == detail
    assert finding.evidence["test"] == f"{IMPL_PATH}::{test}"
    if needle is not None:
        assert finding.location.line == _line_of(head, needle)


def test_weakened_evidence_has_before_after_and_base_line(tamper_repo):
    finding = _only(_rule_case(tamper_repo, "assert quote(20) == 5", "assert quote(20) >= 5"))
    base_line = _line_of(RULE_FILE, "assert quote(20) == 5")
    assert finding.evidence["before"] == "assert quote(20) == 5"
    assert finding.evidence["after"] == "assert quote(20) >= 5"
    assert finding.evidence["base_line"] == base_line
    assert finding.evidence["rules"] == ["SHIP-003"]


def test_removed_assertion_has_no_after(tamper_repo):
    finding = _only(_rule_case(tamper_repo, *CASES["assertion_removed"][:2]))
    assert finding.evidence["before"] == "assert quote(20) == 5"
    assert "after" not in finding.evidence


@pytest.mark.rule("VERDICT-001")
def test_deleted_rule_test_uses_base_location(tamper_repo):
    head = _edit(RULE_FILE, "def test_free():\n    total = 80\n    assert quote(total) == 0", "")
    tamper_repo.base({RULE_PATH: RULE_FILE})
    finding = _only(tamper_repo.head({RULE_PATH: head}))
    assert finding.kind == "rule_test_deleted"
    assert finding.severity == Severity.BLOCK
    assert finding.location.file == RULE_PATH
    assert finding.location.line == _line_of(RULE_FILE, "def test_free")
    assert finding.evidence["base_line"] == finding.location.line
    assert finding.evidence["before"] == "def test_free():"
    assert "after" not in finding.evidence


@pytest.mark.rule("VERDICT-001")
def test_deleting_a_rule_test_file_blocks_every_test_in_it(tamper_repo):
    tamper_repo.base({RULE_PATH: RULE_FILE})
    findings = tamper_repo.head({RULE_PATH: None})
    assert {f.kind for f in findings} == {"rule_test_deleted"}
    assert len(findings) == 6
    assert all(f.severity == Severity.BLOCK for f in findings)


@pytest.mark.rule("VERDICT-001")
def test_untracking_a_rule_test_file_blocks_though_it_stays_on_disk(tamper_repo):
    # git reports the file deleted, but it is still on disk and pytest still runs it locally
    tamper_repo.base({RULE_PATH: RULE_FILE})
    tamper_repo.repo.git("rm", "-q", "--cached", RULE_PATH)
    findings = tamper_repo.head({".gitignore": f"{RULE_PATH}\n"})
    assert {f.kind for f in findings} == {"rule_test_deleted"}
    assert len(findings) == 6
    assert all(f.severity == Severity.BLOCK for f in findings)


def _rename_and_break(tamper_repo, new_path: str):
    tamper_repo.base({RULE_PATH: RULE_FILE})
    (tamper_repo.path / new_path).parent.mkdir(parents=True, exist_ok=True)
    tamper_repo.repo.git("mv", RULE_PATH, new_path)
    return tamper_repo.head({new_path: RULE_FILE + "\ndef broken(:\n"})


@pytest.mark.rule("VERDICT-001")
@pytest.mark.parametrize("new_path", ["tests/rules_old.py", "old/rules.py"])
def test_renaming_a_rule_test_file_out_of_collection_and_breaking_it_blocks(tamper_repo, new_path):
    findings = _rename_and_break(tamper_repo, new_path)
    assert {f.kind for f in findings} == {"rule_test_deleted"}
    assert len(findings) == 6
    assert all(f.severity == Severity.BLOCK for f in findings)


def test_renaming_a_rule_test_file_to_a_test_name_and_breaking_it_is_skipped(tamper_repo):
    # pytest still collects the file, so its collection error blocks the baseline
    assert _rename_and_break(tamper_repo, "tests/test_rules_old.py") == []


@pytest.mark.rule("VERDICT-001")
def test_an_unparsable_test_file_does_not_hide_tampering_in_the_next_one(tamper_repo):
    tamper_repo.base({"tests/test_a.py": RULE_FILE, RULE_PATH: RULE_FILE})
    head = _edit(RULE_FILE, "def test_free():\n    total = 80\n    assert quote(total) == 0", "")
    findings = tamper_repo.head(
        {"tests/test_a.py": RULE_FILE + "\ndef broken(:\n", RULE_PATH: head}
    )
    assert [(f.kind, f.location.file) for f in findings] == [("rule_test_deleted", RULE_PATH)]


def test_deleted_impl_test_is_info(tamper_repo):
    head = _edit(IMPL_FILE, "def test_free():\n    total = 80\n    assert quote(total) == 0", "")
    tamper_repo.base({IMPL_PATH: IMPL_FILE})
    finding = _only(tamper_repo.head({IMPL_PATH: head}))
    assert finding.kind == "impl_test_changed"
    assert finding.evidence["change"] == "deleted"


@pytest.mark.rule("VERDICT-001")
@pytest.mark.parametrize(
    ("base", "head"),
    [
        (
            '@pytest.mark.rule("SHIP-003")\ndef test_pays():\n    assert quote(10) == 5\n',
            "def test_pays():\n    assert quote(10) == 5\n",
        ),
        (
            'pytestmark = pytest.mark.rule("SHIP-003")\n\n\ndef test_pays():\n'
            "    assert quote(10) == 5\n",
            "\n\ndef test_pays():\n    assert quote(10) == 5\n",
        ),
        (
            '@pytest.mark.rule("SHIP-003")\nclass TestQ:\n    def test_pays(self):\n'
            "        assert quote(10) == 5\n",
            "class TestQ:\n    def test_pays(self):\n        assert quote(10) == 5\n",
        ),
        (
            '@pytest.mark.rule("SHIP-003", "PAY-001")\ndef test_pays():\n'
            "    assert quote(10) == 5\n",
            '@pytest.mark.rule("PAY-001")\ndef test_pays():\n    assert quote(10) == 5\n',
        ),
    ],
    ids=["decorator", "module_pytestmark", "class_decorator", "one_of_two_ids"],
)
def test_marker_removed(tamper_repo, base, head):
    tamper_repo.base({RULE_PATH: "import pytest\n\n" + base})
    finding = _only(tamper_repo.head({RULE_PATH: "import pytest\n\n" + head}))
    assert finding.kind == "rule_marker_removed"
    assert finding.severity == Severity.BLOCK
    assert finding.rules == ["SHIP-003"]
    assert "SHIP-003" in finding.evidence["before"]


@pytest.mark.rule("VERDICT-001")
def test_module_level_skip_skips_every_rule_test(tamper_repo):
    head = RULE_FILE.replace(
        'pytestmark = pytest.mark.rule("SHIP-003")',
        'pytestmark = [pytest.mark.rule("SHIP-003"), pytest.mark.skip]',
    )
    tamper_repo.base({RULE_PATH: RULE_FILE})
    findings = tamper_repo.head({RULE_PATH: head})
    assert {f.kind for f in findings} == {"rule_test_skipped"}
    assert len(findings) == 6


def test_unittest_skip_decorator_counts(tamper_repo):
    finding = _only(
        _rule_case(
            tamper_repo,
            "    def test_is(self):",
            "    @unittest.skip('later')\n    def test_is(self):",
        )
    )
    assert finding.kind == "rule_test_skipped"


# ---- precedence: one finding per test, first kind in table order ----


def test_marker_removed_beats_weakened(tamper_repo):
    base = 'import pytest\n\n@pytest.mark.rule("SHIP-003")\ndef test_p():\n    assert f() == 5\n'
    head = "import pytest\n\ndef test_p():\n    assert f() >= 5\n"
    tamper_repo.base({RULE_PATH: base})
    assert _only(tamper_repo.head({RULE_PATH: head})).kind == "rule_marker_removed"


def test_skipped_beats_weakened(tamper_repo):
    finding = _only(
        _rule_case(
            tamper_repo,
            "def test_pays():\n    assert quote(10) == 5",
            "@pytest.mark.xfail\ndef test_pays():\n    assert quote(10) >= 5",
        )
    )
    assert finding.kind == "rule_test_skipped"


def test_weakened_beats_expected_changed(tamper_repo):
    finding = _only(
        _rule_case(
            tamper_repo,
            "    assert quote(10) == 5\n    assert quote(20) == 5",
            "    assert quote(10) == 6\n    assert quote(20) >= 5",
        )
    )
    assert finding.kind == "rule_test_weakened"
    assert finding.evidence["after"] == "assert quote(20) >= 5"


def test_location_is_first_affected_assertion(tamper_repo):
    finding = _only(
        _rule_case(
            tamper_repo,
            "    assert quote(10) == 5\n    assert quote(20) == 5",
            "    assert quote(10) >= 5\n    assert quote(20) >= 5",
        )
    )
    head = (tamper_repo.path / RULE_PATH).read_text()
    assert finding.location.line == _line_of(head, "assert quote(10) >= 5")


# ---- severity: WARN only when every guarded rule changed in rules.toml ----

TWO_RULES = (
    'import pytest\n\n@pytest.mark.rule("SHIP-003", "PAY-001")\ndef test_p():\n'
    "    assert f() == 5\n"
)


def _relaxed(rules_toml: str, rule_id: str) -> str:
    marker = f'id = "{rule_id}"\nstatement = "'
    return rules_toml.replace(marker, marker + "Relaxed: ")


@pytest.mark.rule("VERDICT-001")
@pytest.mark.parametrize(
    ("changed", "severity"),
    [((), Severity.BLOCK), (("SHIP-003",), Severity.BLOCK), (("SHIP-003", "PAY-001"), "warn")],
)
def test_warn_only_when_all_guarded_rules_changed(tamper_repo, changed, severity):
    tamper_repo.base({RULE_PATH: TWO_RULES})
    rules = tamper_repo.rules_toml
    for rule_id in changed:
        rules = _relaxed(rules, rule_id)
    tamper_repo.repo.write(".mutineer/rules.toml", rules)
    finding = _only(tamper_repo.head({RULE_PATH: TWO_RULES.replace("== 5", ">= 5")}))
    assert finding.kind == "rule_test_weakened"
    assert finding.severity == severity
    assert finding.rules == ["PAY-001", "SHIP-003"]
