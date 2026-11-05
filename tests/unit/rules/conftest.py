"""Fixtures for rules tests: a private clone of the shop fixture"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest


def _git_env() -> dict[str, str]:
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


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=_git_env(), capture_output=True, text=True, check=True
    ).stdout


@pytest.fixture
def shop_clone(_shop_repo_session: Path, tmp_path: Path) -> Callable[[str], Path]:
    """Clone the session's shop fixture and check out a branch, safe to mutate"""

    def make(branch: str = "main") -> Path:
        dest = tmp_path / f"shop-{branch}"
        git(tmp_path, "clone", "-q", str(_shop_repo_session), str(dest))
        if branch != "main":
            git(dest, "checkout", "-q", branch)
        return dest

    return make


@pytest.fixture
def run_git() -> Callable[..., str]:
    """``run_git(cwd, *args)``: git with the developer's config ignored"""
    return git
