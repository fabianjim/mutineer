"""The four reporters, on verdicts built by hand"""

from __future__ import annotations

import json as stdjson
from typing import Any

import pytest

from mutineer.models import Location, Score, Verdict
from mutineer.reporters import agent, json, markdown, terminal
from mutineer.verdict import decide


def mixed_verdict(build: Any) -> Verdict:
    return decide(
        [
            build.finding("info", kind="survivor_unowned", check="mutation", message="Unowned."),
            build.finding(
                "warn",
                kind="survivor_rule",
                check="mutation",
                audience="agent",
                rules=["SHIP-003"],
                location=Location(file="src/shop/shipping.py", line=8),
                message="Nothing catches this.",
                evidence={"mutant": "m2", "original": "a >= b", "mutated": "a > b"},
                next_action="Test SHIP-003 through `shop.shipping.quote`.",
            ),
            build.finding(
                "block",
                kind="rule_test_weakened",
                rules=["SHIP-003"],
                location=Location(file="tests/rules/test_shipping_rules.py", line=12),
                message="Weakened.",
                evidence={"before": "assert quote(10) == 5", "after": "assert quote(10) >= 0"},
            ),
            build.finding("warn", kind="test_config_changed", message="Config changed."),
        ],
        base="0123456789abcdef",
        head="WORKTREE",
        rule_score=Score(killed=3, total=7),
        diff_score=Score(killed=5, total=7),
        duration_s=2.5,
    )


##
# json


def test_json_is_the_full_verdict(build: Any) -> None:
    verdict = mixed_verdict(build)
    assert Verdict.model_validate_json(json.render(verdict)) == verdict


##
# agent


@pytest.mark.rule("TRIAGE-001")
def test_agent_output_has_only_agent_findings(build: Any) -> None:
    payload = stdjson.loads(agent.render(mixed_verdict(build)))
    assert set(payload) == {"status", "rule_score", "diff_score", "instruction", "findings"}
    assert [f["kind"] for f in payload["findings"]] == ["survivor_rule"]
    assert payload["findings"][0]["location"] == {"file": "src/shop/shipping.py", "line": 8}
    assert payload["findings"][0]["next_action"] == "Test SHIP-003 through `shop.shipping.quote`."
    assert payload["rule_score"] == {"killed": 3, "total": 7}
    assert "\n" not in agent.render(mixed_verdict(build)).rstrip("\n")


def test_agent_instruction_depends_on_what_is_left(build: Any) -> None:
    def instruction(*findings: Any) -> str:
        verdict = decide(list(findings), base="abc", head="WORKTREE")
        return str(stdjson.loads(agent.render(verdict))["instruction"])

    work = build.finding("warn", kind="survivor_rule", check="mutation", audience="agent")
    assert instruction(work) == agent.WORK_INSTRUCTION
    assert instruction() == agent.DONE_INSTRUCTION
    assert instruction(build.finding("warn")) == agent.DONE_INSTRUCTION
    assert instruction(work, build.finding("block")) == agent.BLOCKED_INSTRUCTION
    broken = build.finding("block", kind="baseline_failed", check="mutation")
    assert instruction(broken) == agent.BROKEN_SUITE_INSTRUCTION
    assert instruction(broken, build.finding("block")) == agent.BLOCKED_INSTRUCTION


##
# markdown


def test_markdown_puts_blocking_agent_and_human_findings_in_their_own_sections(
    build: Any,
) -> None:
    text = markdown.render(mixed_verdict(build))
    assert text.startswith("## Mutineer: BLOCK\n")
    assert "| 3/7 (43%) | 5/7 (71%) | `0123456789` | 2.5 s |" in text
    blocking = text.index("### Blocking (1)")
    agent_part = text.index("### For the agent (1)")
    human = text.index("### Questions for a human (2)")
    assert blocking < agent_part < human
    assert text.index("rule_test_weakened") < agent_part
    assert agent_part < text.index("survivor_rule") < human
    assert text.index("test_config_changed") < text.index("survivor_unowned")
    assert "  - before: `assert quote(10) == 5`" in text


def test_markdown_without_findings(build: Any) -> None:
    text = markdown.render(decide([], base="abc", head="WORKTREE"))
    assert "No findings." in text
    assert "| n/a | n/a |" in text


@pytest.mark.parametrize(
    ("text", "expected"),
    [("a > b", "`a > b`"), ("x = `y`", "`` x = `y` ``"), ("``", "``` `` ```")],
)
def test_markdown_code_spans_survive_backticks(text: str, expected: str) -> None:
    assert markdown.code(text) == expected


##
# terminal


def test_terminal_lists_block_first_without_colour_by_default(build: Any) -> None:
    text = terminal.render(mixed_verdict(build))
    assert "\x1b[" not in text
    first = text.splitlines()[0]
    assert first.startswith("Mutineer BLOCK  rule score 3/7 (43%)  diff score 5/7 (71%)")
    order = [text.index(k) for k in ("rule_test_weakened", "survivor_rule", "survivor_unowned")]
    assert order == sorted(order)
    assert "before: assert quote(10) == 5" in text
    assert all(line == line.rstrip() for line in text.splitlines())


def test_terminal_colour_is_opt_in(build: Any) -> None:
    assert "\x1b[" in terminal.render(mixed_verdict(build), color=True)


def test_terminal_shows_markup_like_text_literally(build: Any) -> None:
    finding = build.finding("info", message="Saw [bold]x[/bold] in a test.")
    text = terminal.render(decide([finding], base="abc", head="WORKTREE"))
    assert "Saw [bold]x[/bold] in a test." in text


def test_terminal_wraps_long_text_without_trailing_spaces(build: Any) -> None:
    finding = build.finding("info", message="word " * 60, next_action="next " * 60)
    for color in (False, True):
        text = terminal.render(decide([finding], base="abc", head="WORKTREE"), color=color)
        assert len(text.splitlines()) > 4
        assert all(not line.replace("\x1b[0m", "").endswith(" ") for line in text.splitlines())
