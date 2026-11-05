"""``mutineer run``. WP0 stub; WP1 adds ``--no-mutation``; WP4 owns it from step 3."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import typer

from mutineer.errors import MutineerError


class OutputFormat(StrEnum):
    TERMINAL = "terminal"
    MARKDOWN = "markdown"
    JSON = "json"
    AGENT = "agent"


def run(
    base: str | None = typer.Option(
        None, "--base", help="Base branch. Default: [tool.mutineer] base, else main, else master."
    ),
    format: OutputFormat = typer.Option(OutputFormat.TERMINAL, "--format", help="Output format."),
    output: Path | None = typer.Option(None, "--output", help="Write the report to this file."),
    no_mutation: bool = typer.Option(
        False, "--no-mutation", help="Tamper and rules checks only; skip mutation testing."
    ),
    strict: bool = typer.Option(False, "--strict", help="Exit 1 on warn."),
) -> int:
    """Judge the diff from the merge-base to the working tree."""
    raise MutineerError("not implemented yet")
