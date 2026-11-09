"""``ledger``: the proposals file, id allocation and proposal validation"""

from __future__ import annotations

import tomllib

import pytest

from mutineer.errors import RulesError
from mutineer.ledger import (
    PROPOSALS_FILE,
    Request,
    add_proposals,
    load_proposals,
    next_id,
    remove_proposals,
    save_proposals,
    strip_params,
    toml_str,
    validate,
)
from mutineer.models import Proposal, ProposalsFile, Rule, RulesFile
from mutineer.rules import Collected

RULES = RulesFile(
    rules=[
        Rule(id="SHIP-003", statement="Orders of $50 or more ship free"),
        Rule(id="OLD-001", statement="gone", status="retired"),
    ]
)

TEST_PRICING = """\
import pytest


def test_member_discount():
    pass


@pytest.mark.parametrize("n", [1, 2])
def test_cases(n):
    pass


@pytest.mark.rule("SHIP-003")
def test_ship():
    pass


class TestCart:
    def test_add(self):
        pass
"""

COLLECTED = Collected(
    rule_map={
        "tests/test_pricing.py::test_ship": ["SHIP-003"],
        "tests/test_other.py::test_stray": ["STRAY-001"],
    },
    node_ids=[
        "tests/test_dynamic.py::test_generated",
        "tests/test_other.py::test_stray",
        "tests/test_pricing.py::TestCart::test_add",
        "tests/test_pricing.py::test_cases[1]",
        "tests/test_pricing.py::test_cases[2]",
        "tests/test_pricing.py::test_member_discount",
        "tests/test_pricing.py::test_ship",
    ],
)


@pytest.fixture
def project(tmp_path):
    (tmp_path / "src/shop").mkdir(parents=True)
    (tmp_path / "src/shop/__init__.py").write_text("")
    (tmp_path / "src/shop/pricing.py").write_text("def discount(total):\n    return total\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_pricing.py").write_text(TEST_PRICING)
    (tmp_path / "tests/test_dynamic.py").write_text("globals()['test_generated'] = lambda: None\n")
    return tmp_path


def _validate(project, *proposals, rules=RULES, collected=COLLECTED):
    return validate(list(proposals), rules=rules, collected=collected, root=project)


def _add(project, *requests, existing=None, rules=RULES):
    return add_proposals(
        list(requests),
        existing=existing or ProposalsFile(),
        rules=rules,
        collected=COLLECTED,
        root=project,
    )


# ---- next_id ----


def test_next_id_starts_at_001():
    assert next_id("PRICE", []) == "PRICE-001"


def test_next_id_is_one_more_than_the_highest_skipping_gaps():
    assert next_id("PRICE", ["PRICE-001", "PRICE-005", "SHIP-009"]) == "PRICE-006"


def test_next_id_counts_retired_and_marker_ids_given_as_taken():
    retired = [r.id for r in RULES.rules if r.status == "retired"]
    markers = ["OLD-004"]
    assert next_id("OLD", retired + markers) == "OLD-005"


def test_next_id_ignores_other_prefixes_that_share_a_start():
    assert next_id("PRICE", ["PRICES-010", "XPRICE-020"]) == "PRICE-001"


def test_next_id_keeps_three_digits_and_grows_past_them():
    assert next_id("A", ["A-009"]) == "A-010"
    assert next_id("A", ["A-999"]) == "A-1000"


def test_add_proposals_allocates_past_retired_marker_and_pending_ids(project):
    rules = RulesFile(rules=[Rule(id="STRAY-002", statement="x", status="retired")])
    existing = ProposalsFile(proposals=[Proposal(id="STRAY-003", statement="y")])
    _, added, problems = _add(
        project, Request(prefix="STRAY", statement="z"), existing=existing, rules=rules
    )
    assert problems == []
    assert [a.id for a in added] == ["STRAY-004"]


# ---- toml_str and strip_params ----


@pytest.mark.parametrize(
    "value",
    [
        "plain",
        'say "hi"',
        "back\\slash and \\n literally",
        "unicode: café, 日本, emoji 🎉",
        "controls: \x00 \x01 \t \n \r \x1f \x7f end",
        "",
    ],
)
def test_toml_str_round_trips_through_tomllib(value):
    assert tomllib.loads(f"v = {toml_str(value)}")["v"] == value


@pytest.mark.parametrize(
    ("node_id", "stripped"),
    [
        ("tests/t.py::test_a", "tests/t.py::test_a"),
        ("tests/t.py::test_a[1-2]", "tests/t.py::test_a"),
        ("tests/t.py::TestX::test_a[a[b]]", "tests/t.py::TestX::test_a"),
        ("tests/t[1].py::test_a", "tests/t[1].py::test_a"),
    ],
)
def test_strip_params(node_id, stripped):
    assert strip_params(node_id) == stripped


# ---- the file ----


def test_load_proposals_missing_file_is_empty(tmp_path):
    assert load_proposals(tmp_path) == ProposalsFile()


def test_save_then_load_round_trips_with_header(tmp_path):
    proposals = ProposalsFile(
        proposals=[
            Proposal(id="PRICE-001", statement='Members "get" 10%', seam="shop.pricing.discount"),
            Proposal(id="SHIP-003", tests=["tests/a.py::test_b", "tests/a.py::test_c"]),
        ]
    )
    save_proposals(tmp_path, proposals)
    text = (tmp_path / PROPOSALS_FILE).read_text()
    assert text.startswith("# Rules proposed with `mutineer rules propose`.")
    assert "[[proposal]]" in text
    assert load_proposals(tmp_path) == proposals
    assert list(tmp_path.joinpath(".mutineer").iterdir()) == [tmp_path / PROPOSALS_FILE]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("version = [", "is not valid TOML"),
        ("version = 2\n", "version must be 1"),
        ('[[proposal]]\nid = "bad"\n', "must look like SHIP-003"),
        ('[[proposal]]\nid = "A-001"\ncolour = "red"\n', "unknown key 'colour'"),
        ('[[proposals]]\nid = "A-001"\n', "[[proposal]] table"),
        ('[[proposal]]\nid = "A-001"\n[[proposal]]\nid = "A-001"\n', "duplicate proposal id"),
    ],
)
def test_load_proposals_on_an_invalid_file_raises_one_line(tmp_path, text, message):
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / PROPOSALS_FILE).write_text(text)
    with pytest.raises(RulesError) as exc:
        load_proposals(tmp_path)
    assert message in str(exc.value)
    assert "\n" not in str(exc.value)


def test_load_proposals_names_the_bad_table(tmp_path):
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / PROPOSALS_FILE).write_text(
        '[[proposal]]\nid = "A-001"\n[[proposal]]\nid = "B-001"\ntests = "x"\n'
    )
    with pytest.raises(RulesError, match=r"\[\[proposal\]\] #2 \(B-001\)"):
        load_proposals(tmp_path)


# ---- validate ----


def test_valid_new_and_existing_rule_proposals_have_no_problems(project):
    assert (
        _validate(
            project,
            Proposal(
                id="PRICE-001",
                statement="Members get 10% off",
                seam="shop.pricing.discount",
                tests=["tests/test_pricing.py::test_member_discount"],
            ),
            Proposal(
                id="SHIP-003",
                tests=[
                    "tests/test_pricing.py::test_cases",
                    "tests/test_pricing.py::TestCart::test_add",
                ],
            ),
        )
        == []
    )


def test_a_single_parametrized_case_is_accepted(project):
    p = Proposal(id="SHIP-003", tests=["tests/test_pricing.py::test_cases[2]"])
    assert _validate(project, p) == []


@pytest.mark.parametrize(
    ("proposal", "problem"),
    [
        (Proposal(id="SHIP-003", statement="x"), "SHIP-003 is already in the rules file"),
        (Proposal(id="OLD-001", statement="x"), "OLD-001 is already in the rules file"),
        (Proposal(id="STRAY-001", statement="x"), "tests are already marked with STRAY-001"),
        (Proposal(id="NEW-001", statement="  "), "the statement is empty"),
        (Proposal(id="NEW-001", statement="a\nb"), "the statement must be one line"),
        (Proposal(id="NEW-001", statement="x", seam="shop.pricing.nope"), "'nope' not found"),
        (Proposal(id="NOPE-001", tests=["tests/test_pricing.py::test_ship"]), "not in the rules"),
        (Proposal(id="OLD-001", tests=["tests/test_pricing.py::test_ship"]), "OLD-001 is retired"),
        (
            Proposal(id="SHIP-003", seam="shop.pricing.discount", tests=["x"]),
            "can't set a seam",
        ),
        (Proposal(id="SHIP-003"), "give at least one test"),
        (
            Proposal(id="SHIP-003", tests=["tests/test_pricing.py::test_missing"]),
            "pytest doesn't collect it",
        ),
        (
            Proposal(id="SHIP-003", tests=["tests/test_dynamic.py::test_generated"]),
            "not found as a test function",
        ),
        (
            Proposal(id="SHIP-003", tests=["tests/test_pricing.py::test_ship"]),
            "already marked with SHIP-003",
        ),
    ],
)
def test_each_validation_problem(project, proposal, problem):
    problems = _validate(project, proposal)
    assert any(problem in p for p in problems), problems
    assert all(p.startswith(f"{proposal.id}: ") for p in problems)


def test_bad_id_is_reported(project):
    bad = Proposal.model_construct(id="bad", statement="x", seam=None, tests=[])
    assert _validate(project, bad) == [
        "bad: id 'bad' must look like SHIP-003 (uppercase prefix, dash, 3+ digits)"
    ]


def test_duplicate_ids_are_reported(project):
    p = Proposal(id="NEW-001", statement="x")
    assert "NEW-001: proposed more than once" in _validate(project, p, p)[0]


def test_every_problem_is_reported_not_just_the_first(project):
    p = Proposal(
        id="SHIP-003",
        statement="a\nb",
        seam="shop.nope",
        tests=["tests/test_pricing.py::test_missing"],
    )
    assert len(_validate(project, p)) == 4


def test_a_new_rule_id_already_pending_is_a_problem(project):
    existing = ProposalsFile(proposals=[Proposal(id="NEW-001", statement="x")])
    _, _, problems = _add(project, Request(id="NEW-001", statement="y"), existing=existing)
    assert problems == [(0, "NEW-001 is already proposed; choose another id")]


def test_validate_accepts_a_proposals_file(project):
    proposals = ProposalsFile(proposals=[Proposal(id="SHIP-003")])
    assert _validate(project, *proposals.proposals) == validate(
        proposals, rules=RULES, collected=COLLECTED, root=project
    )


# ---- add_proposals ----


def test_prefix_requests_are_allocated_in_order(project):
    updated, added, problems = _add(
        project,
        Request(prefix="PRICE", statement="one"),
        Request(id="PRICE-002", statement="two"),
        Request(prefix="PRICE", statement="three"),
    )
    assert problems == []
    assert [a.id for a in added] == ["PRICE-001", "PRICE-002", "PRICE-003"]
    assert [p.id for p in updated.proposals] == ["PRICE-001", "PRICE-002", "PRICE-003"]


def test_an_explicit_id_after_an_allocated_one_collides(project):
    _, _, problems = _add(
        project, Request(prefix="PRICE", statement="one"), Request(id="PRICE-001", statement="two")
    )
    assert problems == [(1, "PRICE-001 is already proposed; choose another id")]


def test_batch_is_all_or_nothing_with_indexed_problems(project):
    existing = ProposalsFile(proposals=[Proposal(id="KEEP-001", statement="kept")])
    updated, added, problems = _add(
        project,
        Request(prefix="PRICE", statement="fine"),
        Request(prefix="price", statement="bad prefix"),
        Request(rule="SHIP-003", statement="no statement for a rule", tests=("x",)),
        Request(prefix="A", id="A-001", statement="two modes"),
        Request(id="NEW-001"),
        existing=existing,
    )
    assert updated == existing
    assert added == []
    assert {i for i, _ in problems} == {1, 2, 3, 4}
    assert (1, "prefix 'price' must be uppercase letters and digits, e.g. PRICE") in problems
    assert (2, "a proposal for an existing rule takes no statement") in problems
    assert (3, "give exactly one of prefix, id or rule") in problems
    assert (4, "a new rule needs a statement") in problems


def test_tests_for_an_existing_rule_are_merged_sorted(project):
    existing = ProposalsFile(
        proposals=[Proposal(id="SHIP-003", tests=["tests/test_pricing.py::test_member_discount"])]
    )
    updated, added, problems = _add(
        project,
        Request(rule="SHIP-003", tests=("tests/test_pricing.py::test_cases",)),
        Request(
            rule="SHIP-003",
            tests=(
                "tests/test_pricing.py::TestCart::test_add",
                "tests/test_pricing.py::test_member_discount",
            ),
        ),
        existing=existing,
    )
    assert problems == []
    assert updated.proposals == [
        Proposal(
            id="SHIP-003",
            tests=[
                "tests/test_pricing.py::TestCart::test_add",
                "tests/test_pricing.py::test_cases",
                "tests/test_pricing.py::test_member_discount",
            ],
        )
    ]
    assert [len(a.tests) for a in added] == [1, 2]


def test_statement_is_stored_stripped(project):
    updated, _, _ = _add(project, Request(id="NEW-001", statement="  promise  "))
    assert updated.proposals[0].statement == "promise"


# ---- remove_proposals ----


def test_remove_a_known_proposal():
    existing = ProposalsFile(
        proposals=[Proposal(id="A-001", statement="a"), Proposal(id="B-001", statement="b")]
    )
    assert remove_proposals(existing, ["A-001"]).proposals == [existing.proposals[1]]


def test_remove_an_unknown_id_raises_and_names_it():
    existing = ProposalsFile(proposals=[Proposal(id="A-001", statement="a")])
    with pytest.raises(RulesError, match="no pending proposal Z-001; nothing was removed"):
        remove_proposals(existing, ["A-001", "Z-001"])


def test_load_proposals_says_how_to_write_a_proposal_table(tmp_path):
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / PROPOSALS_FILE).write_text('[[proposals]]\nid = "A-001"\n')
    with pytest.raises(RulesError) as exc:
        load_proposals(tmp_path)
    assert str(exc.value).endswith("write each proposal as a [[proposal]] table")


def test_load_proposals_names_every_duplicate_id(tmp_path):
    (tmp_path / ".mutineer").mkdir()
    (tmp_path / PROPOSALS_FILE).write_text(
        '[[proposal]]\nid = "A-001"\n[[proposal]]\nid = "B-001"\n'
        '[[proposal]]\nid = "A-001"\n[[proposal]]\nid = "B-001"\n'
    )
    with pytest.raises(RulesError, match="duplicate proposal id A-001, B-001; ids must be unique"):
        load_proposals(tmp_path)
