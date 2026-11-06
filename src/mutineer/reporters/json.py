"""``--format json``: the full ``Verdict``, as published in ``docs/schema``."""

from __future__ import annotations

from mutineer.models import Verdict


def render(verdict: Verdict) -> str:
    return verdict.model_dump_json(indent=2) + "\n"
