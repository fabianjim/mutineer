"""``--format agent``: compact JSON for the implementing agent.

Only agent-audience findings are included (TRIAGE-001): survivors in rule-covered code,
each with the rule, its seam and the line. Questions for a human stay out, so the agent
never pins behaviour nobody promised. The payload is ``models.AgentReport``, published as
``docs/schema/agent.schema.json``.
"""

from __future__ import annotations

import json
from typing import Any

from mutineer.models import AgentFinding, AgentReport, Audience, Finding, Severity, Verdict

RUN_COMMAND = "mutineer run --format agent"
RECHECK_COMMAND = "mutineer recheck --format agent"

WORK_INSTRUCTION = (
    "For each finding, write a test as its next_action says and mark it with the finding's "
    'rule; or, only if no caller can observe the change, run `mutineer dispute <id> "<why>"`. '
    "Then run next_command. You're done when no survivor_rule is left. Don't dispute what "
    "you could test, don't edit, weaken or unmark rule tests, and don't edit the rules file."
)
BLOCKED_INSTRUCTION = (
    "Stop: this change is blocked on findings only a human can resolve. "
    "Report the status to a human."
)
BROKEN_SUITE_INSTRUCTION = (
    "The test suite fails without any mutant, so nothing was measured. Run the tests to see "
    "the failure, make them pass without weakening a rule test, then run next_command."
)
DONE_INSTRUCTION = "Nothing for you to do. Report the status; anything else is for a human."
DISPUTES_PENDING_INSTRUCTION = (
    "Nothing else for you to do: no survivor_rule is left, and your disputes wait for a "
    "human. Report the status."
)


def render(verdict: Verdict) -> str:
    return json.dumps(dump(report(verdict)), ensure_ascii=False, separators=(",", ":")) + "\n"


def report(verdict: Verdict) -> AgentReport:
    findings = [f for f in verdict.findings if f.audience == Audience.AGENT]
    return AgentReport(
        status=verdict.status,
        rule_score=verdict.rule_score,
        diff_score=verdict.diff_score,
        instruction=instruction(verdict, findings),
        next_command=next_command(verdict, findings),
        findings=[_agent_finding(f) for f in findings],
    )


def dump(r: AgentReport) -> dict[str, Any]:
    """``r`` as JSON data: ``location`` and ``next_action`` left out when None."""
    out = r.model_dump(mode="json", exclude={"findings"})
    out["findings"] = [_compact(f) for f in r.findings]
    # keep the key order of earlier releases, with findings last
    return {k: out[k] for k in AgentReport.model_fields}


def instruction(verdict: Verdict, findings: list[Finding]) -> str:
    if verdict.status == "block":
        return BROKEN_SUITE_INSTRUCTION if _only_baseline_blocks(verdict) else BLOCKED_INSTRUCTION
    if findings:
        return WORK_INSTRUCTION
    if any(_open_dispute(f) for f in verdict.findings):
        return DISPUTES_PENDING_INSTRUCTION
    return DONE_INSTRUCTION


def next_command(verdict: Verdict, findings: list[Finding]) -> str | None:
    if verdict.status == "block":
        return RUN_COMMAND if _only_baseline_blocks(verdict) else None
    return RECHECK_COMMAND if findings else None


def _only_baseline_blocks(verdict: Verdict) -> bool:
    blocking = {f.kind for f in verdict.findings if f.severity == Severity.BLOCK}
    return blocking == {"baseline_failed"}


def _open_dispute(f: Finding) -> bool:
    # a confirmed dispute is INFO; an open one still warns
    return f.kind == "mutant_disputed" and f.severity == Severity.WARN


def _agent_finding(f: Finding) -> AgentFinding:
    return AgentFinding(
        id=f.id,
        kind=f.kind,
        rules=f.rules,
        location=f.location,
        message=f.message,
        evidence=f.evidence,
        next_action=f.next_action or None,
    )


def _compact(f: AgentFinding) -> dict[str, Any]:
    out = f.model_dump(mode="json")
    if f.location is None:
        del out["location"]
    else:
        out["location"] = f.location.model_dump(mode="json", exclude_none=True)
    if f.next_action is None:
        del out["next_action"]
    return out
