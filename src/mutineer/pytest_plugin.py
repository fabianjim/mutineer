"""pytest plugin, loaded through the ``pytest11`` entry point.

WP0 stub: registers the ``rule`` marker so ``--strict-markers`` passes.
WP2 adds ``--mutineer-rulemap``.
"""

from __future__ import annotations

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "rule(*ids): links a test to rules in .mutineer/rules.toml (Mutineer)",
    )
