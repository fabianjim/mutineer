"""Tamper detection on the shop fixture's branches"""

from __future__ import annotations

import json

import pytest

from mutineer.checks import tamper
from mutineer.cli import main
from mutineer.gitdiff import load_diff
from mutineer.models import Severity
from mutineer.rules import load_rules


def _findings(shop_repo, branch):
    shop_repo.checkout(branch)
    root = shop_repo.path
    diff = load_diff(root, "main", ".mutineer/rules.toml")
    rules = load_rules((root / ".mutineer/rules.toml").read_text())
    return tamper.check(diff, root, rules, test_paths=["tests"])


def _assert_weakened_ship_003(finding, severity):
    assert finding.kind == "rule_test_weakened"
    assert finding.check == "tamper"
    assert finding.severity == severity
    assert finding.rules == ["SHIP-003"]
    assert finding.location is not None
    assert (finding.location.file, finding.location.line) == (
        "tests/rules/test_shipping_rules.py",
        12,
    )
    assert finding.evidence["before"] == "assert quote(10) == 5"
    assert finding.evidence["after"] == "assert quote(10) >= 0"
    assert finding.evidence["test"] == "tests/rules/test_shipping_rules.py::test_small_order_pays"
    assert finding.evidence["base_line"] == 12
    assert finding.id == ""


@pytest.mark.rule("VERDICT-001")
def test_agent_tamper_blocks_on_the_weakened_rule_test(shop_repo):
    findings = _findings(shop_repo, "agent-tamper")
    blocking = [f for f in findings if f.severity == Severity.BLOCK]
    assert len(blocking) == 1
    assert [f for f in findings if f.severity != Severity.INFO] == blocking
    _assert_weakened_ship_003(blocking[0], Severity.BLOCK)


@pytest.mark.rule("VERDICT-001")
def test_agent_tamper_approved_is_the_same_finding_at_warn(shop_repo):
    findings = [f for f in _findings(shop_repo, "agent-tamper-approved") if f.severity != "info"]
    assert len(findings) == 1
    _assert_weakened_ship_003(findings[0], Severity.WARN)


@pytest.mark.parametrize("branch", ["agent", "agent-gap", "main"])
def test_clean_branches_have_nothing_above_info(shop_repo, branch):
    assert [f for f in _findings(shop_repo, branch) if f.severity != Severity.INFO] == []


@pytest.mark.rule("VERDICT-001")
def test_cli_no_mutation_blocks_on_agent_tamper(shop_repo, monkeypatch, capsys):
    shop_repo.checkout("agent-tamper")
    monkeypatch.chdir(shop_repo.path)
    assert main(["run", "--no-mutation", "--format", "json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "block"
    assert [f["kind"] for f in out["findings"]] == ["rule_test_weakened"]


@pytest.mark.parametrize(
    ("branch", "argv", "code", "status"),
    [
        ("agent", [], 0, "pass"),
        ("agent-gap", ["--strict"], 0, "pass"),
        ("agent-tamper-approved", [], 0, "warn"),
        ("agent-tamper-approved", ["--strict"], 1, "warn"),
    ],
)
def test_cli_no_mutation_exit_codes(shop_repo, monkeypatch, capsys, branch, argv, code, status):
    shop_repo.checkout(branch)
    monkeypatch.chdir(shop_repo.path)
    assert main(["run", "--no-mutation", "--format", "json", *argv]) == code
    assert json.loads(capsys.readouterr().out)["status"] == status


def test_cli_no_mutation_writes_output_file(shop_repo, monkeypatch, capsys, tmp_path):
    shop_repo.checkout("agent-tamper")
    monkeypatch.chdir(shop_repo.path)
    target = tmp_path / "report.json"
    assert (
        main(
            ["run", "--no-mutation", "--format", "json", "--base", "main", "--output", str(target)]
        )
        == 2
    )
    assert capsys.readouterr().out == ""
    assert json.loads(target.read_text())["findings"][0]["severity"] == "block"


def test_cli_narrowed_test_paths_cannot_hide_a_rule_test(shop_repo, monkeypatch, capsys):
    shop_repo.checkout("agent-tamper")
    pyproject = shop_repo.path / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + '\n[tool.mutineer]\ntest_paths = ["nowhere"]\n')
    monkeypatch.chdir(shop_repo.path)
    assert main(["run", "--no-mutation", "--format", "json"]) == 2
    kinds = {f["kind"] for f in json.loads(capsys.readouterr().out)["findings"]}
    assert kinds == {"rule_test_weakened", "test_config_changed"}
