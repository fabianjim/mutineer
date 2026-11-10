"""Every pytest session in a mutmut run gets its own basetemp. Slow: runs mutmut.

pytest's default temp root is shared: each session makes a numbered dir there and cleans up
old dirs and dead symlinks when it finishes. Two sessions that finish together can crash
there with ``FileNotFoundError``, and the crash becomes the mutant's status (exit 3, killed,
under ``fork``; -1, timeout, under ``forkserver``). The launcher gives each session its own
basetemp, unless the project sets one.
"""

from __future__ import annotations

import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mutineer.engine.mutmut_engine import MutmutEngine
from mutineer.models import Diff, EngineRun, MutantStatus, RuleMap

pytestmark = pytest.mark.slow

DiffOf = Callable[..., Diff]
FUNCTIONS = 8

# every function has several mutants, and its test checks nothing: all of them survive
MODULE = "".join(f"\n\ndef f{i}(x):\n    return x * {i + 2} + {i + 1}\n" for i in range(FUNCTIONS))

TESTS = "import pytest\nfrom pkg import m\n" + "".join(
    f'\n\n@pytest.mark.rule("R-1")\ndef test_f{i}(tmp_path):\n'
    f'    (tmp_path / "out").write_text(str(m.f{i}(3)))\n'
    for i in range(FUNCTIONS)
)

BARRIER = """
import os
import time

import pytest


def _barrier(path):
    # wait until a full group of sessions has arrived, so they go on together; a short
    # timeout lets a last, partial group go
    size = {size}
    os.makedirs(path, exist_ok=True)
    open(os.path.join(path, f"{{os.getpid()}}-{{time.time_ns()}}"), "w").close()
    group = -(-len(os.listdir(path)) // size) * size
    deadline = time.monotonic() + 0.5
    while len(os.listdir(path)) < group and time.monotonic() < deadline:
        time.sleep(0.001)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    # before the first tmp_path: sessions make their numbered temp dirs together
    if not getattr(item.session, "_started", False):
        item.session._started = True
        _barrier({barrier!r} + "/start")


@pytest.hookimpl(tryfirst=True)
def pytest_sessionfinish(session):
    # and reach pytest's temp cleanup together
    _barrier({barrier!r} + "/finish")
"""

TMP_PATH_LOGGER = """

@pytest.fixture(autouse=True)
def _log_tmp_path(tmp_path):
    with open({log!r}, "a") as f:
        f.write(str(tmp_path) + "\\n")
"""


def make_project(git_repo: Any, isolation: str, *, pyproject_extra: str = "") -> Path:
    git_repo.write(
        "pyproject.toml",
        f'[tool.mutmut]\nsource_paths = ["src/pkg"]\nprocess_isolation = "{isolation}"\n'
        f"{pyproject_extra}\n"
        '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
    )
    git_repo.write("src/pkg/__init__.py", "")
    git_repo.write("tests/conftest.py", "import pytest\n")
    git_repo.write("tests/test_m.py", TESTS)
    git_repo.write(".gitignore", "mutants/\n")
    git_repo.commit("base")
    git_repo.write("src/pkg/m.py", MODULE)
    return Path(git_repo.path)


def add_to_conftest(root: Path, text: str) -> None:
    conftest = root / "tests/conftest.py"
    conftest.write_text(conftest.read_text() + text)


def rule_map() -> RuleMap:
    return {f"tests/test_m.py::test_f{i}": ["R-1"] for i in range(FUNCTIONS)}


def wrong_statuses(run: EngineRun) -> list[tuple[str, str, MutantStatus]]:
    return [(r.run, r.name, r.status) for r in run.results if r.status != MutantStatus.SURVIVED]


@pytest.fixture
def private_temp(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    """Point pytest's default temp root and the system temp dir at dirs private to the test.

    The children's default root can't then disturb, or be disturbed by, the outer session,
    and a launcher temp dir left behind is visible.
    """
    temproot, system = tmp_path / "temproot", tmp_path / "system"
    temproot.mkdir()
    system.mkdir()
    monkeypatch.setenv("PYTEST_DEBUG_TEMPROOT", str(temproot))
    monkeypatch.setenv("TMPDIR", str(system))
    monkeypatch.setattr(tempfile, "tempdir", None)  # gettempdir() reads TMPDIR again
    return temproot, system


# ---- the race ----

# the race is random: before the fix about 1 run in 7 had a wrong status (7 of 80 runs
# over both isolations), after it none did in 40; a few rounds make a regression likely
# to show without slowing the suite much
ROUNDS = 6


@pytest.mark.parametrize("isolation", ["fork", "forkserver"])
def test_sessions_finishing_together_keep_their_statuses(
    git_repo: Any,
    diff_of: DiffOf,
    tmp_path: Path,
    private_temp: tuple[Path, Path],
    isolation: str,
) -> None:
    root = make_project(git_repo, isolation)
    barrier = tmp_path / "barrier"
    children = 8
    add_to_conftest(root, BARRIER.format(barrier=str(barrier), size=children))
    engine = MutmutEngine(max_children=children)

    for _ in range(ROUNDS):
        run = engine.run(diff_of(root), rule_map(), root)
        assert not run.baseline_failed
        assert {r.run for r in run.results} == {"all", "rule"}
        assert wrong_statuses(run) == []


# ---- the mechanism ----


@pytest.mark.parametrize(
    ("isolation", "children"), [("fork", 2), ("forkserver", 1), ("forkserver", 2)]
)
def test_no_session_uses_the_default_temp_root(
    git_repo: Any,
    diff_of: DiffOf,
    tmp_path: Path,
    private_temp: tuple[Path, Path],
    isolation: str,
    children: int,
) -> None:
    temproot, system = private_temp
    root = make_project(git_repo, isolation)
    log = tmp_path / "tmp_paths.log"
    add_to_conftest(root, TMP_PATH_LOGGER.format(log=str(log)))

    run = MutmutEngine(max_children=children).run(diff_of(root), rule_map(), root)
    assert not run.baseline_failed
    assert {r.run for r in run.results} == {"all", "rule"}
    assert wrong_statuses(run) == []
    used = log.read_text().splitlines()
    assert used and all(Path(p).is_relative_to(system) for p in used)
    assert len(set(used)) == len(used)  # no two tests shared a dir
    assert list(temproot.iterdir()) == []
    assert list(system.iterdir()) == []  # the launcher removed its temp dir


# ---- a project's own --basetemp ----


@pytest.mark.parametrize("isolation", ["fork", "forkserver"])
@pytest.mark.parametrize("where", ["pytest_add_cli_args", "addopts", "PYTEST_ADDOPTS"])
def test_projects_own_basetemp_is_kept(
    git_repo: Any,
    diff_of: DiffOf,
    tmp_path: Path,
    private_temp: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    isolation: str,
    where: str,
) -> None:
    temproot, system = private_temp
    own = tmp_path / "own"
    extra = ""
    if where == "pytest_add_cli_args":
        extra = f'pytest_add_cli_args = ["--basetemp={own}"]\n'
    root = make_project(git_repo, isolation, pyproject_extra=extra)
    if where == "addopts":
        pyproject = root / "pyproject.toml"
        pyproject.write_text(pyproject.read_text() + f'addopts = "--basetemp={own}"\n')
    if where == "PYTEST_ADDOPTS":
        monkeypatch.setenv("PYTEST_ADDOPTS", f"--basetemp={own}")
    log = tmp_path / "tmp_paths.log"
    add_to_conftest(root, TMP_PATH_LOGGER.format(log=str(log)))

    # one child: the project's dir is shared by every session, so keep them apart
    run = MutmutEngine(max_children=1).run(diff_of(root), rule_map(), root)
    assert not run.baseline_failed
    assert wrong_statuses(run) == []
    used = log.read_text().splitlines()
    assert used and all(Path(p).is_relative_to(own) for p in used)
    assert list(temproot.iterdir()) == []
    assert list(system.iterdir()) == []
