"""``mutineer init --agent`` end to end on a copy of the shop fixture (slow)

``init`` installs the packaged skill and merges the Stop hook into the agent's project
config, keeping other hooks, and a second run changes nothing. The last test runs the
hook command ``init`` wrote, through a shell as a harness does.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from mutineer.cli import main

pytestmark = pytest.mark.slow

SKILL = files("mutineer").joinpath("skill", "SKILL.md").read_bytes()
SKILL_PATHS = {
    "claude": ".claude/skills/mutineer/SKILL.md",
    "codex": ".agents/skills/mutineer/SKILL.md",
}
CONFIG_PATHS = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json"}
OTHER_HOOK = {"hooks": [{"type": "command", "command": "./scripts/lint.sh"}]}
OTHER_SETTINGS = {
    "permissions": {"allow": ["Bash(uv run pytest:*)"]},
    "hooks": {
        "PreToolUse": [
            {"matcher": "Bash", "hooks": [{"type": "command", "command": "./guard.sh"}]}
        ],
        "Stop": [OTHER_HOOK],
    },
}
CODEX_TOML = """\
model = "gpt-5-codex"

[[hooks.Stop]]

[[hooks.Stop.hooks]]
type = "command"
command = "./scripts/lint.sh"
"""


def mutineer_hook(agent: str) -> dict[str, Any]:
    command = f"mutineer hook stop --agent {agent}"
    return {"hooks": [{"type": "command", "command": command, "timeout": 600}]}


def init(root: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.chdir(root)
    return main(["init", *args])


def snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes()
        for p in sorted(root.rglob("*"))
        if p.is_file() and ".git" not in p.relative_to(root).parts
    }


def changed(before: dict[str, bytes], after: dict[str, bytes]) -> set[str]:
    return {p for p in before.keys() | after.keys() if before.get(p) != after.get(p)}


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_init_agent_on_a_fresh_repo_writes_the_skill_and_the_hook(
    agent: str, shop_copy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = shop_copy.path
    before = snapshot(root)
    assert init(root, monkeypatch, "--agent", agent) == 0
    after = snapshot(root)
    # the fixture already has rules.toml and ignores mutants/, so .gitignore gains last-run.json
    assert changed(before, after) == {
        ".gitignore",
        SKILL_PATHS[agent],
        CONFIG_PATHS[agent],
    }
    assert after[SKILL_PATHS[agent]] == SKILL
    assert json.loads(after[CONFIG_PATHS[agent]]) == {"hooks": {"Stop": [mutineer_hook(agent)]}}

    assert init(root, monkeypatch, "--agent", agent) == 0
    assert snapshot(root) == after


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_init_agent_keeps_the_hooks_already_configured(
    agent: str, shop_copy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = shop_copy.path
    shop_copy.write(CONFIG_PATHS[agent], json.dumps(OTHER_SETTINGS, indent=2) + "\n")
    assert init(root, monkeypatch, "--agent", agent) == 0
    merged = json.loads((root / CONFIG_PATHS[agent]).read_text())
    assert merged == {
        **OTHER_SETTINGS,
        "hooks": {**OTHER_SETTINGS["hooks"], "Stop": [OTHER_HOOK, mutineer_hook(agent)]},
    }

    after = snapshot(root)
    assert init(root, monkeypatch, "--agent", agent) == 0
    assert snapshot(root) == after


def test_init_agent_codex_appends_to_a_config_toml_that_has_hooks(
    shop_copy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = shop_copy.path
    shop_copy.write(".codex/config.toml", CODEX_TOML)
    assert init(root, monkeypatch, "--agent", "codex") == 0
    text = (root / ".codex/config.toml").read_text()
    assert text.startswith(CODEX_TOML)
    config = tomllib.loads(text)
    assert config["model"] == "gpt-5-codex"
    assert config["hooks"]["Stop"] == [OTHER_HOOK, mutineer_hook("codex")]
    assert not (root / ".codex/hooks.json").exists()

    after = snapshot(root)
    assert init(root, monkeypatch, "--agent", "codex") == 0
    assert snapshot(root) == after


@pytest.mark.parametrize("agent", ["claude", "codex"])
def test_the_hook_init_wrote_runs_and_lets_a_clean_tree_stop(
    agent: str, shop_copy: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = shop_copy.path
    assert init(root, monkeypatch, "--agent", agent) == 0
    (group,) = json.loads((root / CONFIG_PATHS[agent]).read_text())["hooks"]["Stop"]
    (entry,) = group["hooks"]

    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["PATH"] = f"{Path(sys.executable).parent}{os.pathsep}{env.get('PATH', '')}"
    proc = subprocess.run(
        entry["command"],
        shell=True,
        input=json.dumps({"cwd": str(root), "hook_event_name": "Stop", "stop_hook_active": False}),
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=entry["timeout"],
    )
    assert proc.returncode == 0, proc.stderr
    answer = json.loads(proc.stdout)
    assert "decision" not in answer
    assert answer["systemMessage"].startswith("Mutineer: pass"), answer
