"""``rules.collect_rule_map``: runtime rule identity, as mutmut will see it"""

from __future__ import annotations

import subprocess
import textwrap

import pytest

from mutineer.errors import CollectionError, ConfigError, RulesError
from mutineer.rules import collect, collect_rule_map

SHIP_003_TESTS = {
    "tests/rules/test_shipping_rules.py::test_small_order_pays": ["SHIP-003"],
    "tests/rules/test_shipping_rules.py::test_well_over_threshold_ships_free": ["SHIP-003"],
}


def _project(tmp_path, files):
    for rel, text in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text))
    return tmp_path


def test_fixture_main_maps_exactly_the_two_ship_003_tests(shop_clone):
    assert collect_rule_map(shop_clone("main")) == SHIP_003_TESTS


def test_fixture_agent_gap_ignores_the_unmarked_express_test(shop_clone):
    assert collect_rule_map(shop_clone("agent-gap")) == SHIP_003_TESTS


def test_copies_under_mutants_are_ignored(shop_clone):
    root = shop_clone("main")
    copy = root / "mutants" / "tests" / "rules"
    copy.mkdir(parents=True)
    (copy / "test_shipping_rules.py").write_text(
        (root / "tests/rules/test_shipping_rules.py").read_text()
    )
    assert collect_rule_map(root) == SHIP_003_TESTS


def test_collection_leaves_the_worktree_untouched(shop_clone):
    root = shop_clone("main")

    # without __pycache__/ ignored any bytecode written would show up
    (root / ".gitignore").write_text("mutants/\n")
    # explicit identity: CI runners have no global git config
    subprocess.run(
        ["git", "-c", "user.name=test", "-c", "user.email=test@example.com", "commit", "-qam", "x"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    before = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"], cwd=root, capture_output=True, text=True
    ).stdout
    collect_rule_map(root)
    after = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"], cwd=root, capture_output=True, text=True
    ).stdout
    assert after == before


def test_user_m_filter_in_addopts_does_not_hide_rule_tests(tmp_path):
    root = _project(
        tmp_path,
        {
            "pyproject.toml": """
                [tool.pytest.ini_options]
                addopts = ["-m", "not slow"]
                markers = ["slow: slow tests"]
            """,
            "tests/test_x.py": """
                import pytest

                @pytest.mark.slow
                @pytest.mark.rule("X-001")
                def test_slow_rule():
                    pass

                @pytest.mark.rule("X-002")
                def test_fast_rule():
                    pass
            """,
        },
    )
    assert collect_rule_map(root) == {
        "tests/test_x.py::test_slow_rule": ["X-001"],
        "tests/test_x.py::test_fast_rule": ["X-002"],
    }


def test_mutmut_test_selection_args_are_used(tmp_path):
    root = _project(
        tmp_path,
        {
            "pyproject.toml": """
                [tool.mutmut]
                pytest_add_cli_args_test_selection = ["tests/unit"]
            """,
            "tests/unit/test_u.py": """
                import pytest

                @pytest.mark.rule("U-001")
                def test_u():
                    pass
            """,
            "tests/e2e/test_e.py": """
                import pytest

                @pytest.mark.rule("E-001")
                def test_e():
                    pass
            """,
        },
    )
    assert collect_rule_map(root) == {"tests/unit/test_u.py::test_u": ["U-001"]}


def test_mutmut_extra_pytest_args_are_passed(tmp_path):
    root = _project(
        tmp_path,
        {
            "pyproject.toml": """
                [tool.mutmut]
                pytest_add_cli_args = ["-p", "needed_plugin"]
            """,
            "needed_plugin.py": """
                def pytest_addoption(parser):
                    parser.addini("x", "x")
            """,
            "conftest.py": "import sys, os; sys.path.insert(0, os.path.dirname(__file__))\n",
            "test_a.py": """
                import pytest

                @pytest.mark.rule("A-001")
                def test_a(request):
                    assert request.config.pluginmanager.has_plugin("needed_plugin")
            """,
        },
    )
    # collection fails if the -p plugin can't load, so success shows the args were passed
    assert collect_rule_map(root) == {"test_a.py::test_a": ["A-001"]}


def test_parametrized_tests_appear_once_per_case(tmp_path):
    root = _project(
        tmp_path,
        {
            "test_p.py": """
                import pytest

                @pytest.mark.rule("P-001")
                @pytest.mark.parametrize("x", ["a", "b"])
                def test_p(x):
                    pass
            """
        },
    )
    assert collect_rule_map(root) == {
        "test_p.py::test_p[a]": ["P-001"],
        "test_p.py::test_p[b]": ["P-001"],
    }


def test_no_tests_gives_an_empty_map(tmp_path):
    assert collect_rule_map(tmp_path) == {}


def test_plugin_loads_even_with_autoload_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")
    root = _project(
        tmp_path,
        {"test_a.py": "import pytest\n\n@pytest.mark.rule('A-001')\ndef test_a():\n    pass\n"},
    )
    assert collect_rule_map(root) == {"test_a.py::test_a": ["A-001"]}


def test_collection_error_raises_rules_error(tmp_path):
    root = _project(tmp_path, {"test_broken.py": "import does_not_exist_anywhere\n"})
    with pytest.raises(RulesError, match=r"could not collect.*fix collection"):
        collect_rule_map(root)


def test_bad_mutmut_args_are_a_config_error(tmp_path):
    root = _project(
        tmp_path,
        {"pyproject.toml": '[tool.mutmut]\npytest_add_cli_args_test_selection = "tests"\n'},
    )
    with pytest.raises(ConfigError, match="must be a list of strings"):
        collect_rule_map(root)


def test_collection_writes_no_bytecode_whatever_the_callers_environment(tmp_path, monkeypatch):
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    root = _project(
        tmp_path,
        {
            "helper.py": "VALUE = 1\n",
            "tests/test_a.py": "import helper\n\ndef test_a():\n    assert helper.VALUE\n",
            "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["."]\n',
        },
    )
    collect(root)
    assert list(root.rglob("__pycache__")) == []


def test_collection_runs_in_the_callers_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MUTINEER_TEST_SETTING", "on")
    root = _project(
        tmp_path,
        {
            "conftest.py": """
                import os

                if os.environ.get("MUTINEER_TEST_SETTING") != "on":
                    raise ImportError("the project's setting is missing")
            """,
            "test_a.py": "import pytest\n\n@pytest.mark.rule('A-001')\ndef test_a():\n    pass\n",
        },
    )
    assert collect(root).rule_map == {"test_a.py::test_a": ["A-001"]}


def test_a_pytest_usage_error_is_a_rules_error_naming_the_exit(tmp_path):
    root = _project(
        tmp_path,
        {"pyproject.toml": '[tool.mutmut]\npytest_add_cli_args = ["--no-such-option"]\n'},
    )
    with pytest.raises(RulesError) as exc:
        collect(root)
    assert not isinstance(exc.value, CollectionError)
    assert str(exc.value).startswith("pytest could not collect the tests (exit 4): ")


def test_missing_plugin_output_says_how_to_fix_it(tmp_path):
    # a later option wins, so the collected ids go elsewhere and only the rule map is written
    root = _project(
        tmp_path,
        {
            "pyproject.toml": (
                '[tool.mutmut]\npytest_add_cli_args = ["--mutineer-collected=elsewhere.json"]\n'
            ),
            "test_a.py": "def test_a():\n    pass\n",
        },
    )
    with pytest.raises(RulesError) as exc:
        collect(root)
    assert str(exc.value) == (
        "pytest ran but Mutineer's pytest plugin did not write the rule map; "
        "install mutineer in the project's environment and don't block "
        "the plugin with -p no:mutineer"
    )


@pytest.mark.parametrize(
    ("written", "message"),
    [
        ("not json", r"^the collected test list from pytest is not valid JSON \(.+\)$"),
        ('"test_a.py::test_a"', r"^the collected test list from pytest has an unexpected shape$"),
        ("[1]", r"^the collected test list from pytest has an unexpected shape$"),
    ],
)
def test_a_corrupt_collected_list_is_a_rules_error(tmp_path, written, message):
    root = _project(
        tmp_path,
        {
            "conftest.py": f"""
                from pathlib import Path


                def pytest_sessionfinish(session):
                    path = session.config.getoption("--mutineer-collected")
                    Path(path).write_text({written!r})
            """,
            "test_a.py": "def test_a():\n    pass\n",
        },
    )
    with pytest.raises(RulesError, match=message):
        collect(root)
