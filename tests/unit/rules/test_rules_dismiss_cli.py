"""``mutineer rules dismiss``: a human's "no", appended to .mutineer/answers.toml"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from mutineer import state
from mutineer.cli import main
from mutineer.ledger import ANSWERS_FILE, load_answers
from mutineer.models import Answer, Audience, Finding, Location, Severity, Verdict

POINTS = "shop.pricing.x_loyalty_points"
QUOTE = "shop.shipping.x_quote"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _question(kind: str, function: str, rules: list[str], qualname: str) -> Finding:
    return Finding(
        id="mutation-0001",
        check="mutation",
        kind=kind,
        severity=Severity.INFO,
        audience=Audience.HUMAN,
        rules=rules,
        location=Location(file="src/shop/mod.py", line=3, end_line=5),
        message="A question.",
        evidence={"function": function, "qualname": qualname, "mutants": []},
    )


@pytest.fixture
def last_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    verdict = Verdict(
        status="pass",
        base="abc",
        head="WORKTREE",
        rule_score=None,
        diff_score=None,
        findings=[
            _question("survivor_unowned", POINTS, [], "loyalty_points"),
            _question("guarded_by_impl_only", QUOTE, ["SHIP-003", "PAY-001"], "quote"),
        ],
        duration_s=1.0,
    )
    state.save(tmp_path, state.State(base_ref="main", base="abc", verdict=verdict))
    return tmp_path


def dismiss(*argv: str) -> int:
    return main(["rules", "dismiss", *argv])


def test_without_a_last_run_it_says_to_run_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    assert dismiss(POINTS, "--reason", "no", "--yes") == 4
    assert "run `mutineer run` first" in capsys.readouterr().err
    assert not (tmp_path / ANSWERS_FILE).exists()


def test_an_unknown_function_writes_nothing(last_run: Path, capsys: Any) -> None:
    assert dismiss(POINTS, "shop.x_nope", "--reason", "no", "--yes") == 4
    assert "shop.x_nope" in capsys.readouterr().err
    assert not (last_run / ANSWERS_FILE).exists()


@pytest.mark.parametrize("reason", ["", "   ", "two\nlines"])
def test_the_reason_is_one_non_empty_line(last_run: Path, reason: str, capsys: Any) -> None:
    assert dismiss(POINTS, "--reason", reason, "--yes") == 4
    assert "one non-empty line" in capsys.readouterr().err
    assert not (last_run / ANSWERS_FILE).exists()


def test_without_a_terminal_it_needs_yes(last_run: Path, capsys: Any) -> None:
    assert dismiss(POINTS, "--reason", "no") == 4
    assert "records a human decision" in capsys.readouterr().err
    assert not (last_run / ANSWERS_FILE).exists()


def test_a_human_at_a_terminal_confirms(
    last_run: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr("sys.stdin", _Tty("n\n"))
    assert dismiss(POINTS, "--reason", "no") == 4
    assert not (last_run / ANSWERS_FILE).exists()
    monkeypatch.setattr("sys.stdin", _Tty("y\n"))
    assert dismiss(POINTS, "--reason", "no") == 0
    assert f"DISMISS {POINTS}" in capsys.readouterr().out
    assert [a.function for a in load_answers(last_run).answers] == [POINTS]


def test_answers_come_from_the_last_runs_findings(last_run: Path, capsys: Any) -> None:
    assert dismiss(POINTS, QUOTE, "--reason", "Marketing detail", "--yes") == 0
    out = capsys.readouterr().out
    assert f"DISMISSED {POINTS}" in out and f"DISMISSED {QUOTE}" in out
    assert load_answers(last_run).answers == [
        Answer(
            function=POINTS,
            question="survivor_unowned",
            reason="Marketing detail",
            file="src/shop/mod.py",
            qualname="loyalty_points",
        ),
        Answer(
            function=QUOTE,
            question="guarded_by_impl_only",
            rules=["PAY-001", "SHIP-003"],
            reason="Marketing detail",
            file="src/shop/mod.py",
            qualname="quote",
        ),
    ]


def test_an_existing_comment_is_kept(last_run: Path) -> None:
    path = last_run / ANSWERS_FILE
    path.write_text("# answers are reviewed by the pricing team\nversion = 1\n")
    assert dismiss(POINTS, "--reason", "no", "--yes") == 0
    text = path.read_text()
    assert text.startswith("# answers are reviewed by the pricing team\nversion = 1\n")
    assert [a.function for a in load_answers(last_run).answers] == [POINTS]


def test_a_duplicate_is_not_written_again(last_run: Path, capsys: Any) -> None:
    assert dismiss(POINTS, "--reason", "no", "--yes") == 0
    before = (last_run / ANSWERS_FILE).read_text()
    capsys.readouterr()
    assert dismiss(POINTS, "--reason", "no", "--yes") == 0
    assert f"ALREADY ANSWERED {POINTS}" in capsys.readouterr().out
    assert (last_run / ANSWERS_FILE).read_text() == before
