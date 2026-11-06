# Handoff log

Shared, append-only. Add an entry for any decision that affects another work
package, and for any request to change a shared contract. Newest at the bottom.

Format:

```text
## YYYY-MM-DD · WPn · short title
What changed or what you need, and why. Which packages it affects.
```

## 2026-11-05 · spec · starting point
The build spec, the spike notes and the PRD snapshot were written before any code.
The spike's findings (two-pass triage, unmeasured changes, launcher, function-level
coverage) are already reflected in `docs/build-spec.md` section 3.

## 2026-11-05 · spec · independent review applied
A reviewer that hadn't seen the spec being written checked it against the fixture.
Its fixes are in the current spec:
- baseline-failure messages depend on cache state;
- results must be read after each pass, because the rule pass overwrites them;
- no empty `only_mutate`, and Mutineer runs its own baseline when nothing is mutable;
- `-m` filters are combined, not appended;
- `Engine.rerun` and the merge rules for `recheck`;
- `collect_rule_map` is owned by WP2;
- the CLI is split into one module per owner;
- `errors.py`;
- the line-mapping formula;
- exit codes 3 and 4;
- the full triage status table, with `no_tests` counted in the scores;
- gitdiff details: `-U0`, untracked files, zero-length hunks.

Deviation from the PRD: the PRD's `--max-rounds` is kept as a `recheck` option
(default 3, from config).

## 2026-11-05 · spec · Python version and repo layout
- Python minimum is 3.11, chosen on its own merits rather than mutmut's 3.10 floor:
  - 3.10 reached end of life on Oct 1, 2026;
  - 3.11 brings `tomllib` and `StrEnum`.
  Develop on 3.14; CI tests 3.11–3.15. Policy: support every CPython version
  python.org still supports.
- `.agents/` holds only `AGENTS.md` and `CLAUDE.md` (which contains only `@AGENTS.md`).
  Build documents stay in `docs/`, spike scripts in `spike/`.

## 2026-11-05 · WP0 · contracts landed, with small additive extras
Affects WP1–WP4. Details in `docs/impl/WP0.md`.
- **Models:**
  - Every model is frozen (`_Model` base). Use `model_copy(update=...)` to change one.
  - `Rule.id` enforces `RULE_ID_PATTERN` in the model.
  - `RulesFile` also accepts `rules=` by name, so JSON round trips work. TOML must
    still say `[[rule]]`; `load_rules` rejects a top-level `rules` key.
  - New, additive: `models.verdict_json_schema()`.
- **`load_rules`** also rejects duplicate rule ids.
- **`gitdiff` public helpers beyond `load_diff`:**
  - `resolve_base_ref(root, base)`;
  - `merge_base(root, base)` (WP4: use it for recheck's "base moved");
  - `read_base_file(root, sha, path)` (WP1: base versions of tests);
  - `HEAD_WORKTREE`.
- **`gitdiff` paths are relative to `root`** (`git diff --relative`). Anything under
  `mutants/`, and `.mutineer/last-run.json`, is never in a `Diff`.
- **CLI:**
  - `main()` returns the exit code. Commands should **return** their exit code (or
    raise `typer.Exit(code)`).
  - `MutineerError` maps to its `exit_code`, and usage errors map to 4.
  - typer 0.27 vendors click: catch `typer.exceptions.TyperException`, not `click.*`.
  - `OutputFormat` lives in `cli/run.py`.
- **Tests:** `tests/conftest.py` provides `shop_repo` and `git_repo`.
  - `shop_repo` is the fixture repo, built once per session. Call
    `shop_repo.checkout("<branch>")` first; it resets and cleans the worktree.
  - `git_repo` is an empty repo on `main`, with `.write()`, `.commit()` and `.git()`.
  - Both ignore the developer's global git config.
  - WP3 and WP4 e2e tests can build on them. Mutating `shop_repo` beyond a checkout
    must be undone; see `test_diff_runs_from_merge_base_not_base_tip`.

## 2026-11-05 · WP0 · warning: `ruff format` rewrites Markdown code blocks
Affects everyone. This ruff version formats Python code blocks inside `.md` files.
During WP0 a bare `uv run ruff format` rewrote the contract blocks in
`docs/build-spec.md` and the two spike scripts. They were restored and are
byte-identical to the `initial` commit. `pyproject.toml` now excludes `spike`, `docs`
and `.agents` from ruff, so the bare commands are safe again. Keep those excludes.

## 2026-11-05 · WP0 · requests for later packages
- WP4 (README owner): add `readme = "README.md"` to `[project]` in `pyproject.toml`
  when the README lands. WP0 left it out because hatchling fails on a missing readme.
- WP3/WP4: once slow e2e tests exist, drop the `|| [ $? -eq 5 ]` guard in the CI e2e
  job.
- Human: the repo's default branch is `master` (the `initial` commit), but the spec and
  CI assume `main`. CI's e2e job and `push` trigger only fire on `main`. Rename the
  branch or adjust `ci.yml`.

## 2026-11-05 · WP0 · resolved: branch is `main`, license is Apache-2.0
- The human renamed the default branch from `master` to `main`, so the request above is
  done. CI's `main`-only triggers now apply.
- `pyproject.toml` declares `license = "Apache-2.0"`. There is no `LICENSE` file yet.

## 2026-11-05 · WP1 · tamper check landed; notes for WP4
Details in `docs/impl/WP1.md`.
- **Signature:** `checks.tamper.check(diff, root, rules=None, *, test_paths=("tests",))`.
  `rules` (head `RulesFile`) is accepted but unused; pass `config.test_paths` (WP1's CLI
  passes head plus base `test_paths`, see `cli/run.py:_test_paths`, so narrowing them in
  the diff can't hide a test). Findings come sorted by file and line, with `id` empty.
- **Static test key** is `file::Class::test` (`::`-joined, no brackets): WP4's
  static/runtime agreement check only needs to strip parametrize brackets from node ids.
  `testscan.scan_source(text, path)` returns `TestFunc`s with `.key` and `.rules`.
- **`cli/run.py`:** `run --no-mutation` calls `tamper_only(root, base, output, strict)`,
  which prints `{"status", "base", "head", "findings"}` and ignores `--format`. WP4
  replaces it; a full `run` still raises "not implemented yet; pass --no-mutation".
- **`special_case_suspected` is narrower than the spec** (deviation): a literal that
  also appears in tracked non-test source outside the changed lines is not reported, and
  findings are grouped per file and literal (`evidence.lines`). The literal rule gave 17
  false-positive WARNs on WP1's own diff.
- **`test_config_changed`** also fires on `[tool.mutineer]` and `[tool.mutmut]` changes
  in `pyproject.toml`, and on any new or edited `conftest.py`. The dogfood run of WP1's
  branch is `warn` because of `tests/unit/conftest.py` (requested in the WP1 brief) and
  3 remaining special-case false positives.
- **Touched a WP0 test:** removed the `run ... --no-mutation --strict` case from
  `test_stubs_exit_4_with_a_message` in `tests/unit/test_cli.py`. WP2 may edit nearby
  lines in that list (`rules check`, `init`); expect a trivial merge.

## 2026-11-05 · WP2 · rules API and rule map landed
Affects WP3, WP4. Details in `docs/impl/WP2.md`.
- **`rules.compare(base, head, *, rules_file=None)`:** the keyword-only `rules_file` is
  additive and sets each finding's `location.file`. WP4: pass `config.rules_file`.
  Deleting an id raises `RulesError` ("retire SHIP-003 instead of deleting it"); `base`
  None means every head rule is `added`. Evidence: `change`, `before`, `after`.
- **`rules.rule_findings`:** one finding per rule id, in catalogue order;
  `evidence["tests"]` lists the marked node ids.
- **`rules.collect_rule_map(root, config)`:**
  - passes `[tool.mutmut]` `pytest_add_cli_args_test_selection` (+ deprecated
    `tests_dir`) **and** `pytest_add_cli_args`, after `--rootdir=.`, exactly as mutmut's
    own collection does, so node ids match;
  - reads `pyproject.toml` only; mutmut's `setup.cfg [mutmut]` fallback isn't read;
  - loads the plugin with `-p mutineer` (entry-point name), so it needs mutineer
    installed in the project's environment.
- **Plugin:** records the map in a `tryfirst` `pytest_collection_modifyitems`, before
  `-m`/`-k` deselection. WP3: the rule pass still filters with `-m rule`, which is
  unaffected (the plugin changes nothing without `--mutineer-rulemap`).

## 2026-11-05 · WP2 · edited WP0's `tests/unit/test_cli.py`
Removed `["rules", "check", "--base", "main"]` and `["init"]` from
`test_stubs_exit_4_with_a_message`, because they're implemented now. WP1 (adding
`run --no-mutation`) will probably edit the same parametrize list; the merger should
keep both removals.

## 2026-11-05 · WP2 · first slow e2e test exists
Added `tests/e2e/test_rulemap_matches_mutmut.py` (marked `slow`), which runs
`python -m mutmut run` on a clone of the fixture. `tests/e2e/` is WP3/WP4's
directory; this file is WP2's and has a unique basename. WP0's request to drop the
`|| [ $? -eq 5 ]` guard in the CI e2e job can now be done (ci.yml is WP0's; human or
WP4).
- WP2's test fixtures (`shop_clone`, `run_git`) live in
  `tests/unit/rules/conftest.py`, not `tests/conftest.py`.

## 2026-11-05 · WP3 · contract: new unmeasured reason `no_mutants`
Affects WP4 (triage of `unmeasured_change`). Additive change to `models.py`:
`UnmeasuredLocation.reason` gains `"no_mutants"`. It marks changed lines in a function
mutmut generates no mutants for: nothing mutable in it (`pass`, `return x`), names mutmut
never mutates (`__new__`, `__getattribute__`, `__setattr__`, an enum's `__init__` and
`_generate_next_value_`), `# pragma: no mutate`, or a file libcst can't parse. Without it
such a function would look `unreached`. Treat it like the other reasons: unmeasured, out of
every score. The `Verdict` schema is unchanged (it doesn't include `EngineRun`).

## 2026-11-05 · WP3 · engine behaviour WP4 relies on
Details in `docs/impl/WP3.md`.
- **Construction:** `MutmutEngine()` (optional `python=`, `max_children=`). `run` reads
  `[tool.mutineer] test_paths` itself and asks mutmut for its own config.
- **`mutants/` is deleted at the start of every `run` and `rerun`.** mutmut's cache gives
  wrong answers across branches and passes (see `docs/spike-mutmut.md`, "mutmut 3.8.1
  cache behaviour"). A user's own mutmut results in `mutants/` are lost. README (WP4):
  please say so.
- **Details** (`line`, `original`, `mutated`) are filled only for results that aren't
  killed or caught by the type check. For `guarded_by_impl_only`, take the text from the
  rule-pass record; the all-pass record of the same mutant (killed) has none.
- **Rule pass outcomes:** a baseline failure in the rule pass sets `baseline_failed=True`
  and keeps the all-pass results. If no rule test reaches the functions after all, each
  of their mutants gets a rule result of `no_tests`.
- **`rerun(names, ...)`:** reports exactly `names`, all with `run="rule"`. A name mutmut no
  longer generates (its function was edited and renumbered, or deleted) comes back
  `not_checked`, so `recheck` doesn't keep a stale `survived`. Note that after an edit to
  the function, a stored name may now point at a *different* mutant: compare
  `original`/`mutated` with the stored result if that matters. `rule_map` is unused; the
  rule pass selects with `-m`. `functions`, `unmeasured`, `unreached` and
  `tests_by_function` are empty.

## 2026-11-05 · WP3 · spec corrections (mutmut wins)
- Section 8, `functions.py`: the module name is the file path relative to the **project
  root**, dotted, with a leading `src.` removed and `.__init__` dropped, not "relative to
  its `source_paths` entry". Same result for the fixture and this repo.
- Section 8, step 6: the line-mapping anchor is the first line of mutmut's rendering of
  the function: a comment directly above it, else its decorator, else the `def` line.
  `FunctionRef.start_line` stays the `def` line, as the contract says.
Both are recorded in `docs/spike-mutmut.md`.

## 2026-11-05 · WP3 · questions for the human / WP4
1. **Untested-only changes and `diff_score`.** The spec says that when mutmut stops early
   (no changed function has any test), every function is `unreached` and there are *no
   results*. When only some functions are untested, their mutants do come back as
   `no_tests`. So an untested new function lowers `diff_score` (section 3 item 9) only if
   another changed function is tested. WP3 follows the spec as written. If both cases
   should count the same, the engine could report the generated mutants as `no_tests` in
   the stop-early case too; that's a small change in `MutmutEngine.run`.
2. **`-m` in pytest `addopts`.** The rule pass combines a `-m` from `[tool.mutmut]
   pytest_add_cli_args` as specified. A `-m` in the project's pytest `addopts` is
   overridden by the rule pass's `-m` (pytest keeps the last), so a test excluded by
   `addopts` (e.g. `-m "not slow"`) runs in the rule pass but not the all pass. That can
   show up as `mutant_inconclusive`. Reading `addopts` from every pytest config file is
   doable, but it isn't in the spec.

## 2026-11-05 · WP3 · resolved: untested changes always count in `diff_score`
Answers question 1 of "WP3 · questions for the human / WP4". The human decided that
untested mutants always count. When mutmut stops early because no changed function has a
test, `MutmutEngine.run` now reports every generated mutant as `no_tests` (`run="all"`,
with `line`/`original`/`mutated`), and every function is still in `unreached`. So an
untested change always lowers `diff_score`, whether or not another changed function is
tested. This deviates from section 8 step 3 ("there are no results"). Affects WP4: no
special case needed. `no_tests` mutants of `unreached` functions are counted like any
others (section 3 item 9), and `diff_score` is `None` only when mutation didn't run.
Question 2 (`-m` in `addopts`) is still open.

## 2026-11-05 · human · decided: the rule pass selects rule tests with a plugin option, not `-m`
Answers question 2 of "WP3 · questions for the human / WP4". WP4 does this.
- **Problem:** pytest keeps only the last `-m`, so the rule pass's `-m` silently replaces
  a `-m` in the project's pytest `addopts`. Tests the project excludes then run in the
  rule pass but not the all pass, and show up as `mutant_inconclusive`.
- **Decision:** the pytest plugin gets an option, `--mutineer-rule-only`, that deselects
  every test without a `rule` marker (reported through `pytest_deselected`, like `-m`).
  The rule pass passes `-p mutineer --mutineer-rule-only` instead of building a `-m`.
  The user's `-m`, from `addopts` or `[tool.mutmut] pytest_add_cli_args`, is never
  touched.
  - `-p mutineer` makes it work with `PYTEST_DISABLE_PLUGIN_AUTOLOAD`. It's a no-op
    when the plugin is already loaded, as in `collect_rule_map`.
  - The rule map must still be recorded before any deselection.
- **Ownership:** WP4 may edit `pytest_plugin.py` (WP2's) and
  `engine/mutmut_engine.py` (WP3's) for this, and the tests that cover them.
  `rule_pass_args` and its `-m` combining go away. The unit test of that function and
  the e2e test of a project `-m "not slow"` change accordingly; neither is a rule test.
- **Spec deviation:** build spec section 3 item 3 and the WP3 acceptance line about
  `-m "(not slow) and rule"` are superseded. The behaviour they protect stays: a project's
  `-m "not slow"` still keeps a slow rule test out of the rule pass. Add an e2e test for
  `-m` in pytest `addopts` too.

## 2026-11-05 · human · for WP5: false positives to investigate
Seen while merging WP1–WP3. WP5 should reproduce each one, judge how often it would
fire on real changes, and recommend a fix (WP5 fixes nothing itself).
1. **`test_config_changed` on docstring- or comment-only edits.** Any edit to a
   `conftest.py` warns, including a module docstring change. Seen on the comment cleanup
   commit (three `conftest.py` docstrings) and on every new `conftest.py` WP1–WP3 added.
   Question: should edits that leave the AST unchanged (docstrings, comments) be
   ignored, and are new `conftest.py` files that only add fixtures worth a WARN?
2. **`special_case_suspected` when the value's definition is edited in the same diff.**
   The check skips literals already known from source *outside the changed lines*. WP3
   edited the `UnmeasuredLocation.reason` line in `models.py`, so `"decorated"` stopped
   counting as known, and `engine/functions.py` got a WARN for comparing to it. Also
   flagged: `"__mutmut_"` in `mutmut_engine.py`, mutmut's name marker. Question: should
   values from `Literal[...]` annotations, enums or module constants count as known
   even when their line changed?
3. **`pins_implementation` on `_launch`.** WP3's unit tests import the private module
   `engine/_launch.py`, which the spec itself makes private. INFO only; check whether
   tests of a private module's own behaviour should count as pinning.

## 2026-11-05 · WP4 · done: the rule pass selects with `--mutineer-rule-only`
Implements "human · decided: the rule pass selects rule tests with a plugin option".
- `pytest_plugin.py`: `--mutineer-rule-only` deselects every test with no rule id and
  reports them through `pytest_deselected` (mutmut lists tests as collected minus
  deselected). A test counts when it has at least one id, the same definition as the rule
  map, so an empty `@pytest.mark.rule()` is deselected (`-m rule` used to keep it). The rule
  map is still recorded first, in the same `tryfirst` hook.
- `engine/mutmut_engine.py`: the rule pass (run and rerun) appends `RULE_PASS_ARGS`
  (`-p mutineer --mutineer-rule-only`) to the project's `pytest_add_cli_args`.
  `rule_pass_args` and its unit test are gone.
- e2e: `test_rule_pass_keeps_the_projects_marker_filter` (`-m` in `[tool.mutmut]`) is kept,
  and `test_rule_pass_keeps_a_marker_filter_from_pytest_addopts` is new.

## 2026-11-05 · WP4 · fix in WP3's `_launch.py`: Mutineer couldn't mutation-test itself
Found by the first full dogfood run. `python -m mutineer.engine._launch` imports the
`mutineer` package from its installed location before mutmut runs the tests in-process.
mutmut puts `mutants/src` first on `sys.path`, but a module already in `sys.modules` is
never imported again, so Mutineer's own tests ran the original code, no mutant was ever
hit, and every changed function came back `unreached` (diff score 0/1931). The launcher
now drops `mutineer*` from `sys.modules` just before importing `mutmut.__main__`
(`_forget_mutineer`). No effect on other projects. WP5: the slow e2e suite can't catch a
regression here, because the fixture project isn't Mutineer; only the dogfood run does.

## 2026-11-05 · WP4 · dogfood config: `also_copy` in `[tool.mutmut]`
mutmut runs the tests from `mutants/`, which holds only the source, `tests/` and a few
config files. `tests/unit/test_load_rules.py` and `tests/unit/test_models.py` read
`.mutineer/rules.toml` and `docs/schema/` from the project root, so the baseline failed
under mutmut. `pyproject.toml` (WP0's) now has `also_copy = [".mutineer", "docs/schema"]`.
It gives a `test_config_changed` WARN on WP4's diff, as it should.

## 2026-11-05 · WP4 · edited WP0's and WP1's CLI tests
- `tests/unit/test_cli.py`: removed `test_stubs_exit_4_with_a_message`; `run` and
  `recheck` are no longer stubs.
- `tests/unit/test_tamper_fixture.py`: the `run --no-mutation` tests pass `--format json`
  (the default is now the terminal report) and read the `Verdict` JSON, whose `status` and
  `findings` keys match the old payload. Removed `test_cli_full_run_is_still_wp4s`. The
  VERDICT-001 test's assertions are unchanged.

## 2026-11-05 · WP4 · `run`, `recheck` and triage: decisions other packages see
Details in `docs/impl/WP4.md`.
- `run --no-mutation` now renders like a full run (`--format`, default terminal); the
  `tamper_only` helper is gone. Both scores are `None`.
- `.mutineer/last-run.json` is a `mutineer.state.State` (`version`, `base_ref`, `base`,
  `rounds`, `survivor_names`, `engine_run`, `verdict`). `recheck` checks the merge-base of
  the stored `base_ref`, so `--base` carries over.
- Finding ids are numbered per check in output order (`tamper-0001`, `rules-0001`,
  `mutation-0001`); output order is BLOCK, WARN, INFO, then tamper, rules, mutation.
- An `unreached` function gets only the `unreached` finding, not also `survivor_unowned`;
  its `no_tests` mutants still count in `diff_score`.
- A failing baseline leaves both scores `None` and suppresses the per-mutant findings
  (`unmeasured_change` is still reported).
- A rule-covered mutant with no rule-pass result (and `not_checked` names from `rerun`) is
  `mutant_inconclusive`.
- A pytest collection error makes `collect_rule_map` raise, so `run` exits 4, not 2. The
  suite is broken either way, and CI fails either way; WP5 may want it to be a BLOCK.

## 2026-11-05 · WP4 · dogfood misses the 2-minute target on WP4's own diff
The spec expects WP4's PR to be ≤300 changed lines; it is about 1,300 lines of source
(triage, verdict, state, four reporters, both commands). The full run takes several
minutes: see `docs/dogfood.md` for the numbers and the breakdown. Two costs dominate:
mutmut runs the whole selected test suite three times per pass before any mutant (stats,
clean run, forced fail: about 60 s for `tests/unit`), and then about 8 mutants per second
on 10 cores. For WP5 and the human: options are a faster unit suite, a narrower
`pytest_add_cli_args_test_selection` for dogfooding, or splitting large PRs.

## 2026-11-05 · human · decided after WP4's first report
- **A suite pytest can't collect blocks.** Resolves "A pytest collection error makes
  `collect_rule_map` raise, so `run` exits 4" above. Done by WP4; see the next entry.
- **An empty `@pytest.mark.rule()` doesn't make a test a rule test.** Fine as built.
- **The 2-minute dogfood target is deferred.** Finish v0.1 first, performance after.
  Recorded in `docs/build-spec.md` (WP4 acceptance) and `docs/dogfood.md`.
- **No `LICENSE` file until the repo is published.** `pyproject.toml` keeps
  `license = "Apache-2.0"`.
- **Rule coverage that is too broad goes to WP5** (next entry but one).

## 2026-11-05 · WP4 · contract: `CollectionError`; a broken suite is a BLOCK
- Additive change to `errors.py`: `CollectionError(RulesError)` with an `output` attribute
  (the tail of pytest's output).
- `rules.collect_rule_map` (WP2's) raises it when pytest exits 1 or 2 during collection
  (a syntax or import error in a test module, an unknown marker under
  `--strict-markers`), or exits 4 with `ImportError while loading conftest`. pytest also
  exits 4 for a bad option or a missing plugin, so that case is told apart by pytest's
  message. Other failures stay `RulesError` (exit 4). Existing callers that catch
  `RulesError` are unaffected.
- `run` and `recheck` turn it into `baseline_failed` (BLOCK, exit 2,
  `evidence.stage = "collection"`), also under `--no-mutation`. Mutation is skipped, and
  so are the rule findings, because the rule map is unknown. Tamper detection still runs.
  `recheck` keeps the stored survivors, so it retries them once the suite collects again.
- `mutineer rules check` still exits 4 on any collection failure: it has no verdict.

## 2026-11-05 · WP4 · for WP5: rule coverage is broader than the rules
Seen on WP4's dogfood run (`docs/dogfood.md`). Coverage is per function, and a function
is rule-covered when any rule test calls it, directly or not. The VERDICT-001 tests go
through `cli.main(["run", ...])`, so `run`, `static_checks`, `emit` and `_test_paths`
count as covered by VERDICT-001. A surviving mutant there (say, a dropped
`encoding="utf-8"` in `emit`) becomes a `survivor_rule` telling the agent to "test
VERDICT-001 through `mutineer.checks.tamper.check`", which is unrelated, and would push
the agent to pin output details. This is the spec's triage table working as written; it
only shows up in layered code, not on the fixture. 185 of WP4's findings were like this.
WP5: judge how often this happens on real projects, and recommend a fix, for example:
- send survivors to the agent only from functions in, or called directly by, the rule's
  seam, and the rest to a human;
- at least name the mutated function in `next_action` rather than the rule's seam.

## 2026-11-05 · WP4 · cleanup after the first report
- **`setup.cfg [mutmut]`:** `collect_rule_map` (WP2's) now reads mutmut's test selection
  and pytest args the way mutmut does: `[tool.mutmut]` in `pyproject.toml` if the table
  exists, else `setup.cfg [mutmut]` (one list item per line). Closes the known limit in
  `docs/impl/WP2.md`, so the rule map and the engine can no longer disagree on such
  projects.
- **Launcher regression test:**
  `tests/e2e/verdict/test_mutineer_on_itself_e2e.py` mutation-tests a copy of Mutineer's
  own source. It fails with `_forget_mutineer` disabled (the changed function comes back
  `unreached`), so the dogfood bug can't return unnoticed.
- **Build spec updated** with every decision that overrode it: the rule pass's
  `--mutineer-rule-only` (section 3 item 3 and the WP2/WP3 lines), the `no_mutants`
  reason, wiping `mutants/`, collection errors as BLOCK (section 3 item 7, exit codes,
  `errors.py`), untested-only changes in `diff_score`, module names, the line-mapping
  anchor, the launcher's module purge, and the deferred dogfood target.
