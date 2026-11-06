"""``--format agent``: compact JSON for the implementing agent.

Only agent-audience findings are included (TRIAGE-001): survivors in rule-covered code,
each with the rule, its seam and the line. Questions for a human stay out, so the agent
never pins behaviour nobody promised.
"""

from __future__ import annotations

import json
from typing import Any

from mutineer.models import Audience, Finding, Severity, Verdict

WORK_INSTRUCTION = (
    "Write a test for each finding as its next_action says, then run "
    "`mutineer recheck --format agent`. Don't edit, weaken or unmark existing rule tests, "
    "and don't edit the rules file."
)
BLOCKED_INSTRUCTION = (
    "Stop: this change is blocked on findings only a human can resolve. "
    "Report the status to a human."
)
BROKEN_SUITE_INSTRUCTION = (
    "The test suite fails without any mutant, so nothing was measured. Run the tests to see "
    "the failure, make them pass without weakening a rule test, then run "
    "`mutineer run --format agent` again."
)
DONE_INSTRUCTION = "Nothing for you to do. Report the status; anything else is for a human."


def render(verdict: Verdict) -> str:
    findings = [f for f in verdict.findings if f.audience == Audience.AGENT]
    payload = {
        "status": verdict.status,
        "rule_score": verdict.rule_score.model_dump() if verdict.rule_score else None,
        "diff_score": verdict.diff_score.model_dump() if verdict.diff_score else None,
        "instruction": instruction(verdict, findings),
        "findings": [_compact(f) for f in findings],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"


def instruction(verdict: Verdict, findings: list[Finding]) -> str:
    if verdict.status == "block":
        blocking = {f.kind for f in verdict.findings if f.severity == Severity.BLOCK}
        if blocking == {"baseline_failed"}:
            return BROKEN_SUITE_INSTRUCTION
        return BLOCKED_INSTRUCTION
    return WORK_INSTRUCTION if findings else DONE_INSTRUCTION


def _compact(f: Finding) -> dict[str, Any]:
    out: dict[str, Any] = {"id": f.id, "kind": f.kind, "rules": f.rules}
    if f.location is not None:
        out["location"] = f.location.model_dump(exclude_none=True)
    out["message"] = f.message
    out["evidence"] = f.evidence
    if f.next_action:
        out["next_action"] = f.next_action
    return out
