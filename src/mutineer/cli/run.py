"""``mutineer run``: judge the diff from the merge-base to the working tree."""

from __future__ import annotations

import shutil
import sys
import time
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import typer

from mutineer import gitdiff, state
from mutineer.checks import tamper
from mutineer.config import Config, load_config
from mutineer.engine import Engine
from mutineer.engine.mutmut_engine import MutmutEngine
from mutineer.errors import CollectionError, RulesError
from mutineer.models import Diff, EngineRun, Finding, RuleMap, RulesFile, Verdict
from mutineer.reporters import agent, json, markdown, terminal
from mutineer.rules import collect_rule_map, compare, load_rules, rule_findings
from mutineer.triage import Triage, collection_failed, triage
from mutineer.verdict import decide, exit_code


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
    started = time.monotonic()
    root = Path.cwd()
    config = load_config(root)
    base_ref = gitdiff.resolve_base_ref(root, base or config.base)
    checked = static_checks(root, config, base_ref)

    engine_run: EngineRun | None = None
    result = Triage(findings=[], rule_score=None, diff_score=None)
    if not no_mutation and not checked.suite_broken:
        engine_run = make_engine().run(checked.diff, checked.rule_map, root)
        result = triage(engine_run, checked.rule_map, checked.rules)

    verdict = decide(
        [*checked.findings, *result.findings],
        base=checked.diff.base,
        head=checked.diff.head,
        rule_score=result.rule_score,
        diff_score=result.diff_score,
        duration_s=time.monotonic() - started,
    )
    state.save(
        root,
        state.State(
            base_ref=base_ref,
            base=checked.diff.base,
            survivor_names=result.survivor_names,
            engine_run=engine_run,
            verdict=verdict,
        ),
    )
    emit(verdict, format, output)
    return exit_code(verdict, strict=strict)


# ---- shared with recheck ----


def make_engine() -> Engine:
    return MutmutEngine()


@dataclass(frozen=True)
class StaticChecks:
    """The diff, rules and rule map, and the findings that need no mutation."""

    diff: Diff
    rules: RulesFile
    rule_map: RuleMap
    findings: list[Finding]
    suite_broken: bool = False
    """pytest couldn't collect the tests; ``findings`` holds a ``baseline_failed`` BLOCK."""


def static_checks(root: Path, config: Config, base_ref: str) -> StaticChecks:
    """Load the diff and rules, collect the rule map, and run tamper and the rules checks.

    Raises ``RulesError`` when a rule id was deleted from the rules file. When pytest can't
    collect the tests, the suite is broken: the result holds a ``baseline_failed`` BLOCK
    instead of the rule findings (the rule map is unknown), and ``suite_broken`` is set so
    callers skip mutation. Tamper detection reads files only, so it still runs.
    """
    diff = gitdiff.load_diff(root, base_ref, config.rules_file)
    head_rules = _head_rules(root, config)
    rules_changes = compare(
        _base_rules(root, diff.base, config), head_rules, rules_file=config.rules_file
    )
    test_paths = _test_paths(root, diff.base, config)
    tamper_findings = tamper.check(diff, root, head_rules, test_paths=test_paths)
    try:
        rule_map = collect_rule_map(root, config)
    except CollectionError as exc:
        return StaticChecks(
            diff=diff,
            rules=head_rules,
            rule_map={},
            findings=[*tamper_findings, *rules_changes, collection_failed(exc.output)],
            suite_broken=True,
        )
    findings = [*tamper_findings, *rules_changes, *rule_findings(head_rules, rule_map)]
    return StaticChecks(diff=diff, rules=head_rules, rule_map=rule_map, findings=findings)


def emit(verdict: Verdict, format: OutputFormat, output: Path | None) -> None:
    """Render ``verdict`` to stdout, or to ``output`` when given."""
    if format == OutputFormat.TERMINAL:
        color = output is None and sys.stdout.isatty()
        text = terminal.render(verdict, color=color, width=_width() if color else 100)
    elif format == OutputFormat.MARKDOWN:
        text = markdown.render(verdict)
    elif format == OutputFormat.JSON:
        text = json.render(verdict)
    else:
        text = agent.render(verdict)
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")


def _width() -> int:
    return max(60, min(shutil.get_terminal_size().columns, 140))


def _head_rules(root: Path, config: Config) -> RulesFile:
    path = root / config.rules_file
    if not path.is_file():
        return RulesFile()
    return load_rules(path.read_text(encoding="utf-8"))


def _base_rules(root: Path, sha: str, config: Config) -> RulesFile | None:
    """The rules file at the merge-base; None if it is absent or invalid there.

    An invalid base file is skipped rather than fatal, so a broken base can't block the
    change that fixes it; the head file is always validated.
    """
    text = gitdiff.read_base_file(root, sha, config.rules_file)
    if text is None:
        return None
    try:
        return load_rules(text)
    except RulesError:
        return None


def _test_paths(root: Path, sha: str, config: Config) -> list[str]:
    """Head ``test_paths`` plus base ones, so narrowing them can't hide a tampered test."""
    paths = list(config.test_paths)
    text = gitdiff.read_base_file(root, sha, "pyproject.toml")
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
