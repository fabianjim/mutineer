from __future__ import annotations

import json
from pathlib import Path

import pydantic
import pytest

from mutineer.models import (
    SCHEMA_VERSION,
    Audience,
    ChangedFile,
    Diff,
    EngineRun,
    Finding,
    FunctionRef,
    Hunk,
    Location,
    MutantResult,
    MutantStatus,
    Rule,
    RulesFile,
    Score,
    Severity,
    UnmeasuredLocation,
    Verdict,
    verdict_json_schema,
)

SCHEMA_FILE = Path(__file__).parents[2] / "docs" / "schema" / "verdict.schema.json"

FUNCTION = FunctionRef(
    mutmut_name="shop.cart.xǁCartǁadd",
    file="src/shop/cart.py",
    qualname="Cart.add",
    start_line=5,
    end_line=8,
)
RESULT = MutantResult(
    name="shop.cart.xǁCartǁadd__mutmut_1",
    function="shop.cart.xǁCartǁadd",
    run="rule",
    status=MutantStatus.SURVIVED,
    line=6,
    original="if qty < 1:",
    mutated="if qty <= 1:",
)
FINDING = Finding(
    id="mutation-0001",
    check="mutation",
    kind="survivor_rule",
    severity=Severity.WARN,
    audience=Audience.AGENT,
    rules=["SHIP-003"],
    location=Location(file="src/shop/shipping.py", line=8, end_line=8),
    message="Nothing catches this.",
    evidence={"mutants": [{"name": "x", "status": "survived"}], "count": 2, "ok": None},
    next_action="Test SHIP-003 through `shop.shipping.quote`.",
)

INSTANCES = [
    Location(file="a.py"),
    Rule(id="SHIP-003", statement="Orders of $50 or more ship free", seam="shop.shipping.quote"),
    RulesFile(rules=[Rule(id="A-001", statement="s", status="retired")]),
    Hunk(start=3, length=0),
    ChangedFile(path="b.py", old_path="a.py", status="renamed", hunks=[Hunk(start=1, length=2)]),
    Diff(base="abc", head="WORKTREE", files=[], rules_file_changed_ids=["SHIP-003"]),
    FUNCTION,
    RESULT,
    UnmeasuredLocation(file="src/shop/shipping.py", line=3, end_line=3, reason="module_level"),
    EngineRun(
        baseline_failed=False,
        baseline_output="tail",
        functions=[FUNCTION],
        unmeasured=[UnmeasuredLocation(file="x.py", line=1, reason="decorated")],
        unreached=["shop.pricing.x_new"],
        results=[RESULT],
        tests_by_function={"shop.cart.xǁCartǁadd": ["tests/test_cart.py::test_cart"]},
        duration_s=1.5,
    ),
    FINDING,
    Finding(
        check="tamper", kind="rule_test_deleted", severity="block", audience="human", message="m"
    ),
    Score(killed=3, total=7),
    Verdict(
        status="warn",
        base="abc",
        head="WORKTREE",
        rule_score=Score(killed=3, total=7),
        diff_score=None,
        findings=[FINDING],
        duration_s=0.25,
    ),
]


@pytest.mark.parametrize("instance", INSTANCES, ids=lambda m: type(m).__name__)
def test_every_model_round_trips_through_json(instance):
    model = type(instance)
    again = model.model_validate_json(instance.model_dump_json())
    assert again == instance
    assert json.loads(again.model_dump_json()) == json.loads(instance.model_dump_json())


def test_models_are_frozen():
    with pytest.raises(pydantic.ValidationError):
        FUNCTION.start_line = 1  # type: ignore[misc]


def test_verdict_defaults():
    v = Verdict(
        status="pass",
        base="b",
        head="WORKTREE",
        rule_score=None,
        diff_score=None,
        findings=[],
        duration_s=0.0,
    )
    assert v.schema_version == SCHEMA_VERSION == "1"
    assert Finding(check="rules", kind="k", severity="info", audience="human", message="m").id == ""


def test_mutant_status_values():
    assert MutantStatus("caught_by_type_check") is MutantStatus.TYPE_CHECK
    assert {s.value for s in MutantStatus} == {
        "killed",
        "survived",
        "no_tests",
        "timeout",
        "suspicious",
        "skipped",
        "caught_by_type_check",
        "not_checked",
        "segfault",
        "interrupted",
    }


def test_rules_file_reads_toml_rule_tables():
    import tomllib

    data = tomllib.loads(
        'version = 1\n[[rule]]\nid = "A-001"\nstatement = "s"\n'
        '[[rule]]\nid = "B-002"\nstatement = "t"\nstatus = "retired"\n'
    )
    rf = RulesFile.model_validate(data)
    assert [r.id for r in rf.rules] == ["A-001", "B-002"]
    assert rf.rules[1].status == "retired"
    assert rf.rules[0].status == "active"


def test_rule_id_must_match_pattern():
    with pytest.raises(pydantic.ValidationError):
        Rule(id="ship-3", statement="s")


def test_schema_file_matches_the_models():
    assert json.loads(SCHEMA_FILE.read_text(encoding="utf-8")) == verdict_json_schema(), (
        "run `uv run python scripts/export_schema.py`"
    )
