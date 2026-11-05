# WP3 mutmut engine

Status: done, not yet merged. All checks in `.agents/AGENTS.md` pass on 3.14, including
the slow e2e tests.

## What's here

| File | Notes |
|---|---|
| `src/mutineer/engine/functions.py` | diff → changed `FunctionRef`s and `UnmeasuredLocation`s. Pure libcst/tokenize; never imports mutmut |
| `src/mutineer/engine/_launch.py` | runs mutmut with an in-memory config override. The only module that touches mutmut internals. Checks mutmut is `>=3.8,<3.9` |
| `src/mutineer/engine/mutmut_engine.py` | `MutmutEngine` (`run`, `rerun`, `preflight`), the exit-code status map, the rule-pass `-m` rewrite, the mutant-line mapping |
| `src/mutineer/models.py` | additive: `UnmeasuredLocation.reason` gains `"no_mutants"` (handoff entry) |
| `tests/unit/test_engine_functions.py` | the diff → function mapping, case by case |
| `tests/unit/test_engine_mutmut_helpers.py` | status map, `-m` rewrite, line mapping, launcher version and usage checks, preflight errors |
| `tests/e2e/conftest.py` | `shop_copy`, `engine`, `ship_rule_map`, `diff_of` |
| `tests/e2e/test_engine_e2e.py` | the WP3 acceptance tests and the ground-truth corpus (all `slow`) |
| `.github/workflows/ci.yml` | dropped the `|| [ $? -eq 5 ]` guard on the e2e job |
| `docs/spike-mutmut.md` | two corrections, the agent-gap table, the ground-truth table, mutmut 3.8.1 cache traps |

## How `run` works

1. **Preflight** (`_launch mutineer-config`): mutmut is importable and in range, its config
   loads, and every `source_paths` entry exists. Otherwise `EngineError` with one line
   that says what to fix. Returns `source_paths`, `do_not_mutate`, `pytest_add_cli_args`
   and the test selection as mutmut resolves them, so `setup.cfg` configs and guessed
   source paths work too.
2. **Map the diff** (`functions.analyze`). Test files (under `[tool.mutineer] test_paths`)
   and non-`.py` files are skipped. Files outside `source_paths` are
   `outside_source_paths`, files matching `do_not_mutate` are `excluded_by_config`.
   Inside source files, each changed code line goes to its top-level function or method,
   or becomes `decorated` or `module_level`. Blank lines, comment-only lines and
   docstrings never count.
3. **Ask mutmut which changed functions get mutants** (`_launch mutineer-functions`, which
   calls mutmut's own `mutate_file_contents`). Those that get none become
   `no_mutants` (`functions.demote_unmutated`).
4. **No mutable function left:** mutmut is not called. The engine runs
   `python -m pytest --ignore=mutants -p no:cacheprovider <pytest_add_cli_args>
   <test selection>` in the root with `PYTHONDONTWRITEBYTECODE=1`. Exit 0 or 5 (no tests
   collected) passes; anything else is `baseline_failed`.
5. **Delete `mutants/`** (see Decisions).
6. **All pass:** `only_mutate` = the changed files, one glob per function
   (`<name>__mutmut_*`), the project's own `pytest_add_cli_args`. The baseline-failure
   messages give `baseline_failed=True` and the output tail; `Stopping early, because we
   could not find any test case` puts every function in `unreached` and reports each of
   their generated mutants as `no_tests` (no rule pass); any other non-zero exit is
   `EngineError`. Then the `.meta` exit codes for the requested
   functions' mutants and `mutmut-stats.json` are read straight away.
7. **Rule pass:** functions whose covering tests include a test with a non-empty entry in
   `rule_map`. `mutmut-stats.json` is deleted first, and `pytest_add_cli_args` gets the
   combined `-m` (`rule_pass_args`). Same outcome handling. If it stops early (no rule
   test reaches those functions after all) each of their mutants gets a rule result of
   `no_tests`. If the rule pass hits a baseline failure, `baseline_failed=True` and the
   all-pass results are kept.
8. **Details:** one `mutmut show` call (the launcher accepts several names) for every
   mutant that isn't killed or caught by the type check. `line`, `original` and `mutated`
   are filled from the first `-`/`+` lines (multi-line mutations are joined with `\n`).

`rerun(names, ...)`: same preflight and wipe; the names are grouped by function, each
function is found by its module name among the source files, and mutmut's generator is
asked whether it still has mutants. Then one rule pass with per-function globs. Only the
requested names are reported; a name mutmut no longer generates is `not_checked`.
`functions`, `unmeasured`, `unreached` and `tests_by_function` are empty.

## Decisions

- **`mutants/` is deleted at the start of every `run` and `rerun`.** mutmut's cache can
  give wrong answers here: deleted tests keep running from `mutants/tests/`, test maps go
  stale, and a file copied unmutated in one run is skipped when it's later in scope
  (all confirmed and written up in `docs/spike-mutmut.md`, "mutmut 3.8.1 cache
  behaviour"). The rule pass changes mutmut's config fingerprint, so mutmut resets its
  cache between passes anyway. The cost is one stats collection per pass. With the wipe
  disabled, the ENGINE-002 test and the warm-baseline test both fail.
- **`rerun` uses per-function globs, not exact names.** An exact name raises `KeyError`
  when its function has no stats entry, and after the wipe the engine can't know that
  before calling. Globs never raise; the extra mutants of the same functions are run and
  dropped. Reported names are exactly the requested ones (ENGINE-002).
- **Names that no longer exist in `rerun` are `not_checked`**, not dropped. If the agent
  edits the function, mutmut renumbers its mutants, so a stored name may now be a
  different mutant or none. See the handoff entry.
- **`no_mutants` is a new unmeasured reason** (additive models change). Without it, a
  changed function with nothing to mutate (e.g. `def f(): pass`, `__new__`) would show up
  as `unreached`, and if every changed function were like that, mutmut would "stop early"
  and report everything unreached.
- **Status mapping** is the spec table, implemented in `status_for_exit_code`; mutmut's
  status strings are never read.
- **Subprocesses get `PYTHONDONTWRITEBYTECODE=1`**, including mutmut's, so nothing writes
  `__pycache__` outside `mutants/` even when the project's `.gitignore` lacks it
  (ENGINE-001). `NO_COLOR=1` too; output is captured, never shown.
- **`only_mutate` patterns and globs are `glob.escape`d**, so paths and names with `[`
  match literally.
- **Pure deletions** (hunk length 0) count as a change to the function containing the
  line before the deletion point. Deleted module-level code leaves nothing to measure and
  isn't reported.
- **Unmeasured locations** are one per run of consecutive lines with the same reason,
  with `end_line` always set.
- **`MutmutEngine(python=None, max_children=None)`**: `python` defaults to
  `sys.executable`; `max_children` is passed to `mutmut run --max-children`.

## Deviations from the spec

- **Module names** (section 8, `functions.py`): the spec says "the path relative to its
  `source_paths` entry". mutmut uses the path relative to the project root with a leading
  `src.` removed, and mutmut wins (section 8 says so). For the fixture and this repo
  both readings agree. Corrected in `docs/spike-mutmut.md`.
- **Line mapping** (step 6): the anchor is the first line of mutmut's rendering of the
  function (leading comment, else decorator, else `def`), not the `def` line. The result
  is checked against the head file's text; if it doesn't match, the nearest matching line
  inside the function is used, else `line` is `None`.
- **`rerun` globs** instead of exact names (above).
- **`no_mutants`** reason (above).
- **Stop-early results** (step 3 of `MutmutEngine.run`): the spec says no results when
  mutmut stops early. The engine reports every generated mutant as `no_tests` instead, so
  an untested change always lowers `diff_score` (section 3 item 9), whether or not another
  changed function is tested. Decided by the human; see the handoff entry.
- **Launcher commands:** besides `run` and `show`, `_launch` has `mutineer-config` and
  `mutineer-functions`, and `show` takes several names. The call shape
  `_launch '<json overrides>' <args>` is unchanged.

## Known limits

**Open question for WP4** (in `docs/handoff.md`, "WP3 · questions for the human / WP4";
decide it before building triage). The `diff_score` question there is resolved: untested
mutants always count (see Deviations).

- A `-m` in the project's pytest `addopts` (not in `[tool.mutmut] pytest_add_cli_args`) is
  overridden by the rule pass's `-m`, because pytest keeps the last one. Handoff question.
- `mutate_only_covered_lines = true` in the project's mutmut config isn't accounted for in
  the "has mutants" check.
- Functions under `if TYPE_CHECKING:` or similar at module level are `module_level`, which
  is what mutmut does, though the reason name is a stretch.

## How I tested

- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`: clean.
- `uv run pytest -m "not slow"`: 163 passed (66 new).
- `uv run pytest -m slow`: 14 passed in about 25 s (macOS, 3.14).
- `uv run python scripts/export_schema.py --check`: clean (the `Verdict` schema doesn't
  include `EngineRun`, so the new reason doesn't change it).
- WP3 acceptance, all in `tests/e2e/test_engine_e2e.py`:

  | Acceptance item | Test |
  |---|---|
  | `agent`: two pricing functions, no other results, no rule pass, nothing unmeasured | `test_agent_branch` |
  | `agent-gap`: rule-covered, survivors on lines 8 and 10, express mutants survive the rule pass, line 3 `module_level`, all-pass statuses kept | `test_agent_gap_two_passes` |
  | failing test with fresh and warm `mutants/` | `test_failing_test_with_fresh_and_warm_mutants` |
  | failing test on `main` with no changed function | `test_failing_test_without_changed_functions` |
  | untested new module → `unreached`, no `EngineError` | `test_untested_new_module_is_unreached`, `test_untested_module_next_to_tested_change` |
  | changed `@property` → `decorated`, no FunctionRef | `test_changed_property_is_unmeasured` |
  | `-m "not slow"` → `-m "(not slow) and rule"`; a slow rule test doesn't run | `test_rule_pass_keeps_the_projects_marker_filter` (with a control run without the filter) |
  | ENGINE-001, `.gitignore` without `__pycache__/` | `test_run_writes_only_under_mutants` (run, rerun and the pytest-only baseline) |
  | ENGINE-002, stale `mutants/` from another branch | `test_stale_mutants_dir_from_another_branch` (also plants a stale test copy that would kill every pricing mutant) |
  | ground truth: predicted functions = mutmut's | `test_predicted_functions_match_what_mutmut_generates` |
  | `rerun` | `test_rerun_reports_only_requested_names`, `test_rerun_with_failing_test` |

- By hand: ran the engine against the fixture's `agent` and `agent-gap` branches and
  compared with the spike's table; reproduced each mutmut cache trap in a scratch copy.
