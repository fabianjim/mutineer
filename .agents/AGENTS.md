# Mutineer: instructions for coding agents

Mutineer checks whether agent-written work is really done before it merges. It runs
mutation testing (via mutmut 3.8) scoped to a diff, separates promised behaviour (rules)
from implementation detail, and catches tests that were weakened to get green.

## How to work here

- The code and the tests are the reference for how things behave; this file holds the
  invariants they must keep.
- Stay inside the scope you were given. If you need to change something outside it, stop
  and say so instead of working around it.
- When a choice belongs to the human (a contract, a dependency, a rule), investigate, bring
  the options with evidence and a recommendation, and wait for an answer.

## How Mutineer works

`mutineer run` takes the diff from `git merge-base <base> HEAD` to the files on disk
(uncommitted and untracked files count), runs tamper detection on the changed tests,
collects the rule map with pytest, runs mutmut on the changed functions (twice), triages the
mutants, and renders one verdict. When nothing the verdict depends on changed since the last
`run`, `run` shows the stored verdict without running pytest or mutmut (`--no-reuse` always
runs); `fresh` says whether it would, without running anything. `recheck` reruns the
agent's undisputed survivors; `dispute` (agent) and `resolve` (human) manage `.mutineer/disputes.toml`; `rules check`
validates the rules file; `rules propose`/`reject` (agent) manage
`.mutineer/proposals.toml`; `rules accept`/`dismiss` (human) turn proposals into rules and
record answers; `init` writes a template and never overwrites. `init` and `rules accept`
are the only commands that write outside `mutants/` and `.mutineer/`; `fresh` writes
nothing. Invariants and the reasons for them:

- **Promises over details.** Only behaviour named in `.mutineer/rules.toml` is protected.
  A survivor in code no rule covers is a question for a human, never work for the agent,
  because agents told to kill such mutants write tests that pin implementation.
- **Rule ids are never deleted**, only retired (`status = "retired"`); a deleted id is exit 4.
- **A rule test** carries `@pytest.mark.rule("ID")` on the function, its class or the module
  `pytestmark`. Rule-test identity has two sources: static (`testscan.py`, libcst), for
  tamper detection, because it must read the base version without running it; and runtime
  (the pytest plugin's rule map), for triage, because it sees node ids as mutmut does.
- **Tamper tier comes from the base markers.** A changed rule test is BLOCK, or WARN when
  every rule it guards changed in the rules file in the same diff; an implementation test
  is INFO. One finding per test.
- **Coverage is per function** (mutmut maps tests to functions). A rule-covered function is
  a changed function whose covering tests include a rule test.
- **Two mutation passes:** all mutants of changed functions against all tests, then the
  rule-covered functions' mutants against rule tests only. The rule pass selects with
  `-p mutineer --mutineer-rule-only`, never with `-m`: pytest keeps only the last `-m`, so
  adding one would drop the project's own.
- **Triage:** survived in both passes → `survivor_rule` for the agent (WARN); caught only by
  an implementation test → `guarded_by_impl_only` for a human; not rule-covered →
  `survivor_unowned` for a human; timeouts and other unclear results count in no score.
  A disputed `survivor_rule` is `mutant_disputed` for a human (WARN while open, INFO and in
  no score once confirmed; rejected, it is a `survivor_rule` again). A dispute is keyed by
  the mutmut function name plus the original and mutated text, never the mutant number.
- **`--format agent`** follows `models.AgentReport`, checked in as
  `docs/schema/agent.schema.json` (`scripts/export_schema.py`).
- **Growing the ledger.** Agents propose (`rules propose`) and never mark a test with an
  id that isn't accepted: triage takes rule ids from the rule map, and
  `--mutineer-rule-only` keeps any marked test, so it would send `survivor_rule` work and
  raise `unknown_rule_id`. Proposals are keyed by rule id, answers (`rules dismiss`) by
  mutmut function name. Answered findings stay in the report, under "Already answered".
  `accept` and `dismiss` refuse without a TTY unless `--yes` is passed; that is a
  convention, and the guard is visibility: `rules_file_changed` and `answer_added`.
- **Changed code mutmut can't mutate is reported, never hidden** (`unmeasured_change`:
  decorated functions, module-level code, files outside `source_paths`, excluded files,
  functions with no mutants), and is left out of every score.
- **mutmut, as verified for 3.8.1** (details in `docs/mutmut.md`):
  - never call it with an empty `only_mutate`: that means "mutate everything"; with no
    mutable changed function, run the baseline with pytest instead, so a broken suite
    still blocks;
  - delete `mutants/` before every mutmut run: its cache gives wrong answers across
    branches and passes. Deleted or renamed tests keep running from `mutants/tests/`,
    a test whose body changed keeps its old test map, and a file copied unmutated earlier
    fails a later run with `nothing matches`. The two passes use different pytest args,
    which resets the cache anyway, and mutants requested by name always rerun;
  - read `.meta` exit codes and `mutmut-stats.json` right after each pass (the next pass
    overwrites them), only for the names requested; never use `mutmut results`;
  - run mutmut's clean-test and forced-fail runs on the requested functions' covering tests
    only, and skip both when there are none; also skip the clean run under `forkserver`
    and in `run`'s rule pass under `fork`: `fork` forks mutants from the process that ran
    stats and they run only their covering tests, so a second clean pass of those there is
    what catches a test that passes only once per process;
  - under `forkserver`, split the stats run round-robin over `min(max_children, 4, tests)`
    children; never under `fork`, because there stats runs in the process the mutants fork
    from, and the clean run relies on that;
  - map exit codes to statuses yourself; use per-function globs, because an exact name
    raises `KeyError` when its function has no stats entry;
  - `engine/_launch.py` is the only module touching mutmut internals; it overrides mutmut's
    config in memory (the user's config is never edited) and drops `mutineer*` from
    `sys.modules` so Mutineer can mutation-test itself.
- **Reuse:** `run` reuses the last verdict only when its fingerprint (`fingerprint.py`)
  matches the stored one. The inputs and why each is there are in that module's
  docstring; a missed input gives a wrong verdict, which is worse than a slow run. A
  fingerprint is stored only when it didn't change during the run, and `recheck` clears
  it, because its merged verdict can differ from a fresh run's.
- **Verdict:** `block` if any finding is BLOCK, else `warn` if any is WARN, else `pass`;
  INFO never changes it. A failing baseline, including a suite pytest can't collect, is
  BLOCK. Exit codes: 0 pass or warn, 1 warn with `--strict`, 2 block, 3 `recheck` round
  cap, 4 Mutineer could not run (config, unknown base, deleted rule, usage error).
- **The fixture** (`tests/fixtures/make_fixture.sh DIR`) builds the toy `shop` repo with
  branches `main`, `agent`, `agent-gap`, `agent-tamper` and `agent-tamper-approved`. Their
  expected verdicts are asserted in `tests/e2e/verdict/test_verdict_e2e.py`.

## Rules

- Python 3.11 is the minimum; nothing newer than 3.11 in `src/`. Develop on 3.14
  (`.python-version`; `uv` installs it).
- Runtime dependencies are fixed: mutmut `>=3.8,<3.9`, pytest, libcst, pydantic, typer,
  rich. No new ones without asking. No LLM calls.
- Data contracts in `src/mutineer/models.py` and `errors.py` change only additively (new
  optional fields, new enum members, new models). Say so in your result.
- If you change one of the invariants under "How Mutineer works", update it here in the same
  change. Don't add other docs; they go stale.
- Test promised behaviour through public entry points. Don't assert on private names or
  mock call arguments unless the test is about that internal detail.
- Fix bugs test-first: a test that reproduces the bug and fails before your change.
- Tests that prove a rule in `.mutineer/rules.toml` carry `@pytest.mark.rule("<ID>")`. Never
  weaken, skip or delete one without the human changing that rule. Mutineer blocks it.
  Never edit `.mutineer/rules.toml` yourself; propose rules with `mutineer rules propose`,
  and never run `rules accept`, `rules dismiss` or `resolve`. Mark a new test that proves
  an accepted rule with that rule.
- Never assert on mutmut mutant numbers (`__mutmut_3`); assert on the mutated source text.
- Unit tests go in `tests/unit/`; end-to-end tests in `tests/e2e/`, marked
  `@pytest.mark.slow` and built on `tests/fixtures/make_fixture.sh`.
- Mutineer must never write outside `mutants/` and `.mutineer/` in a user's project,
  except in the explicit human commands `init` (`.gitignore`) and `rules accept` (the rules
  file, rule markers in tests).
- Every pytest run Mutineer starts passes `--ignore=mutants -p no:cacheprovider` and sets
  `PYTHONDONTWRITEBYTECODE=1`. Subprocesses use `sys.executable`.
- Experiments go in a temporary directory or a fixture copy, never in this repo's tracked
  files.
- Comments: `#` comments are lowercase with no trailing period. Docstrings can be normal
  sentences.
- Don't commit, branch or open PRs; the human does that.
- Commit messages and PR titles follow Conventional Commits: `type(scope): subject`, with
  type `feat`, `fix`, `refactor`, `perf`, `test`, `docs`, `style`, `build`, `ci` or `chore`,
  and an optional scope naming the area (`engine`, `cli`, `tamper`, …).

## Before you say you're done

```console
uv run ruff check
uv run ruff format --check
uv run mypy src
uv run python scripts/export_schema.py --check
uv run pytest -m "not slow"
uv run pytest -m slow            # if you touched the engine, the CLI or e2e tests
uv run mutineer run --no-mutation
```

Report any failure with its output. The full `uv run mutineer run` mutation-tests your
diff and takes several minutes on a large one; run it when you change behaviour.
