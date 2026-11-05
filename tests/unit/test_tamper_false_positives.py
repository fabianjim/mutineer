"""False-positive guard (build spec section 8, WP1): none of these gives a finding above INFO."""

from __future__ import annotations

import pytest

from mutineer.models import Severity

RULE_PATH = "tests/rules/test_ship.py"

BASE = """import pytest
from shop import quote

pytestmark = pytest.mark.rule("SHIP-003")


def test_free():
    total = 80
    assert quote(total) == 0


def test_pays():
    assert quote(10) == 5


@pytest.mark.parametrize('total,fee', [(10, 5), (80, 0)])
def test_table(total, fee):
    assert quote(total) == fee
"""

RENAMED_LOCAL = BASE.replace(
    "total = 80\n    assert quote(total)", "amount = 80\n    assert quote(amount)"
)

ADDED_ASSERTION = BASE.replace(
    "assert quote(10) == 5\n", "assert quote(10) == 5\n    assert quote(49) == 5\n"
)

# What `ruff format` (and similar tools) do: quotes, spacing, line breaks, trailing commas.
REFORMATTED = """import pytest
from shop import quote

pytestmark = pytest.mark.rule('SHIP-003')


def test_free():
    total = 80
    assert (
        quote( total )
        == 0
    )


def test_pays():
    assert quote(10)==5


@pytest.mark.parametrize(
    "total,fee",
    [
        (10, 5),
        (80, 0),
    ],
)
def test_table(total, fee):
    assert quote(total) == fee
"""

REORDERED = """import pytest
from shop import quote

pytestmark = pytest.mark.rule("SHIP-003")


@pytest.mark.parametrize('total,fee', [(10, 5), (80, 0)])
def test_table(total, fee):
    assert quote(total) == fee


def test_pays():
    assert quote(10) == 5


def test_free():
    total = 80
    assert quote(total) == 0
"""

ADDED_CASE = BASE.replace("[(10, 5), (80, 0)]", "[(10, 5), (49, 5), (80, 0)]")


def _above_info(findings):
    return [f for f in findings if f.severity != Severity.INFO]


@pytest.mark.parametrize(
    "head",
    [RENAMED_LOCAL, ADDED_ASSERTION, REFORMATTED, REORDERED, ADDED_CASE],
    ids=["rename_local", "add_assertion", "reformat", "reorder", "add_parametrize_case"],
)
def test_harmless_edits_give_nothing_above_info(tamper_repo, head):
    tamper_repo.base({RULE_PATH: BASE})
    assert _above_info(tamper_repo.head({RULE_PATH: head})) == []


def test_real_ruff_format_gives_nothing_above_info(tamper_repo):
    import subprocess
    import sys

    tamper_repo.base({RULE_PATH: REFORMATTED})
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "format", "--isolated", "-q", RULE_PATH],
        cwd=tamper_repo.path,
        capture_output=True,
    )
    if proc.returncode != 0:
        pytest.skip("ruff is not importable as a module here")
    assert (tamper_repo.path / RULE_PATH).read_text() != REFORMATTED
    assert _above_info(tamper_repo.findings()) == []


def test_moving_a_rule_test_with_its_marker_is_not_a_deletion(tamper_repo):
    other = "tests/rules/test_moved.py"
    tamper_repo.base({RULE_PATH: BASE, other: "import pytest\n"})
    head = BASE.replace("\n\ndef test_pays():\n    assert quote(10) == 5\n", "")
    moved = (
        "import pytest\nfrom shop import quote\n\n\n"
        '@pytest.mark.rule("SHIP-003")\ndef test_pays():\n    assert quote(10) == 5\n'
    )
    assert tamper_repo.head({RULE_PATH: head, other: moved}) == []


def test_moving_a_rule_test_to_a_new_file_is_not_a_deletion(tamper_repo):
    tamper_repo.base({RULE_PATH: BASE})
    head = BASE.replace("\n\ndef test_pays():\n    assert quote(10) == 5\n", "")
    moved = (
        "import pytest\nfrom shop import quote\n\n"
        'pytestmark = pytest.mark.rule("SHIP-003")\n\n\n'
        "class TestPaying:\n    pass\n\n\n"
        "def test_small_order_pays():\n    assert quote(10) == 5\n"
    )
    findings = tamper_repo.head({RULE_PATH: head, "tests/test_new_home.py": moved})
    assert _above_info(findings) == []


def test_moving_a_rule_test_without_its_marker_is_flagged(tamper_repo):
    tamper_repo.base({RULE_PATH: BASE})
    head = BASE.replace("\n\ndef test_pays():\n    assert quote(10) == 5\n", "")
    moved = "from shop import quote\n\n\ndef test_pays():\n    assert quote(10) == 5\n"
    findings = tamper_repo.head({RULE_PATH: head, "tests/test_elsewhere.py": moved})
    assert [(f.kind, f.location.file) for f in findings] == [
        ("rule_marker_removed", "tests/test_elsewhere.py")
    ]


def test_renaming_the_test_file_is_harmless(tamper_repo):
    tamper_repo.base({RULE_PATH: BASE})
    (tamper_repo.path / RULE_PATH).rename(tamper_repo.path / "tests/rules/test_shipping.py")
    assert tamper_repo.findings() == []


def test_adding_a_new_test_and_a_new_rule_test_is_harmless(tamper_repo):
    tamper_repo.base({RULE_PATH: BASE})
    head = BASE + '\n\n@pytest.mark.rule("PAY-001")\ndef test_new():\n    assert quote(1) == 5\n'
    assert tamper_repo.head({RULE_PATH: head, "tests/test_x.py": "def test_x():\n    pass\n"}) == []


def test_strengthening_is_harmless(tamper_repo):
    base = BASE.replace("assert quote(10) == 5", "assert quote(10) >= 0")
    tamper_repo.base({RULE_PATH: base})
    head = base.replace("assert quote(10) >= 0", "assert quote(10) == 5")
    assert tamper_repo.head({RULE_PATH: head}) == []


def test_changing_a_non_literal_operand_is_not_expected_changed(tamper_repo):
    tamper_repo.base({RULE_PATH: BASE})
    head = BASE.replace("assert quote(10) == 5", "assert quote(10) == quote(11)")
    assert tamper_repo.head({RULE_PATH: head}) == []


def test_unparsable_head_file_is_skipped_not_reported_as_deleted(tamper_repo):
    tamper_repo.base({RULE_PATH: BASE})
    assert tamper_repo.head({RULE_PATH: BASE + "\ndef broken(:\n"}) == []


def test_files_outside_test_paths_are_ignored(tamper_repo):
    tamper_repo.base({"scripts/test_tool.py": BASE})
    assert tamper_repo.head({"scripts/test_tool.py": None}) == []
