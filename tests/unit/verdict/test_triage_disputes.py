"""Disputes in triage, the verdict and the human reports"""

from __future__ import annotations

from typing import Any

import pytest

from mutineer.ledger import Ledger, dispute_findings
from mutineer.models import Audience, Dispute, DisputesFile, RulesFile, Score, Severity
from mutineer.reporters import markdown, terminal
from mutineer.triage import disputed_names, triage
from mutineer.verdict import decide

TEXT = ("rate += F", "rate = F")


def survivor_run(build: Any) -> Any:
    """One rule-covered survivor (TEXT) and one killed mutant of ``quote``."""
    q = build.quote
    return build.run(
        functions=[build.fn(q, "src/shop/shipping.py", 6, 13)],
        results=[
            build.mutant(q, 1, "survived", "survived", 12, TEXT),
            build.mutant(q, 2, "killed", "killed", 9),
        ],
        tests={q: [build.rule_test]},
    )


def disputes(*items: Dispute) -> Ledger:
    return Ledger(disputes=DisputesFile(disputes=list(items)))


def dispute(build: Any, status: str = "open", **extra: Any) -> Dispute:
    fields: dict[str, Any] = {
        "function": build.quote,
        "original": TEXT[0],
        "mutated": TEXT[1],
        "rules": ["SHIP-003"],
        "reason": "F is zero here",
        "status": status,
    }
    if status == "rejected":
        fields["response"] = "F is not always zero"
    return Dispute(**(fields | extra))


def run_triage(build: Any, rules: RulesFile, ledger: Ledger | None) -> Any:
    return triage(survivor_run(build), build.rule_map, rules, ledger=ledger)


##
# the table


def test_no_dispute_is_a_survivor_rule(build: Any, ship_rules: RulesFile) -> None:
    result = run_triage(build, ship_rules, disputes())
    (f,) = result.findings
    assert (f.kind, f.severity, f.audience) == ("survivor_rule", Severity.WARN, Audience.AGENT)
    assert "dispute" not in f.evidence
    assert result.survivor_names == [f"{build.quote}__mutmut_1"]
    assert result.rule_score == Score(killed=1, total=2)
    assert result.diff_score == Score(killed=1, total=2)


def test_an_open_dispute_goes_to_a_human(build: Any, ship_rules: RulesFile) -> None:
    result = run_triage(build, ship_rules, disputes(dispute(build)))
    (f,) = result.findings
    assert (f.kind, f.severity, f.audience) == ("mutant_disputed", Severity.WARN, Audience.HUMAN)
    assert (f.evidence["function"], f.evidence["original"], f.evidence["mutated"]) == (
        build.quote,
        *TEXT,
    )
    assert f.evidence["dispute"] == {"reason": "F is zero here", "status": "open", "response": None}
    assert f.location is not None and (f.location.file, f.location.line) == (
        "src/shop/shipping.py",
        12,
    )
    assert "`rate += F` changed to `rate = F`" in f.message
    assert "F is zero here" in f.message
    assert f.next_action is not None
    assert "mutineer resolve <this finding's id> --confirm" in f.next_action
    assert '--reject --reason "<why>"' in f.next_action
    assert result.survivor_names == []
    assert result.rule_score == Score(killed=1, total=2)
    assert result.diff_score == Score(killed=1, total=2)


def test_a_confirmed_dispute_counts_in_no_score(build: Any, ship_rules: RulesFile) -> None:
    result = run_triage(build, ship_rules, disputes(dispute(build, "confirmed")))
    (f,) = result.findings
    assert (f.kind, f.severity, f.audience) == ("mutant_disputed", Severity.INFO, Audience.HUMAN)
    assert f.evidence["dispute"]["status"] == "confirmed"
    assert result.survivor_names == []
    assert result.rule_score == Score(killed=1, total=1)
    assert result.diff_score == Score(killed=1, total=1)


def test_a_rejected_dispute_is_the_agents_work_again(build: Any, ship_rules: RulesFile) -> None:
    result = run_triage(build, ship_rules, disputes(dispute(build, "rejected")))
    (f,) = result.findings
    assert (f.kind, f.severity, f.audience) == ("survivor_rule", Severity.WARN, Audience.AGENT)
    assert f.evidence["dispute"] == {
        "reason": "F is zero here",
        "status": "rejected",
        "response": "F is not always zero",
    }
    assert f.evidence["original"] == TEXT[0]
    assert f.next_action is not None
    assert f.next_action.startswith(
        'A human rejected the dispute of this mutant ("F is not always zero")'
    )
    assert "Test SHIP-003 through `shop.shipping.quote`" in f.next_action
    assert result.survivor_names == [f"{build.quote}__mutmut_1"]
    assert result.rule_score == Score(killed=1, total=2)


@pytest.mark.parametrize(
    "extra",
    [{"original": "rate -= F"}, {"mutated": "rate = None"}, {"function": "shop.other.x_quote"}],
)
def test_a_dispute_that_no_longer_matches_does_nothing(
    build: Any, ship_rules: RulesFile, extra: dict[str, str]
) -> None:
    stale = disputes(dispute(build, "confirmed", **extra))
    assert run_triage(build, ship_rules, stale) == run_triage(build, ship_rules, None)
    assert disputed_names(survivor_run(build), stale) == set()


def test_a_dispute_only_touches_survivor_rule(build: Any, ship_rules: RulesFile) -> None:
    q = build.quote
    run = build.run(
        functions=[build.fn(q, "src/shop/shipping.py", 6, 13)],
        results=[build.mutant(q, 1, "killed", "survived", 12, TEXT)],
        tests={q: sorted([build.rule_test, build.impl_test])},
    )
    with_dispute = triage(
        run, build.rule_map, ship_rules, ledger=disputes(dispute(build, "confirmed"))
    )
    assert with_dispute == triage(run, build.rule_map, ship_rules, ledger=disputes())
    assert [f.kind for f in with_dispute.findings] == ["guarded_by_impl_only"]


def test_disputed_findings_follow_the_survivors_by_location(
    build: Any, ship_rules: RulesFile
) -> None:
    q = build.quote
    run = build.run(
        functions=[build.fn(q, "src/shop/shipping.py", 6, 13)],
        results=[
            build.mutant(q, 1, "survived", "survived", 12, ("a", "b")),
            build.mutant(q, 2, "survived", "survived", 8, ("c", "d")),
            build.mutant(q, 3, "survived", "survived", 10, TEXT),
        ],
        tests={q: [build.rule_test]},
    )
    ledger = disputes(
        dispute(build, original="a", mutated="b"), dispute(build, original="c", mutated="d")
    )
    result = triage(run, build.rule_map, ship_rules, ledger=ledger)
    got = [(f.kind, f.location.line if f.location else None) for f in result.findings]
    assert got == [("survivor_rule", 10), ("mutant_disputed", 8), ("mutant_disputed", 12)]


##
# the verdict


def test_an_open_dispute_warns(build: Any, ship_rules: RulesFile) -> None:
    result = run_triage(build, ship_rules, disputes(dispute(build)))
    assert decide(result.findings, base="b", head="h").status == "warn"


def test_a_confirmed_dispute_passes(build: Any, ship_rules: RulesFile) -> None:
    result = run_triage(build, ship_rules, disputes(dispute(build, "confirmed")))
    assert decide(result.findings, base="b", head="h").status == "pass"


##
# dispute_confirmed


def test_a_newly_confirmed_dispute_is_reported_once(build: Any) -> None:
    confirmed = dispute(build, "confirmed")
    (f,) = dispute_findings(None, DisputesFile(disputes=[confirmed, dispute(build, "open")]))
    assert (f.check, f.kind, f.severity, f.audience) == (
        "rules",
        "dispute_confirmed",
        Severity.INFO,
        Audience.HUMAN,
    )
    assert f.location is not None and f.location.file == ".mutineer/disputes.toml"
    assert build.quote in f.message and "rate += F" in f.message and "F is zero" in f.message
    assert f.next_action == "Check that a human confirmed this dispute."


def test_a_confirmed_dispute_after_an_open_one_is_reported_with_its_record(build: Any) -> None:
    confirmed = dispute(build, "confirmed")
    (f,) = dispute_findings(None, DisputesFile(disputes=[dispute(build, "open"), confirmed]))
    assert f.rules == ["SHIP-003"]
    assert f.evidence == confirmed.model_dump()


def test_a_dispute_open_at_the_base_and_confirmed_now_is_reported(build: Any) -> None:
    base = '[[dispute]]\nfunction = "{}"\noriginal = "rate += F"\nmutated = "rate = F"\n'
    base += 'rules = ["SHIP-003"]\nreason = "F is zero here"\nstatus = "open"\n'
    head = DisputesFile(disputes=[dispute(build, "confirmed")])
    assert len(dispute_findings(base.format(build.quote), head)) == 1
    confirmed_base = base.replace('"open"', '"confirmed"').format(build.quote)
    assert dispute_findings(confirmed_base, head) == []
    assert len(dispute_findings("not toml [", head)) == 1


##
# the human reports


def report_findings(build: Any, ship_rules: RulesFile, status: str) -> Any:
    result = run_triage(build, ship_rules, disputes(dispute(build, status)))
    return decide(result.findings, base="0123456789abcdef", head="WORKTREE")


def test_a_confirmed_dispute_is_already_answered(build: Any, ship_rules: RulesFile) -> None:
    verdict = report_findings(build, ship_rules, "confirmed")
    md = markdown.render(verdict)
    assert "### Already answered (1)" in md
    assert "- `src/shop/shipping.py:12` · quote · 1 mutant: F is zero here" in md
    assert "Questions for a human" not in md
    text = terminal.render(verdict)
    assert "Already answered (1)" in text
    assert 'src/shop/shipping.py:12  quote  1 mutant  "F is zero here"' in text
    assert "mutant_disputed" not in text


def test_an_open_dispute_is_a_question_for_a_human(build: Any, ship_rules: RulesFile) -> None:
    verdict = report_findings(build, ship_rules, "open")
    md = markdown.render(verdict)
    assert "### Questions for a human (1)" in md
    assert "  - `rate += F` → `rate = F`" in md
    assert "  - dispute: F is zero here" in md
    assert "response:" not in md
    text = terminal.render(verdict)
    assert "mutant_disputed" in text and "dispute: F is zero here" in text


def test_a_rejected_dispute_shows_its_response(build: Any, ship_rules: RulesFile) -> None:
    verdict = report_findings(build, ship_rules, "rejected")
    md = markdown.render(verdict)
    assert "### For the agent (1)" in md
    assert "  - response: F is not always zero" in md
    assert "response: F is not always zero" in terminal.render(verdict)
