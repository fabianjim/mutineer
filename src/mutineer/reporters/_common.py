"""Helpers the human-facing reporters share: scores, locations and evidence lines."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from mutineer.models import Finding, Score, Verdict

BASELINE_LINES = 20
"""How much of a failing baseline's output the reports show."""


def score_text(score: Score | None) -> str:
    """``3/7 (43%)``, or ``n/a`` when there is no score."""
    if score is None:
        return "n/a"
    if score.total == 0:
        return "0/0"
    return f"{score.killed}/{score.total} ({round(100 * score.killed / score.total)}%)"


def where(f: Finding) -> str | None:
    """``file:line`` or ``file:line-end``, or None when the finding has no location."""
    loc = f.location
    if loc is None:
        return None
    if loc.line is None:
        return loc.file
    if loc.end_line is not None and loc.end_line != loc.line:
        return f"{loc.file}:{loc.line}-{loc.end_line}"
    return f"{loc.file}:{loc.line}"


def short_sha(verdict: Verdict) -> str:
    return verdict.base[:10]


def detail_lines(f: Finding, code: Callable[[str], str]) -> list[str]:
    """The evidence worth showing a reader, one line each. ``code`` formats source text."""
    ev = f.evidence
    out: list[str] = []
    mutants = ev.get("mutants")
    if isinstance(mutants, list):
        out += [_mutant_line(m, code) for m in mutants if isinstance(m, dict)]
    if "before" in ev and f.check == "tamper":
        out.append(f"before: {code(str(ev['before']))}")
        if "after" in ev:
            out.append(f"after: {code(str(ev['after']))}")
    locations = ev.get("locations")
    if isinstance(locations, list):
        for loc in locations:
            if isinstance(loc, dict):
                out.append(f"{_lines(loc)}: {loc.get('reason')}")
    dispute = ev.get("dispute")
    if isinstance(dispute, dict) and f.kind in ("mutant_disputed", "survivor_rule"):
        if f.kind == "mutant_disputed":
            out.append(_change(ev, code))
        out.append(f"dispute: {dispute.get('reason')}")
        if dispute.get("response"):
            out.append(f"response: {dispute['response']}")
    tests = ev.get("tests")
    if f.check == "rules" and isinstance(tests, list):
        out += [f"test: {code(str(t))}" for t in tests]
    output = ev.get("output")
    if f.kind == "baseline_failed" and isinstance(output, str) and output:
        out += [code(line) for line in output.splitlines()[-BASELINE_LINES:]]
    return out


def _change(ev: dict[str, Any], code: Callable[[str], str]) -> str:
    original, mutated = ev.get("original"), ev.get("mutated")
    if original is None or mutated is None:
        return f"mutant {code(str(ev.get('mutant')))}"
    return f"{code(str(original))} → {code(str(mutated))}"


def _mutant_line(m: dict[str, Any], code: Callable[[str], str]) -> str:
    head = f"line {m['line']}: " if m.get("line") is not None else ""
    passes = ", ".join(f"{k} pass {m[k]}" for k in ("all", "rule") if m.get(k))
    return f"{head}{_change(m, code)}" + (f" ({passes})" if passes else "")


def _lines(loc: dict[str, Any]) -> str:
    line, end = loc.get("line"), loc.get("end_line")
    if line is None:
        return "file"
    if end is None or end == line:
        return f"line {line}"
    return f"lines {line}-{end}"


ANSWERED_TITLE = "Already answered"
"""The last group of the human reports: questions a human already said no to, and disputes
a human confirmed."""


def is_answered(f: Finding) -> bool:
    """A question a human already answered: dismissed, or a confirmed dispute."""
    if "answer" in f.evidence:
        return True
    dispute = f.evidence.get("dispute")
    return (
        f.kind == "mutant_disputed"
        and isinstance(dispute, dict)
        and dispute.get("status") == "confirmed"
    )


def split_answered(findings: list[Finding]) -> tuple[list[Finding], list[Finding]]:
    """``findings`` without the answered questions, and the answered ones, in order."""
    main = [f for f in findings if not is_answered(f)]
    answered = [f for f in findings if is_answered(f)]
    return main, answered


def answered_parts(f: Finding) -> tuple[str, str, str, str]:
    """Location, name, mutant count and reason of an answered question.

    For a confirmed dispute the reason is the agent's.
    """
    ev = f.evidence
    name = ev.get("qualname") or ev.get("function") or f.kind
    mutants = ev.get("mutants")
    # a dispute is about one mutant
    many = len(mutants) if isinstance(mutants, list) else 0
    n = 1 if f.kind == "mutant_disputed" else many
    count = f"{n} mutant" if n == 1 else f"{n} mutants"
    answer = ev.get("answer")
    dispute = ev.get("dispute")
    if isinstance(answer, dict):
        reason = answer.get("reason", "")
    elif isinstance(dispute, dict):
        reason = dispute.get("reason", "")
    else:
        reason = ""
    return where(f) or "", str(name), count, str(reason)
