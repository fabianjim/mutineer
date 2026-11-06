"""``--format markdown``: a PR comment.

Blocking findings come first, then the agent's work, then questions only a human can
answer, each in its own section.
"""

from __future__ import annotations

import re

from mutineer.models import Audience, Finding, Severity, Verdict
from mutineer.reporters._common import detail_lines, score_text, short_sha, where

_SEVERITY_ORDER = {Severity.BLOCK: 0, Severity.WARN: 1, Severity.INFO: 2}


def render(verdict: Verdict) -> str:
    lines = [
        f"## Mutineer: {verdict.status.upper()}",
        "",
        "| Rule score | Diff score | Base | Time |",
        "|---|---|---|---|",
        f"| {score_text(verdict.rule_score)} | {score_text(verdict.diff_score)} "
        f"| `{short_sha(verdict)}` | {verdict.duration_s:.1f} s |",
    ]
    blocking = [f for f in verdict.findings if f.severity == Severity.BLOCK]
    rest = [f for f in verdict.findings if f.severity != Severity.BLOCK]
    sections = [
        ("Blocking", blocking),
        ("For the agent", [f for f in rest if f.audience == Audience.AGENT]),
        ("Questions for a human", [f for f in rest if f.audience == Audience.HUMAN]),
    ]
    if not verdict.findings:
        lines += ["", "No findings."]
    for title, findings in sections:
        if not findings:
            continue
        lines += ["", f"### {title} ({len(findings)})", ""]
        for f in sorted(findings, key=lambda f: _SEVERITY_ORDER[f.severity]):
            lines += _finding(f)
    return "\n".join(lines) + "\n"


def _finding(f: Finding) -> list[str]:
    head = [f"**{f.severity.upper()}**", f"`{f.kind}`"]
    loc = where(f)
    if loc:
        head.append(code(loc))
    if f.rules:
        head.append(", ".join(f.rules))
    out = [f"- {' · '.join(head)}: {_inline(f.message)}"]
    out += [f"  - {line}" for line in detail_lines(f, code)]
    if f.next_action:
        out.append(f"  - _Next:_ {_inline(f.next_action)}")
    return out


def code(text: str) -> str:
    """``text`` as inline code, with a fence longer than any backtick run inside it."""
    longest = max((len(m) for m in re.findall(r"`+", text)), default=0)
    fence = "`" * (longest + 1)
    pad = " " if text.startswith("`") or text.endswith("`") else ""
    return f"{fence}{pad}{text}{pad}{fence}"


def _inline(text: str) -> str:
    # keep a message on its own list item
    return " ".join(text.split())
