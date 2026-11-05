# Mutineer — PRD

> Snapshot of the living PRD, exported 2026-11-05. Where it disagrees with `docs/build-spec.md`, the build spec wins.


Nov 5, 2026 · @Fabian

## Summary

Mutineer is an open-source Python CLI that checks whether agent-written work is really done before it merges. It builds on mutmut, Python's established mutation tester, and adds what agent work needs: runs scoped to the diff, a line between promised behaviour and implementation detail, and detection of tests weakened to get green.

The one question it answers: **if the agent had got a promised behaviour wrong, would a test have noticed?**

Promises live in a committed rules file. Tests that guard them are protected; every other test stays free to change, so mutation never pushes agents to freeze implementation. It judges artifacts, not models or harnesses, so it works with any coding agent. We build it with agents and gate our own merges with it from the first week.

## Problem and why now

Agents now write much of the code, and they also write and run the tests that decide whether it is done. The thing being judged controls the signal that judges it.

- **Verification is the bottleneck.** In Sonar's 2026 survey of 1,100+ developers, AI accounts for 42% of committed code. 96% don't fully trust it, and only 48% always verify it before committing ([Sonar](https://www.sonarsource.com/company/press-releases/sonar-data-reveals-critical-verification-gap-in-ai-coding/)). In GitLab's 2026 survey, 85% say AI moved the bottleneck from writing code to validating it ([DevOps.com](https://devops.com/?p=188681)).
- **Coverage lies.** MUTGEN reports an LLM-written suite with 100% line and branch coverage and a 4% mutation score ([arXiv 2506.02954](https://arxiv.org/pdf/2506.02954v3)).
- **Agents game the check.** Given tests that contradict the spec, agents edit tests, overload equality, keep state between calls and special-case test inputs. More capable models cheated more, not less ([Dev Genius](https://blog.devgenius.io/we-gave-ai-permission-to-run-our-tests-then-it-started-testing-the-wrong-thing-00dfeb4d1420)).
- **Simple mutants are too easy.** SWE-Mutation found rule-based mutants trivial and built realistic ones from common LLM and human errors ([review](https://www.themoonlight.io/de/review/swe-mutation-can-llms-generate-reliable-test-suites-in-software-engineering)).
- **Naive mutation pushes agents to pin implementation.** Practitioners report that agents told to kill mutants write tests that assert on internals, freezing implementation as if it were a requirement. One developer cut an agent's 40 tests to 12 good ones. Those who make it work split tests into protected business-rule tests and disposable implementation tests.
- **Specs aren't enforced.** Spec-driven tools are mainstream, but a spec's authority stays conventional: nothing checks the finished code against it ([CodeMySpec](https://codemyspec.com/blog/spec-kit-vs-kiro)).

The quiet failure is worse than the loud one. An agent that writes its own tests doesn't need to cheat; it tests what it built, not what was asked.

## Landscape and positioning

Mutineer fills one gap: an independent referee between "the agent says it's done" and merge, judged by execution rather than opinion. Everything else in the table is served already, so we build on it, sit beside it, or leave it alone.

| Area | Already served by | Mutineer's stance |
| --- | --- | --- |
| AI code review | Greptile, CodeRabbit, Copilot review | Complement: they give a model's opinion of the code; Mutineer runs the tests against broken code and reports evidence |
| Mutation testing | mutmut, cosmic-ray, pytest-gremlins | Built on mutmut; adds diff scope, a rules ledger, tamper checks and triage |
| Parallel agents and worktrees | Conductor, Codex, Cursor, Meta Muse Code | Out of scope; works on any branch they produce |
| In-session context management | Native compaction in the Anthropic and OpenAI APIs | Out of scope; never touches the model's context |
| Planning and specs | Spec Kit, Kiro, OpenSpec, BMAD, Matt Pocock's skills | Their PRDs seed our rules file; we never replace them |
| Mutation-guided LLM testing | Research: MUTGEN, GEM, SWE-Mutation | Turns the useful parts into an open tool |

An agent fix loop is not our edge: CodeRabbit already lets agents fix its review comments unattended. Our edge is evidence from execution, plus the line between promises and details that no reviewer draws.

Why a lab or reviewer won't simply own this: a vendor grading its own agent's work has the agent's conflict of interest. A model-neutral, local, open-source judge with a community pattern library is a position they are badly placed to take.

## Goals, non-goals and principles

**Goals**

1. Give an honest, per-diff measure of whether tests would catch a wrong implementation of a promised behaviour.
2. Protect the tests that guard promises, and leave every other test free to change.
3. Detect test tampering and reward hacking automatically.
4. Fit any agent workflow with one command and machine-readable output.
5. Grow a contributor community around a library of agent-error patterns.

**Non-goals**

- Languages other than Python.
- Writing our own mutation engine.
- Orchestrating agents or managing worktrees.
- Managing an agent's context window.
- Writing specs or plans.
- Style linting, security scanning, or opinion-based code review.
- Replacing human review. Mutineer narrows what a reviewer has to look at.
- A hosted service before v1.

**Design principles**

- **Promises over details.** Only behaviour named in the rules file is protected. A survivor in unclaimed code becomes a question for a human, never a test request to an agent.
- **Stand on proven tools.** mutmut does the mutating. What belongs in mutmut goes upstream to mutmut.
- **Judge artifacts, not models.** Inputs are git, tests and the rules file. Nothing depends on a harness or on how a model behaves this month.
- **Deterministic core, optional LLM.** Every check that can run without a model does. LLM features are opt-in and labelled.
- **Evidence over scores.** Every finding points at a line, a mutant, a rule or a test.
- **Diff-scoped and fast.** Target: under 2 minutes for a typical PR.
- **Talk to agents and humans.** The same findings, rendered twice.

## Users and use cases

The primary user is a Python developer running coding agents through plan, orchestrate, implement and review. The agents themselves are the second user: they read Mutineer's JSON and act on it.

| User | What they need from Mutineer |
| --- | --- |
| Developer running agents | A gate between "agent says done" and merge |
| Implementing agent | Concrete gaps in promised behaviour it can close without a human |
| Reviewer of agent PRs | Where to look, and which questions only a human can answer |
| OSS maintainer | Whether an incoming agent PR's tests mean anything |
| Vibe coder | Plain pass or fail, gaps in plain language |

**Core use cases**

1. **Write down the promises.** After planning (for example with grill-me), the rules go into `.mutineer/rules.toml`, each with an id and the seam it should be tested through.
2. **Merge gate.** An agent finishes a goal; `mutineer run --base main` produces a verdict before merge.
3. **Self-check loop.** The implementing agent runs Mutineer, gets the survivors in rule-covered code, writes tests through the named seam, and runs `mutineer recheck`.
4. **Parallel branches.** Each agent's branch gets its own verdict, so the reviewer knows which branches to trust first.
5. **CI check.** A GitHub Action posts the verdict on the PR and fails on rule-test tampering.
6. **Maintainer triage.** Run on an outside agent PR to see if its tests can fail.
7. **Dogfooding.** Mutineer gates every Mutineer PR (see Dogfooding plan).

## The checks

Three deterministic checks ship in v0.1 and work together: the rules ledger says what is promised, mutation measures whether tests guard it, and tamper detection protects those tests. Agent-error patterns follow in v0.2; the audit is a separate opt-in command.

| Check | Question it answers | Needs an LLM | Ships |
| --- | --- | --- | --- |
| Rules ledger | Which behaviours are promises, and which tests guard each one? | No (LLM drafting optional, v0.2) | v0.1 |
| Diff-scoped mutation and triage | Would a test notice if a promise in this diff were broken? | No | v0.1 |
| Tiered tamper detection | Did the agent weaken a test that guards a promise? | No | v0.1 |
| Agent-error mutants | Would the tests notice the mistakes agents really make? | Optional | v0.2 |
| `mutineer audit` | What did the agent decide that nobody asked for? | Yes | v0.3, opt-in |

### 1. Rules ledger

A rule is any behaviour someone has promised: business ("orders of $50 or more ship free") or system ("retries 3 times on a 503"). Rules live in a committed, human-reviewed file with stable ids.

```toml
# .mutineer/rules.toml
[[rule]]
id = "SHIP-003"
statement = "Orders of $50 or more ship free"
seam = "billing.shipping.quote"   # the public entry point to test through
```

```python
@pytest.mark.rule("SHIP-003")
def test_free_shipping_at_threshold():
    assert quote(cart(total=50)).shipping == 0
```

- A test linked to one or more rule ids is a **rule test**. Every other test is an **implementation test**, and most tests stay that way.
- The link is pytest's own `rule` marker, at any level: one test, a class, or a whole file with `pytestmark = pytest.mark.rule("SHIP-003")`. Recommended layout: rule tests live in `tests/rules/`, one file per area, marked once at the top.
- Links are always explicit. Mutineer never decides on its own that a test is protected. From v0.2, `mutineer rules suggest` proposes links (tests that call a rule's seam and kill its mutants) for a human to accept.
- A small pytest plugin registers the marker and records which tests guard which rules.
- Changing a rule's statement or removing a rule means editing `rules.toml`, which a human reviews.
- The seam tells agents where a rule should be tested, so tests go through public behaviour, not internals.

### 2. Diff-scoped mutation and triage

- **Engine:** mutmut 3.x does all mutating and running. Mutineer never mutates code itself.
- **Scope:** changed files become mutmut's `only_mutate`, set in memory so the user's config is never edited; only mutants in changed functions run (mutmut accepts mutant-name globs). mutmut maps tests to functions, so coverage is judged per function.
- **Two passes:** an *all pass* runs every changed function's mutants against all tests; a *rule pass* reruns the mutants of rule-covered functions with only rule tests (`pytest -m rule`).
- **Unmeasured changes:** mutmut never mutates decorated functions (except `@staticmethod` and `@classmethod`) or module-level code. Changed lines there are reported as unmeasured and left out of every score, so they never look tested.
- **Results:** read only for the mutants requested in this run (mutmut keeps stale results); `mutmut show` gives each mutant's diff.
- **Triage** decides who acts, in functions a rule test covers:

| All tests | Rule tests only | What Mutineer says | Who acts |
| --- | --- | --- | --- |
| Survives | Survives | "Nothing catches this. Test SHIP-003 through `billing.shipping.quote`." | Agent |
| Caught | Survives | "Only an implementation test catches this. Is it part of SHIP-003?" | Human |
| Caught | Caught | Nothing |  |

Outside rule-covered functions, survivors become "Is this behaviour a promise?" for a human, and functions no test reaches are flagged as possibly dead code. The middle row is a spike finding: sending it to the agent would make it pin behaviour that isn't part of the rule.

- **Pinning flag (info):** a new test that kills a mutant by asserting on private names or mock call arguments is marked "pins implementation".

### 3. Tiered tamper detection

Compares the tests at base and head, and judges by tier.

- **Rule tests (BLOCK):** deleted; assertions removed or weakened (`==` to `in`, `assertEqual` to `assertTrue`, broadened `pytest.raises`); new `skip` or `xfail`; expected values changed; rule marker removed. Allowed only when `rules.toml` changes in the same PR.
- **Implementation tests (info):** deletion or rewrite is reported, never blocked. Agents can refactor freely.
- **Either tier (warn):** changes to `conftest.py`, pytest config or the runner; `__eq__` overrides added in source; source branches keyed on literals that also appear in tests (special-casing).

### 4. Agent-error mutants (v0.2)

- Each pattern is a named mutation: description, matcher, transform, and the real case it came from.
- Seed ideas: off-by-one in slicing or pagination; `except Exception: pass`; changed defaults (naive vs aware datetime, `None` vs `[]`); empty-input guard removed; dropped `sorted()`; milliseconds vs seconds.
- **Delivery:** mutmut's operators are a fixed list with no plugin hook today. We propose an operator hook upstream. If it is declined, patterns run as patches on a temp copy against the tests mutmut's stats say cover that function.
- Optional LLM mode proposes semantic mutants from the diff and rules; only ones that apply and import are kept.

### 5. `mutineer audit` (v0.3, opt-in)

- Its own command; never part of `mutineer run` unless the user adds it.
- A fresh-context LLM sees only the rules, the diff and the tests.
- It reports decisions the agent made that no rule covers, each with a location and a question for the human. A finding without a cited hunk is dropped.

## The verdict and the agent loop

Every run ends in one verdict, PASS, WARN or BLOCK, plus findings that each point at evidence. The same findings render for a human (terminal, markdown, PR comment) and for an agent (compact JSON).

**Verdict rules (v0.1)**

- **BLOCK:** rule-test tampering without a matching `rules.toml` change, or a failing baseline suite.
- **WARN:** survivors in rule-covered code, or warn-level tamper findings.
- **PASS:** none of the above. Questions for a human (survivors in unclaimed code) are listed but never change the verdict.

Mutation is report-only in v0.1: no score threshold blocks a merge until the dogfood log gives us real numbers.

**Rule score** = mutants in rule-covered changed functions killed in the rule pass ÷ all such mutants. It is the headline number. The diff score (all pass, all changed functions) sits beside it. Unmeasured changes and inconclusive mutants (timeouts) are listed on their own and count in neither.

```json
{
  "id": "mut-0007",
  "check": "mutation",
  "severity": "warn",
  "rule": "SHIP-003",
  "location": {"file": "src/billing/shipping.py", "line": 42},
  "evidence": {
    "mutmut_name": "billing.shipping.x_quote__mutmut_7",
    "original": "if total >= FREE_SHIPPING_MIN:",
    "mutant": "if total > FREE_SHIPPING_MIN:",
    "outcome": "survived",
    "rule_tests_run": ["tests/test_shipping.py::test_free_shipping_well_over_threshold"]
  },
  "next_action": "Add a SHIP-003 test through billing.shipping.quote where total equals the threshold."
}
```

_(Diagram in the living doc: the agent loop, where WARN or BLOCK findings go back to the agent and `mutineer recheck` reruns them.)_

`mutineer run --format agent` hands the agent only survivors in rule-covered code, as next actions naming the rule and its seam. After it writes tests, `mutineer recheck` reruns only those mutants, since mutmut can run a single mutant by name. A `--max-rounds` cap stops endless loops, and tamper rules apply to the tests the agent adds.

## Architecture

Mutineer is a thin Python layer around mutmut: it decides what mutmut runs, reads what mutmut finds, and adds the rules, tamper and triage logic mutmut doesn't have.

**Pipeline**

1. **Collect:** the diff from the merge-base with `--base` to the working tree (uncommitted changes included, because mutmut mutates files on disk), plus `rules.toml` at base and head.
2. **Tamper:** compare test files at base (`git show`) and head (disk); rule tests are found statically. No mutmut needed.
3. **Map:** find the changed functions and their mutmut names; changed lines mutmut cannot mutate become unmeasured.
4. **Mutate, all pass:** run mutmut on globs of mutants in changed functions, with `only_mutate` set in memory by a small launcher. A failing baseline stops here with BLOCK.
5. **Mutate, rule pass:** rerun the mutants of rule-covered functions with only rule tests, using the rule map from the pytest plugin.
6. **Triage and verdict:** classify each mutant by which pass caught it, score, aggregate findings, render.

**Stack**

- Python 3.11+ (developed on 3.14, tested on 3.11–3.15), packaged with `uv`.
- mutmut 3.x, pinned to a tested version range.
- `libcst` for our own analysis of tests (assertion diffing, pinning flags).
- A pytest plugin for the `rule` marker.
- `typer` for the CLI, `pydantic` for the findings and verdict schema.
- Config in `pyproject.toml` under `[tool.mutineer]`.
- LLM access (v0.2+) through a thin provider interface; bring your own key, off by default, cached by diff hash.

**Package layout**

```text
mutineer/
  cli.py              # run, recheck, audit, rules
  config.py
  diff.py             # changed files, functions, hunks
  rules.py            # rules.toml load + validation
  pytest_plugin.py    # rule marker, rule-to-test map
  engine/mutmut.py    # the only module that talks to mutmut
  checks/             # mutation_triage, tamper, audit
  patterns/           # agent-error patterns (v0.2)
  findings.py         # pydantic schema
  verdict.py
  reporters/          # terminal, markdown, json, agent, github
  llm/                # provider interface + cache (v0.2+)
```

**Living with mutmut**

- Everything mutmut-specific sits in the engine package, and one launcher module is the only code touching mutmut internals (its in-memory config).
- CI runs our test suite against each mutmut release in the supported range.
- mutmut's result files are internal; we ask upstream for a stable JSON report and a public config-override API.
- mutmut doesn't run on native Windows (it points users to WSL); Mutineer inherits that.

The spike notes and the v0.1 build spec live in the repo's docs/ folder, next to the handoff log: `docs/spike-mutmut.md` and `docs/build-spec.md`. The build spec's package layout supersedes the sketch above.

## Plugins and community

The community flywheel is the agent-error pattern: a small, testable plugin that encodes one real mistake agents make. It works like an ESLint or Semgrep rule, and anyone who has watched an agent fail has one to contribute.

| Plugin type | Entry point group | Size of a contribution |
| --- | --- | --- |
| Agent-error pattern | `mutineer.patterns` | Small: one file plus fixtures |
| Rules importer (PRD, Spec Kit, GitHub issue) | `mutineer.rule_sources` | Small to medium |
| Reporter | `mutineer.reporters` | Small to medium |
| Check | `mutineer.checks` | Medium; core-team review |

**A pattern contribution includes**

- Name, one-line description, and tags (framework, library).
- Where it was seen: agent, model and date.
- A before-and-after code example.
- The pattern, a fixture proving it applies, and a test that kills it.

**How patterns arrive**

- **Field reports:** an issue template for "an agent made this mistake and Mutineer missed it". Each accepted report becomes a pattern.
- **Light review:** patterns merge with one maintainer approval; checks and the verdict schema need two.
- **Discovery:** `mutineer patterns list` shows installed patterns by tag; third-party packs install with pip.

**Upstream first.** Improvements that belong in mutmut (an operator hook, a stable JSON report) go to mutmut as issues and PRs. A healthy mutmut is our foundation, and contributing there builds the project's credibility.

## Roadmap

Three versions, each released only when its gate is met. There are no dates yet; scope comes first.

_(Diagram in the living doc: v0.1 an honest number, v0.2 agent mistakes, v0.3 fits everywhere, each gated.)_

v0.1 has no model in it on purpose: rules, mutmut runs, triage and tamper checks are all deterministic, so the first number people see is one they can trust. The mutmut operator hook is opened as an upstream issue before v0.1 ships, so that conversation runs in parallel. Python is the only language, now and after v0.3.

## Dogfooding plan

Mutineer is built by agents and judged by itself. Each check gates our own merges as soon as it exists.

1. **This PRD seeds the first rules file.** On day one we write `.mutineer/rules.toml` by hand from this doc, each rule with a seam.
2. **Tamper detection ships first.** It is pure diff analysis, so it guards our rule tests from week one, before the mutmut integration works.
3. **Mutation runs on itself next.** From the first working build, every PR to main gets a diff-scoped run.
4. **No BLOCK merges.** An agent PR with a BLOCK verdict doesn't merge. An override needs a written reason in the PR.
5. **Keep a dogfood log.** `docs/dogfood.md` records each verdict on our PRs: what it caught, false positives, runtime, and any test flagged as pinning implementation. It is our tuning data and our launch story.
6. **Every escape becomes a pattern.** When a bug gets past Mutineer in our own code, we file a field report and add the pattern.

## Success metrics

Trust comes first: a referee people override is worse than none. All targets are first guesses to grill.

| Metric | Target | How we measure it |
| --- | --- | --- |
| False positives on BLOCK tamper findings | Under 5% | Dogfood log and user reports |
| Agent tests written from survivors that pin implementation | Under 10% | Pinning flags plus sampled human review |
| Survivors in rule-covered code a human calls a real gap | At least 70% | Sampled review on our repo and 3 OSS repos |
| Runtime, PR of up to 300 changed lines | Under 2 min (median) | Timing in the dogfood log |
| Repos running Mutineer in CI | 3 outside projects by v0.3 | GitHub code search for the Action |
| External pattern contributions | 5 by the end of v0.2 | Merged PRs of patterns |
| Field reports filed | Rising month over month | Issue tracker label |

## Risks

The biggest risk is noise: false positives, slow runs or pointless test requests teach people to ignore the verdict.

| Risk | Mitigation |
| --- | --- |
| mutmut changes its internals | All mutmut code in one module; CI against each release; ask upstream for a stable JSON report |
| Decorated code is invisible to mutmut (routes, @property, @lru\_cache) | Report those changes as unmeasured, never as tested; propose opt-in mutation of decorated functions upstream |
| The launcher relies on mutmut's private config object | One module, a version check, CI per release; ask upstream for a public override API |
| mutmut declines the operator hook | Fallback: patterns run as patches against the tests that cover the function |
| Mutation runs too slowly | Diff scope, changed-function globs, mutmut's per-test selection and workers, a `--budget` cap |
| Equivalent mutants create noise | Only rule-covered survivors reach the agent; a "mark equivalent" command that persists |
| Writing rules feels like overhead | Seed them from the PRD you already write; unmarked code still gets a plain report; LLM drafting in v0.2 |
| Tamper rules flag honest refactors | Only rule tests block; changing `rules.toml` in the same PR is the reviewed escape hatch |
| Agents learn to satisfy Mutineer instead of the rules | Hold back a hidden set of agent-error mutants; rule changes always pass a human |
| An AI reviewer adds mutation testing | Stay open source, local and model-neutral; own the pattern library |
| No native Windows | Inherited from mutmut; WSL documented up front |

## Decisions

| Decision | Answer |
| --- | --- |
| Name | Mutineer. Free on PyPI as of Nov 5, 2026; GitHub org and trademark still to check |
| Mutation engine | Build on mutmut 3.x; no engine of our own |
| Languages | Python only |
| Agent sees survivors | Yes, but only survivors in rule-covered code |
| Rules file | Committed, human-reviewed, stable ids |
| Linking tests to rules | One way: pytest's `rule` marker at test, class or file level; rule tests kept in `tests/rules/`. `mutineer rules suggest` in v0.2; a `tests` list in `rules.toml` only if users ask |
| One test, several rules | Yes: `rule("SHIP-003", "SHIP-004")` |
| Rule ids | An area prefix plus a number, assigned when the rule is written and never reused |
| Protecting rule tests | Detect only in v0.1; an optional agent hook that blocks edits to rule tests arrives with the v0.3 wrappers |
| Unowned survivors | Never in the headline; listed as questions for a human |
| mutmut version range | One minor series at a time, widened as CI passes |
| v0.1 threshold | Report-only, no mutation threshold; block only on rule-test tampering |
| Audit | Its own opt-in command, `mutineer audit` |
| License | Apache-2.0 |
| Hosted offering | Not before v1 |
| Python version | Minimum 3.11, develop on 3.14, test 3.11–3.15. Support every CPython version python.org still supports; drop one in the first release after its end of life |

## Sources

- [Sonar 2026 State of Code survey](https://www.sonarsource.com/company/press-releases/sonar-data-reveals-critical-verification-gap-in-ai-coding/)
- [DevOps.com on Sonar and GitLab 2026 surveys](https://devops.com/?p=188681)
- [MUTGEN, arXiv 2506.02954](https://arxiv.org/pdf/2506.02954v3)
- [SWE-Mutation (ACL 2026 Findings)](https://preview.aclanthology.org/ingest-acl/2026.findings-acl.1976/)
- [Agents editing tests (Dev Genius)](https://blog.devgenius.io/we-gave-ai-permission-to-run-our-tests-then-it-started-testing-the-wrong-thing-00dfeb4d1420)
- [Reward hacking glossary (Prime Intellect)](https://primeintellect.ai/rl-glossary/reward-hacking)
- [Spec Kit vs Kiro (CodeMySpec)](https://codemyspec.com/blog/spec-kit-vs-kiro)
- [mutmut on PyPI](https://pypi.org/project/mutmut/) (version 3.8.1 source inspected Nov 5, 2026)
- [Python mutation tools compared (pytest-gremlins)](https://pytest-gremlins.readthedocs.io/en/latest/guide/comparison/)
- [Greptile (Capterra)](https://www.capterra.com/p/10050225/Greptile/)
- [CodeRabbit product overview (Issoh)](https://www.issoh.co.jp/md/tech/5812)
- [Meta Muse Code launch (Enterprise DNA)](https://enterprisedna.co/resources/news/meta-muse-code-ai-coding-agent-launch-august-2026)
- [Conductor (AI Engineer)](https://www.ai.engineer/orgs/conductor)
