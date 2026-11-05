# Handoff log

Shared, append-only. Add an entry for any decision that affects another work
package, and for any request to change a shared contract. Newest at the bottom.

Format:

```text
## YYYY-MM-DD · WPn · short title
What changed or what you need, and why. Which packages it affects.
```

## 2026-11-05 · spec · starting point
The build spec, the spike notes and the PRD snapshot were written before any code.
The spike's findings (two-pass triage, unmeasured changes, launcher, function-level
coverage) are already reflected in `docs/build-spec.md` section 3.

## 2026-11-05 · spec · independent review applied
A reviewer that hadn't seen the spec being written checked it against the fixture.
Its fixes are in the current spec:
- baseline-failure messages depend on cache state;
- results must be read after each pass, because the rule pass overwrites them;
- no empty `only_mutate`, and Mutineer runs its own baseline when nothing is mutable;
- `-m` filters are combined, not appended;
- `Engine.rerun` and the merge rules for `recheck`;
- `collect_rule_map` is owned by WP2;
- the CLI is split into one module per owner;
- `errors.py`;
- the line-mapping formula;
- exit codes 3 and 4;
- the full triage status table, with `no_tests` counted in the scores;
- gitdiff details: `-U0`, untracked files, zero-length hunks.

Deviation from the PRD: the PRD's `--max-rounds` is kept as a `recheck` option
(default 3, from config).

## 2026-11-05 · spec · Python version and repo layout
- Python minimum is 3.11, chosen on its own merits rather than mutmut's 3.10 floor:
  - 3.10 reached end of life on Oct 1, 2026;
  - 3.11 brings `tomllib` and `StrEnum`.
  Develop on 3.14; CI tests 3.11–3.15. Policy: support every CPython version
  python.org still supports.
- `.agents/` holds only `AGENTS.md` and `CLAUDE.md` (which contains only `@AGENTS.md`).
  Build documents stay in `docs/`, spike scripts in `spike/`.
