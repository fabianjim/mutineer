"""``mutineer hook stop`` through ``cli.main``, with recorded Stop payloads and stand-in engines

Every case checks the hook's contract with the harness: exit 0 and exactly one JSON
object on stdout.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

import mutineer.cli.run as run_cmd
from mutineer import state
from mutineer.cli import main
from mutineer.models import (
    Diff,
    EngineRun,
    FunctionRef,
    MutantResult,
    MutantStatus,
    RuleMap,
)

FN = "pkg.mod.x_double"
RULE_TEST = "tests/test_mod.py::test_double"

PYPROJECT = """\
[project]
name = "pkg"
version = "0"

[tool.mutmut]
source_paths = ["src/pkg"]
pytest_add_cli_args_test_selection = ["tests"]

[tool.pytest.ini_options]
pythonpath = ["src"]
"""
RULES = """\
version = 1

[[rule]]
id = "DBL-001"
statement = "double doubles"
seam = "pkg.mod.double"
"""
TEST = """\
import pytest
from pkg.mod import double


@pytest.mark.rule("DBL-001")
def test_double():
    assert double(2) == 4
"""
UNKNOWN_RULE_TEST = """\
import pytest


@pytest.mark.rule("NOPE-001")
def test_other():
    assert True
"""
AGENTS = ["claude", "codex"]


def payload(agent: str, cwd: Path, *, active: bool = False) -> str:
    """A Stop payload as each harness records it."""
    if agent == "claude":
        data: dict[str, Any] = {
            "session_id": "abc123",
            "transcript_path": "/home/u/.claude/projects/p/abc123.jsonl",
            "cwd": str(cwd),
            "permission_mode": "default",
            "hook_event_name": "Stop",
            "stop_hook_active": active,
        }
    else:
        data = {
            "session_id": "0199a213-81c0-7800-8aa1-bbab2a035a53",
            "transcript_path": "/home/u/.codex/sessions/rollout.jsonl",
            "cwd": str(cwd),
            "hook_event_name": "Stop",
            "model": "gpt-5-codex",
            "turn_id": "1",
            "stop_hook_active": active,
            "last_assistant_message": "Done.",
        }
    return json.dumps(data)


def _function() -> FunctionRef:
    return FunctionRef(
        mutmut_name=FN, file="src/pkg/mod.py", qualname="double", start_line=1, end_line=2
    )


def _result(n: int, run: str, status: str) -> MutantResult:
    shown = status != "killed"
    return MutantResult(
        name=f"{FN}__mutmut_{n}",
        function=FN,
        run=run,  # type: ignore[arg-type]
        status=MutantStatus(status),
        line=2 if shown else None,
        original="return x * 2" if shown else None,
        mutated=f"return x * {n + 2}" if shown else None,
    )


class Engine:
    """A stand-in engine: ``statuses`` per mutant as (all pass, rule pass)."""

    def __init__(self, statuses: list[tuple[str, str]], tests: list[str]) -> None:
        self.statuses = statuses
        self.tests = tests
        self.runs = 0

    def run(self, diff: Diff, rule_map: RuleMap, root: Path) -> EngineRun:
        self.runs += 1
        results = [
            _result(n, kind, status)
            for n, pair in enumerate(self.statuses, 1)
            for kind, status in zip(("all", "rule"), pair, strict=True)
        ]
        return EngineRun(
            baseline_failed=False,
            functions=[_function()],
            unmeasured=[],
            results=results,
            tests_by_function={FN: self.tests},
            duration_s=0.1,
        )


def survivor_engine() -> Engine:
    return Engine([("survived", "survived"), ("killed", "killed")], [RULE_TEST])


def killing_engine() -> Engine:
    return Engine([("killed", "killed")], [RULE_TEST])


def unowned_engine() -> Engine:
    return Engine([("survived", "survived")], ["tests/test_mod.py::test_other"])


def no_engine() -> Engine:
    raise AssertionError("the engine must not be constructed")


@pytest.fixture
def project(git_repo: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    git_repo.write("pyproject.toml", PYPROJECT)
    git_repo.write(".mutineer/rules.toml", RULES)
    git_repo.write(".gitignore", "mutants/\n.mutineer/last-run.json\n__pycache__/\n")
    git_repo.write("src/pkg/__init__.py", "")
    git_repo.write("src/pkg/mod.py", "def double(x):\n    return x + x\n")
    git_repo.write("tests/test_mod.py", TEST)
    git_repo.commit("base")
    git_repo.git("checkout", "-q", "-b", "work")
    git_repo.write("src/pkg/mod.py", "def double(x):\n    return x * 2\n")
    monkeypatch.chdir(git_repo.path)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return git_repo


def use_engine(monkeypatch: pytest.MonkeyPatch, factory: Any) -> None:
    monkeypatch.setattr(run_cmd, "make_engine", factory)


def hook(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    agent: str,
    stdin: str,
) -> dict[str, Any]:
    """Run the hook; assert exit 0 and one JSON object on stdout, and return it."""
    capsys.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    assert main(["hook", "stop", "--agent", agent]) == 0
    out = capsys.readouterr().out
    assert out.endswith("\n") and out.count("\n") == 1
    answer = json.loads(out)
    assert isinstance(answer, dict)
    return answer


def blocked_report(answer: dict[str, Any]) -> dict[str, Any]:
    assert answer["decision"] == "block"
    first, rest = answer["reason"].split("\n", 1)
    assert first == "Mutineer found work for you before you stop:"
    return json.loads(rest)  # type: ignore[no-any-return]


##
# the payload


@pytest.mark.parametrize("agent", AGENTS)
@pytest.mark.parametrize("stdin", ["", "not json", "[1, 2]", '"text"'])
def test_input_that_is_not_a_json_object_lets_the_agent_stop(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str, stdin: str
) -> None:
    use_engine(monkeypatch, no_engine)
    answer = hook(monkeypatch, capsys, agent, stdin)
    assert "decision" not in answer
    assert "not a JSON object" in answer["systemMessage"]


@pytest.mark.parametrize("agent", AGENTS)
def test_a_turn_already_continued_by_the_hook_may_stop(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, no_engine)
    answer = hook(monkeypatch, capsys, agent, payload(agent, project.path, active=True))
    assert answer == {}
    assert state.load(project.path) is None


##
# work for the agent


@pytest.mark.parametrize("agent", AGENTS)
def test_rule_survivors_send_the_agent_back(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, survivor_engine)
    report = blocked_report(hook(monkeypatch, capsys, agent, payload(agent, project.path)))
    assert report["next_command"] == "mutineer recheck --format agent"
    assert report["status"] == "warn"
    assert [f["kind"] for f in report["findings"]] == ["survivor_rule"]
    assert "instruction" in report
    saved = state.load(project.path)
    assert saved is not None and saved.survivor_names == [f"{FN}__mutmut_1"]


@pytest.mark.parametrize("agent", AGENTS)
def test_a_broken_suite_sends_the_agent_back(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, no_engine)
    project.write("tests/test_broken.py", "def test_broken(:\n    pass\n")
    report = blocked_report(hook(monkeypatch, capsys, agent, payload(agent, project.path)))
    assert report["status"] == "block"
    assert report["next_command"] == "mutineer run --format agent"


##
# the agent may stop


@pytest.mark.parametrize("agent", AGENTS)
def test_an_unchanged_tree_runs_nothing(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, survivor_engine)
    blocked_report(hook(monkeypatch, capsys, agent, payload(agent, project.path)))
    use_engine(monkeypatch, no_engine)
    assert hook(monkeypatch, capsys, agent, payload(agent, project.path)) == {}


@pytest.mark.parametrize("agent", AGENTS)
def test_the_recheck_cap_hands_over_to_a_human(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, survivor_engine)
    blocked_report(hook(monkeypatch, capsys, agent, payload(agent, project.path)))
    saved = state.load(project.path)
    assert saved is not None
    # what recheck leaves after its third round
    state.save(project.path, saved.model_copy(update={"rounds": 3, "fingerprint": None}))
    use_engine(monkeypatch, no_engine)
    answer = hook(monkeypatch, capsys, agent, payload(agent, project.path))
    assert "decision" not in answer
    assert "cap 3" in answer["systemMessage"]
    assert "a human should review" in answer["systemMessage"]


@pytest.mark.parametrize("agent", AGENTS)
def test_a_block_only_a_human_can_lift_lets_the_agent_stop(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, killing_engine)
    project.write("tests/test_mod.py", TEST.replace("assert double(2) == 4", "assert True"))
    answer = hook(monkeypatch, capsys, agent, payload(agent, project.path))
    assert "decision" not in answer
    assert answer["systemMessage"].startswith("Mutineer: block")
    assert "for a human" in answer["systemMessage"]


@pytest.mark.parametrize("agent", AGENTS)
def test_a_pass_lets_the_agent_stop(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, killing_engine)
    answer = hook(monkeypatch, capsys, agent, payload(agent, project.path))
    assert answer == {
        "systemMessage": "Mutineer: pass (rule score 1/1, diff score 1/1); "
        "0 findings for a human. See `mutineer run`."
    }


@pytest.mark.parametrize("agent", AGENTS)
def test_a_warn_with_only_human_findings_lets_the_agent_stop(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, unowned_engine)
    # a marker no accepted rule matches warns, for a human
    project.write("tests/test_other.py", UNKNOWN_RULE_TEST)
    answer = hook(monkeypatch, capsys, agent, payload(agent, project.path))
    assert "decision" not in answer
    assert answer["systemMessage"] == (
        "Mutineer: warn (diff score 0/1); 2 findings for a human. See `mutineer run`."
    )


##
# Mutineer can't run


@pytest.mark.parametrize("agent", AGENTS)
def test_a_deleted_rule_lets_the_agent_stop(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, no_engine)
    project.write(".mutineer/rules.toml", "version = 1\n")
    answer = hook(monkeypatch, capsys, agent, payload(agent, project.path))
    assert "decision" not in answer
    assert "retire DBL-001 instead of deleting it" in answer["systemMessage"]


@pytest.mark.parametrize("agent", AGENTS)
def test_outside_a_git_repository_lets_the_agent_stop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    use_engine(monkeypatch, no_engine)
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    answer = hook(monkeypatch, capsys, agent, payload(agent, tmp_path))
    assert "decision" not in answer
    # the same error `mutineer run` exits 4 with there
    assert answer["systemMessage"].startswith("Mutineer could not run: no 'main' or 'master'")


@pytest.mark.parametrize("agent", AGENTS)
def test_an_unexpected_error_lets_the_agent_stop(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any, agent: str
) -> None:
    def broken() -> Engine:
        raise RuntimeError("boom")

    use_engine(monkeypatch, broken)
    answer = hook(monkeypatch, capsys, agent, payload(agent, project.path))
    assert answer == {"systemMessage": "Mutineer failed: RuntimeError: boom"}


##
# where it runs


def test_claude_prefers_its_project_dir(
    project: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    use_engine(monkeypatch, survivor_engine)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(project.path))
    blocked_report(hook(monkeypatch, capsys, "claude", payload("claude", elsewhere)))


def test_codex_runs_in_the_session_cwd_from_a_subdirectory(
    project: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    use_engine(monkeypatch, survivor_engine)
    monkeypatch.chdir(tmp_path)
    blocked_report(hook(monkeypatch, capsys, "codex", payload("codex", project.path / "src")))


class NoisyEngine(Engine):
    """Prints to stdout from Python and from a subprocess, as mutmut and pytest do."""

    def run(self, diff: Diff, rule_map: RuleMap, root: Path) -> EngineRun:
        print("noise from python")
        subprocess.run([sys.executable, "-c", "print('noise from a child')"], check=True)
        return super().run(diff, rule_map, root)


def test_nothing_but_the_answer_reaches_stdout(
    project: Any, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    use_engine(monkeypatch, lambda: NoisyEngine([("killed", "killed")], [RULE_TEST]))
    monkeypatch.setattr("sys.stdin", io.StringIO(payload("codex", project.path)))
    assert main(["hook", "stop", "--agent", "codex"]) == 0
    out, err = capfd.readouterr()
    assert json.loads(out)["systemMessage"].startswith("Mutineer: pass")
    assert "noise from python" in err and "noise from a child" in err
