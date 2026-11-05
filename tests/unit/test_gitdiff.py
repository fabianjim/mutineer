from __future__ import annotations

from pathlib import Path

import pytest

from mutineer.errors import ConfigError
from mutineer.gitdiff import load_diff, merge_base, resolve_base_ref
from mutineer.models import ChangedFile, Hunk

RULES = ".mutineer/rules.toml"


def by_path(files: list[ChangedFile]) -> dict[str, ChangedFile]:
    return {f.path: f for f in files}


# ---- the shop fixture ----


def test_agent_branch_against_main(shop_repo):
    shop_repo.checkout("agent")
    diff = load_diff(shop_repo.path, "main", RULES)

    files = by_path(diff.files)
    assert set(files) == {"src/shop/pricing.py", "tests/test_pricing.py"}
    assert files["src/shop/pricing.py"].status == "modified"
    assert files["src/shop/pricing.py"].hunks == [Hunk(start=3, length=1), Hunk(start=9, length=6)]
    assert files["tests/test_pricing.py"].status == "modified"
    assert diff.head == "WORKTREE"
    assert diff.base == shop_repo.git("rev-parse", "main").strip()
    assert diff.rules_file_changed_ids == []


def test_default_base_is_main(shop_repo):
    shop_repo.checkout("agent")
    assert load_diff(shop_repo.path, None, RULES) == load_diff(shop_repo.path, "main", RULES)


def test_rules_file_changed_ids_on_approved_tamper(shop_repo):
    shop_repo.checkout("agent-tamper-approved")
    assert load_diff(shop_repo.path, "main", RULES).rules_file_changed_ids == ["SHIP-003"]


def test_rules_file_changed_ids_on_tamper(shop_repo):
    shop_repo.checkout("agent-tamper")
    diff = load_diff(shop_repo.path, "main", RULES)
    assert diff.rules_file_changed_ids == []
    assert "tests/rules/test_shipping_rules.py" in by_path(diff.files)


def test_main_against_itself_is_empty(shop_repo):
    shop_repo.checkout("main")
    assert load_diff(shop_repo.path, "main", RULES).files == []


def test_diff_runs_from_merge_base_not_base_tip(shop_repo):
    # main moves on after `agent` branched: its new commit must not show up.
    shop_repo.checkout("main")
    shop_repo.write("src/shop/later.py", "X = 1\n")
    shop_repo.commit("later on main")
    try:
        shop_repo.checkout("agent")
        diff = load_diff(shop_repo.path, "main", RULES)
        assert "src/shop/later.py" not in by_path(diff.files)
        assert diff.base == shop_repo.git("rev-parse", "main~1").strip()
    finally:
        shop_repo.git("branch", "-f", "main", "main~1")


# ---- temporary repos ----


@pytest.fixture
def base_repo(git_repo):
    git_repo.write("a.py", "one\ntwo\nthree\nfour\nfive\n")
    git_repo.write("gone.py", "x = 1\ny = 2\n")
    git_repo.write("old_name.py", "".join(f"line {i}\n" for i in range(20)))
    git_repo.write(".gitignore", "ignored.py\n")
    git_repo.commit("base")
    git_repo.git("checkout", "-q", "-b", "feature")
    return git_repo


def test_uncommitted_edit(base_repo):
    base_repo.write("a.py", "one\nTWO\nthree\nfour\nfive\n")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["a.py"] == ChangedFile(
        path="a.py", status="modified", hunks=[Hunk(start=2, length=1)]
    )


def test_untracked_new_file_is_added(base_repo):
    base_repo.write("pkg/new.py", "a = 1\nb = 2\nc = 3\n")
    base_repo.write("ignored.py", "nope\n")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["pkg/new.py"] == ChangedFile(
        path="pkg/new.py", status="added", hunks=[Hunk(start=1, length=3)]
    )
    assert "ignored.py" not in files


def test_committed_new_file_is_added(base_repo):
    base_repo.write("b.py", "a = 1\n")
    base_repo.commit("add b")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["b.py"].status == "added"
    assert files["b.py"].hunks == [Hunk(start=1, length=1)]


def test_deleted_file(base_repo):
    (base_repo.path / "gone.py").unlink()
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["gone.py"].status == "deleted"
    assert all(h.length == 0 for h in files["gone.py"].hunks)


def test_rename(base_repo):
    base_repo.git("mv", "old_name.py", "new_name.py")
    base_repo.commit("rename")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["new_name.py"].status == "renamed"
    assert files["new_name.py"].old_path == "old_name.py"
    assert files["new_name.py"].hunks == []
    assert "old_name.py" not in files


def test_rename_with_edit_staged_not_committed(base_repo):
    base_repo.git("mv", "old_name.py", "new_name.py")
    text = (base_repo.path / "new_name.py").read_text().replace("line 5\n", "line five\n")
    base_repo.write("new_name.py", text)
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["new_name.py"].status == "renamed"
    assert files["new_name.py"].old_path == "old_name.py"
    assert files["new_name.py"].hunks == [Hunk(start=6, length=1)]


def test_pure_deletion_hunk_points_after_deletion(base_repo):
    # Delete lines 2-3 ("two", "three"): head file is one/four/five.
    base_repo.write("a.py", "one\nfour\nfive\n")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    # git reports "+1,0" (the line before); the contract wants the line after: 2 ("four").
    assert files["a.py"].hunks == [Hunk(start=2, length=0)]


def test_deletion_at_top_of_file(base_repo):
    base_repo.write("a.py", "three\nfour\nfive\n")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["a.py"].hunks == [Hunk(start=1, length=0)]


def test_mixed_hunks_in_order(base_repo):
    base_repo.write("a.py", "zero\none\ntwo\nfour\nFIVE\nsix\n")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["a.py"].hunks == [
        Hunk(start=1, length=1),  # "zero" inserted
        Hunk(start=4, length=0),  # "three" deleted; "four" is now line 4
        Hunk(start=5, length=2),  # "five" -> "FIVE", "six" added
    ]


def test_mutineer_output_is_not_part_of_the_diff(base_repo):
    base_repo.write("mutants/src/a.py", "mutated\n")
    base_repo.write(".mutineer/last-run.json", "{}\n")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert not any(p.startswith("mutants/") for p in files)
    assert ".mutineer/last-run.json" not in files


def test_paths_with_spaces_and_unicode(base_repo):
    base_repo.write("dir with space/naïve ǁ.py", "a = 1\n")
    base_repo.commit("odd names")
    base_repo.write("untracked é.py", "b = 1\n")
    files = by_path(load_diff(base_repo.path, "main", RULES).files)
    assert files["dir with space/naïve ǁ.py"].status == "added"
    assert files["untracked é.py"].status == "added"


def test_paths_are_relative_to_a_subdirectory_root(git_repo):
    git_repo.write("proj/src/m.py", "a = 1\n")
    git_repo.write("other/x.py", "a = 1\n")
    git_repo.commit("base")
    git_repo.git("checkout", "-q", "-b", "feature")
    git_repo.write("proj/src/m.py", "a = 2\n")
    git_repo.write("other/x.py", "a = 2\n")
    git_repo.write("proj/.mutineer/rules.toml", '[[rule]]\nid = "A-001"\nstatement = "s"\n')
    diff = load_diff(git_repo.path / "proj", "main", RULES)
    assert {f.path for f in diff.files} == {"src/m.py", ".mutineer/rules.toml"}
    assert diff.rules_file_changed_ids == ["A-001"]


# ---- rules_file_changed_ids ----

RULE_A = '[[rule]]\nid = "A-001"\nstatement = "a"\n'
RULE_B = '[[rule]]\nid = "B-001"\nstatement = "b"\n'


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (RULE_A + RULE_B, RULE_A + RULE_B, []),
        (RULE_A + RULE_B, RULE_A + RULE_B.replace('"b"', '"bee"'), ["B-001"]),
        (RULE_A, RULE_A + RULE_B, ["B-001"]),
        (RULE_A + RULE_B, RULE_A, ["B-001"]),
        (RULE_A, RULE_A.replace('"a"\n', '"a"\nstatus = "retired"\n'), ["A-001"]),
        (RULE_A, RULE_A.replace('"a"\n', '"a"\nstatus = "active"\n'), []),  # default made explicit
        (RULE_B + RULE_A, RULE_A + RULE_B, []),  # reordered
        (None, RULE_A, ["A-001"]),
        (RULE_A, None, ["A-001"]),
    ],
)
def test_rules_file_changed_ids(git_repo, before, after, expected):
    if before is not None:
        git_repo.write(RULES, before)
    git_repo.commit("base")
    git_repo.git("checkout", "-q", "-b", "feature")
    path = git_repo.path / RULES
    if after is None:
        path.unlink(missing_ok=True)
    else:
        git_repo.write(RULES, after)
    assert load_diff(git_repo.path, "main", RULES).rules_file_changed_ids == expected


# ---- base resolution ----


def test_master_is_the_fallback_base(git_repo):
    git_repo.git("checkout", "-q", "-b", "master")
    git_repo.write("a.py", "a = 1\n")
    sha = git_repo.commit("base")
    git_repo.git("checkout", "-q", "-b", "feature")
    assert resolve_base_ref(git_repo.path, None) == "master"
    assert merge_base(git_repo.path, None) == sha


def test_explicit_base_wins(git_repo):
    git_repo.write("a.py", "a = 1\n")
    git_repo.commit("base")
    git_repo.git("branch", "develop")
    assert resolve_base_ref(git_repo.path, "develop") == "develop"


def test_missing_base_is_a_config_error(git_repo):
    git_repo.write("a.py", "a = 1\n")
    git_repo.commit("base")
    with pytest.raises(ConfigError, match="--base"):
        load_diff(git_repo.path, "nope", RULES)


def test_no_main_or_master_is_a_config_error(git_repo):
    git_repo.git("checkout", "-q", "-b", "trunk")
    git_repo.write("a.py", "a = 1\n")
    git_repo.commit("base")
    with pytest.raises(ConfigError, match="--base"):
        load_diff(git_repo.path, None, RULES)


def test_not_a_repo_is_a_config_error(tmp_path: Path):
    with pytest.raises(ConfigError, match="git"):
        load_diff(tmp_path, "main", RULES)
