"""``mutineer init`` (WP2). Writes only files that are missing; never overwrites."""

from __future__ import annotations

from pathlib import Path

import typer

from mutineer.config import load_config

RULES_TEMPLATE = """\
# Mutineer rules: the behaviour this project promises.
#
# Each rule is a [[rule]] table:
#   id        - uppercase prefix, a dash and 3+ digits, e.g. SHIP-003
#   statement - the promise, in one sentence
#   seam      - optional: dotted path to the public entry point that delivers it
#   status    - "active" (default) or "retired"
#
# Link a test to a rule with @pytest.mark.rule("SHIP-003"). The mark also works on a
# class or in a module's pytestmark, and takes several ids.
#
# Never delete a rule: set status = "retired" instead. `mutineer rules check` lists
# each rule's tests.
version = 1

# [[rule]]
# id = "SHIP-003"
# statement = "Orders of $50 or more ship free"
# seam = "shop.shipping.quote"
"""

# Each entry, with the spellings that already cover it.
_GITIGNORE_ENTRIES: dict[str, set[str]] = {
    "mutants/": {"mutants", "mutants/", "/mutants", "/mutants/"},
    ".mutineer/last-run.json": {
        ".mutineer/last-run.json",
        "/.mutineer/last-run.json",
        ".mutineer",
        ".mutineer/",
        "/.mutineer",
        "/.mutineer/",
    },
}


def init() -> int:
    """Write a rules.toml template and .gitignore entries. Never overwrites."""
    root = Path.cwd()
    config = load_config(root)

    rules_path = root / config.rules_file
    if rules_path.exists():
        typer.echo(f"{config.rules_file} already exists; left unchanged.")
    else:
        rules_path.parent.mkdir(parents=True, exist_ok=True)
        rules_path.write_text(RULES_TEMPLATE, encoding="utf-8")
        typer.echo(f"Wrote {config.rules_file}.")

    gitignore = root / ".gitignore"
    text = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    present = {line.strip() for line in text.splitlines()}
    missing = [entry for entry, spellings in _GITIGNORE_ENTRIES.items() if not present & spellings]
    if missing:
        prefix = "" if not text or text.endswith("\n") else "\n"
        with gitignore.open("a", encoding="utf-8") as fh:
            fh.write(prefix + "".join(f"{entry}\n" for entry in missing))
        typer.echo(f"Added {', '.join(missing)} to .gitignore.")
    else:
        typer.echo(".gitignore already has Mutineer's entries.")
    return 0
