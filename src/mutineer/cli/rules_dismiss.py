"""``mutineer rules dismiss``: record a human's "no" to a question from the last run.

Answers go to ``.mutineer/answers.toml``, keyed by the function's mutmut name (see
``models.Answer``). A dismissal hides a question from everyone who reads later reports,
so it is a human decision: it asks for confirmation, and the next ``mutineer run`` shows
each new answer once as ``answer_added``.
"""

from __future__ import annotations

from pathlib import Path

import typer

from mutineer import ledger, state
from mutineer.cli._confirm import confirm_human
from mutineer.errors import MutineerError
from mutineer.models import Answer, Finding

QUESTIONS = ("survivor_unowned", "guarded_by_impl_only")


def dismiss(
    functions: list[str] = typer.Argument(
        ..., metavar="FUNCTION...", help="mutmut names, as in a finding's evidence.function."
    ),
    reason: str = typer.Option(..., "--reason", help="Why the answer is no, in one line."),
    yes: bool = typer.Option(False, "--yes", help="Don't ask for confirmation."),
) -> int:
    """Answer no to a survivor_unowned or guarded_by_impl_only question, so it isn't asked again."""
    reason = reason.strip()
    if not reason or "\n" in reason or "\r" in reason:
        raise MutineerError("--reason must be one non-empty line")
    root = Path.cwd()
    last = state.load(root)
    if last is None:
        raise MutineerError(f"no {state.STATE_FILE}; run `mutineer run` first")

    findings = {
        f.evidence.get("function"): f
        for f in reversed(last.verdict.findings)
        if f.kind in QUESTIONS
    }
    functions = list(dict.fromkeys(functions))
    unknown = [fn for fn in functions if fn not in findings]
    if unknown:
        raise MutineerError(
            f"the last run asked no survivor_unowned or guarded_by_impl_only question about "
            f"{', '.join(unknown)}; nothing was written (use evidence.function from the report)"
        )
    answers = [_answer(fn, findings[fn], reason) for fn in functions]

    confirm_human(
        "dismiss",
        [f"DISMISS {a.function} ({a.question}): {a.reason}" for a in answers],
        yes=yes,
    )
    existing = ledger.load_answers(root).answers
    for a in answers:
        if a in existing:
            typer.echo(f"ALREADY ANSWERED {a.function}")
            continue
        ledger.append_answer(root, a)
        existing = [*existing, a]
        typer.echo(f"DISMISSED {a.function}")
    typer.echo(
        f"Commit {ledger.ANSWERS_FILE}. The next `mutineer run` reports each new answer as "
        "answer_added."
    )
    return 0


def _answer(function: str, f: Finding, reason: str) -> Answer:
    qualname = f.evidence.get("qualname")
    return Answer(
        function=function,
        question=f.kind,
        rules=sorted(f.rules) if f.kind == "guarded_by_impl_only" else [],
        reason=reason,
        file=f.location.file if f.location else None,
        qualname=qualname if isinstance(qualname, str) else None,
    )
