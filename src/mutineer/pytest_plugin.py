"""pytest plugin, loaded through the ``pytest11`` entry point (name ``mutineer``).

- Registers the ``rule`` marker, so ``--strict-markers`` passes.
- ``--mutineer-rulemap=PATH`` writes the ``RuleMap`` (node id -> sorted rule ids) as
  JSON after collection.
- ``--mutineer-collected=PATH`` writes every collected node id as a sorted JSON list
  after collection.
- ``--mutineer-rule-only`` deselects every test that carries no rule id, and reports it
  through ``pytest_deselected`` as ``-m`` does. Mutineer's rule pass uses it instead of
  ``-m rule``, because pytest keeps only the last ``-m`` and a project's own ``-m`` (in
  ``addopts`` or mutmut's ``pytest_add_cli_args``) must keep working.

Without these options the plugin does nothing else.

Rule ids come from every ``rule`` mark that applies to a test: on the function, its
class and the module ``pytestmark``. Several ids per mark and stacked marks are merged.
The map and the collected ids are taken before any deselection (``-m``, ``-k``,
``--mutineer-rule-only``), so a user's filter never hides a rule test. A test is a rule
test for both options when it carries at least one rule id; an empty
``@pytest.mark.rule()`` doesn't count.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_RULEMAP_OPTION = "mutineer_rulemap"
_RULE_ONLY_OPTION = "mutineer_rule_only"
_COLLECTED_OPTION = "mutineer_collected"
_rule_map_key = pytest.StashKey[dict[str, list[str]]]()
_collected_key = pytest.StashKey[list[str]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("mutineer")
    group.addoption(
        "--mutineer-rulemap",
        dest=_RULEMAP_OPTION,
        metavar="PATH",
        default=None,
        help="Write the rule map (test node id -> rule ids) as JSON to PATH after collection.",
    )
    group.addoption(
        "--mutineer-rule-only",
        dest=_RULE_ONLY_OPTION,
        action="store_true",
        default=False,
        help="Deselect every test without a rule id (Mutineer's rule pass).",
    )
    group.addoption(
        "--mutineer-collected",
        dest=_COLLECTED_OPTION,
        metavar="PATH",
        default=None,
        help="Write every collected test node id as a JSON list to PATH after collection.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "rule(*ids): links a test to rules in .mutineer/rules.toml (Mutineer)",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    record = config.getoption(_RULEMAP_OPTION) is not None
    rule_only = bool(config.getoption(_RULE_ONLY_OPTION))
    if config.getoption(_COLLECTED_OPTION) is not None:
        config.stash[_collected_key] = sorted(item.nodeid for item in items)
    if not (record or rule_only):
        return
    rule_map: dict[str, list[str]] = {}
    for item in items:
        ids = _rule_ids(item)
        if ids:
            rule_map[item.nodeid] = ids
    # record first: the map lists every rule test, whatever is deselected
    if record:
        config.stash[_rule_map_key] = rule_map
    if rule_only:
        kept = [item for item in items if item.nodeid in rule_map]
        dropped = [item for item in items if item.nodeid not in rule_map]
        if dropped:
            config.hook.pytest_deselected(items=dropped)
            items[:] = kept


def _rule_ids(item: pytest.Item) -> list[str]:
    return sorted({str(arg) for mark in item.iter_markers("rule") for arg in mark.args})


def pytest_collection_finish(session: pytest.Session) -> None:
    config = session.config
    path = config.getoption(_RULEMAP_OPTION)
    if path is not None:
        rule_map = config.stash.get(_rule_map_key, {})
        _write_json(path, rule_map)
    path = config.getoption(_COLLECTED_OPTION)
    if path is not None:
        _write_json(path, config.stash.get(_collected_key, []))


def _write_json(path: str, data: object) -> None:
    Path(path).write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
