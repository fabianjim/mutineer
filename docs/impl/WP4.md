# WP4 Triage, verdict, reporters, `run` and `recheck`

Status: done, not yet merged. Every check in `.agents/AGENTS.md` passes on 3.14, including
the slow e2e tests. The dogfood run misses the 2-minute target on WP4's own diff; the human
deferred that target until v0.1 is complete (`docs/dogfood.md`).

## What's here

| File | Notes |
|---|---|
| `src/mutineer/triage.py` | `triage(run, rule_map, rules) -> Triage` (findings, `rule_score`, `diff_score`, `survivor_names`) |
| `src/mutineer/verdict.py` | `decide(findings, *, base, head, rule_score, diff_score, duration_s) -> Verdict`; `exit_code(verdict, strict=)` |
| `src/mutineer/state.py` | `State`, `save`, `load` for `.mutineer/last-run.json` |
| `src/mutineer/reporters/{terminal,markdown,json,agent}.py` | one `render(verdict) -> str` each (`terminal` also takes `color`, `width`) |
| `src/mutineer/reporters/_common.py` | score text, `file:line`, evidence lines shared by terminal and markdown |
| `src/mutineer/cli/run.py` | full `run`; `static_checks`, `emit`, `make_engine` shared with `recheck` |
| `src/mutineer/cli/recheck.py` | `recheck`, `merge_rerun` |
| `src/mutineer/pytest_plugin.py` (WP2's) | `--mutineer-rule-only` |
| `src/mutineer/engine/mutmut_engine.py` (WP3's) | rule pass uses `RULE_PASS_ARGS`; `rule_pass_args` removed |
| `src/mutineer/engine/_launch.py` (WP3's) | `_forget_mutineer`, so Mutineer can mutation-test itself |
| `src/mutineer/errors.py` (WP0's) | additive: `CollectionError(RulesError)` with `output` |
| `src/mutineer/rules.py` (WP2's) | `collect_rule_map` raises `CollectionError` for a broken suite, and reads `setup.cfg [mutmut]` like mutmut |
| `docs/build-spec.md` | the decisions that overrode it, written back (handoff) |
| `README.md`, `pyproject.toml` | README; `readme = "README.md"`; `also_copy` for dogfooding |
| `docs/dogfood.md` | template and the first entries |
| `tests/unit/verdict/` | `conftest.py` (`build`, `ship_rules`), triage table, verdict, reporters, state, CLI with a stand-in engine |
| `tests/e2e/verdict/` | `conftest.py` (`mutineer`), the acceptance table, recheck, rule identity, ENGINE-001 for the CLI |
| `tests/unit/rules/test_rulemap_plugin.py`, `tests/e2e/test_engine_e2e.py`, `tests/unit/test_engine_mutmut_helpers.py` | rule-only option tests; the addopts e2e test; the removed `rule_pass_args` test |
| `tests/unit/test_cli.py`, `tests/unit/test_tamper_fixture.py` | updated for the real `run`/`recheck` (handoff) |
| `tests/unit/verdict/test_collection_failures.py`, `tests/unit/rules/test_rulemap_setup_cfg.py` | `CollectionError` versus setup errors; `setup.cfg` selection |
| `tests/e2e/verdict/test_mutineer_on_itself_e2e.py` | the engine on a copy of Mutineer's own source (launcher regression) |

## How it works

### `run`
1. `load_config`; the base ref is resolved once (`--base`, config, main/master) and kept
   for `recheck`.
2. `static_checks`: `load_diff`, the head rules (validated; a missing file is empty), the
   base rules from git (`compare`; an invalid base file is skipped, as in `rules check`),
   `tamper.check` (head plus base `test_paths`, WP1's guard), `collect_rule_map` and
   `rule_findings`. If pytest can't collect the suite (`CollectionError`), the result
   holds a `baseline_failed` BLOCK (`evidence.stage = "collection"`) instead of the rule
   findings, and is marked `suite_broken`.
3. Unless `--no-mutation` or the suite is broken: `MutmutEngine().run(diff, rule_map,
   root)`, then `triage`.
4. `decide`, `state.save`, render, exit (`exit_code`).

### Triage
Status groups: K (killed, type check), S (survived), N (no tests), I (everything else, or
no result where one is needed). A function is rule-covered when a test in its
`tests_by_function` has a rule id in the rule map; its rule ids are the union of those
tests' ids.

- Rule-covered, per mutant: K/K nothing; K then S/N `guarded_by_impl_only`; S/N then S/N
  `survivor_rule`; S/N then K, or I anywhere (including a missing rule result)
  `mutant_inconclusive`.
- Not rule-covered: S/N `survivor_unowned`, I `mutant_inconclusive`.
- `unreached` functions get one `unreached` finding and nothing else.
- `survivor_rule` is one finding per mutant, located at the mutated line, with
  `evidence`: `mutant`, `line`, `original`, `mutated`, both statuses, `function`,
  `rule_tests` and `rule_statements`. Its `next_action` is
  "Test SHIP-003 through `shop.shipping.quote` so that this change to line 8 fails." plus
  the spec's seam guidance. A rule without a seam says "through its public entry point".
- `guarded_by_impl_only`, `survivor_unowned` and `mutant_inconclusive` are one finding per
  function, with every mutant (line, text, status in each pass) in `evidence.mutants`, and
  a location spanning the mutated lines.
- `unmeasured_change` is one finding per file, with `evidence.reasons` and
  `evidence.locations`. `no_mutants` is handled like the other reasons.
- The text of a mutant comes from the rule-pass record when it has one, because the engine
  leaves text off killed results.
- Scores: `diff_score` over every all-pass result; `rule_score` over the rule-pass results
  of rule-covered functions, `None` if there are none. Group I counts in neither.
- A failing baseline gives one `baseline_failed` (BLOCK) plus `unmeasured_change`; no
  mutant findings, and both scores `None`, because no mutant result means anything then.

### Verdict
`decide` orders findings BLOCK, WARN, INFO, then tamper, rules, mutation (stable within),
numbers them per check, forces `baseline_failed` to BLOCK, and takes the status from the
most severe finding. INFO never changes it.

### Reporters
- `json`: `Verdict.model_dump_json(indent=2)`.
- `agent`: one line of JSON: `status`, both scores, `instruction`, and only agent-audience
  findings (`id`, `kind`, `rules`, `location`, `message`, `evidence`, `next_action`). The
  instruction is "write tests, then `recheck`" when there is agent work, "nothing for you
  to do" when not, "stop and report to a human" on a block, and "make the suite pass" when
  the only block is a failing baseline.
- `markdown`: a heading with the status, a score table, then "Blocking", "For the agent"
  and "Questions for a human" sections. Code spans pick a fence longer than any backtick
  run in the text.
- `terminal`: rich, BLOCK first, colour only on a TTY and never in `--output` files.
  rich pads wrapped lines; the padding is stripped.

### `recheck`
1. No state: exit 4, "run `mutineer run` first". Merge-base of the stored `base_ref`
   differs from the stored sha: exit 4, "base moved".
2. No `survivor_names` (or a `--no-mutation` state): re-render the stored verdict, exit by
   its status. State is not touched.
3. `rounds >= --max-rounds` (default `config.max_rounds`): `NeedsHumanError`, exit 3.
4. `static_checks` on the current tree, `engine.rerun(survivor_names)`, `merge_rerun`
   (replace those names' rule-pass results; everything else stays), `triage`, `decide`,
   save with `rounds + 1` and the new survivor names.
   - If the suite can't be collected, there is no rerun. If it can't be collected or the
     rerun hits a failing baseline, the verdict blocks, but the stored engine run and
     survivor names are kept, so the next `recheck` retries the same mutants.

## Decisions

- **The rule pass selects rule tests with `--mutineer-rule-only`**, per the human's
  decision (handoff). The plugin deselects tests with no rule id after recording the map,
  and reports them through `pytest_deselected`, which is what mutmut subtracts. An empty
  `@pytest.mark.rule()` no longer selects a test; it never made a function rule-covered
  anyway, because it isn't in the rule map.
- **`run --no-mutation` renders like a full run.** WP1's plain JSON was a placeholder.
- **`survivor_names` live in the state**, so `recheck` doesn't need to re-derive them.
- **`base_ref` is stored**, so `recheck` compares against the same base `run` used.
- **Finding ids are numbered per check**, matching `"<check>-<nnnn>"` in the spec.
- **A suite pytest can't collect blocks** (decided by the human). `collect_rule_map`
  raises `CollectionError` on pytest exit 1 or 2, or exit 4 with
  `ImportError while loading conftest`; a bad option or a missing plugin also exits 4
  and stays an exit-4 `RulesError`. Matching that one pytest message is the only way to
  tell a broken `conftest.py` from a setup error.
- **`setup.cfg [mutmut]`** is read by `collect_rule_map` when `pyproject.toml` has no
  `[tool.mutmut]`, as mutmut does, so the rule map matches the engine's test selection.
- **Message text is fixed English.** Tests assert on the parts that carry meaning (seam,
  line, rule, guidance), not whole sentences, except for `next_action` of
  `survivor_unowned` and the seam guidance, which the spec words.

## Deviations from the spec

- Section 3 item 3 and the WP3 acceptance line about `-m "(not slow) and rule"` are
  superseded by the human's decision (`--mutineer-rule-only`).
- `reporters/_common.py` is an extra module in WP4's package.
- `decide` takes keyword arguments for the scores and the duration; the spec names only
  the function.
- `unreached` functions don't also get `survivor_unowned` (the spec doesn't say; one
  finding per cause).
- With a failing baseline, scores are `None` and per-mutant findings are dropped.
- A collection error is a BLOCK, not exit 4 (the exit-code table said 4; the spike and
  VERDICT-002 said block). The spec is updated.
- Edited files owned by others: `pytest_plugin.py` and `mutmut_engine.py` (allowed by the
  human's decision), `_launch.py` (dogfood bug), `errors.py` (`CollectionError`),
  `rules.py` (`CollectionError`, `setup.cfg`), `pyproject.toml` (`readme`, `also_copy`),
  `docs/build-spec.md`, WP0's and WP1's CLI tests. Each is in `docs/handoff.md`.
- **Dogfood time:** over 2 minutes on WP4's diff (about 1,300 lines, against the spec's
  ≤300). Deferred by the human. Numbers in `docs/dogfood.md`.

## Known limits

- Most `survivor_rule` findings on Mutineer's own diff are argument, string and
  presentation mutants (`ensure_ascii=None`, `"XXNothing catches this…"`), because the
  VERDICT-001 and TRIAGE rule tests pass through the CLI and the message builders, and
  coverage is per function. They are real survivors but not promises. See
  `docs/dogfood.md`.
- `recheck` reruns only the stored names. If the agent edits the function, mutmut may
  renumber its mutants: changed names come back `not_checked` (WP3), which triage reports
  as `mutant_inconclusive`. New code added during the loop is only measured by the next
  `run`.
- Rule coverage is per function, so a function any rule test passes through is
  rule-covered; on layered code this sends unrelated survivors to the agent. Handed to
  WP5 (handoff).
- A test that tampers with Mutineer's own pytest plugin can change which tests the rule
  pass runs while that mutant is tested. Inherent to dogfooding a pytest plugin.

## How I tested

- `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src`: clean.
- `uv run python scripts/export_schema.py --check`: clean (models unchanged).
- `uv run pytest -m "not slow"`: 416 passed.
- `uv run pytest -m slow`: 32 passed (17 engine/rule-map, 15 WP4).
- Acceptance (`tests/e2e/verdict/test_verdict_e2e.py`):

  | Acceptance item | Test |
  |---|---|
  | `agent`: pass, 0, `survivor_unowned` ×2, no agent findings | `test_agent_passes_with_questions_for_a_human` |
  | `agent-gap`: warn, 0 / 1 strict, `survivor_rule` lines 8 and 10 with the seam, `guarded_by_impl_only` for the express mutants, `unmeasured_change` line 3, 3/7 and 5/7 | `test_agent_gap_warns_and_sends_two_survivors_to_the_agent` |
  | `agent-tamper`: block, 2 | `test_agent_tamper_blocks` (VERDICT-001) |
  | `agent-tamper-approved`: warn, 0, WARN `rule_test_weakened`, `rules_file_changed` | `test_agent_tamper_approved_warns` |
  | `main` plus a failing test: block, 2 | `test_a_failing_test_on_main_blocks` (VERDICT-002) |
  | agent output for `agent-gap` has no human findings | `test_agent_output_for_agent_gap_has_no_human_findings` (TRIAGE-001) |
  | changed `@property`: `unmeasured_change`, neither score changes | `test_a_changed_property_is_unmeasured_and_changes_no_score` (TRIAGE-002) |
  | `recheck` after `quote(50) == 0`: line 8 gone, 4/7, fourth recheck exits 3 | `test_recheck_after_a_new_rule_test` |
  | static and runtime rule identity agree on every branch | `test_static_and_runtime_rule_identity_agree` |
  | `run`/`recheck` write only under `mutants/` and `.mutineer/` | `test_run_and_recheck_write_only_under_mutants_and_mutineer` (ENGINE-001) |

- Unit (`tests/unit/verdict/`): every row of the triage table, inconclusive scoring,
  grouping, unreached, every unmeasured reason (TRIAGE-001, TRIAGE-002), status and
  VERDICT-002/003, ids and order, exit codes, each reporter, the state file, and the
  `run`/`recheck` flow with a stand-in engine (round cap, config cap, base moved, no
  state, no survivors, `--no-mutation`, `--output`, `--strict`, a deleted rule).
- Rule pass: `--mutineer-rule-only` with pytester (selection, a project `-m` in addopts,
  `pytest_deselected`, the rule map); e2e with `-m "not slow"` in `[tool.mutmut]` and in
  pytest `addopts`.
- By hand: every fixture branch in every format, and the recheck loop.
- Dogfood: `docs/dogfood.md`.
