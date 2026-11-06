"""The verdict: one status for the whole run, and stable ids for its findings."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from typing import Literal

from mutineer.models import Finding, Score, Severity, Verdict

Status = Literal["pass", "warn", "block"]

_SEVERITY_ORDER = {Severity.BLOCK: 0, Severity.WARN: 1, Severity.INFO: 2}
_CHECK_ORDER = {"tamper": 0, "rules": 1, "mutation": 2}

EXIT_CODES: dict[Status, int] = {"pass": 0, "warn": 0, "block": 2}
STRICT_WARN_EXIT = 1


def decide(
    findings: Iterable[Finding],
    *,
    base: str,
    head: str,
    rule_score: Score | None = None,
    diff_score: Score | None = None,
    duration_s: float = 0.0,
) -> Verdict:
    """The verdict for ``findings``.

    - ``block`` if any finding is BLOCK, else ``warn`` if any is WARN, else ``pass``.
      INFO findings never change the status (VERDICT-003).
    - A ``baseline_failed`` finding is always BLOCK (VERDICT-002).
    - Findings are ordered BLOCK, WARN, INFO, then by check (tamper, rules, mutation),
      keeping each producer's own order, and get ids ``<check>-0001``, numbered per check
      in that order. Ids set by a producer are replaced.
    """
    fixed = [
        f.model_copy(update={"severity": Severity.BLOCK}) if f.kind == "baseline_failed" else f
        for f in findings
    ]
    ordered = sorted(
        fixed, key=lambda f: (_SEVERITY_ORDER[f.severity], _CHECK_ORDER.get(f.check, 3))
    )
    counters: Counter[str] = Counter()
    numbered = []
    for f in ordered:
        counters[f.check] += 1
        numbered.append(f.model_copy(update={"id": f"{f.check}-{counters[f.check]:04d}"}))
    return Verdict(
        status=status_of(numbered),
        base=base,
        head=head,
        rule_score=rule_score,
        diff_score=diff_score,
        findings=numbered,
        duration_s=round(duration_s, 3),
    )


def status_of(findings: Iterable[Finding]) -> Status:
    """``block``, ``warn`` or ``pass``, from the most severe finding."""
    severities = {f.severity for f in findings}
    if Severity.BLOCK in severities:
        return "block"
    if Severity.WARN in severities:
        return "warn"
    return "pass"


def exit_code(verdict: Verdict, *, strict: bool = False) -> int:
    """0 for pass or warn (1 for warn with ``--strict``), 2 for block."""
    if verdict.status == "warn" and strict:
        return STRICT_WARN_EXIT
    return EXIT_CODES[verdict.status]


__all__ = ["decide", "exit_code", "status_of"]
