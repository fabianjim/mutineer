"""``ledger``: the proposals and answers files are UTF-8 whatever the locale says"""

from __future__ import annotations

import locale
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from mutineer.ledger import ANSWERS_FILE, PROPOSALS_FILE, load_ledger

STATEMENT = "Members get 10 % off — außer Gutscheine"
REASON = "Punkte sind ein Marketing-Detail — no promise"


@pytest.fixture
def c_locale() -> Iterator[None]:
    if sys.flags.utf8_mode:
        pytest.skip("UTF-8 mode makes every locale read UTF-8")
    old = locale.setlocale(locale.LC_CTYPE)
    try:
        locale.setlocale(locale.LC_CTYPE, "C")
    except locale.Error:
        pytest.skip("the C locale is not available")
    try:
        yield
    finally:
        locale.setlocale(locale.LC_CTYPE, old)


def test_ledger_reads_non_ascii_text_under_a_non_utf8_locale(
    tmp_path: Path, c_locale: None
) -> None:
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / PROPOSALS_FILE).write_bytes(
        (
            "version = 1\n\n[[proposal]]\n"
            f'id = "PRICE-001"\nstatement = "{STATEMENT}"\n'
            'seam = "shop.pricing.discount"\n'
            'tests = ["tests/test_pricing.py::test_member_discount"]\n'
        ).encode()
    )
    (tmp_path / ANSWERS_FILE).write_bytes(
        (
            "version = 1\n\n[[answer]]\n"
            'function = "shop.pricing.x_loyalty_points"\n'
            f'question = "survivor_unowned"\nreason = "{REASON}"\n'
        ).encode()
    )

    ledger = load_ledger(tmp_path)

    assert [p.statement for p in ledger.proposals.proposals] == [STATEMENT]
    assert [a.reason for a in ledger.answers.answers] == [REASON]
