"""The ``--format agent`` payload: ``AgentReport``, ``next_command`` and omitted keys"""

from __future__ import annotations

import json
from typing import Any

import pytest

from mutineer.models import AGENT_SCHEMA_VERSION, AgentReport, Location, Score
from mutineer.reporters import agent
from mutineer.verdict import decide


def payload(*findings: Any, **kwargs: Any) -> dict[str, Any]:
    verdict = decide(list(findings), base="abc", head="WORKTREE", **kwargs)
    return dict(json.loads(agent.render(verdict)))


def survivor(build: Any, **extra: Any) -> Any:
    return build.finding("warn", kind="survivor_rule", check="mutation", audience="agent", **extra)


@pytest.mark.parametrize(
    ("findings", "expected"),
    [
        ([("block", "baseline_failed", "human")], "mutineer run --format agent"),
        ([("block", "baseline_failed", "human"), ("block", "rule_test_weakened", "human")], None),
        ([("block", "rule_test_weakened", "human"), ("warn", "survivor_rule", "agent")], None),
        ([("warn", "survivor_rule", "agent")], "mutineer recheck --format agent"),
        ([("warn", "survivor_unowned", "human")], None),
        ([], None),
    ],
)
def test_next_command_depends_on_what_is_left(
    build: Any, findings: list[tuple[str, str, str]], expected: str | None
) -> None:
    built = [
        build.finding(sev, kind=kind, check="mutation", audience=aud) for sev, kind, aud in findings
    ]
    out = payload(*built)
    assert "next_command" in out
    assert out["next_command"] == expected


def test_payload_carries_schema_version_and_nullable_keys(build: Any) -> None:
    out = payload()
    assert out["schema_version"] == AGENT_SCHEMA_VERSION == "1"
    assert out["rule_score"] is None
    assert out["diff_score"] is None
    assert out["next_command"] is None
    assert list(out) == [
        "schema_version",
        "status",
        "rule_score",
        "diff_score",
        "instruction",
        "next_command",
        "findings",
    ]


def test_absent_location_and_next_action_are_omitted(build: Any) -> None:
    bare, located = payload(
        survivor(build, message="Bare."),
        survivor(build, location=Location(file="src/a.py", line=3), next_action="Test it."),
    )["findings"]
    assert set(bare) == {"id", "kind", "rules", "message", "evidence"}
    assert bare["rules"] == []
    assert located["location"] == {"file": "src/a.py", "line": 3}
    assert located["next_action"] == "Test it."


def test_done_with_open_disputes_says_they_wait_for_a_human(build: Any) -> None:
    disputed = build.finding("warn", kind="mutant_disputed", check="mutation")
    out = payload(disputed)
    assert out["status"] == "warn"
    assert out["findings"] == []
    assert out["next_command"] is None
    assert out["instruction"] == agent.DISPUTES_PENDING_INSTRUCTION
    assert "human" in out["instruction"]


def test_work_instruction_offers_dispute_and_defines_done() -> None:
    text = agent.WORK_INSTRUCTION
    assert "mutineer dispute <id>" in text
    assert "next_command" in text
    assert "no survivor_rule is left" in text


def test_payload_round_trips_through_agent_report(build: Any) -> None:
    text = agent.render(
        decide(
            [
                survivor(
                    build,
                    rules=["SHIP-003"],
                    location=Location(file="src/shop/shipping.py", line=8),
                    evidence={"mutant": "m2", "original": "a >= b", "mutated": "a > b"},
                    next_action="Test SHIP-003 through `shop.shipping.quote`.",
                ),
                survivor(build, message="Ünïcode, no location."),
            ],
            base="abc",
            head="WORKTREE",
            rule_score=Score(killed=1, total=3),
            diff_score=Score(killed=2, total=3),
        )
    )
    report = AgentReport.model_validate_json(text)
    assert report.next_command == agent.RECHECK_COMMAND
    assert report.findings[1].location is None
    assert agent.dump(report) == json.loads(text)
    assert "Ünïcode" in text


def test_the_payload_is_compact_and_keeps_non_ascii(build: Any) -> None:
    verdict = decide([survivor(build, message="Nothing catches ≥.")], base="abc", head="WORKTREE")
    text = agent.render(verdict)
    assert '"status":"warn","rule_score":null,' in text
    assert "≥" in text
