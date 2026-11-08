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
``duration_by_test`` (read). Exit codes
``EXIT_VERSION`` and ``EXIT_CONFIG`` mean mutmut is out of range or has no usable
``source_paths``; the reason is on stderr.
"""

from __future__ import annotations

import dataclasses
import json
import sys
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

    if command == "run":
        fork_clean_checked = FORK_CLEAN_CHECKED in args
        args = [a for a in args if a != FORK_CLEAN_CHECKED]
        _scope_baseline_runs(fork_clean_checked=fork_clean_checked)
    sys.argv = ["mutmut", command, *args]
    cli()


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
