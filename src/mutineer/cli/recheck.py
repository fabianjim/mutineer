"""``mutineer recheck``: rerun only the agent's surviving mutants from the last run.

Mutants an open or confirmed dispute matches are not rerun. Even with nothing to rerun,
the stored engine run is triaged again with the current ledger, so disputes filed or
resolved since the last run show up; that costs no round.
"""

from __future__ import annotations

import time
from pathlib import Path

import typer

from mutineer import gitdiff, state
from mutineer.cli.run import OutputFormat, emit, make_engine, static_checks
from mutineer.config import load_config
from mutineer.errors import MutineerError, NeedsHumanError
from mutineer.ledger import Ledger, load_disputes
from mutineer.models import EngineRun
from mutineer.seams import seam_reach
from mutineer.triage import disputed_names, triage
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

    if last.engine_run is None:
        # mutation didn't run: show the stored verdict again
        emit(last.verdict, format, output)
        return exit_code(last.verdict)

    # mutants an open or confirmed dispute matches wait for a human, so they aren't rerun
    disputed = disputed_names(last.engine_run, Ledger(disputes=load_disputes(root)))
    names = [n for n in last.survivor_names if n not in disputed]
    cap = max_rounds or config.max_rounds
    if names and last.rounds >= cap:
        raise NeedsHumanError(
            f"recheck ran {last.rounds} rounds (cap {cap}) and survivors remain; "
            "a human should review the findings"
        )
    # only a rerun costs a round; re-triaging the stored run with the current ledger is free
    rounds = last.rounds + 1 if names else last.rounds

    checked = static_checks(root, config, last.base_ref)
    findings = list(checked.findings)
    rule_score = diff_score = None
    suite_failed = checked.suite_broken
    if not suite_failed:
        merged = last.engine_run
        if names:
            rerun = make_engine().rerun(names, checked.rule_map, root)
            merged = merge_rerun(merged, rerun)
            suite_failed = rerun.baseline_failed
        result = triage(
            merged,
            checked.rule_map,
            checked.rules,
            ledger=checked.ledger,
            seam_reached=seam_reach(root),
        )
        findings += result.findings
        rule_score, diff_score = result.rule_score, result.diff_score
    verdict = decide(
        findings,
        base=checked.diff.base,
        head=checked.diff.head,
        rule_score=rule_score,
        diff_score=diff_score,
        duration_s=time.monotonic() - started,
    )
    # a merged verdict can differ from a fresh run's, so the next run is always real
    update: dict[str, object] = {"rounds": rounds, "verdict": verdict, "fingerprint": None}
    if not suite_failed:
        # on a failed suite, keep the last good results, so the next recheck retries the
        # same mutants once the suite passes again
        update |= {
            "survivor_names": result.survivor_names,
            "engine_run": merged,
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
