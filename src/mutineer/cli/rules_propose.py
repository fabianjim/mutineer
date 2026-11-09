"""``mutineer rules propose`` and ``mutineer rules reject``.

Proposals go to ``.mutineer/proposals.toml``; rules.toml and the tests are never touched.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from mutineer import ledger
from mutineer.config import load_config
from mutineer.errors import RulesError
from mutineer.models import RulesFile
from mutineer.rules import collect, load_rules

_BATCH_KEYS = frozenset({"prefix", "id", "rule", "statement", "seam", "tests"})


def propose(
    prefix: str | None = typer.Option(
        None, "--prefix", help="Propose a new rule; its id is the next free PREFIX-NNN."
    ),
    rule_id: str | None = typer.Option(None, "--id", help="Propose a new rule with this id."),
    rule: str | None = typer.Option(
        None, "--rule", help="Propose more tests for this existing active rule."
    ),
    batch: str | None = typer.Option(
        None,
        "--batch",
        help='A JSON array of proposals (keys: prefix | id | rule, statement, seam, tests); "-" '
        "reads stdin.",
    ),
    statement: str | None = typer.Option(
        None, "--statement", help="The new rule's promise, in one line."
    ),
    seam: str | None = typer.Option(
        None, "--seam", help="Dotted path to the public entry point that delivers the rule."
    ),
    tests: list[str] = typer.Option(
        [], "--test", help="A pytest node id that proves the rule (repeatable)."
    ),
) -> int:
    """Propose a rule, or more tests for a rule, without touching rules.toml."""
    modes = [m for m in (prefix, rule_id, rule, batch) if m is not None]
    if len(modes) != 1:
        raise RulesError("give exactly one of --prefix, --id, --rule or --batch")
    if batch is not None:
        if statement is not None or seam is not None or tests:
            raise RulesError("--batch takes no --statement, --seam or --test; put them in FILE")
        requests, problems = _read_batch(batch)
        if problems:
            _fail([f"[{i}] {p}" for i, p in problems], batch=True)
    else:
        requests = [
            ledger.Request(
                prefix=prefix,
                id=rule_id,
                rule=rule,
                statement=statement,
                seam=seam,
                tests=tuple(tests),
            )
        ]

    root = Path.cwd()
    config = load_config(root)
    rules = _load_rules_if_any(root / config.rules_file)
    existing = ledger.load_proposals(root)
    collected = collect(root)
    updated, added, problems = ledger.add_proposals(
        requests, existing=existing, rules=rules, collected=collected, root=root
    )
    if problems:
        if batch is not None:
            _fail([f"[{i}] {p}" for i, p in problems], batch=True)
        _fail([p for _, p in problems], batch=False)
    ledger.save_proposals(root, updated)
    for a in added:
        summary = a.statement if a.statement is not None else f"+{len(a.tests)} tests"
        typer.echo(f"PROPOSED {a.id}  {summary}")
    for a in added:
        if a.statement is not None and not a.tests:
            typer.echo(
                f"note: {a.id} has no tests; it will be unguarded after accept "
                "until a test is marked"
            )
    return 0


def reject(
    ids: list[str] = typer.Argument(..., metavar="ID...", help="Pending proposal ids to drop."),
) -> int:
    """Drop pending proposals from .mutineer/proposals.toml."""
    root = Path.cwd()
    existing = ledger.load_proposals(root)
    ledger.save_proposals(root, ledger.remove_proposals(existing, ids))
    for rule_id in dict.fromkeys(ids):
        typer.echo(f"REJECTED {rule_id}")
    return 0


def _fail(problems: list[str], *, batch: bool) -> None:
    for problem in problems:
        typer.echo(problem, err=True)
    what = "the batch has" if batch else "the proposal has"
    n = len(problems)
    raise RulesError(
        f"{what} {n} problem{'s' if n != 1 else ''}; nothing was written, fix them and rerun"
    )


def _load_rules_if_any(path: Path) -> RulesFile:
    # proposals can bootstrap a project that has no rules file yet
    if not path.is_file():
        return RulesFile()
    return load_rules(path.read_text(encoding="utf-8"))


def _read_batch(source: str) -> tuple[list[ledger.Request], list[tuple[int, str]]]:
    try:
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesError(f"can't read the batch file {source} ({exc})") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RulesError(f"the batch is not valid JSON ({exc}); give a JSON array") from exc
    if not isinstance(data, list):
        raise RulesError("the batch must be a JSON array of objects")
    requests: list[ledger.Request] = []
    problems: list[tuple[int, str]] = []
    for index, item in enumerate(data):
        request, errs = _request(item)
        problems += [(index, e) for e in errs]
        if request is not None:
            requests.append(request)
    return requests, problems


def _request(item: Any) -> tuple[ledger.Request | None, list[str]]:
    if not isinstance(item, dict):
        return None, ["each entry must be a JSON object"]
    errs = [f"unknown key {k!r}" for k in sorted(set(item) - _BATCH_KEYS)]
    strings: dict[str, str | None] = {}
    for key in ("prefix", "id", "rule", "statement", "seam"):
        value = item.get(key)
        if value is not None and not isinstance(value, str):
            errs.append(f"{key!r} must be a string")
            value = None
        strings[key] = value
    tests = item.get("tests", [])
    if not isinstance(tests, list) or not all(isinstance(t, str) for t in tests):
        errs.append("'tests' must be a list of node id strings")
        tests = []
    if errs:
        return None, errs
    request = ledger.Request(
        prefix=strings["prefix"],
        id=strings["id"],
        rule=strings["rule"],
        statement=strings["statement"],
        seam=strings["seam"],
        tests=tuple(tests),
    )
    return request, []
