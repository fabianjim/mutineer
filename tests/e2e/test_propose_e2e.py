"""``mutineer rules propose`` on the shop fixture (slow)"""

from __future__ import annotations

import tomllib

import pytest

from mutineer.cli import main

pytestmark = pytest.mark.slow


def _propose(statement):
    return main(
        [
            "rules",
            "propose",
            "--prefix",
            "PRICE",
            "--statement",
            statement,
            "--seam",
            "shop.pricing.discount",
            "--test",
            "tests/test_pricing.py::test_member_discount",
        ]
    )


def _snapshot(root):
    files = [root / ".mutineer/rules.toml", *sorted((root / "tests").rglob("*.py"))]
    return {p: p.read_bytes() for p in files}


def test_propose_writes_only_proposals_and_allocates_the_next_id(shop_copy, monkeypatch, capsys):
    root = shop_copy.path
    monkeypatch.chdir(root)
    before = _snapshot(root)

    assert _propose("Members get 10% off orders over $100") == 0, capsys.readouterr().err
    assert "PROPOSED PRICE-001" in capsys.readouterr().out
    proposals = tomllib.loads((root / ".mutineer/proposals.toml").read_text())["proposal"]
    assert proposals == [
        {
            "id": "PRICE-001",
            "statement": "Members get 10% off orders over $100",
            "seam": "shop.pricing.discount",
            "tests": ["tests/test_pricing.py::test_member_discount"],
        }
    ]
    assert _snapshot(root) == before

    assert _propose("Non-members pay full price") == 0, capsys.readouterr().err
    assert "PROPOSED PRICE-002" in capsys.readouterr().out
    assert _snapshot(root) == before
