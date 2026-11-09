"""``mutineer run`` and ``recheck`` through ``cli.main``, with a stand-in engine

The engine is replaced so these tests cover the command flow (state, round cap, base
checks, output) without running mutmut. The e2e tests run the real engine.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import mutineer.cli.recheck as recheck_cmd
import mutineer.cli.run as run_cmd
from mutineer import state
from mutineer.cli import main
from mutineer.models import Diff, EngineRun, FunctionRef, MutantResult, MutantStatus, RuleMap

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


class FakeEngine:
    """Survivors on the first run; ``rerun`` reports the names as ``killed_on_rerun``."""

    def __init__(self, killed_on_rerun: set[str] | None = None) -> None:
        self.killed_on_rerun = killed_on_rerun or set()
        self.reruns: list[list[str]] = []
        self.runs = 0

    def run(self, diff: Diff, rule_map: RuleMap, root: Path) -> EngineRun:
        self.runs += 1
        results = []
        for n, (a, r) in enumerate(
            [("survived", "survived"), ("survived", "survived"), ("killed", "killed")], 1
        ):
            for kind, status in (("all", a), ("rule", r)):
                results.append(_result(n, kind, status))
        return EngineRun(
            baseline_failed=False,
            functions=[
                {
                    "mutmut_name": FN,
                    "file": "src/pkg/mod.py",
                    "qualname": "double",
                    "start_line": 1,
                    "end_line": 2,
                }  # type: ignore[list-item]
            ],
            unmeasured=[],
            results=results,
            tests_by_function={FN: [RULE_TEST]},
            duration_s=0.5,
        )

    def rerun(self, names: list[str], rule_map: RuleMap, root: Path) -> EngineRun:
        self.reruns.append(list(names))
        results = [
            _result(
                int(n.rsplit("_", 1)[1]),
                "rule",
                "killed" if n in self.killed_on_rerun else "survived",
            )
            for n in names
        ]
        return EngineRun(
            baseline_failed=False,
            functions=[],
            unmeasured=[],
            results=results,
            tests_by_function={},
            duration_s=0.1,
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
    return git_repo


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    fake = FakeEngine(killed_on_rerun={f"{FN}__mutmut_1"})
    monkeypatch.setattr(run_cmd, "make_engine", lambda: fake)
    monkeypatch.setattr(recheck_cmd, "make_engine", lambda: fake)
    return fake


def agent_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)  # type: ignore[no-any-return]


##
# run


def test_run_saves_state_for_recheck(project: Any, engine: FakeEngine, capsys: Any) -> None:
    assert main(["run", "--format", "agent"]) == 0
    out = agent_json(capsys)
    assert out["status"] == "warn"
    assert out["rule_score"] == {"killed": 1, "total": 3}
    saved = state.load(project.path)
    assert saved is not None
    assert saved.rounds == 0
    assert saved.base_ref == "main"
    assert saved.survivor_names == [f"{FN}__mutmut_1", f"{FN}__mutmut_2"]
    assert saved.engine_run is not None and saved.verdict.status == "warn"


def test_no_mutation_never_calls_the_engine(project: Any, engine: FakeEngine, capsys: Any) -> None:
    assert main(["run", "--no-mutation", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["status"], out["rule_score"], out["diff_score"]) == ("pass", None, None)
    assert engine.runs == 0
    saved = state.load(project.path)
    assert saved is not None and saved.engine_run is None and saved.survivor_names == []


def test_output_file_leaves_stdout_empty(
    project: Any, engine: FakeEngine, capsys: Any, tmp_path: Path
) -> None:
    target = tmp_path / "report.md"
    assert main(["run", "--format", "markdown", "--output", str(target)]) == 0
    assert capsys.readouterr().out == ""
    assert target.read_text().startswith("## Mutineer: WARN")


def test_strict_turns_warn_into_exit_1(project: Any, engine: FakeEngine) -> None:
    assert main(["run", "--strict", "--format", "json"]) == 1


def test_deleting_a_rule_stops_the_run(project: Any, engine: FakeEngine, capsys: Any) -> None:
    project.write(".mutineer/rules.toml", "version = 1\n")
    assert main(["run"]) == 4
    assert "retire DBL-001 instead of deleting it" in capsys.readouterr().err
    assert engine.runs == 0


##
# recheck


def test_recheck_without_a_run(project: Any, engine: FakeEngine, capsys: Any) -> None:
    assert main(["recheck"]) == 4
    assert "run `mutineer run` first" in capsys.readouterr().err


def test_recheck_reruns_survivors_and_keeps_the_denominator(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    main(["run", "--format", "agent"])
    capsys.readouterr()
    assert main(["recheck", "--format", "agent"]) == 0
    out = agent_json(capsys)
    assert engine.reruns == [[f"{FN}__mutmut_1", f"{FN}__mutmut_2"]]
    assert out["rule_score"] == {"killed": 2, "total": 3}
    assert out["diff_score"] == {"killed": 1, "total": 3}  # the all pass is kept
    assert [f["evidence"]["mutant"] for f in out["findings"]] == [f"{FN}__mutmut_2"]
    saved = state.load(project.path)
    assert saved is not None
    assert (saved.rounds, saved.survivor_names) == (1, [f"{FN}__mutmut_2"])


def test_recheck_round_cap_needs_a_human(project: Any, engine: FakeEngine, capsys: Any) -> None:
    main(["run"])
    assert [main(["recheck", "--max-rounds", "2"]) for _ in range(2)] == [0, 0]
    capsys.readouterr()
    assert main(["recheck", "--max-rounds", "2"]) == 3
    assert "a human should review" in capsys.readouterr().err
    assert len(engine.reruns) == 2


def test_recheck_cap_comes_from_config(project: Any, engine: FakeEngine) -> None:
    pyproject = project.path / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + "\n[tool.mutineer]\nmax_rounds = 1\n")
    main(["run"])
    assert main(["recheck"]) == 0
    assert main(["recheck"]) == 3


def test_recheck_without_survivors_shows_the_stored_verdict(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    main(["run", "--no-mutation", "--format", "json"])
    stored = json.loads(capsys.readouterr().out)
    assert main(["recheck", "--format", "json"]) == 0
    assert json.loads(capsys.readouterr().out) == stored
    assert engine.reruns == []


def test_recheck_after_the_base_moved(project: Any, engine: FakeEngine, capsys: Any) -> None:
    project.commit("work")
    main(["run"])
    project.git("checkout", "-q", "main")
    project.write("README", "new\n")
    project.commit("main moves on")
    project.git("checkout", "-q", "work")
    project.git("merge", "-q", "main", "-m", "merge main")
    capsys.readouterr()
    assert main(["recheck"]) == 4
    assert "base moved" in capsys.readouterr().err


def test_merge_replaces_only_the_rerun_rule_results() -> None:
    stored = FakeEngine().run(None, {}, Path())  # type: ignore[arg-type]
    rerun = FakeEngine({f"{FN}__mutmut_1"}).rerun([f"{FN}__mutmut_1"], {}, Path())
    merged = recheck_cmd.merge_rerun(stored, rerun)
    by_key = {(r.name, r.run): r.status for r in merged.results}
    assert by_key[(f"{FN}__mutmut_1", "rule")] == MutantStatus.KILLED
    assert by_key[(f"{FN}__mutmut_1", "all")] == MutantStatus.SURVIVED
    assert by_key[(f"{FN}__mutmut_2", "rule")] == MutantStatus.SURVIVED
    assert len(merged.results) == len(stored.results)
    assert merged.functions == stored.functions
    assert merged.tests_by_function == stored.tests_by_function


##
# a test suite pytest can't collect


BROKEN_TEST = "def test_broken(:\n    pass\n"


@pytest.mark.rule("VERDICT-002")
@pytest.mark.parametrize("extra", [[], ["--no-mutation"]])
def test_a_suite_that_cannot_be_collected_blocks(
    project: Any, engine: FakeEngine, capsys: Any, extra: list[str]
) -> None:
    project.write("tests/test_broken.py", BROKEN_TEST)
    assert main(["run", *extra, "--format", "json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "block"
    (failed,) = [f for f in out["findings"] if f["kind"] == "baseline_failed"]
    assert failed["severity"] == "block"
    assert failed["evidence"]["stage"] == "collection"
    assert "test_broken.py" in failed["evidence"]["output"]
    assert (out["rule_score"], out["diff_score"]) == (None, None)
    assert engine.runs == 0
    # the rule map is unknown, so no rule is reported unguarded
    assert [f for f in out["findings"] if f["kind"] == "rule_unguarded"] == []


def test_the_agent_is_told_to_fix_an_uncollectable_suite(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    project.write("tests/test_broken.py", BROKEN_TEST)
    assert main(["run", "--format", "agent"]) == 2
    out = agent_json(capsys)
    assert out["instruction"].startswith("The test suite fails without any mutant")
    assert out["findings"] == []


def test_recheck_with_an_uncollectable_suite_keeps_the_survivors(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    main(["run"])
    project.write("tests/test_broken.py", BROKEN_TEST)
    capsys.readouterr()
    assert main(["recheck", "--format", "json"]) == 2
    out = json.loads(capsys.readouterr().out)
    assert [f["kind"] for f in out["findings"] if f["severity"] == "block"] == ["baseline_failed"]
    assert engine.reruns == []
    saved = state.load(project.path)
    assert saved is not None
    assert saved.rounds == 1
    assert saved.survivor_names == [f"{FN}__mutmut_1", f"{FN}__mutmut_2"]

    (project.path / "tests/test_broken.py").unlink()
    assert main(["recheck", "--format", "agent"]) == 0
    assert engine.reruns == [[f"{FN}__mutmut_1", f"{FN}__mutmut_2"]]


##
# answers


ANSWER = """\
version = 1

[[answer]]
function = "pkg.mod.x_triple"
question = "survivor_unowned"
reason = "{reason}"
"""


def test_run_reports_an_answer_added_since_the_base(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    assert main(["run", "--no-mutation", "--format", "json"]) == 0
    kinds = [f["kind"] for f in json.loads(capsys.readouterr().out)["findings"]]
    assert "answer_added" not in kinds

    project.write(".mutineer/answers.toml", ANSWER.format(reason="new"))
    assert main(["run", "--no-mutation", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    added = [f for f in out["findings"] if f["kind"] == "answer_added"]
    assert [f["message"] for f in added] == [
        "A human answer was recorded for pkg.mod.x_triple: new"
    ]
    assert out["status"] == "pass"


def test_an_invalid_answers_file_stops_the_run(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    project.write(".mutineer/answers.toml", "version = 2\n")
    assert main(["run"]) == 4
    assert ".mutineer/answers.toml" in capsys.readouterr().err
    assert engine.runs == 0


def _base_here(project: Any) -> str:
    """Commit the worktree and branch ``other`` there, so ``--base other`` diffs from it."""
    sha: str = project.commit("work")
    project.git("branch", "other")
    return sha


def test_run_judges_the_diff_from_the_base_it_is_given(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    sha = _base_here(project)
    assert main(["run", "--base", "other", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["base"] == sha
    assert out["diff_score"] == {"killed": 1, "total": 3}
    assert 0 < out["duration_s"] < 600
    saved = state.load(project.path)
    assert saved is not None
    assert (saved.base_ref, saved.base) == ("other", sha)


def test_an_answer_already_in_the_base_is_not_reported_again(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    project.write(".mutineer/answers.toml", ANSWER.format(reason="old"))
    _base_here(project)
    assert main(["run", "--base", "other", "--no-mutation", "--format", "json"]) == 0
    kinds = [f["kind"] for f in json.loads(capsys.readouterr().out)["findings"]]
    assert "answer_added" not in kinds


class UnownedEngine(FakeEngine):
    """One survivor in ``double``, covered only by a test no rule marks."""

    def run(self, diff: Diff, rule_map: RuleMap, root: Path) -> EngineRun:
        self.runs += 1
        result = _result(1, "all", "survived")
        return EngineRun(
            baseline_failed=False,
            functions=[
                FunctionRef(
                    mutmut_name=FN,
                    file="src/pkg/mod.py",
                    qualname="double",
                    start_line=1,
                    end_line=2,
                )
            ],
            unmeasured=[],
            results=[result],
            tests_by_function={FN: ["tests/test_mod.py::test_other"]},
            duration_s=0.5,
        )


def test_run_shows_the_recorded_answer_on_its_question(
    project: Any, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr(run_cmd, "make_engine", UnownedEngine)
    project.write(
        ".mutineer/answers.toml",
        ANSWER.replace("pkg.mod.x_triple", FN).format(reason="not a promise"),
    )
    assert main(["run", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    (unowned,) = [f for f in out["findings"] if f["kind"] == "survivor_unowned"]
    assert unowned["evidence"]["answer"] == {
        "reason": "not a promise",
        "question": "survivor_unowned",
    }
    assert out["diff_score"] == {"killed": 0, "total": 1}


def test_a_changed_rule_is_reported_at_the_rules_file(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    project.write(".mutineer/rules.toml", RULES.replace("double doubles", "double returns 2x"))
    assert main(["run", "--no-mutation", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    (changed,) = [f for f in out["findings"] if f["kind"] == "rules_file_changed"]
    assert changed["location"]["file"] == ".mutineer/rules.toml"


SPEC_TEST = """\
import pytest
from pkg.mod import double


@pytest.mark.rule("DBL-001")
def test_spec_double():
    assert double(3) == 6
"""


def test_a_weakened_rule_test_under_a_configured_test_path_blocks(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    pyproject = project.path / "pyproject.toml"
    pyproject.write_text(pyproject.read_text() + '\n[tool.mutineer]\ntest_paths = ["spec"]\n')
    project.write("spec/test_spec.py", SPEC_TEST)
    _base_here(project)
    project.write("spec/test_spec.py", SPEC_TEST.replace("double(3) == 6", "double(3)"))
    assert main(["run", "--base", "other", "--no-mutation", "--format", "json"]) == 2
    out = json.loads(capsys.readouterr().out)
    blocks = [f for f in out["findings"] if f["severity"] == "block"]
    assert [f["location"]["file"] for f in blocks] == ["spec/test_spec.py"]


##
# disputes


def _dispute(n: int, status: str = "open") -> str:
    response = '\nresponse = "it matters"' if status == "rejected" else ""
    return (
        f'[[dispute]]\nfunction = "{FN}"\noriginal = "return x * 2"\n'
        f'mutated = "return x * {n + 2}"\nreason = "same result for the inputs"\n'
        f'status = "{status}"{response}\n'
    )


def _write_disputes(project: Any, *disputes: str) -> None:
    project.write(".mutineer/disputes.toml", "version = 1\n\n" + "\n".join(disputes))


def _kinds(out: dict[str, Any]) -> list[str]:
    return [f["kind"] for f in out["findings"]]


def test_recheck_skips_an_open_disputed_mutant(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    main(["run"])
    _write_disputes(project, _dispute(2))
    capsys.readouterr()
    assert main(["recheck", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert engine.reruns == [[f"{FN}__mutmut_1"]]
    # mutmut_1 is now killed by the rule pass only, which is inconclusive
    assert sorted(_kinds(out)) == ["mutant_disputed", "mutant_inconclusive"]
    assert out["status"] == "warn"
    saved = state.load(project.path)
    assert saved is not None
    assert (saved.rounds, saved.survivor_names) == (1, [])


def test_recheck_with_only_disputed_survivors_retriages_for_free(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    main(["run"])
    _write_disputes(project, _dispute(1), _dispute(2))
    capsys.readouterr()
    for _ in range(3):
        assert main(["recheck", "--max-rounds", "1", "--format", "json"]) == 0
    assert engine.reruns == []
    saved = state.load(project.path)
    assert saved is not None
    assert (saved.rounds, saved.survivor_names) == (0, [])
    assert [f.kind for f in saved.verdict.findings] == ["mutant_disputed", "mutant_disputed"]
    assert saved.verdict.status == "warn"


def test_a_dispute_resolved_after_the_run_changes_the_recheck(
    project: Any, engine: FakeEngine, capsys: Any
) -> None:
    _write_disputes(project, _dispute(1), _dispute(2))
    main(["run", "--format", "json"])
    assert json.loads(capsys.readouterr().out)["status"] == "warn"

    _write_disputes(project, _dispute(1, "confirmed"), _dispute(2, "confirmed"))
    assert main(["recheck", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "pass"
    assert sorted(_kinds(out)) == ["dispute_confirmed"] * 2 + ["mutant_disputed"] * 2
    assert out["rule_score"] == {"killed": 1, "total": 1}
    assert engine.reruns == []

    _write_disputes(project, _dispute(1, "rejected"), _dispute(2, "confirmed"))
    assert main(["recheck", "--format", "agent"]) == 0
    out = agent_json(capsys)
    assert out["status"] == "warn"
    assert [f["evidence"]["mutant"] for f in out["findings"]] == [f"{FN}__mutmut_1"]
    assert engine.reruns == []
    saved = state.load(project.path)
    assert saved is not None
    assert (saved.rounds, saved.survivor_names) == (0, [f"{FN}__mutmut_1"])

    assert main(["recheck", "--format", "agent"]) == 0
    assert engine.reruns == [[f"{FN}__mutmut_1"]]


def test_dispute_confirmed_is_reported_once(project: Any, engine: FakeEngine, capsys: Any) -> None:
    _write_disputes(project, _dispute(1, "confirmed"), _dispute(2))
    assert main(["run", "--no-mutation", "--format", "json"]) == 0
    out = json.loads(capsys.readouterr().out)
    (f,) = [f for f in out["findings"] if f["kind"] == "dispute_confirmed"]
    assert (f["severity"], f["audience"]) == ("info", "human")
    assert f["location"]["file"] == ".mutineer/disputes.toml"
    assert out["status"] == "pass"

    _base_here(project)
    assert main(["run", "--base", "other", "--no-mutation", "--format", "json"]) == 0
    assert "dispute_confirmed" not in _kinds(json.loads(capsys.readouterr().out))
