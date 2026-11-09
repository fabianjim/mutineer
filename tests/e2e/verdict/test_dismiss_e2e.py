"""``mutineer rules dismiss`` between two runs on the shop fixture (slow)"""

from __future__ import annotations

from typing import Any

import pytest

from mutineer.cli import main

pytestmark = pytest.mark.slow

POINTS = "shop.pricing.x_loyalty_points"


def test_a_dismissed_question_is_answered_on_the_next_run(
    shop_copy: Any, mutineer: Any, capsys: Any
) -> None:
    shop_copy.checkout("agent")
    root = shop_copy.path
    code, first = mutineer(root, "run")
    assert (first["status"], code) == ("pass", 0)
    (question,) = [
        f
        for f in first["findings"]
        if f["kind"] == "survivor_unowned" and f["evidence"]["function"] == POINTS
    ]
    assert f"mutineer rules dismiss {POINTS}" in question["next_action"]

    reason = "Points are a marketing detail, not a promise"
    assert main(["rules", "dismiss", POINTS, "--reason", reason, "--yes"]) == 0, (
        capsys.readouterr().err
    )

    code, report = mutineer(root, "run")
    assert (report["status"], code) == ("pass", 0)
    (answered,) = [
        f
        for f in report["findings"]
        if f["kind"] == "survivor_unowned" and f["evidence"]["function"] == POINTS
    ]
    assert answered["evidence"]["answer"] == {"reason": reason, "question": "survivor_unowned"}
    added = [f for f in report["findings"] if f["kind"] == "answer_added"]
    assert [f["evidence"]["function"] for f in added] == [POINTS]

    # with no survivors for the agent, recheck shows the stored verdict again
    text = mutineer(root, "recheck", fmt="terminal", to_stdout=True)[1]
    head, _, group = text.partition("Already answered (1)")
    assert group, text
    assert "loyalty_points" in group and reason in " ".join(group.split())
    assert "in loyalty_points survived" not in head and "in discount survived" in head
