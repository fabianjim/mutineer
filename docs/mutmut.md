# mutmut notes

Mutineer drives mutmut 3.8 (`>=3.8,<3.9`). These are the mutmut behaviours the engine depends
on, verified against mutmut 3.8.1. When upgrading mutmut, check each one again: the fixture
(`tests/fixtures/make_fixture.sh DIR`) and the e2e tests in `tests/e2e/` exercise all of them.

## Driving it

- **Config without editing the user's files.** mutmut keeps its config in a private
  dataclass, `mutmut.configuration._config` (loaded by `_load_config`). `engine/_launch.py`
  replaces fields with `dataclasses.replace` before importing `mutmut.__main__`.
- **Private names Mutineer touches**, all in `engine/_launch.py`:
  `mutmut.configuration._load_config` and `._config`;
  `mutmut.mutation.file_mutation.mutate_file_contents` (predicting mutated functions);
  `run_clean_tests` on `mutmut.workers.isolation.ForkServerRunner` and `.ForkRunner`,
  `ForkRunner.run_forced_fail`, `mutmut.__main__.tests_for_mutant_names` and
  `mutmut.runners.harness.PytestRunner.run_forced_fail` (replaced), `PytestRunner.run_tests`,
  `mutmut.workers.isolation.run_in_fork` (called), `ForkRunner.test_runner` and
  `mutmut.state.state()` with its `tests_by_mangled_function_name` and `duration_by_test`
  (read), to skip the clean run and scope the forced-fail run (see "Failures and exit
  codes"); `collect_stats` on `ForkServerRunner` and `ForkRunner` (replaced), their
  `.list_all_tests` (called) and `.max_workers`, `ForkServerRunner.test_runner_class` and
  `.test_runner_args` (read), `mutmut.runners.harness.CollectTestsFailedException`
  (caught), `PytestRunner.run_stats` (called), `mutmut.models.results.StatsResult` and
  `state()`'s `function_dependencies` and `stats_time` (merged), to split the stats run
  (see "The cache"); `mutmut.runners.harness.PytestRunner.execute_pytest`
  (replaced), to give every pytest session its own basetemp (see "Temp dirs").
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
  `.meta` exit codes and the stats file's test map. `mutmut show` needs the pass's mutated
  files in `mutants/`, and a later run copies a file outside its `only_mutate` unmutated, so
  `show` then prints `!error: … Could not find original function` (and still exits 0). Read
  results and details right after each pass.
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
collection, about one run of the test suite. The launcher splits it: it lists the selected
tests, sorts them and deals them round-robin over `min(max_children, 4, tests)` concurrent
children, each a stats run of its own whose results are merged as mutmut merges its single
child's (with every caller kept in `function_dependencies`). More than 4 children bought
nothing on Mutineer's own suite, and round-robin was balanced where splitting by file was
not. Under `forkserver` the listing runs in a child, and one chunk is mutmut's own run.
Under `fork` (mutmut's default) the listing runs in the launcher, as mutmut's does, and
even one chunk runs in a forked child: every mutant forks from the launcher, so no test may
run there (see "Failures and exit codes").

## Failures and exit codes

- `mutmut run` exits 0 when it finishes, even if mutants survive.
- **A failing baseline** exits 1. All of these are BLOCK:
  - `Failed to collect list of tests`: pytest can't collect the suite;
  - `failed to collect stats`: a test fails in the stats run, which always runs, because the
    engine deletes `mutants/` and `mutmut-stats.json` before each pass; also a collection
    error while listing the tests for the split stats run. The failing children's pytest
    output is shown under `fork`; under `forkserver` it stays in the child.
- **The clean-test and forced-fail runs** come after stats. mutmut picks their tests by
  fnmatching the requested names against its test map, whose keys (`pkg.mod.x_f`) never
  contain `__mutmut_`, so with Mutineer's per-function globs it finds none and runs the whole
  selection. The launcher replaces that lookup: the forced-fail run uses only the tests
  covering the requested functions (the union of their test map entries, fastest first),
  and is skipped when that union is empty, because every requested mutant is then
  `no_tests` and none runs.
- **The forced-fail run** sets `MUTANT_UNDER_TEST=fail`, so every trampoline raises, and
  mutmut only checks that the run fails. The fastest covering test reaches a requested
  function, so it usually fails at once. If every covering test swallows the exception
  (`except Exception`), the launcher falls back to mutmut's own unscoped run, which fails
  with `FAILED: Unable to force test failures` only where it did before. Under `fork` it runs
  in a forked child, as under `forkserver`: a test it ran in the launcher would run a second
  time in every mutant's process.
- **The clean run** only checks an exit code, and the launcher always skips it: stats, the
  forced fail and every mutant each run in a fresh child, under `fork` forked from a
  launcher that never ran a test, so the clean run passes whenever stats did. A test that
  passes only once per process sees what a fresh process sees in every mutant, as under
  `forkserver`; mutmut's own `fork` path ran stats in the launcher, where such a test failed
  the second time (a false kill, or `Failed to run clean test`).
- **A split stats run** runs each test once, but with different neighbours and in a
  different order than one session, and session-scoped fixtures run once per chunk. A test
  that passes only after (or only without) certain others can now fail the stats run or
  start passing; mutants already run per-function subsets in their own
  order, so such a suite was already unreliable. A mutated function called at import time
  is recorded against the first test of each chunk instead of the first of the session
  (mutmut clears its hit list per test), so it can map to up to 4 tests instead of one.
  Durations measured under concurrency can be longer; they only lengthen mutant timeouts.
  Test pollution (a test that passes only after another ran first in the same process) is
  not supported. Under `fork`, when the two tests land in different stats children the stats
  run fails, a BLOCK, and the launcher prints a line naming test pollution as the likely
  cause; when they land in the same child the stats run passes and that test's mutants can
  be killed falsely, with no warning.
- **No test for any requested function:** exit 1, `Stopping early, because we could not find
  any test case`, and no results written.
- **Tests hit no mutant's module path:** `Stopping early, because tests recorded trampoline
  hits but none match any mutant key`. A project setup problem (e.g. `pythonpath` makes tests
  import `foo` instead of `pkg.foo`), raised as `EngineError` with the output.
- `FAILED: Unable to force test failures` is also an `EngineError`.

## Temp dirs

mutmut runs several pytest sessions at once: the mutants (`max_children` forked children),
and alongside them the stats run (split into children), the forced-fail run, the test
listing and the forkserver warm-up. By default every one of them
uses pytest's shared temp root, `$TMPDIR/pytest-of-<user>/`: each session makes a numbered
`pytest-<n>` there, re-points `pytest-current` at it and, when it finishes, removes old
numbered dirs and dead symlinks. Two sessions that finish together can both try to unlink
the same dead `pytest-current` and one fails with `FileNotFoundError`. The test outcome is
lost and the crash becomes the mutant's status: under `fork` a killed mutant (a survivor
disappears), under `forkserver` a timeout (exit 255). It happened 0–2 times in a large run,
so two identical runs could give different scores.

So for `run` the launcher makes one temp dir, `mutineer-pytest-*` in the system temp dir
(never under `mutants/`, which pytest collects from), and replaces
`PytestRunner.execute_pytest`, through which mutmut starts every pytest session, so that
each call adds a plugin to `pytest.main`'s `plugins`. Its `pytest_configure` hook (marked
`tryfirst`, so it runs before pytest's `tmpdir` plugin reads the option) sets
`config.option.basetemp` to a new dir under the launcher's dir when it is unset. The dir is
made in the process that calls `pytest.main`: each forked mutant and each split stats child
gets its own. With a given basetemp pytest makes no numbered dirs, no `pytest-current` link
and cleans up no other session's dirs. The launcher removes its dir when it finishes, also
on failure, and only in its own process: forked children leave through `os._exit`, and one
that escaped with an exception must not remove a dir its siblings still use.

A project that sets its own `--basetemp` (in `[tool.mutmut] pytest_add_cli_args`, pytest
`addopts` or `PYTEST_ADDOPTS`) keeps it: the plugin only fills an unset option, so every
session uses the project's dir, as without Mutineer. Concurrent children then share that
dir, and pytest empties it at the start of each session, so such a project can still get
wrong statuses; Mutineer doesn't detect or work around it. A relative `--basetemp` resolves
under `mutants/`, mutmut's working directory. Mutineer's own pytest runs (the baseline and
the rule map) run alone and keep pytest's default.

## Platforms

mutmut doesn't run on native Windows (it exits and points to WSL). macOS and Linux work.
