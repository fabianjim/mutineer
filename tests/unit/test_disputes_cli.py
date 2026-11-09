"""``mutineer dispute`` and ``mutineer resolve``: disputes in .mutineer/disputes.toml"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from mutineer import state
from mutineer.cli import main
from mutineer.ledger import DISPUTES_FILE, Ledger, load_disputes, load_ledger, save_disputes
from mutineer.models import (
    Audience,
    Dispute,
    DisputesFile,
    Finding,
    Location,
    Severity,
    Verdict,
)

FN = "shop.io.x_read"
ORIGINAL = 'return open(p, encoding="utf-8").read()'
MUTATED = 'return open(p, encoding="UTF-8").read()'
OTHER_MUTATED = 'return open(p, encoding="XXutf-8XX").read()'
REASON = "Codec names are case-insensitive"


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


def _finding(fid: str, kind: str, **evidence: Any) -> Finding:
    return Finding(
        id=fid,
        check="mutation",
        kind=kind,
        severity=Severity.WARN,
        audience=Audience.AGENT,
        rules=["IO-002", "IO-001"],
        location=Location(file="src/shop/io.py", line=7),
        message="A finding.",
        evidence={"function": FN, "line": 7, **evidence},
    )


def _disputed(fid: str, mutated: str) -> Finding:
    return _finding(
        fid,
        "mutant_disputed",
        original=ORIGINAL,
        mutated=mutated,
        dispute={"reason": REASON, "status": "open", "response": None},
    )


@pytest.fixture
def last_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    verdict = Verdict(
        status="warn",
        base="abc",
        head="WORKTREE",
        rule_score=None,
        diff_score=None,
        findings=[
            _finding("mutation-0001", "survivor_rule", original=ORIGINAL, mutated=MUTATED),
            _finding("mutation-0002", "survivor_unowned"),
            _finding("mutation-0003", "survivor_rule", original=None, mutated=None),
            _disputed("mutation-0004", MUTATED),
            _disputed("mutation-0005", OTHER_MUTATED),
        ],
        duration_s=1.0,
    )
    state.save(tmp_path, state.State(base_ref="main", base="abc", verdict=verdict))
    return tmp_path


def dispute(*argv: str) -> int:
    return main(["dispute", *argv])


def resolve(*argv: str) -> int:
    return main(["resolve", *argv])


def _open(mutated: str = MUTATED) -> Dispute:
    return Dispute(
        function=FN,
        original=ORIGINAL,
        mutated=mutated,
        rules=["IO-001", "IO-002"],
        reason=REASON,
        file="src/shop/io.py",
        line=7,
    )


##
# dispute


def test_without_a_last_run_it_says_to_run_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.chdir(tmp_path)
    assert dispute("mutation-0001", REASON) == 4
    assert "run `mutineer run` first" in capsys.readouterr().err


def test_filing_a_dispute(last_run: Path, capsys: Any) -> None:
    assert dispute("mutation-0001", REASON) == 0
    out = capsys.readouterr().out
    assert "DISPUTED mutation-0001" in out and FN in out and "line 7" in out
    assert "mutineer recheck --format agent" in out
    assert load_disputes(last_run).disputes == [_open()]
    assert load_ledger(last_run).disputes.disputes == [_open()]


def test_only_a_survivor_rule_can_be_disputed(last_run: Path, capsys: Any) -> None:
    assert dispute("mutation-0002", REASON) == 4
    assert "only survivor_rule" in capsys.readouterr().err
    assert not (last_run / DISPUTES_FILE).exists()


def test_an_unknown_id_is_refused(last_run: Path, capsys: Any) -> None:
    assert dispute("mutation-9999", REASON) == 4
    assert "mutation-9999" in capsys.readouterr().err
    assert not (last_run / DISPUTES_FILE).exists()


def test_a_finding_without_the_mutation_text_is_refused(last_run: Path) -> None:
    assert dispute("mutation-0003", REASON) == 4
    assert not (last_run / DISPUTES_FILE).exists()


@pytest.mark.parametrize("reason", ["", "  ", "two\nlines"])
def test_the_reason_is_one_line(last_run: Path, reason: str) -> None:
    assert dispute("mutation-0001", reason) == 4
    assert not (last_run / DISPUTES_FILE).exists()


@pytest.mark.parametrize("status", ["open", "confirmed"])
def test_an_existing_dispute_is_already_disputed(last_run: Path, status: str, capsys: Any) -> None:
    before = DisputesFile(
        disputes=[_open().model_copy(update={"status": status, "reason": "first"})]
    )
    save_disputes(last_run, before)
    assert dispute("mutation-0001", REASON) == 0
    assert "ALREADY DISPUTED mutation-0001" in capsys.readouterr().out
    assert load_disputes(last_run) == before


def test_a_rejected_dispute_cant_be_filed_again(last_run: Path, capsys: Any) -> None:
    rejected = _open().model_copy(update={"status": "rejected", "response": "Callers log it"})
    save_disputes(last_run, DisputesFile(disputes=[rejected]))
    assert dispute("mutation-0001", REASON) == 4
    err = capsys.readouterr().err
    assert "rejected" in err and "Callers log it" in err and "write a test" in err
    assert load_disputes(last_run).disputes == [rejected]


##
# resolve


@pytest.fixture
def disputed(last_run: Path) -> Path:
    save_disputes(last_run, DisputesFile(disputes=[_open(), _open(OTHER_MUTATED)]))
    return last_run


def test_confirm(disputed: Path, capsys: Any) -> None:
    assert resolve("mutation-0004", "--confirm", "--yes") == 0
    out = capsys.readouterr().out
    assert "CONFIRMED mutation-0004" in out and f"Commit {DISPUTES_FILE}" in out
    assert [(d.status, d.response) for d in load_disputes(disputed).disputes] == [
        ("confirmed", None),
        ("open", None),
    ]


def test_reject_with_a_reason(disputed: Path, capsys: Any) -> None:
    assert (
        resolve("mutation-0004", "mutation-0005", "--reject", "--reason", "Visible", "--yes") == 0
    )
    out = capsys.readouterr().out
    assert "REJECTED mutation-0004" in out and "REJECTED mutation-0005" in out
    assert [(d.status, d.response) for d in load_disputes(disputed).disputes] == [
        ("rejected", "Visible"),
        ("rejected", "Visible"),
    ]


def test_reject_without_a_reason_is_refused(disputed: Path, capsys: Any) -> None:
    before = (disputed / DISPUTES_FILE).read_text()
    assert resolve("mutation-0004", "--reject", "--yes") == 4
    assert "--reason" in capsys.readouterr().err
    assert (disputed / DISPUTES_FILE).read_text() == before


@pytest.mark.parametrize("flags", [(), ("--confirm", "--reject", "--reason", "x")])
def test_exactly_one_of_confirm_or_reject(disputed: Path, flags: tuple[str, ...]) -> None:
    assert resolve("mutation-0004", *flags, "--yes") == 4


def test_without_a_terminal_it_needs_yes(disputed: Path, capsys: Any) -> None:
    before = (disputed / DISPUTES_FILE).read_text()
    assert resolve("mutation-0004", "--confirm") == 4
    assert "`mutineer resolve` records a human decision" in capsys.readouterr().err
    assert (disputed / DISPUTES_FILE).read_text() == before


def test_a_human_at_a_terminal_confirms(
    disputed: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    monkeypatch.setattr("sys.stdin", _Tty("y\n"))
    assert resolve("mutation-0004", "--confirm") == 0
    assert "CONFIRM mutation-0004" in capsys.readouterr().out
    assert load_disputes(disputed).disputes[0].status == "confirmed"


@pytest.mark.parametrize("unknown", ["mutation-9999", "mutation-0001"])
def test_an_unknown_id_changes_nothing(disputed: Path, unknown: str, capsys: Any) -> None:
    before = (disputed / DISPUTES_FILE).read_text()
    assert resolve("mutation-0004", unknown, "--confirm", "--yes") == 4
    assert unknown in capsys.readouterr().err
    assert (disputed / DISPUTES_FILE).read_text() == before


def test_a_dispute_no_longer_in_the_file_is_refused(last_run: Path) -> None:
    save_disputes(last_run, DisputesFile(disputes=[_open(OTHER_MUTATED)]))
    assert resolve("mutation-0004", "--confirm", "--yes") == 4


##
# the file


def test_a_missing_file_is_empty(tmp_path: Path) -> None:
    assert load_disputes(tmp_path) == DisputesFile()
    assert load_ledger(tmp_path) == Ledger()


def test_the_file_round_trips(tmp_path: Path) -> None:
    file = DisputesFile(
        disputes=[
            _open(),
            _open(OTHER_MUTATED).model_copy(update={"status": "rejected", "response": 'a "b"'}),
        ]
    )
    save_disputes(tmp_path, file)
    assert load_disputes(tmp_path) == file
    assert "comments" in (tmp_path / DISPUTES_FILE).read_text()


_ENTRY = f"""\
[[dispute]]
function = "{FN}"
original = "a"
mutated = "b"
reason = "r"
"""


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("version = 1\n[[dispute]\n", "not valid TOML"),
        (_ENTRY + "x = 1\n", "unknown key 'x'"),
        (_ENTRY + "\n" + _ENTRY, "more than once"),
        (_ENTRY + 'status = "rejected"\n', "no response"),
    ],
)
def test_an_invalid_file_exits_4_naming_it(
    last_run: Path, text: str, message: str, capsys: Any
) -> None:
    (last_run / DISPUTES_FILE).write_text(text)
    assert dispute("mutation-0001", REASON) == 4
    err = capsys.readouterr().err
    assert DISPUTES_FILE in err and message in err
    assert (last_run / DISPUTES_FILE).read_text() == text


def test_an_unreadable_file_exits_4_naming_it(last_run: Path, capsys: Any) -> None:
    (last_run / DISPUTES_FILE).write_bytes(b"version = 1\n# \xff\n")
    assert dispute("mutation-0001", REASON) == 4
    err = capsys.readouterr().err
    assert f"{DISPUTES_FILE} can't be read" in err
