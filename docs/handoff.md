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
