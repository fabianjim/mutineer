"""Triage: turn an ``EngineRun`` into findings for the right reader, and score it.

Each mutant's status in each pass falls into one group:

- **K**: killed, or caught by the type check;
- **S**: survived;
- **N**: no tests;
- **I**: anything else (timeout, suspicious, segfault, skipped, interrupted, not checked),
  or a rule-covered mutant with no rule-pass result.

A function is *rule-covered* when one of the tests that cover it (``tests_by_function``,
from the all pass) has a rule id in the rule map. Its mutants are judged by both passes:

========  =========  =========================================
all pass  rule pass  finding
========  =========  =========================================
K         K          none
K         S or N     ``guarded_by_impl_only`` (human, per function)
S or N    S or N     ``survivor_rule`` (agent, per mutant)
S or N    K          ``mutant_inconclusive`` (human, per function)
I in either pass     ``mutant_inconclusive`` (human, per function)
========  =========  =========================================

Mutants of other functions are judged by the all pass alone: S or N is
``survivor_unowned`` and I is ``mutant_inconclusive``, both per function. A function no
test reaches is one ``unreached`` finding instead. Changed lines mutmut can't mutate are
one ``unmeasured_change`` per file.

Only ``survivor_rule`` goes to the agent (TRIAGE-001). Unmeasured lines have no mutants,
so they never enter a score (TRIAGE-002).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from mutineer.models import (
    Audience,
    EngineRun,
    Finding,
    FunctionRef,
    Location,
    MutantResult,
    MutantStatus,
    Rule,
    RuleMap,
    RulesFile,
    Score,
    Severity,
)

Group = Literal["K", "S", "N", "I"]

_GROUP: dict[MutantStatus, Group] = {
    MutantStatus.KILLED: "K",
    MutantStatus.TYPE_CHECK: "K",
    MutantStatus.SURVIVED: "S",
    MutantStatus.NO_TESTS: "N",
}

_LOST: frozenset[Group] = frozenset({"S", "N"})
"""Not caught: survived, or no test ran."""

SEAM_GUIDANCE = (
    "Test the promised behaviour through the seam. Don't assert on private names or mocks. "
    "If the mutant can't change behaviour visible through the seam, don't write a test; "
    "say so."
)


def status_group(status: MutantStatus | None) -> Group:
    """The triage group of a status; a missing status is inconclusive."""
    return "I" if status is None else _GROUP.get(status, "I")


@dataclass(frozen=True)
class Triage:
    """What ``triage`` found."""

    findings: list[Finding]
    rule_score: Score | None
    """Rule pass over rule-covered functions; None when no function is rule-covered."""
    diff_score: Score | None
    """All pass over every changed function; None when the baseline failed."""
    survivor_names: list[str] = field(default_factory=list)
    """Mutant names of the ``survivor_rule`` findings, which ``recheck`` reruns."""


@dataclass
class _Mutant:
    name: str
    function: str
    all: MutantResult | None = None
    rule: MutantResult | None = None

    @property
    def line(self) -> int | None:
        return self._detail().line

    @property
    def original(self) -> str | None:
        return self._detail().original

    @property
    def mutated(self) -> str | None:
        return self._detail().mutated

    def _detail(self) -> MutantResult:
        # the engine fills the text only for results that weren't killed, so a mutant
        # killed in the all pass has its text on the rule-pass record
        for r in (self.rule, self.all):
            if r is not None and r.mutated is not None:
                return r
        r = self.all or self.rule
        assert r is not None
        return r

    def evidence(self) -> dict[str, Any]:
        return {
            "mutant": self.name,
            "line": self.line,
            "original": self.original,
            "mutated": self.mutated,
            "all": self.all.status.value if self.all else None,
            "rule": self.rule.status.value if self.rule else None,
        }


def triage(run: EngineRun, rule_map: RuleMap, rules: RulesFile) -> Triage:
    """Findings and scores for ``run``, with rule coverage from ``rule_map``."""
    unmeasured = _unmeasured(run)
    if run.baseline_failed:
        # mutant results mean nothing when the suite fails without any mutant
        return Triage([_baseline_failed(run), *unmeasured], rule_score=None, diff_score=None)

    refs = {f.mutmut_name: f for f in run.functions}
    rules_by_id = {r.id: r for r in rules.rules}
    mutants = _mutants(run.results)
    by_function: dict[str, list[_Mutant]] = {}
    for m in mutants:
        by_function.setdefault(m.function, []).append(m)

    order = [f.mutmut_name for f in run.functions]
    order += sorted(set(by_function) - set(order))
    unreached = set(run.unreached)

    survivors: list[Finding] = []
    guarded: list[Finding] = []
    inconclusive: list[Finding] = []
    unowned: list[Finding] = []
    unreached_findings: list[Finding] = []
    survivor_names: list[str] = []
    rule_killed = rule_total = 0
    any_rule_covered = False

    for fn in order:
        ref = refs.get(fn)
        fn_mutants = by_function.get(fn, [])
        rule_tests = [t for t in run.tests_by_function.get(fn, []) if rule_map.get(t)]
        rule_ids = sorted({i for t in rule_tests for i in rule_map[t]})

        if fn in unreached:
            unreached_findings.append(_unreached(fn, ref, fn_mutants))
            continue

        if not rule_ids:
            lost = [m for m in fn_mutants if status_group(_status(m.all)) in _LOST]
            unsure = [m for m in fn_mutants if status_group(_status(m.all)) == "I"]
            if lost:
                unowned.append(_unowned(fn, ref, lost))
            if unsure:
                inconclusive.append(_inconclusive(fn, ref, unsure, []))
            continue

        any_rule_covered = True
        impl_only: list[_Mutant] = []
        unsure = []
        for m in fn_mutants:
            a, r = status_group(_status(m.all)), status_group(_status(m.rule))
            if r == "K":
                rule_killed += 1
            if r != "I":
                rule_total += 1
            if a == "I" or r == "I" or (a in _LOST and r == "K"):
                unsure.append(m)
            elif a == "K" and r in _LOST:
                impl_only.append(m)
            elif a in _LOST and r in _LOST:
                survivors.append(_survivor_rule(m, ref, rule_ids, rule_tests, rules_by_id))
                survivor_names.append(m.name)
        if impl_only:
            guarded.append(_guarded(fn, ref, impl_only, rule_ids))
        if unsure:
            inconclusive.append(_inconclusive(fn, ref, unsure, rule_ids))

    diff_killed = diff_total = 0
    for m in mutants:
        g = status_group(_status(m.all))
        diff_killed += g == "K"
        diff_total += g != "I"

    survivors.sort(key=_location_key)
    findings = [
        *survivors,
        *guarded,
        *inconclusive,
        *unowned,
        *unreached_findings,
        *unmeasured,
    ]
    return Triage(
        findings,
        rule_score=Score(killed=rule_killed, total=rule_total) if any_rule_covered else None,
        diff_score=Score(killed=diff_killed, total=diff_total),
        survivor_names=sorted(survivor_names),
    )


# ---- helpers ----


def _status(r: MutantResult | None) -> MutantStatus | None:
    return None if r is None else r.status


def _mutants(results: Iterable[MutantResult]) -> list[_Mutant]:
    out: dict[str, _Mutant] = {}
    for r in results:
        m = out.setdefault(r.name, _Mutant(r.name, r.function))
        if r.run == "all":
            m.all = r
        else:
            m.rule = r
    return list(out.values())


def _location_key(f: Finding) -> tuple[str, int]:
    loc = f.location
    return (loc.file, loc.line or 0) if loc else ("", 0)


def _name(fn: str, ref: FunctionRef | None) -> str:
    return ref.qualname if ref else fn


def _span(ref: FunctionRef | None, mutants: list[_Mutant]) -> Location | None:
    if ref is None:
        return None
    lines = sorted(m.line for m in mutants if m.line is not None)
    if not lines:
        return Location(file=ref.file, line=ref.start_line, end_line=ref.end_line)
    return Location(file=ref.file, line=lines[0], end_line=lines[-1])


def _count(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _seam_phrase(rule_id: str, rules_by_id: dict[str, Rule]) -> str:
    rule = rules_by_id.get(rule_id)
    if rule is not None and rule.seam:
        return f"{rule_id} through `{rule.seam}`"
    return f"{rule_id} through its public entry point (the rule names no seam)"


def _survivor_rule(
    m: _Mutant,
    ref: FunctionRef | None,
    rule_ids: list[str],
    rule_tests: list[str],
    rules_by_id: dict[str, Rule],
) -> Finding:
    where = f"line {m.line}" if m.line is not None else "this function"
    if m.original is not None and m.mutated is not None:
        change = f"`{m.original}` changed to `{m.mutated}`"
    else:
        change = f"mutant `{m.name}`"
    seams = " or ".join(_seam_phrase(i, rules_by_id) for i in rule_ids)
    evidence = m.evidence() | {
        "function": m.function,
        "rule_tests": rule_tests,
        "rule_statements": {i: rules_by_id[i].statement for i in rule_ids if i in rules_by_id},
    }
    return Finding(
        check="mutation",
        kind="survivor_rule",
        severity=Severity.WARN,
        audience=Audience.AGENT,
        rules=rule_ids,
        location=Location(file=ref.file, line=m.line) if ref else None,
        message=(
            f"Nothing catches this: {change} in {_name(m.function, ref)} and every test passes."
        ),
        evidence=evidence,
        next_action=f"Test {seams} so that this change to {where} fails. {SEAM_GUIDANCE}",
    )


def _guarded(
    fn: str, ref: FunctionRef | None, mutants: list[_Mutant], rule_ids: list[str]
) -> Finding:
    rules_text = ", ".join(rule_ids)
    return Finding(
        check="mutation",
        kind="guarded_by_impl_only",
        severity=Severity.INFO,
        audience=Audience.HUMAN,
        rules=rule_ids,
        location=_span(ref, mutants),
        message=(
            f"Only an implementation test catches {_count(len(mutants), 'mutant')} in "
            f"{_name(fn, ref)}; no {rules_text} test does."
        ),
        evidence={"function": fn, "mutants": [m.evidence() for m in mutants]},
        next_action=(
            f"Is this behaviour part of {rules_text}? If so, mark a test that proves it with "
            f"the rule; if not, nothing to do."
        ),
    )


def _inconclusive(
    fn: str, ref: FunctionRef | None, mutants: list[_Mutant], rule_ids: list[str]
) -> Finding:
    return Finding(
        check="mutation",
        kind="mutant_inconclusive",
        severity=Severity.INFO,
        audience=Audience.HUMAN,
        rules=rule_ids,
        location=_span(ref, mutants),
        message=(
            f"{_count(len(mutants), 'mutant')} in {_name(fn, ref)} had no clear result "
            "(a timeout, a crash, or passes that disagree), so they count in no score."
        ),
        evidence={"function": fn, "mutants": [m.evidence() for m in mutants]},
        next_action="Check for slow or flaky tests covering this function.",
    )


def _unowned(fn: str, ref: FunctionRef | None, mutants: list[_Mutant]) -> Finding:
    return Finding(
        check="mutation",
        kind="survivor_unowned",
        severity=Severity.INFO,
        audience=Audience.HUMAN,
        location=_span(ref, mutants),
        message=(
            f"{_count(len(mutants), 'mutant')} in {_name(fn, ref)} survived, and no rule "
            "test covers it."
        ),
        evidence={"function": fn, "mutants": [m.evidence() for m in mutants]},
        next_action="Is this behaviour a promise? If so, add a rule.",
    )


def _unreached(fn: str, ref: FunctionRef | None, mutants: list[_Mutant]) -> Finding:
    return Finding(
        check="mutation",
        kind="unreached",
        severity=Severity.INFO,
        audience=Audience.HUMAN,
        location=Location(file=ref.file, line=ref.start_line, end_line=ref.end_line)
        if ref
        else None,
        message=f"No test reaches {_name(fn, ref)}.",
        evidence={"function": fn, "mutants": len(mutants)},
        next_action="Is it untested, or dead code? Test it or remove it.",
    )


def _unmeasured(run: EngineRun) -> list[Finding]:
    by_file: dict[str, list[dict[str, Any]]] = {}
    for u in sorted(run.unmeasured, key=lambda u: (u.file, u.line or 0)):
        by_file.setdefault(u.file, []).append(
            {"line": u.line, "end_line": u.end_line, "reason": u.reason}
        )
    findings = []
    for file, locations in by_file.items():
        reasons = sorted({loc["reason"] for loc in locations})
        lines = [loc["line"] for loc in locations if loc["line"] is not None]
        ends = [loc["end_line"] or loc["line"] for loc in locations if loc["line"] is not None]
        findings.append(
            Finding(
                check="mutation",
                kind="unmeasured_change",
                severity=Severity.INFO,
                audience=Audience.HUMAN,
                location=Location(
                    file=file,
                    line=min(lines) if lines else None,
                    end_line=max(ends) if ends else None,
                ),
                message=(
                    f"Changed lines in {file} can't be mutated ({', '.join(reasons)}), "
                    "so no score measures them."
                ),
                evidence={"reasons": reasons, "locations": locations},
                next_action="Review these lines by hand; no test result covers them.",
            )
        )
    return findings


def collection_failed(output: str) -> Finding:
    """The ``baseline_failed`` BLOCK for a suite pytest can't even collect."""
    return Finding(
        check="mutation",
        kind="baseline_failed",
        severity=Severity.BLOCK,
        audience=Audience.HUMAN,
        message="pytest can't collect the test suite, so no test result can be trusted.",
        evidence={"output": output, "stage": "collection"},
        next_action="Fix the collection error, then run Mutineer again.",
    )


def _baseline_failed(run: EngineRun) -> Finding:
    return Finding(
        check="mutation",
        kind="baseline_failed",
        severity=Severity.BLOCK,
        audience=Audience.HUMAN,
        message="The test suite fails without any mutant, so no mutation result can be trusted.",
        evidence={"output": run.baseline_output or ""},
        next_action="Make the test suite pass, then run Mutineer again.",
    )


__all__ = ["SEAM_GUIDANCE", "Triage", "collection_failed", "status_group", "triage"]
