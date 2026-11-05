"""The mutation engine interface. Everything mutmut-specific lives in this package."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from mutineer.models import Diff, EngineRun, RuleMap


class Engine(Protocol):
    def run(self, diff: Diff, rule_map: RuleMap, root: Path) -> EngineRun:
        """All pass over the changed functions, then the rule pass over rule-covered ones."""
        ...

    def rerun(self, names: list[str], rule_map: RuleMap, root: Path) -> EngineRun:
        """Rule pass only, for these exact mutant names; ``functions``/``unmeasured`` empty."""
        ...
