"""``mutineer init --agent claude|codex``: installs the skill and merges the Stop hook"""

from __future__ import annotations

import json
import tomllib
from importlib.resources import files
from pathlib import Path
from typing import Any

import pytest

from mutineer.cli import main

SKILL = files("mutineer").joinpath("skill", "SKILL.md").read_bytes()
SKILL_PATH = {
    "claude": ".claude/skills/mutineer/SKILL.md",
    "codex": ".agents/skills/mutineer/SKILL.md",
}
HOOKS_PATH = {"claude": ".claude/settings.json", "codex": ".codex/hooks.json"}
AGENTS = ("claude", "codex")


def _entry(command: str) -> dict[str, Any]:
    return {"hooks": [{"type": "command", "command": command, "timeout": 600}]}


def _snapshot(root: Path) -> dict[Path, bytes]:
    return {p: p.read_bytes() for p in root.rglob("*") if p.is_file() and ".git" not in p.parts}


@pytest.fixture
def repo(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo.path)
    return git_repo.path


@pytest.mark.parametrize("agent", AGENTS)
def test_fresh_repo_gets_skill_hook_and_init_files(repo, agent, capsys):
    assert main(["init", "--agent", agent]) == 0
    assert (repo / SKILL_PATH[agent]).read_bytes() == SKILL
    hooks = json.loads((repo / HOOKS_PATH[agent]).read_text())
    assert hooks == {"hooks": {"Stop": [_entry(f"mutineer hook stop --agent {agent}")]}}
    assert (repo / HOOKS_PATH[agent]).read_text().endswith("}\n")
    assert (repo / ".mutineer/rules.toml").exists()
    assert "mutants/" in (repo / ".gitignore").read_text()
    out = capsys.readouterr().out
    assert f"Wrote {SKILL_PATH[agent]}." in out
    assert f"Wrote {HOOKS_PATH[agent]}." in out


def test_codex_reports_the_trust_note(repo, capsys):
    assert main(["init", "--agent", "codex"]) == 0
    out = capsys.readouterr().out
    assert "trusted" in out
    assert "/hooks" in out


def test_claude_does_not_report_the_codex_note(repo, capsys):
    assert main(["init", "--agent", "claude"]) == 0
    assert "/hooks" not in capsys.readouterr().out


@pytest.mark.parametrize("agent", AGENTS)
def test_existing_config_keeps_every_other_key_event_and_hook(repo, agent):
    other_stop = _entry("./my-stop.sh")
    existing = {
        "model": "x",
        "hooks": {
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "lint"}]}],
            "Stop": [other_stop],
            "SessionStart": [{"hooks": [{"type": "command", "command": "hi"}]}],
        },
        "permissions": {"allow": ["Bash(ls)"]},
    }
    path = repo / HOOKS_PATH[agent]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(existing))
    assert main(["init", "--agent", agent]) == 0
    merged = json.loads(path.read_text())
    assert list(merged) == ["model", "hooks", "permissions"]
    assert list(merged["hooks"]) == ["PreToolUse", "Stop", "SessionStart"]
    assert merged["hooks"]["Stop"] == [other_stop, _entry(f"mutineer hook stop --agent {agent}")]
    assert {k: v for k, v in merged.items() if k != "hooks"} == {
        "model": "x",
        "permissions": {"allow": ["Bash(ls)"]},
    }
    assert merged["hooks"]["PreToolUse"] == existing["hooks"]["PreToolUse"]
    assert path.read_text() == json.dumps(merged, indent=2) + "\n"


@pytest.mark.parametrize("agent", AGENTS)
def test_second_run_changes_no_byte_and_says_already_present(repo, agent, capsys):
    assert main(["init", "--agent", agent]) == 0
    snapshot = _snapshot(repo)
    capsys.readouterr()
    assert main(["init", "--agent", agent]) == 0
    assert _snapshot(repo) == snapshot
    out = capsys.readouterr().out
    assert f"{HOOKS_PATH[agent]} already has Mutineer's Stop hook." in out
    assert f"{SKILL_PATH[agent]} is up to date." in out


@pytest.mark.parametrize("agent", AGENTS)
def test_any_mutineer_stop_hook_counts_as_present(repo, agent):
    path = repo / HOOKS_PATH[agent]
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"hooks": {"Stop": [_entry("/opt/bin/mutineer hook stop")]}}))
    before = path.read_bytes()
    assert main(["init", "--agent", agent]) == 0
    assert path.read_bytes() == before


@pytest.mark.parametrize("agent", AGENTS)
def test_uv_lock_makes_the_hook_use_uv_run(repo, agent):
    (repo / "uv.lock").write_text("")
    assert main(["init", "--agent", agent]) == 0
    stop = json.loads((repo / HOOKS_PATH[agent]).read_text())["hooks"]["Stop"]
    assert stop == [_entry(f"uv run --no-sync mutineer hook stop --agent {agent}")]


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("content", ["{not json", "[]", '{"hooks": []}', '{"hooks": {"Stop": {}}}'])
def test_unmergeable_config_is_left_unchanged_and_exits_4(repo, agent, content, capsys):
    path = repo / HOOKS_PATH[agent]
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert main(["init", "--agent", agent]) == 4
    assert path.read_text() == content
    # the other files are still written
    assert (repo / SKILL_PATH[agent]).read_bytes() == SKILL
    assert (repo / ".mutineer/rules.toml").exists()
    captured = capsys.readouterr()
    assert f"Left {HOOKS_PATH[agent]} unchanged" in captured.err
    assert f"mutineer hook stop --agent {agent}" in captured.err


def test_failure_for_one_agent_still_installs_the_other(repo):
    (repo / ".claude").mkdir()
    (repo / ".claude/settings.json").write_text("{")
    assert main(["init", "--agent", "claude", "--agent", "codex"]) == 4
    assert (repo / ".claude/settings.json").read_text() == "{"
    assert (repo / SKILL_PATH["codex"]).exists()
    assert (repo / HOOKS_PATH["codex"]).exists()


@pytest.mark.parametrize("agent", AGENTS)
def test_different_skill_is_left_unchanged(repo, agent, capsys):
    path = repo / SKILL_PATH[agent]
    path.parent.mkdir(parents=True)
    path.write_text("my own skill\n")
    assert main(["init", "--agent", agent]) == 0
    assert path.read_text() == "my own skill\n"
    out = capsys.readouterr().out
    assert f"{SKILL_PATH[agent]} differs from this Mutineer version" in out
    assert f"mutineer init --agent {agent}" in out


def test_repeated_agent_installs_both(repo):
    assert main(["init", "--agent", "claude", "--agent", "codex"]) == 0
    for agent in AGENTS:
        assert (repo / SKILL_PATH[agent]).read_bytes() == SKILL
        assert (repo / HOOKS_PATH[agent]).exists()


def test_codex_with_hooks_table_in_config_toml_appends_toml(repo, capsys):
    config = repo / ".codex/config.toml"
    config.parent.mkdir()
    original = (
        'model = "o3"\n\n[hooks]\n\n[[hooks.Stop]]\n\n[[hooks.Stop.hooks]]\n'
        'type = "command"\ncommand = "./my-stop.sh"\n\n[profiles.fast]\nmodel = "mini"\n'
    )
    config.write_text(original)
    assert main(["init", "--agent", "codex"]) == 0
    text = config.read_text()
    assert text.startswith(original)
    data = tomllib.loads(text)
    assert data["hooks"]["Stop"] == [
        {"hooks": [{"type": "command", "command": "./my-stop.sh"}]},
        _entry("mutineer hook stop --agent codex"),
    ]
    assert data["profiles"] == {"fast": {"model": "mini"}}
    assert not (repo / ".codex/hooks.json").exists()
    assert "Appended Mutineer's Stop hook to .codex/config.toml." in capsys.readouterr().out
    # second run: no byte changes
    snapshot = _snapshot(repo)
    assert main(["init", "--agent", "codex"]) == 0
    assert _snapshot(repo) == snapshot


def test_codex_config_toml_without_hooks_uses_json(repo):
    config = repo / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text('model = "o3"\n')
    assert main(["init", "--agent", "codex"]) == 0
    assert config.read_text() == 'model = "o3"\n'
    stop = json.loads((repo / ".codex/hooks.json").read_text())["hooks"]["Stop"]
    assert stop == [_entry("mutineer hook stop --agent codex")]


@pytest.mark.parametrize(
    "content",
    [
        "hooks = {}\n",  # an inline table can't take [[hooks.Stop]]
        "[hooks]\nStop = [{ hooks = [] }]\n",  # nor a static array
        "not toml = = \n",
    ],
)
def test_codex_toml_that_cant_take_the_hook_is_left_unchanged(repo, content, capsys):
    config = repo / ".codex/config.toml"
    config.parent.mkdir()
    config.write_text(content)
    assert main(["init", "--agent", "codex"]) == 4
    assert config.read_text() == content
    assert not (repo / ".codex/hooks.json").exists()
    assert (repo / SKILL_PATH["codex"]).exists()
    err = capsys.readouterr().err
    assert "Left .codex/config.toml unchanged" in err
    assert "[[hooks.Stop.hooks]]" in err


def test_codex_hook_already_in_json_is_not_added_to_toml(repo):
    (repo / ".codex").mkdir()
    (repo / ".codex/hooks.json").write_text(
        json.dumps({"hooks": {"Stop": [_entry("mutineer hook stop --agent codex")]}})
    )
    (repo / ".codex/config.toml").write_text("[hooks]\n")
    hooks_json = (repo / ".codex/hooks.json").read_bytes()
    assert main(["init", "--agent", "codex"]) == 0
    assert (repo / ".codex/hooks.json").read_bytes() == hooks_json
    assert (repo / ".codex/config.toml").read_text() == "[hooks]\n"


def test_init_without_agent_writes_only_rules_and_gitignore(repo):
    assert main(["init"]) == 0
    written = {p.relative_to(repo).as_posix() for p in _snapshot(repo)}
    assert written == {".mutineer/rules.toml", ".gitignore"}


def test_unknown_agent_is_a_usage_error(repo):
    assert main(["init", "--agent", "cursor"]) == 4
    assert not (repo / ".mutineer").exists()
