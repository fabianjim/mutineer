"""``mutineer rules ...`` (WP2)."""

from __future__ import annotations

from pathlib import Path

import typer

from mutineer import gitdiff
from mutineer.config import load_config
from mutineer.errors import ConfigError, RulesError
from mutineer.models import Finding, RuleMap, RulesFile
from mutineer.rules import collect_rule_map, compare, load_rules, rule_findings

app = typer.Typer(help="Inspect and validate the rules file.", no_args_is_help=True)


@app.command(name="check")
def check(
    base: str | None = typer.Option(
        None, "--base", help="Base branch. Default: [tool.mutineer] base, else main, else master."
    ),
) -> int:
    """Validate rules.toml, compare it with the base, and list each rule's tests."""
    root = Path.cwd()
    config = load_config(root)
    path = root / config.rules_file
    if not path.is_file():
        raise RulesError(f"no rules file at {config.rules_file}; run `mutineer init` to create one")
    head = load_rules(path.read_text(encoding="utf-8"))

    findings: list[Finding] = []
    base_ref = base or config.base
    base_rules, note = _base_rules(root, base_ref, config.rules_file)
    if note:
        typer.echo(note)
    findings += compare(base_rules, head, rules_file=config.rules_file)

    rule_map = collect_rule_map(root, config)
    findings += rule_findings(head, rule_map)

    _print_rules(head, rule_map)
    _print_findings(findings)
    return 0


def _base_rules(
    root: Path, base_ref: str | None, rules_file: str
) -> tuple[RulesFile | None, str | None]:
    """The rules file at the merge-base, and a note to print when it can't be compared."""
    try:
        sha = gitdiff.merge_base(root, base_ref)
    except ConfigError as exc:
        if base_ref:  # asked for explicitly: a bad ref is an error
            raise
        return None, f"Not compared with a base ({exc})."
    text = gitdiff.read_base_file(root, sha, rules_file)
    if text is None:
        return None, None
    try:
        return load_rules(text), None
    except RulesError as exc:
        return None, f"Not compared with the base: its rules file is invalid ({exc})."


def _print_rules(rules: RulesFile, rule_map: RuleMap) -> None:
    tests_by_rule: dict[str, list[str]] = {}
    for node_id, ids in rule_map.items():
        for rule_id in ids:
            tests_by_rule.setdefault(rule_id, []).append(node_id)
    if not rules.rules:
        typer.echo("No rules yet.")
    for rule in rules.rules:
        status = "" if rule.status == "active" else f" [{rule.status}]"
        typer.echo(f"{rule.id}{status}: {rule.statement}")
        if rule.seam:
            typer.echo(f"  seam: {rule.seam}")
        tests = sorted(tests_by_rule.get(rule.id, []))
        if not tests:
            typer.echo("  no tests")
        for test in tests:
            typer.echo(f"  - {test}")


def _print_findings(findings: list[Finding]) -> None:
    if not findings:
        return
    typer.echo("")
    for f in findings:
        typer.echo(f"{f.severity.value.upper()} {f.kind}: {f.message}")
