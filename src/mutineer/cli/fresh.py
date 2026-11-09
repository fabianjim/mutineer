"""``mutineer fresh``: would ``run`` reuse the last verdict? Runs nothing, writes nothing."""

from __future__ import annotations

from pathlib import Path

import typer

from mutineer import fingerprint, gitdiff, state
from mutineer.cli.run import current_fingerprint
from mutineer.config import load_config
from mutineer.errors import MutineerError

FRESH_EXIT = 0
STALE_EXIT = 1

# one line per paragraph, so the help wraps to the terminal
HELP = "\n\n".join(
    [
        "Exit 0 and print `fresh` when `run` with these options would reuse the last verdict; "
        "otherwise exit 1 and print `stale:` with the inputs that changed. "
        "Runs nothing and writes nothing.",
        "The verdict depends on the merge-base, every file git sees in the repository, the "
        "files under mutmut's source and also_copy paths and the test paths (ignored or not), "
        "the rules and ledger files, the config, the Python environment, and PYTEST_ADDOPTS, "
        "PYTEST_PLUGINS, PYTEST_DISABLE_PLUGIN_AUTOLOAD, PYTHONPATH and PYTHONHASHSEED.",
        "Not covered: ignored files outside those paths, changes inside git submodules "
        "outside them, other environment variables, the network and other services, and "
        "flaky tests. Use `run --no-reuse` to always run.",
    ]
)


def fresh(
    base: str | None = typer.Option(
        None, "--base", help="Base branch. Default: [tool.mutineer] base, else main, else master."
    ),
    no_mutation: bool = typer.Option(False, "--no-mutation", help="Ask about `run --no-mutation`."),
) -> int:
    """Would `run` with these options reuse the last verdict? See ``HELP``."""
    root = Path.cwd()
    config = load_config(root)
    base_ref = gitdiff.resolve_base_ref(root, base or config.base)
    current = current_fingerprint(root, config, base_ref, no_mutation=no_mutation)
    try:
        last = state.load(root)
    except MutineerError as exc:
        return _stale(str(exc))
    if last is None:
        return _stale(f"no {state.STATE_FILE}")
    if last.fingerprint is None:
        return _stale("the last run stored no fingerprint (recheck, or files changed during it)")
    changed = fingerprint.differences(last.fingerprint, current)
    if changed:
        return _stale("; ".join(changed))
    typer.echo("fresh")
    return FRESH_EXIT


def _stale(reason: str) -> int:
    typer.echo(f"stale: {reason}")
    return STALE_EXIT
