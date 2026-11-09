"""``ledger``: the answers file and the ``answer_added`` findings"""

from __future__ import annotations

from pathlib import Path

import pytest

from mutineer.errors import RulesError
from mutineer.ledger import (
    ANSWERS_FILE,
    Ledger,
    answer_findings,
    append_answer,
    load_answers,
    load_ledger,
    parse_answers,
)
from mutineer.models import Answer, AnswersFile, Severity

POINTS = Answer(
    function="shop.pricing.x_loyalty_points",
    question="survivor_unowned",
    reason="Points are a marketing detail",
    file="src/shop/pricing.py",
    qualname="loyalty_points",
)
QUOTE = Answer(
    function="shop.shipping.x_quote",
    question="guarded_by_impl_only",
    rules=["SHIP-003"],
    reason='The "express" fee is not part of it',
)

BASE = """\
version = 1

[[answer]]
function = "shop.pricing.x_loyalty_points"
question = "survivor_unowned"
reason = "Points are a marketing detail"
file = "src/shop/pricing.py"
qualname = "loyalty_points"
"""


##
# the file


def test_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_answers(tmp_path) == AnswersFile()
    assert load_ledger(tmp_path) == Ledger()


def test_the_documented_shape_loads() -> None:
    assert parse_answers(BASE).answers == [POINTS]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("version = 1\n[[answer]\n", "not valid TOML"),
        ('[[answer]]\nfunction = "f"\nquestion = "survivor_unowned"\n', "missing required key"),
        ('[[answer]]\nfunction = "f"\nquestion = "other"\nreason = "r"\n', "question"),
        (
            '[[answer]]\nfunction = "f"\nquestion = "survivor_unowned"\nreason = "r"\nx = 1\n',
            "unknown key 'x'",
        ),
        ('[[answers]]\nfunction = "f"\n', "[[answer]] table"),
    ],
)
def test_an_invalid_file_says_what_to_fix(tmp_path: Path, text: str, message: str) -> None:
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / ANSWERS_FILE).write_text(text)
    with pytest.raises(RulesError, match=ANSWERS_FILE) as exc:
        load_answers(tmp_path)
    assert message in str(exc.value)


def test_append_creates_the_file_and_keeps_comments(tmp_path: Path) -> None:
    append_answer(tmp_path, POINTS)
    assert load_answers(tmp_path).answers == [POINTS]

    path = tmp_path / ANSWERS_FILE
    path.write_text(path.read_text() + "# keep me\n")
    append_answer(tmp_path, QUOTE)
    text = path.read_text()
    assert "# keep me\n" in text
    assert text.index("# keep me") < text.index("shop.shipping.x_quote")
    assert load_answers(tmp_path).answers == [POINTS, QUOTE]


def test_append_to_a_file_without_a_final_newline(tmp_path: Path) -> None:
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / ANSWERS_FILE).write_text("version = 1  # no newline")
    append_answer(tmp_path, QUOTE)
    assert load_answers(tmp_path).answers == [QUOTE]


##
# answer_added


def test_an_added_answer_is_reported_once() -> None:
    head = AnswersFile(answers=[POINTS, QUOTE])
    (f,) = answer_findings(BASE, head)
    assert (f.check, f.kind, f.severity, f.audience) == (
        "rules",
        "answer_added",
        Severity.INFO,
        "human",
    )
    assert f.location is not None and f.location.file == ANSWERS_FILE
    assert f.rules == ["SHIP-003"]
    assert f.evidence == QUOTE.model_dump()
    assert f.message == (
        'A human answer was recorded for shop.shipping.x_quote: The "express" fee is not part of it'
    )
    assert f.next_action == "Check that a human recorded this answer."


def test_a_changed_answer_is_reported() -> None:
    changed = POINTS.model_copy(update={"reason": "Not a promise after all"})
    (f,) = answer_findings(BASE, AnswersFile(answers=[changed]))
    assert f.evidence["reason"] == "Not a promise after all"


def test_a_removed_answer_gives_no_finding() -> None:
    assert answer_findings(BASE, AnswersFile()) == []
    assert answer_findings(BASE, AnswersFile(answers=[POINTS])) == []


@pytest.mark.parametrize("base", [None, "not toml ["])
def test_a_missing_or_invalid_base_counts_as_empty(base: str | None) -> None:
    findings = answer_findings(base, AnswersFile(answers=[POINTS, QUOTE]))
    assert [f.evidence["function"] for f in findings] == [POINTS.function, QUOTE.function]


def test_an_unreadable_file_says_what_to_fix(tmp_path: Path) -> None:
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / ANSWERS_FILE).write_bytes(b"reason = '\xff'\n")
    with pytest.raises(
        RulesError, match=rf"^{ANSWERS_FILE} can't be read \(.*\); fix or remove it$"
    ):
        load_answers(tmp_path)


def test_the_ledger_holds_the_proposals_and_the_answers(tmp_path: Path) -> None:
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / ANSWERS_FILE).write_text(BASE)
    (tmp_path / ".mutineer/proposals.toml").write_text('[[proposal]]\nid = "A-001"\n')
    loaded = load_ledger(tmp_path)
    assert [p.id for p in loaded.proposals.proposals] == ["A-001"]
    assert loaded.answers.answers == [POINTS]
