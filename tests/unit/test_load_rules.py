from __future__ import annotations

from pathlib import Path

import pytest

from mutineer.errors import RulesError
from mutineer.rules import load_rules

GOOD = """
version = 1

[[rule]]
id = "SHIP-003"
statement = "Orders of $50 or more ship free"
seam = "shop.shipping.quote"

[[rule]]
id = "AUTH-1234"
statement = "Old promise"
status = "retired"
"""


def test_parses_rule_tables():
    rf = load_rules(GOOD)
    assert [(r.id, r.status) for r in rf.rules] == [
        ("SHIP-003", "active"),
        ("AUTH-1234", "retired"),
    ]
    assert rf.rules[0].seam == "shop.shipping.quote"
    assert rf.rules[1].seam is None


def test_empty_file_has_no_rules():
    assert load_rules("").rules == []


def test_mutineers_own_rules_file_is_valid():
    text = (Path(__file__).parents[2] / ".mutineer" / "rules.toml").read_text(encoding="utf-8")
    ids = [r.id for r in load_rules(text).rules]
    assert ids == [
        "VERDICT-001",
        "VERDICT-002",
        "VERDICT-003",
        "TRIAGE-001",
        "TRIAGE-002",
        "RULES-001",
        "ENGINE-001",
        "ENGINE-002",
        "REUSE-001",
    ]


@pytest.mark.parametrize("bad_id", ["ship-003", "SHIP-03", "SHIP003", "3SHIP-003", "SHIP-003a", ""])
def test_rejects_bad_ids(bad_id):
    with pytest.raises(RulesError, match="SHIP-003"):
        load_rules(f'[[rule]]\nid = "{bad_id}"\nstatement = "s"\n')


def test_rejects_missing_statement():
    with pytest.raises(RulesError, match="missing required key 'statement'"):
        load_rules('[[rule]]\nid = "A-001"\n')


def test_rejects_unknown_rule_key():
    with pytest.raises(RulesError, match=r"\[\[rule\]\] #1 \(A-001\): unknown key 'tests'"):
        load_rules('[[rule]]\nid = "A-001"\nstatement = "s"\ntests = []\n')


def test_rejects_unknown_top_level_key():
    with pytest.raises(RulesError, match="unknown key 'rules'"):
        load_rules('[[rules]]\nid = "A-001"\nstatement = "s"\n')


@pytest.mark.parametrize("version", ["2", "0", '"1"'])
def test_rejects_other_versions(version):
    with pytest.raises(RulesError, match="version must be 1"):
        load_rules(f"version = {version}\n")


def test_rejects_unknown_status():
    with pytest.raises(RulesError, match="status"):
        load_rules('[[rule]]\nid = "A-001"\nstatement = "s"\nstatus = "deleted"\n')


def test_rejects_duplicate_ids():
    with pytest.raises(RulesError, match="duplicate rule id A-001"):
        load_rules(
            '[[rule]]\nid = "A-001"\nstatement = "s"\n[[rule]]\nid = "A-001"\nstatement = "t"\n'
        )


def test_rejects_invalid_toml():
    with pytest.raises(RulesError, match="not valid TOML"):
        load_rules("[[rule]\n")


def test_error_messages_are_one_line():
    for text in ['[[rule]]\nid = "x"\nstatement = "s"\n', "version = 2\n", "[[rule]\n"]:
        with pytest.raises(RulesError) as info:
            load_rules(text)
        assert "\n" not in str(info.value)
