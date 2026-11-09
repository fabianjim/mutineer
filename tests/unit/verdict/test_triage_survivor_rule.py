"""What a ``survivor_rule`` sends the agent: the message, the location and the guidance

The guidance is the agent's whole instruction for the mutant, so its text is checked in
full: how to test it, how to mark the test, and when a dispute is allowed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from mutineer.models import Finding, MutantResult, MutantStatus, Rule, RulesFile
from mutineer.seams import seam_reach
from mutineer.triage import triage

TEXT = ("a >= b", "a > b")
OTHER_TEST = "tests/rules/test_other.py::test_other"

CLOSING = (
    "an unmarked test can't clear this finding. Don't mark tests that only exercise wiring, "
    "and never mark with an id that isn't in the rules file. Don't assert on private names "
    "or mocks. Only if no caller can observe the change, run mutineer dispute <this "
    'finding\'s id> "<why>"; "the seam doesn\'t reach this code" is a valid reason when the '
    "rule tests reach it only through wiring."
)


def survivor(
    build: Any,
    rules: RulesFile,
    *,
    rule_map: dict[str, list[str]] | None = None,
    line: int | None = 8,
    with_ref: bool = True,
    root: Path | None = None,
) -> Finding:
    q = build.quote
    rule_map = rule_map or build.rule_map
    run = build.run(
        functions=[build.fn(q, "src/shop/shipping.py", 6, 13)] if with_ref else [],
        results=[build.mutant(q, 1, "survived", "survived", line, TEXT)],
        tests={q: sorted(rule_map)},
    )
    reached = seam_reach(root) if root is not None else None
    (f,) = triage(run, rule_map, rules, seam_reached=reached).findings
    assert f.kind == "survivor_rule"
    return f


@pytest.mark.rule("TRIAGE-001")
def test_one_rule_whose_seam_reaches_gets_the_full_guidance(
    build: Any, ship_rules: RulesFile
) -> None:
    f = survivor(build, ship_rules)
    assert f.message == (
        "Nothing catches this: `a >= b` changed to `a > b` in quote and every test passes."
    )
    assert f.location is not None
    assert (f.location.file, f.location.line) == ("src/shop/shipping.py", 8)
    assert f.next_action == (
        "Test SHIP-003 through `shop.shipping.quote` so that this change to line 8 fails. "
        'Mark the new test @pytest.mark.rule("SHIP-003") with that existing rule; ' + CLOSING
    )


@pytest.mark.rule("TRIAGE-001")
def test_two_rules_where_one_seam_does_not_reach_get_the_full_guidance(
    build: Any, tmp_path: Path
) -> None:
    second = "tests/rules/test_other.py::test_other_too"
    (tmp_path / "tests" / "rules").mkdir(parents=True)
    (tmp_path / "tests/rules/test_shipping_rules.py").write_text(
        "def test_small_order_pays():\n    assert quote(10) > 0\n"
    )
    (tmp_path / "tests/rules/test_other.py").write_text(
        "def test_other():\n    assert main([])\n\n\ndef test_other_too():\n    assert main([])\n"
    )
    rules = RulesFile(
        rules=[
            Rule(id="SHIP-003", statement="free", seam="shop.shipping.quote"),
            Rule(id="SHIP-004", statement="fast", seam="shop.shipping.express"),
        ]
    )
    f = survivor(
        build,
        rules,
        rule_map={build.rule_test: ["SHIP-003"], OTHER_TEST: ["SHIP-004"], second: ["SHIP-004"]},
        root=tmp_path,
    )
    assert f.next_action == (
        "Any one of these rules will do. "
        "Test SHIP-003 through `shop.shipping.quote` so that this change to line 8 fails. "
        "`shop.shipping.express` doesn't reach this code; SHIP-004's tests that do are "
        f"`{OTHER_TEST}`, `{second}`. Test the promised behaviour the way they do, so that "
        "this change to line 8 fails. "
        'Mark the new test @pytest.mark.rule("<the rule you tested>") with that existing rule; '
        + CLOSING
    )


@pytest.mark.rule("TRIAGE-001")
def test_a_mutant_without_a_line_points_at_the_function(build: Any, ship_rules: RulesFile) -> None:
    f = survivor(build, ship_rules, line=None)
    assert f.next_action is not None
    assert f.next_action.startswith(
        "Test SHIP-003 through `shop.shipping.quote` so that this change to this function fails."
    )


@pytest.mark.rule("TRIAGE-001")
def test_a_function_mutmut_did_not_locate_is_named_by_its_mutmut_name(
    build: Any, ship_rules: RulesFile
) -> None:
    f = survivor(build, ship_rules, with_ref=False)
    assert f.location is None
    assert f.message == (
        f"Nothing catches this: `a >= b` changed to `a > b` in {build.quote} and every test passes."
    )


@pytest.mark.rule("TRIAGE-001")
def test_a_mutant_with_only_part_of_its_text_is_named_by_its_mutant_name(
    build: Any, ship_rules: RulesFile
) -> None:
    q = build.quote
    results = [
        MutantResult(
            name=f"{q}__mutmut_1",
            function=q,
            run=run,
            status=MutantStatus.SURVIVED,
            line=8,
            original="a >= b",
            mutated=None,
        )
        for run in ("all", "rule")
    ]
    run = build.run(
        functions=[build.fn(q, "src/shop/shipping.py", 6, 13)],
        results=[results],
        tests={q: [build.rule_test]},
    )
    (f,) = triage(run, build.rule_map, ship_rules).findings
    assert f.kind == "survivor_rule"
    assert f.message == (
        f"Nothing catches this: mutant `{q}__mutmut_1` in quote and every test passes."
    )


@pytest.mark.rule("TRIAGE-001")
def test_a_rule_missing_from_the_rules_file_still_sends_the_survivor(build: Any) -> None:
    f = survivor(build, RulesFile(rules=[]))
    assert f.rules == ["SHIP-003"]
    assert f.evidence["seams"] == {"SHIP-003": {"seam": None, "reaches": False}}
    assert f.next_action is not None
    assert f.next_action.startswith(
        f"SHIP-003 names no seam; its tests that reach this code are `{build.rule_test}`."
    )


@pytest.mark.parametrize(
    ("seam", "body"),
    [
        ("quote", "assert quote(10) > 0"),
        ("shop.shipping", "import shop.shipping as s\n\n    assert s.quote(10) > 0"),
    ],
)
@pytest.mark.rule("TRIAGE-001")
def test_a_seam_is_reached_by_its_last_name_or_an_import_of_it(
    build: Any, tmp_path: Path, seam: str, body: str
) -> None:
    (tmp_path / "tests" / "rules").mkdir(parents=True)
    (tmp_path / "tests/rules/test_shipping_rules.py").write_text(
        f"def test_small_order_pays():\n    {body}\n"
    )
    rules = RulesFile(rules=[Rule(id="SHIP-003", statement="free", seam=seam)])
    f = survivor(build, rules, root=tmp_path)
    assert f.evidence["seams"] == {"SHIP-003": {"seam": seam, "reaches": True}}
    assert f.next_action is not None
    assert f.next_action.startswith(f"Test SHIP-003 through `{seam}`")
