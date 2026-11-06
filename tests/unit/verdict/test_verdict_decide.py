"""The verdict through ``verdict.decide``: status, ordering, ids and exit codes"""

from __future__ import annotations

from typing import Any

import pytest

from mutineer.models import Score, Severity
from mutineer.triage import triage
from mutineer.verdict import decide, exit_code


def statuses(build: Any, *severities: str) -> str:
    findings = [build.finding(s) for s in severities]
    return decide(findings, base="abc", head="WORKTREE").status


##
# status


def test_status_follows_the_most_severe_finding(build: Any) -> None:
    assert statuses(build) == "pass"
    assert statuses(build, "warn") == "warn"
    assert statuses(build, "warn", "block") == "block"
    assert statuses(build, "block", "info", "warn") == "block"


@pytest.mark.rule("VERDICT-003")
@pytest.mark.parametrize("others", [(), ("warn",), ("block",)])
def test_info_findings_never_change_the_status(build: Any, others: tuple[str, ...]) -> None:
    without = statuses(build, *others)
    assert statuses(build, *others, "info") == without
    assert statuses(build, *others, *["info"] * 5) == without


@pytest.mark.rule("VERDICT-003")
def test_info_from_every_check_and_audience_still_passes(build: Any) -> None:
    findings = [
        build.finding("info", kind=k, check=c, audience=a)
        for k, c, a in [
            ("impl_test_changed", "tamper", "human"),
            ("rule_unguarded", "rules", "human"),
            ("survivor_unowned", "mutation", "human"),
            ("unmeasured_change", "mutation", "human"),
            ("odd_info", "mutation", "agent"),
        ]
    ]
    assert decide(findings, base="abc", head="WORKTREE").status == "pass"


@pytest.mark.rule("VERDICT-002")
def test_a_failing_baseline_blocks(build: Any, ship_rules: Any) -> None:
    run = build.run(baseline_failed=True, baseline_output="1 failed")
    result = triage(run, build.rule_map, ship_rules)
    verdict = decide(result.findings, base="abc", head="WORKTREE")
    assert verdict.status == "block"
    assert [f.kind for f in verdict.findings] == ["baseline_failed"]


@pytest.mark.rule("VERDICT-002")
def test_baseline_failed_is_block_whatever_severity_it_arrives_with(build: Any) -> None:
    finding = build.finding("info", kind="baseline_failed", check="mutation")
    verdict = decide([finding], base="abc", head="WORKTREE")
    assert verdict.status == "block"
    assert verdict.findings[0].severity == Severity.BLOCK


##
# ids and order


def test_findings_are_ordered_by_severity_then_check_and_numbered_per_check(
    build: Any,
) -> None:
    findings = [
        build.finding("info", check="mutation", kind="m1"),
        build.finding("warn", check="mutation", kind="m2", audience="agent"),
        build.finding("info", check="tamper", kind="t1"),
        build.finding("block", check="tamper", kind="t2"),
        build.finding("warn", check="rules", kind="r1"),
        build.finding("warn", check="mutation", kind="m3", audience="agent"),
    ]
    verdict = decide(findings, base="abc", head="WORKTREE")
    assert [(f.id, f.kind) for f in verdict.findings] == [
        ("tamper-0001", "t2"),
        ("rules-0001", "r1"),
        ("mutation-0001", "m2"),
        ("mutation-0002", "m3"),
        ("tamper-0002", "t1"),
        ("mutation-0003", "m1"),
    ]


def test_decide_replaces_producer_ids_and_keeps_scores(build: Any) -> None:
    verdict = decide(
        [build.finding(id="custom")],
        base="abc",
        head="WORKTREE",
        rule_score=Score(killed=3, total=7),
        diff_score=Score(killed=5, total=7),
        duration_s=1.23456,
    )
    assert verdict.findings[0].id == "tamper-0001"
    assert (verdict.rule_score, verdict.diff_score) == (
        Score(killed=3, total=7),
        Score(killed=5, total=7),
    )
    assert verdict.duration_s == 1.235
    assert (verdict.base, verdict.head, verdict.schema_version) == ("abc", "WORKTREE", "1")


##
# exit codes


@pytest.mark.parametrize(
    ("severities", "strict", "code"),
    [
        ((), False, 0),
        ((), True, 0),
        (("warn",), False, 0),
        (("warn",), True, 1),
        (("block",), False, 2),
        (("block",), True, 2),
    ],
)
def test_exit_codes(build: Any, severities: tuple[str, ...], strict: bool, code: int) -> None:
    verdict = decide([build.finding(s) for s in severities], base="abc", head="WORKTREE")
    assert exit_code(verdict, strict=strict) == code
