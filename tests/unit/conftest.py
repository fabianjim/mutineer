"""Fixtures for tamper tests: a two-commit repo

``tamper_repo.base({...})`` commits files on ``main`` and branches to ``work``;
``tamper_repo.head({...})`` writes (or, with ``None``, deletes) files in the worktree
  and returns the tamper findings for the diff
"""

from __future__ import annotations

from typing import Any

import pytest

from mutineer.checks import tamper
from mutineer.gitdiff import load_diff
from mutineer.models import Finding

RULES_TOML = """version = 1

[[rule]]
id = "SHIP-003"
statement = "Orders of $50 or more ship free"

[[rule]]
id = "PAY-001"
statement = "Payments are rounded to cents"
"""


class TamperRepo:
    rules_toml = RULES_TOML

    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self.path = repo.path

    def base(self, files: dict[str, str], *, rules: str = RULES_TOML) -> None:
        self.repo.write(".mutineer/rules.toml", rules)
        for rel, text in files.items():
            self.repo.write(rel, text)
        self.repo.commit("base")
        self.repo.git("checkout", "-q", "-b", "work")

    def head(
        self, files: dict[str, str | None], *, test_paths: tuple[str, ...] = ("tests",)
    ) -> list[Finding]:
        for rel, text in files.items():
            if text is None:
                (self.path / rel).unlink()
            else:
                self.repo.write(rel, text)
        return self.findings(test_paths=test_paths)

    def findings(self, *, test_paths: tuple[str, ...] = ("tests",)) -> list[Finding]:
        diff = load_diff(self.path, None, ".mutineer/rules.toml")
        return tamper.check(diff, self.path, None, test_paths=test_paths)


@pytest.fixture
def tamper_repo(git_repo: Any) -> TamperRepo:
    return TamperRepo(git_repo)
