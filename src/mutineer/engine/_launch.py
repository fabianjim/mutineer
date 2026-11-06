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

The two ``mutineer-*`` commands print one ``MARKER`` line holding JSON:

- ``mutineer-config``: the resolved config fields the engine needs, and mutmut's version;
- ``mutineer-functions``: for each file, the mangled function names mutmut generates at
  least one mutant for.

Private mutmut names used: ``mutmut.configuration._load_config``, ``._config`` and
``mutmut.mutation.file_mutation.mutate_file_contents``. Exit codes ``EXIT_VERSION`` and
``EXIT_CONFIG`` mean mutmut is out of range or has no usable ``source_paths``; the reason
is on stderr.
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

    sys.argv = ["mutmut", *argv[1:]]
    cli()


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
