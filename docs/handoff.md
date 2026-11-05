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
