# WP2 Rules file, rule map and pytest plugin

Status: done, not yet merged. All WP2 acceptance checks pass locally on 3.14.

## What's here

| File | Notes |
|---|---|
| `src/mutineer/rules.py` | `load_rules` (WP0, unchanged), plus `compare`, `rule_findings`, `collect_rule_map` |
| `src/mutineer/pytest_plugin.py` | `rule` marker; `--mutineer-rulemap=PATH` |
| `src/mutineer/cli/rules.py` | `mutineer rules check [--base REF]` |
| `src/mutineer/cli/init.py` | `mutineer init`; exports `RULES_TEMPLATE` |
| `tests/unit/rules/conftest.py` | fixtures `shop_clone(branch)` and `run_git` |
| `tests/unit/rules/test_rulemap_plugin.py` | the plugin, in-process via pytester |
| `tests/unit/rules/test_collect_rule_map.py` | `collect_rule_map` on the fixture and temp projects |
| `tests/unit/rules/test_rules_compare_findings.py` | `compare` and `rule_findings` |
| `tests/unit/rules/test_rules_cli_commands.py` | `rules check` and `init` through `cli.main` |
| `tests/e2e/test_rulemap_matches_mutmut.py` | slow: rule-map node ids vs mutmut's `mutmut-stats.json` |
| `tests/unit/test_cli.py` (WP0's) | removed `rules check` and `init` from the "stubs exit 4" cases |

## API

### `compare(base, head, *, rules_file=None) -> list[Finding]`
- An id at base missing at head raises `RulesError`:
  `rule SHIP-003 was deleted from the rules file; retire SHIP-003 instead of deleting it (keep the [[rule]] entry and set status = "retired")`.
  Several deleted ids are all named, comma-separated.
- One `rules_file_changed` (rules, INFO, human) per added or changed entry, sorted by
  id. Entries are compared as `Rule` models, so an explicit `status = "active"` or
  reordering is not a change (same as `gitdiff.rules_file_changed_ids`).
- `evidence`: `change` (`added` | `modified` | `retired` | `reactivated`), `after`
  (the rule as a dict) and `before` (absent for `added`).
- `base=None` (no rules file at base): every head rule is `added`.
- **Additive:** the keyword-only `rules_file` sets `location.file` on each finding.
  Without it, `location` is None. The positional contract is unchanged.

### `rule_findings(rules, rule_map) -> list[Finding]`
- One finding per rule id, in catalogue order (`unknown_rule_id`,
  `retired_rule_marked`, `rule_unguarded`), each group sorted by id.
- `evidence["tests"]` lists the marked node ids, sorted (empty for `rule_unguarded`).
- `location.file` is the first marked test's file; no line (the rule map has none).
  `rule_unguarded` has no location.
- A retired rule with no tests produces nothing.

### `collect_rule_map(root, config) -> RuleMap`
Runs, with `cwd=root` and `PYTHONDONTWRITEBYTECODE=1`:

```text
sys.executable -m pytest --collect-only -q --rootdir=. --ignore=mutants -p no:cacheprovider
    -p mutineer --mutineer-rulemap=<tmp>/rulemap.json
    <[tool.mutmut] pytest_add_cli_args_test_selection> <tests_dir> <pytest_add_cli_args>
```

- The map file lives in a system temp directory, never in the project.
- Exit 0 or 5 (nothing collected) is success. Anything else raises `RulesError` with the
  last `ERROR`/`E ` line of the output and "fix collection so `pytest --collect-only`
  passes, then rerun".
- A missing map file after success raises `RulesError` (plugin blocked).
- Bad `[tool.mutmut]` types raise `ConfigError`.

## Decisions

- **Which pytest args.** mutmut's own collection (`list_all_tests` in
  `runners/harness.py`) runs `--rootdir=. … <test_selection> <pytest_add_cli_args>` from
  `mutants/`. Collection mirrors that from `root`, so node ids match. Including
  `pytest_add_cli_args` matters when they're needed for collection to succeed
  (`-p plugin`, `--import-mode`, Django settings). The deprecated `tests_dir` is
  appended to the selection, as mutmut does.
- **Read with `tomllib`, not mutmut.** Only `pyproject.toml` `[tool.mutmut]` is read.
  mutmut falls back to `setup.cfg [mutmut]` when that table is absent; Mutineer doesn't
  (yet). See handoff.
- **`config` is unused.** The contract takes it, but selection comes from mutmut's
  settings, not `[tool.mutineer] test_paths`, because the ids must match mutmut's.
- **`-p mutineer`** (the entry-point name, not the module path). pytest resolves `-p`
  names through `pytest11` entry points, so the plugin loads even with
  `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and it's a no-op when already autoloaded. Passing
  `-p mutineer.pytest_plugin` would register the module twice and fail. This needs
  mutineer installed in the project's environment, which the spec already requires.
- **Deselection never hides rule tests.** The plugin's
  `pytest_collection_modifyitems` is `tryfirst`, so it records items before `-m`/`-k`
  deselection (pytest's own and most plugins'). No `-m ""` override is passed.
- **Plugin identity.** A rule test is any item with at least one id from
  `item.iter_markers("rule")`: function, class, module `pytestmark`, and
  `pytest.param(..., marks=...)`. Several ids per mark and stacked marks are merged,
  deduplicated and sorted. Non-string args are kept as `str(arg)`, so they show up as
  `unknown_rule_id` instead of vanishing. An empty `@pytest.mark.rule()` contributes no
  ids, so the test is not in the map (it still matches `-m rule` in WP3's rule pass).
- **No overhead without the option**: the hooks return immediately.
- **`rules check`:**
  - root is the current directory;
  - missing rules file: exit 4, "run `mutineer init`";
  - base is `--base`, else `[tool.mutineer] base`, else main/master. An explicit bad
    `--base` is an error (exit 4). Without one, if no base resolves (not a git repo,
    no main/master), it prints "Not compared with a base (…)" and carries on;
  - an invalid rules file at base is skipped with a note, so a broken base can't block
    fixing it;
  - prints each rule (status if not active, seam, tests) then findings as
    `SEVERITY kind: message`; exits 0 whenever it runs (findings don't change the exit).
- **`init`:**
  - writes the template to `[tool.mutineer] rules_file` only if nothing exists there;
  - the template is `version = 1` plus a commented example, and parses with
    `load_rules` (zero rules);
  - appends `mutants/` and `.mutineer/last-run.json` to `.gitignore` (created if
    absent) unless an equivalent line exists: `mutants`, `/mutants`, `/mutants/`; and
    `.mutineer`, `.mutineer/` or the leading-slash forms for the second;
  - adds a newline first if the file doesn't end with one. It's idempotent.

## Deviations from the spec
- `compare` has the extra keyword-only `rules_file` argument (additive).
- WP2 added `tests/e2e/test_rulemap_matches_mutmut.py` although `tests/e2e/` is listed
  for WP3/WP4. It's the only way to prove the acceptance item "node ids equal mutmut's
  `tests_by_mangled_function_name`". It calls `python -m mutmut run` as a subprocess and
  imports no mutmut internals.
- Edited WP0's `tests/unit/test_cli.py` to drop the `rules check` and `init` stub cases,
  which now work. Logged in handoff.

## Known limits
- `setup.cfg [mutmut]` isn't read (above).
- Under pytest-xdist with `-n` in addopts, collection happens in workers; the plugin
  hasn't been tried there.
- Plugins that deselect in a `tryfirst` hook registered before Mutineer's could still
  hide tests from the map.

## How I tested
- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`: clean.
- `uv run pytest -m "not slow"`: 139 passed. `uv run pytest -m slow`: 2 passed.
- Acceptance coverage:
  - fixture `main` maps exactly the two SHIP-003 tests
    (`test_fixture_main_maps_exactly_the_two_ship_003_tests`), and the slow e2e test
    checks they are mutmut's covering tests for `shop.shipping.x_quote` (on `main`
    exactly; on `agent-gap` plus `test_express_adds_fee`);
  - parametrized cases once each with brackets; class and module marks inherited;
    stacked and multi-id marks (plugin and `collect_rule_map` tests);
  - a user `-m` (and `-k`) in addopts doesn't hide rule tests;
  - `rules check --base main` after deleting SHIP-003 in a commit exits 4 with the
    retire message, marked `rule("RULES-001")`, as are the `compare` deletion and
    retirement tests;
  - `rules check` on the fixture lists SHIP-003 and its two tests and exits 0.
- Also: collection leaves `git status --porcelain --ignored` unchanged on a fixture copy
  whose `.gitignore` lacks `__pycache__/`; `mutants/` copies are ignored; collection
  errors become `RulesError`; autoload disabled still works.
- Dogfood: `uv run mutineer rules check` on this repo lists the five RULES-001 tests
  from `tests/unit` (the `[tool.mutmut]` selection) and reports the other seven rules as
  `rule_unguarded` (their packages haven't landed). Exit 0.
