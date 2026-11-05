"""The diff Mutineer judges: merge-base to the working tree (build spec section 3 item 1).

Paths are relative to ``root`` (``git diff --relative``), so a project in a subdirectory
of a larger repository sees only its own files, with paths relative to itself.
"""

from __future__ import annotations

import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mutineer.errors import ConfigError, MutineerError
from mutineer.models import ChangedFile, Diff, Hunk, Rule

HEAD_WORKTREE = "WORKTREE"

# Paths Mutineer itself writes; never part of the change being judged.
_OWN_OUTPUT_DIRS = ("mutants/",)
_OWN_OUTPUT_FILES = (".mutineer/last-run.json",)

_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_STATUS = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "T": "modified"}


def resolve_base_ref(root: Path, base: str | None) -> str:
    """The base ref: ``base`` if given, else ``main`` if it exists, else ``master``.

    Callers pass ``--base`` or, failing that, ``[tool.mutineer] base``.
    """
    if base:
        if not _ref_exists(root, base):
            raise ConfigError(f"base ref '{base}' not found; pass an existing branch to --base")
        return base
    for candidate in ("main", "master"):
        if _ref_exists(root, candidate):
            return candidate
    raise ConfigError(
        "no 'main' or 'master' branch; pass --base or set [tool.mutineer] base in pyproject.toml"
    )


def merge_base(root: Path, base: str | None) -> str:
    """``git merge-base <base> HEAD`` as a full sha, with ``base`` resolved as above."""
    ref = resolve_base_ref(root, base)
    proc = _git(root, "merge-base", ref, "HEAD", check=False)
    if proc.returncode != 0:
        raise ConfigError(f"'{ref}' and HEAD share no history; pass a related branch to --base")
    return proc.stdout.strip()


def load_diff(root: Path, base: str | None, rules_file: str) -> Diff:
    """Changed files from the merge-base with ``base`` to the files on disk.

    Includes uncommitted edits and untracked files (reported as ``added``), because
    mutmut mutates files on disk. ``rules_file`` is relative to ``root``.
    """
    _check_repo(root)
    sha = merge_base(root, base)
    files = [f for f in _tracked_changes(root, sha) if not _is_own_output(f.path)]
    seen = {f.path for f in files}
    for path in _untracked(root):
        if path not in seen and not _is_own_output(path):
            files.append(_added_file(root, path))
    files.sort(key=lambda f: f.path)
    return Diff(
        base=sha,
        head=HEAD_WORKTREE,
        files=files,
        rules_file_changed_ids=_changed_rule_ids(root, sha, rules_file),
    )


def read_base_file(root: Path, sha: str, path: str) -> str | None:
    """Text of ``path`` (relative to ``root``) at commit ``sha``, or None if absent."""
    proc = _git(root, "show", f"{sha}:./{path}", check=False)
    return proc.stdout if proc.returncode == 0 else None


# ---- internals ----


def _tracked_changes(root: Path, sha: str) -> list[ChangedFile]:
    common = ("--relative", "-M", "--no-ext-diff", "--no-textconv", "--ignore-submodules")
    names = _git(root, "diff", "--name-status", "-z", *common, sha, "--").stdout
    entries = _parse_name_status(names)
    if not entries:
        return []
    patch = _git(root, "diff", "-U0", "--no-color", *common, sha, "--").stdout
    sections = _split_patch(patch)
    if len(sections) != len(entries):
        raise MutineerError(
            "could not match `git diff` output to changed files; report this as a Mutineer bug"
        )
    return [
        ChangedFile(path=path, old_path=old, status=status, hunks=_parse_hunks(section))
        for (status, path, old), section in zip(entries, sections, strict=True)
    ]


def _parse_name_status(out: str) -> list[tuple[str, str, str | None]]:
    """``git diff --name-status -z`` -> (status, head path, old path for renames)."""
    fields = out.split("\0")
    entries: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(fields) and fields[i]:
        code = fields[i][0]
        if code in ("R", "C"):
            old, new = fields[i + 1], fields[i + 2]
            i += 3
            if code == "C":
                entries.append(("added", new, None))
            else:
                entries.append(("renamed", new, old))
            continue
        status = _STATUS.get(code, "modified")
        entries.append((status, fields[i + 1], None))
        i += 2
    return entries


def _split_patch(patch: str) -> list[list[str]]:
    sections: list[list[str]] = []
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            sections.append([])
        elif sections:
            sections[-1].append(line)
    return sections


def _parse_hunks(lines: list[str]) -> list[Hunk]:
    hunks = []
    for line in lines:
        m = _HUNK_RE.match(line)
        if not m:
            continue
        start = int(m.group(1))
        length = 1 if m.group(2) is None else int(m.group(2))
        if length == 0:
            # Pure deletion: git reports the line before the deletion point.
            hunks.append(Hunk(start=start + 1, length=0))
        else:
            hunks.append(Hunk(start=start, length=length))
    return hunks


def _untracked(root: Path) -> list[str]:
    out = _git(root, "ls-files", "--others", "--exclude-standard", "-z").stdout
    return [p for p in out.split("\0") if p]


def _added_file(root: Path, path: str) -> ChangedFile:
    try:
        n = len((root / path).read_bytes().splitlines())
    except OSError:
        n = 0
    return ChangedFile(path=path, status="added", hunks=[Hunk(start=1, length=n)] if n else [])


def _is_own_output(path: str) -> bool:
    return path.startswith(_OWN_OUTPUT_DIRS) or path in _OWN_OUTPUT_FILES


def _changed_rule_ids(root: Path, sha: str, rules_file: str) -> list[str]:
    head_path = root / rules_file
    head_text = head_path.read_text(encoding="utf-8") if head_path.is_file() else None
    before = _rule_entries(read_base_file(root, sha, rules_file))
    after = _rule_entries(head_text)
    ids = set(before) | set(after)
    return sorted(i for i in ids if before.get(i) != after.get(i))


def _rule_entries(text: str | None) -> dict[str, Any]:
    """id -> normalised ``[[rule]]`` entry. Lenient: validation belongs to ``load_rules``."""
    if text is None:
        return {}
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}
    tables = data.get("rule", [])
    if not isinstance(tables, list):
        return {}
    entries: dict[str, Any] = {}
    for table in tables:
        if not isinstance(table, dict) or not isinstance(table.get("id"), str):
            continue
        try:
            entries[table["id"]] = Rule.model_validate(table).model_dump()
        except ValidationError:
            entries[table["id"]] = table
    return entries


def _check_repo(root: Path) -> None:
    proc = _git(root, "rev-parse", "--verify", "--quiet", "HEAD", check=False)
    if proc.returncode != 0:
        raise ConfigError(f"{root} is not a git repository with at least one commit; commit first")


def _ref_exists(root: Path, ref: str) -> bool:
    proc = _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}", check=False)
    return proc.returncode == 0


def _git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"}
    cmd = ["git", "-c", "core.quotepath=false", "-c", "diff.noprefix=false", *args]
    try:
        proc = subprocess.run(
            cmd,
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="surrogateescape",
        )
    except FileNotFoundError as exc:
        raise ConfigError("git is not installed or not on PATH; install git") from exc
    if check and proc.returncode != 0:
        detail = proc.stderr.strip().splitlines()
        raise ConfigError(f"git {args[0]} failed: {detail[-1] if detail else 'no output'}")
    return proc
