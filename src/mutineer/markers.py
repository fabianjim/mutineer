"""Adding ``@pytest.mark.rule("<id>")`` to a test, for ``mutineer rules accept``.

The edit goes through libcst, so everything else in the file stays byte for byte. The
module's own spelling of pytest is reused (``pytest``, an ``import pytest as pt`` alias,
or ``mark`` after ``from pytest import mark``); a module that doesn't import pytest gets
``import pytest``. The result is checked with ``testscan.scan_source``, the same static
reading tamper detection uses.
"""

from __future__ import annotations

from collections.abc import Sequence

import libcst as cst
from libcst.codemod import CodemodContext
from libcst.codemod.visitors import AddImportsVisitor

from mutineer.errors import RulesError
from mutineer.testscan import TestFunc, pytest_imports, scan_source


def add_rule_marker(source: str, qualname: str, rule_id: str, *, path: str = "<test>") -> str:
    """``source`` with ``rule_id`` marked on the test ``qualname`` (``Class::test_x``).

    Unchanged when the id already applies to the test (on the function, its class or the
    module ``pytestmark``). The new decorator goes directly above the ``def``, above the
    other decorators. Raises ``RulesError`` when the test isn't in ``source`` or the marked
    text doesn't read as marked.
    """
    test = _find(scan_source(source, path), qualname, path)
    if rule_id in test.rules:
        return source

    module = cst.parse_module(source)
    prefix = _mark_prefix(module)
    if prefix is None:
        context = CodemodContext()
        AddImportsVisitor.add_needed_import(context, "pytest")
        module = AddImportsVisitor(context).transform_module(module)
        prefix = "pytest.mark"
    decorator = cst.Decorator(decorator=cst.parse_expression(f'{prefix}.rule("{rule_id}")'))
    marked = module.visit(_AddDecorator(qualname.split("::"), decorator))
    result = marked.code

    if rule_id not in _find(scan_source(result, path), qualname, path).rules:
        raise RulesError(
            f"{path}::{qualname}: the {rule_id} mark could not be added; add it by hand"
        )
    return result


def _find(tests: Sequence[TestFunc], qualname: str, path: str) -> TestFunc:
    for test in tests:
        if test.qualname == qualname:
            return test
    raise RulesError(f"{path}::{qualname} is not a test function in {path}")


def _mark_prefix(module: cst.Module) -> str | None:
    """How the module spells ``pytest.mark``, or None when it doesn't import pytest."""
    imports = pytest_imports(module)
    if "pytest" in imports.modules:
        return "pytest.mark"
    if imports.modules:
        return f"{imports.modules[0]}.mark"
    for local, name in imports.names.items():
        if name == "mark":
            return local
    return None


class _AddDecorator(cst.CSTTransformer):
    """Puts ``decorator`` first on the function at ``path`` (classes, then the function)."""

    def __init__(self, path: list[str], decorator: cst.Decorator) -> None:
        self.path = path
        self.decorator = decorator
        self.stack: list[str] = []

    def visit_ClassDef(self, node: cst.ClassDef) -> bool:
        self.stack.append(node.name.value)
        return True

    def leave_ClassDef(
        self, original_node: cst.ClassDef, updated_node: cst.ClassDef
    ) -> cst.ClassDef:
        self.stack.pop()
        return updated_node

    def visit_FunctionDef(self, node: cst.FunctionDef) -> bool:
        return False  # tests are never nested in functions

    def leave_FunctionDef(
        self, original_node: cst.FunctionDef, updated_node: cst.FunctionDef
    ) -> cst.FunctionDef:
        if [*self.stack, updated_node.name.value] != self.path:
            return updated_node
        return updated_node.with_changes(decorators=[self.decorator, *updated_node.decorators])
