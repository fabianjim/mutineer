"""Fixtures for the end-to-end tests of ``mutineer run`` and ``recheck``

- ``mutineer``: runs the CLI in-process in a repo and returns the exit code and the
  parsed report. ``fmt="json"`` (default) or ``"agent"`` parse JSON; other formats return
  the text. The report goes to a file outside the repo unless ``to_stdout`` is set.

The fixture repos come from ``shop_repo`` (shared, reset on checkout) and ``shop_copy``
(a private copy, for tests that edit files).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mutineer.cli import main

Mutineer = Callable[..., tuple[int, Any]]


@pytest.fixture
def mutineer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> Mutineer:
    calls = 0

    def call(root: Path, *args: str, fmt: str = "json", to_stdout: bool = False) -> tuple[int, Any]:
        nonlocal calls
        calls += 1
        monkeypatch.chdir(root)
        argv = [*args, "--format", fmt]
        target = tmp_path / f"report-{calls}.out"
        if not to_stdout:
            argv += ["--output", str(target)]
        capsys.readouterr()
        code = main(argv)
        captured = capsys.readouterr()
        text = captured.out if to_stdout else (target.read_text() if target.exists() else "")
        if not text:
            return code, captured.err
        return code, json.loads(text) if fmt in ("json", "agent") else text

    return call
