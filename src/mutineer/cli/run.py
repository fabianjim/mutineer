"""``mutineer run``: judge the diff from the merge-base to the working tree."""

from __future__ import annotations

import shutil
import sys
import time
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

import typer

from mutineer import fingerprint, gitdiff, state
from mutineer.checks import tamper
from mutineer.config import Config, load_config
from mutineer.engine import Engine
from mutineer.engine.mutmut_engine import MutmutEngine
from mutineer.errors import CollectionError, MutineerError, RulesError
from mutineer.ledger import (
    ANSWERS_FILE,
    DISPUTES_FILE,
    Ledger,
    answer_findings,
    dispute_findings,
    load_ledger,
)
from mutineer.models import Diff, EngineRun, Finding, RuleMap, RulesFile, Verdict
from mutineer.reporters import agent, json, markdown, terminal
from mutineer.rules import collect_rule_map, compare, load_rules, rule_findings
from mutineer.seams import seam_reach
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
    no_reuse: bool = typer.Option(
        False, "--no-reuse", help="Always run, even when nothing changed since the last run."
    ),
) -> int:
    """Judge the diff from the merge-base to the working tree.

    Reuses the last verdict when nothing it depends on changed (see `mutineer fresh`).
    """
    started = time.monotonic()
    root = Path.cwd()
    config = load_config(root)
    base_ref = gitdiff.resolve_base_ref(root, base or config.base)
    before = current_fingerprint(root, config, base_ref, no_mutation=no_mutation)
    last = None if no_reuse else _last_state(root)
    if last is not None and last.fingerprint == before:
        verdict = last.verdict.model_copy(
            update={"duration_s": round(time.monotonic() - started, 3)}
        )
        if last.base_ref != base_ref:
            # recheck reads the name, so keep the one this run was given
            state.save(root, last.model_copy(update={"base_ref": base_ref}))
        emit(verdict, format, output)
        return exit_code(verdict, strict=strict)

    checked = static_checks(root, config, base_ref)

    engine_run: EngineRun | None = None
    result = Triage(findings=[], rule_score=None, diff_score=None)
    if not no_mutation and not checked.suite_broken:
        engine_run = make_engine().run(checked.diff, checked.rule_map, root)
        result = triage(
            engine_run,
            checked.rule_map,
            checked.rules,
            ledger=checked.ledger,
            seam_reached=seam_reach(root),
        )

    verdict = decide(
        [*checked.findings, *result.findings],
        base=checked.diff.base,
        head=checked.diff.head,
        rule_score=result.rule_score,
        diff_score=result.diff_score,
        duration_s=time.monotonic() - started,
    )
    # a file edited during the run may not match the result, so store no fingerprint then
    after = current_fingerprint(root, config, base_ref, no_mutation=no_mutation)
    state.save(
        root,
        state.State(
            base_ref=base_ref,
            base=checked.diff.base,
            survivor_names=result.survivor_names,
            engine_run=engine_run,
            verdict=verdict,
            fingerprint=before if after == before else None,
        ),
    )
    emit(verdict, format, output)
    return exit_code(verdict, strict=strict)


def current_fingerprint(
    root: Path, config: Config, base_ref: str, *, no_mutation: bool
) -> fingerprint.Fingerprint:
    """The fingerprint ``run`` with these options would compare with the stored one."""
    gitdiff.check_repo(root)
    sha = gitdiff.merge_base(root, base_ref)
    return fingerprint.compute(
        root,
        config,
        sha,
        mutation=not no_mutation,
        test_paths=_test_paths(root, sha, config),
    )


def _last_state(root: Path) -> state.State | None:
    """The stored state, or None when there is none or it can't be read (a real run)."""
    try:
        return state.load(root)
    except MutineerError:
        return None


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
    ledger: Ledger = field(default_factory=Ledger)
    """Pending proposals, human answers and disputes, which triage applies."""


def static_checks(root: Path, config: Config, base_ref: str) -> StaticChecks:
    """Load the diff and rules, collect the rule map, and run tamper and the rules checks.

    Also loads the ledger (proposals, answers and disputes), and reports each answer added
    or changed since the merge-base as ``answer_added`` and each dispute newly confirmed
    since then as ``dispute_confirmed``.

    Raises ``RulesError`` when a rule id was deleted from the rules file, or when the
    proposals, answers or disputes file is invalid. When pytest can't
    collect the tests, the suite is broken: the result holds a ``baseline_failed`` BLOCK
    instead of the rule findings (the rule map is unknown), and ``suite_broken`` is set so
    callers skip mutation. Tamper detection reads files only, so it still runs.
    """
    diff = gitdiff.load_diff(root, base_ref, config.rules_file)
    head_rules = _head_rules(root, config)
    rules_changes = compare(
        _base_rules(root, diff.base, config), head_rules, rules_file=config.rules_file
    )
    recorded = load_ledger(root)
    answers_added = answer_findings(
        gitdiff.read_base_file(root, diff.base, ANSWERS_FILE), recorded.answers
    )
    disputes_confirmed = dispute_findings(
        gitdiff.read_base_file(root, diff.base, DISPUTES_FILE), recorded.disputes
    )
    rules_changes = [*rules_changes, *answers_added, *disputes_confirmed]
    test_paths = _test_paths(root, diff.base, config)
    tamper_findings = tamper.check(diff, root, test_paths=test_paths)
    try:
        rule_map = collect_rule_map(root)
    except CollectionError as exc:
        rule_map = {}
        findings = [*tamper_findings, *rules_changes, collection_failed(exc.output)]
        suite_broken = True
    else:
        findings = [*tamper_findings, *rules_changes, *rule_findings(head_rules, rule_map)]
        suite_broken = False
    return StaticChecks(
        diff=diff,
        rules=head_rules,
        rule_map=rule_map,
        findings=findings,
        suite_broken=suite_broken,
        ledger=recorded,
    )


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
