# mutmut notes

Mutineer drives mutmut 3.8 (`>=3.8,<3.9`). These are the mutmut behaviours the engine depends
on, verified against mutmut 3.8.1. When upgrading mutmut, check each one again: the fixture
(`tests/fixtures/make_fixture.sh DIR`) and the e2e tests in `tests/e2e/` exercise all of them.

## Driving it

- **Config without editing the user's files.** mutmut keeps its config in a private
  dataclass, `mutmut.configuration._config` (loaded by `_load_config`). `engine/_launch.py`
  replaces fields with `dataclasses.replace` before importing `mutmut.__main__`. These are
  the only private names Mutineer touches.
- **Scope to files** with `only_mutate`. An empty `only_mutate` means every file matches, so
  never pass it empty.
- **Scope to functions** with fnmatch globs: `mutmut run "shop.pricing.x_discount__mutmut_*"`.
  Never pass an exact mutant name: it raises `KeyError` when its function has no stats entry.
  A glob that matches no mutant fails with `AssertionError: Filtered for specific mutants,
  but nothing matches`, so only send globs for functions known to have mutants.
- **pytest keeps only the last `-m`.** Adding `-m rule` would silently drop a project's own
  `-m "not slow"`, so the rule pass selects tests with `-p mutineer --mutineer-rule-only`.
- **mutmut copies `tests/` into `mutants/tests/`.** Collecting from the project root picks up
  the copies with ids prefixed `mutants/`. Every pytest run passes `--ignore=mutants`; with
  it, rule-marker node ids match mutmut's exactly.
- **mutmut fails on import if it can't guess `source_paths`**, even for `mutmut --version`
  (`FileNotFoundError`). Check the config before calling it.

## Reading results

- **Exit codes** are in `mutants/<source path>.meta` (JSON): `exit_code_by_key[mutant]` is
  `0` survived, `1` killed, `None` not run. Map them to statuses yourself.
- **Coverage** is in `mutants/mutmut-stats.json`, `tests_by_mangled_function_name`, e.g.
  `"shop.shipping.x_quote": ["tests/rules/test_shipping_rules.py::test_..."]`. It is per
  function, not per line.
- **Results go stale.** `.meta` files and `mutmut results` keep results from earlier runs,
  including functions outside the current scope. Read only the names requested in this
  call, and never use `mutmut results`.
- **Each run overwrites the last.** A second run with different test args overwrites the
  `.meta` exit codes and the stats file's test map. Read results right after each pass.
- **Mutant text:** `mutmut show <name>` prints a unified diff of the function. A method is
  printed dedented, starting at its decorator if it has one.

## Names and lines

- **Function names:** `<module>.x_<func>__mutmut_<n>`; methods
  `<module>.xǁ<Class>ǁ<method>__mutmut_<n>` (the separator is U+01C1 `ǁ`, not a pipe).
  Mutant numbers can change between versions; never rely on them.
- **The module** is the file path relative to the project root, dotted, with a leading `src.`
  removed and `.__init__` dropped (mutmut's `get_mutant_name`). `source_paths = ["src/shop"]`
  gives `shop.pricing`, but `source_paths = ["lib/foo"]` gives `lib.foo.bar`, not `bar`.
- **Diff line → file line.** `mutmut show` hunk line numbers are relative to where mutmut's
  rendering of the function starts: the first comment line directly above it, else its
  decorator (a lone `@staticmethod` or `@classmethod`), else the `def` line. File line =
  that start line + (the hunk's old-side start − 1) + (the context lines before the first
  `-` line).

## What gets mutated

Checked against a real `mutmut run` by `test_predicted_functions_match_what_mutmut_generates`.

| Case | Mutated? | Mutineer reports |
|---|---|---|
| top-level function, `async def`, generator | yes | the function |
| method of a top-level class, including `__init__` | yes | `xǁClassǁmethod` |
| nested function | inside its outermost function | the outer function |
| lone bare `@staticmethod` / `@classmethod` | yes | the function |
| any other decorator (`@property`, `@lru_cache`, route handlers, two decorators) | no | unmeasured, `decorated` |
| module code, class attributes, class headers | no | unmeasured, `module_level` |
| methods of a nested class, functions under a module-level `if` | no | unmeasured, `module_level` |
| `__new__`, `__getattribute__`, `__setattr__`; an enum's `__init__` and `_generate_next_value_` | no | unmeasured, `no_mutants` |
| a function with nothing mutable (`pass`, `return x`) | no | unmeasured, `no_mutants` |
| a file libcst can't parse | no (copied unmutated) | unmeasured, `no_mutants` |

Type annotations and default values that aren't a plain name, number or string are not
mutated either (the function body still is). The last three rows can't be predicted from
structure, so the engine asks mutmut's own generator (`mutate_file_contents`, via
`_launch mutineer-functions`) which changed functions get mutants.

## The cache, and why `mutants/` is deleted before every run

1. **A config change resets everything.** mutmut fingerprints `pytest_add_cli_args` and
   `pytest_add_cli_args_test_selection` in `mutmut-stats.json` and resets all results when
   they change. The two passes use different args, so they never share a cache.
2. **Named mutants always rerun**, cached or not.
3. **Stale test copies.** `tests/` is copied with `copytree(dirs_exist_ok=True)`, which never
   deletes. A test deleted or renamed in the project keeps running from `mutants/tests/`.
4. **Stale test map.** An incremental run only collects stats for new test ids, so a test
   whose body changed keeps its old function mapping.
5. **Unmutated copies look mutated.** A file outside `only_mutate` is copied with a new
   mtime. If a later run includes it, mutmut reports it as "1 unmodified", never mutates it,
   and fails with `Filtered for specific mutants, but nothing matches`.

Because of 3 to 5 the engine deletes `mutants/` at the start of every `run` and `rerun`.
Because of 1 and 2 little of the cache would be reused anyway. The cost is one stats
collection, about one run of the test suite.

## Failures and exit codes

- `mutmut run` exits 0 when it finishes, even if mutants survive.
- **A failing baseline** exits 1. The message depends on cache state: `failed to collect
  stats` (fresh `mutants/`), `Failed to run clean test` (cached stats) or `Failed to collect
  list of tests` (collection error). All are BLOCK.
- **No test for any requested function:** exit 1, `Stopping early, because we could not find
  any test case`, and no results written.
- **Tests hit no mutant's module path:** `Stopping early, because tests recorded trampoline
  hits but none match any mutant key`. A project setup problem (e.g. `pythonpath` makes tests
  import `foo` instead of `pkg.foo`), raised as `EngineError` with the output.
- `FAILED: Unable to force test failures` is also an `EngineError`.

## Platforms

mutmut doesn't run on native Windows (it exits and points to WSL). macOS and Linux work.
