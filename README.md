# Mutineer

Mutineer checks whether agent-written work is really done before it merges. It answers
one question: **if the agent had got a promised behaviour wrong, would a test have
noticed?**

It builds on [mutmut](https://pypi.org/project/mutmut/) and adds what agent work needs:

- **Diff-scoped mutation testing.** Only the functions your branch changed are mutated.
- **A rules ledger.** Promised behaviour lives in `.mutineer/rules.toml`. Tests that prove
  a rule carry `@pytest.mark.rule("SHIP-003")`. Every other test stays free to change.
- **Tamper detection.** A rule test that was weakened, skipped, unmarked or deleted
  without a matching change to the rules file blocks the merge.
- **Triage.** Gaps in promised behaviour go to the agent; questions about everything
  else go to a human.

Mutineer is deterministic: no LLM calls.

## Install

Mutineer runs inside your project's environment, next to pytest, because mutmut and
Mutineer's pytest plugin import your code.

```console
uv add --dev mutineer        # or: pip install mutineer
```

Python 3.11 or newer. mutmut doesn't run on native Windows and requires WSL.

## Set up

```console
mutineer init
```

`init` writes a commented `.mutineer/rules.toml` and adds `mutants/` and
`.mutineer/last-run.json` to `.gitignore`. It never overwrites anything.

To let a coding agent use Mutineer on its own, name the agent (repeatable):

```console
mutineer init --agent claude     # Claude Code
mutineer init --agent codex      # Codex
```

This also writes Mutineer's skill, which teaches the agent the loop below, and its Stop
hook (see [The Stop hook](#the-stop-hook)):

| Agent | Skill | Stop hook merged into |
|---|---|---|
| `claude` | `.claude/skills/mutineer/SKILL.md` | `.claude/settings.json` |
| `codex` | `.agents/skills/mutineer/SKILL.md` | `.codex/hooks.json`, or `.codex/config.toml` when it already has a `hooks` table |

Other settings and hooks in those files are kept, a hook that already runs
`mutineer hook stop` isn't added twice, and a file that can't be merged is left unchanged
with the snippet to add by hand (exit 4). A SKILL.md that differs from your Mutineer
version is left alone; delete it and rerun `init --agent` to update it. In a project with
`uv.lock` the hook runs `uv run --no-sync mutineer`. Commit these files so every checkout
gets them.

mutmut needs to know where your code is. In `pyproject.toml`:

```toml
[tool.mutmut]
source_paths = ["src/shop"]
pytest_add_cli_args_test_selection = ["tests/"]
```

Write down your promises, each with a stable id and the public entry point (the *seam*)
it should be tested through:

```toml
# .mutineer/rules.toml
version = 1

[[rule]]
id = "SHIP-003"
statement = "Orders of $50 or more ship free"
seam = "shop.shipping.quote"
```

Mark the tests that prove a rule, on the test, its class, or the whole module:

```python
import pytest
from shop.shipping import quote

pytestmark = pytest.mark.rule("SHIP-003")


def test_well_over_threshold_ships_free():
    assert quote(80) == 0
```

Rules are never deleted. To drop one, set `status = "retired"` and keep the entry.

### Start the ledger

You don't have to write rules and markers by hand. An agent proposes, a human accepts:

```console
mutineer rules propose --prefix SHIP --statement "Orders of $50 or more ship free" \
  --seam shop.shipping.quote --test tests/rules/test_shipping_rules.py::test_small_order_pays
mutineer rules check             # lists the rules and the pending proposals
mutineer rules accept SHIP-001   # human: appends the rule, marks its tests
```

- `propose` writes only `.mutineer/proposals.toml`. It picks the next free id for the
  prefix (or takes `--id`, or `--rule ID` to add tests to an existing rule) and checks the
  id, the seam and the tests. To bootstrap a whole repo, pass a JSON array with
  `--batch FILE` (or `-` for stdin); one bad entry means nothing is written.
- `reject ID...` drops pending proposals.
- `accept ID...` or `accept --all` appends the rules to rules.toml and adds
  `@pytest.mark.rule("ID")` to the proposed tests.
- `dismiss FUNCTION... --reason TEXT` answers no to a `survivor_unowned` or
  `guarded_by_impl_only` question from the last run (use the finding's
  `evidence.function`). Later runs still list the finding, under "Already answered".

Commit rules.toml, `.mutineer/proposals.toml`, `.mutineer/answers.toml` and
`.mutineer/disputes.toml`; only `.mutineer/last-run.json` is ignored.

`accept`, `dismiss` and `resolve` record a human decision: without a terminal they refuse unless you
pass `--yes`. That is a convention, not a security boundary; the protection is that every
change shows up in the next run as a finding (`rules_file_changed`, `answer_added`).

## Use

```console
mutineer run                     # judge the diff from the merge-base to the working tree
mutineer run --format agent      # compact JSON for the implementing agent
mutineer recheck --format agent  # after the agent adds tests: rerun only its survivors
mutineer fresh                   # would `run` reuse the last verdict? exit 0 yes, 1 no
mutineer hook stop --agent claude|codex  # the Stop hook; `init --agent` installs it
mutineer dispute ID "<why>"      # agent: a survivor_rule no caller can observe
mutineer resolve ID... --confirm # human: agree (or --reject --reason TEXT)
mutineer rules check             # validate rules.toml and list each rule's tests
mutineer rules propose ...       # agent: propose a rule (or --batch FILE)
mutineer rules reject ID...      # drop pending proposals
mutineer rules accept ID...      # human: add proposed rules and mark their tests
mutineer rules dismiss FN... --reason TEXT  # human: answer no to a question
```

`run` compares the working tree (uncommitted and untracked files included) with
`git merge-base <base> HEAD`. The base is `--base`, else `[tool.mutineer] base`, else
`main`, else `master`.

When nothing the verdict depends on changed since the last `run` with the same mode,
`run` shows the stored verdict again, with the current `--format`, `--output` and
`--strict`, without running pytest or mutmut. It compares a fingerprint of the merge-base,
every change git sees in the whole repository, the files under mutmut's `source_paths`
and `also_copy` and your test paths (ignored or not), the rules and ledger files, the
config, the Python environment (interpreter, Mutineer, installed packages), and
`PYTEST_ADDOPTS`, `PYTEST_PLUGINS`, `PYTEST_DISABLE_PLUGIN_AUTOLOAD`, `PYTHONPATH` and
`PYTHONHASHSEED`. `mutineer fresh` (same `--base` and `--no-mutation`) prints `fresh` and
exits 0 when `run` would reuse; otherwise it prints `stale:` with what changed and exits
1. It runs nothing and writes nothing. After a `recheck` the next `run` is always real.

Reuse doesn't see ignored files outside those paths, changes inside git submodules
outside them, other environment variables, the network or other services, or flaky
tests. Pass `--no-reuse` when one of those matters. A report written with `--output`
into the repository is a change git sees, so the next `run` is real; write reports
outside the repository or to an ignored path.

| Option | |
|---|---|
| `--format terminal\|markdown\|json\|agent` | terminal (default), a PR comment, the full verdict, or agent JSON (schema: `docs/schema/agent.schema.json`) |
| `--output FILE` | write the report to a file instead of stdout |
| `--no-mutation` | tamper and rules checks only; no mutmut run, no scores |
| `--strict` | exit 1 on warn |
| `--no-reuse` | always run, even when `fresh` would say nothing changed |
| `recheck --max-rounds N` | round cap (default `[tool.mutineer] max_rounds`, 3) |

### The verdict

Every run ends in one status:

- **block**: a rule test was tampered with and the rules file doesn't change that rule,
  or the test suite fails on its own (including a test file or `conftest.py` pytest can't
  collect).
- **warn**: a mutant in code a rule test covers survives every test, an agent's dispute
  waits for a human, or a warn-level tamper or rules finding.
- **pass**: none of the above. Questions for a human are listed but never change the
  status.

Two scores sit beside it:

- **rule score**: mutants in changed functions that a rule test covers, killed by rule
  tests alone. The headline number.
- **diff score**: mutants in all changed functions, killed by any test.

Timeouts, other inconclusive mutants and equivalent mutants a human confirmed count in
neither score. Changed lines mutmut
can't mutate (decorated functions, module-level code, excluded files) are reported as
`unmeasured_change` and never count as tested.

### Who gets what

In a function a rule test covers, each mutant is run twice: against all tests and
against rule tests only.

| All tests | Rule tests only | Finding | For |
|---|---|---|---|
| caught | caught | none | |
| caught | survives | `guarded_by_impl_only`: is this part of the rule? | human |
| survives | survives | `survivor_rule`: test the rule through its seam | agent |

Survivors in code no rule covers become `survivor_unowned` ("Is this behaviour a
promise?"), and a changed function no test reaches is `unreached`. Both are for a human.
A `survivor_rule` the agent disputed becomes `mutant_disputed`, for a human to confirm or
reject; a rejected one goes back to the agent.

### The agent loop

1. The agent runs `mutineer run --format agent`.
2. For each `survivor_rule` finding, it writes a test through the named seam and marks it
   with the finding's rule (an unmarked test can't clear it). Only if no caller can observe
   the change does it run `mutineer dispute ID "<why>"` instead; a human then runs
   `mutineer resolve ID --confirm`, or `--reject --reason TEXT` to send it back.
3. It runs `next_command` from the report, `mutineer recheck --format agent`, which reruns
   only the undisputed survivors with the rule tests and checks for tampering again.
4. The agent is done when no undisputed `survivor_rule` is left; `next_command` is then
   null, as it is when a human must act. After `max_rounds` rechecks with survivors left,
   `recheck` exits 3: a human takes over.

`recheck` exits 4 if there's no previous run, or if the merge-base moved since then; run
`mutineer run` again in that case.

### The Stop hook

With `init --agent`, the agent's harness runs `mutineer hook stop` each time the agent
tries to finish a turn. The hook runs `mutineer run` and:

- **sends the agent back** when the agent report has a `next_command`: an undisputed
  `survivor_rule`, or a test suite that fails on its own. The reason the agent sees is the
  `--format agent` report.
- **lets the agent stop** otherwise, with a one-line message for you: a pass, findings for
  a human only (including a tamper block), the `recheck` round cap, or Mutineer failing
  to run. It never sends the agent back twice in a row (`stop_hook_active`), and when
  nothing changed since the last verdict it lets the agent stop without running, in well
  under a second.

It always exits 0 and prints one JSON object, so it can't trap the agent. The hook's
`timeout` is 600 s; raise it in the agent's config if a full `mutineer run` on your
project takes longer.

Codex loads project hooks only when the project's `.codex/` layer is trusted, and asks
you to review a new hook once (`/hooks`).

### Exit codes

| Code | Meaning |
|---|---|
| 0 | pass or warn |
| 1 | warn, with `--strict` |
| 2 | block |
| 3 | `recheck` round cap reached; needs a human |
| 4 | Mutineer could not run (bad config, unknown base, deleted rule, usage error) |

## What Mutineer writes

`run` and `recheck` write only to `mutants/` (mutmut's working directory) and
`.mutineer/last-run.json` (the state `recheck` and reuse read), unless you pass
`--output`. `fresh` writes nothing.
`rules propose`, `reject` and `dismiss`, `dispute` and `resolve` write only in
`.mutineer/`, and `hook stop` writes what `run` does. Only the commands you run yourself
write elsewhere: `init` (`.gitignore`, and with `--agent` the skill and the hook config in
`.claude/`, `.agents/skills/` or `.codex/`) and `rules accept` (the rules file and rule
markers in tests).

**`mutants/` is deleted at the start of every `run` that isn't reused, and of every
`recheck`.** mutmut's cache gives
wrong answers across branches and between Mutineer's two passes, so Mutineer always
starts clean. If you keep your own mutmut results in `mutants/`, they will be lost; run
mutmut and Mutineer in separate checkouts if you need both.

Your mutmut config (`[tool.mutmut]`, or `[mutmut]` in `setup.cfg`) is never edited: Mutineer sets mutmut's scope in memory. The
rule pass adds `-p mutineer --mutineer-rule-only` to pytest, and leaves any `-m` of yours
(in `addopts` or `pytest_add_cli_args`) alone.

## Configuration

```toml
[tool.mutineer]
base = "main"                       # default: main, else master
rules_file = ".mutineer/rules.toml"
test_paths = ["tests"]              # where tamper detection looks for tests
max_rounds = 3                      # recheck round cap
```

## License

Apache-2.0
