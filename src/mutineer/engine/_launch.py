"""Run mutmut with an in-memory config override. The only module that touches mutmut internals.

Usage (always with the project root as the working directory)::

    python -m mutineer.engine._launch '<json overrides>' run [FLAG] 'pkg.mod.x_f__mutmut_*' ...
    python -m mutineer.engine._launch '<json overrides>' show NAME [NAME ...]
    python -m mutineer.engine._launch '<json overrides>' mutineer-config
    python -m mutineer.engine._launch '<json overrides>' mutineer-functions FILE [FILE ...]

``run`` and ``show`` are mutmut's own commands. The overrides replace fields of mutmut's
config dataclass with ``dataclasses.replace`` before ``mutmut.__main__`` is imported, so the
user's ``pyproject.toml`` is never edited. ``show`` accepts
several names and prints a ``RECORD_SEP`` + name line before each mutant's output.

``run`` scopes mutmut's clean-test and forced-fail runs to the tests that cover the
requested functions (mutmut's own lookup never matches the per-function globs, so it runs
the whole selection), and skips both when there are none: no mutant would run. The forced
fail starts with the fastest covering test; if the covering tests don't fail, it falls back
to mutmut's unscoped run. The clean run only checks an exit code. Under ``forkserver`` it
always passes when stats did (each runs in a fresh child), so it is always skipped. Under
``fork`` mutants fork from the process that ran stats and the clean run checks that the
tests the mutants will run pass a second time there, so it is kept unless ``FLAG`` is
``--fork-clean-checked``: the launcher's own flag, meaning an earlier pass already checked
these tests. It is removed before mutmut parses its argv.

``run`` also splits mutmut's full stats run under ``forkserver``: the selected tests are
listed in a child, sorted and dealt round-robin into ``min(max_children, STATS_CHILDREN,
tests)`` chunks, each a stats run in its own concurrent child, merged as mutmut merges its
single child's result. With one chunk or fewer it is mutmut's own run. A collection error in
the listing makes the stats run fail, as one in the stats run itself does. Under ``fork``
stats runs in the launcher process, the one every mutant forks from, and the clean run checks
that the tests pass a second time there, so moving stats into children would break it.

``run`` gives every pytest session mutmut starts its own basetemp. Sessions run
concurrently (mutants, split stats children, and the clean, forced-fail, listing and
warm-up runs beside them), and on pytest's shared default root two that finish together
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
``mutmut.__main__.tests_for_mutant_names`` (replaced),
``mutmut.runners.harness.PytestRunner.run_forced_fail`` (replaced) and ``.run_tests``, and
``mutmut.state.state()`` with its ``tests_by_mangled_function_name`` and
``duration_by_test`` (read); ``ForkServerRunner.collect_stats`` (replaced), its
``.list_all_tests``, ``.max_workers``, ``.test_runner_class`` and ``.test_runner_args``,
``PytestRunner.run_stats``, ``mutmut.models.results.StatsResult``, and ``state()``'s
``function_dependencies`` and ``stats_time`` (merged);
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
FORK_CLEAN_CHECKED = "--fork-clean-checked"
"""``run`` flag: an earlier pass already ran mutmut's clean-test run for these tests."""
STATS_CHILDREN = 4
"""Most concurrent children for mutmut's stats run under ``forkserver``; more bought nothing."""
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
    fork_clean_checked = FORK_CLEAN_CHECKED in args
    args = [a for a in args if a != FORK_CLEAN_CHECKED]
    _scope_baseline_runs(fork_clean_checked=fork_clean_checked)
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


def _scope_baseline_runs(*, fork_clean_checked: bool) -> None:
    """Decide what mutmut's clean-test and forced-fail runs do.

    Both use only the covering tests of the requested functions, fastest first, and are
    skipped when there are none (every requested mutant is then ``no_tests`` and none
    runs). The clean run is skipped under ``forkserver``, and under ``fork`` when
    ``fork_clean_checked``. A forced fail that the covering tests don't fail falls back to
    mutmut's own unscoped one.
    """
    import fnmatch

    import mutmut.__main__ as mutmut_main
    from mutmut.runners.harness import PytestRunner
    from mutmut.state import state
    from mutmut.workers import isolation

    original_tests_for = mutmut_main.tests_for_mutant_names
    original_forced_fail = PytestRunner.run_forced_fail
    original_fork_clean = isolation.ForkRunner.run_clean_tests
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

    def fork_clean(self: Any, tests: Any) -> int:
        if covering == []:
            return 0
        return int(original_fork_clean(self, tests))

    def forced_fail(self: Any) -> int:
        if covering is None:
            return int(original_forced_fail(self))
        if not covering:
            return 1  # no requested mutant has a test, so none will run
        code = int(self.run_tests(mutant_name=None, tests=covering))
        if code == 0:  # the covering tests swallowed the failure: try them all, as mutmut does
            return int(original_forced_fail(self))
        return code

    # a list, not a set: pytest runs the tests in this order
    mutmut_main.tests_for_mutant_names = tests_for_mutant_names  # type: ignore[assignment]
    PytestRunner.run_forced_fail = forced_fail  # type: ignore[method-assign]
    isolation.ForkServerRunner.run_clean_tests = skipped  # type: ignore[method-assign]
    isolation.ForkRunner.run_clean_tests = (  # type: ignore[method-assign]
        skipped if fork_clean_checked else fork_clean
    )


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
    """Under ``forkserver``, split mutmut's full stats run over concurrent children.

    The selected tests are listed in a child, sorted and dealt round-robin into
    ``min(max_children, STATS_CHILDREN, tests)`` chunks, each run in its own child; the
    results are merged as mutmut merges its single child's. With one chunk or fewer, or
    for an incremental stats run, mutmut's own method runs unchanged. A listing that fails
    (a collection error) returns a non-zero code, so mutmut reports a failed stats run.
    ``ForkRunner`` is left alone: it runs stats in the process the mutants fork from.
    """
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
        return _stats_in_children(self, [ids[i::k] for i in range(k)])

    isolation.ForkServerRunner.collect_stats = collect_stats  # type: ignore[method-assign]


def _stats_in_children(runner: Any, chunks: list[list[str]]) -> int:
    """Run mutmut's stats collection on each chunk in its own child, merge into ``state()``.

    Returns 0 only when every child sent a result and every result's exit code is 0.
    """
    import os
    import pickle
    import signal
    import traceback

    from mutmut.models.results import StatsResult
    from mutmut.state import state

    def run_chunk(chunk: list[str]) -> bytes:
        child_runner = runner.test_runner_class(**runner.test_runner_args)
        exit_code = child_runner.run_stats(tests=chunk)
        result = StatsResult(
            exit_code=exit_code,
            tests_by_mangled_function_name=dict(state().tests_by_mangled_function_name),
            duration_by_test=dict(state().duration_by_test),
            stats_time=state().stats_time or 0.0,
            function_dependencies=dict(state().function_dependencies),
        )
        return pickle.dumps(result.to_dict())

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
    for pid, code in codes.items():
        try:
            result = StatsResult.from_dict(pickle.loads(payloads[pid])) if code == 0 else None
        except Exception:  # an empty or cut-off payload
            result = None
        if result is None:
            failed = failed or code or 1
            continue
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
