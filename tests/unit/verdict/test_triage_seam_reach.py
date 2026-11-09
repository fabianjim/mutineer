"""``survivor_rule`` guidance through ``triage.triage`` with ``seams.seam_reach``

A rule's seam reaches a function when one of the function's rule tests mentions it; when
none does, the guidance names those tests instead of sending the agent through the seam.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mutineer.models import Finding, Rule, RulesFile
from mutineer.seams import seam_reach
from mutineer.triage import triage

RULE_TEST_FILE = "tests/rules/test_shipping_rules.py"


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def survivor(
    build: Any,
    root: Path,
    rules: RulesFile,
    *,
    fn: str | None = None,
    rule_map: dict[str, list[str]] | None = None,
) -> Finding:
    fn = fn or build.quote
    rule_map = rule_map or build.rule_map
    run = build.run(
        functions=[build.fn(fn, "src/shop/shipping.py", 6, 13)],
        results=[build.mutant(fn, 1, "survived", "survived", 8, ("a >= b", "a > b"))],
        tests={fn: sorted(rule_map)},
    )
    (f,) = triage(run, rule_map, rules, seam_reached=seam_reach(root)).findings
    assert f.kind == "survivor_rule"
    assert f.next_action is not None
    return f


def test_a_rule_test_that_calls_the_seam_gets_the_seam_text(
    build: Any, ship_rules: RulesFile, tmp_path: Path
) -> None:
    write(tmp_path, RULE_TEST_FILE, "def test_small_order_pays():\n    assert quote(10) > 0\n")
    f = survivor(build, tmp_path, ship_rules)
    assert f.next_action is not None
    assert f.next_action.startswith(
        "Test SHIP-003 through `shop.shipping.quote` so that this change to line 8 fails."
    )
    assert f.evidence["seams"] == {"SHIP-003": {"seam": "shop.shipping.quote", "reaches": True}}


def test_a_rule_test_that_never_names_the_seam_gets_the_rule_tests_text(
    build: Any, ship_rules: RulesFile, tmp_path: Path
) -> None:
    write(tmp_path, RULE_TEST_FILE, "def test_small_order_pays():\n    assert main(['buy'])\n")
    f = survivor(build, tmp_path, ship_rules)
    assert f.next_action is not None
    assert f.next_action.startswith(
        "`shop.shipping.quote` doesn't reach this code; SHIP-003's tests that do are "
        f"`{build.rule_test}`. Test the promised behaviour the way they do"
    )
    assert "through `shop.shipping.quote`" not in f.next_action
    assert f.evidence["seams"] == {"SHIP-003": {"seam": "shop.shipping.quote", "reaches": False}}


def test_a_rule_with_no_seam_names_its_tests(build: Any, tmp_path: Path) -> None:
    rules = RulesFile(rules=[Rule(id="SHIP-003", statement="free")])
    write(tmp_path, RULE_TEST_FILE, "def test_small_order_pays():\n    assert quote(10) > 0\n")
    f = survivor(build, tmp_path, rules)
    assert f.next_action is not None
    assert f.next_action.startswith(
        f"SHIP-003 names no seam; its tests that reach this code are `{build.rule_test}`."
    )
    assert f.evidence["seams"] == {"SHIP-003": {"seam": None, "reaches": False}}


def test_an_unreadable_test_file_falls_back_to_the_seam_text(
    build: Any, ship_rules: RulesFile, tmp_path: Path
) -> None:
    # no test file written under tmp_path
    f = survivor(build, tmp_path, ship_rules)
    assert f.next_action is not None
    assert f.next_action.startswith("Test SHIP-003 through `shop.shipping.quote`")
    assert f.evidence["seams"]["SHIP-003"]["reaches"] is True


def test_the_rules_001_shape_names_the_rule_tests(build: Any, tmp_path: Path) -> None:
    # a rule test that calls the CLI makes its rule claim a CLI helper the seam never calls
    test = "tests/unit/rules/test_rules_cli_commands.py::test_rules_check_accepts_a_retired_rule"
    write(
        tmp_path,
        test.split("::")[0],
        "import pytest\n\n\n"
        '@pytest.mark.rule("RULES-001")\n'
        "def test_rules_check_accepts_a_retired_rule(tmp_path):\n"
        '    assert main(["rules", "check", str(tmp_path)]) == 0\n',
    )
    rules = RulesFile(
        rules=[
            Rule(id="RULES-001", statement="ids are never deleted", seam="mutineer.rules.compare")
        ]
    )
    f = survivor(
        build,
        tmp_path,
        rules,
        fn="mutineer.cli.rules.x__print_proposals",
        rule_map={test: ["RULES-001"]},
    )
    assert f.next_action is not None
    assert f.next_action.startswith(
        f"`mutineer.rules.compare` doesn't reach this code; RULES-001's tests that do are `{test}`."
    )


@pytest.mark.parametrize("calls", ["quote(10)", "main(['buy'])"])
def test_every_survivor_rule_says_to_mark_the_test_and_names_dispute(
    build: Any, ship_rules: RulesFile, tmp_path: Path, calls: str
) -> None:
    write(tmp_path, RULE_TEST_FILE, f"def test_small_order_pays():\n    assert {calls}\n")
    f = survivor(build, tmp_path, ship_rules)
    assert f.next_action is not None
    assert '@pytest.mark.rule("SHIP-003")' in f.next_action
    assert "an unmarked test can't clear this finding" in f.next_action
    assert "mutineer dispute <this finding's id>" in f.next_action


def test_each_rule_gets_its_own_text(build: Any, tmp_path: Path) -> None:
    other = "tests/rules/test_other.py::test_other"
    write(tmp_path, RULE_TEST_FILE, "def test_small_order_pays():\n    assert quote(10) > 0\n")
    write(tmp_path, "tests/rules/test_other.py", "def test_other():\n    assert main([])\n")
    rules = RulesFile(
        rules=[
            Rule(id="SHIP-003", statement="free", seam="shop.shipping.quote"),
            Rule(id="SHIP-004", statement="fast", seam="shop.shipping.express"),
        ]
    )
    f = survivor(
        build,
        tmp_path,
        rules,
        rule_map={build.rule_test: ["SHIP-003"], other: ["SHIP-004"]},
    )
    assert f.evidence["seams"] == {
        "SHIP-003": {"seam": "shop.shipping.quote", "reaches": True},
        "SHIP-004": {"seam": "shop.shipping.express", "reaches": False},
    }
    assert f.next_action is not None
    assert "Test SHIP-003 through `shop.shipping.quote`" in f.next_action
    assert f"SHIP-004's tests that do are `{other}`" in f.next_action
    assert '@pytest.mark.rule("<the rule you tested>")' in f.next_action
