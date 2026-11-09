"""``mutineer dispute``: an agent's claim that a ``survivor_rule`` mutant is equivalent.

Some surviving mutants change nothing a caller can see (``"utf-8"`` to ``"UTF-8"``). The
agent records that here instead of writing a test that pins an implementation detail. The
dispute goes to ``.mutineer/disputes.toml``, keyed by function and mutation text (see
``models.Dispute``), and waits for a human's ``mutineer resolve``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from mutineer import ledger, state
from mutineer.errors import MutineerError
from mutineer.models import Dispute, DisputesFile


def dispute(
    finding_id: str = typer.Argument(
        ..., metavar="FINDING-ID", help="A survivor_rule finding's id from the last run."
    ),
    reason: str = typer.Argument(
        ..., metavar="REASON", help="Why no caller can see the change, in one line."
    ),
) -> int:
    """Dispute a survivor_rule finding: say why the mutant can't change visible behaviour."""
    reason = reason.strip()
    root = Path.cwd()
    last = state.load(root)
    if last is None:
        raise MutineerError(f"no {state.STATE_FILE}; run `mutineer run` first")

    finding = next((f for f in last.verdict.findings if f.id == finding_id), None)
    if finding is None:
        raise MutineerError(
            f"the last run has no finding {finding_id}; nothing was written "
            "(use a survivor_rule finding's id from the report)"
        )
    if finding.kind != "survivor_rule":
        raise MutineerError(
            f"{finding_id} is a {finding.kind} finding; only survivor_rule findings can be disputed"
        )
    ev = finding.evidence
    function, original, mutated = ev.get("function"), ev.get("original"), ev.get("mutated")
    if not (isinstance(function, str) and isinstance(original, str) and isinstance(mutated, str)):
        raise MutineerError(
            f"{finding_id} has no original and mutated text, so it can't be disputed; "
            "write a test that catches it"
        )
    if not reason or "\n" in reason or "\r" in reason:
        raise MutineerError("REASON must be one non-empty line")

    existing = ledger.load_disputes(root)
    key = (function, original, mutated)
    old = next((d for d in existing.disputes if d.key == key), None)
    change = f"`{original}` -> `{mutated}`"
    if old is not None and old.status == "rejected":
        raise MutineerError(
            f"a human already rejected this dispute of {function} {change}: "
            f"{old.response!r}; write a test that catches it"
        )
    if old is not None:
        typer.echo(f"ALREADY DISPUTED {finding_id} {function} {change} ({old.status})")
        return 0

    line = ev.get("line")
    new = Dispute(
        function=function,
        original=original,
        mutated=mutated,
        rules=sorted(finding.rules),
        reason=reason,
        file=finding.location.file if finding.location else None,
        line=line if isinstance(line, int) else None,
    )
    ledger.save_disputes(root, DisputesFile(disputes=[*existing.disputes, new]))
    where = f" line {new.line}" if new.line is not None else ""
    typer.echo(f"DISPUTED {finding_id} {function}{where}: {change}")
    typer.echo("Next: run `mutineer recheck --format agent`.")
    return 0
