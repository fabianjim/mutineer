"""The ``mutineer`` command line. Each command lives in its own module, owned per the build spec."""

from __future__ import annotations

import sys

import typer
from typer.exceptions import TyperException

from mutineer import __version__
from mutineer.cli import init, recheck, rules, run
from mutineer.errors import MutineerError

USAGE_ERROR_EXIT = 4
"""Usage errors exit 4 (Mutineer could not run), not click's default 2."""

app = typer.Typer(
    name="mutineer",
    help="Check whether agent-written work is really done before it merges.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
)


def _version(value: bool) -> None:
    if value:
        typer.echo(f"mutineer {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False, "--version", callback=_version, is_eager=True, help="Show the version and exit."
    ),
) -> None:
    """Check whether agent-written work is really done before it merges."""


app.command(name="run")(run.run)
app.command(name="recheck")(recheck.recheck)
app.add_typer(rules.app, name="rules")
app.command(name="init")(init.init)


def main(argv: list[str] | None = None) -> int:
    """Console-script entry point. Returns the process exit code (build spec section 5)."""
    args = sys.argv[1:] if argv is None else list(argv)
    if not args:
        args = ["--help"]
    command = typer.main.get_command(app)
    try:
        result = command.main(args=args, prog_name="mutineer", standalone_mode=False)
    except MutineerError as exc:
        typer.echo(f"mutineer: {exc}", err=True)
        return exc.exit_code
    except typer.Exit as exc:
        return int(exc.exit_code)
    except typer.Abort:
        typer.echo("Aborted.", err=True)
        return USAGE_ERROR_EXIT
    except TyperException as exc:
        # click's UsageError, BadParameter, NoSuchOption, ... (vendored inside typer).
        show = getattr(exc, "show", None)
        if callable(show):
            show()
        else:
            typer.echo(f"mutineer: {exc}", err=True)
        return USAGE_ERROR_EXIT
    return result if isinstance(result, int) else 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
