"""The mutmut engine: a diff in, scoped mutant results out.

Every mutmut call goes through ``python -m mutineer.engine._launch`` in a subprocess, with
the project root as the working directory, so mutmut's in-process state never leaks into
Mutineer and its spinners never reach Mutineer's output.

Invariants:

- **Fresh ``mutants/``.** ``run`` and ``rerun`` delete ``mutants/`` first. mutmut's cache
  keeps test copies, test-to-function maps and mutated files from earlier runs (traps 1
  and 6 in ``docs/spike-mutmut.md``, plus stale ``mutants/tests`` copies), and both passes
  invalidate most of it anyway. Mutineer writes nowhere else (ENGINE-001).
- **Read only what was asked, right after asking** (ENGINE-002): results come from the
  ``.meta`` keys of the functions requested in that call, read before the next call.
- **Never an empty scope**: no mutable changed function means no mutmut call.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from glob import escape as glob_escape
from pathlib import Path
from typing import Any, Literal

import libcst as cst
from libcst.metadata import MetadataWrapper, PositionProvider

from mutineer.config import load_config
from mutineer.engine import _launch
from mutineer.engine.functions import (
    analyze,
    demote_unmutated,
    function_refs,
    module_name,
    under,
)
from mutineer.errors import EngineError
from mutineer.models import Diff, EngineRun, FunctionRef, MutantResult, MutantStatus, RuleMap

TAIL_LINES = 60

_STATUS_BY_EXIT_CODE: dict[int | None, MutantStatus] = {
    1: MutantStatus.KILLED,
    3: MutantStatus.KILLED,
    0: MutantStatus.SURVIVED,
    5: MutantStatus.NO_TESTS,
    33: MutantStatus.NO_TESTS,
    34: MutantStatus.SKIPPED,
    35: MutantStatus.SUSPICIOUS,
    36: MutantStatus.TIMEOUT,
    24: MutantStatus.TIMEOUT,
    -24: MutantStatus.TIMEOUT,
    152: MutantStatus.TIMEOUT,
    255: MutantStatus.TIMEOUT,
    37: MutantStatus.TYPE_CHECK,
    -11: MutantStatus.SEGFAULT,
    -9: MutantStatus.SEGFAULT,
    2: MutantStatus.INTERRUPTED,
    None: MutantStatus.NOT_CHECKED,
}

_BASELINE_FAILED = (
    "failed to collect stats",
    "Failed to collect list of tests",
    "Failed to run clean test",
)
_NO_TESTS_AT_ALL = "Stopping early, because we could not find any test case"
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_NOT_KILLED_EXEMPT = {MutantStatus.KILLED, MutantStatus.TYPE_CHECK}

RULE_PASS_ARGS = ("-p", "mutineer", "--mutineer-rule-only")
"""Appended to the project's ``pytest_add_cli_args`` in the rule pass.

The plugin option deselects tests without a rule id. The project's own ``-m`` (in pytest
``addopts`` or ``pytest_add_cli_args``) is left alone, because pytest keeps only the last
``-m``. ``-p mutineer`` loads the plugin even with ``PYTEST_DISABLE_PLUGIN_AUTOLOAD`` set,
and is a no-op when it is already loaded.
"""


def status_for_exit_code(code: int | None) -> MutantStatus:
    """Map a mutmut worker exit code to a status."""
    return _STATUS_BY_EXIT_CODE.get(code, MutantStatus.SUSPICIOUS)


def output_tail(text: str, lines: int = TAIL_LINES) -> str:
    """The last ``lines`` lines, with spinner redraws and ANSI codes removed."""
    cleaned = []
    for raw in _ANSI.sub("", text).split("\n"):
        line = raw.rsplit("\r", 1)[-1].rstrip()
        if line:
            cleaned.append(line)
    return "\n".join(cleaned[-lines:])


@dataclass(frozen=True)
class MutmutConfig:
    """The parts of mutmut's resolved config the engine needs."""

    version: str
    source_paths: list[str]
    do_not_mutate: list[str]
    pytest_add_cli_args: list[str]
    pytest_add_cli_args_test_selection: list[str]


@dataclass
class _Pass:
    """The outcome of one ``mutmut run``."""

    kind: Literal["ok", "baseline_failed", "no_tests"]
    output: str
    exit_codes: dict[str, int | None] = field(default_factory=dict)
    tests_by_function: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class _Detail:
    line: int | None
    original: str | None
    mutated: str | None


class MutmutEngine:
    """``Engine`` implementation on mutmut 3.8 (see ``mutineer.engine.Engine``)."""

    def __init__(self, *, python: str | None = None, max_children: int | None = None) -> None:
        self.python = python or sys.executable
        self.max_children = max_children

    # ---- public API ----

    def run(self, diff: Diff, rule_map: RuleMap, root: Path) -> EngineRun:
        started = time.monotonic()
        root = root.resolve()
        cfg = self.preflight(root)
        changed = analyze(
            diff,
            root,
            source_paths=cfg.source_paths,
            do_not_mutate=cfg.do_not_mutate,
            test_paths=load_config(root).test_paths,
        )
        if changed.functions:
            changed = demote_unmutated(changed, self._mutated_functions(root, changed.files))
        functions = [f.ref for f in changed.functions]

        def finish(**kwargs: Any) -> EngineRun:
            kwargs.setdefault("results", [])
            kwargs.setdefault("tests_by_function", {})
            return EngineRun(
                functions=functions,
                unmeasured=changed.unmeasured,
                duration_s=round(time.monotonic() - started, 3),
                **kwargs,
            )

        if not functions:
            failed, output = self._pytest_baseline(root, cfg)
            return finish(baseline_failed=failed, baseline_output=output if failed else None)

        _wipe_mutants(root)
        files = changed.files
        all_pass = self._mutmut_run(root, files, functions, cfg.pytest_add_cli_args)
        if all_pass.kind == "baseline_failed":
            return finish(baseline_failed=True, baseline_output=all_pass.output)
        if all_pass.kind == "no_tests":
            # no changed function has a test: report every generated mutant as no_tests,
            # as mutmut does when only some functions are untested, so untested code always
            # lowers diff_score; no rule pass, because nothing is rule-covered
            untested = [
                r.model_copy(update={"status": MutantStatus.NO_TESTS})
                for r in _results(all_pass.exit_codes, functions, "all")
            ]
            return finish(
                baseline_failed=False,
                unreached=[f.mutmut_name for f in functions],
                results=self._with_details(root, untested, functions),
            )

        tests_by_function = all_pass.tests_by_function
        unreached = [f.mutmut_name for f in functions if not tests_by_function.get(f.mutmut_name)]
        results = _results(all_pass.exit_codes, functions, "all")

        rule_covered = [
            f
            for f in functions
            if any(rule_map.get(t) for t in tests_by_function.get(f.mutmut_name, []))
        ]
        baseline_failed, baseline_output = False, None
        if rule_covered:
            rule_files = sorted({f.file for f in rule_covered})
            rule_pass = self._mutmut_run(
                root, rule_files, rule_covered, [*cfg.pytest_add_cli_args, *RULE_PASS_ARGS]
            )
            if rule_pass.kind == "baseline_failed":
                baseline_failed, baseline_output = True, rule_pass.output
            elif rule_pass.kind == "no_tests":
                covered = {f.mutmut_name for f in rule_covered}
                results += [
                    r.model_copy(update={"run": "rule", "status": MutantStatus.NO_TESTS})
                    for r in results
                    if r.function in covered
                ]
            else:
                results += _results(rule_pass.exit_codes, rule_covered, "rule")

        results = self._with_details(root, results, functions)
        return finish(
            baseline_failed=baseline_failed,
            baseline_output=baseline_output,
            unreached=unreached,
            results=results,
            tests_by_function=tests_by_function,
        )

    def rerun(self, names: list[str], rule_map: RuleMap, root: Path) -> EngineRun:
        """Rule pass only, for exactly ``names``.

        mutmut runs every mutant of the names' functions (per-function globs, because an
        exact name raises ``KeyError`` when its function has no stats entry), but only the
        requested names are reported. A name mutmut no longer generates (its function
        changed or vanished) is reported as ``not_checked``. ``rule_map`` is unused: the
        rule pass selects rule tests with the plugin's ``--mutineer-rule-only``.
        """
        del rule_map
        started = time.monotonic()
        root = root.resolve()
        cfg = self.preflight(root)
        wanted = sorted(set(names))
        by_function: dict[str, list[str]] = {}
        for name in wanted:
            if "__mutmut_" not in name:
                raise EngineError(f"'{name}' is not a mutmut mutant name")
            by_function.setdefault(name.partition("__mutmut_")[0], []).append(name)

        files = _module_files(root, cfg.source_paths, load_config(root).test_paths)
        refs: list[FunctionRef] = []
        for fn in sorted(by_function):
            path = files.get(fn.rsplit(".", 1)[0]) if "." in fn else None
            if path is None:
                continue
            for ref in function_refs(path, (root / path).read_text(encoding="utf-8")).values():
                if ref.mutmut_name == fn:
                    refs.append(ref)
        live = self._mutated_functions(root, sorted({r.file for r in refs})) if refs else set()
        refs = [r for r in refs if r.mutmut_name in live]

        def finish(results: list[MutantResult], **kwargs: Any) -> EngineRun:
            got = {r.name for r in results}
            results = results + [
                MutantResult(
                    name=n,
                    function=n.partition("__mutmut_")[0],
                    run="rule",
                    status=MutantStatus.NOT_CHECKED,
                )
                for n in wanted
                if n not in got
            ]
            results.sort(key=lambda r: r.name)
            return EngineRun(
                functions=[],
                unmeasured=[],
                results=results,
                tests_by_function={},
                duration_s=round(time.monotonic() - started, 3),
                **kwargs,
            )

        if not refs:
            return finish([], baseline_failed=False)

        _wipe_mutants(root)
        rule_pass = self._mutmut_run(
            root, sorted({r.file for r in refs}), refs, [*cfg.pytest_add_cli_args, *RULE_PASS_ARGS]
        )
        if rule_pass.kind == "baseline_failed":
            return finish([], baseline_failed=True, baseline_output=rule_pass.output)
        live_names = {n for r in refs for n in by_function[r.mutmut_name]}
        if rule_pass.kind == "no_tests":
            results = [
                MutantResult(
                    name=n,
                    function=n.partition("__mutmut_")[0],
                    run="rule",
                    status=MutantStatus.NO_TESTS,
                )
                for n in sorted(live_names)
            ]
        else:
            results = [
                r for r in _results(rule_pass.exit_codes, refs, "rule") if r.name in live_names
            ]
        return finish(self._with_details(root, results, refs), baseline_failed=False)

    def preflight(self, root: Path) -> MutmutConfig:
        """Check mutmut is importable and in range, and its source paths resolve."""
        proc = self._launch(root, {}, ["mutineer-config"])
        data = _marker_json(proc.stdout)
        if proc.returncode != 0 or not isinstance(data, dict):
            reason = proc.stderr.strip().splitlines()[-1:] or [output_tail(proc.stdout, 5)]
            msg = reason[0].removeprefix("mutineer: ") if reason and reason[0] else ""
            if proc.returncode in (_launch.EXIT_VERSION, _launch.EXIT_CONFIG) and msg:
                raise EngineError(msg)
            raise EngineError(
                f"could not start mutmut ({msg or 'exit ' + str(proc.returncode)}); "
                "install mutmut>=3.8,<3.9 next to Mutineer and set [tool.mutmut] source_paths"
            )
        return MutmutConfig(
            version=str(data["version"]),
            source_paths=[str(p) for p in data["source_paths"]],
            do_not_mutate=[str(p) for p in data["do_not_mutate"]],
            pytest_add_cli_args=[str(a) for a in data["pytest_add_cli_args"]],
            pytest_add_cli_args_test_selection=[
                str(a) for a in data["pytest_add_cli_args_test_selection"]
            ],
        )

    # ---- subprocesses ----

    def _launch(
        self, root: Path, overrides: dict[str, Any], args: list[str]
    ) -> subprocess.CompletedProcess[str]:
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["NO_COLOR"] = "1"
        env.pop("PYTHONSAFEPATH", None)
        cmd = [self.python, "-m", "mutineer.engine._launch", json.dumps(overrides), *args]
        try:
            return subprocess.run(
                cmd, cwd=root, env=env, capture_output=True, text=True, errors="replace"
            )
        except OSError as exc:
            raise EngineError(f"could not start {self.python}: {exc}") from exc

    def _mutated_functions(self, root: Path, files: list[str]) -> set[str]:
        """Mangled function names (with module) mutmut generates mutants for in ``files``."""
        proc = self._launch(root, {}, ["mutineer-functions", *files])
        data = _marker_json(proc.stdout)
        if proc.returncode != 0 or not isinstance(data, dict):
            raise EngineError(
                "mutmut could not list mutants for the changed files: "
                + (output_tail(proc.stderr or proc.stdout, 5) or f"exit {proc.returncode}")
            )
        return {
            f"{module_name(path)}.{fn}" for path, fns in data.items() for fn in fns if path in files
        }

    def _mutmut_run(
        self, root: Path, files: list[str], functions: list[FunctionRef], pytest_args: list[str]
    ) -> _Pass:
        """One ``mutmut run`` over ``functions``, read immediately."""
        assert files and functions, "never call mutmut with an empty scope"
        stats = root / "mutants" / "mutmut-stats.json"
        if stats.is_file():
            stats.unlink()  # force a fresh test map for this pass's test selection
        overrides = {
            "only_mutate": [glob_escape(f) for f in files],
            "pytest_add_cli_args": pytest_args,
        }
        args = ["run"]
        if self.max_children:
            args += ["--max-children", str(self.max_children)]
        args += [glob_escape(f.mutmut_name) + "__mutmut_*" for f in functions]
        proc = self._launch(root, overrides, args)
        output = (proc.stdout or "") + (proc.stderr or "")
        tail = output_tail(output)
        if proc.returncode != 0:
            if any(m in output for m in _BASELINE_FAILED):
                return _Pass("baseline_failed", tail)
            if _NO_TESTS_AT_ALL in output:
                # mutmut wrote the generated mutants (untested, exit code None) before
                # it stopped, so the names are known
                return _Pass("no_tests", tail, exit_codes=_read_exit_codes(root, files))
            raise EngineError(f"mutmut run failed (exit {proc.returncode}):\n{tail}")
        return _Pass(
            "ok",
            tail,
            exit_codes=_read_exit_codes(root, files),
            tests_by_function=_read_tests(root, functions),
        )

    def _pytest_baseline(self, root: Path, cfg: MutmutConfig) -> tuple[bool, str]:
        """Run the project's tests once without mutmut."""
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        cmd = [
            self.python,
            "-m",
            "pytest",
            "--ignore=mutants",
            "-p",
            "no:cacheprovider",
            *cfg.pytest_add_cli_args,
            *cfg.pytest_add_cli_args_test_selection,
        ]
        try:
            proc = subprocess.run(
                cmd, cwd=root, env=env, capture_output=True, text=True, errors="replace"
            )
        except OSError as exc:
            raise EngineError(f"could not run pytest: {exc}") from exc
        # 5: no tests collected; an empty suite isn't a broken one
        failed = proc.returncode not in (0, 5)
        return failed, output_tail((proc.stdout or "") + (proc.stderr or ""))

    # ---- mutant details ----

    def _with_details(
        self, root: Path, results: list[MutantResult], functions: list[FunctionRef]
    ) -> list[MutantResult]:
        """Fill ``line``/``original``/``mutated`` for every result that isn't killed."""
        names = sorted({r.name for r in results if r.status not in _NOT_KILLED_EXEMPT})
        if not names:
            return results
        proc = self._launch(root, {}, ["show", *names])
        if proc.returncode != 0:
            raise EngineError(f"mutmut show failed:\n{output_tail(proc.stdout + proc.stderr)}")
        by_name = {f.mutmut_name: f for f in functions}
        texts: dict[str, list[str]] = {}
        anchors: dict[str, dict[str, int]] = {}
        details: dict[str, _Detail] = {}
        for name, diff_text in _split_show(proc.stdout).items():
            fn = by_name.get(name.partition("__mutmut_")[0])
            if fn is None:
                continue
            if fn.file not in texts:
                text = (root / fn.file).read_text(encoding="utf-8")
                texts[fn.file] = text.split("\n")
                anchors[fn.file] = render_anchors(text)
            details[name] = locate(
                diff_text, texts[fn.file], fn, anchors[fn.file].get(fn.qualname, fn.start_line)
            )
        out = []
        for r in results:
            d = details.get(r.name)
            if d is not None and r.status not in _NOT_KILLED_EXEMPT:
                r = r.model_copy(
                    update={"line": d.line, "original": d.original, "mutated": d.mutated}
                )
            out.append(r)
        return out


# ---- helpers (module level so tests can reach them without mutmut) ----


def render_anchors(text: str) -> dict[str, int]:
    """For each top-level function and method: the head line ``mutmut show`` calls line 1.

    mutmut renders a function from its first leading comment (comment lines directly
    above it), else its first decorator, else its ``def`` line.
    """
    try:
        wrapper = MetadataWrapper(cst.parse_module(text))
    except cst.ParserSyntaxError:
        return {}
    positions = wrapper.resolve(PositionProvider)
    out: dict[str, int] = {}

    def anchor(fn: cst.FunctionDef) -> int:
        def_line = positions[fn].start.line
        first = min([positions[d].start.line for d in fn.decorators] + [def_line])
        leading = list(fn.leading_lines)
        for i, line in enumerate(leading):
            if line.comment is not None:
                return first - len(leading) + i
        return first

    for stmt in wrapper.module.body:
        if isinstance(stmt, cst.FunctionDef):
            out[stmt.name.value] = anchor(stmt)
        elif isinstance(stmt, cst.ClassDef) and isinstance(stmt.body, cst.IndentedBlock):
            for child in stmt.body.body:
                if isinstance(child, cst.FunctionDef):
                    out[f"{stmt.name.value}.{child.name.value}"] = anchor(child)
    return out


_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@")


def locate(diff_text: str, head_lines: list[str], fn: FunctionRef, anchor: int) -> _Detail:
    """Map a ``mutmut show`` diff to the head file line and the changed source text.

    file line = anchor + (hunk old start - 1) + (context lines before the first ``-``), where
    the anchor is the line mutmut's rendering of the function starts on. The result is
    checked against the head file; if it doesn't match, the nearest matching line in the
    function is used, and ``line`` is ``None`` if there is none.
    """
    old_start: int | None = None
    context_before = 0
    minus: list[str] = []
    plus: list[str] = []
    for raw in diff_text.split("\n"):
        m = _HUNK.match(raw)
        if m:
            if old_start is not None:
                break  # one mutation, one hunk; ignore anything after
            old_start = int(m.group(1))
            continue
        if old_start is None or raw.startswith(("---", "+++")):
            continue
        if raw.startswith("-"):
            minus.append(raw[1:].strip())
        elif raw.startswith("+"):
            plus.append(raw[1:].strip())
        elif not minus:
            context_before += 1
    original = "\n".join(minus) if minus else None
    mutated = "\n".join(plus) if plus or minus else None
    if old_start is None or not minus:
        return _Detail(None, original, mutated)

    guess = anchor + (old_start - 1) + context_before

    def matches(line: int) -> bool:
        return 1 <= line <= len(head_lines) and head_lines[line - 1].strip() == minus[0]

    if matches(guess):
        return _Detail(guess, original, mutated)
    lo, hi = min(anchor, fn.start_line), fn.end_line
    candidates = [n for n in range(lo, hi + 1) if matches(n)]
    line = min(candidates, key=lambda n: abs(n - guess)) if candidates else None
    return _Detail(line, original, mutated)


def _split_show(stdout: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for block in stdout.split(_launch.RECORD_SEP)[1:]:
        name, _, body = block.partition("\n")
        if not body.startswith("!error:"):
            out[name.strip()] = body
    return out


def _marker_json(stdout: str) -> Any:
    for line in reversed(stdout.splitlines()):
        if line.startswith(_launch.MARKER):
            return json.loads(line[len(_launch.MARKER) :])
    return None


def _wipe_mutants(root: Path) -> None:
    target = root / "mutants"
    if target.is_symlink() or target.is_file():
        target.unlink()
    elif target.is_dir():
        shutil.rmtree(target)


def _read_exit_codes(root: Path, files: Iterable[str]) -> dict[str, int | None]:
    codes: dict[str, int | None] = {}
    for f in files:
        meta = root / "mutants" / (f + ".meta")
        try:
            data = json.loads(meta.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise EngineError(f"mutmut wrote no readable results for {f} ({exc})") from exc
        codes.update(data.get("exit_code_by_key", {}))
    return codes


def _read_tests(root: Path, functions: Iterable[FunctionRef]) -> dict[str, list[str]]:
    path = root / "mutants" / "mutmut-stats.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EngineError(f"mutmut wrote no readable test map ({exc})") from exc
    table = data.get("tests_by_mangled_function_name", {})
    out: dict[str, list[str]] = {}
    for f in functions:
        tests = table.get(f.mutmut_name)
        if tests:
            out[f.mutmut_name] = sorted(set(tests))
    return out


def _results(
    exit_codes: dict[str, int | None], functions: Iterable[FunctionRef], run: Literal["all", "rule"]
) -> list[MutantResult]:
    """Results for the requested functions' mutants only, in mutant order."""
    out: list[MutantResult] = []
    for f in functions:
        pattern = re.compile(re.escape(f.mutmut_name) + r"__mutmut_(\d+)$")
        mine = []
        for name, code in exit_codes.items():
            m = pattern.match(name)
            if m:
                mine.append((int(m.group(1)), name, code))
        for _, name, code in sorted(mine):
            out.append(
                MutantResult(
                    name=name, function=f.mutmut_name, run=run, status=status_for_exit_code(code)
                )
            )
    return out


def _module_files(
    root: Path, source_paths: Sequence[str], test_paths: Sequence[str]
) -> dict[str, str]:
    """mutmut module name -> repo-relative source file, for every source file."""
    out: dict[str, str] = {}
    for sp in source_paths:
        base = root / sp
        files = [base] if base.is_file() else sorted(base.rglob("*.py"))
        for path in files:
            rel = path.relative_to(root).as_posix()
            if rel.startswith("mutants/") or under(rel, test_paths):
                continue
            out.setdefault(module_name(rel), rel)
    return out


__all__ = ["RULE_PASS_ARGS", "MutmutEngine", "status_for_exit_code"]
