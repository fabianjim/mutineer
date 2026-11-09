"""``mutineer dispute`` and ``resolve`` between runs on the shop fixture (slow)

On ``agent-gap`` the rule pass leaves two ``survivor_rule`` mutants in ``quote``: line 8,
which a test of ``quote(50)`` kills, and line 10, which is truly equivalent because
``_rate`` ignores its argument. Mutants are identified by line and source text, never by
mutmut's numbering.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mutineer.cli import main
from mutineer.engine.mutmut_engine import MutmutEngine
from mutineer.models import AgentReport

pytestmark = pytest.mark.slow

EQUIVALENT = "rate = _rate(None)"
REASON = "_rate ignores its argument, so no caller can see what is passed to it"
THRESHOLD_TEST = """from shop.shipping import quote


def test_exactly_fifty_ships_free():
    assert quote(50) == 0
"""
MARKED_THRESHOLD_TEST = """import pytest

from shop.shipping import quote


@pytest.mark.rule("SHIP-003")
def test_exactly_fifty_ships_free():
    assert quote(50) == 0
"""


def of_kind(report: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    return [f for f in report["findings"] if f["kind"] == kind]


def score(report: dict[str, Any], name: str) -> tuple[int, int] | None:
    s = report[name]
    return None if s is None else (s["killed"], s["total"])


def last_run(root: Path) -> dict[str, Any]:
    state: dict[str, Any] = json.loads((root / ".mutineer/last-run.json").read_text())
    return state


def survivor_names(root: Path) -> list[str]:
    names: list[str] = last_run(root)["survivor_names"]
    return names


def cli(capsys: pytest.CaptureFixture[str], *argv: str) -> int:
    """Run a Mutineer command in the current directory; fail with its stderr if it errs."""
    capsys.readouterr()
    return main(list(argv))


def dispute_the_equivalent(
    root: Path, report: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    (survivor,) = [f for f in of_kind(report, "survivor_rule") if f["location"]["line"] == 10]
    assert survivor["evidence"]["mutated"] == EQUIVALENT
    assert cli(capsys, "dispute", survivor["id"], REASON) == 0, capsys.readouterr().err


@pytest.fixture
def rerun_names(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """The names each ``rerun`` of the engine is asked for, in order."""
    calls: list[list[str]] = []
    real = MutmutEngine.rerun

    def spy(self: MutmutEngine, names: list[str], *args: Any, **kwargs: Any) -> Any:
        calls.append(list(names))
        return real(self, names, *args, **kwargs)

    monkeypatch.setattr(MutmutEngine, "rerun", spy)
    return calls


def test_a_confirmed_dispute_leaves_only_the_testable_survivor_to_the_agent(
    shop_copy: Any,
    mutineer: Any,
    rerun_names: list[list[str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    code, first = mutineer(root, "run")
    assert (first["status"], code) == ("warn", 0)
    assert [f["location"]["line"] for f in of_kind(first, "survivor_rule")] == [8, 10]
    (line_8,) = [f for f in of_kind(first, "survivor_rule") if f["location"]["line"] == 8]
    testable = line_8["evidence"]["mutant"]

    # 1. the dispute waits for a human, and recheck doesn't rerun the disputed mutant
    dispute_the_equivalent(root, first, capsys)
    assert (root / ".mutineer/disputes.toml").is_file()
    code, report = mutineer(root, "recheck")
    assert (report["status"], code) == ("warn", 0)
    assert rerun_names == [[testable]]
    assert survivor_names(root) == [testable]
    assert [f["location"]["line"] for f in of_kind(report, "survivor_rule")] == [8]
    (disputed,) = of_kind(report, "mutant_disputed")
    assert (disputed["severity"], disputed["audience"]) == ("warn", "human")
    assert disputed["location"]["line"] == 10
    assert disputed["evidence"]["mutated"] == EQUIVALENT
    assert disputed["evidence"]["dispute"]["reason"] == REASON
    assert REASON in disputed["message"]
    assert score(report, "rule_score") == (3, 7)

    # 2. an unmarked test can't clear a survivor_rule; the same test marked with the rule can
    shop_copy.write("tests/test_threshold.py", THRESHOLD_TEST)
    code, report = mutineer(root, "recheck")
    assert (report["status"], code) == ("warn", 0)
    assert [f["location"]["line"] for f in of_kind(report, "survivor_rule")] == [8]
    assert score(report, "rule_score") == (3, 7)

    shop_copy.write("tests/test_threshold.py", MARKED_THRESHOLD_TEST)
    code, report = mutineer(root, "recheck")
    assert (report["status"], code) == ("warn", 0)  # the open dispute still warns
    assert of_kind(report, "survivor_rule") == []
    assert score(report, "rule_score") == (4, 7)
    assert [f for f in report["findings"] if f["severity"] == "block"] == []
    assert rerun_names == [[testable]] * 3
    assert survivor_names(root) == []

    code, agent = mutineer(root, "recheck", fmt="agent")
    assert (agent["status"], code) == ("warn", 0)
    assert (agent["findings"], agent["next_command"]) == ([], None)
    assert AgentReport.model_validate(agent).schema_version == agent["schema_version"]
    assert len(rerun_names) == 3  # nothing left to rerun

    # 3. a human confirms the dispute: the change passes, and the answer is shown once
    (disputed,) = of_kind(last_run(root)["verdict"], "mutant_disputed")
    code = cli(capsys, "resolve", disputed["id"], "--confirm", "--yes")
    assert code == 0, capsys.readouterr().err

    code, report = mutineer(root, "run")
    assert (report["status"], code) == ("pass", 0)
    (disputed,) = of_kind(report, "mutant_disputed")
    assert (disputed["severity"], disputed["evidence"]["dispute"]["status"]) == (
        "info",
        "confirmed",
    )
    (confirmed,) = of_kind(report, "dispute_confirmed")
    assert confirmed["severity"] == "info"
    assert confirmed["evidence"]["mutated"] == EQUIVALENT
    assert of_kind(report, "survivor_rule") == []
    assert score(report, "rule_score") == (4, 6)  # a confirmed equivalent counts in no score

    text = mutineer(root, "recheck", fmt="terminal", to_stdout=True)[1]
    head, _, group = text.partition("Already answered (1)")
    assert group, text
    assert "shipping.py:10" in group and REASON in " ".join(group.split())
    assert "The agent disputes" not in head


def test_a_rejected_dispute_sends_the_survivor_back_to_the_agent(
    shop_copy: Any, mutineer: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    shop_copy.checkout("agent-gap")
    root = shop_copy.path
    _, first = mutineer(root, "run")
    dispute_the_equivalent(root, first, capsys)
    _, report = mutineer(root, "run")
    (disputed,) = of_kind(report, "mutant_disputed")

    response = "a refactor could make _rate read its argument; pin the call"
    code = cli(capsys, "resolve", disputed["id"], "--reject", "--reason", response, "--yes")
    assert code == 0, capsys.readouterr().err

    code, report = mutineer(root, "recheck")
    assert (report["status"], code) == ("warn", 0)
    assert of_kind(report, "mutant_disputed") == []
    (back,) = [f for f in of_kind(report, "survivor_rule") if f["location"]["line"] == 10]
    assert (back["severity"], back["audience"]) == ("warn", "agent")
    assert back["evidence"]["mutated"] == EQUIVALENT
    assert response in back["next_action"]
    assert back["evidence"]["mutant"] in survivor_names(root)

    assert cli(capsys, "dispute", back["id"], REASON) == 4
    assert "rejected" in capsys.readouterr().err
