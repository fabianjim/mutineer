# Mutineer: instructions for coding agents

Mutineer checks whether agent-written work is really done before it merges. It runs
mutation testing (via mutmut) scoped to a diff, separates promised behaviour (rules)
from implementation detail, and catches tests weakened to get green.

## Read before you write code

1. `docs/build-spec.md`: your work package, the files you own, the shared contracts
   and your acceptance tests. **This is the source of truth for the build.**
2. `docs/prd.md`: what Mutineer is for and why. A snapshot of the living PRD.
3. `docs/spike-mutmut.md`: verified facts about mutmut 3.8.1. Don't re-derive them.
4. `docs/handoff.md` and the `docs/impl/WP*.md` files from packages before yours.

This file and `CLAUDE.md` live in `.agents/`. The build documents above are in `docs/`,
and the spike scripts in `spike/`.

## Rules of the road

- Python 3.11 is the minimum (`requires-python = ">=3.11"`); develop on 3.14
  (`.python-version`); `uv` installs it. Nothing newer than 3.11 in `src/`.
- Edit only the files your work package owns. Need something elsewhere? Append a
  request to `docs/handoff.md` and work around it.
- Shared contracts in `src/mutineer/models.py` change only additively (new optional
  fields), and every change is logged in `docs/handoff.md`.
- Keep `docs/impl/WP<n>.md` current: plan, decisions, deviations, how you tested.
- Run `uv run ruff check`, `uv run ruff format --check`, `uv run mypy src` and
  `uv run pytest -m "not slow"` before you say you're done. Run the slow e2e tests
  if your package has any.
- Test promised behaviour through public entry points. Don't assert on private
  names or mock call arguments unless the test is about that internal detail.
- Tests that prove a rule in `.mutineer/rules.toml` carry
  `@pytest.mark.rule("<ID>")`. Never weaken, skip or delete one without changing that
  rule in `rules.toml`, and say why in your impl doc. Mutineer will block it otherwise.
- Never assert on mutmut mutant numbers (`__mutmut_3`). Assert on the mutated source text.
- Mutineer must never write outside `mutants/` and `.mutineer/` in a user's project.
- No LLM calls in v0.1.
- `spike/` is reference material. Don't import from it or edit it.
