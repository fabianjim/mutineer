"""``mutineer rules accept``: turn pending proposals into rules and test markers.

This is the human's step. Like ``init``, it writes outside ``.mutineer/``: it appends new
rules to rules.toml and adds ``@pytest.mark.rule("<id>")`` to the proposed tests. It is the
only Mutineer command that edits test files. Everything is computed and checked in memory
first; on any problem nothing is written.
"""

from __future__ import annotations

from pathlib import Path

import typer

from mutineer import ledger
from mutineer.cli._confirm import confirm_human
from mutineer.config import load_config
from mutineer.errors import RulesError
from mutineer.markers import add_rule_marker
from mutineer.models import Proposal
from mutineer.rules import collect, load_rules

_DONE = (
    "Commit rules.toml, the marked tests and .mutineer/proposals.toml together. The next "
    "`mutineer run` reports each new rule as rules_file_changed."
)


def accept(
    ids: list[str] = typer.Argument(
        None, metavar="[ID...]", help="Pending proposal ids to accept.", show_default=False
    ),
    all_: bool = typer.Option(False, "--all", help="Accept every pending proposal."),
    yes: bool = typer.Option(False, "--yes", help="Don't ask for confirmation."),
) -> int:
    """Accept proposals: add their rules to rules.toml and mark their tests."""
    ids = list(ids or [])
    if all_ == bool(ids):
        raise RulesError("give proposal ids or --all, not both or neither")
    root = Path.cwd()
    config = load_config(root)
    rules_path = root / config.rules_file
    if not rules_path.is_file():
        raise RulesError(f"no rules file at {config.rules_file}; run `mutineer init` first")
    rules_text = _read(rules_path)
    rules = load_rules(rules_text)

    existing = ledger.load_proposals(root)
    if all_:
        picked = list(existing.proposals)
        if not picked:
            raise RulesError("no pending proposals to accept")
    else:
        pending = {p.id: p for p in existing.proposals}
        unknown = [i for i in dict.fromkeys(ids) if i not in pending]
        if unknown:
            raise RulesError(
                f"no pending proposal {', '.join(unknown)}; nothing was written "
                "(`mutineer rules check` lists the proposals)"
            )
        picked = [pending[i] for i in dict.fromkeys(ids)]

    collected = collect(root)
    problems = ledger.validate(picked, rules=rules, collected=collected, root=root)
    if problems:
        for problem in problems:
            typer.echo(problem, err=True)
        n = len(problems)
        raise RulesError(
            f"{n} problem{'s' if n != 1 else ''} with the proposals; nothing was written, "
            "fix them or reject the proposals"
        )

    changes: list[str] = []
    new_rules_text = _append_rules(rules_text, picked, config.rules_file)
    if new_rules_text != rules_text:
        added = [p.id for p in picked if p.statement is not None]
        changes.append(f"{config.rules_file}: add {', '.join(added)}")

    marks = _marks_by_file(picked)
    new_tests: dict[str, str] = {}
    notes: list[str] = []
    for file, by_test in marks.items():
        old = _read(root / file)
        text = old
        for qualname, rule_ids in by_test.items():
            for rule_id in rule_ids:
                text = add_rule_marker(text, qualname, rule_id, path=file)
            changes.append(f"{file}: mark {qualname} with {', '.join(rule_ids)}")
            cases = [
                n for n in collected.node_ids if ledger.strip_params(n) == f"{file}::{qualname}"
            ]
            if len(cases) > 1:
                notes.append(
                    f"note: the mark on {file}::{qualname} applies to all {len(cases)} "
                    "parametrize cases"
                )
        if text != old:
            new_tests[file] = text
    accepted = [p.id for p in picked]
    changes.append(f"{ledger.PROPOSALS_FILE}: remove {', '.join(accepted)}")

    confirm_human("accept", changes, yes=yes)

    # rules first: an interrupted accept leaves at worst an unguarded rule, never
    # markers with an unknown id
    if new_rules_text != rules_text:
        _write(rules_path, new_rules_text)
    for file, text in new_tests.items():
        _write(root / file, text)
    ledger.save_proposals(root, ledger.remove_proposals(existing, accepted))

    for rule_id in accepted:
        typer.echo(f"ACCEPTED {rule_id}")
    for note in notes:
        typer.echo(note)
    changed = [config.rules_file] if new_rules_text != rules_text else []
    for file in [*changed, *new_tests, ledger.PROPOSALS_FILE]:
        typer.echo(f"changed {file}")
    typer.echo(_DONE)
    return 0


def _append_rules(text: str, proposals: list[Proposal], rules_file: str) -> str:
    new = [p for p in proposals if p.statement is not None]
    if not new:
        return text
    tables = []
    for p in new:
        assert p.statement is not None
        lines = ["[[rule]]", f"id = {ledger.toml_str(p.id)}"]
        lines.append(f"statement = {ledger.toml_str(p.statement)}")
        if p.seam is not None:
            lines.append(f"seam = {ledger.toml_str(p.seam)}")
        tables.append("\n".join(lines) + "\n")
    sep = "" if not text or text.endswith("\n") else "\n"
    result = text + sep + "".join(f"\n{t}" for t in tables)
    try:
        loaded = load_rules(result)
    except RulesError as exc:
        raise RulesError(
            f"can't append to {rules_file} ({exc}); add the rules by hand as [[rule]] tables"
        ) from exc
    missing = [p.id for p in new if p.id not in {r.id for r in loaded.rules}]
    if missing:
        raise RulesError(
            f"appending to {rules_file} didn't add {', '.join(missing)}; "
            "add the rules by hand as [[rule]] tables"
        )
    return result


def _marks_by_file(proposals: list[Proposal]) -> dict[str, dict[str, list[str]]]:
    """file -> test qualname -> rule ids to mark, with parametrize cases merged."""
    out: dict[str, dict[str, list[str]]] = {}
    for p in proposals:
        for test in p.tests:
            file, _, qualname = ledger.strip_params(test).partition("::")
            ids = out.setdefault(file, {}).setdefault(qualname, [])
            if p.id not in ids:
                ids.append(p.id)
    return out


def _read(path: Path) -> str:
    # bytes, so line endings survive untouched
    try:
        return path.read_bytes().decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RulesError(f"can't read {path} ({exc})") from exc


def _write(path: Path, text: str) -> None:
    path.write_bytes(text.encode("utf-8"))
