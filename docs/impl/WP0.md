# WP0 Scaffold and contracts

Status: done, not yet merged. All WP0 acceptance checks pass locally on 3.14, and the
unit tests also pass on 3.11 and 3.15.

## What's here

| File | Notes |
|---|---|
| `pyproject.toml` | hatchling, `src/` layout, `mutineer` console script, `pytest11` entry point, dev group (`ruff`, `mypy`), `[tool.mutineer]`, `[tool.mutmut]` (dogfooding), pytest/ruff/mypy config |
| `.python-version` | `3.14` |
| `uv.lock` | committed; CI uses `uv sync --locked` |
| `.gitignore` | includes `mutants/` and `.mutineer/last-run.json` |
| `.mutineer/rules.toml` | the eight rules from build spec section 6 |
| `.github/workflows/ci.yml` | lint, mypy and schema check on 3.14; unit tests on 3.11–3.15; slow e2e on 3.11 and 3.15, `main` only |
| `scripts/export_schema.py` | writes `docs/schema/verdict.schema.json`; `--check` exits 1 if stale (CI) |
| `docs/schema/verdict.schema.json` | generated from `Verdict` |
| `src/mutineer/models.py` | the section 5 contracts |
| `src/mutineer/errors.py` | as in section 5 |
| `src/mutineer/config.py` | `Config`, `load_config` |
| `src/mutineer/gitdiff.py` | `load_diff` plus helpers (see below) |
| `src/mutineer/rules.py` | `load_rules` only; WP2 owns the rest |
| `src/mutineer/engine/__init__.py` | `Engine` protocol |
| `src/mutineer/pytest_plugin.py` | registers the `rule` marker |
| `src/mutineer/cli/` | typer app and stubs for `run`, `recheck`, `rules check`, `init` |
| `src/mutineer/checks/__init__.py`, `reporters/__init__.py` | empty package markers |
| `tests/conftest.py` | shared fixtures `shop_repo` and `git_repo` |
| `tests/fixtures/make_fixture.sh` | byte-identical copy of `spike/make_fixture.sh` |
| `tests/unit/test_{models,load_rules,config,gitdiff,cli,pytest_plugin_marker}.py` | 97 tests |

## Decisions

### Models
- Every contract derives from a private `_Model` base with `frozen=True`. Use
  `model_copy(update=...)` to change one.
- `Rule.id` enforces `RULE_ID_PATTERN` in the model (`Field(pattern=...)`), so a bad id
  can't be built anywhere, not only through `load_rules`.
- `RulesFile` has `populate_by_name=True`, so both `RulesFile(rules=[...])` and the JSON
  round trip work: `model_dump_json()` writes `rules`, and that reads back. TOML still
  has to say `[[rule]]`: `load_rules` rejects a top-level `rules` key explicitly.
- `models.verdict_json_schema()` (additive) is the one place the schema comes from:
  `Verdict.model_json_schema(mode="serialization")` plus a `$schema` key.

### `load_rules`
- Errors are one line and name the entry, for example
  `rules file: [[rule]] #1 (A-001): unknown key 'tests'`.
- It also rejects duplicate ids. The spec doesn't list this, but two entries with the
  same id would make `rules_file_changed_ids` and the rule map ambiguous.

### `load_config`
- Missing `pyproject.toml` or a missing `[tool.mutineer]` table: defaults.
- Unknown keys are a `ConfigError` that lists the known keys. `max_rounds` must be ≥ 1.

### `gitdiff`
- **Public helpers beyond `load_diff`:**
  - `resolve_base_ref(root, base)`: `base`, else `main`, else `master`, else
    `ConfigError`. Callers pass `--base or config.base`.
  - `merge_base(root, base)`: full sha. WP4's `recheck` can use it for the "base
    moved" check.
  - `read_base_file(root, sha, path)`: `git show <sha>:./<path>`, or `None` if
    absent. WP1 and WP4 can use it.
  - `HEAD_WORKTREE = "WORKTREE"`.
- **How the diff is read:**
  - `git diff --name-status -z -M <merge-base>` gives the file list and statuses.
    It is NUL-separated, so odd paths are safe.
  - `git diff -U0 -M <merge-base>` gives the hunks. Its per-file sections are zipped
    with the name-status entries; both come from the same diff queue, so the order
    matches. A count mismatch raises `MutineerError` and is never guessed at.
  - Untracked files come from `git ls-files --others --exclude-standard -z`. Each is
    `added`, with one hunk `(1, line count)`, or none if the file is empty.
- **Flags:**
  - `--no-ext-diff --no-textconv --ignore-submodules -c core.quotepath=false`.
  - `GIT_OPTIONAL_LOCKS=0`, so Mutineer never rewrites `.git/index` (ENGINE-001 spirit).
  - Explicit `-M`, so the user's `diff.renames` setting doesn't matter.
- **Status mapping:**
  - Type changes (`T`) are `modified`.
  - Copies (`C`, only with `diff.renames=copies`) are `added`.
  - A deleted file has hunks of length 0.
  - A rename with no content change has `hunks == []`.
- **Paths are relative to `root`** (`git diff --relative`, and `ls-files` run in
  `root`). A project in a subdirectory of a monorepo sees only its own files, with
  paths relative to itself. This matches "repo-relative" when `root` is the repo top.
- **Mutineer's own output is never part of the diff:** anything under `mutants/`, and
  `.mutineer/last-run.json`, even if the user's `.gitignore` lacks them.
- **`rules_file_changed_ids`:**
  - Compares `[[rule]]` entries by id. Added, removed and changed ids are all listed,
    sorted.
  - Entries are normalised through `Rule`, so writing the default `status = "active"`
    explicitly, or reordering rules, is not a change.
  - Lenient by design: an unparsable rules file counts as having no entries.
    Validation is `load_rules`'s job, and WP4 calls it separately.
- **Errors:**
  - Not a repo, or no commit: `ConfigError`.
  - Unknown `--base`, or no `main`/`master`: `ConfigError` saying to pass `--base`.
  - Unrelated histories: `ConfigError`.

### CLI
- `mutineer.cli:main(argv=None) -> int` runs typer with `standalone_mode=False` and maps:
  - `MutineerError` → its `exit_code` (4, or 3 for `NeedsHumanError`), with the
    message on stderr as `mutineer: <message>`;
  - typer/click usage errors (`TyperException`, which covers click's vendored
    `UsageError`, `BadParameter`, …) → 4;
  - `typer.Exit(n)` → n;
  - a command's integer return value → the exit code. **WP1/WP4: return the exit
    code from the command function** (or raise `typer.Exit(code)`).
- typer 0.27 vendors click as `typer._click`, so `import click` exception classes do
  **not** match. Catch `typer.exceptions.TyperException`.
- Bare `mutineer` prints help and exits 0. `mutineer rules` with no subcommand prints
  help and exits 4 (a missing subcommand is a usage error).
- `OutputFormat` (a `StrEnum`: terminal, markdown, json, agent) lives in `cli/run.py`.
  `cli/recheck.py` imports it from there; WP4 owns both.
- Every stub declares its full v0.1 option set from section 5, so later packages only
  fill in bodies. Option names are fixed by the spec.

### pyproject
- `[tool.mutmut]` sets `source_paths = ["src/mutineer"]` and
  `pytest_add_cli_args_test_selection = ["tests/unit"]`, which excludes `tests/e2e`.
  Checked: mutmut 3.8.1's `_load_config()` reads both.
- `license = "Apache-2.0"` (an SPDX expression). There is no `LICENSE` file yet.
- There is no `readme` in `[project]`. `README.md` is WP4's, and hatchling fails if a
  declared readme is missing. Whoever lands the README should add
  `readme = "README.md"`.
- ruff: line length 100, `target-version = "py311"`, rules `E F W I UP B SIM RUF`.
  - `ǁ` is an allowed confusable (mutmut's separator).
  - `typer.Option`/`typer.Argument` are immutable calls for B008.
  - **`spike/`, `docs/` and `.agents/` are excluded.** This ruff version formats
    Python code blocks inside Markdown. Without the exclude, `ruff format` rewrites the
    contract blocks in `docs/build-spec.md` and the spike scripts. That happened once
    during WP0; both were restored and are byte-identical to the `initial` commit.
- mypy: `--strict` over `src`, with the pydantic plugin. `mutmut` is
  `ignore_missing_imports`, because it ships no type hints.

### CI
- `astral-sh/setup-uv@v6` with `python-version`, which overrides `.python-version`.
- The e2e job treats pytest exit 5 (no tests collected) as success until WP3/WP4 add
  slow tests. Remove `|| [ $? -eq 5 ]` once they exist.

## Deviations from the spec
- Added `scripts/export_schema.py`, which section 4 doesn't list. The spec asks for "the
  schema export script" but gives no path.
- `load_rules` also rejects duplicate ids (above).
- None about branches: the repo started on `master`, and the human renamed it to
  `main` during WP0, as the spec assumes.

## How I tested
- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`: clean.
- `uv run pytest -m "not slow"`: 97 passed on 3.14. Also passed on 3.11 and 3.15, each
  in a separate venv, with `--locked`.
- `uv run python scripts/export_schema.py --check`: clean.
- By hand:
  - `uv run mutineer --help` lists run, recheck, init and rules.
  - `uv run mutineer bogus` exits 4.
  - `mutineer run --format nope` and `mutineer run --base` (no value) exit 4.
  - The stubs exit 4 with "not implemented yet".
- Acceptance coverage (`tests/unit/test_gitdiff.py`, using the real fixture):
  - `agent` vs `main` gives `src/shop/pricing.py` modified with hunks `(3,1)`,
    `(9,6)`, and `tests/test_pricing.py` modified;
  - `rules_file_changed_ids` is `["SHIP-003"]` on `agent-tamper-approved` and `[]` on
    `agent-tamper`;
  - temporary repos cover an uncommitted edit, an untracked file, a deleted file,
    committed and staged renames, pure deletions (mid-file and at the top),
    mixed hunks, odd paths, a subdirectory root, merge-base versus base tip, base
    resolution and errors.
- No WP0 test carries `@pytest.mark.rule`: none of the section 6 rules is WP0's to
  prove.
