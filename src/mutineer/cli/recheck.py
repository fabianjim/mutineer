"""``mutineer recheck``: rerun only the agent's surviving mutants from the last run."""

from __future__ import annotations

from pathlib import Path

import typer

from mutineer.cli.run import OutputFormat
from mutineer.errors import MutineerError


def recheck(
    format: OutputFormat = typer.Option(OutputFormat.TERMINAL, "--format", help="Output format."),
    output: Path | None = typer.Option(None, "--output", help="Write the report to this file."),
    max_rounds: int | None = typer.Option(
        None, "--max-rounds", min=1, help="Round cap. Default: [tool.mutineer] max_rounds (3)."
    ),
) -> int:
    """Rerun only the agent's surviving mutants from the last run."""
    raise MutineerError("not implemented yet")
