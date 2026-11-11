---
name: mutineer
description: Work with Mutineer, the check that decides whether agent-written Python work is really done (diff-scoped mutation testing, promised rules, tamper detection). Use when a repository has .mutineer/rules.toml or a [tool.mutineer] section, when a Mutineer Stop hook sends you back, when a report or the user mentions survivor_rule, mutineer run or mutineer recheck, or when asked to start a rules ledger or turn a human's answer into a rule proposal.
---

# Mutineer

Mutineer protects only the behaviour named in `.mutineer/rules.toml` (the promises). Your
job is to prove those promises with tests, not to chase every mutant.

## Hard rules

- Never edit `.mutineer/rules.toml`. Propose rules with `mutineer rules propose`.
- Never weaken, skip, delete or unmark a test marked `@pytest.mark.rule("ID")`. Mutineer
  blocks it.
- Never mark a test with a rule id that isn't accepted (in rules.toml). A pending proposal
  is not accepted.
- Never run `rules accept`, `rules dismiss` or `resolve`. They record a human's decision.
- Findings for a human (`survivor_unowned`, `guarded_by_impl_only`, `unreached`,
  `mutant_disputed`, `unmeasured_change`) are questions, never work. Don't write tests to
  kill them; pass them on.

## The loop

1. Run `mutineer run --format agent`. Follow its `instruction` field.
2. For each `survivor_rule` finding, write a test that calls the rule's seam (the public
   entry point named in the finding) and fails on the mutated line, and mark it with the
   finding's rule: `@pytest.mark.rule("ID")`. An unmarked test can't clear it.
3. Only if no caller of the seam can observe the change, dispute instead:
   `mutineer dispute ID "<why>"`, with the finding's id and one line of reason. Don't
   dispute what you could test.
4. Run `next_command` from the report, normally `mutineer recheck --format agent`. Repeat
   from step 2 with the new findings.
5. When `next_command` is null you're done, or a human must act. Stop. Don't run another
   command. From that last report, give the human the status, the scores and the findings
   for a human in a few lines.

If the report says the suite fails on its own, make the tests pass without weakening a
rule test, then run `next_command`. If `recheck` exits 3 (round cap) or 4 (no previous run,
or the merge-base moved), run `mutineer run --format agent` again or hand over.

## The Stop hook

The harness may run Mutineer when you try to stop. If it sends you back with the agent
JSON, treat it as the report from step 1 and follow it. When it lets you stop, report the
status it gives to the human.

## Starting a ledger

When asked to write the first rules, propose them; a human accepts. For each promise give
a statement, the seam and existing tests that prove it:

```console
mutineer rules propose --prefix SHIP --statement "Orders of $50 or more ship free" --seam shop.shipping.quote --test tests/test_shipping.py::test_free_over_50
```

For many rules at once, pass a JSON array (keys: `prefix` or `id`, `statement`, `seam`,
`tests`) on stdin or as a file; one bad entry means nothing is written:

```console
mutineer rules propose --batch - < proposals.json
mutineer rules propose --batch proposals.json
```

Check what is pending with `mutineer rules check`. Drop your own mistakes with
`mutineer rules reject ID`.

## Turning a human's answer into a proposal

A `survivor_unowned` or `guarded_by_impl_only` finding asks the human whether the
behaviour is a promise. Act only on their answer:

- "Yes, a new promise": write a test through the seam without a rule marker, then
  `mutineer rules propose --prefix SHIP --statement "..." --seam shop.shipping.quote --test tests/test_shipping.py::test_new`
  (or `--id SHIP-007` for a chosen id).
- "Yes, part of rule SHIP-003": write the test, again unmarked, then
  `mutineer rules propose --rule SHIP-003 --test tests/test_shipping.py::test_new`.
- "No": nothing for you. The human records it.
