"""``mutineer rules propose``, ``rules reject`` and proposals in ``rules check``"""

from __future__ import annotations

import io
import json
import tomllib

import pytest

from mutineer.cli import main
from mutineer.ledger import PROPOSALS_FILE

MEMBER = "tests/test_pricing.py::test_member_discount"


@pytest.fixture
def shop(shop_clone, monkeypatch):
    root = shop_clone("main")
    monkeypatch.chdir(root)
    return root


def _proposals(root):
    return tomllib.loads((root / PROPOSALS_FILE).read_text())["proposal"]


def test_propose_with_a_prefix_writes_the_proposal(shop, capsys):
    argv = ["rules", "propose", "--prefix", "PRICE", "--statement", "Members get 10% off"]
    assert main([*argv, "--seam", "shop.pricing.discount", "--test", MEMBER]) == 0
    assert capsys.readouterr().out == "PROPOSED PRICE-001  Members get 10% off\n"
    assert _proposals(shop) == [
        {
            "id": "PRICE-001",
            "statement": "Members get 10% off",
            "seam": "shop.pricing.discount",
            "tests": [MEMBER],
        }
    ]


def test_propose_a_new_rule_without_tests_notes_it_will_be_unguarded(shop, capsys):
    assert main(["rules", "propose", "--id", "PRICE-007", "--statement", "x"]) == 0
    out = capsys.readouterr().out
    assert "PROPOSED PRICE-007  x" in out
    assert "unguarded after accept" in out


def test_propose_tests_for_an_existing_rule_merges(shop, capsys):
    assert main(["rules", "propose", "--rule", "SHIP-003", "--test", MEMBER]) == 0
    assert main(["rules", "propose", "--rule", "SHIP-003", "--test", MEMBER]) == 0
    assert "PROPOSED SHIP-003  +1 tests" in capsys.readouterr().out
    assert _proposals(shop) == [{"id": "SHIP-003", "tests": [MEMBER]}]


def test_propose_problems_exit_4_and_write_nothing(shop, capsys):
    argv = ["rules", "propose", "--id", "SHIP-003", "--statement", "dup"]
    assert main([*argv, "--seam", "shop.nope"]) == 4
    err = capsys.readouterr().err
    assert "SHIP-003 is already in the rules file" in err
    assert "seam shop.nope" in err
    assert "nothing was written" in err
    assert not (shop / PROPOSALS_FILE).exists()


@pytest.mark.parametrize(
    "argv",
    [
        ["rules", "propose", "--statement", "x"],
        ["rules", "propose", "--prefix", "A", "--rule", "SHIP-003"],
        ["rules", "propose", "--batch", "-", "--test", MEMBER],
    ],
)
def test_propose_usage_errors_exit_4(shop, argv):
    assert main(argv) == 4
    assert not (shop / PROPOSALS_FILE).exists()


def test_batch_writes_every_proposal_in_order(shop, monkeypatch, capsys):
    batch = [
        {"prefix": "PRICE", "statement": "one", "tests": [MEMBER]},
        {"rule": "SHIP-003", "tests": [MEMBER]},
        {"prefix": "PRICE", "statement": "two", "seam": "shop.pricing.unchanged_helper"},
    ]
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(batch)))
    assert main(["rules", "propose", "--batch", "-"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[:3] == [
        "PROPOSED PRICE-001  one",
        "PROPOSED SHIP-003  +1 tests",
        "PROPOSED PRICE-002  two",
    ]
    assert [p["id"] for p in _proposals(shop)] == ["PRICE-001", "SHIP-003", "PRICE-002"]


def test_batch_is_all_or_nothing(shop, tmp_path, capsys):
    batch = [
        {"prefix": "PRICE", "statement": "fine", "tests": [MEMBER]},
        {"rule": "NOPE-001", "tests": [MEMBER]},
        {"prefix": "PRICE", "statement": "bad", "colour": "red"},
        "not an object",
    ]
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(batch))
    assert main(["rules", "propose", "--batch", str(path)]) == 4
    err = capsys.readouterr().err
    assert "[2] unknown key 'colour'" in err
    assert "[3] each entry must be a JSON object" in err
    assert not (shop / PROPOSALS_FILE).exists()

    path.write_text(json.dumps(batch[:2]))
    assert main(["rules", "propose", "--batch", str(path)]) == 4
    assert "[1] NOPE-001 is not in the rules file" in capsys.readouterr().err
    assert not (shop / PROPOSALS_FILE).exists()


def test_propose_leaves_rules_and_tests_untouched(shop, run_git):
    assert (
        main(["rules", "propose", "--prefix", "PRICE", "--statement", "x", "--test", MEMBER]) == 0
    )
    assert run_git(shop, "status", "--porcelain").splitlines() == [f"?? {PROPOSALS_FILE}"]


# ---- reject ----


def test_reject_a_known_id_removes_it(shop, capsys):
    assert main(["rules", "propose", "--id", "A-001", "--statement", "a"]) == 0
    assert main(["rules", "propose", "--id", "B-001", "--statement", "b"]) == 0
    assert main(["rules", "reject", "A-001"]) == 0
    assert "REJECTED A-001" in capsys.readouterr().out
    assert [p["id"] for p in _proposals(shop)] == ["B-001"]


def test_reject_an_unknown_id_exits_4_and_removes_nothing(shop, capsys):
    assert main(["rules", "propose", "--id", "A-001", "--statement", "a"]) == 0
    before = (shop / PROPOSALS_FILE).read_bytes()
    assert main(["rules", "reject", "A-001", "Z-001"]) == 4
    assert "no pending proposal Z-001" in capsys.readouterr().err
    assert (shop / PROPOSALS_FILE).read_bytes() == before


# ---- rules check ----


def test_rules_check_lists_proposals_and_stale_problems(shop, capsys):
    assert main(["rules", "propose", "--rule", "SHIP-003", "--test", MEMBER]) == 0
    assert main(["rules", "propose", "--id", "PRICE-001", "--statement", "Members save"]) == 0
    # the proposed test disappears afterwards: the proposal is stale
    (shop / "tests/test_pricing.py").write_text("def test_other():\n    pass\n")
    capsys.readouterr()
    assert main(["rules", "check"]) == 0
    out = capsys.readouterr().out
    assert "Proposed (not accepted):" in out
    assert "SHIP-003: +tests" in out
    assert "PRICE-001: Members save" in out
    assert f"  - {MEMBER}" in out
    assert f"STALE SHIP-003: test {MEMBER}: pytest doesn't collect it" in out


def test_rules_check_shows_each_proposal_with_its_seam_and_tests(shop, capsys):
    assert main(["rules", "propose", "--rule", "SHIP-003", "--test", MEMBER]) == 0
    argv = ["--statement", "Members save", "--seam", "shop.pricing.discount"]
    assert main(["rules", "propose", "--id", "PRICE-001", *argv]) == 0
    capsys.readouterr()
    assert main(["rules", "check"]) == 0
    out = capsys.readouterr().out
    section = (
        "\n\nProposed (not accepted):\n"
        "SHIP-003: +tests\n"
        f"  - {MEMBER}\n"
        "PRICE-001: Members save\n"
        "  seam: shop.pricing.discount\n"
        "  no tests\n"
    )
    assert out.endswith(section)
