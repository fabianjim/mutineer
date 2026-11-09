"""``mutineer run`` and ``recheck`` end to end on the fixture repo. Slow: runs mutmut.

Mutants are identified by line and source text, never by mutmut's numbering.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

from mutineer.config import load_config
from mutineer.rules import collect_rule_map
from mutineer.testscan import is_test_file, scan_source

pytestmark = pytest.mark.slow

BRANCHES = ["main", "agent", "agent-gap", "agent-tamper", "agent-tamper-approved"]
FAILING_TEST = "def test_broken():\n    assert 1 == 2\n"
THRESHOLD_TEST = """

def test_exactly_fifty_ships_free():
    assert quote(50) == 0
"""


def of_kind(report: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [f for f in report["findings"] if f["kind"] == kind]


def score(report: dict[str, Any], name: str) -> tuple[int, int] | None:
    s = report[name]
    return None if s is None else (s["killed"], s["total"])


##
# the acceptance table


def test_agent_passes_with_questions_for_a_human(shop_repo: Any, mutineer: Any) -> None:
    shop_repo.checkout("agent")
    code, report = mutineer(shop_repo.path, "run")
    assert (report["status"], code) == ("pass", 0)
    unowned = of_kind(report, "survivor_unowned")
    assert [f["evidence"]["function"] for f in unowned] == [
        "shop.pricing.x_discount",
        "shop.pricing.x_loyalty_points",
    ]
    assert [f for f in report["findings"] if f["audience"] == "agent"] == []
    assert score(report, "rule_score") is None


def test_agent_gap_warns_and_sends_two_survivors_to_the_agent(
    shop_repo: Any, mutineer: Any
) -> None:
    shop_repo.checkout("agent-gap")
    code, report = mutineer(shop_repo.path, "run")
    assert (report["status"], code) == ("warn", 0)

    survivors = of_kind(report, "survivor_rule")
    assert [(f["location"]["file"], f["location"]["line"]) for f in survivors] == [
        ("src/shop/shipping.py", 8),
        ("src/shop/shipping.py", 10),
    ]
    assert [f["evidence"]["mutated"] for f in survivors] == [
        "if total > FREE_SHIPPING_MIN:",
        "rate = _rate(None)",
    ]
    for f in survivors:
        assert (f["rules"], f["severity"], f["audience"]) == (["SHIP-003"], "warn", "agent")
        assert "`shop.shipping.quote`" in f["next_action"]
        assert f"line {f['location']['line']}" in f["next_action"]

    (guarded,) = of_kind(report, "guarded_by_impl_only")
    assert sorted(m["mutated"] for m in guarded["evidence"]["mutants"]) == [
        "rate -= EXPRESS_FEE",
        "rate = EXPRESS_FEE",
    ]
    (unmeasured,) = of_kind(report, "unmeasured_change")
    assert unmeasured["location"] == {"file": "src/shop/shipping.py", "line": 3, "end_line": 3}
    assert unmeasured["evidence"]["reasons"] == ["module_level"]

    assert score(report, "rule_score") == (3, 7)
    assert score(report, "diff_score") == (5, 7)
    assert mutineer(shop_repo.path, "run", "--strict")[0] == 1


@pytest.mark.rule("VERDICT-001")
def test_agent_tamper_blocks(shop_repo: Any, mutineer: Any) -> None:
    shop_repo.checkout("agent-tamper")
    code, report = mutineer(shop_repo.path, "run")
    assert (report["status"], code) == ("block", 2)
    (weakened,) = of_kind(report, "rule_test_weakened")
    assert weakened["severity"] == "block"
    assert report["findings"][0]["kind"] == "rule_test_weakened"


def test_agent_tamper_approved_warns(shop_repo: Any, mutineer: Any) -> None:
    shop_repo.checkout("agent-tamper-approved")
    code, report = mutineer(shop_repo.path, "run")
    assert (report["status"], code) == ("warn", 0)
    (weakened,) = of_kind(report, "rule_test_weakened")
    assert weakened["severity"] == "warn"
    (changed,) = of_kind(report, "rules_file_changed")
    assert changed["rules"] == ["SHIP-003"]


@pytest.mark.rule("VERDICT-002")
def test_a_failing_test_on_main_blocks(shop_repo: Any, mutineer: Any) -> None:
    shop_repo.checkout("main")
    shop_repo.write("tests/test_broken.py", FAILING_TEST)  # uncommitted, no function changed
    code, report = mutineer(shop_repo.path, "run")
    assert (report["status"], code) == ("block", 2)
    (failed,) = of_kind(report, "baseline_failed")
    assert failed["severity"] == "block"
    assert "test_broken" in failed["evidence"]["output"]


@pytest.mark.rule("TRIAGE-001")
def test_agent_output_for_agent_gap_has_no_human_findings(shop_repo: Any, mutineer: Any) -> None:
    shop_repo.checkout("agent-gap")
    _, full = mutineer(shop_repo.path, "run")
    _, agent = mutineer(shop_repo.path, "run", fmt="agent")
    human_kinds = {f["kind"] for f in full["findings"] if f["audience"] == "human"}
    assert human_kinds  # there are some, and they stay out
    assert [f["kind"] for f in agent["findings"]] == ["survivor_rule", "survivor_rule"]
    agent_ids = {f["id"] for f in full["findings"] if f["audience"] == "agent"}
    assert {f["id"] for f in agent["findings"]} == agent_ids
    assert (agent["status"], agent["rule_score"]) == ("warn", {"killed": 3, "total": 7})


@pytest.mark.rule("TRIAGE-002")
def test_a_changed_property_is_unmeasured_and_changes_no_score(
    shop_copy: Any, mutineer: Any
) -> None:
    shop_copy.checkout("agent")
    _, before = mutineer(shop_copy.path, "run")
    cart = shop_copy.path / "src/shop/cart.py"
    cart.write_text(
        cart.read_text().replace("return sum(self.items)", "return sum(self.items) + 0")
    )
    _, after = mutineer(shop_copy.path, "run")

    assert of_kind(before, "unmeasured_change") == []
    (unmeasured,) = of_kind(after, "unmeasured_change")
    assert unmeasured["location"]["file"] == "src/shop/cart.py"
    assert unmeasured["evidence"]["reasons"] == ["decorated"]
    assert score(after, "diff_score") == score(before, "diff_score")
    assert score(after, "rule_score") == score(before, "rule_score")


##
# recheck


def test_recheck_after_a_new_rule_test(shop_copy: Any, mutineer: Any) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    mutineer(root, "run")
    with (root / "tests/rules/test_shipping_rules.py").open("a") as f:
        f.write(THRESHOLD_TEST)

    code, report = mutineer(root, "recheck")
    assert (report["status"], code) == ("warn", 0)
    assert [f["location"]["line"] for f in of_kind(report, "survivor_rule")] == [10]
    assert score(report, "rule_score") == (4, 7)
    assert score(report, "diff_score") == (5, 7)
    # the new test isn't tampering, and the static checks ran again on the new tree
    assert [f for f in report["findings"] if f["severity"] == "block"] == []

    assert [mutineer(root, "recheck")[0] for _ in range(2)] == [0, 0]
    code, err = mutineer(root, "recheck")
    assert code == 3
    assert "a human should review" in err


##
# static and runtime rule identity


@pytest.mark.parametrize("branch", BRANCHES)
def test_static_and_runtime_rule_identity_agree(shop_repo: Any, branch: str) -> None:
    shop_repo.checkout(branch)
    root: Path = shop_repo.path
    config = load_config(root)
    static: dict[str, list[str]] = {}
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if is_test_file(rel, config.test_paths):
            for test in scan_source(path.read_text(), rel):
                if test.rules:
                    static[test.key] = sorted(test.rules)
    runtime: dict[str, list[str]] = {}
    for node_id, ids in collect_rule_map(root).items():
        runtime.setdefault(re.sub(r"\[.*\]$", "", node_id), sorted(ids))
    assert static == runtime
    assert static  # SHIP-003 is guarded on every branch


##
# writes


def _status_outside_mutineer(root: Path) -> list[str]:
    out = subprocess.run(
        ["git", "status", "--porcelain", "--ignored", "--untracked-files=all"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [
        line
        for line in out.splitlines()
        if not line[3:].startswith(("mutants/", ".mutineer/")) and line[3:] != "mutants"
    ]


@pytest.mark.rule("ENGINE-001")
def test_run_and_recheck_write_only_under_mutants_and_mutineer(
    shop_copy: Any, mutineer: Any
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    (root / ".gitignore").write_text(".venv/\n")  # no __pycache__/, mutants/ or last-run.json
    shop_copy.commit("bare gitignore")
    assert _status_outside_mutineer(root) == []

    for fmt in ("terminal", "markdown", "json", "agent"):
        code, _ = mutineer(root, "run", fmt=fmt, to_stdout=True)
        assert code == 0
    assert mutineer(root, "recheck", fmt="terminal", to_stdout=True)[0] == 0
    assert mutineer(root, "run", "--no-mutation", fmt="terminal", to_stdout=True)[0] == 0

    assert _status_outside_mutineer(root) == []
    assert (root / ".mutineer" / "last-run.json").is_file()
