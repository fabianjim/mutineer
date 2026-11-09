"""``mutineer resolve``: a human's answer to an agent's dispute.

The ids are ``mutant_disputed`` findings from the last run; each names a dispute in
``.mutineer/disputes.toml`` by ``evidence.function``, ``evidence.original`` and
``evidence.mutated``. Confirming or rejecting is a human decision, so it asks first.
"""

from __future__ import annotations

from pathlib import Path

import typer

from mutineer import ledger, state
from mutineer.cli._confirm import confirm_human
from mutineer.errors import MutineerError
from mutineer.models import DisputesFile, Finding


def resolve(
    finding_ids: list[str] = typer.Argument(
        ..., metavar="FINDING-ID...", help="mutant_disputed finding ids from the last run."
    ),
    confirm: bool = typer.Option(False, "--confirm", help="Agree the mutant is equivalent."),
    reject: bool = typer.Option(False, "--reject", help="Disagree: the agent writes the test."),
    reason: str | None = typer.Option(
        None, "--reason", help="Why, in one line. Required with --reject."
    ),
    yes: bool = typer.Option(False, "--yes", help="Don't ask for confirmation."),
) -> int:
    """Confirm or reject disputed mutants."""
    if confirm == reject:
        raise MutineerError("pass exactly one of --confirm or --reject")
    if reason is not None:
        reason = reason.strip()
        if not reason or "\n" in reason or "\r" in reason:
            raise MutineerError("--reason must be one non-empty line")
    if reject and reason is None:
        raise MutineerError("--reject needs --reason: say why the mutant matters, in one line")
    root = Path.cwd()
    last = state.load(root)
    if last is None:
        raise MutineerError(f"no {state.STATE_FILE}; run `mutineer run` first")

    findings = {f.id: f for f in last.verdict.findings if f.kind == "mutant_disputed"}
    disputes = ledger.load_disputes(root)
    keys = {d.key for d in disputes.disputes}
    wanted: dict[tuple[str, str, str], str] = {}
    bad: list[str] = []
    for fid in dict.fromkeys(finding_ids):
        f = findings.get(fid)
        key = _key(f) if f is not None else None
        if key is None or key not in keys:
            bad.append(fid)
            continue
        wanted[key] = fid
    if bad:
        raise MutineerError(
            f"{', '.join(bad)}: not a mutant_disputed finding of the last run whose dispute "
            f"is still in {ledger.DISPUTES_FILE}; nothing was written"
        )

    status = "confirmed" if confirm else "rejected"
    verb = "CONFIRM" if confirm else "REJECT"
    by_key = {d.key: d for d in disputes.disputes}
    changes = []
    for key, fid in wanted.items():
        d = by_key[key]
        because = f": {reason}" if reason else ""
        changes.append(
            f"{verb} {fid} {d.function} `{d.original}` -> `{d.mutated}` "
            f"(agent: {d.reason}){because}"
        )
    confirm_human(verb.lower(), changes, yes=yes, command="resolve")

    updated = [
        d.model_copy(update={"status": status, "response": reason}) if d.key in wanted else d
        for d in disputes.disputes
    ]
    ledger.save_disputes(root, DisputesFile(disputes=updated))
    for key, fid in wanted.items():
        typer.echo(f"{verb}ED {fid} {key[0]} `{key[1]}` -> `{key[2]}`")
    typer.echo(f"Commit {ledger.DISPUTES_FILE}.")
    return 0


def _key(f: Finding) -> tuple[str, str, str] | None:
    ev = f.evidence
    function, original, mutated = ev.get("function"), ev.get("original"), ev.get("mutated")
    if isinstance(function, str) and isinstance(original, str) and isinstance(mutated, str):
        return (function, original, mutated)
    return None
