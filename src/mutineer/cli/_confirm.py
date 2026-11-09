"""A confirmation step for commands that record a human decision.

``confirm_human`` lists the changes and asks before anything is written. Without a
terminal it refuses unless ``--yes`` is passed, so a command run from a script or by an
agent doesn't silently answer for the human.

This only adds a step. It is not a security boundary: an agent can pass ``--yes``. The real
protection is that every rules.toml change is reported by the next ``mutineer run`` as
``rules_file_changed``, so people see it in review.
"""

from __future__ import annotations

import sys

import typer

from mutineer.errors import MutineerError


def confirm_human(
    action: str, changes: list[str], *, yes: bool, command: str | None = None
) -> None:
    """Return when the human agrees to ``changes``; raise ``MutineerError`` (exit 4) otherwise.

    The refusal names ``mutineer {command}``; ``command`` defaults to ``rules {action}``.
    """
    if yes:
        return
    if not sys.stdin.isatty():
        name = command if command is not None else f"rules {action}"
        raise MutineerError(
            f"`mutineer {name}` records a human decision; run it in a terminal, or pass --yes"
        )
    for change in changes:
        typer.echo(change)
    if not typer.confirm("Proceed?", default=False):
        raise MutineerError("Nothing changed.")
