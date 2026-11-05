# Spike: building on mutmut 3.8.1

Date: 2026-11-05. Environment: Python 3.13, pytest 9.2.0, mutmut 3.8.1, Linux.
Reproduce: `spike/make_fixture.sh /tmp/shop` builds the toy repo used below
(branches `main` and `agent`). `spike/mutmut_launcher.py` and
`spike/rulemap_plugin.py` are the two helper scripts the spike ran.

**Verdict: mutmut can carry Mutineer v0.1 without a fork.** Every assumption the
PRD makes held, with the corrections listed under "What changes in the design".

## What was confirmed

| # | Question | Result |
|---|---|---|
| 1 | Can a run be limited to the diff's files? | Yes. `only_mutate = ["src/shop/pricing.py"]` → "1 files mutated, 2 ignored". |
| 2 | Can a run be limited to changed functions? | Yes. `mutmut run "shop.pricing.x_discount__mutmut_*"` runs only those mutants (fnmatch globs). |
| 3 | Can we set `only_mutate` without editing the user's `pyproject.toml`? | Yes, with a launcher that replaces mutmut's in-memory config before importing its CLI (`spike/mutmut_launcher.py`). Git status stays clean. |
| 4 | Where are results? | `mutants/<source path>.meta` (JSON). `exit_code_by_key[mutant] = 0` survived, `1` killed, `None` not run. |
| 5 | Which tests cover which code? | `mutants/mutmut-stats.json` → `tests_by_mangled_function_name`, e.g. `"shop.shipping.x_quote": ["tests/rules/test_shipping_rules.py::test_..."]`. Function-level, not line-level. |
| 6 | Do rule-marker test ids match mutmut's? | Yes, exactly, when collection runs with `--ignore=mutants` (see trap 2). |
| 7 | Mutant diffs? | `mutmut show <name>` prints a unified diff. Hunk line numbers are **relative to the function**, not the file. |
| 8 | Failing baseline? | `mutmut run` exits 1. The message depends on cache state: `failed to collect stats` with a fresh `mutants/`, `Failed to run clean test` when stats are cached, and `Failed to collect list of tests` on collection errors. All map to BLOCK. If none of the requested functions has a test, it exits 1 with `Stopping early, because we could not find any test case` and writes no results. |
| 9 | Speed? | ~50 mutants/second on the toy repo; a full run of 3 files in under 1 s. Real suites will be slower; see PRD risks. |
| 10 | Name formats | Function: `shop.pricing.x_discount__mutmut_3`. Method: `shop.cart.xǁCartǁadd__mutmut_1` (the separator is U+01C1 `ǁ`, not a pipe). Nested functions are mutated inside their outer function's mutants. |

## Two-pass result on fixture branch `agent-gap`

`x_quote` is covered by both SHIP-003 rule tests and the implementation test
`test_express_adds_fee`. The all pass runs every test; the rule pass runs with `-m rule`.

| Mutated line (head line) | All pass | Rule pass | Triage |
|---|---|---|---|
| `def quote(total, express=True):` (6) | killed | killed | none |
| `if total > FREE_SHIPPING_MIN:` (8) | survived | survived | `survivor_rule` → agent |
| `return 1` (9) | killed | killed | none |
| `rate = None` (10) | killed | killed | none |
| `rate = _rate(None)` (10) | survived | survived | `survivor_rule` → agent (an equivalent mutant: `_rate` ignores its argument) |
| `rate = EXPRESS_FEE` (12) | killed | survived | `guarded_by_impl_only` → human |
| `rate -= EXPRESS_FEE` (12) | killed | survived | `guarded_by_impl_only` → human |

All pass: 5 of 7 killed. Rule pass: 3 of 7 killed. Every line number above was
checked with the line-mapping formula below. Exact mutant numbering is mutmut's and
may change between versions.

## Traps found

1. **Stale results.** `mutmut results` and the `.meta` files keep results from
   earlier runs, including functions outside the current scope. Mutineer must read
   only the exact mutant names it asked mutmut to run in this invocation, never the
   whole results list.
2. **Collected twice.** mutmut copies tests into `mutants/tests/`. Running pytest
   collection from the repo root picks up the copies and returns ids prefixed
   `mutants/`. Always collect with `--ignore=mutants`.
3. **Decorated functions are never mutated.** mutmut skips any function with a
   decorator except a lone `@staticmethod` or `@classmethod`
   (`mutation/file_mutation.py`, comment explains why). That excludes `@property`,
   `@app.get(...)` / `@router.post(...)` route handlers, `@lru_cache`, and most
   framework code. Also not mutated: module-level code (constants), type
   annotations, enum creation hooks such as `_generate_next_value_`, and default
   values that are not a plain name, number or string (the function body is still
   mutated).
4. **mutmut fails on import without a source path.** Even `mutmut --version`
   raises `FileNotFoundError` when it can't guess `source_paths`. Mutineer must
   check config before calling it and give its own error.
5. **No native Windows.** mutmut exits on Windows and points to WSL. macOS works
   (it disables `setproctitle` there).
6. **Each run overwrites the last.** A second `mutmut run` with different test args
   overwrites the `.meta` exit codes and the stats file's test map. Read results
   right after each run.
7. **pytest keeps only the last `-m`.** Appending `-m rule` to a project's
   `-m "not slow"` silently drops the user's filter. Combine them instead:
   `-m "(not slow) and rule"`.
8. **An empty `only_mutate` mutates everything** (`configuration.py`: no patterns
   means every file matches). Never pass it empty.
9. **Explicit mutant names need a stats entry.** `mutmut run <exact name>` raises
   `KeyError` when the function has no recorded tests. Globs are safe.
10. **Private coupling.** The launcher touches one private name,
   `mutmut.configuration._config` (plus `_load_config`). The config is a
   dataclass, so `dataclasses.replace` works today.

## What changes in the design

- **Coverage is function-level.** "Code a rule test covers" means "a function
  whose covering tests include a rule test". Triage works per function, not per line.
- **Diff lines must be mapped to mutmut function names ourselves.** Parse the
  head version of each changed file with libcst, find the function (or method)
  containing each changed line, and build its mutmut name
  (`<module>.x_<func>` or `<module>.xǁ<Class>ǁ<method>`). Module path is relative
  to the `source_paths` entry.
- **Mutant diff → file line** = the function's `def` line in the head file + (the
  hunk's old-side start − 1) + (the number of context lines before the first `-`
  line). `mutmut show` prints context lines first, so the hunk header alone is off by
  that count. Needed so findings point at real lines.
- **New finding: "unmeasured change".** Changed lines that mutmut cannot mutate
  (decorated functions, module-level code) are reported as `unmeasured`, listed
  by file and line, and excluded from the score so they never look tested. In
  route-heavy code this list will be long; that is the honest answer.
- **Upstream asks for mutmut** (open as issues, in this order): a stable JSON
  report, a public config-override API, an operator hook (v0.2), opt-in mutation
  of decorated functions.
