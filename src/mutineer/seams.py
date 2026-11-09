"""Static check that a rule's seam (a dotted path) names something in the project.

It never imports or runs user code. The longest dotted prefix that is a module file
(``a/b.py`` or ``a/b/__init__.py``) is looked up under the project root, ``src/`` and the
parent of each mutmut ``source_paths`` entry. The rest of the path is resolved with
``ast``: a module-level ``def``, ``class``, assignment or import binds a name, and a class
body binds its methods and attributes. Imports are not followed.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator, Sequence
from pathlib import Path

from mutineer.rules import mutmut_source_paths

_Scope = Sequence[ast.stmt]
_BLOCKS = (ast.If, ast.Try, ast.TryStar, ast.With, ast.AsyncWith)


def seam_problem(root: Path, seam: str) -> str | None:
    """One line saying why ``seam`` doesn't resolve, or None when it does."""
    parts = seam.split(".")
    if not all(part.isidentifier() for part in parts):
        return f"seam {seam!r} must be a dotted path such as shop.shipping.quote"
    bases = _search_dirs(root)
    found = _longest_module(bases, parts)
    if found is None:
        where = ", ".join(_show(root, b) for b in bases)
        return f"seam {seam}: no module {parts[0]!r} found under {where}"
    path, used = found
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, ValueError):
        return f"seam {seam}: {_show(root, path)} can't be parsed"
    scope: _Scope | None = tree.body
    owner = ".".join(parts[:used])
    for part in parts[used:]:
        if scope is None:
            return (
                f"seam {seam}: {part!r} can't be found, {owner} is not a class defined there; "
                "point the seam at where it is defined"
            )
        if part not in set(_names(scope)):
            return f"seam {seam}: {part!r} not found in {owner} ({_show(root, path)})"
        scope = _class_body(scope, part)
        owner = f"{owner}.{part}"
    return None


def _search_dirs(root: Path) -> list[Path]:
    dirs = [root, root / "src"]
    for entry in mutmut_source_paths(root):
        dirs.append((root / entry).parent)
    unique: list[Path] = []
    for d in dirs:
        resolved = d.resolve()
        if resolved not in unique:
            unique.append(resolved)
    return unique


def _show(root: Path, path: Path) -> str:
    try:
        rel = path.relative_to(root.resolve())
    except ValueError:
        return str(path)
    return rel.as_posix() if rel.parts else "."


def _longest_module(bases: Sequence[Path], parts: Sequence[str]) -> tuple[Path, int] | None:
    for used in range(len(parts), 0, -1):
        for base in bases:
            stem = base.joinpath(*parts[:used])
            for candidate in (stem.with_name(stem.name + ".py"), stem / "__init__.py"):
                if candidate.is_file():
                    return candidate, used
    return None


def _names(scope: _Scope) -> Iterator[str]:
    """Names bound directly in a module or class body."""
    for stmt in _statements(scope):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield stmt.name
        elif isinstance(stmt, ast.Assign):
            for target in stmt.targets:
                yield from _target_names(target)
        elif isinstance(stmt, (ast.AnnAssign, ast.AugAssign)):
            yield from _target_names(stmt.target)
        elif isinstance(stmt, ast.Import):
            for alias in stmt.names:
                yield alias.asname or alias.name.split(".")[0]
        elif isinstance(stmt, ast.ImportFrom):
            for alias in stmt.names:
                if alias.name != "*":
                    yield alias.asname or alias.name


def _statements(scope: _Scope) -> Iterator[ast.stmt]:
    """Statements of a body, including those nested in ``if``, ``try`` and ``with``."""
    for stmt in scope:
        yield stmt
        if isinstance(stmt, ast.If):
            yield from _statements(stmt.body)
            yield from _statements(stmt.orelse)
        elif isinstance(stmt, (ast.Try, ast.TryStar)):
            yield from _statements(stmt.body)
            for handler in stmt.handlers:
                yield from _statements(handler.body)
            yield from _statements(stmt.orelse)
            yield from _statements(stmt.finalbody)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            yield from _statements(stmt.body)


def _target_names(target: ast.expr) -> Iterator[str]:
    if isinstance(target, ast.Name):
        yield target.id
    elif isinstance(target, (ast.Tuple, ast.List)):
        for el in target.elts:
            yield from _target_names(el)
    elif isinstance(target, ast.Starred):
        yield from _target_names(target.value)


def _class_body(scope: _Scope, name: str) -> _Scope | None:
    """The body of the last class ``name`` binds in ``scope``, or None if it isn't a class."""
    last: ast.stmt | None = None
    for stmt in _statements(scope):
        # a block's own statements are walked separately
        if not isinstance(stmt, _BLOCKS) and name in set(_names([stmt])):
            last = stmt
    return last.body if isinstance(last, ast.ClassDef) else None
