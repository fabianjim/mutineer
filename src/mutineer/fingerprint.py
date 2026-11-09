"""The fingerprint of everything a run's verdict depends on, so an unchanged run is reused.

``mutineer run`` stores the fingerprint with its verdict in ``.mutineer/last-run.json``.
When the next ``run`` computes an equal one, it shows the stored verdict without running
pytest or mutmut; ``mutineer fresh`` answers whether it would. A missed input gives a
wrong verdict, which is worse than a slow one, so every input below is there for a reason.

The inputs, each a named part so ``fresh`` can say what changed:

1. ``mode``: full run or ``--no-mutation``. Their reports differ; one never stands in for
   the other.
2. ``merge-base``: the merge-base sha. It fixes the base side of everything: the base
   rules file (``rules_file_changed``, deleted ids), the base test markers (tamper tier),
   base ``test_paths``, the base ledger files (``answer_added``, ``dispute_confirmed``),
   and the content of every file the diff doesn't touch.
3. ``worktree``: every change in the working tree against the merge-base, for the whole
   git repository: each changed path, its status, and a hash of its content on disk
   (deleted: a fixed marker), tracked and untracked-not-ignored, with ``mutants/`` and
   ``.mutineer/last-run.json`` left out, and untracked ``__pycache__/`` and ``*.pyc``
   too (Python rebuilds them from the sources). With 2, this fixes the content of every
   file git doesn't ignore: source, tests, ``conftest.py``, ``pyproject.toml``,
   ``pytest.ini``/``setup.cfg``, the rules file, the ledger files and the lock file. The
   whole repository, not only the project root: a ``conftest.py`` or config above a
   project in a subdirectory still changes what pytest does. A changed ``.gitignore`` or
   exclude file moves files in or out of this set, which changes the fingerprint.
4. ``files read``: every file on disk under mutmut's ``source_paths`` and ``also_copy``
   (mutmut's defaults included) and under ``test_paths`` (head and base), plus the rules
   file and the ``.mutineer/`` ledger files, whether git ignores them or not, skipping
   ``__pycache__/``, ``*.pyc``, ``mutants/`` and ``last-run.json``. mutmut copies these
   into ``mutants/`` and tests read them: an ignored generated module or test data file
   changes results without appearing in 3. Overlap with 3 is fine.
5. ``config``: the resolved ``[tool.mutineer]`` and ``[tool.mutmut]`` (or ``setup.cfg``
   ``[mutmut]``) config. Already fixed by 2 and 3; kept as its own input so ``fresh``
   can say the config changed.
6. ``python``: ``sys.version``, ``sys.executable``, Mutineer's version and a hash of its
   installed package files (an editable install changes without a version bump), and
   the name and version of every installed distribution, which covers mutmut, pytest,
   plugins and the project's dependencies.
7. ``environment``: the variables that change how pytest or Python run the tests:
   ``PYTEST_ADDOPTS``, ``PYTEST_PLUGINS``, ``PYTEST_DISABLE_PLUGIN_AUTOLOAD``,
   ``PYTHONPATH`` and ``PYTHONHASHSEED`` (set order decides the order of a mutant's tests).

Deliberately left out:

- ``--format``, ``--output``, ``--strict``: they only render and pick the exit code; a
  reused verdict is rendered and exited on with the current ones.
- The base ref's name: only the merge-base matters. The state keeps the latest name,
  since ``recheck`` reads it.
- The HEAD sha: the report's head is always ``WORKTREE``, so a commit that changes no
  content gives the same report.
- ``mutants/`` and ``last-run.json``: Mutineer's own output.
- ``--max-children`` and the CPU count: they change timing only, and timeouts are
  already timing-dependent.

Not covered: ignored files outside the paths in 4, changes inside git submodules outside
those paths, other environment variables, the network and other services, and flaky
tests. ``run --no-reuse`` always runs.

File contents are hashed, never mtimes.
"""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import sys
import tomllib
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

import mutineer
from mutineer import gitdiff
from mutineer.config import Config
from mutineer.ledger import ANSWERS_FILE, DISPUTES_FILE, PROPOSALS_FILE

FULL = "full"
NO_MUTATION = "no-mutation"

ENV_VARS = (
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
    "PYTHONPATH",
    "PYTHONHASHSEED",
)

# Mutineer's own output, relative to the project root (``state.STATE_FILE`` included)
_OWN_OUTPUT = ["mutants/", ".mutineer/last-run.json"]
_SKIP_DIRS = {"__pycache__"}
# mutmut copies these besides any configured ``also_copy``
_MUTMUT_ALSO_COPY = [
    "tests/",
    "test/",
    "setup.cfg",
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "Pipfile.lock",
    "pdm.lock",
]
_DELETED = "deleted"
_SHOWN = 10
"""How many paths ``fresh`` names per input before summarising the rest."""


class Fingerprint(BaseModel):
    """The inputs a verdict depends on, by name (see the module docstring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    mode: str
    merge_base: str
    worktree: dict[str, str]
    """Path relative to the repository's top level -> status and content hash."""
    files_read: dict[str, str]
    """Path relative to the project root -> content hash."""
    config: str
    python: str
    environment: str


def compute(
    root: Path, config: Config, merge_base: str, *, mutation: bool, test_paths: list[str]
) -> Fingerprint:
    """The fingerprint of a run in ``root`` against ``merge_base``.

    ``test_paths`` are the head and base test paths, as the run's tamper check uses them.
    """
    mutmut = _mutmut_table(root)
    source_paths = _mutmut_list(mutmut, "source_paths") or _mutmut_list(mutmut, "paths_to_mutate")
    source_paths = source_paths or _guess_source_paths(root)
    also_copy = [
        *_mutmut_list(mutmut, "also_copy"),
        *_MUTMUT_ALSO_COPY,
        *sorted(p.name for p in root.glob("test*.py")),
    ]
    read = [
        *source_paths,
        *also_copy,
        *test_paths,
        config.rules_file,
        PROPOSALS_FILE,
        ANSWERS_FILE,
        DISPUTES_FILE,
    ]
    return Fingerprint(
        mode=FULL if mutation else NO_MUTATION,
        merge_base=merge_base,
        worktree=_worktree(root, merge_base),
        files_read=_files_read(root, read),
        config=_digest(
            {
                "mutineer": config.model_dump(mode="json"),
                "mutmut": mutmut,
                "source_paths": source_paths,
                "also_copy": also_copy,
            }
        ),
        python=_digest(_python()),
        environment=_digest({name: os.environ.get(name) for name in ENV_VARS}),
    )


def differences(stored: Fingerprint, current: Fingerprint) -> list[str]:
    """What differs between two fingerprints, one entry per input, paths capped."""
    out = []
    if stored.mode != current.mode:
        out.append(f"mode ({stored.mode} -> {current.mode})")
    if stored.merge_base != current.merge_base:
        out.append("merge-base")
    for name, old, new in (
        ("worktree", stored.worktree, current.worktree),
        ("files read", stored.files_read, current.files_read),
    ):
        paths = sorted(p for p in old.keys() | new.keys() if old.get(p) != new.get(p))
        if paths:
            shown = ", ".join(paths[:_SHOWN])
            more = f" and {len(paths) - _SHOWN} more" if len(paths) > _SHOWN else ""
            out.append(f"{name} ({shown}{more})")
    for name, a, b in (
        ("config", stored.config, current.config),
        ("python", stored.python, current.python),
        ("environment", stored.environment, current.environment),
    ):
        if a != b:
            out.append(name)
    return out


# ---- inputs ----


def _worktree(root: Path, merge_base: str) -> dict[str, str]:
    top, changes = gitdiff.repository_changes(root, merge_base, _OWN_OUTPUT)
    out = {}
    for path, status in changes:
        if status == "?" and _is_bytecode(path):
            continue
        out[path] = f"{status}:{_DELETED if status == 'D' else _file_hash(top / path)}"
    return out


def _files_read(root: Path, paths: Iterable[str]) -> dict[str, str]:
    out: dict[str, str] = {}
    seen: set[str] = set()
    for rel in paths:
        path = root / rel
        files = _walk(root, path, seen) if path.is_dir() else [path] if path.exists() else []
        for file in files:
            key = _rel(root, file)
            if key not in _OWN_OUTPUT:
                out.setdefault(key, _file_hash(file))
    return out


def _walk(root: Path, top: Path, seen: set[str]) -> Iterable[Path]:
    """Files under ``top``, following directory links once, without bytecode or mutants."""
    mutants = os.path.realpath(root / "mutants")
    for dirpath, dirnames, filenames in os.walk(top, followlinks=True):
        real = os.path.realpath(dirpath)
        if real in seen or real == mutants:
            dirnames[:] = []
            continue
        seen.add(real)
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS)
        for name in sorted(filenames):
            if not name.endswith(".pyc"):
                yield Path(dirpath) / name


def _python() -> dict[str, Any]:
    package = Path(mutineer.__file__).parent
    files = {_rel(package, f): _file_hash(f) for f in _walk(package, package, set())}
    dists = sorted(
        f"{d.metadata['Name']}=={d.version}" for d in metadata.distributions() if d.metadata
    )
    return {
        "version": sys.version,
        "executable": sys.executable,
        "mutineer": mutineer.__version__,
        "package": _digest(files),
        "distributions": dists,
    }


# ---- mutmut's config, read as mutmut 3.8 reads it ----


def _mutmut_table(root: Path) -> dict[str, Any]:
    """``[tool.mutmut]`` from ``pyproject.toml``, else ``setup.cfg [mutmut]`` as lists.

    Lenient: an invalid file is left to the run, which fails on it.
    """
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        data = {}
    tool = data.get("tool", {})
    if isinstance(tool, dict) and isinstance(tool.get("mutmut"), dict):
        return dict(tool["mutmut"])
    parser = configparser.ConfigParser()
    try:
        parser.read(root / "setup.cfg", encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError):
        return {}
    if not parser.has_section("mutmut"):
        return {}
    return {k: [x for x in v.split("\n") if x] for k, v in parser.items("mutmut")}


def _mutmut_list(table: dict[str, Any], key: str) -> list[str]:
    value = table.get(key, [])
    if isinstance(value, str):
        return [value]
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _guess_source_paths(root: Path) -> list[str]:
    """mutmut's guess when no source paths are configured."""
    name = root.name
    candidates = [
        "lib",
        "src",
        name,
        name.replace("-", "_"),
        name.replace(" ", "_"),
        name.replace("-", ""),
        name.replace(" ", ""),
    ]
    for candidate in candidates:
        if (root / candidate).is_dir():
            return [candidate]
    return [name + ".py"] if (root / (name + ".py")).is_file() else []


# ---- helpers ----


def _file_hash(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        # a dangling link, a directory where a file was, or a file we can't read
        return f"unreadable:{type(exc).__name__}"


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_bytecode(path: str) -> bool:
    return path.endswith(".pyc") or "__pycache__/" in f"/{path}"


__all__ = ["ENV_VARS", "FULL", "NO_MUTATION", "Fingerprint", "compute", "differences"]
