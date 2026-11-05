"""WP2 acceptance: runtime rule identity matches the node ids mutmut records (slow)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from mutineer.config import Config
from mutineer.rules import collect_rule_map

pytestmark = pytest.mark.slow


@pytest.mark.parametrize("branch", ["main", "agent-gap"])
def test_rule_map_node_ids_equal_mutmut_stats_for_quote(_shop_repo_session, tmp_path, branch):
    root = tmp_path / "shop"
    env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1")
    subprocess.run(
        ["git", "clone", "-q", "-b", branch, str(_shop_repo_session), str(root)],
        env=env,
        check=True,
    )
    subprocess.run(
        [sys.executable, "-m", "mutmut", "run", "shop.shipping.x_quote__mutmut_*"],
        cwd=root,
        env=env,
        capture_output=True,
        check=True,
    )
    stats = json.loads(Path(root, "mutants/mutmut-stats.json").read_text())
    covering = set(stats["tests_by_mangled_function_name"]["shop.shipping.x_quote"])

    rule_map = collect_rule_map(root, Config())

    assert sorted(rule_map) == [
        "tests/rules/test_shipping_rules.py::test_small_order_pays",
        "tests/rules/test_shipping_rules.py::test_well_over_threshold_ships_free",
    ]
    assert set(rule_map) <= covering
    # main: the rule tests are exactly quote's covering tests; agent-gap adds one impl test.
    assert covering - set(rule_map) == (
        set() if branch == "main" else {"tests/test_express.py::test_express_adds_fee"}
    )
