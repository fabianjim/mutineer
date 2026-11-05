"""Shared test fixtures (WP0). Later packages may add fixtures in their own conftest files.

- ``shop_repo``: the spike's toy repo, built once per session from
  ``tests/fixtures/make_fixture.sh``. Call ``shop_repo.checkout("agent")`` before use;
  it resets the worktree, so tests must not rely on state left by another test.
- ``git_repo``: an empty git repo in ``tmp_path`` with small helpers.

Both run git with the user's global and system config ignored, so signing, hooks and
aliases on the developer's machine can't change results.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURE_SCRIPT = Path(__file__).parent / "fixtures" / "make_fixture.sh"


def git_env() -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="test",
        GIT_AUTHOR_EMAIL="test@example.com",
        GIT_COMMITTER_NAME="test",
        GIT_COMMITTER_EMAIL="test@example.com",
    )
    return env


class GitRepo:
    def __init__(self, path: Path) -> None:
        self.path = path

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args],
            cwd=self.path,
            env=git_env(),
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    def write(self, rel: str, text: str) -> Path:
        p = self.path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        return p

    def commit(self, message: str = "commit") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def checkout(self, branch: str) -> None:
        self.git("checkout", "-q", "-f", branch)
        self.git("clean", "-fdxq")


@pytest.fixture(scope="session")
def _shop_repo_session(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("shop") / "repo"
    subprocess.run(
        ["bash", str(FIXTURE_SCRIPT), str(path)],
        env=git_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    return path


@pytest.fixture
def shop_repo(_shop_repo_session: Path) -> Iterator[GitRepo]:
    repo = GitRepo(_shop_repo_session)
    yield repo
    repo.checkout("main")


@pytest.fixture
def git_repo(tmp_path: Path) -> GitRepo:
    repo = GitRepo(tmp_path / "repo")
    repo.path.mkdir()
    repo.git("init", "-q", "-b", "main")
    return repo
