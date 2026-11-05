"""``rules.compare`` and ``rules.rule_findings``"""

from __future__ import annotations

import pytest

from mutineer.errors import RulesError
from mutineer.models import Audience, Rule, RulesFile, Severity
from mutineer.rules import compare, rule_findings


def _rules(*rules: Rule) -> RulesFile:
    return RulesFile(rules=list(rules))


SHIP = Rule(id="SHIP-003", statement="Orders of $50 or more ship free", seam="shop.shipping.quote")
TAX = Rule(id="TAX-001", statement="Tax is 20%")


# ---- compare ----


@pytest.mark.rule("RULES-001")
def test_deleting_a_rule_id_is_an_error_naming_the_fix():
    with pytest.raises(RulesError, match="retire SHIP-003 instead of deleting it"):
        compare(_rules(SHIP, TAX), _rules(TAX))


@pytest.mark.rule("RULES-001")
def test_retiring_a_rule_is_allowed_and_reported():
    retired = SHIP.model_copy(update={"status": "retired"})
    [finding] = compare(_rules(SHIP), _rules(retired))
    assert finding.kind == "rules_file_changed"
    assert finding.rules == ["SHIP-003"]
    assert finding.evidence["change"] == "retired"


@pytest.mark.rule("RULES-001")
def test_several_deleted_ids_are_all_named():
    with pytest.raises(RulesError, match="SHIP-003, TAX-001"):
        compare(_rules(SHIP, TAX), _rules())


def test_unchanged_rules_give_no_findings():
    assert compare(_rules(SHIP, TAX), _rules(TAX, SHIP)) == []


def test_explicit_default_status_is_not_a_change():
    explicit = Rule(**{**SHIP.model_dump(), "status": "active"})
    assert compare(_rules(SHIP), _rules(explicit)) == []


def test_changed_and_added_rules_are_info_for_humans_sorted_by_id():
    edited = SHIP.model_copy(update={"statement": "Orders of $40 or more ship free"})
    findings = compare(_rules(SHIP), _rules(TAX, edited), rules_file=".mutineer/rules.toml")
    assert [(f.rules, f.evidence["change"]) for f in findings] == [
        (["SHIP-003"], "modified"),
        (["TAX-001"], "added"),
    ]
    for f in findings:
        assert (f.check, f.kind, f.severity, f.audience) == (
            "rules",
            "rules_file_changed",
            Severity.INFO,
            Audience.HUMAN,
        )
        assert f.location is not None and f.location.file == ".mutineer/rules.toml"
        assert f.id == ""
    assert findings[0].evidence["before"]["statement"] == SHIP.statement
    assert findings[0].evidence["after"]["statement"] == "Orders of $40 or more ship free"
    assert "before" not in findings[1].evidence


def test_no_base_file_reports_every_rule_as_added():
    findings = compare(None, _rules(SHIP))
    assert [f.evidence["change"] for f in findings] == ["added"]
    assert findings[0].location is None


def test_reactivating_a_retired_rule_is_reported():
    retired = SHIP.model_copy(update={"status": "retired"})
    [finding] = compare(_rules(retired), _rules(SHIP))
    assert finding.evidence["change"] == "reactivated"


# ---- rule_findings ----


def test_every_rule_guarded_gives_no_findings():
    rule_map = {"tests/t.py::test_a": ["SHIP-003"], "tests/t.py::test_b": ["SHIP-003"]}
    assert rule_findings(_rules(SHIP), rule_map) == []


def test_unknown_marker_id_is_a_warning_per_id_listing_its_tests():
    rule_map = {
        "tests/b.py::test_b": ["NOPE-001", "SHIP-003"],
        "tests/a.py::test_a[1]": ["NOPE-001"],
    }
    [finding] = rule_findings(_rules(SHIP), rule_map)
    assert finding.kind == "unknown_rule_id"
    assert (finding.severity, finding.audience) == (Severity.WARN, Audience.HUMAN)
    assert finding.rules == ["NOPE-001"]
    assert finding.evidence["tests"] == ["tests/a.py::test_a[1]", "tests/b.py::test_b"]
    assert finding.location is not None and finding.location.file == "tests/a.py"


def test_marker_on_retired_rule_is_a_warning():
    retired = TAX.model_copy(update={"status": "retired"})
    rule_map = {"tests/t.py::test_a": ["SHIP-003", "TAX-001"]}
    [finding] = rule_findings(_rules(SHIP, retired), rule_map)
    assert finding.kind == "retired_rule_marked"
    assert finding.severity == Severity.WARN
    assert finding.rules == ["TAX-001"]


def test_active_rule_without_tests_is_info_and_retired_one_is_not_reported():
    retired = Rule(id="OLD-001", statement="old", status="retired")
    [finding] = rule_findings(_rules(SHIP, TAX, retired), {"t.py::test": ["SHIP-003"]})
    assert finding.kind == "rule_unguarded"
    assert (finding.severity, finding.audience) == (Severity.INFO, Audience.HUMAN)
    assert finding.rules == ["TAX-001"]


def test_findings_come_in_catalogue_order():
    retired = Rule(id="OLD-001", statement="old", status="retired")
    rule_map = {"t.py::test": ["ZZZ-001", "OLD-001", "AAA-001"]}
    kinds = [(f.kind, f.rules[0]) for f in rule_findings(_rules(SHIP, retired), rule_map)]
    assert kinds == [
        ("unknown_rule_id", "AAA-001"),
        ("unknown_rule_id", "ZZZ-001"),
        ("retired_rule_marked", "OLD-001"),
        ("rule_unguarded", "SHIP-003"),
    ]
