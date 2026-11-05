"""``mutineer rules check`` and ``mutineer init`` through the CLI entry point"""

from __future__ import annotations

import pytest

from mutineer.cli import main
from mutineer.cli.init import RULES_TEMPLATE
from mutineer.rules import load_rules

# ---- rules check ----


def test_rules_check_on_fixture_lists_ship_003_and_its_two_tests(shop_clone, monkeypatch, capsys):
    monkeypatch.chdir(shop_clone("main"))
    assert main(["rules", "check"]) == 0
    out = capsys.readouterr().out
    assert "SHIP-003: Orders of $50 or more ship free" in out
    assert "seam: shop.shipping.quote" in out
    assert "tests/rules/test_shipping_rules.py::test_small_order_pays" in out
    assert "tests/rules/test_shipping_rules.py::test_well_over_threshold_ships_free" in out
    assert "WARN" not in out


@pytest.mark.rule("RULES-001")
def test_rules_check_after_deleting_a_rule_in_a_commit_exits_4_with_retire_message(
    shop_clone, run_git, monkeypatch, capsys
):
    root = shop_clone("main")
    run_git(root, "checkout", "-q", "-b", "drop-rule")
    (root / ".mutineer/rules.toml").write_text("version = 1\n")
    run_git(root, "commit", "-qam", "drop SHIP-003")
    monkeypatch.chdir(root)
    assert main(["rules", "check", "--base", "main"]) == 4
    assert "retire SHIP-003 instead of deleting it" in capsys.readouterr().err


@pytest.mark.rule("RULES-001")
def test_rules_check_accepts_a_retired_rule(shop_clone, run_git, monkeypatch, capsys):
    root = shop_clone("main")
    run_git(root, "checkout", "-q", "-b", "retire-rule")
    path = root / ".mutineer/rules.toml"
    path.write_text(path.read_text() + 'status = "retired"\n')
    run_git(root, "commit", "-qam", "retire SHIP-003")
    monkeypatch.chdir(root)
    assert main(["rules", "check", "--base", "main"]) == 0
    out = capsys.readouterr().out
    assert "SHIP-003 [retired]" in out
    assert "INFO rules_file_changed" in out
    assert "WARN retired_rule_marked" in out


def test_rules_check_on_approved_branch_reports_the_changed_rule(shop_clone, monkeypatch, capsys):
    monkeypatch.chdir(shop_clone("agent-tamper-approved"))
    assert main(["rules", "check"]) == 0
    assert "INFO rules_file_changed: Rule SHIP-003 was modified" in capsys.readouterr().out


def test_rules_check_reports_unknown_marker_ids(shop_clone, monkeypatch, capsys):
    root = shop_clone("main")
    (root / "tests/test_new.py").write_text(
        "import pytest\n\n@pytest.mark.rule('NEW-001')\ndef test_new():\n    pass\n"
    )
    monkeypatch.chdir(root)
    assert main(["rules", "check"]) == 0
    assert "WARN unknown_rule_id" in capsys.readouterr().out


def test_rules_check_without_rules_file_says_to_run_init(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main(["rules", "check"]) == 4
    assert "mutineer init" in capsys.readouterr().err


def test_rules_check_with_invalid_rules_file_exits_4(tmp_path, monkeypatch, capsys):
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / ".mutineer/rules.toml").write_text('[[rule]]\nid = "bad"\nstatement = "x"\n')
    monkeypatch.chdir(tmp_path)
    assert main(["rules", "check"]) == 4
    assert "must look like SHIP-003" in capsys.readouterr().err


def test_rules_check_outside_git_still_checks_without_a_base(tmp_path, monkeypatch, capsys):
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / ".mutineer/rules.toml").write_text(
        'version = 1\n[[rule]]\nid = "A-001"\nstatement = "a"\n'
    )
    monkeypatch.chdir(tmp_path)
    assert main(["rules", "check"]) == 0
    out = capsys.readouterr().out
    assert "Not compared with a base" in out
    assert "INFO rule_unguarded" in out


def test_rules_check_with_unknown_base_exits_4(shop_clone, monkeypatch):
    monkeypatch.chdir(shop_clone("main"))
    assert main(["rules", "check", "--base", "no-such-branch"]) == 4


# ---- init ----


def test_init_writes_a_valid_template_and_gitignore_entries(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    text = (tmp_path / ".mutineer/rules.toml").read_text()
    assert text == RULES_TEMPLATE
    assert load_rules(text).rules == []
    assert (tmp_path / ".gitignore").read_text() == "mutants/\n.mutineer/last-run.json\n"


def test_init_never_overwrites_and_only_appends_missing_entries(tmp_path, monkeypatch):
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / ".mutineer/rules.toml").write_text("version = 1  # mine\n")
    (tmp_path / ".gitignore").write_text(".venv/\n/mutants")
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert (tmp_path / ".mutineer/rules.toml").read_text() == "version = 1  # mine\n"
    assert (tmp_path / ".gitignore").read_text() == ".venv/\n/mutants\n.mutineer/last-run.json\n"


def test_init_twice_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    snapshot = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert main(["init"]) == 0
    assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == snapshot


def test_init_uses_the_configured_rules_file(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[tool.mutineer]\nrules_file = "promises.toml"\n')
    monkeypatch.chdir(tmp_path)
    assert main(["init"]) == 0
    assert (tmp_path / "promises.toml").read_text() == RULES_TEMPLATE
    assert not (tmp_path / ".mutineer").exists()
