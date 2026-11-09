"""``mutineer rules accept``: proposals become rules and markers, or nothing changes"""

from __future__ import annotations

import io
import tomllib

import pytest

from mutineer.cli import main
from mutineer.ledger import PROPOSALS_FILE
from mutineer.testscan import scan_source

MEMBER = "tests/test_pricing.py::test_member_discount"
HELPER = "tests/test_pricing.py::test_helper_runs"
RULES = ".mutineer/rules.toml"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.fixture
def shop(shop_clone, monkeypatch):
    root = shop_clone("main")
    monkeypatch.chdir(root)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    return root


def _propose(*argv):
    assert main(["rules", "propose", *argv]) == 0


def _snapshot(root):
    files = [root / RULES, root / PROPOSALS_FILE, *sorted((root / "tests").rglob("*.py"))]
    return {p: p.read_bytes() for p in files if p.exists()}


def _rules_of(root, node_id):
    file, _, qualname = node_id.partition("::")
    tests = scan_source((root / file).read_text(), file)
    (test,) = [t for t in tests if t.qualname == qualname]
    return test.rules


def test_accept_adds_the_rule_marks_the_test_and_drops_the_proposal(shop, capsys):
    _propose("--id", "PRICE-001", "--statement", "Members save", "--test", MEMBER)
    _propose("--id", "PRICE-002", "--statement", "Other", "--seam", "shop.pricing.discount")
    rules_before = (shop / RULES).read_text()
    capsys.readouterr()

    assert main(["rules", "accept", "PRICE-001", "--yes"]) == 0
    out = capsys.readouterr().out
    assert "ACCEPTED PRICE-001" in out
    assert "rules_file_changed" in out
    assert (shop / RULES).read_text() == (
        rules_before + '\n[[rule]]\nid = "PRICE-001"\nstatement = "Members save"\n'
    )
    assert _rules_of(shop, MEMBER) == ("PRICE-001",)
    proposals = tomllib.loads((shop / PROPOSALS_FILE).read_text())["proposal"]
    assert [p["id"] for p in proposals] == ["PRICE-002"]


def test_accept_tests_for_an_existing_rule_only_marks_them(shop):
    _propose("--rule", "SHIP-003", "--test", MEMBER, "--test", HELPER)
    rules_before = (shop / RULES).read_bytes()
    assert main(["rules", "accept", "SHIP-003", "--yes"]) == 0
    assert (shop / RULES).read_bytes() == rules_before
    assert _rules_of(shop, MEMBER) == ("SHIP-003",)
    assert _rules_of(shop, HELPER) == ("SHIP-003",)
    assert (shop / "tests/test_pricing.py").read_text().count("import pytest") == 1


def test_accept_all_accepts_everything(shop):
    _propose("--id", "PRICE-001", "--statement", "a", "--seam", "shop.pricing.discount")
    _propose("--id", "PRICE-002", "--statement", "b", "--test", MEMBER)
    _propose("--rule", "SHIP-003", "--test", MEMBER)
    assert main(["rules", "accept", "--all", "--yes"]) == 0
    rules = tomllib.loads((shop / RULES).read_text())["rule"]
    assert [r["id"] for r in rules] == ["SHIP-003", "PRICE-001", "PRICE-002"]
    assert rules[1]["seam"] == "shop.pricing.discount"
    assert _rules_of(shop, MEMBER) == ("PRICE-002", "SHIP-003")
    assert "proposal" not in tomllib.loads((shop / PROPOSALS_FILE).read_text())


def test_an_unknown_id_exits_4_and_changes_nothing(shop, capsys):
    _propose("--id", "PRICE-001", "--statement", "a", "--test", MEMBER)
    before = _snapshot(shop)
    assert main(["rules", "accept", "PRICE-001", "NOPE-001", "--yes"]) == 4
    assert "no pending proposal NOPE-001" in capsys.readouterr().err
    assert _snapshot(shop) == before


def test_a_stale_proposal_exits_4_and_changes_nothing(shop, capsys):
    _propose("--id", "PRICE-001", "--statement", "a", "--test", HELPER)
    (shop / "tests/test_pricing.py").write_text("def test_member_discount():\n    pass\n")
    before = _snapshot(shop)
    assert main(["rules", "accept", "PRICE-001", "--yes"]) == 4
    err = capsys.readouterr().err
    assert f"PRICE-001: test {HELPER}: pytest doesn't collect it" in err
    assert "nothing was written" in err
    assert _snapshot(shop) == before


def test_an_inline_rules_array_exits_4_and_changes_nothing(shop, capsys):
    _propose("--id", "PRICE-001", "--statement", "a", "--test", MEMBER)
    (shop / RULES).write_text(
        'version = 1\nrule = [{ id = "SHIP-003", statement = "Orders of $50 ship free" }]\n'
    )
    before = _snapshot(shop)
    assert main(["rules", "accept", "PRICE-001", "--yes"]) == 4
    assert "add the rules by hand" in capsys.readouterr().err
    assert _snapshot(shop) == before


def test_a_missing_rules_file_exits_4(shop, capsys):
    _propose("--id", "PRICE-001", "--statement", "a", "--test", MEMBER)
    (shop / RULES).unlink()
    before = _snapshot(shop)
    assert main(["rules", "accept", "PRICE-001", "--yes"]) == 4
    assert "run `mutineer init` first" in capsys.readouterr().err
    assert _snapshot(shop) == before


def test_no_terminal_without_yes_exits_4_and_changes_nothing(shop, capsys):
    _propose("--id", "PRICE-001", "--statement", "a", "--test", MEMBER)
    before = _snapshot(shop)
    assert main(["rules", "accept", "PRICE-001"]) == 4
    assert "run it in a terminal, or pass --yes" in capsys.readouterr().err
    assert _snapshot(shop) == before


def test_answering_no_in_a_terminal_changes_nothing(shop, monkeypatch, capsys):
    _propose("--id", "PRICE-001", "--statement", "a", "--test", MEMBER)
    before = _snapshot(shop)
    monkeypatch.setattr("sys.stdin", _Tty("n\n"))
    assert main(["rules", "accept", "PRICE-001"]) == 4
    captured = capsys.readouterr()
    assert f"{RULES}: add PRICE-001" in captured.out
    assert "Proceed? [y/N]" in captured.out
    assert "Nothing changed." in captured.err
    assert _snapshot(shop) == before


def test_answering_yes_in_a_terminal_accepts(shop, monkeypatch):
    _propose("--id", "PRICE-001", "--statement", "a", "--test", MEMBER)
    monkeypatch.setattr("sys.stdin", _Tty("y\n"))
    assert main(["rules", "accept", "PRICE-001"]) == 0
    assert _rules_of(shop, MEMBER) == ("PRICE-001",)


@pytest.mark.parametrize("argv", [["rules", "accept"], ["rules", "accept", "A-001", "--all"]])
def test_ids_or_all_is_required(shop, argv):
    assert main([*argv, "--yes"]) == 4


def test_a_parametrized_test_gets_one_mark_and_a_note(shop, capsys):
    (shop / "tests/test_param.py").write_text(
        'import pytest\n\n\n@pytest.mark.parametrize("x", [1, 2])\ndef test_p(x):\n    assert x\n'
    )
    node = "tests/test_param.py::test_p"
    _propose("--id", "PRICE-001", "--statement", "a", "--test", f"{node}[1]", "--test", node)
    capsys.readouterr()
    assert main(["rules", "accept", "PRICE-001", "--yes"]) == 0
    assert "applies to all 2 parametrize cases" in capsys.readouterr().out
    text = (shop / "tests/test_param.py").read_text()
    assert text.count('mark.rule("PRICE-001")') == 1
