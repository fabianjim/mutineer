"""The ledger loop on the shop fixture, from zero rules: run, propose, accept, dismiss, run (slow)

Mutants are identified by their mutated source text, never by mutmut's numbering.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from mutineer.cli import main
from mutineer.cli.init import RULES_TEMPLATE
from mutineer.reporters.agent import DONE_INSTRUCTION, WORK_INSTRUCTION

pytestmark = pytest.mark.slow

DISCOUNT = "shop.pricing.x_discount"
POINTS = "shop.pricing.x_loyalty_points"
MEMBER = "tests/test_pricing.py::test_member_discount"
SHIP_TESTS = [
    "tests/rules/test_shipping_rules.py::test_well_over_threshold_ships_free",
    "tests/rules/test_shipping_rules.py::test_small_order_pays",
]
BATCH = [
    {
        "prefix": "PRICE",
        "statement": "Members get 10% off orders over $100",
        "seam": "shop.pricing.discount",
        "tests": [MEMBER],
    },
    {
        "prefix": "SHIP",
        "statement": "Orders of $50 or more ship free",
        "seam": "shop.shipping.quote",
        "tests": SHIP_TESTS,
    },
]
LEDGER_FILES = [
    ".mutineer/rules.toml",
    ".mutineer/proposals.toml",
    "tests/test_pricing.py",
    "tests/rules/test_shipping_rules.py",
]


def of_kind(report: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [f for f in report["findings"] if f["kind"] == kind]


def about(report: dict[str, Any], kind: str, function: str) -> dict[str, Any]:
    (found,) = [f for f in of_kind(report, kind) if f["evidence"].get("function") == function]
    return found


def snapshot(root: Path) -> dict[str, str | None]:
    return {
        rel: (root / rel).read_text() if (root / rel).exists() else None for rel in LEDGER_FILES
    }


def propose_batch(monkeypatch: pytest.MonkeyPatch, root: Path, batch: list[Any]) -> int:
    monkeypatch.chdir(root)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(batch)))
    return main(["rules", "propose", "--batch", "-"])


def test_the_ledger_grows_from_zero_rules(
    shop_copy: Any, mutineer: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    root = shop_copy.path
    # zero rules at the base, then the agent's change on top
    shop_copy.write(".mutineer/rules.toml", RULES_TEMPLATE)
    ship_tests = root / "tests/rules/test_shipping_rules.py"
    ship_tests.write_text(
        ship_tests.read_text().replace('pytestmark = pytest.mark.rule("SHIP-003")\n', "")
    )
    shop_copy.commit("no rules yet")
    shop_copy.git("checkout", "-q", "-b", "work")
    shop_copy.git("checkout", "agent", "--", "src/shop/pricing.py", "tests/test_pricing.py")

    # 1. every survivor is a question for a human
    code, report = mutineer(root, "run")
    assert (report["status"], code) == ("pass", 0)
    assert report["rule_score"] is None
    unowned = of_kind(report, "survivor_unowned")
    assert [f["evidence"]["function"] for f in unowned] == [DISCOUNT, POINTS]
    for f in unowned:
        function = f["evidence"]["function"]
        assert "mutineer rules propose" in f["next_action"]
        assert f"mutineer rules dismiss {function}" in f["next_action"]
    points_tests = about(report, "survivor_unowned", POINTS)["evidence"]["tests"]
    assert MEMBER in about(report, "survivor_unowned", DISCOUNT)["evidence"]["tests"]
    assert "tests/test_pricing.py::test_points" in points_tests
    assert mutineer(root, "run", fmt="agent")[1]["instruction"] == DONE_INSTRUCTION

    # 2. a batch with one bad entry writes nothing
    before = snapshot(root)
    bad = [BATCH[0], {**BATCH[1], "seam": "shop.shipping.no_such_function"}]
    assert propose_batch(monkeypatch, root, bad) == 4
    assert snapshot(root) == before

    # 3. the agent proposes; nothing is protected yet
    capsys.readouterr()
    assert propose_batch(monkeypatch, root, BATCH) == 0, capsys.readouterr().err
    out = capsys.readouterr().out
    assert "PROPOSED PRICE-001" in out and "PROPOSED SHIP-001" in out

    code, report = mutineer(root, "run")
    assert (report["status"], code) == ("pass", 0)
    assert of_kind(report, "survivor_rule") == of_kind(report, "unknown_rule_id") == []
    assert about(report, "survivor_unowned", DISCOUNT)["evidence"]["proposed"] == ["PRICE-001"]

    # 4. the human accepts and dismisses; without a terminal or --yes, accept refuses
    before = snapshot(root)
    assert main(["rules", "accept", "PRICE-001"]) == 4
    assert snapshot(root) == before
    assert main(["rules", "accept", "--all", "--yes"]) == 0, capsys.readouterr().err
    reason = "marketing detail"
    assert main(["rules", "dismiss", POINTS, "--reason", reason, "--yes"]) == 0, (
        capsys.readouterr().err
    )

    # 5. the accepted rule now sends work to the agent, and each change is visible
    code, report = mutineer(root, "run")
    assert (report["status"], code) == ("warn", 0)
    assert report["rule_score"] is not None
    survivors = [
        f for f in of_kind(report, "survivor_rule") if f["evidence"]["function"] == DISCOUNT
    ]
    assert survivors
    for f in survivors:
        assert f["rules"] == ["PRICE-001"]
    assert "if member or total > 100:" in [f["evidence"]["mutated"] for f in survivors]

    changed = sorted(rule for f in of_kind(report, "rules_file_changed") for rule in f["rules"])
    assert changed == ["PRICE-001", "SHIP-001"]
    tamper = [f for f in report["findings"] if f["check"] == "tamper" and f["severity"] != "info"]
    assert tamper == []
    assert about(report, "survivor_unowned", POINTS)["evidence"]["answer"] == {
        "reason": reason,
        "question": "survivor_unowned",
    }
    assert [f["evidence"]["function"] for f in of_kind(report, "answer_added")] == [POINTS]
    assert mutineer(root, "run", fmt="agent")[1]["instruction"] == WORK_INSTRUCTION
