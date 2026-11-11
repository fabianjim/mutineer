"""Run mutmut with an in-memory config override. The only module that touches mutmut internals.

Usage (always with the project root as the working directory)::

    python -m mutineer.engine._launch '<json overrides>' run 'pkg.mod.x_f__mutmut_*' ...
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
support. Two such tests dealt into the same child pass, and their mutants can then be killed
falsely.

``run`` gives every pytest session mutmut starts its own basetemp. Sessions run
concurrently (mutants, split stats children, and the forced-fail, listing and warm-up runs
beside them), and on pytest's shared default root two that finish together
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
``mutmut.runners.harness.PytestRunner.execute_pytest`` (replaced). Exit codes
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
from collections.abc import Callable
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
TEST_POLLUTION = (
    "mutineer: the stats run was split over processes; a test may depend on another test "
    "having run first in the same process (test pollution), which Mutineer doesn't support"
)
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
    _scope_baseline_runs()
    _split_stats_run()
    sys.argv = ["mutmut", command, *args]
    temp_root = tempfile.mkdtemp(prefix="mutineer-pytest-")
    owner = os.getpid()
    try:
        _own_basetemp(temp_root)
        cli()
    finally:
        # forked children leave through os._exit; one that escaped with an exception must
        # not remove the dir its siblings still use
        if os.getpid() == owner:
            shutil.rmtree(temp_root, ignore_errors=True)


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


def _own_basetemp(root: str) -> None:
    """Give every pytest session mutmut starts its own basetemp, made under ``root``.

    mutmut starts every session through ``PytestRunner.execute_pytest``; the replacement
    adds a plugin to ``pytest.main``'s ``plugins`` (keeping the caller's) whose
    ``pytest_configure``, marked ``tryfirst`` so it runs before pytest's ``tmpdir`` plugin
    reads the option, fills an unset ``--basetemp`` with a new dir. It runs in the process
    that calls ``pytest.main``, so each forked child gets its own dir. A ``--basetemp`` the
    project sets is left alone.
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

        kwargs["plugins"] = [*(kwargs.get("plugins") or []), OwnBasetemp()]
        return int(original(self, params, **kwargs))

    PytestRunner.execute_pytest = execute_pytest  # type: ignore[method-assign]


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
    import io
    import pickle
    import signal
    import traceback

    from mutmut.models.results import StatsResult
    from mutmut.state import state

    def run_chunk(chunk: list[str]) -> bytes:
        streams = sys.stdout, sys.stderr
        captured = io.StringIO()
        if show_output:
            sys.stdout = sys.stderr = captured
        try:
            exit_code = make_runner().run_stats(tests=chunk)
        finally:
            sys.stdout, sys.stderr = streams
        result = StatsResult(
            exit_code=exit_code,
            tests_by_mangled_function_name=dict(state().tests_by_mangled_function_name),
            duration_by_test=dict(state().duration_by_test),
            stats_time=state().stats_time or 0.0,
            function_dependencies=dict(state().function_dependencies),
        )
        return pickle.dumps({"result": result.to_dict(), "output": captured.getvalue()})

    pids: list[int] = []  # in chunk order
    readers: dict[int, int] = {}  # pid -> read end of its pipe, until read
    running: set[int] = set()  # pids not reaped yet
    payloads: dict[int, bytes] = {}
    codes: dict[int, int] = {}
    try:
        for chunk in chunks:
            read_fd, write_fd = os.pipe()
            pid = os.fork()
            if pid == 0:
                try:
                    os.close(read_fd)
                    payload = run_chunk(chunk)
                    with os.fdopen(write_fd, "wb") as out:
                        out.write(payload)
                except BaseException:
                    traceback.print_exc()
                    os._exit(1)
                os._exit(0)
            os.close(write_fd)
            pids.append(pid)
            readers[pid] = read_fd
            running.add(pid)
        # read every pipe to the end before waiting: a child blocks on a full pipe
        for pid in list(readers):
            with os.fdopen(readers.pop(pid), "rb") as pipe:
                payloads[pid] = pipe.read()
        for pid in list(running):
            _, status = os.waitpid(pid, 0)
            running.discard(pid)
            codes[pid] = os.waitstatus_to_exitcode(status)
    finally:
        # only left over when this process failed half-way: leave no orphan behind
        for fd in readers.values():
            os.close(fd)
        for pid in running:  # not reaped yet, so the pid is still this child's
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)

    failed = 0
    for pid in pids:
        code = codes[pid]
        try:
            sent: dict[str, Any] | None = pickle.loads(payloads[pid]) if code == 0 else None
            result = StatsResult.from_dict(sent["result"]) if sent else None
        except Exception:  # an empty or cut-off payload
            result = None
        if result is None:
            failed = failed or code or 1
            continue
        if show_output and result.exit_code and sent and sent["output"]:
            print(sent["output"], end="")
        for key, of_key in result.tests_by_mangled_function_name.items():
            state().tests_by_mangled_function_name[key] |= of_key
        state().duration_by_test.update(result.duration_by_test)
        state().stats_time = result.stats_time  # as mutmut does; it overwrites it anyway
        for key, callers in result.function_dependencies.items():
            # several children can report the same function: keep every edge
            state().function_dependencies[key] |= callers
        failed = failed or result.exit_code
    return int(failed)


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
