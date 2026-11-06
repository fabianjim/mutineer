"""``mutineer recheck``: rerun only the agent's surviving mutants from the last run."""

from __future__ import annotations

import time
from pathlib import Path

import typer

from mutineer import gitdiff, state
from mutineer.cli.run import OutputFormat, emit, make_engine, static_checks
from mutineer.config import load_config
from mutineer.errors import MutineerError, NeedsHumanError
from mutineer.models import EngineRun
from mutineer.triage import triage
from mutineer.verdict import decide, exit_code


def recheck(
    format: OutputFormat = typer.Option(OutputFormat.TERMINAL, "--format", help="Output format."),
    output: Path | None = typer.Option(None, "--output", help="Write the report to this file."),
    max_rounds: int | None = typer.Option(
        None, "--max-rounds", min=1, help="Round cap. Default: [tool.mutineer] max_rounds (3)."
    ),
) -> int:
    """Rerun only the agent's surviving mutants from the last run."""
    started = time.monotonic()
    root = Path.cwd()
    config = load_config(root)
    last = state.load(root)
    if last is None:
        raise MutineerError(f"no {state.STATE_FILE}; run `mutineer run` first")
    if gitdiff.merge_base(root, last.base_ref) != last.base:
        raise MutineerError(f"base moved since the last run ({last.base_ref}); run `mutineer run`")

    if not last.survivor_names or last.engine_run is None:
        # nothing for the agent to recheck: show the stored verdict again
        emit(last.verdict, format, output)
        return exit_code(last.verdict)

    cap = max_rounds or config.max_rounds
    if last.rounds >= cap:
        raise NeedsHumanError(
            f"recheck ran {last.rounds} rounds (cap {cap}) and survivors remain; "
            "a human should review the findings"
        )

    checked = static_checks(root, config, last.base_ref)
    findings = list(checked.findings)
    rule_score = diff_score = None
    suite_failed = checked.suite_broken
    if not suite_failed:
        rerun = make_engine().rerun(last.survivor_names, checked.rule_map, root)
        merged = merge_rerun(last.engine_run, rerun)
        result = triage(merged, checked.rule_map, checked.rules)
        findings += result.findings
        rule_score, diff_score = result.rule_score, result.diff_score
        suite_failed = rerun.baseline_failed
    verdict = decide(
        findings,
        base=checked.diff.base,
        head=checked.diff.head,
        rule_score=rule_score,
        diff_score=diff_score,
        duration_s=time.monotonic() - started,
    )
    if suite_failed:
        # keep the last good results, so the next recheck retries the same mutants once
        # the suite passes again
        update = {"rounds": last.rounds + 1, "verdict": verdict}
    else:
        update = {
            "rounds": last.rounds + 1,
            "survivor_names": result.survivor_names,
            "engine_run": merged,
            "verdict": verdict,
        }
    state.save(root, last.model_copy(update=update))
    emit(verdict, format, output)
    return exit_code(verdict)


def merge_rerun(stored: EngineRun, rerun: EngineRun) -> EngineRun:
    """``stored`` with the rule-pass results of the rerun mutants replaced.

    Everything else (functions, unmeasured lines, all-pass results, the test map) stays,
    so ``rule_score`` keeps its full denominator. A failing baseline in the rerun is
    carried over, so triage reports it.
    """
    fresh = {r.name: r for r in rerun.results if r.run == "rule"}
    results = [r for r in stored.results if not (r.run == "rule" and r.name in fresh)]
    results += fresh.values()
    return stored.model_copy(
        update={
            "results": results,
            "baseline_failed": stored.baseline_failed or rerun.baseline_failed,
            "baseline_output": rerun.baseline_output
            if rerun.baseline_failed
            else stored.baseline_output,
            "duration_s": round(stored.duration_s + rerun.duration_s, 3),
        }
    )
