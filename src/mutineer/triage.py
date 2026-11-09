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

With a ``Ledger``, the two questions for a human (``survivor_unowned`` and
``guarded_by_impl_only``) say what was already recorded about them:

- **proposed**: a pending proposal answers the question. A proposal is linked to a
  function through its tests (``strip_params`` of a proposed test equals that of one of the
  function's covering tests, or of its implementation tests for ``guarded_by_impl_only``),
  because a proposal names tests, not functions. For
  ``guarded_by_impl_only`` it must add tests to one of the finding's rules; for
  ``survivor_unowned`` it must be a new rule;
- **answered**: a human said no (``.mutineer/answers.toml``). Answers are keyed by the
  mutmut name, which is unique and stable while the function keeps its module and name
  (finding ids are renumbered every run, lines move and qualnames aren't unique). A
  renamed function is asked about again, the safe direction. For ``guarded_by_impl_only``
  the rule set must also be the same, so a changed rule set asks again.

A proposal takes precedence over an answer. Neither changes kind, severity or audience.

An agent may dispute a ``survivor_rule`` mutant it believes can't change visible behaviour
(``.mutineer/disputes.toml``). A dispute is keyed by (mutmut name, original text, mutated
text), not by mutmut's mutant number, which changes when the function is edited; when the
line changes the key stops matching and the mutant is triaged as usual. Only a mutant
that would be a ``survivor_rule`` is affected:

=========  ======================  ===============  ===============  ========
dispute    finding                 severity         ``recheck``      scores
=========  ======================  ===============  ===============  ========
none       ``survivor_rule``       WARN, agent      reruns it        survived
open       ``mutant_disputed``     WARN, human      skips it         survived
confirmed  ``mutant_disputed``     INFO, human      skips it         none
rejected   ``survivor_rule``       WARN, agent      reruns it        survived
=========  ======================  ===============  ===============  ========
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from mutineer.ledger import Ledger, strip_params
from mutineer.models import (
    Audience,
    Dispute,
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


DisputeKey = tuple[str, str, str]


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

    @property
    def key(self) -> DisputeKey | None:
        """The dispute key, or None when the text is unknown (it then matches nothing)."""
        if self.original is None or self.mutated is None:
            return None
        return (self.function, self.original, self.mutated)

    def evidence(self) -> dict[str, Any]:
        return {
            "mutant": self.name,
            "line": self.line,
            "original": self.original,
            "mutated": self.mutated,
            "all": self.all.status.value if self.all else None,
            "rule": self.rule.status.value if self.rule else None,
        }


def triage(
    run: EngineRun,
    rule_map: RuleMap,
    rules: RulesFile,
    *,
    ledger: Ledger | None = None,
    seam_reached: Callable[[str, str], bool] | None = None,
) -> Triage:
    """Findings and scores for ``run``, with rule coverage from ``rule_map``.

    ``seam_reached`` answers (seam, rule test node id) -> does that test exercise the seam
    (``seams.seam_reach``). A ``survivor_rule`` whose rule tests never do names those
    tests instead of the seam. Without it, every seam counts as reaching.

    With ``ledger``, the questions for a human carry their tests, any pending proposal or
    human answer, and a ``next_action`` naming the commands that answer them. Without it,
    they read as they did before answers existed. Its disputes turn matching survivors
    into ``mutant_disputed`` questions, or annotate them when rejected.
    """
    unmeasured = _unmeasured(run)
    if run.baseline_failed:
        # mutant results mean nothing when the suite fails without any mutant
        return Triage([_baseline_failed(run), *unmeasured], rule_score=None, diff_score=None)

    refs = {f.mutmut_name: f for f in run.functions}
    disputes = _disputes(ledger)
    rules_by_id = {r.id: r for r in rules.rules}
    mutants = _mutants(run.results)
    by_function: dict[str, list[_Mutant]] = {}
    for m in mutants:
        by_function.setdefault(m.function, []).append(m)

    order = [f.mutmut_name for f in run.functions]
    order += sorted(set(by_function) - set(order))
    unreached = set(run.unreached)

    survivors: list[Finding] = []
    disputed: list[Finding] = []
    unscored: set[str] = set()
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
        covering = run.tests_by_function.get(fn, [])
        rule_tests = [t for t in covering if rule_map.get(t)]
        rule_ids = sorted({i for t in rule_tests for i in rule_map[t]})

        if fn in unreached:
            unreached_findings.append(_unreached(fn, ref, fn_mutants))
            continue

        if not rule_ids:
            lost = [m for m in fn_mutants if status_group(_status(m.all)) in _LOST]
            unsure = [m for m in fn_mutants if status_group(_status(m.all)) == "I"]
            if lost:
                finding = _unowned(fn, ref, lost)
                if ledger is not None:
                    finding = _with_ledger(finding, ref, sorted(covering), ledger)
                unowned.append(finding)
            if unsure:
                inconclusive.append(_inconclusive(fn, ref, unsure, []))
            continue

        any_rule_covered = True
        impl_only: list[_Mutant] = []
        unsure = []
        reach: list[_Reach] | None = None
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
            elif a in _LOST:  # r is lost too here
                if reach is None:
                    reach = _seam_reach(rule_ids, rule_tests, rule_map, rules_by_id, seam_reached)
                finding = _survivor_rule(m, ref, rule_ids, rule_tests, rules_by_id, reach)
                dispute = disputes.get(m.key) if m.key is not None else None
                if dispute is None:
                    survivors.append(finding)
                    survivor_names.append(m.name)
                elif dispute.status == "rejected":
                    survivors.append(_rejected(finding, dispute))
                    survivor_names.append(m.name)
                else:
                    disputed.append(_disputed(finding, m, ref, dispute))
                    if dispute.status == "confirmed":
                        # counted as lost above; a confirmed one counts in no score
                        rule_total -= 1
                        unscored.add(m.name)
        if impl_only:
            finding = _guarded(fn, ref, impl_only, rule_ids)
            if ledger is not None:
                impl_tests = sorted(set(covering) - set(rule_tests))
                finding = _with_ledger(finding, ref, impl_tests, ledger)
            guarded.append(finding)
        if unsure:
            inconclusive.append(_inconclusive(fn, ref, unsure, rule_ids))

    diff_killed = diff_total = 0
    for m in mutants:
        if m.name in unscored:
            continue
        g = status_group(_status(m.all))
        diff_killed += g == "K"
        diff_total += g != "I"

    survivors.sort(key=_location_key)
    disputed.sort(key=_location_key)
    findings = [
        *survivors,
        *disputed,
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


def disputed_names(run: EngineRun, ledger: Ledger) -> set[str]:
    """Names of mutants in ``run`` that an open or confirmed dispute matches.

    ``recheck`` leaves them out of its rerun: they wait for a human, or a human agreed.
    """
    disputes = _disputes(ledger)
    return {
        m.name
        for m in _mutants(run.results)
        if m.key is not None
        and (d := disputes.get(m.key)) is not None
        and d.status in ("open", "confirmed")
    }


# ---- helpers ----


def _disputes(ledger: Ledger | None) -> dict[DisputeKey, Dispute]:
    if ledger is None:
        return {}
    return {d.key: d for d in ledger.disputes.disputes}


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


@dataclass(frozen=True)
class _Reach:
    """Whether rule ``rule_id``'s seam reaches a function, and its tests that cover it."""

    rule_id: str
    seam: str | None
    reaches: bool
    tests: list[str]


def _seam_reach(
    rule_ids: list[str],
    rule_tests: list[str],
    rule_map: RuleMap,
    rules_by_id: dict[str, Rule],
    seam_reached: Callable[[str, str], bool] | None,
) -> list[_Reach]:
    """Per rule: its seam reaches when one of its covering tests mentions it.

    A rule with no seam never reaches. With no ``seam_reached``, every seam reaches.
    """
    out = []
    for rule_id in rule_ids:
        rule = rules_by_id.get(rule_id)
        seam = rule.seam if rule is not None and rule.seam else None
        tests = sorted(t for t in rule_tests if rule_id in rule_map.get(t, []))
        if seam is None:
            reaches = False
        elif seam_reached is None:
            reaches = True
        else:
            reaches = any(seam_reached(seam, t) for t in tests)
        out.append(_Reach(rule_id, seam, reaches, tests))
    return out


def _survivor_guidance(reach: list[_Reach], where: str) -> str:
    """The ``next_action`` of a ``survivor_rule``: how to test it, then how to mark it."""
    parts = []
    for r in reach:
        tests = ", ".join(f"`{t}`" for t in r.tests)
        if r.reaches:
            parts.append(
                f"Test {r.rule_id} through `{r.seam}` so that this change to {where} fails."
            )
        elif r.seam is not None:
            parts.append(
                f"`{r.seam}` doesn't reach this code; {r.rule_id}'s tests that do are {tests}. "
                f"Test the promised behaviour the way they do, so that this change to {where} "
                "fails."
            )
        else:
            parts.append(
                f"{r.rule_id} names no seam; its tests that reach this code are {tests}. Test "
                f"the promised behaviour the way they do, so that this change to {where} fails."
            )
    if len(reach) > 1:
        parts.insert(0, "Any one of these rules will do.")
        mark = "<the rule you tested>"
    else:
        mark = reach[0].rule_id
    parts.append(
        f'Mark the new test @pytest.mark.rule("{mark}") with that existing rule; an unmarked '
        "test can't clear this finding. Don't mark tests that only exercise wiring, and never "
        "mark with an id that isn't in the rules file. Don't assert on private names or mocks. "
        "Only if no caller can observe the change, run mutineer dispute <this finding's id> "
        '"<why>"; "the seam doesn\'t reach this code" is a valid reason when the rule tests '
        "reach it only through wiring."
    )
    return " ".join(parts)


def _survivor_rule(
    m: _Mutant,
    ref: FunctionRef | None,
    rule_ids: list[str],
    rule_tests: list[str],
    rules_by_id: dict[str, Rule],
    reach: list[_Reach],
) -> Finding:
    where = f"line {m.line}" if m.line is not None else "this function"
    change = _change(m)
    evidence = m.evidence() | {
        "function": m.function,
        "rule_tests": rule_tests,
        "rule_statements": {i: rules_by_id[i].statement for i in rule_ids if i in rules_by_id},
        "seams": {r.rule_id: {"seam": r.seam, "reaches": r.reaches} for r in reach},
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
        next_action=_survivor_guidance(reach, where),
    )


def _change(m: _Mutant) -> str:
    if m.original is not None and m.mutated is not None:
        return f"`{m.original}` changed to `{m.mutated}`"
    return f"mutant `{m.name}`"


def _dispute_evidence(d: Dispute) -> dict[str, Any]:
    return {"reason": d.reason, "status": d.status, "response": d.response}


def _rejected(f: Finding, d: Dispute) -> Finding:
    """``f`` (a ``survivor_rule``) whose dispute a human rejected: the agent must test it."""
    return f.model_copy(
        update={
            "evidence": f.evidence | {"dispute": _dispute_evidence(d)},
            "next_action": (
                f'A human rejected the dispute of this mutant ("{d.response}"), so it matters: '
                f"{f.next_action}"
            ),
        }
    )


def _disputed(f: Finding, m: _Mutant, ref: FunctionRef | None, d: Dispute) -> Finding:
    """``f`` (a ``survivor_rule``) as a ``mutant_disputed`` question for a human."""
    evidence = f.evidence | {"dispute": _dispute_evidence(d)}
    if ref is not None:
        evidence["qualname"] = ref.qualname
    name = _name(m.function, ref)
    if d.status == "confirmed":
        severity = Severity.INFO
        message = (
            f"A human confirmed the agent's dispute: {_change(m)} in {name} can't change "
            f'visible behaviour ("{d.reason}"), so it counts in no score.'
        )
        action = "Nothing to do; remove the dispute from .mutineer/disputes.toml to ask again."
    else:
        severity = Severity.WARN
        message = (
            f"The agent disputes this survivor: {_change(m)} in {name} survives every test, "
            f'and the agent says "{d.reason}".'
        )
        action = (
            "A human decides: if the change can't alter promised behaviour, run mutineer "
            "resolve <this finding's id> --confirm; if it can, run mutineer resolve <this "
            'finding\'s id> --reject --reason "<why>" and the agent tests it.'
        )
    return f.model_copy(
        update={
            "kind": "mutant_disputed",
            "severity": severity,
            "audience": Audience.HUMAN,
            "message": message,
            "evidence": evidence,
            "next_action": action,
        }
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


def _with_ledger(f: Finding, ref: FunctionRef | None, tests: list[str], ledger: Ledger) -> Finding:
    """``f`` (a question for a human) with its tests, and what the ledger says about it.

    ``tests`` are the tests the question is about: every covering test for
    ``survivor_unowned``, the implementation tests for ``guarded_by_impl_only``. A pending
    proposal answers the question when it lists one of them.
    """
    fn = f.evidence["function"]
    evidence = dict(f.evidence)
    if ref is not None:
        evidence["qualname"] = ref.qualname
    evidence["tests" if f.kind == "survivor_unowned" else "impl_tests"] = tests

    stripped = {strip_params(t) for t in tests}
    proposed = sorted(
        p.id
        for p in ledger.proposals.proposals
        if any(strip_params(t) in stripped for t in p.tests)
        and (
            p.statement is not None
            if f.kind == "survivor_unowned"
            else p.statement is None and p.id in f.rules
        )
    )
    answer = next(
        (
            a
            for a in ledger.answers.answers
            if a.function == fn
            and a.question == f.kind
            and (f.kind == "survivor_unowned" or sorted(a.rules) == f.rules)
        ),
        None,
    )
    rules_text = ", ".join(f.rules)
    if proposed:
        evidence["proposed"] = proposed
        args = " ".join(proposed)
        action = (
            f"Rule {', '.join(proposed)} is proposed for this: a human runs mutineer rules "
            f"accept {args} or mutineer rules reject {args}."
        )
    elif answer is not None:
        evidence["answer"] = {"reason": answer.reason, "question": answer.question}
        if f.kind == "survivor_unowned":
            action = (
                f'Answered before: not a promise ("{answer.reason}"). If this change makes it '
                "one, propose a rule and remove the answer from .mutineer/answers.toml."
            )
        else:
            action = (
                f'Answered before: not part of {rules_text} ("{answer.reason}"). If this '
                f"change makes it part of {rules_text}, propose the tests and remove the "
                "answer from .mutineer/answers.toml."
            )
    elif f.kind == "survivor_unowned":
        action = (
            "Is this behaviour a promise? If yes, propose a rule: mutineer rules propose "
            '--prefix <PREFIX> --statement "<the promise>" --test <a test from evidence.tests '
            "that proves it>; a human then runs mutineer rules accept <id>. If no, run "
            f'mutineer rules dismiss {fn} --reason "<why>" so this isn\'t asked again.'
        )
    else:
        rule = f.rules[0] if len(f.rules) == 1 else f"<one of {rules_text}>"
        action = (
            f"Is this behaviour part of {rules_text}? If yes, mutineer rules propose --rule "
            f"{rule} --test <a test from evidence.impl_tests that catches it>; a human then "
            f"runs mutineer rules accept {rule}. If no, run mutineer rules dismiss {fn} "
            '--reason "<why>".'
        )
    return f.model_copy(update={"evidence": evidence, "next_action": action})


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


__all__ = [
    "Triage",
    "collection_failed",
    "disputed_names",
    "status_group",
    "triage",
]
