"""Map a diff to the functions mutmut will mutate, and to changed code it cannot mutate.

Pure source analysis: this module reads head files and never runs or imports mutmut.
What mutmut mutates (verified against mutmut 3.8.1, see ``docs/mutmut.md``):

- top-level functions, and methods defined directly in a top-level class body;
- not if decorated, except by a lone bare ``@staticmethod`` or ``@classmethod``;
- nested functions are mutated as part of their outermost function;
- nothing else: module code, class bodies, methods of nested classes, functions defined
  under ``if``/``try`` at module level.

Mutant names: ``<module>.x_<func>`` or ``<module>.xǁ<Class>ǁ<method>``, where ``<module>``
is the file path relative to the project root, dotted, with a leading ``src.`` removed
and ``.__init__`` dropped (mutmut's ``get_mutant_name``).

Some structurally mutable functions still get no mutants (nothing mutable in them,
names mutmut never mutates, pragmas). Only mutmut can tell; the engine asks it through
``_launch`` and demotes them with :func:`demote_unmutated`.
"""

from __future__ import annotations

import fnmatch
import io
import tokenize
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal

import libcst as cst
from libcst.metadata import CodeRange, MetadataWrapper, PositionProvider

from mutineer.models import ChangedFile, Diff, FunctionRef, UnmeasuredLocation

Reason = Literal[
    "decorated", "module_level", "outside_source_paths", "excluded_by_config", "no_mutants"
]

CLASS_SEPARATOR = "ǁ"  # U+01C1, mutmut's method separator
_MUTABLE_DECORATORS = frozenset({"staticmethod", "classmethod"})


@dataclass(frozen=True)
class ChangedFunction:
    """A changed function mutmut may mutate, with the changed head lines inside it."""

    ref: FunctionRef
    changed_lines: tuple[int, ...]


@dataclass
class ChangedCode:
    functions: list[ChangedFunction] = field(default_factory=list)
    unmeasured: list[UnmeasuredLocation] = field(default_factory=list)

    @property
    def files(self) -> list[str]:
        """Source files holding at least one changed function, sorted."""
        return sorted({f.ref.file for f in self.functions})


def module_name(path: str) -> str:
    """mutmut's module name for a source file path relative to the project root."""
    p = PurePosixPath(path)
    dotted = ".".join(p.with_suffix("").parts)
    dotted = dotted.removeprefix("src.")
    if dotted.endswith(".__init__"):
        dotted = dotted[: -len(".__init__")]
    return dotted


def function_name(module: str, qualname: str) -> str:
    """mutmut's mangled name for ``qualname`` ("f" or "Class.method") in ``module``."""
    if "." in qualname:
        cls, meth = qualname.split(".", 1)
        return f"{module}.x{CLASS_SEPARATOR}{cls}{CLASS_SEPARATOR}{meth}"
    return f"{module}.x_{qualname}"


def under(path: str, roots: Iterable[str]) -> bool:
    """Whether repo-relative ``path`` is one of ``roots`` or inside one of them."""
    p = PurePosixPath(path)
    for root in roots:
        r = PurePosixPath(Path(root).as_posix())
        if str(r) in ("", "."):
            return True
        if p == r or r in p.parents:
            return True
    return False


def analyze(
    diff: Diff,
    root: Path,
    *,
    source_paths: Sequence[str],
    do_not_mutate: Sequence[str],
    test_paths: Sequence[str],
) -> ChangedCode:
    """Changed functions mutmut can mutate, and changed code lines it cannot.

    Paths in ``diff`` are relative to ``root`` (mutmut's working directory). Files under
    ``test_paths`` are tests, even inside a source path, and are skipped, as are
    non-``.py`` files. Blank lines,
    comment-only lines and docstrings never count as changes.
    """
    out = ChangedCode()
    for cf in sorted(diff.files, key=lambda f: f.path):
        if cf.status == "deleted" or not cf.path.endswith(".py"):
            continue
        if under(cf.path, test_paths):
            continue
        source_file = root / cf.path
        if not source_file.is_file():
            continue
        text = source_file.read_text(encoding="utf-8")
        if not under(cf.path, source_paths):
            _add_ranges(
                out, cf.path, _code_lines_in(text, _touched_lines(cf)), "outside_source_paths"
            )
            continue
        if any(fnmatch.fnmatch(cf.path, pat) for pat in do_not_mutate):
            _add_ranges(
                out, cf.path, _code_lines_in(text, _touched_lines(cf)), "excluded_by_config"
            )
            continue
        _analyze_file(out, cf, text)
    return out


def demote_unmutated(code: ChangedCode, mutated: set[str]) -> ChangedCode:
    """Move functions mutmut generates no mutants for into ``unmeasured`` (``no_mutants``)."""
    out = ChangedCode(unmeasured=list(code.unmeasured))
    for fn in code.functions:
        if fn.ref.mutmut_name in mutated:
            out.functions.append(fn)
        else:
            _add_ranges(out, fn.ref.file, fn.changed_lines, "no_mutants")
    out.unmeasured.sort(key=lambda u: (u.file, u.line or 0))
    return out


def function_refs(path: str, text: str) -> dict[str, FunctionRef]:
    """Every top-level function and method in ``text`` (a head file at ``path``) by qualname.

    Decorated ones are included; this is structure only, not a mutability check.
    """
    try:
        wrapper = MetadataWrapper(cst.parse_module(text))
    except cst.ParserSyntaxError:
        return {}
    module = module_name(path)
    out: dict[str, FunctionRef] = {}
    for reg in _regions(wrapper.module, dict(wrapper.resolve(PositionProvider))):
        assert reg.qualname is not None
        out[reg.qualname] = FunctionRef(
            mutmut_name=function_name(module, reg.qualname),
            file=path,
            qualname=reg.qualname,
            start_line=reg.def_line,
            end_line=reg.last,
        )
    return out


# ---- internals ----


def _touched_lines(cf: ChangedFile) -> tuple[set[int], set[int]]:
    """(changed head lines, anchor lines of pure deletions)."""
    changed: set[int] = set()
    deletions: set[int] = set()
    for h in cf.hunks:
        if h.length == 0:
            if h.start > 1:
                deletions.add(h.start - 1)
        else:
            changed.update(range(h.start, h.start + h.length))
    return changed, deletions


def _code_lines_in(text: str, touched: tuple[set[int], set[int]]) -> list[int]:
    changed, _deletions = touched
    code = _code_lines(text)
    return sorted(line for line in changed if line in code)


def _code_lines(text: str) -> set[int]:
    """Lines holding real code: not blank, not comment-only, not part of a docstring."""
    lines: set[int] = set()
    skip = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENDMARKER,
        tokenize.ENCODING,
    }
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type not in skip:
                lines.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, SyntaxError, IndentationError):
        return set(range(1, text.count("\n") + 2))
    return lines - _docstring_lines(text)


def _docstring_lines(text: str) -> set[int]:
    try:
        wrapper = MetadataWrapper(cst.parse_module(text))
    except cst.ParserSyntaxError:
        return set()
    positions = wrapper.resolve(PositionProvider)
    found: set[int] = set()

    def first_docstring(body: Sequence[cst.CSTNode]) -> None:
        if not body:
            return
        stmt = body[0]
        if (
            isinstance(stmt, cst.SimpleStatementLine)
            and len(stmt.body) == 1
            and isinstance(stmt.body[0], cst.Expr)
            and isinstance(stmt.body[0].value, (cst.SimpleString, cst.ConcatenatedString))
        ):
            r = positions[stmt]
            found.update(range(r.start.line, r.end.line + 1))

    class Visitor(cst.CSTVisitor):
        def visit_Module(self, node: cst.Module) -> None:
            first_docstring(node.body)

        def visit_ClassDef(self, node: cst.ClassDef) -> None:
            if isinstance(node.body, cst.IndentedBlock):
                first_docstring(node.body.body)

        def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
            if isinstance(node.body, cst.IndentedBlock):
                first_docstring(node.body.body)

    wrapper.module.visit(Visitor())
    return found


@dataclass(frozen=True)
class _Region:
    """A top-level construct's lines and what a change there means."""

    first: int  # first line, decorators included
    last: int
    kind: Literal["function", "decorated", "module_level"]
    qualname: str | None = None
    def_line: int = 0


def _regions(module: cst.Module, positions: dict[cst.CSTNode, CodeRange]) -> list[_Region]:
    regions: list[_Region] = []

    def function_region(fn: cst.FunctionDef, qualname: str) -> _Region:
        r = positions[fn]
        first = min([positions[d].start.line for d in fn.decorators] + [r.start.line])
        kind: Literal["function", "decorated"] = "function" if _mutable(fn) else "decorated"
        return _Region(first, r.end.line, kind, qualname, r.start.line)

    for stmt in module.body:
        if isinstance(stmt, cst.FunctionDef):
            regions.append(function_region(stmt, stmt.name.value))
        elif isinstance(stmt, cst.ClassDef) and isinstance(stmt.body, cst.IndentedBlock):
            for child in stmt.body.body:
                if isinstance(child, cst.FunctionDef):
                    regions.append(function_region(child, f"{stmt.name.value}.{child.name.value}"))
    return regions


def _mutable(fn: cst.FunctionDef) -> bool:
    if not fn.decorators:
        return True
    if len(fn.decorators) != 1:
        return False
    dec = fn.decorators[0].decorator
    return isinstance(dec, cst.Name) and dec.value in _MUTABLE_DECORATORS


def _analyze_file(out: ChangedCode, cf: ChangedFile, text: str) -> None:
    changed, deletions = _touched_lines(cf)
    try:
        wrapper = MetadataWrapper(cst.parse_module(text))
    except cst.ParserSyntaxError:
        # mutmut skips files libcst cannot parse, so nothing here gets mutants
        _add_ranges(out, cf.path, sorted(changed), "no_mutants")
        return
    positions = wrapper.resolve(PositionProvider)
    regions = _regions(wrapper.module, dict(positions))
    code = _code_lines(text)
    module = module_name(cf.path)

    def region_of(line: int) -> _Region | None:
        for reg in regions:
            if reg.first <= line <= reg.last:
                return reg
        return None

    hits: dict[_Region, set[int]] = {}
    unmeasured: dict[Reason, list[int]] = {}
    for line in sorted(changed):
        if line not in code:
            continue
        reg = region_of(line)
        if reg is None:
            unmeasured.setdefault("module_level", []).append(line)
        elif reg.kind == "decorated":
            unmeasured.setdefault("decorated", []).append(line)
        else:
            hits.setdefault(reg, set()).add(line)
    # a pure deletion changes the function it was cut from; deleted module code leaves
    # nothing behind to measure, so it is not reported
    for line in sorted(deletions):
        reg = region_of(line)
        if reg is None:
            continue
        if reg.kind == "decorated":
            if line in code:
                unmeasured.setdefault("decorated", []).append(line)
        else:
            hits.setdefault(reg, set()).add(line)

    for reg, lines in sorted(hits.items(), key=lambda kv: kv[0].first):
        assert reg.qualname is not None
        ref = FunctionRef(
            mutmut_name=function_name(module, reg.qualname),
            file=cf.path,
            qualname=reg.qualname,
            start_line=reg.def_line,
            end_line=reg.last,
        )
        out.functions.append(ChangedFunction(ref=ref, changed_lines=tuple(sorted(lines))))
    for reason, reason_lines in unmeasured.items():
        _add_ranges(out, cf.path, reason_lines, reason)
    out.unmeasured.sort(key=lambda u: (u.file, u.line or 0))


def _add_ranges(out: ChangedCode, path: str, lines: Iterable[int], reason: Reason) -> None:
    """Append one location per run of consecutive lines."""
    run: list[int] = []
    for line in sorted(set(lines)):
        if run and line != run[-1] + 1:
            out.unmeasured.append(
                UnmeasuredLocation(file=path, line=run[0], end_line=run[-1], reason=reason)
            )
            run = []
        run.append(line)
    if run:
        out.unmeasured.append(
            UnmeasuredLocation(file=path, line=run[0], end_line=run[-1], reason=reason)
        )
