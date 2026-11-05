"""Fixtures for the engine's end-to-end tests.

- ``shop_copy``: a private copy of the fixture repo in ``tmp_path``, on ``main``, for tests
  that change files, configs or ``mutants/``. It has the same helpers as ``shop_repo``.
- ``engine``: a ``MutmutEngine``.
- ``ship_rule_map``: the rule map ``collect_rule_map`` returns on the fixture, built by
  hand so these tests don't depend on ``collect_rule_map``.
- ``diff_of``: ``load_diff`` for a repo, against ``main`` by default.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mutineer.engine.mutmut_engine import MutmutEngine
from mutineer.gitdiff import load_diff
from mutineer.models import Diff, RuleMap

SHIP_RULE_MAP: RuleMap = {
    "tests/rules/test_shipping_rules.py::test_well_over_threshold_ships_free": ["SHIP-003"],
    "tests/rules/test_shipping_rules.py::test_small_order_pays": ["SHIP-003"],
}


@pytest.fixture
def shop_copy(shop_repo: Any, tmp_path: Path) -> Any:
    shop_repo.checkout("main")
    dst = tmp_path / "shop"
    shutil.copytree(shop_repo.path, dst, symlinks=True)
    return type(shop_repo)(dst)


@pytest.fixture
def engine() -> MutmutEngine:
    return MutmutEngine()


@pytest.fixture
def ship_rule_map() -> RuleMap:
    return dict(SHIP_RULE_MAP)


@pytest.fixture
def diff_of() -> Callable[..., Diff]:
    def load(root: Path, base: str = "main") -> Diff:
        return load_diff(root, base, ".mutineer/rules.toml")

    return load
