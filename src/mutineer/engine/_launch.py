"""Run mutmut with an in-memory config override. The only module that touches mutmut internals.

Usage (always with the project root as the working directory)::

    python -m mutineer.engine._launch '<json overrides>' run [--mutineer-rule-first] GLOB ...
    python -m mutineer.engine._launch '<json overrides>' show NAME [NAME ...]
    python -m mutineer.engine._launch '<json overrides>' mutineer-config
    python -m mutineer.engine._launch '<json overrides>' mutineer-functions FILE [FILE ...]

``run`` and ``show`` are mutmut's own commands. The overrides replace fields of mutmut's
config dataclass with ``dataclasses.replace`` before ``mutmut.__main__`` is imported, so the
user's ``pyproject.toml`` is never edited. ``show`` accepts
several names and prints a ``RECORD_SEP`` + name line before each mutant's output.

``run`` scopes mutmut's forced-fail run to the tests that cover the requested functions
(mutmut's own lookup never matches the per-function globs, so it runs the whole selection),
and skips it when there are none: no mutant would run. It starts with the fastest covering
test; if the covering tests don't fail, it falls back to mutmut's unscoped run. Under
``fork`` it runs in a forked child, as under ``forkserver``. mutmut's clean-test run is
always skipped: it only checks an exit code, and it always passes when stats did, because
no test ever runs in the process the mutants start from.

``run`` also splits mutmut's full stats run: the selected tests are listed (in a child under
``forkserver``, in the launcher under ``fork``, as mutmut does), sorted and dealt round-robin
into ``min(max_children, STATS_CHILDREN, tests)`` chunks, each a stats run in its own
concurrent child, merged as mutmut merges its single child's result. A collection error in
the listing makes the stats run fail, as one in the stats run itself does. Under
``forkserver``, one chunk or fewer, or an incremental stats run, is mutmut's own run. Under
``fork`` the stats run always runs in forked children, even as one chunk, so the launcher,
which every mutant forks from, never runs a test: a test that passes only once per process
then passes in each mutant, as under ``forkserver``. The children's pytest output is printed
when one fails, and when a split stats run fails the launcher adds that a test may depend on
another having run first in the same process (test pollution), which Mutineer doesn't
support. Two such tests dealt into the same child pass; the check below catches that.

``run --mutineer-rule-first`` (the launcher strips the flag before mutmut reads
``sys.argv``; a launcher argument, not an environment variable, which would leak into the
processes the project's tests start) runs rule tests first in every pytest session but the
stats run: each session's items are stably partitioned, rule tests (at least one id in any
``rule`` mark on the function, class or module) first, after the project's own reordering.
mutmut runs a mutant's tests with ``-x``, so the first failing test says which tests kill
it: a session whose first failed report is a rule test's exits ``EXIT_RULE_KILLED`` (40)
instead of 1, so one pass tells "killed by a rule test" (40) from "killed only by
another test" (1). The stats run, and so the test map, is untouched.

``run`` checks, after a normal end of mutmut's mutant loop, the functions none of whose
mutants survived: their covering tests run unmutated, in the order their mutants got them
(rule tests first with ``--mutineer-rule-first``; 40 is a failure),
each distinct list in its own forked child (at most ``max_workers`` at a time). Test
pollution hidden in one stats child kills every mutant of a function falsely, because each
mutant runs only its function's tests in a fresh process; a function with a survivor
can't be affected. A failing check prints its pytest output and a ``POLLUTED_KILLS`` line
naming the function and its tests, and the launcher exits 1: a failing baseline.

``run`` gives every pytest session mutmut starts its own basetemp. Sessions run
concurrently (mutants, split stats children, and the forced-fail, listing and warm-up runs
beside them, then the unmutated checks), and on pytest's shared default root two that
finish together
can crash in its cleanup (``FileNotFoundError`` unlinking a dead ``pytest-current``); the
crash becomes a false kill under ``fork`` or a false timeout under ``forkserver``. The
launcher makes one ``mutineer-pytest-*`` dir in the system temp dir (never under
``mutants/``, which pytest collects from) and wraps ``PytestRunner.execute_pytest`` to add a
plugin whose ``tryfirst`` ``pytest_configure`` sets an unset ``--basetemp`` to a new dir
under it, in the process that calls ``pytest.main``. A ``--basetemp`` the project sets (in
``pytest_add_cli_args``, ``addopts`` or ``PYTEST_ADDOPTS``) is kept, shared or not. The
launcher removes its dir when ``run`` ends, also on failure, only in its own process.

The two ``mutineer-*`` commands print one ``MARKER`` line holding JSON:

- ``mutineer-config``: the resolved config fields the engine needs, and mutmut's version;
- ``mutineer-functions``: for each file, the mangled function names mutmut generates at
  least one mutant for.

Private mutmut names used: ``mutmut.configuration._load_config``, ``._config``,
``mutmut.mutation.file_mutation.mutate_file_contents``, ``run_clean_tests`` on
``mutmut.workers.isolation.ForkServerRunner`` and ``.ForkRunner`` (replaced),
``ForkRunner.run_forced_fail`` (replaced), its ``.test_runner``, and
``mutmut.workers.isolation.run_in_fork``; ``mutmut.__main__.tests_for_mutant_names``
(replaced), ``mutmut.runners.harness.PytestRunner.run_forced_fail`` (replaced) and
``.run_tests``, and ``mutmut.state.state()`` with its ``tests_by_mangled_function_name`` and
``duration_by_test`` (read); ``collect_stats`` on ``ForkServerRunner`` and ``ForkRunner``
(replaced), their ``.list_all_tests`` and ``.max_workers``, ``ForkServerRunner``'s
``.test_runner_class`` and ``.test_runner_args``,
``mutmut.runners.harness.CollectTestsFailedException``, ``PytestRunner.run_stats``,
``mutmut.models.results.StatsResult``, and ``state()``'s ``function_dependencies`` and
``stats_time`` (merged);
``mutmut.runners.harness.PytestRunner.execute_pytest`` (replaced);
``mutmut.__main__._register_mutant_result`` and ``startup`` and ``stop_all_workers`` on
``ForkServerRunner`` and ``ForkRunner`` (wrapped), ``MutantResult``'s ``mutant_name`` and
``exit_code``, ``mutmut.utils.format_utils.mangled_name_from_mutant_name``,
``mutmut.mutation.trampoline.set_mutant_under_test``, and ``ForkRunner.test_runner``,
``ForkServerRunner.test_runner_class`` and ``.test_runner_args``, and ``.max_workers`` on
both, for the unmutated check. Rule-first relies on: the ``MUTANT_UNDER_TEST``
environment variable and its ``"stats"`` value in the stats run (mutmut's
``set_mutant_under_test`` mirrors its name into the environment); raw pytest exit codes
passing unchanged through ``ForkRunner.submit`` (``os._exit(result)``),
``ForkServerRunner._forkserver_main`` (``os._exit(exit_code)``) and
``_register_mutant_result`` into ``.meta``'s ``exit_code_by_key``;
``mutmut.stats.status_by_exit_code`` tolerating an unknown code (a ``defaultdict``); and 40
being unused by mutmut (33-37, 24, -24, 152, 255, -9, -11) and pytest (0-5). Checked on
mutmut 3.8.1 and main @ 2c034f2. Exit codes
``EXIT_VERSION`` and ``EXIT_CONFIG`` mean mutmut is out of range or has no usable
``source_paths``; the reason is on stderr.
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import sys
import tempfile
from collections.abc import Callable, Sequence
from importlib import metadata
from pathlib import Path
from typing import Any

SUPPORTED = ((3, 8), (3, 9))
"""mutmut versions this launcher knows: ``>=3.8,<3.9``."""
MARKER = "@@mutineer "
RECORD_SEP = "\x1e"
EXIT_VERSION = 90
EXIT_CONFIG = 91
EXIT_USAGE = 92
STATS_CHILDREN = 4
"""Most concurrent children for mutmut's stats run; more bought nothing."""
RULE_FIRST = "--mutineer-rule-first"
"""The launcher argument of ``run`` that turns rule-first on; mutmut never sees it."""
MUTMUT_STATS = "stats"
"""mutmut's ``MUTANT_UNDER_TEST`` value for its stats run (``set_mutant_under_test``)."""
EXIT_RULE_KILLED = 40
"""A rule-first session whose first failed report is a rule test's; unused by mutmut and pytest."""
TEST_POLLUTION = (
    "mutineer: the stats run was split over processes; a test may depend on another test "
    "having run first in the same process (test pollution), which Mutineer doesn't support"
)
POLLUTED_KILLS = (
    "mutineer: every mutant was killed, but the covering tests also fail without a mutant"
)
"""The fixed start of the line naming a function whose unmutated check failed."""
_GLOB_SUFFIX = "__mutmut_*"


def version_ok(version: str) -> bool:
    try:
        major, minor = (int(p) for p in version.split(".")[:2])
    except ValueError:
        return False
    return SUPPORTED[0] <= (major, minor) < SUPPORTED[1]


def _fail(code: int, message: str) -> None:
    print(f"mutineer: {message}", file=sys.stderr)
    raise SystemExit(code)


def _check_version() -> str:
    try:
        version = metadata.version("mutmut")
    except metadata.PackageNotFoundError:
        _fail(EXIT_VERSION, "mutmut is not installed; install mutmut>=3.8,<3.9 in this environment")
        raise  # unreachable
    if not version_ok(version):
        _fail(EXIT_VERSION, f"mutmut {version} is not supported; install mutmut>=3.8,<3.9")
    return version


def _apply_overrides(overrides: dict[str, Any]) -> Any:
    import mutmut.configuration as mc

    try:
        cfg = mc._load_config()
    except FileNotFoundError as exc:
        _fail(EXIT_CONFIG, f"{exc} (set source_paths in [tool.mutmut])")
    except ValueError as exc:
        _fail(EXIT_CONFIG, f"invalid [tool.mutmut] config: {exc}")
    for path in cfg.source_paths:
        if not Path(path).exists():
            _fail(EXIT_CONFIG, f"mutmut source path '{path}' does not exist; fix [tool.mutmut]")
    fields = {f.name for f in dataclasses.fields(cfg)}
    unknown = sorted(set(overrides) - fields)
    if unknown:
        _fail(EXIT_USAGE, f"unknown mutmut config override(s): {unknown}")
    cfg = dataclasses.replace(cfg, **overrides)
    mc._config = cfg
    return cfg


def _print_config(cfg: Any, version: str) -> None:
    out = {
        "version": version,
        "source_paths": [Path(p).as_posix() for p in cfg.source_paths],
        "do_not_mutate": list(cfg.do_not_mutate),
        "also_copy": [Path(p).as_posix() for p in cfg.also_copy],
        "pytest_add_cli_args": list(cfg.pytest_add_cli_args),
        "pytest_add_cli_args_test_selection": list(cfg.pytest_add_cli_args_test_selection),
    }
    print(MARKER + json.dumps(out))


def _print_functions(files: list[str]) -> None:
    from mutmut.mutation.file_mutation import mutate_file_contents

    out: dict[str, list[str]] = {}
    for name in files:
        try:
            source = Path(name).read_text(encoding="utf-8")
            mutated = mutate_file_contents(name, source)
        except Exception:  # unparsable: mutmut copies such files unmutated
            out[name] = []
            continue
        out[name] = sorted({m.partition("__mutmut_")[0] for m in mutated.mutant_names})
    print(MARKER + json.dumps(out))


def main(argv: list[str]) -> None:
    if len(argv) < 2:
        _fail(EXIT_USAGE, "usage: _launch '<json overrides>' <mutmut args>")
    try:
        overrides = json.loads(argv[0])
    except json.JSONDecodeError as exc:
        _fail(EXIT_USAGE, f"overrides are not JSON: {exc}")
    if not isinstance(overrides, dict):
        _fail(EXIT_USAGE, "overrides must be a JSON object")
    version = _check_version()
    cfg = _apply_overrides(overrides)
    command, args = argv[1], argv[2:]

    if command == "mutineer-config":
        _print_config(cfg, version)
        return
    if command == "mutineer-functions":
        _print_functions(args)
        return

    _forget_mutineer()
    from mutmut.__main__ import cli  # imported after the override on purpose

    if command == "show":
        for name in args:
            print(RECORD_SEP + name, flush=True)
            try:
                cli.main(["show", name], prog_name="mutmut", standalone_mode=False)
            except Exception as exc:  # unknown name: report it, keep going
                print(f"!error: {type(exc).__name__}: {exc}")
            sys.stdout.flush()
        return

    if command != "run":
        sys.argv = ["mutmut", command, *args]
        cli()
        return
    rule_first = RULE_FIRST in args
    args = [a for a in args if a != RULE_FIRST]
    _scope_baseline_runs()
    _split_stats_run()
    check_full_kills = _watch_full_kills()
    sys.argv = ["mutmut", command, *args]
    temp_root = tempfile.mkdtemp(prefix="mutineer-pytest-")
    owner = os.getpid()
    try:
        _own_basetemp(temp_root, rule_first=rule_first)
        try:
            cli()
        except SystemExit as exc:
            # click ends a normal run with SystemExit(0); anything else is mutmut failing,
            # or a forked child that escaped
            if exc.code not in (0, None) or os.getpid() != owner:
                raise
        sys.stdout.flush()
        code = check_full_kills()  # its sessions need temp_root
    finally:
        # forked children leave through os._exit; one that escaped with an exception must
        # not remove the dir its siblings still use
        if os.getpid() == owner:
            shutil.rmtree(temp_root, ignore_errors=True)
    if code:
        raise SystemExit(code)


def _scope_baseline_runs() -> None:
    """Decide what mutmut's clean-test and forced-fail runs do.

    The clean run is always skipped. The forced fail uses only the covering tests of the
    requested functions, fastest first, and is skipped when there are none (every requested
    mutant is then ``no_tests`` and none runs); one that the covering tests don't fail falls
    back to mutmut's own unscoped one. Under ``fork`` it runs in a forked child, so none of
    its tests runs in the process the mutants fork from.
    """
    import fnmatch

    import mutmut.__main__ as mutmut_main
    from mutmut.runners.harness import PytestRunner
    from mutmut.state import state
    from mutmut.workers import isolation

    original_tests_for = mutmut_main.tests_for_mutant_names
    original_forced_fail = PytestRunner.run_forced_fail
    # the covering tests, known once mutmut asks for the clean run's tests; None: not scoped
    covering: list[str] | None = None

    def tests_for_mutant_names(mutant_names: Any) -> list[str]:
        nonlocal covering
        if not mutant_names:  # no names means every mutant, so every test
            covering = None
            return sorted(original_tests_for(mutant_names))
        test_map = state().tests_by_mangled_function_name
        tests: set[str] = set()
        for name in mutant_names:
            if not name.endswith(_GLOB_SUFFIX):
                tests |= set(original_tests_for([name]))
                continue
            # the keys never contain the suffix, so mutmut's own match finds nothing
            pattern = name[: -len(_GLOB_SUFFIX)]
            for key, of_key in test_map.items():
                if fnmatch.fnmatchcase(key, pattern):
                    tests |= set(of_key)
        durations = state().duration_by_test
        covering = sorted(tests, key=lambda t: (durations.get(t, 0.0), t))
        return covering

    def skipped(self: Any, tests: Any) -> int:
        return 0

    def forced_fail(self: Any) -> int:
        if covering is None:
            return int(original_forced_fail(self))
        if not covering:
            return 1  # no requested mutant has a test, so none will run
        code = int(self.run_tests(mutant_name=None, tests=covering))
        if code == 0:  # the covering tests swallowed the failure: try them all, as mutmut does
            return int(original_forced_fail(self))
        return code

    def fork_forced_fail(self: Any) -> int:
        return int(isolation.run_in_fork(self.test_runner.run_forced_fail))

    # a list, not a set: pytest runs the tests in this order
    mutmut_main.tests_for_mutant_names = tests_for_mutant_names  # type: ignore[assignment]
    PytestRunner.run_forced_fail = forced_fail  # type: ignore[method-assign]
    isolation.ForkServerRunner.run_clean_tests = skipped  # type: ignore[method-assign]
    isolation.ForkRunner.run_clean_tests = skipped  # type: ignore[method-assign]
    isolation.ForkRunner.run_forced_fail = fork_forced_fail  # type: ignore[method-assign]


def _own_basetemp(root: str, *, rule_first: bool = False) -> None:
    """Give every pytest session mutmut starts its own basetemp, made under ``root``.

    mutmut starts every session through ``PytestRunner.execute_pytest``; the replacement
    adds a plugin to ``pytest.main``'s ``plugins`` (keeping the caller's) whose
    ``pytest_configure``, marked ``tryfirst`` so it runs before pytest's ``tmpdir`` plugin
    reads the option, fills an unset ``--basetemp`` with a new dir. It runs in the process
    that calls ``pytest.main``, so each forked child gets its own dir. A ``--basetemp`` the
    project sets is left alone.

    With ``rule_first``, every session but the stats run (``MUTANT_UNDER_TEST`` is
    ``"stats"`` there, also while its tests are listed) also gets ``_rule_first_plugin``.
    """
    from mutmut.runners.harness import PytestRunner

    original = PytestRunner.execute_pytest

    def execute_pytest(self: Any, params: list[str], **kwargs: Any) -> int:
        import pytest  # here, not above: under forkserver the launcher never imports pytest

        class OwnBasetemp:
            @pytest.hookimpl(tryfirst=True)
            def pytest_configure(self, config: Any) -> None:
                if config.option.basetemp is None:
                    config.option.basetemp = tempfile.mkdtemp(dir=root)

        plugins = [*(kwargs.get("plugins") or []), OwnBasetemp()]
        if rule_first and os.environ.get("MUTANT_UNDER_TEST") != MUTMUT_STATS:
            plugins.append(_rule_first_plugin())
        kwargs["plugins"] = plugins
        return int(original(self, params, **kwargs))

    PytestRunner.execute_pytest = execute_pytest  # type: ignore[method-assign]


def _is_rule_test(item: Any) -> bool:
    """What ``--mutineer-rule-only`` keeps: at least one id in any ``rule`` mark.

    Defined here, not taken from ``mutineer.pytest_plugin``: when Mutineer tests itself that
    module would be imported from ``mutants/src``, a mutant under test.
    """
    return any(mark.args for mark in item.iter_markers("rule"))


def _rule_first_plugin() -> Any:
    """A pytest plugin that runs rule tests first and exits ``EXIT_RULE_KILLED`` for a rule kill.

    Its ``trylast`` ``pytest_collection_modifyitems`` moves the rule tests (``_is_rule_test``)
    before the others, keeping the order inside each group; ``trylast``, so a project's own
    reordering and ``-m``/``-k`` deselection come first. When the session ends with exit
    status 1 and the first failed report (setup, call or teardown: the reports that end a
    ``-x`` session; an xfail is reported as skipped) belongs to a rule test, it sets the
    status to ``EXIT_RULE_KILLED``. Call it only where pytest may be imported.
    """
    import pytest

    class RuleFirst:
        def __init__(self) -> None:
            self.rule_ids: set[str] = set()
            self.first_failed: str | None = None

        @pytest.hookimpl(trylast=True)
        def pytest_collection_modifyitems(self, items: list[Any]) -> None:
            flags = [_is_rule_test(item) for item in items]
            rule = [item for item, flag in zip(items, flags, strict=True) if flag]
            other = [item for item, flag in zip(items, flags, strict=True) if not flag]
            self.rule_ids = {item.nodeid for item in rule}
            items[:] = rule + other

        def pytest_runtest_logreport(self, report: Any) -> None:
            # the reports that end a -x session; an xfail is reported as skipped
            if self.first_failed is None and report.failed:
                self.first_failed = report.nodeid

        def pytest_sessionfinish(self, session: Any) -> None:
            if session.exitstatus == pytest.ExitCode.TESTS_FAILED and (
                self.first_failed in self.rule_ids
            ):
                session.exitstatus = EXIT_RULE_KILLED

    return RuleFirst()


def _split_stats_run() -> None:
    """Split mutmut's full stats run over concurrent children.

    The selected tests are listed, sorted and dealt round-robin into ``min(max_children,
    STATS_CHILDREN, tests)`` chunks, each run in its own child; the results are merged as
    mutmut merges its single child's. A listing that fails (a collection error) returns a
    non-zero code, so mutmut reports a failed stats run.

    Under ``forkserver`` the listing runs in a child, and with one chunk or fewer, or for
    an incremental stats run, mutmut's own method runs unchanged. Under ``fork`` the
    listing runs in the launcher, as mutmut's does, and every stats run, even one chunk,
    runs in a forked child: the launcher is the process every mutant forks from, so no test
    may run there. A failing child's pytest output is printed, and a failing split run adds
    a line naming test pollution, the likely cause when the suite passes in one process.
    """
    from mutmut.runners.harness import CollectTestsFailedException
    from mutmut.workers import isolation

    original = isolation.ForkServerRunner.collect_stats

    def collect_stats(self: Any, tests: Any) -> int:
        tests = list(tests) if tests is not None else None
        if tests:  # incremental: never for Mutineer
            return int(original(self, tests))
        if min(self.max_workers, STATS_CHILDREN) <= 1:
            return int(original(self, tests))
        try:
            ids = sorted(self.list_all_tests().ids)
        except ChildProcessError as exc:  # how the listing child reports a collection error
            print(f"mutineer: listing the tests for mutmut's stats run failed: {exc}")
            return 1
        k = min(self.max_workers, STATS_CHILDREN, len(ids))
        if k <= 1:  # no ids fails in mutmut's own run as it always did
            return int(original(self, tests))
        return _stats_in_children(
            lambda: self.test_runner_class(**self.test_runner_args),
            [ids[i::k] for i in range(k)],
        )

    def fork_collect_stats(self: Any, tests: Any) -> int:
        chunks = [list(tests)] if tests else None  # incremental: never for Mutineer
        if chunks is None:
            try:
                ids = sorted(self.list_all_tests().ids)
            except CollectTestsFailedException:
                print("mutineer: listing the tests for mutmut's stats run failed")
                return 1
            # no ids: one empty chunk, mutmut's own "every test", which fails as it always did
            k = max(1, min(self.max_workers, STATS_CHILDREN, len(ids)))
            chunks = [ids[i::k] for i in range(k)]
        code = _stats_in_children(lambda: self.test_runner, chunks, show_output=True)
        if code and len(chunks) > 1:
            print(TEST_POLLUTION)
        return code

    isolation.ForkServerRunner.collect_stats = collect_stats  # type: ignore[method-assign]
    isolation.ForkRunner.collect_stats = fork_collect_stats  # type: ignore[method-assign]


def _stats_in_children(
    make_runner: Callable[[], Any], chunks: list[list[str]], *, show_output: bool = False
) -> int:
    """Run mutmut's stats collection on each chunk in its own child, merge into ``state()``.

    ``make_runner`` gives the test runner, called in the child. With ``show_output``, each
    child's output is sent back with its result and printed, in chunk order, for the
    children that failed. Returns 0 only when every child sent a result and every result's
    exit code is 0.
    """
    import functools

    from mutmut.models.results import StatsResult
    from mutmut.state import state

    def run_chunk(chunk: list[str]) -> dict[str, Any]:
        exit_code = make_runner().run_stats(tests=chunk)
        return StatsResult(
            exit_code=exit_code,
            tests_by_mangled_function_name=dict(state().tests_by_mangled_function_name),
            duration_by_test=dict(state().duration_by_test),
            stats_time=state().stats_time or 0.0,
            function_dependencies=dict(state().function_dependencies),
        ).to_dict()

    jobs = [functools.partial(run_chunk, chunk) for chunk in chunks]
    failed = 0
    for code, sent, output in _in_children(jobs, limit=len(jobs), capture=show_output):
        try:
            result = StatsResult.from_dict(sent) if sent is not None else None
        except Exception:  # a cut-off result
            result = None
        if result is None:
            failed = failed or code or 1
            continue
        if show_output and result.exit_code and output:
            print(output, end="")
        for key, of_key in result.tests_by_mangled_function_name.items():
            state().tests_by_mangled_function_name[key] |= of_key
        state().duration_by_test.update(result.duration_by_test)
        state().stats_time = result.stats_time  # as mutmut does; it overwrites it anyway
        for key, callers in result.function_dependencies.items():
            # several children can report the same function: keep every edge
            state().function_dependencies[key] |= callers
        failed = failed or result.exit_code
    return int(failed)


def _in_children(
    jobs: Sequence[Callable[[], Any]], *, limit: int, capture: bool = False
) -> list[tuple[int, Any, str]]:
    """Run each job in its own forked child, at most ``limit`` at a time.

    Returns, per job and in job order, the child's exit code, the job's return value
    (``None`` when the child sent none: it raised, crashed or was cut off) and, with
    ``capture``, what the job printed to ``sys.stdout`` and ``sys.stderr``. Each pipe is read
    to the end before its child is reaped, and every child is reaped on every path.
    """
    import contextlib
    import io
    import pickle
    import signal
    import traceback

    def run_job(job: Callable[[], Any]) -> bytes:
        streams = sys.stdout, sys.stderr
        captured = io.StringIO()
        if capture:
            sys.stdout = sys.stderr = captured
        try:
            value = job()
        finally:
            sys.stdout, sys.stderr = streams
        return pickle.dumps((value, captured.getvalue()))

    results: list[tuple[int, Any, str]] = [(1, None, "")] * len(jobs)
    readers: dict[int, tuple[int, int]] = {}  # pid -> job index and its pipe's read end
    running: set[int] = set()  # pids not reaped yet

    def collect(pid: int) -> None:
        index, read_fd = readers.pop(pid)
        # read the pipe to the end before waiting: a child blocks on a full pipe
        with os.fdopen(read_fd, "rb") as pipe:
            payload = pipe.read()
        _, status = os.waitpid(pid, 0)
        running.discard(pid)
        code = os.waitstatus_to_exitcode(status)
        value, output = None, ""
        if code == 0:
            with contextlib.suppress(Exception):  # an empty or cut-off payload
                value, output = pickle.loads(payload)
        results[index] = (code, value, output)

    try:
        for index, job in enumerate(jobs):
            if len(running) >= limit:
                collect(next(iter(readers)))  # the oldest
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                try:
                    os.close(read_fd)
                    payload = run_job(job)
                    with os.fdopen(write_fd, "wb") as pipe:
                        pipe.write(payload)
                except BaseException:
                    traceback.print_exc()
                    os._exit(1)
                os._exit(0)
            os.close(write_fd)
            readers[pid] = (index, read_fd)
            running.add(pid)
        while readers:
            collect(next(iter(readers)))
    finally:
        # only left over when this process failed half-way: leave no orphan behind
        for _, read_fd in readers.values():
            os.close(read_fd)
        for pid in running:  # not reaped yet, so the pid is still this child's
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
    return results


def _watch_full_kills() -> Callable[[], int]:
    """Prepare the unmutated check of the functions none of whose mutants survived.

    A test that passes only after another one ran in the same process (test pollution) can
    hide in one stats child, where the two run together. Each mutant runs only its
    function's covering tests in a fresh process, where such a test fails whatever the
    mutant did, so every mutant of the function is killed falsely. A function with a
    survivor can't be affected: its covering tests all passed in exactly that setup.

    This wraps mutmut to record each finished mutant's exit code (``_register_mutant_result``),
    the mutant runner (``startup``) and an interrupted mutant loop (``stop_all_workers``:
    mutmut catches the interrupt and then ends normally). It returns the check, to call only
    after ``run`` ended normally. For each function with covering tests and no mutant that
    exited 0, the check runs those tests unmutated, in the order its mutants got them, each
    distinct list once, in its own forked child of the launcher, at most ``max_workers`` at
    a time. Its sessions go through ``execute_pytest`` with ``MUTANT_UNDER_TEST=""``, so
    with rule-first on they run rule tests first, as the mutants did, and a rule test's
    failure exits ``EXIT_RULE_KILLED``: a failure like any other here, and a kill (not 0) in
    the recorded codes. It returns 0 when all pass; otherwise it prints each failing check's
    pytest output and a ``POLLUTED_KILLS`` line per function, and returns 1. A check that crashed
    prints its own line instead, without ``POLLUTED_KILLS``, so the run fails as an error.
    """
    import functools

    import mutmut.__main__ as mutmut_main
    from mutmut.mutation.trampoline import set_mutant_under_test
    from mutmut.state import state
    from mutmut.utils.format_utils import mangled_name_from_mutant_name
    from mutmut.workers import isolation

    codes_by_function: dict[str, list[int]] = {}
    runners: list[Any] = []
    interrupted = False
    original_register = mutmut_main._register_mutant_result

    def register_mutant_result(result: Any, *args: Any, **kwargs: Any) -> None:
        original_register(result, *args, **kwargs)
        function = mangled_name_from_mutant_name(result.mutant_name)
        codes_by_function.setdefault(function, []).append(result.exit_code)

    def recording_startup(original: Any) -> Any:
        def startup(self: Any) -> None:
            runners.append(self)
            original(self)

        return startup

    def recording_stop(original: Any) -> Any:
        def stop_all_workers(self: Any) -> None:
            nonlocal interrupted
            interrupted = True
            original(self)

        return stop_all_workers

    def run_unmutated(make_runner: Callable[[], Any], tests: list[str]) -> int:
        set_mutant_under_test("")  # mutmut's value for the clean run
        return int(make_runner().run_tests(mutant_name=None, tests=tests))

    def check() -> int:
        if interrupted or not runners:
            return 0
        runner = runners[-1]
        under_fork = isinstance(runner, isolation.ForkRunner)
        test_map = state().tests_by_mangled_function_name
        durations = state().duration_by_test
        functions_by_tests: dict[tuple[str, ...], list[str]] = {}
        for function, codes in sorted(codes_by_function.items()):
            covering = test_map.get(function)
            if not covering or 0 in codes:
                continue
            # the list its mutants got: ForkServerRunner sends list(tests) of this very set;
            # ForkRunner.submit sorts that list by duration (a stable sort)
            tests = list(covering)
            if under_fork:
                tests.sort(key=lambda t: durations[t])
            functions_by_tests.setdefault(tuple(tests), []).append(function)
        if not functions_by_tests:
            return 0

        def make_runner() -> Any:
            if under_fork:
                return runner.test_runner
            # under forkserver the launcher never imports pytest: build the runner in the child
            return runner.test_runner_class(**runner.test_runner_args)

        lists = list(functions_by_tests)
        jobs = [functools.partial(run_unmutated, make_runner, list(tests)) for tests in lists]
        failed = 0
        for key, (code, sent, output) in zip(
            lists, _in_children(jobs, limit=runner.max_workers, capture=True), strict=True
        ):
            # 5: pytest collected none of them, as for the mutants, whose status says so
            if sent in (0, 5):
                continue
            functions = ", ".join(functions_by_tests[key])
            if sent is None:
                print(f"mutineer: the unmutated check of {functions} crashed (exit {code})")
                failed = 1
                continue
            print(output, end="")
            for function in functions_by_tests[key]:
                print(
                    f"{POLLUTED_KILLS}: {function} ({', '.join(key)}); a test likely "
                    "depends on another test having run first in the same process "
                    "(test pollution), which Mutineer doesn't support"
                )
            failed = 1
        return failed

    mutmut_main._register_mutant_result = register_mutant_result
    for cls in (isolation.ForkServerRunner, isolation.ForkRunner):
        cls.startup = recording_startup(cls.startup)  # type: ignore[method-assign]
        cls.stop_all_workers = recording_stop(cls.stop_all_workers)  # type: ignore[method-assign]
    return check


def _forget_mutineer() -> None:
    """Drop Mutineer's own modules before mutmut runs any test.

    ``python -m mutineer.engine._launch`` imports the ``mutineer`` package from where it is
    installed. mutmut puts ``mutants/src`` first on ``sys.path`` for the tests, but a module
    already in ``sys.modules`` is never imported again, so when the project under test is
    Mutineer itself its tests would run the original code and never reach a mutant. For any
    other project this changes nothing: the launcher uses no Mutineer module after this.
    """
    for name in list(sys.modules):
        if name == "mutineer" or name.startswith("mutineer."):
            del sys.modules[name]


if __name__ == "__main__":
    main(sys.argv[1:])
