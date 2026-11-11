"""``mutineer hook stop``: the Stop hook a coding agent's harness runs before the agent stops.

The harness pipes a JSON object to stdin and reads one JSON object from stdout. The hook
sends the agent back (``"decision": "block"``) only when Mutineer found work the agent can
do: an undisputed ``survivor_rule``, or a broken suite. Everything else lets the agent stop,
with at most a one-line ``systemMessage`` for the human. It always exits 0 and prints
exactly one JSON object, even when Mutineer itself fails, so it never traps the agent.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import typer

from mutineer import gitdiff, state
from mutineer.cli.run import current_fingerprint, judge
from mutineer.config import load_config
from mutineer.errors import MutineerError
from mutineer.ledger import Ledger, load_disputes
from mutineer.models import Audience, Score, Verdict
from mutineer.reporters import agent
from mutineer.triage import disputed_names

WORK_PREFIX = "Mutineer found work for you before you stop:"

app = typer.Typer(
    help="Hooks for coding agents' harnesses. Each prints one JSON object and exits 0.",
    no_args_is_help=True,
)


class Agent(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


@dataclass(frozen=True)
class Answer:
    """What the hook decided: block with ``reason``, or allow with an optional message."""

    block: bool = False
    reason: str | None = None
    """For the agent, when ``block``."""
    message: str | None = None
    """One line for the human, when the agent may stop."""


@app.command(name="stop")
def stop(
    agent_name: Agent = typer.Option(..., "--agent", help="The harness that runs the hook."),
) -> int:
    """Stop hook: send the agent back only when Mutineer found work it can do."""
    raw = sys.stdin.read()
    answer = decide_stop(raw, agent_name)
    typer.echo(json.dumps(FORMATS[agent_name](answer), ensure_ascii=False))
    return 0


def decide_stop(raw: str, agent_name: Agent) -> Answer:
    """The hook's decision for the stdin payload ``raw``. Never raises."""
    try:
        payload = json.loads(raw)
    except ValueError:
        payload = None
    if not isinstance(payload, dict):
        return Answer(message="Mutineer: the Stop hook input is not a JSON object; skipped.")
    if payload.get("stop_hook_active") is True:
        return Answer()
    try:
        with _stdout_to_stderr():
            return _judge_stop(_root(payload, agent_name))
    except MutineerError as exc:
        return Answer(message=f"Mutineer could not run: {exc}")
    except Exception as exc:
        return Answer(message=f"Mutineer failed: {type(exc).__name__}: {exc}")


def _judge_stop(root: Path) -> Answer:
    os.chdir(root)
    config = load_config(root)
    base_ref = gitdiff.resolve_base_ref(root, config.base)
    current = current_fingerprint(root, config, base_ref, no_mutation=False)
    last = _last_state(root)
    if last is not None:
        if last.fingerprint is not None and last.fingerprint == current:
            return Answer()
        if last.rounds >= config.max_rounds and _undisputed_survivors(root, last):
            return Answer(
                message=f"Mutineer: recheck ran {last.rounds} rounds (cap {config.max_rounds}) "
                "and survivors remain; a human should review the findings."
            )
    verdict = judge(root, None, no_mutation=False, no_reuse=False)
    report = agent.report(verdict)
    if report.next_command is not None:
        return Answer(block=True, reason=f"{WORK_PREFIX}\n{agent.render(verdict).rstrip()}")
    return Answer(message=summary(verdict))


def summary(verdict: Verdict) -> str:
    """One line for the human: status, scores, and how many findings wait for a human."""
    scores = [
        f"{name} {_score(score)}"
        for name, score in (("rule score", verdict.rule_score), ("diff score", verdict.diff_score))
        if score is not None
    ]
    head = f"Mutineer: {verdict.status}" + (f" ({', '.join(scores)})" if scores else "")
    human = sum(1 for f in verdict.findings if f.audience == Audience.HUMAN)
    noun = "finding" if human == 1 else "findings"
    return f"{head}; {human} {noun} for a human. See `mutineer run`."


def _score(score: Score) -> str:
    return f"{score.killed}/{score.total}"


def _root(payload: dict[str, Any], agent_name: Agent) -> Path:
    """The git top level of the session directory, else the current directory."""
    starts: list[str] = []
    if agent_name == Agent.CLAUDE and os.environ.get("CLAUDE_PROJECT_DIR"):
        starts.append(os.environ["CLAUDE_PROJECT_DIR"])
    if isinstance(payload.get("cwd"), str) and payload["cwd"]:
        starts.append(payload["cwd"])
    for start in starts:
        top = _toplevel(Path(start))
        if top is not None:
            return top
    return Path.cwd()


def _toplevel(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
    except OSError:
        return None
    out = proc.stdout.strip()
    return Path(out) if proc.returncode == 0 and out else None


def _last_state(root: Path) -> state.State | None:
    try:
        return state.load(root)
    except MutineerError:
        return None


def _undisputed_survivors(root: Path, last: state.State) -> bool:
    """Whether ``recheck`` would rerun survivors: the condition its round cap applies to."""
    if last.engine_run is None:
        return False
    disputed = disputed_names(last.engine_run, Ledger(disputes=load_disputes(root)))
    return any(n not in disputed for n in last.survivor_names)


@contextlib.contextmanager
def _stdout_to_stderr() -> Iterator[None]:
    """Send everything printed meanwhile, by Python or by subprocesses, to stderr.

    The harness reads stdout as the hook's one JSON answer.
    """
    sys.stdout.flush()
    try:
        saved = os.dup(1)
    except OSError:
        saved = None
    try:
        if saved is not None:
            os.dup2(2, 1)
        with contextlib.redirect_stdout(sys.stderr):
            yield
    finally:
        sys.stderr.flush()
        if saved is not None:
            os.dup2(saved, 1)
            os.close(saved)


def _claude(answer: Answer) -> dict[str, Any]:
    return _common(answer)


def _codex(answer: Answer) -> dict[str, Any]:
    return _common(answer)


def _common(answer: Answer) -> dict[str, Any]:
    if answer.block:
        return {"decision": "block", "reason": answer.reason}
    return {"systemMessage": answer.message} if answer.message else {}


# one function per agent, so the formats can diverge
FORMATS: dict[Agent, Callable[[Answer], dict[str, Any]]] = {
    Agent.CLAUDE: _claude,
    Agent.CODEX: _codex,
}
