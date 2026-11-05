"""``mutineer rules ...``. WP0 stub; WP2 owns it."""

from __future__ import annotations

import typer

from mutineer.errors import MutineerError

app = typer.Typer(help="Inspect and validate the rules file.", no_args_is_help=True)


@app.command(name="check")
def check(
    base: str | None = typer.Option(
        None, "--base", help="Base branch. Default: [tool.mutineer] base, else main, else master."
    ),
) -> int:
    """Validate rules.toml, compare it with the base, and list each rule's tests."""
    raise MutineerError("not implemented yet")
