"""The engine can mutation-test Mutineer's own source. Slow: runs mutmut.

The launcher imports the installed ``mutineer`` package before mutmut runs the tests in
the same process. Unless it forgets those modules first, the tests import the original
code instead of the mutated copy in ``mutants/``, no mutant is ever hit, and every changed
function comes back unreached.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

import mutineer
from mutineer.engine.mutmut_engine import MutmutEngine
from mutineer.gitdiff import load_diff
from mutineer.models import MutantStatus

pytestmark = pytest.mark.slow

PYPROJECT = """\
[project]
name = "mutineer-copy"
version = "0"

[tool.mutmut]
source_paths = ["src/mutineer"]
pytest_add_cli_args_test_selection = ["tests"]
"""
TEST = """\
from mutineer.models import Audience, Finding, Severity
from mutineer.verdict import status_of


def finding(severity):
    return Finding(
        check="tamper", kind="k", severity=severity, audience=Audience.HUMAN, message="m"
    )


def test_status_of():
    assert status_of([]) == "pass"
    assert status_of([finding(Severity.WARN)]) == "warn"
    assert status_of([finding(Severity.BLOCK), finding(Severity.WARN)]) == "block"
"""


def test_mutants_in_mutineers_own_source_are_reached(git_repo: Any) -> None:
    root: Path = git_repo.path
    shutil.copytree(
        Path(mutineer.__file__).parent,
        root / "src" / "mutineer",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    git_repo.write("pyproject.toml", PYPROJECT)
    git_repo.write(".gitignore", "mutants/\n__pycache__/\n")
    git_repo.write("tests/test_status.py", TEST)
    git_repo.commit("copy of mutineer")
    verdict = root / "src" / "mutineer" / "verdict.py"
    verdict.write_text(
        verdict.read_text().replace('    return "pass"\n', '    return str("pass")\n')
    )

    run = MutmutEngine().run(load_diff(root, "main", ".mutineer/rules.toml"), {}, root)

    assert not run.baseline_failed, run.baseline_output
    assert [f.mutmut_name for f in run.functions] == ["mutineer.verdict.x_status_of"]
    assert run.unreached == []
    assert run.tests_by_function["mutineer.verdict.x_status_of"] == [
        "tests/test_status.py::test_status_of"
    ]
    assert any(r.status == MutantStatus.KILLED for r in run.results)
