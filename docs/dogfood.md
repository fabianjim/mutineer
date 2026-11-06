# Dogfood log

Mutineer judges its own changes. Every merge to `main` records one entry here: the
verdict, what it caught, false positives, runtime, and any test flagged as pinning
implementation. This is tuning data: mutation is report-only in v0.1, and these numbers
decide whether a threshold ever blocks.

## How to record an entry

Run both, from the branch, against `main`:

```console
uv run mutineer run --no-mutation --format markdown
time uv run mutineer run --format markdown
```

Then copy this template below the last entry:

```markdown
## YYYY-MM-DD · <branch> · <one-line summary>

- Diff: <changed source lines> lines, <mutants> mutants (`<files>`)
- `--no-mutation`: <status>, <seconds> s. <findings above INFO, one line each>
- Full run: <status>, <seconds> s, rule score <k/t>, diff score <k/t>
- Findings: <count by severity and kind>
- Caught: <real problems Mutineer found>
- False positives: <findings that were wrong, and why>
- Pinning: <tests flagged pins_implementation, and whether they really pin>
- Notes: <anything else worth tuning>
```

Machine for the entries below unless stated: macOS, 10 cores, Python 3.14, mutmut 3.8.1.

## 2026-11-05 · wp1-tamper, wp2-rules, wp3-engine · tamper only

Before WP4 there was no full run. WP1's `--no-mutation` run on its own branch was `warn`:
three `special_case_suspected` false positives (file names handled by new code and
exercised by its tests) and an expected `test_config_changed` for a new `conftest.py`.
See `docs/impl/WP1.md` and the false positives listed for WP5 in `docs/handoff.md`.

## 2026-11-05 · wp4-verdict · first full run

- Diff: about 1,300 changed source lines (triage, verdict, state, four reporters, `run`,
  `recheck`, plus edits to the plugin, engine and launcher); 2,005 mutants in the all
  pass, 892 in the rule pass.
- `--no-mutation`: warn, 1.6 s.
  - WARN `test_config_changed` ×3: `pyproject.toml` (`also_copy` for this dogfood run) and
    the two new `conftest.py` files. Expected; none hides a failure.
  - INFO `impl_test_changed` ×3: the deleted stub tests and the removed `rule_pass_args`
    test. Correct.
  - INFO `rule_unguarded` ×2: ENGINE-001 and ENGINE-002 are proved only by e2e tests,
    which `[tool.mutmut]` leaves out of the selection, so the rule map doesn't see them.
- Full run: warn, **392.6 s**, rule score 485/892 (54%), diff score 1097/2005 (55%).
  Over the 2-minute target; see "Runtime" below.
- Findings: WARN `survivor_rule` 185, `test_config_changed` 3; INFO
  `guarded_by_impl_only` 20, `survivor_unowned` 18, `unmeasured_change` 12, `unreached` 6,
  `impl_test_changed` 3, `rule_unguarded` 2. One `recheck` round with no new tests: 53 s,
  same scores.
- Caught, before this entry's numbers:
  - **Mutineer couldn't mutation-test itself.** The launcher imported the real
    `mutineer` package before mutmut ran the tests in-process, so no mutant was ever hit:
    every function `unreached`, diff score 0/1931. Fixed in `engine/_launch.py`.
  - **The baseline failed under mutmut.** Two unit tests read `.mutineer/rules.toml` and
    `docs/schema/` from the project root, which mutmut doesn't copy into `mutants/`. Fixed
    with `also_copy` in `[tool.mutmut]`.
- The 185 `survivor_rule` findings, sorted by a script on their mutated text:
  - 58 replace an argument or value with `None` (`ensure_ascii=None`, `color = None`);
  - 57 change only a string literal (`"XX…XX"`, case changes), mostly in finding messages
    and instructions;
  - 70 are other changes: dropped keyword arguments (`encoding="utf-8"`, `nl=False`),
    flipped booleans, `indent=2` → `3`, and some logic, for example
    `base or config.base` → `base and config.base` in `run` (no test sets
    `[tool.mutineer] base`) and `_test_paths`'s base-config merge.
  - Why so many reach the agent: coverage is per function, and the VERDICT-001 rule tests
    go through `cli.main(["run", ...])`, so `run`, `static_checks`, `emit` and
    `_test_paths` are all rule-covered. Likewise the TRIAGE rule tests cover every message
    builder in `triage.py`. The findings are true (nothing catches those mutants), but
    most aren't part of any promise.
- False positives: none of the findings is wrong, but most `survivor_rule` ones are noise
  for an agent. Also the `next_action` names the rule's seam even when the mutant is in
  another module (TRIAGE-001's seam is `mutineer.triage.triage`, but `reporters/agent.py`
  is covered by a TRIAGE-001 test), which is the wrong place to test it.
- Pinning: none flagged.

### Runtime

| Run | Diff | Mutants (all + rule) | Time |
|---|---|---|---|
| `--no-mutation`, WP4 diff | ~1,300 lines | none | 1.6 s |
| full, WP4 diff | ~1,300 lines | 2,005 + 892 | 392.6 s |
| `recheck`, WP4 diff | 185 names | 185 | 53 s |
| full, a 207-line slice of WP4 (in a scratch clone: `verdict.py`, `reporters/agent.py`, `reporters/markdown.py` added on top of the rest) | 207 lines | 229 + 129 | 80.1 s |

- The spec's target is under 2 minutes for a PR of up to 300 changed lines. The 207-line
  slice meets it; WP4's own PR, at about four times that size, doesn't.
- About 60 s of every run is fixed: before testing any mutant, mutmut runs the whole
  selected suite three times (stats, clean run, forced fail), and `tests/unit` takes about
  20 s. The rule pass repeats this with the rule tests only (a few seconds). Mutants then
  run at about 8 per second on 10 cores.

### For WP5 and the human

- Runtime: a faster `tests/unit` (the CLI tests run `git` and `pytest --collect-only` in
  subprocesses) cuts the fixed cost; large PRs will stay slow.
- Noise: per-function coverage makes every function a rule test passes through
  rule-covered. Worth considering: only send survivors to the agent from functions the
  rule's seam names or calls directly, or point `next_action` at the mutant's own module.
