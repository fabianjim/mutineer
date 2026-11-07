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

## Use

```console
mutineer run                     # judge the diff from the merge-base to the working tree
mutineer run --format agent      # compact JSON for the implementing agent
mutineer recheck --format agent  # after the agent adds tests: rerun only its survivors
mutineer rules check             # validate rules.toml and list each rule's tests
```

`run` compares the working tree (uncommitted and untracked files included) with
`git merge-base <base> HEAD`. The base is `--base`, else `[tool.mutineer] base`, else
`main`, else `master`.

| Option | |
|---|---|
| `--format terminal\|markdown\|json\|agent` | terminal (default), a PR comment, the full verdict, or agent JSON |
| `--output FILE` | write the report to a file instead of stdout |
| `--no-mutation` | tamper and rules checks only; no mutmut run, no scores |
| `--strict` | exit 1 on warn |
| `recheck --max-rounds N` | round cap (default `[tool.mutineer] max_rounds`, 3) |

### The verdict

Every run ends in one status:

- **block**: a rule test was tampered with and the rules file doesn't change that rule,
  or the test suite fails on its own (including a test file or `conftest.py` pytest can't
  collect).
- **warn**: a mutant in code a rule test covers survives every test, or a warn-level
  tamper or rules finding.
- **pass**: none of the above. Questions for a human are listed but never change the
  status.

Two scores sit beside it:

- **rule score**: mutants in changed functions that a rule test covers, killed by rule
  tests alone. The headline number.
- **diff score**: mutants in all changed functions, killed by any test.

Timeouts and other inconclusive mutants count in neither score. Changed lines mutmut
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

### The agent loop

1. The agent runs `mutineer run --format agent`.
2. For each `survivor_rule` finding, it writes a test through the named seam, or says
   why the mutant can't change visible behaviour.
3. It runs `mutineer recheck --format agent`, which reruns only those mutants with the
   rule tests and checks for tampering again.
4. After `max_rounds` rechecks with survivors left, `recheck` exits 3: a human takes over.

`recheck` exits 4 if there's no previous run, or if the merge-base moved since then; run
`mutineer run` again in that case.

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
`.mutineer/last-run.json` (the state `recheck` reads), unless you pass `--output`.

**`mutants/` is deleted at the start of every `run` and `recheck`.** mutmut's cache gives
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
