"""``mutineer fresh`` and ``run``'s reuse of the last verdict, on a small git repo

Each fingerprint input is changed alone after a ``run``: ``fresh`` must then exit 1 and
name it, and exit 0 before the change. What the fingerprint deliberately leaves out must
keep ``fresh`` at 0.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

import mutineer
import mutineer.cli.run as run_cmd
from mutineer import state
from mutineer.cli import main
from mutineer.models import Diff, EngineRun, RuleMap

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
GITIGNORE = "mutants/\n.mutineer/last-run.json\n__pycache__/\n*.gen\n"


class EmptyEngine:
    """A full run with no changed function to mutate."""

    def __init__(self) -> None:
        self.runs = 0

    def run(self, diff: Diff, rule_map: RuleMap, root: Path) -> EngineRun:
        self.runs += 1
        return EngineRun(
            baseline_failed=False,
            functions=[],
            unmeasured=[],
            results=[],
            tests_by_function={},
            duration_s=0.0,
        )


@pytest.fixture
def project(git_repo: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    git_repo.write("pyproject.toml", PYPROJECT)
    git_repo.write(".mutineer/rules.toml", RULES)
    git_repo.write(".gitignore", GITIGNORE)
    git_repo.write("src/pkg/__init__.py", "")
    git_repo.write("src/pkg/mod.py", "def double(x):\n    return x + x\n")
    git_repo.write("tests/test_mod.py", TEST)
    git_repo.write("tests/data.txt", "data\n")
    git_repo.commit("base")
    git_repo.git("checkout", "-q", "-b", "work")
    git_repo.write("src/pkg/mod.py", "def double(x):\n    return x * 2\n")
    monkeypatch.chdir(git_repo.path)
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    for name in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS", "PYTHONHASHSEED"):
        monkeypatch.delenv(name, raising=False)
    return git_repo


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> EmptyEngine:
    fake = EmptyEngine()
    monkeypatch.setattr(run_cmd, "make_engine", lambda: fake)
    return fake


def fresh(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    capsys.readouterr()
    code = main(["fresh", *args])
    return code, capsys.readouterr().out


def run_no_mutation(capsys: pytest.CaptureFixture[str], *args: str) -> dict[str, Any]:
    capsys.readouterr()
    assert main(["run", "--no-mutation", "--format", "json", *args]) == 0
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


@pytest.fixture
def ran(project: Any, capsys: pytest.CaptureFixture[str]) -> Any:
    """A ``run --no-mutation`` that ``fresh --no-mutation`` agrees is still fresh."""
    run_no_mutation(capsys)
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")
    return project


##
# each input forces a real run


@pytest.mark.rule("REUSE-001")
def test_an_edited_tracked_file(ran: Any, capsys: Any) -> None:
    ran.write("src/pkg/mod.py", "def double(x):\n    return 2 * x\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1
    assert out.startswith("stale:")
    assert "worktree" in out and "src/pkg/mod.py" in out


@pytest.mark.rule("REUSE-001")
def test_an_edited_file_the_diff_had_not_touched(ran: Any, capsys: Any) -> None:
    ran.write("src/pkg/__init__.py", "X = 1\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "src/pkg/__init__.py" in out


@pytest.mark.rule("REUSE-001")
def test_a_new_untracked_file(ran: Any, capsys: Any) -> None:
    ran.write("notes.txt", "hello\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "worktree" in out and "notes.txt" in out


@pytest.mark.rule("REUSE-001")
def test_a_deleted_file(ran: Any, capsys: Any) -> None:
    (ran.path / "tests/data.txt").unlink()
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "tests/data.txt" in out


@pytest.mark.rule("REUSE-001")
def test_a_changed_ignored_file_under_a_test_path(ran: Any, capsys: Any) -> None:
    ran.write("tests/fixture.gen", "one\n")
    run_no_mutation(capsys)
    assert fresh(capsys, "--no-mutation")[0] == 0
    ran.write("tests/fixture.gen", "two\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1
    assert "files read" in out and "tests/fixture.gen" in out
    assert "worktree" not in out


@pytest.mark.rule("REUSE-001")
def test_a_changed_ignored_file_under_a_source_path(ran: Any, capsys: Any) -> None:
    ran.write("src/pkg/table.gen", "one\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "files read" in out and "src/pkg/table.gen" in out


@pytest.mark.rule("REUSE-001")
def test_a_conftest_above_a_project_in_a_subdirectory(
    git_repo: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    git_repo.write("conftest.py", "")
    git_repo.write("app/pyproject.toml", PYPROJECT)
    git_repo.write("app/.mutineer/rules.toml", RULES)
    git_repo.write(".gitignore", GITIGNORE)
    git_repo.write("app/src/pkg/__init__.py", "")
    git_repo.write("app/src/pkg/mod.py", "def double(x):\n    return x + x\n")
    git_repo.write("app/tests/test_mod.py", TEST)
    git_repo.commit("base")
    git_repo.git("checkout", "-q", "-b", "work")
    monkeypatch.chdir(git_repo.path / "app")
    monkeypatch.setenv("PYTHONDONTWRITEBYTECODE", "1")
    run_no_mutation(capsys)
    assert fresh(capsys, "--no-mutation")[0] == 0
    git_repo.write("conftest.py", "X = 1\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "worktree" in out and "conftest.py" in out


@pytest.mark.rule("REUSE-001")
def test_a_rebase_onto_a_new_base_commit(ran: Any, capsys: Any) -> None:
    ran.commit("work")
    run_no_mutation(capsys)
    ran.git("checkout", "-q", "main")
    ran.write("README", "readme\n")
    ran.commit("main moves")
    ran.git("checkout", "-q", "work")
    # a new commit on the base alone doesn't move the merge-base
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")
    ran.git("rebase", "-q", "main")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "merge-base" in out


@pytest.mark.rule("REUSE-001")
def test_a_changed_ledger_file(ran: Any, capsys: Any) -> None:
    ran.write(".mutineer/answers.toml", "version = 1\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and ".mutineer/answers.toml" in out


@pytest.mark.rule("REUSE-001")
def test_a_changed_ignored_ledger_file(ran: Any, capsys: Any) -> None:
    ran.write(".gitignore", GITIGNORE + ".mutineer/disputes.toml\n")
    ran.commit("ignore disputes")
    run_no_mutation(capsys)
    ran.write(".mutineer/disputes.toml", "version = 1\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "files read" in out and ".mutineer/disputes.toml" in out


@pytest.mark.rule("REUSE-001")
def test_a_changed_config(ran: Any, capsys: Any) -> None:
    ran.write("pyproject.toml", PYPROJECT + "\n[tool.mutineer]\nmax_rounds = 5\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "config" in out


@pytest.mark.rule("REUSE-001")
def test_a_changed_python(ran: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "version", sys.version + " (patched)")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "python" in out


@pytest.mark.rule("REUSE-001")
def test_a_block_is_never_reused_across_a_changed_mutineer(
    project: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # an editable install changes without a version bump; a verdict from other Mutineer
    # code must not stand in, or a tamper the new code blocks could be shown as before
    package = tmp_path / "site" / "mutineer"
    shutil.copytree(
        Path(mutineer.__file__).parent, package, ignore=shutil.ignore_patterns("__pycache__")
    )
    monkeypatch.setattr(mutineer, "__file__", str(package / "__init__.py"))
    project.write("tests/test_mod.py", TEST.replace("== 4", ">= 0"))
    capsys.readouterr()
    assert main(["run", "--no-mutation", "--format", "json"]) == 2
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")
    (package / "verdict.py").write_text((package / "verdict.py").read_text() + "\n# changed\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "python" in out


@pytest.mark.rule("REUSE-001")
def test_a_changed_environment_variable(
    ran: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONHASHSEED", "1")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "environment" in out


@pytest.mark.rule("REUSE-001")
def test_no_mutation_never_stands_in_for_a_full_run(
    project: Any, engine: EmptyEngine, capsys: Any
) -> None:
    capsys.readouterr()
    assert main(["run", "--format", "json"]) == 0
    assert fresh(capsys) == (0, "fresh\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "mode" in out
    run_no_mutation(capsys)
    code, out = fresh(capsys)
    assert code == 1 and "mode" in out


def test_no_state_is_stale(project: Any, capsys: Any) -> None:
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and out.startswith("stale:")


def test_an_unknown_base_cannot_run(project: Any, capsys: Any) -> None:
    assert main(["fresh", "--base", "nope"]) == 4


def test_fresh_writes_nothing(ran: Any, capsys: Any) -> None:
    before = (ran.path / state.STATE_FILE).read_bytes()
    ran.write("src/pkg/mod.py", "def double(x):\n    return 2 * x\n")
    fresh(capsys, "--no-mutation")
    assert (ran.path / state.STATE_FILE).read_bytes() == before
    assert not (ran.path / "mutants").exists()


##
# what is left out keeps the verdict


@pytest.mark.rule("REUSE-001")
def test_committing_without_changing_content_keeps_it_fresh(ran: Any, capsys: Any) -> None:
    ran.commit("work")
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")


@pytest.mark.rule("REUSE-001")
def test_touching_mutants_keeps_it_fresh(ran: Any, capsys: Any) -> None:
    ran.write("mutants/src/pkg/mod.py", "junk\n")
    ran.write(".gitignore", GITIGNORE)
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")


@pytest.mark.rule("REUSE-001")
def test_the_state_file_under_also_copy_keeps_it_fresh(project: Any, capsys: Any) -> None:
    project.write(
        "pyproject.toml",
        PYPROJECT.replace("[tool.pytest", 'also_copy = [".mutineer"]\n\n[tool.pytest'),
    )
    run_no_mutation(capsys)
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")


@pytest.mark.rule("REUSE-001")
def test_untracked_bytecode_keeps_it_fresh(project: Any, capsys: Any) -> None:
    # a project that doesn't ignore bytecode: Python writes it next to the sources, and the
    # verdict must not go stale over it
    project.write(".gitignore", "mutants/\n.mutineer/last-run.json\n")
    project.commit("bytecode not ignored")
    run_no_mutation(capsys)
    project.write("src/pkg/mod.pyc", "bytecode")
    project.write("tests/__pycache__/test_mod.cpython-311.pyc", "bytecode")
    # importlib writes bytecode to a temporary name in __pycache__ first
    project.write("src/pkg/__pycache__/mod.cpython-311.pyc.4402", "partial")
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")
    project.write("src/pkg/mod_gen.py", "X = 1\n")
    code, out = fresh(capsys, "--no-mutation")
    assert code == 1 and "src/pkg/mod_gen.py" in out


@pytest.mark.rule("REUSE-001")
def test_a_reused_run_renders_with_the_current_format(
    ran: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = json.loads((ran.path / state.STATE_FILE).read_text())["verdict"]

    def no_pytest(root: Path) -> RuleMap:
        raise AssertionError("a reused run must not collect the rule map")

    monkeypatch.setattr(run_cmd, "collect_rule_map", no_pytest)
    target = tmp_path / "report.md"
    args = ["run", "--no-mutation", "--format", "markdown", "--output", str(target)]
    assert main(args) == 0
    assert target.read_text().startswith("## Mutineer: PASS")
    capsys.readouterr()
    assert main(["run", "--no-mutation", "--format", "json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert {**second, "duration_s": 0} == {**first, "duration_s": 0}
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")


def test_a_reused_run_keeps_the_new_base_name(ran: Any, capsys: Any) -> None:
    ran.git("branch", "trunk", "main")
    run_no_mutation(capsys, "--base", "trunk")
    saved = state.load(ran.path)
    assert saved is not None and saved.base_ref == "trunk"
    assert fresh(capsys, "--no-mutation", "--base", "trunk") == (0, "fresh\n")


def test_no_reuse_always_runs(ran: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    collect = run_cmd.collect_rule_map

    def counting(root: Path) -> RuleMap:
        nonlocal calls
        calls += 1
        return collect(root)

    monkeypatch.setattr(run_cmd, "collect_rule_map", counting)
    run_no_mutation(capsys, "--no-reuse")
    assert calls == 1
    assert fresh(capsys, "--no-mutation") == (0, "fresh\n")


@pytest.mark.rule("REUSE-001")
def test_an_edit_during_the_run_stores_no_fingerprint(
    project: Any, capsys: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    collect = run_cmd.collect_rule_map

    def editing(root: Path) -> RuleMap:
        project.write("src/pkg/mod.py", "def double(x):\n    return x << 1\n")
        return collect(root)

    monkeypatch.setattr(run_cmd, "collect_rule_map", editing)
    run_no_mutation(capsys)
    saved = state.load(project.path)
    assert saved is not None and saved.fingerprint is None
    assert fresh(capsys, "--no-mutation")[0] == 1


def test_an_error_stores_nothing(ran: Any, capsys: Any) -> None:
    before = (ran.path / state.STATE_FILE).read_bytes()
    ran.write(".mutineer/rules.toml", "version = 1\n")
    assert main(["run", "--no-mutation"]) == 4
    assert (ran.path / state.STATE_FILE).read_bytes() == before
