"""pytest plugin, loaded through the ``pytest11`` entry point (name ``mutineer``).

- Registers the ``rule`` marker, so ``--strict-markers`` passes.
- ``--mutineer-rulemap=PATH`` writes the ``RuleMap`` (node id -> sorted rule ids) as
  JSON after collection. Without the option the plugin does nothing else.

Rule ids come from every ``rule`` mark that applies to a test: on the function, its
class and the module ``pytestmark``. Several ids per mark and stacked marks are merged.
The map is taken before any deselection (``-m``, ``-k``), so a user's filter never hides
a rule test.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

_RULEMAP_OPTION = "mutineer_rulemap"
_rule_map_key = pytest.StashKey[dict[str, list[str]]]()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.getgroup("mutineer").addoption(
        "--mutineer-rulemap",
        dest=_RULEMAP_OPTION,
        metavar="PATH",
        default=None,
        help="Write the rule map (test node id -> rule ids) as JSON to PATH after collection.",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "rule(*ids): links a test to rules in .mutineer/rules.toml (Mutineer)",
    )


@pytest.hookimpl(tryfirst=True)
def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption(_RULEMAP_OPTION) is None:
        return
    rule_map: dict[str, list[str]] = {}
    for item in items:
        ids = {str(arg) for mark in item.iter_markers("rule") for arg in mark.args}
        if ids:
            rule_map[item.nodeid] = sorted(ids)
    config.stash[_rule_map_key] = rule_map


def pytest_collection_finish(session: pytest.Session) -> None:
    path = session.config.getoption(_RULEMAP_OPTION)
    if path is None:
        return
    rule_map = session.config.stash.get(_rule_map_key, {})
    Path(path).write_text(json.dumps(rule_map, indent=2, sort_keys=True) + "\n", encoding="utf-8")
