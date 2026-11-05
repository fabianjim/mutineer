"""Tiered tamper detection.

Compares each test at base (``git show``) and at head (the file on disk), keyed by
``file::qualname``, and judges by tier:

- rule tests (marked at base) give the ``rule_*`` kinds, BLOCK unless every rule the
  test guards changed in ``rules.toml`` in the same diff (then WARN);
- implementation tests give one ``impl_test_changed`` (INFO) per test.

Plus checks that apply to either tier: ``test_config_changed``, ``eq_override_added``,
``special_case_suspected`` and ``pins_implementation``.
"""

from __future__ import annotations

import ast
import configparser
import difflib
import os
import subprocess
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from mutineer.gitdiff import read_base_file
from mutineer.models import Audience, ChangedFile, Diff, Finding, Location, RulesFile, Severity
from mutineer.testscan import (
    NOT_LITERAL,
    Assertion,
    Located,
    TestFunc,
    is_test_file,
    is_under,
    scan_source,
)

# detection names, in precedence order: each test gets one finding, for the first that applies
DELETED = "deleted"
MARKER_REMOVED = "marker_removed"
SKIPPED = "skipped"
WEAKENED = "weakened"
EXPECTED_CHANGED = "expected_changed"

_RULE_KIND = {
    DELETED: "rule_test_deleted",
    MARKER_REMOVED: "rule_marker_removed",
    SKIPPED: "rule_test_skipped",
    WEAKENED: "rule_test_weakened",
    EXPECTED_CHANGED: "rule_expected_changed",
}

_DETAIL_TEXT = {
    "assertion_removed": "an assertion was removed",
    "operator_changed": "an == or is comparison now uses a different operator",
    "truthiness_check": "a comparison became a bare truthiness check",
    "raises_widened": "an expected exception was widened to Exception",
    "assert_method_weakened": "assertEqual/assertIs became a weaker assert method",
    "parametrize_case_removed": "a parametrize case was removed",
    "literal_changed": "an expected literal value changed",
    "parametrize_case_changed": "a parametrize case changed",
    "skip_added": "a skip or xfail was added",
    "marker_removed": "its rule marker was removed",
    "deleted": "it was deleted",
}

_STRONG_ASSERTS = frozenset(
    {
        "assertEqual",
        "assertEquals",
        "assertIs",
        "assertListEqual",
        "assertDictEqual",
        "assertTupleEqual",
        "assertSetEqual",
        "assertSequenceEqual",
        "assertMultiLineEqual",
    }
)
_WEAK_ASSERTS = frozenset({"assertTrue", "assertIn", "assertIsNotNone"})
_BROAD_EXCEPTIONS = frozenset({"Exception", "BaseException"})
_EQUALITY_OPS = frozenset({"Equal", "Is"})

_CONFIG_FILES = frozenset({"conftest.py", "pytest.ini", "tox.ini"})
_TRIVIAL_LITERALS: frozenset[tuple[str, object]] = frozenset(
    {
        ("int", 0),
        ("int", 1),
        ("int", -1),
        ("float", 0.0),
        ("float", 1.0),
        ("float", -1.0),
        ("str", ""),
        ("bytes", b""),
        ("NoneType", None),
        ("bool", True),
        ("bool", False),
    }
)


@dataclass(frozen=True)
class Detection:
    """One tamper detection on one test."""

    name: str
    detail: str
    """What exactly changed, e.g. ``operator_changed``."""
    location: Location
    before: str
    after: str | None
    base_line: int
    rules: tuple[str, ...]


def check(
    diff: Diff,
    root: Path,
    rules: RulesFile | None = None,
    *,
    test_paths: Sequence[str] = ("tests",),
) -> list[Finding]:
    """Tamper findings for ``diff``, ordered by file and line.

    ``rules`` is the head rules file. Rule-test identity is static (the markers in the
    test source), so it is accepted for the contract but not needed today.
    ``test_paths`` comes from ``[tool.mutineer] test_paths``.
    """
    del rules
    changed_rules = set(diff.rules_file_changed_ids)
    base_tests, head_tests = _load_tests(diff, root, test_paths)
    findings: list[Finding] = []
    for detection, test in _detect(base_tests, head_tests):
        findings.append(_finding(detection, test, changed_rules))
    findings.extend(_pins(base_tests, head_tests))
    findings.extend(_config_changes(diff, root))
    source_files = [
        f
        for f in diff.files
        if f.path.endswith(".py")
        and f.status != "deleted"
        and not is_under(f.path, test_paths)
        and PurePosixPath(f.path).name != "conftest.py"
    ]
    findings.extend(_eq_overrides(diff, root, source_files))
    findings.extend(_special_cases(root, source_files, test_paths))
    findings.sort(key=lambda f: (f.location.file if f.location else "", _line(f)))
    return findings


# ---- loading ----


def _load_tests(
    diff: Diff, root: Path, test_paths: Sequence[str]
) -> tuple[dict[str, TestFunc], dict[str, TestFunc]]:
    """Tests in the changed test files, keyed by head ``file::qualname``.

    For a renamed file, base tests are keyed under the head path so they pair up.
    A file that fails to parse on either side is skipped: pytest's own collection error
    makes the baseline fail, which blocks on its own.
    """
    base: dict[str, TestFunc] = {}
    head: dict[str, TestFunc] = {}
    for cf in diff.files:
        base_path = cf.old_path or cf.path
        base_is_test = cf.status != "added" and is_test_file(base_path, test_paths)
        head_is_test = cf.status != "deleted" and is_test_file(cf.path, test_paths)
        if not (base_is_test or head_is_test):
            continue
        try:
            before = _scan(read_base_file(root, diff.base, base_path), base_path)
            after = _scan(_read_head(root, cf.path), cf.path)
        except SyntaxError:
            continue
        if base_is_test:
            base.update({f"{cf.path}::{t.qualname}": t for t in before})
        if head_is_test:
            head.update({t.key: t for t in after})
    return base, head


def _scan(text: str | None, path: str) -> list[TestFunc]:
    return [] if text is None else scan_source(text, path)


def _read_head(root: Path, path: str) -> str | None:
    try:
        return (root / path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


# ---- per-test detections ----


def _detect(
    base: dict[str, TestFunc], head: dict[str, TestFunc]
) -> Iterable[tuple[Detection, TestFunc]]:
    by_body: dict[str, list[TestFunc]] = {}
    for t in head.values():
        by_body.setdefault(t.body_key, []).append(t)
    for key, b in base.items():
        h = head.get(key)
        if h is None:
            moved = by_body.get(b.body_key, [])
            if not moved:
                yield _deleted(b), b
                continue
            # the test moved -> check it took its markers along
            h = max(moved, key=lambda t: len(set(t.rules) & set(b.rules)))
            if set(b.rules) - set(h.rules):
                yield _marker_removed(b, h), b
            continue
        d = compare_test(b, h)
        if d is not None:
            yield d, b


def compare_test(b: TestFunc, h: TestFunc) -> Detection | None:
    """The first detection (in precedence order) between a test's base and head versions."""
    removed = tuple(sorted(set(b.rules) - set(h.rules)))
    if removed:
        return _marker_removed(b, h)
    return _skipped(b, h) or _assertion_changes(b, h)


def _deleted(b: TestFunc) -> Detection:
    return Detection(
        name=DELETED,
        detail="deleted",
        location=Location(file=b.file, line=b.line),
        before=b.def_source,
        after=None,
        base_line=b.line,
        rules=b.rules,
    )


def _marker_removed(b: TestFunc, h: TestFunc) -> Detection:
    mark = b.rule_marks[0] if b.rule_marks else Located(b.line, b.def_source)
    return Detection(
        name=MARKER_REMOVED,
        detail="marker_removed",
        location=Location(file=h.file, line=h.line),
        before=mark.source,
        after=h.def_source,
        base_line=mark.line,
        rules=tuple(sorted(set(b.rules) - set(h.rules))),
    )


def _skipped(b: TestFunc, h: TestFunc) -> Detection | None:
    base_keys = [s.key for s in b.skips]
    for s in h.skips:
        if s.key in base_keys:
            base_keys.remove(s.key)
            continue
        return Detection(
            name=SKIPPED,
            detail="skip_added",
            location=Location(file=h.file, line=s.line),
            before=b.def_source,
            after=s.source,
            base_line=b.line,
            rules=b.rules,
        )
    return None


def _assertion_changes(b: TestFunc, h: TestFunc) -> Detection | None:
    weak: list[Detection] = []
    expected: list[Detection] = []
    removed: list[Detection] = []
    bs, hs = b.assertions, h.assertions
    matcher = difflib.SequenceMatcher(
        None, [a.key for a in bs], [a.key for a in hs], autojunk=False
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal" or tag == "insert":
            continue
        pairs = list(zip(bs[i1:i2], hs[j1:j2], strict=False)) if tag == "replace" else []
        for ba, ha in pairs:
            detail = _weakened_pair(ba, ha)
            if detail:
                weak.append(_pair_detection(WEAKENED, detail, b, h, ba, ha))
            elif _literal_changed(ba, ha):
                expected.append(_pair_detection(EXPECTED_CHANGED, "literal_changed", b, h, ba, ha))
        next_head = hs[j1 + len(pairs)] if j1 + len(pairs) < len(hs) else None
        for ba in bs[i1 + len(pairs) : i2]:
            removed.append(
                _pair_detection(WEAKENED, "assertion_removed", b, h, ba, None, next_head)
            )
    if len(hs) < len(bs) and removed:
        weak.append(removed[0])
    weak.sort(key=lambda d: d.base_line)
    param_weak, param_changed = _param_changes(b, h)
    for group in (weak, param_weak, expected, param_changed):
        if group:
            return group[0]
    return None


def _weakened_pair(b: Assertion, h: Assertion) -> str | None:
    if b.kind == "assert" and h.kind == "assert" and b.is_compare:
        if set(b.ops) & _EQUALITY_OPS and h.is_compare and h.ops != b.ops:
            return "operator_changed"
        if not h.is_compare and not h.is_and:
            return "truthiness_check"
    if b.kind == "call" and b.name in _STRONG_ASSERTS and h.name in _WEAK_ASSERTS:
        return "assert_method_weakened"
    if b.kind == "assert" and b.is_compare and h.kind == "call" and h.name in _WEAK_ASSERTS:
        return "assert_method_weakened"
    if b.kind == "raises" and h.kind == "raises" and _is_broad(h) and not _is_broad(b):
        return "raises_widened"
    return None


def _is_broad(a: Assertion) -> bool:
    if not a.operand_sources:
        return False
    try:
        node = ast.parse(a.operand_sources[0], mode="eval").body
    except SyntaxError:
        return False
    names = node.elts if isinstance(node, ast.Tuple) else [node]
    for n in names:
        last = n.attr if isinstance(n, ast.Attribute) else getattr(n, "id", None)
        if last in _BROAD_EXCEPTIONS:
            return True
    return False


def _literal_changed(b: Assertion, h: Assertion) -> bool:
    """Same assertion shape with at least one changed literal operand."""
    if (b.kind, b.name, b.ops, len(b.operands)) != (h.kind, h.name, h.ops, len(h.operands)):
        return False
    changed = False
    for bo, ho, bl, hl in zip(b.operands, h.operands, b.literals, h.literals, strict=True):
        if bo == ho:
            continue
        if not (_is_literal(bl) and _is_literal(hl)):
            return False
        changed = True
    return changed


def _is_literal(value: object) -> bool:
    return value is not NOT_LITERAL


def _pair_detection(
    name: str,
    detail: str,
    b: TestFunc,
    h: TestFunc,
    ba: Assertion,
    ha: Assertion | None,
    anchor: Assertion | None = None,
) -> Detection:
    at = ha or anchor
    return Detection(
        name=name,
        detail=detail,
        location=Location(file=h.file, line=at.line if at else h.line),
        before=ba.source,
        after=ha.source if ha else None,
        base_line=ba.line,
        rules=b.rules,
    )


def _param_changes(b: TestFunc, h: TestFunc) -> tuple[list[Detection], list[Detection]]:
    weak: list[Detection] = []
    changed: list[Detection] = []
    for names, base_cases in b.params.items():
        head_cases = h.params.get(names)
        if head_cases is None:
            continue  # the parametrize itself was rewritten; not judged
        head_keys = {c.key for c in head_cases}
        missing = [c for c in base_cases if c.key not in head_keys]
        if not missing:
            continue
        base_keys = {c.key for c in base_cases}
        added = [c for c in head_cases if c.key not in base_keys]
        line = head_cases[0].line if head_cases else h.line
        if len(head_cases) < len(base_cases):
            d = Detection(
                WEAKENED,
                "parametrize_case_removed",
                Location(file=h.file, line=line),
                missing[0].source,
                None,
                missing[0].line,
                b.rules,
            )
            weak.append(d)
        else:
            after = added[0] if added else None
            d = Detection(
                EXPECTED_CHANGED,
                "parametrize_case_changed",
                Location(file=h.file, line=after.line if after else line),
                missing[0].source,
                after.source if after else None,
                missing[0].line,
                b.rules,
            )
            changed.append(d)
    return weak, changed


def _finding(d: Detection, test: TestFunc, changed_rules: set[str]) -> Finding:
    test_id = f"{test.file}::{test.qualname}"
    evidence: dict[str, Any] = {
        "test": test_id,
        "rules": list(d.rules),
        "before": d.before,
        "base_line": d.base_line,
        "change": d.detail,
    }
    if d.after is not None:
        evidence["after"] = d.after
    what = _DETAIL_TEXT.get(d.detail, d.detail.replace("_", " "))
    if not d.rules:
        evidence["change"] = d.name
        evidence["detail"] = d.detail
        return Finding(
            check="tamper",
            kind="impl_test_changed",
            severity=Severity.INFO,
            audience=Audience.HUMAN,
            location=d.location,
            message=f"Implementation test {test_id} changed: {what}.",
            evidence=evidence,
        )
    ids = ", ".join(d.rules)
    approved = set(d.rules) <= changed_rules
    if approved:
        next_action = f"Confirm the rules.toml change to {ids} covers this test edit."
    else:
        next_action = (
            f"Restore the test, or have a human change {ids} in rules.toml in this change."
        )
    return Finding(
        check="tamper",
        kind=_RULE_KIND[d.name],
        severity=Severity.WARN if approved else Severity.BLOCK,
        audience=Audience.HUMAN,
        rules=list(d.rules),
        location=d.location,
        message=f"Rule test {test_id} guarding {ids} was tampered with: {what}.",
        evidence=evidence,
        next_action=next_action,
    )


# ---- pins_implementation ----


def _pins(base: dict[str, TestFunc], head: dict[str, TestFunc]) -> Iterable[Finding]:
    base_bodies = {t.body_key for t in base.values()}
    for key, h in head.items():
        if not h.pins or h.body_key in base_bodies:
            continue  # unchanged or moved tests are not judged again
        b = base.get(key)
        old = [p.key for p in b.pins] if b else []
        new = []
        for p in h.pins:
            if p.key in old:
                old.remove(p.key)
            else:
                new.append(p)
        if not new:
            continue
        names = sorted({p.source for p in new})
        yield Finding(
            check="tamper",
            kind="pins_implementation",
            severity=Severity.INFO,
            audience=Audience.HUMAN,
            rules=list(h.rules),
            location=Location(file=h.file, line=new[0].line),
            message=(
                f"Test {h.key} asserts on implementation details "
                f"({', '.join(names)}), not promised behaviour."
            ),
            evidence={"test": h.key, "names": names},
        )


# ---- test_config_changed ----


def _config_changes(diff: Diff, root: Path) -> Iterable[Finding]:
    for cf in diff.files:
        name = PurePosixPath(cf.path).name
        old_name = PurePosixPath(cf.old_path).name if cf.old_path else name
        what: str | None = None
        if name in _CONFIG_FILES or old_name in _CONFIG_FILES:
            if cf.status == "renamed" and not cf.hunks and name == old_name:
                continue
            what = name if name in _CONFIG_FILES else old_name
        elif name in ("setup.cfg", "pyproject.toml"):
            before = read_base_file(root, diff.base, cf.old_path or cf.path)
            after = None if cf.status == "deleted" else _read_head(root, cf.path)
            section = _pytest_section(name, before) != _pytest_section(name, after)
            if section:
                what = (
                    f"{name} [tool:pytest]"
                    if name == "setup.cfg"
                    else f"{name} [tool.pytest] / [tool.mutineer] / [tool.mutmut]"
                )
        if what is None:
            continue
        yield Finding(
            check="tamper",
            kind="test_config_changed",
            severity=Severity.WARN,
            audience=Audience.HUMAN,
            location=Location(file=cf.path, line=_first_line(cf)),
            message=f"Test configuration changed ({what}); check it doesn't hide failures.",
            evidence={"file": cf.path, "status": cf.status},
        )


def _pytest_section(name: str, text: str | None) -> object:
    """The test-relevant part of setup.cfg or pyproject.toml, for comparison."""
    if text is None:
        return None
    if name == "setup.cfg":
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(text)
        except configparser.Error:
            return text
        return dict(parser["tool:pytest"]) if parser.has_section("tool:pytest") else None
    try:
        tool = tomllib.loads(text).get("tool", {})
    except tomllib.TOMLDecodeError:
        return text
    # [tool.mutineer] decides which tests are scanned; [tool.mutmut] which tests run
    return {k: tool.get(k) for k in ("pytest", "mutineer", "mutmut")}


def _first_line(cf: ChangedFile) -> int | None:
    if cf.status == "deleted":
        return None
    for h in cf.hunks:
        if h.length:
            return h.start
    return cf.hunks[0].start if cf.hunks else None


# ---- eq_override_added ----


def _eq_overrides(diff: Diff, root: Path, files: Sequence[ChangedFile]) -> Iterable[Finding]:
    for cf in files:
        after = _parse(_read_head(root, cf.path))
        if after is None:
            continue
        before_text = (
            None
            if cf.status == "added"
            else read_base_file(root, diff.base, cf.old_path or cf.path)
        )
        before = _parse(before_text) if before_text is not None else ast.Module([], [])
        if before is None:
            continue
        old = dict(_eq_defs(before))
        for qualname, line in _eq_defs(after):
            if qualname in old:
                continue
            yield Finding(
                check="tamper",
                kind="eq_override_added",
                severity=Severity.WARN,
                audience=Audience.HUMAN,
                location=Location(file=cf.path, line=line),
                message=(
                    f"New __eq__ on {qualname}; check it isn't there to make test comparisons pass."
                ),
                evidence={"class": qualname},
            )


def _eq_defs(tree: ast.Module) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []

    def walk(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, ast.ClassDef):
                qual = f"{prefix}{node.name}"
                for item in node.body:
                    if _defines_eq(item):
                        out.append((qual, item.lineno))
                walk(node.body, f"{qual}.")
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(node.body, f"{prefix}{node.name}.<locals>.")
            elif isinstance(node, (ast.If, ast.Try, ast.With)):
                walk(node.body, prefix)
                walk(getattr(node, "orelse", []), prefix)

    walk(tree.body, "")
    return out


def _defines_eq(item: ast.stmt) -> bool:
    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return item.name == "__eq__"
    if isinstance(item, ast.Assign):
        targets: list[ast.expr] = item.targets
    elif isinstance(item, ast.AnnAssign):
        targets = [item.target]
    else:
        return False
    return any(isinstance(t, ast.Name) and t.id == "__eq__" for t in targets)


def _parse(text: str | None) -> ast.Module | None:
    if text is None:
        return None
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


# ---- special_case_suspected ----


def _special_cases(
    root: Path, files: Sequence[ChangedFile], test_paths: Sequence[str]
) -> Iterable[Finding]:
    """Changed source lines comparing to a literal that the tests also use.

    Narrowed against false positives: a literal that already appears in tracked source
    outside the changed lines is a known domain value (a status string, a file name),
    not a test input being special-cased, so it is not reported. One finding per file
    and literal.
    """
    changed_lines = {
        cf.path: {n for h in cf.hunks for n in range(h.start, h.start + h.length)} for cf in files
    }
    candidates: list[tuple[ChangedFile, list[str], dict[tuple[str, object], list[int]]]] = []
    for cf in files:
        text = _read_head(root, cf.path)
        tree = _parse(text)
        if tree is None or text is None or not changed_lines[cf.path]:
            continue
        by_literal: dict[tuple[str, object], list[int]] = {}
        for line, literal in _changed_literal_compares(tree, changed_lines[cf.path]):
            by_literal.setdefault(_lit_key(literal), []).append(line)
        if by_literal:
            candidates.append((cf, text.splitlines(), by_literal))
    if not candidates:
        return
    in_tests = _collect_test_literals(root, test_paths)
    known = _known_source_literals(root, test_paths, changed_lines)
    for cf, lines, by_literal in candidates:
        for key, at in by_literal.items():
            where = in_tests.get(key)
            if not where or key in known:
                continue
            literal = key[1]
            first = at[0]
            yield Finding(
                check="tamper",
                kind="special_case_suspected",
                severity=Severity.WARN,
                audience=Audience.HUMAN,
                location=Location(file=cf.path, line=first),
                message=(
                    f"Changed code compares to {literal!r}, a value only the tests use; "
                    "check it isn't special-casing a test input."
                ),
                evidence={
                    "literal": repr(literal),
                    "lines": at,
                    "source": lines[first - 1].strip() if first <= len(lines) else "",
                    "test_files": sorted(where)[:5],
                },
            )


def _known_source_literals(
    root: Path, test_paths: Sequence[str], changed_lines: dict[str, set[int]]
) -> set[tuple[str, object]]:
    """Literals in tracked non-test source, outside the lines this diff changed."""
    known: set[tuple[str, object]] = set()
    for rel in _tracked_python(root):
        if is_under(rel, test_paths) or PurePosixPath(rel).name == "conftest.py":
            continue
        tree = _parse(_read_head(root, rel))
        if tree is None:
            continue
        skip = changed_lines.get(rel, set())
        for node, value in _constants(tree):
            if getattr(node, "lineno", 0) not in skip:
                known.add(_lit_key(value))
    return known


def _tracked_python(root: Path) -> list[str]:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    proc = subprocess.run(
        ["git", "-c", "core.quotepath=false", "ls-files", "-z", "--", "*.py"],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    if proc.returncode != 0:
        return []
    return [p for p in proc.stdout.split("\0") if p and not p.startswith("mutants/")]


def _changed_literal_compares(tree: ast.Module, changed: set[int]) -> list[tuple[int, object]]:
    found: dict[tuple[int, tuple[str, object]], object] = {}

    def consider(node: ast.expr) -> None:
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            for el in node.elts:
                consider(el)
            return
        value = _const(node)
        if value is _NO_CONST or node.lineno not in changed:
            return
        key = _lit_key(value)
        if key in _TRIVIAL_LITERALS:
            return
        found.setdefault((node.lineno, key), value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Compare):
            for operand in (node.left, *node.comparators):
                consider(operand)
        elif isinstance(node, ast.MatchValue):
            consider(node.value)
    return [(line, value) for (line, _), value in sorted(found.items(), key=lambda kv: kv[0][0])]


_NO_CONST = object()


def _const(node: ast.expr) -> object:
    if isinstance(node, ast.Constant) and node.value is not Ellipsis:
        return node.value
    if (
        isinstance(node, ast.UnaryOp)
        and isinstance(node.op, ast.USub)
        and isinstance(node.operand, ast.Constant)
        and isinstance(node.operand.value, (int, float, complex))
        and not isinstance(node.operand.value, bool)
    ):
        return -node.operand.value
    return _NO_CONST


def _lit_key(value: object) -> tuple[str, object]:
    return (type(value).__name__, value)


def _collect_test_literals(
    root: Path, test_paths: Sequence[str]
) -> dict[tuple[str, object], set[str]]:
    """Literal value -> test files (at head) it appears in. Docstrings don't count."""
    out: dict[tuple[str, object], set[str]] = {}
    for tp in test_paths:
        base = root / tp
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            if not is_test_file(rel, test_paths) or rel.startswith("mutants/"):
                continue
            tree = _parse(_read_head(root, rel))
            if tree is None:
                continue
            for _, value in _constants(tree):
                out.setdefault(_lit_key(value), set()).add(rel)
    return out


def _constants(tree: ast.Module) -> Iterable[tuple[ast.expr, object]]:
    """Hashable literal values in ``tree`` (negative numbers included), minus docstrings."""
    docstrings = {
        id(n.value)
        for n in ast.walk(tree)
        if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
    }
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Constant, ast.UnaryOp)) or id(node) in docstrings:
            continue
        value = _const(node)
        if value is _NO_CONST:
            continue
        try:
            hash(value)
        except TypeError:
            continue
        yield node, value


def _line(f: Finding) -> int:
    if f.location is None or f.location.line is None:
        return 0
    return f.location.line
