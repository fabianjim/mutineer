"""Triage through ``triage.triage``: the two-pass table, grouping and scores"""

from __future__ import annotations

from typing import Any

import pytest

from mutineer.models import Audience, Finding, RulesFile, Score, Severity, UnmeasuredLocation
from mutineer.triage import SEAM_GUIDANCE, triage


def kinds(findings: list[Finding]) -> list[str]:
    return [f.kind for f in findings]


def agent_gap(build: Any) -> Any:
    """``quote`` on fixture branch ``agent-gap``, as the engine reports it."""
    q = build.quote
    return build.run(
        functions=[build.fn(q, "src/shop/shipping.py", 6, 13)],
        results=[
            build.mutant(q, 1, "killed", "killed"),
            build.mutant(q, 2, "survived", "survived", 8, ("if total >= M:", "if total > M:")),
            build.mutant(q, 3, "killed", "killed"),
            build.mutant(q, 4, "killed", "killed"),
            build.mutant(
                q, 5, "survived", "survived", 10, ("rate = _rate(t)", "rate = _rate(None)")
            ),
            build.mutant(q, 6, "killed", "survived", 12, ("rate += F", "rate = F")),
            build.mutant(q, 7, "killed", "survived", 12, ("rate += F", "rate -= F")),
        ],
        tests={q: sorted([build.rule_test, build.impl_test])},
        unmeasured=[
            UnmeasuredLocation(
                file="src/shop/shipping.py", line=3, end_line=3, reason="module_level"
            )
        ],
    )


##
# the agent-gap shape: survivors to the agent, implementation-only kills to a human


@pytest.mark.rule("TRIAGE-001")
def test_only_mutants_no_test_catches_in_rule_covered_code_go_to_the_agent(
    build: Any, ship_rules: RulesFile
) -> None:
    result = triage(agent_gap(build), build.rule_map, ship_rules)

    agent = [f for f in result.findings if f.audience == Audience.AGENT]
    assert kinds(agent) == ["survivor_rule", "survivor_rule"]
    assert [(f.location.file, f.location.line) for f in agent if f.location] == [
        ("src/shop/shipping.py", 8),
        ("src/shop/shipping.py", 10),
    ]
    assert all(f.severity == Severity.WARN and f.rules == ["SHIP-003"] for f in agent)
    assert [f.evidence["mutated"] for f in agent] == ["if total > M:", "rate = _rate(None)"]
    assert result.survivor_names == [f"{build.quote}__mutmut_2", f"{build.quote}__mutmut_5"]
    # everything else is for a human
    human = [f for f in result.findings if f.audience == Audience.HUMAN]
    assert kinds(human) == ["guarded_by_impl_only", "unmeasured_change"]


def test_agent_gap_scores(build: Any, ship_rules: RulesFile) -> None:
    result = triage(agent_gap(build), build.rule_map, ship_rules)
    assert result.rule_score == Score(killed=3, total=7)
    assert result.diff_score == Score(killed=5, total=7)


def test_survivor_rule_next_action_names_the_seam_and_the_line(
    build: Any, ship_rules: RulesFile
) -> None:
    first = triage(agent_gap(build), build.rule_map, ship_rules).findings[0]
    assert first.next_action == (
        "Test SHIP-003 through `shop.shipping.quote` so that this change to line 8 fails. "
        + SEAM_GUIDANCE
    )
    assert first.evidence["rule_tests"] == [build.rule_test]
    assert first.evidence["rule_statements"] == {"SHIP-003": "Orders of $50 or more ship free"}


def test_a_rule_without_a_seam_points_at_its_public_entry_point(build: Any) -> None:
    rules = RulesFile.model_validate({"rules": [{"id": "SHIP-003", "statement": "free"}]})
    first = triage(agent_gap(build), build.rule_map, rules).findings[0]
    assert first.next_action is not None
    assert first.next_action.startswith("Test SHIP-003 through its public entry point")


def test_guarded_by_impl_only_is_one_finding_per_function_with_rule_pass_text(
    build: Any, ship_rules: RulesFile
) -> None:
    result = triage(agent_gap(build), build.rule_map, ship_rules)
    (guarded,) = [f for f in result.findings if f.kind == "guarded_by_impl_only"]
    assert guarded.severity == Severity.INFO
    assert guarded.rules == ["SHIP-003"]
    assert guarded.location is not None and guarded.location.line == 12
    assert [(m["mutated"], m["all"], m["rule"]) for m in guarded.evidence["mutants"]] == [
        ("rate = F", "killed", "survived"),
        ("rate -= F", "killed", "survived"),
    ]


##
# the rest of the two-pass table


@pytest.mark.parametrize(
    ("all_status", "rule_status", "kind"),
    [
        ("killed", "killed", None),
        ("caught_by_type_check", "killed", None),
        ("killed", "no_tests", "guarded_by_impl_only"),
        ("no_tests", "no_tests", "survivor_rule"),
        ("survived", "no_tests", "survivor_rule"),
        ("survived", "killed", "mutant_inconclusive"),
        ("timeout", "killed", "mutant_inconclusive"),
        ("killed", "suspicious", "mutant_inconclusive"),
        ("survived", "not_checked", "mutant_inconclusive"),
        ("survived", None, "mutant_inconclusive"),
    ],
)
def test_rule_covered_table(
    build: Any, ship_rules: RulesFile, all_status: str, rule_status: str | None, kind: str | None
) -> None:
    q = build.quote
    run = build.run(
        functions=[build.fn(q)],
        results=[build.mutant(q, 1, all_status, rule_status, 4)],
        tests={q: [build.rule_test]},
    )
    result = triage(run, build.rule_map, ship_rules)
    assert kinds(result.findings) == ([kind] if kind else [])


def test_inconclusive_mutants_count_in_neither_score(build: Any, ship_rules: RulesFile) -> None:
    q = build.quote
    run = build.run(
        functions=[build.fn(q)],
        results=[
            build.mutant(q, 1, "killed", "killed"),
            build.mutant(q, 2, "timeout", "killed", 3),
            build.mutant(q, 3, "survived", "segfault", 4),
            build.mutant(q, 4, "survived", None, 5),
        ],
        tests={q: [build.rule_test]},
    )
    result = triage(run, build.rule_map, ship_rules)
    assert result.rule_score == Score(killed=2, total=2)
    assert result.diff_score == Score(killed=1, total=3)
    (finding,) = result.findings
    assert finding.kind == "mutant_inconclusive"
    assert [(m["all"], m["rule"]) for m in finding.evidence["mutants"]] == [
        ("timeout", "killed"),
        ("survived", "segfault"),
        ("survived", None),
    ]


##
# functions no rule test covers


def test_survivors_without_a_rule_are_one_question_per_function(
    build: Any, ship_rules: RulesFile
) -> None:
    d, p = build.discount, "shop.pricing.x_loyalty_points"
    run = build.run(
        functions=[
            build.fn(d, "src/shop/pricing.py", 1, 4),
            build.fn(p, "src/shop/pricing.py", 11, 14),
        ],
        results=[
            build.mutant(d, 1, "survived", line=2),
            build.mutant(d, 2, "no_tests", line=3),
            build.mutant(d, 3, "killed"),
            build.mutant(p, 1, "survived", line=12),
            build.mutant(p, 2, "timeout", line=13),
        ],
        tests={d: ["tests/test_pricing.py::test_member_discount"], p: ["tests/test_pricing.py::t"]},
    )
    result = triage(run, build.rule_map, ship_rules)

    assert kinds(result.findings) == ["mutant_inconclusive", "survivor_unowned", "survivor_unowned"]
    assert all(f.audience == Audience.HUMAN for f in result.findings)
    unowned = [f for f in result.findings if f.kind == "survivor_unowned"]
    assert [f.evidence["function"] for f in unowned] == [d, p]
    assert [len(f.evidence["mutants"]) for f in unowned] == [2, 1]
    assert unowned[0].next_action == "Is this behaviour a promise? If so, add a rule."
    assert result.rule_score is None
    assert result.diff_score == Score(killed=1, total=4)
    assert result.survivor_names == []


def test_unreached_functions_get_one_finding_and_lower_the_diff_score(
    build: Any, ship_rules: RulesFile
) -> None:
    new = "shop.fresh.x_new"
    run = build.run(
        functions=[build.fn(new, "src/shop/fresh.py", 1, 3)],
        results=[
            build.mutant(new, 1, "no_tests", line=2),
            build.mutant(new, 2, "no_tests", line=2),
        ],
        unreached=[new],
    )
    result = triage(run, build.rule_map, ship_rules)
    assert kinds(result.findings) == ["unreached"]
    assert result.findings[0].evidence == {"function": new, "mutants": 2}
    assert result.diff_score == Score(killed=0, total=2)


##
# unmeasured changes


@pytest.mark.rule("TRIAGE-002")
def test_unmeasured_changes_are_reported_per_file_and_change_no_score(
    build: Any, ship_rules: RulesFile
) -> None:
    measured = agent_gap(build)
    with_property = measured.model_copy(
        update={
            "unmeasured": [
                *measured.unmeasured,
                UnmeasuredLocation(
                    file="src/shop/cart.py", line=12, end_line=12, reason="decorated"
                ),
                UnmeasuredLocation(
                    file="src/shop/cart.py", line=20, end_line=22, reason="no_mutants"
                ),
            ]
        }
    )
    before = triage(measured.model_copy(update={"unmeasured": []}), build.rule_map, ship_rules)
    after = triage(with_property, build.rule_map, ship_rules)

    assert (after.rule_score, after.diff_score) == (before.rule_score, before.diff_score)
    unmeasured = [f for f in after.findings if f.kind == "unmeasured_change"]
    assert [
        (f.location.file, f.location.line, f.location.end_line) for f in unmeasured if f.location
    ] == [
        ("src/shop/cart.py", 12, 22),
        ("src/shop/shipping.py", 3, 3),
    ]
    assert unmeasured[0].evidence["reasons"] == ["decorated", "no_mutants"]
    assert unmeasured[0].severity == Severity.INFO
    assert unmeasured[0].audience == Audience.HUMAN


@pytest.mark.rule("TRIAGE-002")
@pytest.mark.parametrize(
    "reason",
    ["decorated", "module_level", "outside_source_paths", "excluded_by_config", "no_mutants"],
)
def test_every_unmeasured_reason_is_reported(
    build: Any, ship_rules: RulesFile, reason: str
) -> None:
    run = build.run(unmeasured=[UnmeasuredLocation(file="a.py", line=1, end_line=1, reason=reason)])  # type: ignore[arg-type]
    result = triage(run, build.rule_map, ship_rules)
    assert kinds(result.findings) == ["unmeasured_change"]
    assert result.diff_score == Score(killed=0, total=0)


##
# baseline and empty runs


def test_a_failing_baseline_is_a_block_and_leaves_no_score(
    build: Any, ship_rules: RulesFile
) -> None:
    run = agent_gap(build).model_copy(
        update={"baseline_failed": True, "baseline_output": "FAILED tests/test_x.py::test_x"}
    )
    result = triage(run, build.rule_map, ship_rules)
    assert kinds(result.findings) == ["baseline_failed", "unmeasured_change"]
    assert result.findings[0].severity == Severity.BLOCK
    assert result.findings[0].evidence["output"] == "FAILED tests/test_x.py::test_x"
    assert (result.rule_score, result.diff_score, result.survivor_names) == (None, None, [])


def test_nothing_changed_gives_an_empty_diff_score(build: Any, ship_rules: RulesFile) -> None:
    result = triage(build.run(), build.rule_map, ship_rules)
    assert result.findings == []
    assert result.rule_score is None
    assert result.diff_score == Score(killed=0, total=0)


##
# questions for a human name the function and point at its lines


def test_human_findings_name_the_function_and_point_at_its_lines(
    build: Any, ship_rules: RulesFile
) -> None:
    d, q, new = build.discount, build.quote, "shop.fresh.x_new"
    run = build.run(
        # the unreached function comes first: the ones after it are still judged
        functions=[
            build.fn(new, "src/shop/fresh.py", 1, 3),
            build.fn(d, "src/shop/pricing.py", 1, 6),
            build.fn(q, "src/shop/shipping.py", 6, 13),
        ],
        results=[
            build.mutant(new, 1, "no_tests", line=2),
            build.mutant(d, 1, "survived", line=2),
            build.mutant(d, 2, "timeout", line=4),
            build.mutant(q, 1, "timeout", "killed", 8),
        ],
        tests={d: [build.impl_test], q: [build.rule_test]},
        unreached=[new],
    )
    result = triage(run, build.rule_map, ship_rules)
    assert [
        (f.kind, f.evidence["function"], f.location.file, f.location.line, f.location.end_line)
        for f in result.findings
        if f.location
    ] == [
        ("mutant_inconclusive", d, "src/shop/pricing.py", 4, 4),
        ("mutant_inconclusive", q, "src/shop/shipping.py", 8, 8),
        ("survivor_unowned", d, "src/shop/pricing.py", 2, 2),
        ("unreached", new, "src/shop/fresh.py", 1, 3),
    ]
    messages = [f.message for f in result.findings]
    assert messages[0].startswith("1 mutant in discount had no clear result")
    assert messages[1].startswith("1 mutant in quote had no clear result")
    assert messages[2].startswith("1 mutant in discount survived")
    assert messages[3] == "No test reaches new."


def test_a_function_without_mutants_gives_no_finding(build: Any, ship_rules: RulesFile) -> None:
    q, d = build.quote, build.discount
    run = build.run(functions=[build.fn(q), build.fn(d)], tests={q: [build.rule_test]})
    result = triage(run, build.rule_map, ship_rules)
    assert result.findings == []
    assert result.rule_score == Score(killed=0, total=0)
