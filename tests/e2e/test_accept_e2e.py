"""``mutineer rules accept`` on the shop fixture, then ``run --no-mutation`` (slow)"""

from __future__ import annotations

import json
import tomllib

import pytest

from mutineer.cli import main
from mutineer.rules import collect_rule_map

pytestmark = pytest.mark.slow

MEMBER = "tests/test_pricing.py::test_member_discount"


def test_accept_marks_the_test_and_run_reports_only_the_rules_change(
    shop_copy, monkeypatch, capsys
):
    root = shop_copy.path
    shop_copy.checkout("agent")
    monkeypatch.chdir(root)
    rules_before = (root / ".mutineer/rules.toml").read_text()

    argv = ["rules", "propose", "--prefix", "PRICE", "--statement", "Members get 10% off"]
    assert main([*argv, "--seam", "shop.pricing.discount", "--test", MEMBER]) == 0
    assert main(["rules", "accept", "PRICE-001", "--yes"]) == 0, capsys.readouterr().err

    rules_after = (root / ".mutineer/rules.toml").read_text()
    assert rules_after.startswith(rules_before)
    added = tomllib.loads(rules_after)["rule"][-1]
    assert added == {
        "id": "PRICE-001",
        "statement": "Members get 10% off",
        "seam": "shop.pricing.discount",
    }
    assert (
        '@pytest.mark.rule("PRICE-001")\ndef test_member_discount'
        in (root / "tests/test_pricing.py").read_text()
    )
    assert "proposal" not in tomllib.loads((root / ".mutineer/proposals.toml").read_text())
    assert collect_rule_map(root)[MEMBER] == ["PRICE-001"]

    out = root.parent / "report.json"
    code = main(["run", "--no-mutation", "--format", "json", "--output", str(out)])
    report = json.loads(out.read_text())
    assert code == 0, report
    tamper = [
        f
        for f in report["findings"]
        if f["check"] == "tamper" and f["severity"] != "info" and MEMBER in json.dumps(f)
    ]
    assert tamper == []
    changed = [f for f in report["findings"] if f["kind"] == "rules_file_changed"]
    assert [f["rules"] for f in changed] == [["PRICE-001"]]
