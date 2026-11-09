"""Proposed rules, kept in ``.mutineer/proposals.toml`` until a human accepts or rejects them.

Agents never edit the rules file or mark a test with an id nobody accepted. They propose
a new rule, or more tests for an existing one, and the proposal waits here.

- ``load_proposals`` / ``save_proposals``: read and write the file (written atomically).
- ``validate``: every problem with a set of proposals, one line each.
- ``add_proposals``: turn requests (``rules propose``) into proposals, merged into a file.

Human answers to triage questions live next to them in ``.mutineer/answers.toml``, a file
humans write and commit (``load_answers`` / ``append_answer``). ``load_ledger`` reads both.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import tomllib
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from mutineer.errors import RulesError
from mutineer.models import (
    RULE_ID_PATTERN,
    Answer,
    AnswersFile,
    Audience,
    Finding,
    Location,
    Proposal,
    ProposalsFile,
    RulesFile,
    Severity,
)
from mutineer.rules import Collected, _describe
from mutineer.seams import seam_problem
from mutineer.testscan import TestFunc, scan_source

PROPOSALS_FILE = ".mutineer/proposals.toml"
ANSWERS_FILE = ".mutineer/answers.toml"

PREFIX_PATTERN = r"^[A-Z][A-Z0-9]*$"

_HEADER = """\
# Rules proposed with `mutineer rules propose`. A human accepts one with
# `mutineer rules accept <id>` or drops it with `mutineer rules reject <id>`.
# Mutineer rewrites this file; comments are not kept.
"""


# ---- the file ----


def load_proposals(root: Path) -> ProposalsFile:
    """The proposals file, or an empty one when it is missing. Raises ``RulesError``."""
    path = root / PROPOSALS_FILE
    if not path.is_file():
        return ProposalsFile()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise RulesError(f"{PROPOSALS_FILE} is not valid TOML ({exc}); fix the syntax") from exc
    if "proposals" in data:
        raise RulesError(
            f"{PROPOSALS_FILE}: unknown key 'proposals'; write each proposal as a "
            "[[proposal]] table"
        )
    try:
        proposals = ProposalsFile.model_validate(data)
    except ValidationError as exc:
        raise RulesError(f"{PROPOSALS_FILE}: {_describe(exc, data, 'proposal')}") from exc
    dupes = _duplicates(p.id for p in proposals.proposals)
    if dupes:
        raise RulesError(
            f"{PROPOSALS_FILE}: duplicate proposal id {', '.join(dupes)}; ids must be unique"
        )
    return proposals


def save_proposals(root: Path, proposals: ProposalsFile) -> None:
    """Write ``proposals`` atomically to ``root/.mutineer/proposals.toml``."""
    path = root / PROPOSALS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".proposals-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_render(proposals))
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _render(proposals: ProposalsFile) -> str:
    lines = [_HEADER + f"version = {proposals.version}"]
    for p in proposals.proposals:
        table = ["[[proposal]]", f"id = {toml_str(p.id)}"]
        if p.statement is not None:
            table.append(f"statement = {toml_str(p.statement)}")
        if p.seam is not None:
            table.append(f"seam = {toml_str(p.seam)}")
        table.append(f"tests = [{', '.join(toml_str(t) for t in p.tests)}]")
        lines.append("\n".join(table))
    return "\n\n".join(lines) + "\n"


def toml_str(value: str) -> str:
    """``value`` as a TOML basic string."""
    # JSON escapes quotes, backslashes and C0 controls the way TOML does; TOML also
    # forbids a raw DEL
    return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")


# ---- ids ----


def strip_params(node_id: str) -> str:
    """``node_id`` without a trailing parametrize ``[...]``."""
    path, sep, rest = node_id.partition("::")
    if sep and rest.endswith("]") and "[" in rest:
        rest = rest[: rest.index("[")]
    return f"{path}{sep}{rest}"


def next_id(prefix: str, taken: Iterable[str]) -> str:
    """``PREFIX-NNN``, one more than the highest number with ``prefix`` in ``taken``."""
    pattern = re.compile(rf"{re.escape(prefix)}-([0-9]+)")
    highest = 0
    for rule_id in taken:
        m = pattern.fullmatch(rule_id)
        if m:
            highest = max(highest, int(m.group(1)))
    return f"{prefix}-{highest + 1:03d}"


def _duplicates(ids: Iterable[str]) -> list[str]:
    return sorted(i for i, n in Counter(ids).items() if n > 1)


def _marker_ids(collected: Collected) -> set[str]:
    return {rule_id for ids in collected.rule_map.values() for rule_id in ids}


# ---- validation ----


def validate(
    proposals: ProposalsFile | Sequence[Proposal],
    *,
    rules: RulesFile,
    collected: Collected,
    root: Path,
) -> list[str]:
    """Every problem with ``proposals``, one line each, prefixed with the proposal id.

    Empty when everything is valid.
    """
    items = proposals.proposals if isinstance(proposals, ProposalsFile) else list(proposals)
    problems = [
        f"{rule_id}: proposed more than once; ids must be unique"
        for rule_id in _duplicates(p.id for p in items)
    ]
    scanner = _Scanner(root)
    for p in items:
        for problem in _problems(p, rules=rules, collected=collected, scanner=scanner):
            problems.append(f"{p.id}: {problem}")
    return problems


def _problems(
    p: Proposal,
    *,
    rules: RulesFile,
    collected: Collected,
    scanner: _Scanner,
    pending: Iterable[str] = (),
) -> list[str]:
    """Problems with one proposal. ``pending``: ids of other proposals it must not reuse."""
    problems: list[str] = []
    if not re.fullmatch(RULE_ID_PATTERN, p.id):
        return [f"id {p.id!r} must look like SHIP-003 (uppercase prefix, dash, 3+ digits)"]
    known = {r.id: r for r in rules.rules}
    if p.statement is not None:
        if p.id in known:
            problems.append(f"{p.id} is already in the rules file; choose another id")
        elif p.id in set(pending):
            problems.append(f"{p.id} is already proposed; choose another id")
        elif p.id in _marker_ids(collected):
            problems.append(
                f"tests are already marked with {p.id}, which is not a rule; choose another id"
            )
        if not p.statement.strip():
            problems.append("the statement is empty; say what the rule promises in one line")
        elif "\n" in p.statement or "\r" in p.statement:
            problems.append("the statement must be one line")
    else:
        rule = known.get(p.id)
        if rule is None:
            problems.append(
                f"{p.id} is not in the rules file; propose a new rule with a statement instead"
            )
        elif rule.status != "active":
            problems.append(f"{p.id} is retired; only an active rule can get more tests")
        if p.seam is not None:
            problems.append("a proposal for an existing rule can't set a seam")
        if not p.tests:
            problems.append("give at least one test to add to the rule")
    if p.seam is not None and p.statement is not None:
        seam = seam_problem(scanner.root, p.seam)
        if seam:
            problems.append(seam)
    for test in p.tests:
        problem = _test_problem(test, p.id, collected, scanner)
        if problem:
            problems.append(f"test {test}: {problem}")
    return problems


def _test_problem(test: str, rule_id: str, collected: Collected, scanner: _Scanner) -> str | None:
    matches = [n for n in collected.node_ids if n == test or strip_params(n) == test]
    if not matches:
        return "pytest doesn't collect it; use a node id as `pytest --collect-only -q` prints it"
    file = test.partition("::")[0]
    tests = scanner.tests(file)
    if tests is None:
        return f"{file} can't be read or parsed, so the test can't be marked"
    if strip_params(test) not in {t.key for t in tests}:
        return f"not found as a test function in {file}, so it can't be marked"
    if any(rule_id in collected.rule_map.get(n, []) for n in matches):
        return f"already marked with {rule_id}"
    return None


@dataclass
class _Scanner:
    """Static test scans, one per file."""

    root: Path
    _cache: dict[str, list[TestFunc] | None] = field(default_factory=dict)

    def tests(self, file: str) -> list[TestFunc] | None:
        if file not in self._cache:
            try:
                text = (self.root / file).read_text(encoding="utf-8")
                self._cache[file] = scan_source(text, file)
            except (OSError, UnicodeDecodeError, SyntaxError):
                self._cache[file] = None
        return self._cache[file]


# ---- proposing ----


@dataclass(frozen=True)
class Request:
    """One ``rules propose`` request: exactly one of ``prefix``, ``id`` or ``rule``."""

    prefix: str | None = None
    id: str | None = None
    rule: str | None = None
    statement: str | None = None
    seam: str | None = None
    tests: tuple[str, ...] = ()


@dataclass(frozen=True)
class Added:
    """A request that was turned into a proposal."""

    id: str
    statement: str | None
    tests: tuple[str, ...]


def add_proposals(
    requests: Sequence[Request],
    *,
    existing: ProposalsFile,
    rules: RulesFile,
    collected: Collected,
    root: Path,
) -> tuple[ProposalsFile, list[Added], list[tuple[int, str]]]:
    """Merge ``requests`` into ``existing``, all or nothing.

    Ids are allocated in request order. A request for an existing rule that already has a
    pending proposal adds its tests to it. Returns the new file, what was added, and every
    problem as ``(request index, message)``; when there are problems, the file is
    ``existing`` unchanged.
    """
    taken = (
        {r.id for r in rules.rules} | {p.id for p in existing.proposals} | _marker_ids(collected)
    )
    merged: dict[str, Proposal] = {p.id: p for p in existing.proposals}
    added: list[Added] = []
    problems: list[tuple[int, str]] = []
    scanner = _Scanner(root)
    for index, req in enumerate(requests):
        errs, proposal = _proposal_for(req, taken)
        if proposal is not None:
            taken.add(proposal.id)
            errs += _problems(
                proposal, rules=rules, collected=collected, scanner=scanner, pending=merged
            )
        problems += [(index, e) for e in errs]
        if errs or proposal is None:
            continue
        old = merged.get(proposal.id)
        if old is not None and old.statement is None:
            tests = sorted(set(old.tests) | set(proposal.tests))
            merged[proposal.id] = old.model_copy(update={"tests": tests})
        else:
            merged[proposal.id] = proposal
        added.append(Added(proposal.id, proposal.statement, tuple(proposal.tests)))
    if problems:
        return existing, [], problems
    return ProposalsFile(proposals=list(merged.values())), added, []


def _proposal_for(req: Request, taken: set[str]) -> tuple[list[str], Proposal | None]:
    given = [k for k in ("prefix", "id", "rule") if getattr(req, k) is not None]
    if len(given) != 1:
        return ["give exactly one of prefix, id or rule"], None
    errs: list[str] = []
    if req.prefix is not None:
        if not re.fullmatch(PREFIX_PATTERN, req.prefix):
            return [f"prefix {req.prefix!r} must be uppercase letters and digits, e.g. PRICE"], None
        rule_id = next_id(req.prefix, taken)
    else:
        rule_id = req.id if req.id is not None else req.rule  # type: ignore[assignment]
        if not re.fullmatch(RULE_ID_PATTERN, rule_id):
            return [
                f"id {rule_id!r} must look like SHIP-003 (uppercase prefix, dash, 3+ digits)"
            ], None
    if req.rule is not None:
        if req.statement is not None:
            errs.append("a proposal for an existing rule takes no statement")
        statement = None
    else:
        if req.statement is None:
            errs.append("a new rule needs a statement")
        statement = (req.statement or "").strip()
    if errs:
        return errs, None
    proposal = Proposal(
        id=rule_id, statement=statement, seam=req.seam, tests=sorted(set(req.tests))
    )
    return [], proposal


def remove_proposals(existing: ProposalsFile, ids: Sequence[str]) -> ProposalsFile:
    """``existing`` without the proposals ``ids``. Raises ``RulesError`` for an unknown id."""
    pending = {p.id for p in existing.proposals}
    unknown = [i for i in dict.fromkeys(ids) if i not in pending]
    if unknown:
        raise RulesError(
            f"no pending proposal {', '.join(unknown)}; nothing was removed "
            "(`mutineer rules check` lists the proposals)"
        )
    drop = set(ids)
    return ProposalsFile(proposals=[p for p in existing.proposals if p.id not in drop])


# ---- answers ----

_ANSWERS_HEADER = """\
# Human answers to Mutineer's questions, written with `mutineer rules dismiss`.
# Each [[answer]] stops one question from being asked again. Delete an entry to ask it
# again. Comments here are kept.
version = 1
"""


def load_answers(root: Path) -> AnswersFile:
    """The answers file, or an empty one when it is missing. Raises ``RulesError``."""
    path = root / ANSWERS_FILE
    if not path.is_file():
        return AnswersFile()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesError(f"{ANSWERS_FILE} can't be read ({exc}); fix or remove it") from exc
    return parse_answers(text)


def parse_answers(text: str) -> AnswersFile:
    """``text`` as an answers file. Raises ``RulesError`` with a fix when it is invalid."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulesError(f"{ANSWERS_FILE} is not valid TOML ({exc}); fix the syntax") from exc
    if "answers" in data:
        raise RulesError(
            f"{ANSWERS_FILE}: unknown key 'answers'; write each answer as an [[answer]] table"
        )
    try:
        return AnswersFile.model_validate(data)
    except ValidationError as exc:
        raise RulesError(f"{ANSWERS_FILE}: {_describe(exc, data, 'answer')}") from exc


def append_answer(root: Path, answer: Answer) -> None:
    """Append ``answer`` as an ``[[answer]]`` table to the answers file.

    The existing text is kept as it is, so a human's comments survive. The result is
    checked before it is written; on a problem the file is left unchanged.
    """
    path = root / ANSWERS_FILE
    old = path.read_text(encoding="utf-8") if path.is_file() else _ANSWERS_HEADER
    if old and not old.endswith("\n"):
        old += "\n"
    table = [
        "[[answer]]",
        f"function = {toml_str(answer.function)}",
        f"question = {toml_str(answer.question)}",
        f"rules = [{', '.join(toml_str(r) for r in answer.rules)}]",
        f"reason = {toml_str(answer.reason)}",
    ]
    if answer.file is not None:
        table.append(f"file = {toml_str(answer.file)}")
    if answer.qualname is not None:
        table.append(f"qualname = {toml_str(answer.qualname)}")
    text = old + "\n" + "\n".join(table) + "\n"
    parse_answers(text)
    _write_atomic(path, text, prefix=".answers-")


def _write_atomic(path: Path, text: str, *, prefix: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=prefix, suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def answer_findings(base_text: str | None, head: AnswersFile) -> list[Finding]:
    """One ``answer_added`` (INFO, for a human) per answer added or changed since the base.

    An answer hides a question from everyone who reads the report, so a new one is shown
    once, in the change that records it. ``base_text`` is the file at the merge-base; an
    absent or invalid base counts as empty. A removed answer gives no finding: the
    question just comes back.
    """
    before: list[Answer] = []
    if base_text is not None:
        try:
            before = parse_answers(base_text).answers
        except RulesError:
            before = []
    findings = []
    for answer in head.answers:
        if answer in before:
            continue
        findings.append(
            Finding(
                check="rules",
                kind="answer_added",
                severity=Severity.INFO,
                audience=Audience.HUMAN,
                rules=list(answer.rules),
                location=Location(file=ANSWERS_FILE),
                message=f"A human answer was recorded for {answer.function}: {answer.reason}",
                evidence=answer.model_dump(),
                next_action="Check that a human recorded this answer.",
            )
        )
    return findings


@dataclass(frozen=True)
class Ledger:
    """What humans and agents recorded about triage questions: proposals and answers."""

    proposals: ProposalsFile = field(default_factory=ProposalsFile)
    answers: AnswersFile = field(default_factory=AnswersFile)


def load_ledger(root: Path) -> Ledger:
    """The proposals and answers files. Raises ``RulesError`` when either is invalid."""
    return Ledger(proposals=load_proposals(root), answers=load_answers(root))
