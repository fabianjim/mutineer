"""Install Mutineer's skill and Stop hook into a coding agent's project config.

Every step is idempotent and never overwrites: a missing file is created, an existing
SKILL.md is left as it is, and a config file gets Mutineer's Stop hook merged in only when
no Stop hook already runs ``mutineer hook stop``. A config file that can't be read or
merged is left unchanged, and its ``Step`` carries the snippet to add by hand.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

HOOK_MARKER = "mutineer hook stop"
"""A Stop hook whose command contains this is already Mutineer's."""
HOOK_TIMEOUT = 600
AGENTS = ("claude", "codex")

_SKILL_DIRS = {"claude": ".claude/skills/mutineer", "codex": ".agents/skills/mutineer"}
_CLAUDE_SETTINGS = ".claude/settings.json"
_CODEX_HOOKS_JSON = ".codex/hooks.json"
_CODEX_CONFIG_TOML = ".codex/config.toml"

CODEX_TRUST_NOTE = (
    "Codex loads project hooks only when the project's `.codex/` layer is trusted, and asks "
    "you to review the hook once (`/hooks`)."
)


@dataclass(frozen=True)
class Step:
    """What happened to one file: a line for the report, and whether it failed."""

    path: str
    message: str
    failed: bool = False


def hook_command(root: Path, agent: str) -> str:
    """The Stop hook command; ``uv run --no-sync`` finds the venv when the project uses uv."""
    prefix = "uv run --no-sync mutineer" if (root / "uv.lock").is_file() else "mutineer"
    return f"{prefix} hook stop --agent {agent}"


def install(root: Path, agent: str) -> list[Step]:
    """Install the skill and the Stop hook for ``agent`` under ``root``."""
    steps = [install_skill(root, f"{_SKILL_DIRS[agent]}/SKILL.md", agent)]
    command = hook_command(root, agent)
    if agent == "claude":
        steps.append(merge_json_hook(root, _CLAUDE_SETTINGS, command))
    else:
        steps.append(_install_codex_hook(root, command))
    return steps


def install_skill(root: Path, rel: str, agent: str) -> Step:
    packaged = files("mutineer").joinpath("skill", "SKILL.md").read_bytes()
    path = root / rel
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(packaged)
        return Step(rel, f"Wrote {rel}.")
    if path.read_bytes() == packaged:
        return Step(rel, f"{rel} is up to date.")
    return Step(
        rel,
        f"{rel} differs from this Mutineer version; delete it and rerun "
        f"`mutineer init --agent {agent}` to update.",
    )


def hook_entry(command: str) -> dict[str, Any]:
    """One Stop matcher group running ``command``."""
    return {"hooks": [{"type": "command", "command": command, "timeout": HOOK_TIMEOUT}]}


def json_snippet(command: str) -> str:
    return json.dumps({"hooks": {"Stop": [hook_entry(command)]}}, indent=2)


def toml_snippet(command: str) -> str:
    return (
        "[[hooks.Stop]]\n\n"
        "[[hooks.Stop.hooks]]\n"
        'type = "command"\n'
        f"command = {json.dumps(command)}\n"
        f"timeout = {HOOK_TIMEOUT}\n"
    )


def has_mutineer_hook(stop: Any) -> bool:
    """Whether a Stop list already holds a hook running ``mutineer hook stop``."""
    if not isinstance(stop, list):
        return False
    for group in stop:
        hooks = group.get("hooks") if isinstance(group, dict) else None
        for hook in hooks if isinstance(hooks, list) else ():
            command = hook.get("command") if isinstance(hook, dict) else None
            if isinstance(command, str) and HOOK_MARKER in command:
                return True
    return False


def _left_unchanged(rel: str, why: str, snippet: str) -> Step:
    return Step(rel, f"Left {rel} unchanged: {why}. Add this by hand:\n{snippet}", failed=True)


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """The JSON object in ``path`` (``{}`` when missing), or why it can't be merged."""
    if not path.exists():
        return {}, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        return None, f"it is not valid JSON ({exc})"
    if not isinstance(data, dict):
        return None, "it is not a JSON object"
    hooks = data.get("hooks", {})
    if not isinstance(hooks, dict):
        return None, "`hooks` is not an object"
    if not isinstance(hooks.get("Stop", []), list):
        return None, "`hooks.Stop` is not a list"
    return data, None


def merge_json_hook(root: Path, rel: str, command: str) -> Step:
    """Merge the Stop hook into the JSON file ``rel``, keeping everything else in order."""
    path = root / rel
    data, why = _read_json(path)
    if data is None:
        return _left_unchanged(rel, why or "it can't be read", json_snippet(command))
    hooks = data.setdefault("hooks", {})
    stop = hooks.setdefault("Stop", [])
    if has_mutineer_hook(stop):
        return Step(rel, f"{rel} already has Mutineer's Stop hook.")
    stop.append(hook_entry(command))
    existed = path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return Step(rel, f"Merged Mutineer's Stop hook into {rel}." if existed else f"Wrote {rel}.")


def _install_codex_hook(root: Path, command: str) -> Step:
    # codex warns when one layer defines hooks in both files, so use the one already in use
    config = root / _CODEX_CONFIG_TOML
    toml_data: dict[str, Any] = {}
    if config.exists():
        try:
            toml_data = tomllib.loads(config.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
            why = f"it is not valid TOML ({exc}), so its hooks can't be checked"
            return _left_unchanged(_CODEX_CONFIG_TOML, why, toml_snippet(command))
    if "hooks" not in toml_data:
        return merge_json_hook(root, _CODEX_HOOKS_JSON, command)
    json_data, _ = _read_json(root / _CODEX_HOOKS_JSON)
    if json_data and has_mutineer_hook(json_data.get("hooks", {}).get("Stop")):
        return Step(_CODEX_HOOKS_JSON, f"{_CODEX_HOOKS_JSON} already has Mutineer's Stop hook.")
    return append_toml_hook(root, _CODEX_CONFIG_TOML, command)


def append_toml_hook(root: Path, rel: str, command: str) -> Step:
    """Append the Stop hook to the TOML file ``rel`` as text, keeping it only if it parses."""
    path = root / rel
    text = path.read_text(encoding="utf-8")
    hooks = tomllib.loads(text).get("hooks")
    if isinstance(hooks, dict) and has_mutineer_hook(hooks.get("Stop")):
        return Step(rel, f"{rel} already has Mutineer's Stop hook.")
    separator = "" if not text else "\n" if text.endswith("\n") else "\n\n"
    new_text = text + separator + toml_snippet(command)
    try:
        new_hooks = tomllib.loads(new_text).get("hooks")
    except tomllib.TOMLDecodeError as exc:
        return _left_unchanged(
            rel, f"appending the hook would not parse ({exc})", toml_snippet(command)
        )
    stop = new_hooks.get("Stop") if isinstance(new_hooks, dict) else None
    if not isinstance(stop, list) or not stop or stop[-1] != hook_entry(command):
        return _left_unchanged(rel, "appending the hook would not add it", toml_snippet(command))
    path.write_text(new_text, encoding="utf-8")
    return Step(rel, f"Appended Mutineer's Stop hook to {rel}.")
