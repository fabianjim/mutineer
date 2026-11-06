"""``collect_rule_map`` reads mutmut's test selection from ``setup.cfg`` as mutmut does"""

from __future__ import annotations

from pathlib import Path

from mutineer.config import Config
from mutineer.rules import collect_rule_map

MARKED = 'import pytest\n\n@pytest.mark.rule("A-001")\ndef test_{name}():\n    pass\n'


def two_test_dirs(root: Path) -> Path:
    for name in ("kept", "other"):
        (root / name).mkdir()
        (root / name / f"test_{name}.py").write_text(MARKED.format(name=name))
    return root


def test_setup_cfg_selection_is_used_without_a_tool_mutmut_table(tmp_path: Path) -> None:
    root = two_test_dirs(tmp_path)
    (root / "pyproject.toml").write_text('[project]\nname = "x"\n')
    (root / "setup.cfg").write_text(
        "[mutmut]\npytest_add_cli_args_test_selection =\n    kept\n"
        "pytest_add_cli_args = -p\n    no:randomly\n"
    )
    assert collect_rule_map(root, Config()) == {"kept/test_kept.py::test_kept": ["A-001"]}


def test_tool_mutmut_wins_over_setup_cfg(tmp_path: Path) -> None:
    root = two_test_dirs(tmp_path)
    (root / "pyproject.toml").write_text(
        '[tool.mutmut]\npytest_add_cli_args_test_selection = ["other"]\n'
    )
    (root / "setup.cfg").write_text("[mutmut]\npytest_add_cli_args_test_selection = kept\n")
    assert collect_rule_map(root, Config()) == {"other/test_other.py::test_other": ["A-001"]}
