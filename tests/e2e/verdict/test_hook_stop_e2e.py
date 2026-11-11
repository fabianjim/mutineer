"""``mutineer hook stop`` end to end on the shop fixture (slow: runs mutmut)

Each case runs the real console script the way a harness does: a recorded Stop payload
on stdin, its ``cwd`` set to a private fixture copy, and the process started somewhere
else so the hook has to find the repo from the payload. Every case checks the contract
with the harness: exit 0 and exactly one JSON object on stdout.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

pytestmark = [pytest.mark.slow, pytest.mark.parametrize("agent", ["claude", "codex"])]

CONSOLE_SCRIPT = Path(sys.executable).parent / "mutineer"
LAST_RUN = ".mutineer/last-run.json"
SENTINEL = "mutants/sentinel.txt"
WORK_PREFIX = "Mutineer found work for you before you stop:"
FAILING_TEST = "def test_broken():\n    assert 1 == 2\n"


def payload(agent: str, cwd: Path, *, active: bool = False) -> dict[str, Any]:
    """A Stop payload as each harness records it; the hook reads only ``cwd`` and the flag."""
    common = {
        "session_id": "0b5e8c1a-4f2d-4e7a-9c1b-6a2f3d4e5f60",
        "transcript_path": str(cwd.parent / "transcript.jsonl"),
        "cwd": str(cwd),
        "hook_event_name": "Stop",
        "stop_hook_active": active,
    }
    if agent == "codex":
        return {**common, "turn_id": "turn-1", "model": "gpt-5-codex"}
    return {**common, "permission_mode": "default"}


def hook(agent: str, data: dict[str, Any], elsewhere: Path) -> dict[str, Any]:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    elsewhere.mkdir(exist_ok=True)
    proc = subprocess.run(
        [str(CONSOLE_SCRIPT), "hook", "stop", "--agent", agent],
        input=json.dumps(data),
        cwd=elsewhere,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.returncode == 0, proc.stderr
    # one JSON object, nothing else: json.loads rejects trailing output
    answer = json.loads(proc.stdout)
    assert isinstance(answer, dict), proc.stdout
    assert proc.stdout.count("\n") == 1, proc.stdout
    return answer


def put_sentinel(root: Path) -> Path:
    path = root / SENTINEL
    path.parent.mkdir(exist_ok=True)
    path.write_text("left by the test\n")
    return path


def test_agent_branch_lets_the_agent_stop_with_a_pass_message(
    agent: str, shop_copy: Any, tmp_path: Path
) -> None:
    shop_copy.git("checkout", "-q", "agent")
    answer = hook(agent, payload(agent, shop_copy.path), tmp_path / "elsewhere")
    assert "decision" not in answer
    assert answer["systemMessage"].startswith("Mutineer: pass")
    assert "for a human" in answer["systemMessage"]


def test_agent_gap_blocks_with_the_two_survivors_then_lets_an_unchanged_tree_stop(
    agent: str, shop_copy: Any, tmp_path: Path
) -> None:
    root = shop_copy.path
    shop_copy.git("checkout", "-q", "agent-gap")
    answer = hook(agent, payload(agent, root), tmp_path / "elsewhere")
    assert answer["decision"] == "block"
    first_line, _, body = answer["reason"].partition("\n")
    assert first_line == WORK_PREFIX
    report = json.loads(body)
    assert (report["status"], report["next_command"]) == (
        "warn",
        "mutineer recheck --format agent",
    )
    survivors = [f for f in report["findings"] if f["kind"] == "survivor_rule"]
    assert [(f["location"]["line"], f["rules"]) for f in survivors] == [
        (8, ["SHIP-003"]),
        (10, ["SHIP-003"]),
    ]

    # the agent stops again without changing anything: the stored verdict matches, no run
    sentinel = put_sentinel(root)
    stored = (root / LAST_RUN).read_bytes()
    answer = hook(agent, payload(agent, root), tmp_path / "elsewhere")
    assert answer == {}
    assert sentinel.exists()
    assert (root / LAST_RUN).read_bytes() == stored


def test_agent_tamper_lets_the_agent_stop_and_tells_the_human_it_blocks(
    agent: str, shop_copy: Any, tmp_path: Path
) -> None:
    shop_copy.git("checkout", "-q", "agent-tamper")
    answer = hook(agent, payload(agent, shop_copy.path), tmp_path / "elsewhere")
    assert "decision" not in answer
    assert answer["systemMessage"].startswith("Mutineer: block")


def test_a_broken_suite_sends_the_agent_back(agent: str, shop_copy: Any, tmp_path: Path) -> None:
    shop_copy.write("tests/test_broken.py", FAILING_TEST)
    answer = hook(agent, payload(agent, shop_copy.path), tmp_path / "elsewhere")
    assert answer["decision"] == "block"
    report = json.loads(answer["reason"].partition("\n")[2])
    assert (report["status"], report["next_command"]) == ("block", "mutineer run --format agent")


def test_stop_hook_active_lets_the_agent_stop_without_running(
    agent: str, shop_copy: Any, tmp_path: Path
) -> None:
    root = shop_copy.path
    shop_copy.git("checkout", "-q", "agent-gap")
    answer = hook(agent, payload(agent, root, active=True), tmp_path / "elsewhere")
    assert answer == {}
    assert not (root / LAST_RUN).exists()
    assert not (root / "mutants").exists()
