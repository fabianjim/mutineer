"""Triage with a ledger: answered and proposed questions, and the commands that answer them"""

from __future__ import annotations

from typing import Any

import pytest

from mutineer.ledger import Ledger
from mutineer.models import (
    Answer,
    AnswersFile,
    Audience,
    Finding,
    Proposal,
    ProposalsFile,
    RulesFile,
    Severity,
)
from mutineer.triage import triage

POINTS = "shop.pricing.x_loyalty_points"
POINTS_TEST = "tests/test_pricing.py::test_points"


def unowned_run(build: Any, tests: list[str] | None = None) -> Any:
    return build.run(
        functions=[build.fn(POINTS, "src/shop/pricing.py", 11, 14)],
        results=[build.mutant(POINTS, 1, "survived", line=12)],
        tests={POINTS: tests or [POINTS_TEST]},
    )


def guarded_run(build: Any) -> Any:
    q = build.quote
    return build.run(
        functions=[build.fn(q, "src/shop/shipping.py", 6, 13)],
        results=[build.mutant(q, 1, "killed", "survived", 12, ("rate += F", "rate = F"))],
        tests={q: sorted([build.rule_test, build.impl_test])},
    )


def ledger(
    *, answers: list[Answer] | None = None, proposals: list[Proposal] | None = None
) -> Ledger:
    return Ledger(
        proposals=ProposalsFile(proposals=proposals or []),
        answers=AnswersFile(answers=answers or []),
    )


def only(findings: list[Finding], kind: str) -> Finding:
    (f,) = [f for f in findings if f.kind == kind]
    return f


def no_points() -> Answer:
    return Answer(function=POINTS, question="survivor_unowned", reason="marketing detail")


##
# without a ledger nothing changes


@pytest.mark.parametrize(
    ("make", "kind", "text"),
    [
        (unowned_run, "survivor_unowned", "Is this behaviour a promise? If so, add a rule."),
        (
            guarded_run,
            "guarded_by_impl_only",
            "Is this behaviour part of SHIP-003? If so, mark a test that proves it with the "
            "rule; if not, nothing to do.",
        ),
    ],
)
def test_no_ledger_gives_the_findings_of_before_answers_existed(
    build: Any, ship_rules: RulesFile, make: Any, kind: str, text: str
) -> None:
    run = make(build)
    f = only(triage(run, build.rule_map, ship_rules, ledger=None).findings, kind)
    assert set(f.evidence) == {"function", "mutants"}
    assert f.next_action == text
    assert triage(run, build.rule_map, ship_rules) == triage(
        run, build.rule_map, ship_rules, ledger=None
    )


##
# open questions name their commands


def test_an_open_unowned_question_names_propose_and_dismiss(
    build: Any, ship_rules: RulesFile
) -> None:
    f = only(
        triage(unowned_run(build), build.rule_map, ship_rules, ledger=ledger()).findings,
        "survivor_unowned",
    )
    assert f.evidence["tests"] == [POINTS_TEST]
    assert f.evidence["qualname"] == "loyalty_points"
    assert "answer" not in f.evidence and "proposed" not in f.evidence
    assert f.next_action is not None
    for part in (
        "mutineer rules propose --prefix <PREFIX>",
        '--statement "<the promise>"',
        "evidence.tests",
        "mutineer rules accept <id>",
        f'mutineer rules dismiss {POINTS} --reason "<why>"',
    ):
        assert part in f.next_action


def test_an_open_guarded_question_names_the_rule_and_the_impl_tests(
    build: Any, ship_rules: RulesFile
) -> None:
    f = only(
        triage(guarded_run(build), build.rule_map, ship_rules, ledger=ledger()).findings,
        "guarded_by_impl_only",
    )
    assert f.evidence["impl_tests"] == [build.impl_test]
    assert f.next_action is not None
    for part in (
        "part of SHIP-003",
        "mutineer rules propose --rule SHIP-003",
        "evidence.impl_tests",
        "mutineer rules accept SHIP-003",
        f'mutineer rules dismiss {build.quote} --reason "<why>"',
    ):
        assert part in f.next_action


##
# answers


def test_an_answer_matches_by_function_and_kind(build: Any, ship_rules: RulesFile) -> None:
    plain = only(
        triage(unowned_run(build), build.rule_map, ship_rules, ledger=ledger()).findings,
        "survivor_unowned",
    )
    f = only(
        triage(
            unowned_run(build), build.rule_map, ship_rules, ledger=ledger(answers=[no_points()])
        ).findings,
        "survivor_unowned",
    )
    assert (f.kind, f.severity, f.audience) == (plain.kind, plain.severity, plain.audience)
    assert (f.severity, f.audience) == (Severity.INFO, Audience.HUMAN)
    assert f.evidence["answer"] == {"reason": "marketing detail", "question": "survivor_unowned"}
    assert f.next_action is not None
    assert f.next_action.startswith('Answered before: not a promise ("marketing detail").')
    assert ".mutineer/answers.toml" in f.next_action


@pytest.mark.parametrize(
    "answer",
    [
        Answer(function="shop.pricing.x_discount", question="survivor_unowned", reason="r"),
        Answer(function=POINTS, question="guarded_by_impl_only", reason="r"),
    ],
)
def test_an_answer_for_another_function_or_question_does_not_match(
    build: Any, ship_rules: RulesFile, answer: Answer
) -> None:
    f = only(
        triage(
            unowned_run(build), build.rule_map, ship_rules, ledger=ledger(answers=[answer])
        ).findings,
        "survivor_unowned",
    )
    assert "answer" not in f.evidence


def test_a_guarded_answer_matches_only_the_same_rules(build: Any, ship_rules: RulesFile) -> None:
    def guarded(rules: list[str]) -> Finding:
        answer = Answer(
            function=build.quote, question="guarded_by_impl_only", rules=rules, reason="detail"
        )
        result = triage(
            guarded_run(build), build.rule_map, ship_rules, ledger=ledger(answers=[answer])
        )
        return only(result.findings, "guarded_by_impl_only")

    same = guarded(["SHIP-003"])
    assert same.evidence["answer"]["reason"] == "detail"
    assert same.next_action is not None
    assert same.next_action.startswith('Answered before: not part of SHIP-003 ("detail").')
    assert "answer" not in guarded(["SHIP-003", "SHIP-004"]).evidence
    assert "answer" not in guarded([]).evidence


##
# proposals


def test_a_new_rule_proposal_matches_through_a_parametrized_test(
    build: Any, ship_rules: RulesFile
) -> None:
    run = unowned_run(build, tests=[f"{POINTS_TEST}[1]", f"{POINTS_TEST}[2]"])
    proposal = Proposal(id="PRICE-001", statement="Points per $10", tests=[POINTS_TEST])
    f = only(
        triage(run, build.rule_map, ship_rules, ledger=ledger(proposals=[proposal])).findings,
        "survivor_unowned",
    )
    assert f.evidence["proposed"] == ["PRICE-001"]
    assert f.next_action == (
        "Rule PRICE-001 is proposed for this: a human runs mutineer rules accept PRICE-001 "
        "or mutineer rules reject PRICE-001."
    )


def test_a_proposal_through_other_tests_or_of_the_wrong_kind_does_not_match(
    build: Any, ship_rules: RulesFile
) -> None:
    other = Proposal(id="PRICE-001", statement="x", tests=["tests/test_pricing.py::test_other"])
    more_tests = Proposal(id="SHIP-003", tests=[POINTS_TEST])
    f = only(
        triage(
            unowned_run(build),
            build.rule_map,
            ship_rules,
            ledger=ledger(proposals=[other, more_tests]),
        ).findings,
        "survivor_unowned",
    )
    assert "proposed" not in f.evidence


def test_a_guarded_question_matches_tests_proposed_for_one_of_its_rules(
    build: Any, ship_rules: RulesFile
) -> None:
    def proposed(p: Proposal) -> Any:
        result = triage(
            guarded_run(build), build.rule_map, ship_rules, ledger=ledger(proposals=[p])
        )
        return only(result.findings, "guarded_by_impl_only").evidence.get("proposed")

    assert proposed(Proposal(id="SHIP-003", tests=[build.impl_test])) == ["SHIP-003"]
    assert proposed(Proposal(id="SHIP-009", tests=[build.impl_test])) is None
    assert proposed(Proposal(id="SHIP-009", statement="new", tests=[build.impl_test])) is None


def test_a_proposal_takes_precedence_over_an_answer(build: Any, ship_rules: RulesFile) -> None:
    proposal = Proposal(id="PRICE-001", statement="Points per $10", tests=[POINTS_TEST])
    f = only(
        triage(
            unowned_run(build),
            build.rule_map,
            ship_rules,
            ledger=ledger(answers=[no_points()], proposals=[proposal]),
        ).findings,
        "survivor_unowned",
    )
    assert f.evidence["proposed"] == ["PRICE-001"]
    assert "answer" not in f.evidence
    assert f.next_action is not None and f.next_action.startswith("Rule PRICE-001 is proposed")


def test_a_guarded_question_names_the_function_by_its_qualname(
    build: Any, ship_rules: RulesFile
) -> None:
    f = only(
        triage(guarded_run(build), build.rule_map, ship_rules, ledger=ledger()).findings,
        "guarded_by_impl_only",
    )
    assert f.evidence["qualname"] == "quote"
