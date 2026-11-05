"""Diff -> mutmut function names and unmeasured lines (engine/functions.py)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from mutineer.engine.functions import (
    ChangedCode,
    analyze,
    demote_unmutated,
    function_name,
    function_refs,
    module_name,
)
from mutineer.models import ChangedFile, Diff, Hunk

SRC = "src/pkg/mod.py"


def run(
    tmp_path: Path,
    text: str,
    hunks: list[tuple[int, int]] | None = None,
    *,
    path: str = SRC,
    status: str = "modified",
    source_paths: tuple[str, ...] = ("src/pkg",),
    do_not_mutate: tuple[str, ...] = (),
    test_paths: tuple[str, ...] = ("tests",),
) -> ChangedCode:
    """Analyze one file. ``hunks=None`` marks every line changed."""
    text = textwrap.dedent(text)
    f = tmp_path / path
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    if hunks is None:
        hunks = [(1, text.count("\n"))]
    cf = ChangedFile(
        path=path,
        status=status,  # type: ignore[arg-type]
        hunks=[Hunk(start=s, length=n) for s, n in hunks],
    )
    diff = Diff(base="0" * 40, head="WORKTREE", files=[cf])
    return analyze(
        diff,
        tmp_path,
        source_paths=list(source_paths),
        do_not_mutate=list(do_not_mutate),
        test_paths=list(test_paths),
    )


def names(code: ChangedCode) -> list[str]:
    return [f.ref.mutmut_name for f in code.functions]


def unmeasured(code: ChangedCode) -> list[tuple[int | None, int | None, str]]:
    return [(u.line, u.end_line, u.reason) for u in code.unmeasured]


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/shop/pricing.py", "shop.pricing"),
        ("src/shop/__init__.py", "shop"),
        ("lib/foo/bar.py", "lib.foo.bar"),
        ("pkg/sub/__init__.py", "pkg.sub"),
        ("tool.py", "tool"),
    ],
)
def test_module_name_follows_mutmut(path: str, expected: str) -> None:
    assert module_name(path) == expected


def test_function_names() -> None:
    assert function_name("shop.cart", "Cart.add") == "shop.cart.xǁCartǁadd"
    assert function_name("shop.pricing", "discount") == "shop.pricing.x_discount"


def test_changed_function_and_method(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        """\
        def f(x):
            return x + 1


        class Cart:
            def add(self, n):
                return n * 2
        """,
        [(2, 1), (7, 1)],
    )
    assert names(code) == ["pkg.mod.x_f", "pkg.mod.xǁCartǁadd"]
    f, add = (c.ref for c in code.functions)
    assert (f.qualname, f.start_line, f.end_line, f.file) == ("f", 1, 2, SRC)
    assert (add.qualname, add.start_line, add.end_line) == ("Cart.add", 6, 7)
    assert code.unmeasured == []
    assert code.files == [SRC]


def test_unchanged_functions_are_not_reported(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        """\
        def a():
            return 1 + 1


        def b():
            return 2 + 2
        """,
        [(5, 2)],
    )
    assert names(code) == ["pkg.mod.x_b"]


def test_nested_function_maps_to_outermost(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        """\
        def outer(x):
            def inner(y):
                return y * 2

            return inner(x) + 1
        """,
        [(3, 1)],
    )
    assert names(code) == ["pkg.mod.x_outer"]


def test_async_and_generator_functions(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        """\
        async def coro(x):
            return x * 2


        def gen(x):
            yield x + 1
        """,
    )
    assert names(code) == ["pkg.mod.x_coro", "pkg.mod.x_gen"]


def test_lone_staticmethod_and_classmethod_are_mutable(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        """\
        class Box:
            @staticmethod
            def stat(x):
                return x - 2

            @classmethod
            def make(cls, x):
                return cls(x + 5)
        """,
    )
    assert names(code) == ["pkg.mod.xǁBoxǁstat", "pkg.mod.xǁBoxǁmake"]
    assert code.functions[0].ref.start_line == 3  # the def line, decorator excluded


def test_decorated_functions_are_unmeasured(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        """\
        import functools


        class Box:
            @property
            def total(self):
                return 1 + 1


        @functools.lru_cache
        def cached(x):
            return x + 3


        @staticmethod
        @functools.cache
        def stacked(x):
            return x
        """,
        [(7, 1), (10, 3), (15, 1)],
    )
    assert names(code) == []
    assert unmeasured(code) == [(7, 7, "decorated"), (10, 12, "decorated"), (15, 15, "decorated")]


def test_module_and_class_body_code_is_module_level(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        '''\
        """Module docstring."""

        # a comment
        LIMIT = 10


        class Box:
            """Class docstring."""

            size = 3 + 4

            class Inner:
                def m(self):
                    return 1 + 1


        if LIMIT > 1:

            def conditional(x):
                return x + 9
        ''',
    )
    assert names(code) == []
    assert unmeasured(code) == [
        (4, 4, "module_level"),
        (7, 7, "module_level"),
        (10, 10, "module_level"),
        (12, 14, "module_level"),
        (17, 17, "module_level"),
        (19, 20, "module_level"),
    ]


def test_blank_comment_and_docstring_lines_are_ignored(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        '''\
        X = 1


        def f():
            """Docstring
            over two lines."""
            # just a comment
            return X + 1
        ''',
        [(2, 2), (5, 3)],
    )
    assert code.functions == [] and code.unmeasured == []


def test_multiline_string_that_is_code_counts(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        '''\
        def f():
            x = 1
            return """a
        b"""
        ''',
        [(4, 1)],
    )
    assert names(code) == ["pkg.mod.x_f"]


def test_pure_deletion_inside_function_changes_it(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        """\
        X = 1


        def f(x):
            y = x + 1
            return y
        """,
        [(6, 0), (2, 0)],  # a line removed after line 5 (in f), and one after line 1
    )
    assert names(code) == ["pkg.mod.x_f"]
    assert code.unmeasured == []


def test_unparsable_file_is_unmeasured(tmp_path: Path) -> None:
    code = run(tmp_path, "def f(:\n    return 1\n", [(1, 2)])
    assert names(code) == []
    assert unmeasured(code) == [(1, 2, "no_mutants")]


def test_file_outside_source_paths(tmp_path: Path) -> None:
    code = run(tmp_path, "X = 1\n\n\ndef f():\n    return 2\n", path="scripts/tool.py")
    assert names(code) == []
    assert unmeasured(code) == [(1, 1, "outside_source_paths"), (4, 5, "outside_source_paths")]
    assert code.unmeasured[0].file == "scripts/tool.py"


def test_tests_and_non_python_files_are_skipped(tmp_path: Path) -> None:
    assert run(tmp_path, "def test_x():\n    assert 1\n", path="tests/test_x.py") == ChangedCode()
    assert run(tmp_path, "x = 1\n", path="src/pkg/data.txt") == ChangedCode()
    # tests inside a source path still aren't source
    code = run(
        tmp_path, "def test_x():\n    assert 1\n", path="tests/test_y.py", source_paths=(".",)
    )
    assert code == ChangedCode()


def test_do_not_mutate(tmp_path: Path) -> None:
    code = run(tmp_path, "def f():\n    return 1 + 1\n", do_not_mutate=("src/pkg/m*.py",))
    assert names(code) == []
    assert unmeasured(code) == [(1, 2, "excluded_by_config")]


def test_deleted_files_are_skipped(tmp_path: Path) -> None:
    cf = ChangedFile(path="src/pkg/gone.py", status="deleted", hunks=[Hunk(start=1, length=0)])
    diff = Diff(base="0" * 40, head="WORKTREE", files=[cf])
    code = analyze(diff, tmp_path, source_paths=["src/pkg"], do_not_mutate=[], test_paths=[])
    assert code == ChangedCode()


def test_init_module_name(tmp_path: Path) -> None:
    code = run(tmp_path, "def f():\n    return 1 + 1\n", path="src/pkg/__init__.py")
    assert names(code) == ["pkg.x_f"]


def test_demote_unmutated_moves_functions_to_no_mutants(tmp_path: Path) -> None:
    code = run(
        tmp_path,
        """\
        X = 1


        def noop():
            pass


        def real(x):
            return x + 1
        """,
    )
    assert names(code) == ["pkg.mod.x_noop", "pkg.mod.x_real"]
    demoted = demote_unmutated(code, {"pkg.mod.x_real"})
    assert names(demoted) == ["pkg.mod.x_real"]
    assert unmeasured(demoted) == [(1, 1, "module_level"), (4, 5, "no_mutants")]


def test_function_refs_lists_structure(tmp_path: Path) -> None:
    refs = function_refs(
        "src/pkg/mod.py",
        "class A:\n    @property\n    def p(self):\n        return 1\n\n\ndef f():\n    pass\n",
    )
    assert sorted(refs) == ["A.p", "f"]
    assert refs["A.p"].mutmut_name == "pkg.mod.xǁAǁp"
    assert (refs["A.p"].start_line, refs["A.p"].end_line) == (3, 4)
