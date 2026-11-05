# Mutineer v0.1 build spec

This is the handoff document for the agents building Mutineer v0.1. Read it with
`docs/prd.md` (what and why) and `docs/spike-mutmut.md` (verified facts about
mutmut). Where this spec and the PRD disagree, this spec wins. Where the spec
itself is unclear, don't guess: write the question in `docs/handoff.md`.

## 1. How the build runs

| Step | Work package | Runs | Depends on |
|---|---|---|---|
| 1 | WP0 Scaffold and contracts | alone | nothing |
| 2 | WP1 Test analysis and tamper | in parallel | WP0 merged |
| 2 | WP2 Rules file, rule map and pytest plugin | in parallel | WP0 merged |
| 2 | WP3 mutmut engine | in parallel | WP0 merged |
| 3 | WP4 Triage, verdict, reporters, `run`/`recheck` | alone | WP1–3 merged |
| 4 | WP5 Independent review | alone | WP4 merged |

- One agent per work package, each in its own git worktree and branch
  (`wp0-scaffold`, `wp1-tamper`, `wp2-rules`, `wp3-engine`, `wp4-verdict`).
- Each agent reads this whole spec but edits only the files its package owns
  (section 4).
- Each agent keeps `docs/impl/WPn.md`: plan, decisions, deviations from this spec,
  and how it tested. The next agent reads it.
- `docs/handoff.md` is shared and append-only. Use it for decisions that affect
  other packages, questions about this spec, and contract changes.
- **Contract changes during step 2:**
  - Any agent may make an *additive* change to `models.py` or `errors.py`: a new
    optional field, a new enum member, or a new model.
  - The same commit must append an entry to `docs/handoff.md`.
  - Nothing may be renamed, removed or retyped.
  - The human merging step 2 resolves any conflicts between packages.
- **Dogfooding:**
  - Once WP1 is merged, every later merge runs `mutineer run --no-mutation`
    (tamper only) on this repo.
  - From WP4 on, every merge runs the full `mutineer run`.

## 2. Ground rules for every package

- **Python:**
  - The minimum is 3.11: `requires-python = ">=3.11"`.
  - Develop on 3.14: `.python-version` holds `3.14`, and `uv` installs it, so the
    system Python doesn't matter.
  - CI tests 3.11 through 3.15.
  - **Support policy:** Mutineer supports every CPython version python.org still
    supports, and drops one in the first release after its end of life (3.11: October
    2027).
  - Use the 3.11 standard library freely: `tomllib`, `enum.StrEnum`, `typing.Self`.
    Nothing newer than 3.11 is allowed in `src/`.
- **Tooling:** `uv`, `ruff` (lint and format), `mypy --strict` on `src/`, `pytest`.
- **Runtime dependencies:** only these.
  - `mutmut>=3.8,<3.9`
  - `pytest>=8`
  - `libcst>=1.1`
  - `pydantic>=2.6`
  - `typer>=0.12`
  - `rich>=13`
- **No LLM calls anywhere in v0.1.**
- **Mutineer runs inside the target project's environment.** It is installed as a dev
  dependency next to pytest, because mutmut and the pytest plugin import the project's
  code. Subprocesses use `sys.executable`.
- **Every pytest run Mutineer starts** (rule-map collection, own baseline) passes
  `--ignore=mutants -p no:cacheprovider` and sets `PYTHONDONTWRITEBYTECODE=1`.
- **Tests:**
  - Unit tests go in `tests/unit/`. End-to-end tests go in `tests/e2e/`, are marked
    `@pytest.mark.slow`, and are built on `tests/fixtures/make_fixture.sh`.
  - Never assert on mutmut mutant numbers (`__mutmut_3`); assert on mutated source
    text.
  - Every acceptance test that proves a rule in `.mutineer/rules.toml` carries
    `@pytest.mark.rule("<ID>")`.

## 3. Behaviour decisions

These refine the PRD, and all packages build to them. Each one comes from the spike
or from review of an earlier draft.

1. **The diff runs from the merge-base to the working tree.**
   - The base is `git merge-base <base> HEAD`.
   - `<base>` is `--base` if given, else `[tool.mutineer] base`, else `main` if it
     exists, else `master`. The config default is `None`.
   - The head is the files on disk, including uncommitted edits and untracked files
     (`git ls-files --others --exclude-standard`), because mutmut mutates files on
     disk.
2. **Coverage is per function.** mutmut maps tests to functions, not lines. A
   *rule-covered function* is a changed function whose covering tests include at least
   one rule test.
3. **Two mutation passes:**
   - *all pass*: every mutant in changed functions, against all tests.
   - *rule pass*: only the mutants of rule-covered functions, with only rule tests.
   The rule pass filters with `-m`:
   - If the project's `pytest_add_cli_args` already contains `-m EXPR`, replace it with
     `-m "(EXPR) and rule"`.
   - Otherwise add `-m rule`.
   pytest keeps only the last `-m`, so appending would silently drop the user's filter.
4. **Triage by mutant status in each pass.**
   - Status groups:
     - **K** = killed or caught by type check
     - **S** = survived
     - **N** = no tests
     - **I** = timeout, suspicious, segfault, skipped, interrupted or not checked
   - Functions not rule-covered: S or N becomes `survivor_unowned` (human); I becomes
     `mutant_inconclusive`.
   - Rule-covered functions, by the result in each pass:

   | all pass | rule pass | Finding | Goes to |
   |---|---|---|---|
   | K | K | none | |
   | K | S or N | `guarded_by_impl_only`: "Only an implementation test catches this. Is it part of SHIP-003?" | human, INFO |
   | S or N | S or N | `survivor_rule`: "Nothing catches this. Test SHIP-003 through `shop.shipping.quote`." | agent, WARN |
   | S or N | K | `mutant_inconclusive` (the passes disagree, possibly a flaky test) | human, INFO |
   | I in either pass | | `mutant_inconclusive` | human, INFO |

   Why the second row goes to a human: in fixture branch `agent-gap`, the express-fee
   mutants survive the rule pass, but express shipping isn't part of SHIP-003. Telling
   the agent to cover them with SHIP-003 tests would make it pin unrelated behaviour.
5. **Changed code mutmut cannot mutate is reported, never hidden.** These become
   `unmeasured_change` with a `reason`, and are left out of every score:
   - lines in decorated functions (except a lone `@staticmethod`/`@classmethod`)
     (`reason: "decorated"`);
   - module-level and class-body lines outside functions (`"module_level"`);
   - changed `.py` files outside mutmut's `source_paths` that aren't under the test
     paths (`"outside_source_paths"`);
   - files matched by the user's `do_not_mutate` (`"excluded_by_config"`).

   Blank lines, comment-only lines and docstrings are ignored.
6. **Read only what we asked for, right after we asked.**
   - Results come from the exact mutant names requested in the current mutmut call.
   - Read the `.meta` files and `mutmut-stats.json` right after each pass, because the
     next pass overwrites both.
   - Never use `mutmut results`.
7. **Never call mutmut with an empty scope.** An empty `only_mutate` means "mutate
   everything". If there are no changed functions, skip mutmut and run the baseline
   suite directly with pytest (section 2 flags plus the project's test-selection
   args), so a broken suite still blocks.
8. **Rule-test identity has two sources, used for different jobs:**
   - *static* (libcst, WP1), for tamper detection, because it must read the base
     version without running it;
   - *runtime* (the pytest plugin via `collect_rule_map`, WP2), for triage, because it
     sees parametrized node ids exactly as mutmut does.

   A WP4 e2e test checks that the two agree on the fixture once parametrize brackets are
   stripped.
9. **Scores.** Statuses are taken from the relevant pass. Group I counts in neither
   score.
   - `diff_score` = K ÷ (K + S + N) over the all pass, every changed function. An
     untested new function lowers it; coverage can't hide it.
   - `rule_score` = K ÷ (K + S + N) over the rule pass, rule-covered functions.

## 4. Repository layout and ownership

```text
mutineer/
  pyproject.toml                 WP0  (incl. [tool.mutmut] and [tool.mutineer] for dogfooding)
  .python-version                WP0  ("3.14")
  README.md                      WP4
  .github/workflows/ci.yml       WP0
  .mutineer/rules.toml           WP0  (Mutineer's own rules, section 6)
  .agents/AGENTS.md, CLAUDE.md   agent instructions, already written (CLAUDE.md only imports AGENTS.md)
  docs/
    build-spec.md, prd.md, spike-mutmut.md   read; edit only where this spec says
    handoff.md                   everyone appends
    impl/WPn.md                  each package writes its own
    schema/                      WP0
    dogfood.md                   WP4
  spike/                         read-only reference
  src/mutineer/
    __init__.py                  WP0  (__version__)
    models.py                    WP0  (contracts, section 5; additive changes per section 1)
    errors.py                    WP0
    gitdiff.py                   WP0
    config.py                    WP0
    cli/__init__.py              WP0  (typer app; registers the command modules)
    cli/run.py                   WP0 stub → WP1 adds --no-mutation (tamper only) → WP4 owns from step 3
    cli/recheck.py               WP0 stub → WP4
    cli/rules.py                 WP0 stub → WP2
    cli/init.py                  WP0 stub → WP2
    rules.py                     WP0 writes load_rules; WP2 owns the rest
    pytest_plugin.py             WP0 stub (registers marker) → WP2
    testscan.py                  WP1
    checks/tamper.py             WP1
    engine/__init__.py           WP0  (Engine protocol)
    engine/functions.py          WP3
    engine/mutmut_engine.py      WP3
    engine/_launch.py            WP3
    triage.py                    WP4
    verdict.py                   WP4
    state.py                     WP4
    reporters/{terminal,markdown,json,agent}.py   WP4
  tests/
    fixtures/make_fixture.sh     WP0 (copy of spike/make_fixture.sh)
    unit/                        each WP adds its own files
    e2e/                         WP3 and WP4
```

## 5. Shared contracts

WP0 writes these exactly. Later changes are additive only, per section 1.
`docs/schema/verdict.schema.json` is generated from the models and checked in CI.

### `src/mutineer/models.py` (pydantic v2, frozen models)

```python
SCHEMA_VERSION = "1"

class Severity(StrEnum): BLOCK = "block"; WARN = "warn"; INFO = "info"
class Audience(StrEnum): AGENT = "agent"; HUMAN = "human"

class Location(BaseModel):
    file: str                     # repo-relative, forward slashes
    line: int | None = None       # 1-based, head version (see tamper for deleted tests)
    end_line: int | None = None

# ---- rules ----
RULE_ID_PATTERN = r"^[A-Z][A-Z0-9]*-[0-9]{3,}$"

class Rule(BaseModel, extra="forbid"):
    id: str                       # matches RULE_ID_PATTERN
    statement: str
    seam: str | None = None       # dotted path to the public entry point
    status: Literal["active", "retired"] = "active"

class RulesFile(BaseModel, extra="forbid"):
    version: Literal[1] = 1
    rules: list[Rule] = Field(default_factory=list, alias="rule")   # TOML [[rule]]

RuleMap = dict[str, list[str]]    # pytest node id -> sorted rule ids

# ---- diff ----
class Hunk(BaseModel):
    start: int                    # head-side first line, 1-based (from git diff -U0)
    length: int                   # head-side line count. 0 = pure deletion; then `start`
                                  # is the line after the deletion point (git new_start + 1)

class ChangedFile(BaseModel):
    path: str                     # head path
    old_path: str | None = None   # renames
    status: Literal["added", "modified", "deleted", "renamed"]
    hunks: list[Hunk]

class Diff(BaseModel):
    base: str                     # merge-base sha
    head: str                     # always "WORKTREE" in v0.1
    files: list[ChangedFile]
    rules_file_changed_ids: list[str] = []  # ids whose [[rule]] entry differs base -> head

# ---- engine ----
class MutantStatus(StrEnum):
    KILLED = "killed"; SURVIVED = "survived"; NO_TESTS = "no_tests"
    TIMEOUT = "timeout"; SUSPICIOUS = "suspicious"; SKIPPED = "skipped"
    TYPE_CHECK = "caught_by_type_check"; NOT_CHECKED = "not_checked"
    SEGFAULT = "segfault"; INTERRUPTED = "interrupted"

class FunctionRef(BaseModel):
    mutmut_name: str              # "shop.cart.xǁCartǁadd" (U+01C1) or "shop.pricing.x_discount"
    file: str
    qualname: str                 # "Cart.add"
    start_line: int               # the `def` line (decorators excluded)
    end_line: int

class MutantResult(BaseModel):
    name: str                     # full mutmut mutant name
    function: str                 # FunctionRef.mutmut_name
    run: Literal["all", "rule"]
    status: MutantStatus
    line: int | None = None       # head file line of the mutated source line
    original: str | None = None   # that line, stripped
    mutated: str | None = None

class UnmeasuredLocation(Location):
    reason: Literal["decorated", "module_level", "outside_source_paths", "excluded_by_config"]

class EngineRun(BaseModel):
    baseline_failed: bool
    baseline_output: str | None = None   # last 60 lines of test output
    functions: list[FunctionRef]         # changed, mutable functions
    unmeasured: list[UnmeasuredLocation] # changed lines mutmut cannot mutate (section 3 item 5)
    unreached: list[str] = []            # function names with no covering tests
    results: list[MutantResult]
    tests_by_function: dict[str, list[str]]   # from the all pass
    duration_s: float

# ---- output ----
class Finding(BaseModel):
    id: str = ""                  # assigned by verdict.decide: "<check>-<nnnn>"
    check: Literal["tamper", "rules", "mutation"]
    kind: str                     # catalogue in section 7
    severity: Severity
    audience: Audience
    rules: list[str] = []
    location: Location | None = None
    message: str                  # one sentence, plain English
    evidence: dict[str, Any] = {}
    next_action: str | None = None

class Score(BaseModel):
    killed: int
    total: int                    # per section 3 item 9

class Verdict(BaseModel):
    schema_version: str = SCHEMA_VERSION
    status: Literal["pass", "warn", "block"]
    base: str
    head: str
    rule_score: Score | None      # None when no function is rule-covered
    diff_score: Score | None      # None when mutation didn't run
    findings: list[Finding]
    duration_s: float
```

### `src/mutineer/errors.py`

```python
class MutineerError(Exception):
    exit_code = 4                 # message must say what to fix, in one line
class ConfigError(MutineerError): ...
class RulesError(MutineerError): ...
class EngineError(MutineerError): ...
class NeedsHumanError(MutineerError):
    exit_code = 3                 # recheck round cap reached
```

### `src/mutineer/engine/__init__.py`

```python
class Engine(Protocol):
    def run(self, diff: Diff, rule_map: RuleMap, root: Path) -> EngineRun: ...
    def rerun(self, names: list[str], rule_map: RuleMap, root: Path) -> EngineRun: ...
        # rule pass only, for these exact mutant names; `functions`/`unmeasured` empty
```

### Other WP0 functions

- `gitdiff.load_diff(root: Path, base: str | None, rules_file: str) -> Diff`. Hunks come
  from `git diff -U0`; untracked files are reported as `added`.
- `rules.load_rules(text: str) -> RulesFile`: parse plus validation only.
- `config.load_config(root: Path) -> Config`, reading `[tool.mutineer]`:
  - `base: str | None = None`
  - `rules_file = ".mutineer/rules.toml"`
  - `test_paths = ["tests"]`
  - `max_rounds = 3`

### Exit codes

| Code | Meaning |
|---|---|
| 0 | pass or warn |
| 1 | warn, when `--strict` is set |
| 2 | block |
| 3 | `recheck` round cap reached; needs a human |
| 4 | Mutineer could not run: any `MutineerError`, and CLI usage errors (typer is configured so that usage errors exit 4, not click's default 2) |

### CLI (v0.1)

```text
mutineer run [--base REF] [--format terminal|markdown|json|agent] [--output FILE]
             [--no-mutation] [--strict]
mutineer recheck [--format ...] [--output FILE] [--max-rounds N]
mutineer rules check [--base REF]
mutineer init
```

## 6. Mutineer's own rules (WP0 writes `.mutineer/rules.toml`)

| Id | Statement | Seam |
|---|---|---|
| VERDICT-001 | Weakening, skipping, unmarking or deleting a rule test without changing that rule in rules.toml yields BLOCK | `mutineer.checks.tamper.check` |
| VERDICT-002 | A failing baseline test suite yields BLOCK, whether or not any function changed | `mutineer.verdict.decide` |
| VERDICT-003 | INFO findings never change the verdict status | `mutineer.verdict.decide` |
| TRIAGE-001 | Only mutants that no test catches, in functions a rule test covers, are sent to the agent | `mutineer.triage.triage` |
| TRIAGE-002 | Changed code mutmut cannot mutate is reported as unmeasured and excluded from scores | `mutineer.triage.triage` |
| RULES-001 | A rule id is never deleted from rules.toml; retiring keeps it in the file | `mutineer.rules.compare` |
| ENGINE-001 | `run` and `recheck` without `--output` never write outside `mutants/` and `.mutineer/` | `mutineer.engine.mutmut_engine.MutmutEngine.run` |
| ENGINE-002 | Only mutants requested in the current run are reported | `mutineer.engine.mutmut_engine.MutmutEngine.run` |

## 7. Finding catalogue (stable `kind` strings)

| kind | check | severity | audience | Owner |
|---|---|---|---|---|
| `rule_test_deleted` | tamper | block* | human | WP1 |
| `rule_marker_removed` | tamper | block* | human | WP1 |
| `rule_test_skipped` | tamper | block* | human | WP1 |
| `rule_test_weakened` | tamper | block* | human | WP1 |
| `rule_expected_changed` | tamper | block* | human | WP1 |
| `impl_test_changed` | tamper | info | human | WP1 |
| `test_config_changed` | tamper | warn | human | WP1 |
| `eq_override_added` | tamper | warn | human | WP1 |
| `special_case_suspected` | tamper | warn | human | WP1 |
| `pins_implementation` | tamper | info | human | WP1 |
| `rules_file_changed` | rules | info | human | WP2 |
| `unknown_rule_id` | rules | warn | human | WP2 |
| `retired_rule_marked` | rules | warn | human | WP2 |
| `rule_unguarded` | rules | info | human | WP2 |
| `baseline_failed` | mutation | block | human | WP4 |
| `survivor_rule` | mutation | warn | agent | WP4 |
| `guarded_by_impl_only` | mutation | info | human | WP4 |
| `survivor_unowned` | mutation | info | human | WP4 |
| `unreached` | mutation | info | human | WP4 |
| `unmeasured_change` | mutation | info | human | WP4 |
| `mutant_inconclusive` | mutation | info | human | WP4 |

\* Becomes WARN when every rule the test guards appears in
`Diff.rules_file_changed_ids`.

- **One tamper finding per test**, using the first kind that applies in table order
  (deleted, then marker removed, skipped, weakened, expected changed).
- **Tamper evidence keys:**
  - `test`: `file::qualname`
  - `rules`
  - `before` and `after`: the relevant source lines (`after` is absent for deletions)
  - `base_line`: the line in the base version

  Deletions use the base file and line in `location`.
- `verdict.decide` assigns every finding id (`tamper-0001`, …) in output order.
  Producers leave `id` empty.

## 8. Work packages

### WP0 Scaffold and contracts

**Goal:** a repo every other agent can build on without touching shared files.

**Delivers:**
- `pyproject.toml`:
  - package `mutineer` in a `src/` layout, with a `mutineer` console script;
  - a `pytest11` entry point `mutineer = "mutineer.pytest_plugin"`;
  - dev dependencies;
  - `[tool.mutineer]`;
  - `[tool.mutmut]` for dogfooding: `source_paths = ["src/mutineer"]`, with test
    selection that excludes `tests/e2e`.
- The section 5 contracts, `errors.py`, `gitdiff.py`, `config.py` and `load_rules`.
- The `cli/` package with stubs that raise `MutineerError("not implemented yet")`.
- A plugin stub that registers the `rule` marker.
- `.mutineer/rules.toml` (section 6).
- `.python-version` (`3.14`) and `requires-python = ">=3.11"`.
- CI: ruff and mypy on 3.14; unit tests on 3.11, 3.12, 3.13, 3.14 and 3.15; slow e2e
  tests on 3.11 and 3.15, on `main` only.
- `tests/fixtures/make_fixture.sh` and the schema export script, with its CI check.
- `docs/impl/WP0.md`.
- `.gitignore` entries for `mutants/` and `.mutineer/last-run.json`.

**Acceptance:**
- `uv sync && uv run pytest -m "not slow"` passes.
- `uv run mutineer --help` lists `run`, `recheck`, `rules` and `init`.
- `uv run mutineer bogus` exits 4.
- Every model round-trips through JSON, `RulesFile` parses `[[rule]]` tables, and the
  schema file matches the models.
- `load_diff` on the fixture, branch `agent` against `main`:
  - `src/shop/pricing.py` is modified, with hunks `(3, 1)` and `(9, 6)`;
  - `tests/test_pricing.py` is modified.
- Unit tests with temporary repos also cover an uncommitted edit, an untracked new file,
  a deleted file, a rename, and a pure deletion (hunk length 0, with `start` per
  section 5).
- `rules_file_changed_ids` is `["SHIP-003"]` on `agent-tamper-approved` and `[]` on
  `agent-tamper`.
- `load_rules` rejects bad ids, a missing `statement`, unknown keys and
  `version != 1`.

### WP1 Test analysis and tamper detection

**Goal:** catch weakened tests statically, judged by tier, with almost no false
positives.

**Delivers:**
- `testscan.py`.
- `checks/tamper.py` with `check(diff, root, rules) -> list[Finding]`.
- `mutineer run --no-mutation` in `cli/run.py`, which runs tamper only and renders
  findings as plain JSON. WP4 replaces the rendering and adds the rules findings.

**Rule-test identity (static):** a test is a rule test if `pytest.mark.rule(...)` (or
`mark.rule(...)` after `from pytest import mark`) appears in any of these places:
- on the function;
- on its class;
- in module `pytestmark`, as a single mark or a list.

Other forms are unsupported; list them in the impl doc. Tests are `test_*` functions
and `Test*` class methods in `test_*.py` / `*_test.py` files under `test_paths`.

**Detections:** compare each test at base (`git show <base>:path`) and at head (disk),
keyed by `file::qualname`. The rules below are listed in precedence order (section 7).
- *Deleted:* present at base and absent at head, with no head test that has an
  identical normalised body. If one does exist, the test moved: no finding.
- *Marker removed:* a rule test at base that is still present at head but no longer
  marked.
- *Skipped:* a new `skip`/`skipif`/`xfail` marker or a `pytest.skip()` call.
- *Weakened:* any of:
  - fewer `assert` statements or `assert*` calls;
  - an `==`/`is` comparison turned into any other operator;
  - an assertion turned into a bare truthiness check;
  - `pytest.raises(X)` widened to `Exception`/`BaseException`;
  - `assertEqual`/`assertIs` replaced by `assertTrue`/`assertIn`.
- *Expected changed:* the same assertion shape with a changed literal operand.
- **Tiers:** rule tests produce the `rule_*` kinds. Implementation tests produce one
  `impl_test_changed` (INFO) per test, with `evidence.change` naming the detection.
- **Location:** the first affected assertion line at head (for `agent-tamper`, line 12,
  not the `def` on line 11).
- **Either tier:**
  - `test_config_changed`: edits to `conftest.py`, `pytest.ini`, `tox.ini`,
    `setup.cfg [tool:pytest]` or `pyproject.toml [tool.pytest.ini_options]`.
  - `eq_override_added`: a new `__eq__` in source.
  - `special_case_suspected`: a changed source line that compares to a literal (other
    than 0, 1, -1, "", None, True or False) which also appears in a test file.
- `pins_implementation`: a new or changed test that accesses a `_private` name from a
  source module, or asserts on mock calls (`assert_called*`, `call_args`).

**Acceptance:**
- Fixture `agent-tamper`: exactly one BLOCK finding, of kind `rule_test_weakened`, with
  rules `["SHIP-003"]` and location `tests/rules/test_shipping_rules.py:12`. Its
  evidence `before` is `assert quote(10) == 5` and `after` is
  `assert quote(10) >= 0`. Marked `rule("VERDICT-001")`.
- Fixture `agent-tamper-approved`: the same finding, at WARN.
- Fixtures `agent` and `agent-gap`: no tamper findings above INFO.
- One unit test per detection, for both tiers.
- **False-positive guard:** none of these produces a finding above INFO:
  - renaming a local variable;
  - adding an assertion;
  - `ruff format`;
  - reordering tests;
  - moving a rule test to another file along with its marker;
  - adding a parametrize case.

### WP2 Rules file, rule map and pytest plugin

**Goal:** promises are explicit, validated and linked to tests the way pytest users
already work.

**Delivers:** the rest of `rules.py`, `pytest_plugin.py`, `cli/rules.py` and
`cli/init.py`.
- **`rules.collect_rule_map(root, config) -> RuleMap`:**
  - runs `pytest --collect-only -q` with the section 2 flags and
    `--mutineer-rulemap=<tmpfile>`, plus the project's mutmut test-selection args;
  - returns the parsed map;
  - raises `RulesError` if collection fails.

  Until this lands, WP1 and WP3 use hand-built maps in their tests.
- **`rules.compare(base: RulesFile | None, head: RulesFile) -> list[Finding]`:**
  - raises `RulesError` ("retire SHIP-003 instead of deleting it") when an id present at
    base is missing at head;
  - returns `rules_file_changed` (INFO) for each changed entry.

  `run` and `recheck` (WP4) call it with the base version from git. So does
  `rules check --base REF`.
- **`rules.rule_findings(rules, rule_map) -> list[Finding]`:**
  - `unknown_rule_id` (WARN) for marker ids not in the file;
  - `retired_rule_marked` (WARN) for markers that reference a retired rule;
  - `rule_unguarded` (INFO) for active rules with no test.
- **Plugin:**
  - registers the `rule` marker, so `--strict-markers` passes;
  - `--mutineer-rulemap=PATH` writes the `RuleMap` JSON after collection;
  - has no other behaviour and no overhead when the option is absent;
  - accepts several ids per marker, and stacked markers.
- **`init`:** writes a commented `rules.toml` template and adds `mutants/` and
  `.mutineer/last-run.json` to `.gitignore` if they are missing. It never overwrites.
  `init` is outside ENGINE-001.

**Acceptance:**
- Fixture `main`: `collect_rule_map` returns exactly the two SHIP-003 tests. Their node
  ids equal the *values* listed under `shop.shipping.x_quote` in mutmut's
  `tests_by_mangled_function_name`.
- Parametrized tests appear once per case with bracketed ids. Class-level and
  module-level marks are inherited.
- A user `-m` in `addopts` doesn't hide rule tests from collection.
- `rules check --base main` after deleting SHIP-003 in a commit exits 4 with the retire
  message. Marked `rule("RULES-001")`.
- `rules check` on the fixture lists SHIP-003 and its two tests and exits 0.

### WP3 mutmut engine

**Goal:** turn a diff into trustworthy, scoped mutant results, using only mutmut.

**Delivers:** `engine/functions.py`, `engine/mutmut_engine.py` (`MutmutEngine`
implementing `Engine`), and `engine/_launch.py`. Add the agent-gap results below to
`docs/spike-mutmut.md` as a table.

- **`functions.py`:** given the diff, the head files, and mutmut's `source_paths` and
  `do_not_mutate`, return the changed `FunctionRef`s and the unmeasured locations with
  reasons, per section 3 item 5.
  - Module name: the path relative to its `source_paths` entry, dotted.
  - Function name: `<module>.x_<func>` or `<module>.xǁ<Class>ǁ<method>`.
  - A changed line in a nested function maps to its outermost function.
- **Ground-truth test:** for a corpus file of tricky cases, the predicted function set
  equals the set mutmut actually generates. Cases: methods, nested functions,
  `async def`, decorated functions, `@property`, `@staticmethod`, `@classmethod`,
  module code, class attributes, enums. Where mutmut differs from the spike notes,
  mutmut wins; update `docs/spike-mutmut.md`.
- **`_launch.py`:** `python -m mutineer.engine._launch '<json overrides>' <mutmut args>`.
  - It replaces mutmut's in-memory config with `dataclasses.replace` before importing
    `mutmut.__main__`, as proven in `spike/mutmut_launcher.py`.
  - It is the only module that touches mutmut internals, and it checks the mutmut
    version range.
  - Overrides used: `only_mutate` (never empty) and `pytest_add_cli_args`
    (the rule pass, section 3 item 3).
- **Status mapping:** map exit codes yourself; don't use mutmut's status strings.

  | Exit code | MutantStatus |
  |---|---|
  | 1, 3 | killed |
  | 0 | survived |
  | 5, 33 | no_tests |
  | 34 | skipped |
  | 35 | suspicious |
  | 36, 24, -24, 152, 255 | timeout |
  | 37 | caught_by_type_check |
  | -11, -9 | segfault |
  | 2 | interrupted |
  | None | not_checked |
  | anything else | suspicious |
- **`MutmutEngine.run`:**
  1. **Preflight:** mutmut is importable and in range, and `source_paths` resolves.
     Otherwise raise `EngineError` with a fix-it message.
  2. **No changed functions:** don't call mutmut (section 3 item 7). Run the baseline
     with pytest; if it fails, return `baseline_failed=True`.
  3. **All pass:** `only_mutate` = the changed source files; the arguments are one glob
     per changed function (`<name>__mutmut_*`). Inspect the outcome:
     - Output containing `failed to collect stats`, `Failed to collect list of tests` or
       `Failed to run clean test` means `baseline_failed=True`. Return with the output
       tail; nothing else runs.
     - `Stopping early, because we could not find any test case` means every requested
       function goes to `unreached`; there are no results.
     - Any other non-zero exit raises `EngineError` with the output tail.
  4. **Immediately read** `mutants/mutmut-stats.json` into `tests_by_function`, and the
     `.meta` exit codes for the requested names only. Functions without a stats entry
     go to `unreached`.
  5. **Rule pass:** the rule-covered functions are those with a covering test in
     `rule_map`. Run the same as step 3, with the rule `-m`, for those functions only.
     Read their `.meta` immediately.
  6. **Mutant details:** for every result that isn't killed, run `mutmut show` through
     the launcher. Map the diff to a head file line:
     - file line = `start_line` + (the hunk's old-side start − 1) + (the number of
       context lines before the first `-` line);
     - fill `original` and `mutated`.
  7. **Output:** capture subprocess output. Mutineer's own output shows no spinners.
- **`MutmutEngine.rerun(names, rule_map, root)`:** the rule pass only, for those exact
  names, read immediately, with details as in step 6. Pass explicit names only when
  their function has a stats entry; mutmut raises `KeyError` otherwise.

**Acceptance (e2e on the fixture, slow):**
- `agent`:
  - functions are `shop.pricing.x_discount` and `shop.pricing.x_loyalty_points`;
  - there are no results for `x_unchanged_helper` or `x_quote`, and no rule pass;
  - added lines 9–10 are blank, so they are not reported as unmeasured.
- `agent-gap`:
  - `x_quote` is rule-covered;
  - in the all pass, the survivors' mutated lines are `if total > FREE_SHIPPING_MIN:`
    (line 8) and `rate = _rate(None)` (line 10);
  - in the rule pass, the survivors also include `rate = EXPRESS_FEE` and
    `rate -= EXPRESS_FEE`;
  - `EXPRESS_FEE = 10` (line 3) is unmeasured with reason `module_level`;
  - the all-pass statuses still match the observed values after the rule pass has run.
- A failing test with a fresh `mutants/` directory gives `baseline_failed=True`. So does
  a failing test with a warm `mutants/`, and so does a failing test on `main` with no
  changed functions.
- A branch that adds an untested module puts its functions in `unreached`, with no
  `EngineError`.
- A changed `@property` body gives an unmeasured location with reason `decorated`, and
  no FunctionRef.
- A project `pytest_add_cli_args = ["-m", "not slow"]` becomes
  `-m "(not slow) and rule"` in the rule pass; a slow rule test does not run there.
- On a fixture copy whose `.gitignore` lacks `__pycache__/`, a run leaves
  `git status --porcelain --ignored` unchanged apart from `mutants/` and `.mutineer/`.
  Marked `rule("ENGINE-001")`.
- Results stay correct when `mutants/` holds stale data from a run on another branch.
  Marked `rule("ENGINE-002")`.

### WP4 Triage, verdict, reporters, `run` and `recheck`

**Goal:** one honest verdict that sends the right work to the right reader.

**Delivers:** `triage.py`, `verdict.py`, `state.py`, the reporters, `cli/run.py` (full)
and `cli/recheck.py`, `README.md`, and `docs/dogfood.md` (a template plus the first
entries).

- **`run`:**
  1. Load the config, diff and rules at base and head (`rules.compare`), plus the rule
     map.
  2. Run tamper and `rules.rule_findings`.
  3. Unless `--no-mutation`, run the engine and triage.
  4. Call `verdict.decide`, save state, render, and exit per section 5.
- **Triage** (section 3 item 4):
  - `unreached` functions become one `unreached` finding per function.
  - `survivor_rule`:
    - `rules` = the function's rule ids;
    - `next_action` names each rule's seam and the line, and says: "Test the promised
      behaviour through the seam. Don't assert on private names or mocks. If the mutant
      can't change behaviour visible through the seam, don't write a test; say so."
  - `guarded_by_impl_only` is grouped per function.
  - `survivor_unowned` is one finding per function, with the mutants in `evidence`:
    "Is this behaviour a promise? If so, add a rule."
  - `mutant_inconclusive` is one finding per function, with each mutant's statuses in
    both passes in `evidence`.
  - `unmeasured_change` is grouped per file, with reasons in `evidence`.
- **Scores:** per section 3 item 9.
- **`verdict.decide`:**
  - `block` if any finding is BLOCK, else `warn` if any is WARN, else `pass`. INFO never
    changes it (VERDICT-003).
  - `baseline_failed` is BLOCK.
  - Assigns finding ids.
- **Reporters:**
  - `json`: the `Verdict`.
  - `agent`: compact JSON with only agent-audience findings, plus status, scores and
    one instruction line.
  - `markdown`: a PR comment, with human questions in their own section.
  - `terminal`: rich, BLOCK first.
- **State:** `.mutineer/last-run.json` holds the full `EngineRun`, the `Verdict`, the
  base sha, the `survivor_rule` mutant names and `rounds` (0 after `run`).
- **`recheck`:**
  - No state file: exit 4 with "run `mutineer run` first".
  - Base sha differs from the current merge-base: exit 4 with "base moved; run
    `mutineer run`".
  - No `survivor_rule` names: re-render the stored verdict and exit by its status.
  - `rounds >= --max-rounds` (default from config, 3): raise `NeedsHumanError` (exit 3).
  - Otherwise:
    1. Call `engine.rerun(names)`.
    2. Replace those mutants' rule-pass results in the stored `EngineRun`.
    3. Rerun tamper and the rules checks on the current tree.
    4. Re-triage the merged `EngineRun`, so `rule_score` keeps its full denominator.
    5. Increment `rounds` and save.
- **`--no-mutation`:** tamper and rules findings only; no engine call; both scores
  `None`.

**Acceptance (e2e on the fixture):**

| Branch | Status | Exit | Must contain |
|---|---|---|---|
| `agent` | pass | 0 | `survivor_unowned` for x_discount and x_loyalty_points; no agent findings |
| `agent-gap` | warn | 0 (1 with `--strict`) | `survivor_rule` ×2 (SHIP-003, seam `shop.shipping.quote`, lines 8 and 10); `guarded_by_impl_only` for the two express mutants; `unmeasured_change` for line 3; rule_score 3/7; diff_score 5/7 |
| `agent-tamper` | block | 2 | `rule_test_weakened` |
| `agent-tamper-approved` | warn | 0 | `rule_test_weakened` at WARN; `rules_file_changed` |
| `main` plus a failing test (uncommitted) | block | 2 | `baseline_failed` (VERDICT-002) |

- The agent output for `agent-gap` contains no human-audience findings. Marked
  `rule("TRIAGE-001")`.
- A changed `@property` appears as `unmeasured_change` and changes neither score.
  Marked `rule("TRIAGE-002")`.
- `recheck` after adding a SHIP-003 test for `quote(50) == 0`:
  - the line-8 survivor is gone and `rule_score` becomes 4/7;
  - a fourth recheck exits 3.
- Static and runtime rule identity agree on every fixture branch.
- **Dogfood:**
  - `mutineer run` on WP4's own final PR (≤300 changed lines) completes in under
    2 minutes;
  - `--no-mutation` and the full run are recorded in `docs/dogfood.md`.

### WP5 Independent review

A fresh agent that built nothing reviews the merged result against the PRD, this spec
and the rule table. It:
- runs the full e2e suite and Mutineer on itself;
- tries to break tamper detection with realistic agent edits;
- writes `docs/impl/WP5-review.md`, ranked by severity.

It fixes nothing itself; fixes go back as new tasks.

## 9. Deferred (not v0.1)

- LLM rule drafting and `mutineer rules suggest`.
- Agent-error patterns and the mutmut operator hook.
- `mutineer audit`.
- A command that marks mutants equivalent and persists it (until then, the agent
  reports such mutants instead of testing them).
- A GitHub Action, skill or hook wrappers.
- Agent-side blocking of rule-test edits.
- Mutation budgets and sampling.
