# Spike material (reference only)

Evidence for `docs/spike-mutmut.md`. Don't import from here or edit it; WP0
copies `make_fixture.sh` into `tests/fixtures/`.

- `make_fixture.sh DIR`: builds the toy `shop` repo with branches `main`, `agent`,
  `agent-gap`, `agent-tamper` and `agent-tamper-approved`. The expected Mutineer
  result for each branch is in `docs/build-spec.md` (WP4 acceptance table).
- `mutmut_launcher.py`: runs mutmut with an in-memory config override, for example
  `python mutmut_launcher.py '{"only_mutate": ["src/shop/pricing.py"]}' run "shop.pricing.x_discount__mutmut_*"`.
- `rulemap_plugin.py`: the throwaway pytest plugin that dumped rule markers.
  Run with `--ignore=mutants`.
