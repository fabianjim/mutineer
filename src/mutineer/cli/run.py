"""``mutineer run``. WP0 stub; WP1 adds ``--no-mutation``; WP4 owns it from step 3."""

from __future__ import annotations

import json
import tomllib
from enum import StrEnum
from pathlib import Path

import typer

from mutineer.checks import tamper
from mutineer.config import Config, load_config
from mutineer.errors import MutineerError
from mutineer.gitdiff import load_diff, read_base_file
from mutineer.models import Finding, RulesFile, Severity
from mutineer.rules import load_rules


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
    if not no_mutation:
        raise MutineerError("mutation testing is not implemented yet; pass --no-mutation")
    return tamper_only(Path.cwd(), base, output, strict)


def tamper_only(root: Path, base: str | None, output: Path | None, strict: bool) -> int:
    """WP1's ``--no-mutation``: tamper findings as plain JSON, whatever ``--format`` says.

    WP4 replaces the rendering and adds the rules findings.
    """
    config = load_config(root)
    diff = load_diff(root, base or config.base, config.rules_file)
    rules = _head_rules(root, config)
    test_paths = _test_paths(root, diff.base, config)
    findings = tamper.check(diff, root, rules, test_paths=test_paths)
    status = _status(findings)
    payload = {
        "status": status,
        "base": diff.base,
        "head": diff.head,
        "findings": [f.model_dump(mode="json") for f in findings],
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")
    if status == "block":
        return 2
    return 1 if status == "warn" and strict else 0


def _status(findings: list[Finding]) -> str:
    severities = {f.severity for f in findings}
    if Severity.BLOCK in severities:
        return "block"
    return "warn" if Severity.WARN in severities else "pass"


def _head_rules(root: Path, config: Config) -> RulesFile:
    path = root / config.rules_file
    if not path.is_file():
        return RulesFile()
    return load_rules(path.read_text(encoding="utf-8"))


def _test_paths(root: Path, sha: str, config: Config) -> list[str]:
    """Head ``test_paths`` plus base ones, so narrowing them can't hide a tampered test."""
    paths = list(config.test_paths)
    text = read_base_file(root, sha, "pyproject.toml")
    if text is None:
        return paths
    try:
        table = tomllib.loads(text).get("tool", {}).get("mutineer", {})
    except tomllib.TOMLDecodeError:
        return paths
    base_paths = table.get("test_paths", ["tests"]) if isinstance(table, dict) else []
    if isinstance(base_paths, list):
        paths.extend(p for p in base_paths if isinstance(p, str) and p not in paths)
    return paths
