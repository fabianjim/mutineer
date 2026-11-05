"""Static test analysis (build spec section 8, WP1): which tests exist, which are rule tests.

This is the *static* source of rule-test identity (section 3 item 8). It reads source
text only, so it works on the base version from git without running anything.

A test is a ``test_*`` function, or a ``test_*`` method of a ``Test*`` class (nested
classes too), in a ``test_*.py`` / ``*_test.py`` file under one of the test paths. Tests
are keyed by ``file::qualname`` where ``qualname`` joins classes and the function with
``::``, as in a pytest node id without parametrize brackets.

A test is a rule test if ``pytest.mark.rule(...)`` (or ``mark.rule(...)`` after
``from pytest import mark``) appears on the function, on any enclosing class, in the
class body's ``pytestmark``, or in the module's ``pytestmark`` (a single mark, a list or
a tuple). Rule ids must be string literals.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Literal

import libcst as cst
from libcst.metadata import CodeRange, MetadataWrapper, PositionProvider

AssertionKind = Literal["assert", "call", "raises"]

_TEST_FILE_PATTERNS = ("test_*.py", "*_test.py")
_SKIP_MARKS = frozenset({"skip", "skipif", "xfail"})
_UNITTEST_SKIPS = frozenset(
    {"unittest.skip", "unittest.skipIf", "unittest.skipUnless", "unittest.expectedFailure"}
)
_SKIP_CALLS = frozenset({"pytest.skip", "pytest.xfail", "pytest.importorskip"})
_MOCK_ATTRS = frozenset(
    {"assert_any_call", "assert_has_calls", "assert_not_called", "call_args", "call_args_list"}
)
_MOCK_PREFIXES = ("assert_called", "assert_awaited", "assert_any_await", "assert_has_awaits")
_NOT_SOURCE_MODULES = ("pytest", "_pytest", "unittest", "mock")


class _Missing:
    """Marks an operand that is not a literal."""

    def __repr__(self) -> str:
        return "<not a literal>"


NOT_LITERAL = _Missing()


@dataclass(frozen=True)
class Located:
    """A piece of source with its 1-based line and stripped text."""

    line: int
    source: str
    key: str = ""
    """Normalised form, for comparing base and head."""


@dataclass(frozen=True)
class Assertion:
    """One assertion in a test, in source order.

    - ``assert`` statements: ``ops`` holds the comparison operators (empty for a bare
      truthiness check) and ``operands`` the compared expressions.
    - ``call``: an ``assert*`` call (``self.assertEqual``, ``mock.assert_called_with``);
      ``name`` is the method name and ``operands`` its arguments.
    - ``raises``: ``pytest.raises(X)`` or ``assertRaises*(X)``; ``operands[0]`` is X.
    """

    kind: AssertionKind
    line: int
    source: str
    key: str
    """Normalised full assertion (formatting-independent)."""
    name: str = ""
    ops: tuple[str, ...] = ()
    is_compare: bool = False
    is_and: bool = False
    operands: tuple[str, ...] = ()
    """Normalised operand expressions."""
    literals: tuple[object, ...] = ()
    """Literal value of each operand, or ``NOT_LITERAL``."""
    operand_sources: tuple[str, ...] = ()


@dataclass(frozen=True)
class TestFunc:
    file: str
    qualname: str
    line: int
    """The ``def`` line (decorators excluded)."""
    def_source: str
    rules: tuple[str, ...]
    """Sorted rule ids from every supported marker place."""
    rule_marks: tuple[Located, ...]
    skips: tuple[Located, ...]
    """Skip/xfail markers and skip calls that apply to this test."""
    assertions: tuple[Assertion, ...]
    params: Mapping[str, tuple[Located, ...]]
    """``parametrize`` argnames -> cases (when argvalues is a literal list or tuple)."""
    pins: tuple[Located, ...]
    """Private names from source modules, and mock-call assertions."""
    body_key: str
    """Normalised parameters and body, for spotting a moved test."""

    @property
    def key(self) -> str:
        return f"{self.file}::{self.qualname}"


@dataclass
class _Context:
    """What the module's imports tell us about names."""

    pytest_names: set[str] = field(default_factory=lambda: {"pytest"})
    from_pytest: dict[str, str] = field(default_factory=dict)
    unittest_names: set[str] = field(default_factory=lambda: {"unittest"})
    from_unittest: dict[str, str] = field(default_factory=dict)
    private_imports: set[str] = field(default_factory=set)
    """Local names bound by ``from <source module> import _name``."""
    source_names: set[str] = field(default_factory=set)
    """Local names bound by importing a source module or something from one."""

    def canonical(self, dotted: str | None) -> str | None:
        """Rewrite a dotted name so pytest/unittest aliases read as ``pytest.x``."""
        if dotted is None:
            return None
        head, _, rest = dotted.partition(".")
        tail = f".{rest}" if rest else ""
        if head in self.pytest_names:
            return f"pytest{tail}"
        if head in self.from_pytest:
            return f"pytest.{self.from_pytest[head]}{tail}"
        if head in self.unittest_names:
            return f"unittest{tail}"
        if head in self.from_unittest:
            return f"unittest.{self.from_unittest[head]}{tail}"
        return dotted


def is_test_file(path: str, test_paths: Sequence[str]) -> bool:
    """A ``test_*.py`` or ``*_test.py`` file under one of ``test_paths``."""
    p = PurePosixPath(path)
    if not any(fnmatch.fnmatchcase(p.name, pat) for pat in _TEST_FILE_PATTERNS):
        return False
    return is_under(path, test_paths)


def is_under(path: str, test_paths: Sequence[str]) -> bool:
    for tp in test_paths:
        tp = tp.strip("/").removeprefix("./")
        if tp in ("", ".") or path == tp or path.startswith(tp + "/"):
            return True
    return False


def scan_source(text: str, path: str) -> list[TestFunc]:
    """Every test in ``text`` (the contents of ``path``). Raises ``SyntaxError`` if unparsable."""
    try:
        module = cst.parse_module(text)
    except cst.ParserSyntaxError as exc:
        raise SyntaxError(f"{path}: {exc.message}") from exc
    wrapper = MetadataWrapper(module, unsafe_skip_copy=True)
    positions = wrapper.resolve(PositionProvider)
    ctx = _read_imports(module)
    scanner = _ModuleScanner(module, path, positions, ctx)
    module.visit(scanner)
    return scanner.tests


# ---- normalisation helpers ----


def normalise(code: str, mode: Literal["exec", "eval"] = "eval") -> str:
    """Formatting-independent form of ``code``: its AST dump, or collapsed whitespace."""
    try:
        return ast.dump(ast.parse(code.strip(), mode=mode))
    except SyntaxError:
        return re.sub(r"\s+", " ", code).strip()


def _literal(code: str) -> object:
    try:
        value = ast.literal_eval(code.strip())
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return NOT_LITERAL
    return value


def _dotted(node: cst.BaseExpression) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        inner = _dotted(node.value)
        return f"{inner}.{node.attr.value}" if inner else None
    return None


def _module_dotted(node: cst.BaseExpression | cst.ImportFrom) -> str:
    if isinstance(node, cst.ImportFrom):
        dots = "." * len(node.relative)
        mod = _dotted(node.module) if node.module is not None else ""
        return f"{dots}{mod or ''}"
    return _dotted(node) or ""


def _is_source_module(module: str) -> bool:
    if module.startswith("."):
        return False  # relative imports from tests are test helpers
    top = module.split(".")[0]
    if top in _NOT_SOURCE_MODULES:
        return False
    last = module.split(".")[-1]
    return not (top in ("tests", "test", "conftest") or last.startswith("test_"))


def _is_private(name: str) -> bool:
    return name.startswith("_") and not name.startswith("__")


def _read_imports(module: cst.Module) -> _Context:
    ctx = _Context()

    class _Imports(cst.CSTVisitor):
        def visit_Import(self, node: cst.Import) -> None:
            for alias in node.names:
                full = _dotted(alias.name) or ""
                local = (
                    alias.asname.name.value  # type: ignore[union-attr]
                    if alias.asname
                    else full.split(".")[0]
                )
                if full == "pytest":
                    ctx.pytest_names.add(local)
                elif full == "unittest":
                    ctx.unittest_names.add(local)
                elif _is_source_module(full):
                    ctx.source_names.add(local)

        def visit_ImportFrom(self, node: cst.ImportFrom) -> None:
            mod = _module_dotted(node)
            if isinstance(node.names, cst.ImportStar):
                return
            for alias in node.names:
                name = _dotted(alias.name) or ""
                local = (
                    alias.asname.name.value  # type: ignore[union-attr]
                    if alias.asname
                    else name
                )
                if mod == "pytest":
                    ctx.from_pytest[local] = name
                elif mod == "unittest":
                    ctx.from_unittest[local] = name
                elif _is_source_module(mod):
                    ctx.source_names.add(local)
                    if _is_private(name):
                        ctx.private_imports.add(local)

    module.visit(_Imports())
    return ctx


# ---- module structure ----


@dataclass(frozen=True)
class _Marks:
    rules: tuple[Located, ...] = ()
    rule_ids: tuple[str, ...] = ()
    skips: tuple[Located, ...] = ()

    def __add__(self, other: _Marks) -> _Marks:
        return _Marks(
            self.rules + other.rules, self.rule_ids + other.rule_ids, self.skips + other.skips
        )


class _Base(cst.CSTVisitor):
    def __init__(
        self,
        module: cst.Module,
        positions: Mapping[cst.CSTNode, CodeRange],
        ctx: _Context,
    ) -> None:
        self.module = module
        self.positions = positions
        self.ctx = ctx

    def line(self, node: cst.CSTNode) -> int:
        return self.positions[node].start.line

    def code(self, node: cst.CSTNode) -> str:
        return self.module.code_for_node(node).strip()

    def located(self, node: cst.CSTNode, mode: Literal["exec", "eval"] = "eval") -> Located:
        code = self.code(node)
        return Located(self.line(node), code, normalise(code, mode))

    def marks_in(self, expr: cst.BaseExpression, at: cst.CSTNode | None = None) -> _Marks:
        """Rule and skip marks in a decorator or a ``pytestmark`` value."""
        if isinstance(expr, (cst.List, cst.Tuple)):
            total = _Marks()
            for el in expr.elements:
                total = total + self.marks_in(el.value, at)
            return total
        func = expr.func if isinstance(expr, cst.Call) else expr
        name = self.ctx.canonical(_dotted(func))
        if name is None:
            return _Marks()
        where = at if at is not None else expr
        here = Located(self.line(where), self.code(where), normalise(self.code(expr)))
        if name == "pytest.mark.rule" and isinstance(expr, cst.Call):
            ids = []
            for arg in expr.args:
                if arg.keyword is None:
                    value = _literal(self.code(arg.value))
                    if isinstance(value, str):
                        ids.append(value)
            return _Marks(rules=(here,), rule_ids=tuple(ids))
        if (
            name.startswith("pytest.mark.") and name.removeprefix("pytest.mark.") in _SKIP_MARKS
        ) or name in _UNITTEST_SKIPS:
            return _Marks(skips=(here,))
        return _Marks()

    def decorator_marks(self, decorators: Sequence[cst.Decorator]) -> _Marks:
        total = _Marks()
        for dec in decorators:
            total = total + self.marks_in(dec.decorator, dec)
        return total

    def pytestmark_in(self, body: Sequence[cst.CSTNode]) -> _Marks:
        total = _Marks()
        for stmt in body:
            if not isinstance(stmt, cst.SimpleStatementLine):
                continue
            for small in stmt.body:
                value: cst.BaseExpression | None = None
                if (
                    isinstance(small, cst.Assign)
                    and any(
                        isinstance(t.target, cst.Name) and t.target.value == "pytestmark"
                        for t in small.targets
                    )
                ) or (
                    isinstance(small, cst.AnnAssign)
                    and isinstance(small.target, cst.Name)
                    and small.target.value == "pytestmark"
                ):
                    value = small.value
                if value is not None:
                    total = total + self.marks_in(value, stmt)
        return total


class _ModuleScanner(_Base):
    def __init__(
        self,
        module: cst.Module,
        path: str,
        positions: Mapping[cst.CSTNode, CodeRange],
        ctx: _Context,
    ) -> None:
        super().__init__(module, positions, ctx)
        self.path = path
        self.tests: list[TestFunc] = []
        self.module_marks = self.pytestmark_in(module.body)
        self.classes: list[tuple[str, _Marks]] = []

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        body = node.body.body if isinstance(node.body, cst.IndentedBlock) else ()
        marks = self.decorator_marks(node.decorators) + self.pytestmark_in(body)
        self.classes.append((node.name.value, marks))
        return True

    def leave_ClassDef(self, original_node: cst.ClassDef) -> None:
        self.classes.pop()

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        name = node.name.value
        in_test_classes = all(cls.startswith("Test") for cls, _ in self.classes)
        if name.startswith("test") and in_test_classes:
            self.tests.append(self._test(node))
        return False  # nothing inside a function is collected as a test

    def _test(self, node: cst.FunctionDef) -> TestFunc:
        marks = self.module_marks
        for _, cls_marks in self.classes:
            marks = marks + cls_marks
        marks = marks + self.decorator_marks(node.decorators)
        body = _BodyScanner(self.module, self.positions, self.ctx)
        node.body.visit(body)
        skips = marks.skips + tuple(body.skip_calls)
        qualname = "::".join([*(cls for cls, _ in self.classes), node.name.value])
        def_line = self.line(node)
        def_source = self.module.code.splitlines()[def_line - 1].strip()
        bare = node.with_changes(decorators=[], name=cst.Name("_"), leading_lines=[])
        return TestFunc(
            file=self.path,
            qualname=qualname,
            line=def_line,
            def_source=def_source,
            rules=tuple(sorted(set(marks.rule_ids))),
            rule_marks=marks.rules,
            skips=skips,
            assertions=tuple(body.assertions),
            params=self._params(node.decorators),
            pins=tuple(body.pins),
            body_key=normalise(cst.Module(body=[bare]).code, "exec"),
        )

    def _params(self, decorators: Sequence[cst.Decorator]) -> dict[str, tuple[Located, ...]]:
        out: dict[str, tuple[Located, ...]] = {}
        for dec in decorators:
            expr = dec.decorator
            if not isinstance(expr, cst.Call):
                continue
            if self.ctx.canonical(_dotted(expr.func)) != "pytest.mark.parametrize":
                continue
            positional = [a for a in expr.args if a.keyword is None]
            keywords = {a.keyword.value: a for a in expr.args if a.keyword is not None}
            names = positional[0] if positional else keywords.get("argnames")
            values = positional[1] if len(positional) > 1 else keywords.get("argvalues")
            if names is None or values is None:
                continue
            if not isinstance(values.value, (cst.List, cst.Tuple)):
                continue
            key = normalise(self.code(names.value))
            out[key] = tuple(self.located(el.value) for el in values.value.elements)
        return out


class _BodyScanner(_Base):
    """Assertions, skip calls and implementation-pinning accesses in a test body."""

    def __init__(
        self,
        module: cst.Module,
        positions: Mapping[cst.CSTNode, CodeRange],
        ctx: _Context,
    ) -> None:
        super().__init__(module, positions, ctx)
        self.assertions: list[Assertion] = []
        self.skip_calls: list[Located] = []
        self.pins: list[Located] = []
        self._assert_depth = 0

    # assert statements
    def visit_Assert(self, node: cst.Assert) -> None:
        self._assert_depth += 1
        test = node.test
        code = self.code(node)
        if isinstance(test, cst.Comparison):
            exprs = [test.left, *(c.comparator for c in test.comparisons)]
            ops = tuple(type(c.operator).__name__ for c in test.comparisons)
        else:
            exprs = [test]
            ops = ()
        is_and = isinstance(test, cst.BooleanOperation) and isinstance(test.operator, cst.And)
        self.assertions.append(self._assertion("assert", node, code, exprs, ops=ops, is_and=is_and))

    def leave_Assert(self, original_node: cst.Assert) -> None:
        self._assert_depth -= 1

    # calls: assert*, raises, skip
    def visit_Call(self, node: cst.Call) -> None:
        dotted = _dotted(node.func)
        name = self.ctx.canonical(dotted)
        if name is None:
            if isinstance(node.func, cst.Attribute):
                name = node.func.attr.value
            else:
                return
        last = name.rsplit(".", 1)[-1]
        args = [a.value for a in node.args if a.keyword is None and not a.star]
        if name == "pytest.raises" or last.startswith("assertRaises"):
            self.assertions.append(
                self._assertion("raises", node, self.code(node), args[:1], name=last)
            )
        elif last.startswith("assert") and self._assert_depth == 0:
            self.assertions.append(self._assertion("call", node, self.code(node), args, name=last))
        if name in _SKIP_CALLS or last == "skipTest":
            self.skip_calls.append(self.located(node))

    # pins
    def visit_Attribute(self, node: cst.Attribute) -> None:
        attr = node.attr.value
        if attr in _MOCK_ATTRS or attr.startswith(_MOCK_PREFIXES):
            self.pins.append(self.located(node))
            return
        if _is_private(attr):
            root = node.value
            while isinstance(root, cst.Attribute):
                root = root.value
            if isinstance(root, cst.Name) and root.value in self.ctx.source_names:
                self.pins.append(self.located(node))

    def visit_Name(self, node: cst.Name) -> None:
        if node.value in self.ctx.private_imports:
            self.pins.append(self.located(node))

    def _assertion(
        self,
        kind: AssertionKind,
        node: cst.CSTNode,
        code: str,
        exprs: Sequence[cst.BaseExpression],
        *,
        name: str = "",
        ops: tuple[str, ...] = (),
        is_and: bool = False,
    ) -> Assertion:
        sources = tuple(self.code(e) for e in exprs)
        mode: Literal["exec", "eval"] = "exec" if kind == "assert" else "eval"
        return Assertion(
            kind=kind,
            line=self.line(node),
            source=code,
            key=normalise(code, mode),
            name=name,
            ops=ops,
            is_compare=bool(ops),
            is_and=is_and,
            operands=tuple(normalise(s) for s in sources),
            literals=tuple(_literal(s) for s in sources),
            operand_sources=sources,
        )
