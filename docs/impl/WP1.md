# WP1 Test analysis and tamper detection

Status: done, not yet merged. All WP1 acceptance checks pass locally on 3.14.

## What's here

| File | Notes |
|---|---|
| `src/mutineer/testscan.py` | static test discovery and rule-test identity (libcst); assertion, skip, parametrize and pinning records per test |
| `src/mutineer/checks/tamper.py` | `check(diff, root, rules=None, *, test_paths=("tests",)) -> list[Finding]` |
| `src/mutineer/cli/run.py` | `run --no-mutation` → `tamper_only(...)`: tamper findings as plain JSON; full `run` still raises "not implemented yet; pass --no-mutation" |
| `tests/unit/conftest.py` | `tamper_repo` fixture (base commit on `main`, edits on `work`, returns findings) |
| `tests/unit/test_testscan.py` | identity forms, what counts as a test, `is_test_file` |
| `tests/unit/test_tamper_detections.py` | every detection, both tiers; precedence; location; WARN vs BLOCK |
| `tests/unit/test_tamper_false_positives.py` | the spec's false-positive guard, plus moves, renames, strengthening |
| `tests/unit/test_tamper_either_tier.py` | `test_config_changed`, `eq_override_added`, `special_case_suspected`, `pins_implementation` |
| `tests/unit/test_tamper_fixture.py` | fixture acceptance (`agent`, `agent-gap`, `agent-tamper`, `agent-tamper-approved`) and the CLI |

`tests/unit/test_cli.py` (WP0): removed the one parametrize case that expected
`run --no-mutation` to be a stub.

## How it works

### Static rule-test identity (`testscan`)
- **Tests:** `test*` functions at module level and `test*` methods of `Test*` classes,
  nested classes included (every enclosing class must be `Test*`), `async def` too.
  Nothing inside a function is collected. Files: `test_*.py` / `*_test.py` under a
  test path (`"."` or `""` means everywhere).
- **Key:** `file::qualname`, where qualname joins classes and the function with `::`
  (`tests/x.py::TestA::test_b`). That is the pytest node id without parametrize
  brackets, so WP4's static/runtime agreement check is a plain bracket strip, as long
  as pytest's rootdir is the project root.
- **Supported rule markers:** `pytest.mark.rule(...)`, `mark.rule(...)` after
  `from pytest import mark` (any `as` alias), and `X.mark.rule(...)` after
  `import pytest as X`. On the function, on any enclosing class (decorator), in a class
  body's `pytestmark`, or in module `pytestmark` (single mark, list or tuple; plain or
  annotated assignment). Ids from all places merge, sorted and de-duplicated.
- **Unsupported forms** (not rule tests statically):
  - rule ids that aren't string literals (`rule(RULE_ID)`, f-strings);
  - marks built elsewhere and reused (`ship = pytest.mark.rule("X"); @ship`);
  - `pytestmark += [...]`, or `pytestmark` set inside `if`/`try`;
  - marks applied by `conftest.py` hooks (`pytest_collection_modifyitems`) or by
    `pytest.param(..., marks=pytest.mark.rule(...))` on one case;
  - `unittest.TestCase` classes not named `Test*`, and custom `python_functions` /
    `python_classes` / `python_files` settings.

### Detections (`checks/tamper.py`)
- Only changed test files are scanned, at base (`gitdiff.read_base_file`) and at head
  (disk). Renamed files pair base tests under the head path.
- **Tier** comes from the *base* markers: a test is a rule test if it was marked at
  base. Marking a test at head doesn't make its earlier edits rule-tier.
- **Deleted:** base key missing at head and no head test (in any changed test file)
  with the same normalised parameters and body (`ast.dump`, so formatting doesn't
  matter, and renaming the function still counts as a move). A move that drops one of
  the base rule ids gives `rule_marker_removed` at the new location.
- **Marker removed:** any base rule id missing at head; `rules` lists only the
  dropped ids.
- **Skipped:** a skip/skipif/xfail mark (pytest), `unittest.skip*`/`expectedFailure`,
  or a `pytest.skip()` / `pytest.xfail()` / `pytest.importorskip()` / `self.skipTest()`
  call that wasn't there at base. Marks are compared by normalised expression, so
  reformatting an existing skip isn't new. Module and class marks apply to each test.
- **Weakened / expected changed:** assertions (assert statements, `assert*` calls,
  `pytest.raises` / `assertRaises*`) are listed in source order and aligned base to
  head with `difflib` on their normalised form. Replaced pairs are checked:
  - `operator_changed`: base compare uses `==`/`is`, head compare uses other operators;
  - `truthiness_check`: base was a compare, head isn't (a head `and` of compares is
    allowed);
  - `assert_method_weakened`: `assertEqual`/`assertIs` (and the typed `assert*Equal`
    variants) to `assertTrue`/`assertIn`/`assertIsNotNone`, or a compare `assert` to
    one of those;
  - `raises_widened`: head expects `Exception`/`BaseException` (alone or in a tuple)
    and base didn't;
  - `literal_changed` (expected changed): same kind, method and operators, every
    non-literal operand identical, at least one literal operand different.
  - `assertion_removed`: the head has fewer assertions than the base, located at the
    head assertion that follows the removed one (or the `def` line).
  - Parametrize: per argnames, fewer cases is `parametrize_case_removed` (weakened);
    the same count with a base case missing is `parametrize_case_changed` (expected
    changed). Adding or reordering cases is fine.
  Weakened beats expected changed regardless of which assertion comes first.
- **Evidence:** `test`, `rules`, `before`, `after` (absent when there is no head
  counterpart), `base_line`, plus `change` naming the detail (`operator_changed`, …).
  For `impl_test_changed`, `change` names the detection (`deleted`, `skipped`,
  `weakened`, `expected_changed`) and `detail` the sub-change.
- **Severity:** BLOCK, or WARN when every rule in the finding's `rules` is in
  `Diff.rules_file_changed_ids`.
- **`test_config_changed`:** any change to a `conftest.py`, `pytest.ini` or `tox.ini`
  (added, deleted, modified; a pure rename is ignored); `setup.cfg` only when
  `[tool:pytest]` differs; `pyproject.toml` when `[tool.pytest]` (covers
  `ini_options`), `[tool.mutineer]` or `[tool.mutmut]` differs. One finding per file.
- **`eq_override_added`:** a class (by qualname) in a changed non-test `.py` file
  that defines or assigns `__eq__` at head but not at base.
- **`special_case_suspected`:** see the deviation below.
- **`pins_implementation`:** a new or changed test (body differs from base and isn't
  a move) with a pin it didn't have before: a name imported as `from <source> import
  _x`, an `_x` attribute on a name imported from a source module, or a mock
  assertion (`assert_called*`, `assert_awaited*`, `assert_any_call`,
  `assert_has_calls`, `assert_not_called`, `call_args`, `call_args_list`). "Source
  module" = not pytest/unittest/mock, not relative, not `tests.*` / `test_*`.

### CLI `run --no-mutation`
- Loads config, diff and the head rules file (validated with `load_rules`; a missing
  file is an empty `RulesFile`).
- Scans `test_paths` from the head config **plus** the base `pyproject.toml`'s, so an
  agent can't hide a tampered test by narrowing `test_paths` in the same diff (that
  edit is also a `test_config_changed` WARN).
- Prints `{"status", "base", "head", "findings"}` JSON (or writes `--output`),
  whatever `--format` says. Finding ids stay empty (WP4's `verdict.decide` assigns
  them). Exit 2 on block, 1 on warn with `--strict`, else 0.

## Deviations from the spec
- **`special_case_suspected` is narrower than written.** Dogfooding the literal spec
  rule on this branch gave 17 WARNs, all false positives: status strings like
  `"deleted"`, `"added"`, `"block"` compared in new code and also used in tests.
  Now a literal is reported only if it does **not** also appear in tracked non-test
  source outside the changed lines (a real special case, like `"vip-gold-37"`, is
  usually known only to the tests). Findings are one per file and literal, with
  `evidence.lines`. This brought the dogfood run to 3 WARNs. Trade-off: special-casing
  on a value the code already uses elsewhere is missed.
- `check` takes `rules` as optional (`RulesFile | None`) and `test_paths` as a keyword.
  Static identity doesn't need the rules file; the parameter is kept for the contract.
- Extensions beyond the spec's lists: class-body `pytestmark`, `import pytest as X`,
  unittest skips, `pytest.xfail()`/`importorskip()`/`skipTest()`, parametrize
  case removal/change, `pytest.raises`/`assertRaises` counted as assertions,
  `[tool.mutineer]`/`[tool.mutmut]` changes as test config.

## Known gaps (for WP5 to probe)
- Neutralising without changing assertions: an early `return`, `if False:`, wrapping
  in `try/except AssertionError`, or moving assertions into a helper.
- Changing fixtures a rule test uses (a conftest edit is only a WARN).
- `special_case_suspected` only sees `Compare` and `match` literals, not dict lookups
  or `startswith` calls.
- A test file that doesn't parse at base or head is skipped; pytest's collection error
  then fails the baseline, which blocks in the full run but not under
  `--no-mutation`.
- Alignment can pair an unrelated deleted and inserted assertion as a "replace";
  that can over-report in rare rewrites.

## How I tested
- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`: clean.
- `uv run pytest -m "not slow"`: 230 passed on 3.14. WP1 has no slow tests.
- `uv run python scripts/export_schema.py --check`: clean (models unchanged).
- Acceptance: `agent-tamper` gives exactly one BLOCK `rule_test_weakened`, SHIP-003,
  `tests/rules/test_shipping_rules.py:12`, before `assert quote(10) == 5`, after
  `assert quote(10) >= 0`; `agent-tamper-approved` the same at WARN; `agent`,
  `agent-gap` and `main` nothing above INFO. Marked `rule("VERDICT-001")`, along with
  the rule-tier delete, unmark, skip and weaken tests.
- False-positive guard: renaming a local, adding an assertion, a hand-reformatted file
  and a real `ruff format` run, reordering, moving a rule test (to an existing or a new
  file) with its marker, adding a parametrize case.
- Dogfood (`uv run mutineer run --no-mutation` on this branch): `warn`, exit 0.
  - 3 × `special_case_suspected`: `"conftest.py"`, `"setup.cfg"` in `tamper.py`,
    `"test"` in `testscan.py`. All false positives: new code that handles those file
    names, with tests exercising it.
  - `test_config_changed` for the new `tests/unit/conftest.py`. Expected.
  - INFO `impl_test_changed` (`parametrize_case_removed`) for the `test_cli.py` edit.
    Correct.
