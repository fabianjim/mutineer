"""``collect_rule_map`` tells a broken suite (``CollectionError``) from a setup problem"""

from __future__ import annotations

from pathlib import Path

import pytest

from mutineer.config import Config
from mutineer.errors import CollectionError, RulesError
from mutineer.rules import collect_rule_map

OK_TEST = "def test_ok():\n    pass\n"


def project(root: Path, files: dict[str, str], mutmut_args: str = "") -> Path:
    (root / "pyproject.toml").write_text(f"[tool.mutmut]\n{mutmut_args}")
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text)
    return root


@pytest.mark.parametrize(
    ("files", "needle"),
    [
        ({"test_a.py": "def test_a(:\n    pass\n"}, "SyntaxError"),
        ({"test_a.py": "import no_such_module_xyz\n"}, "no_such_module_xyz"),
        ({"conftest.py": "import no_such_module_xyz\n", "test_a.py": OK_TEST}, "conftest"),
        (
            {
                "pytest.ini": "[pytest]\naddopts = --strict-markers\n",
                "test_a.py": "import pytest\n\n@pytest.mark.zzz\ndef test_a():\n    pass\n",
            },
            "zzz",
        ),
    ],
    ids=["syntax_error", "import_error", "broken_conftest", "unknown_marker"],
)
def test_a_broken_suite_is_a_collection_error(
    tmp_path: Path, files: dict[str, str], needle: str
) -> None:
    root = project(tmp_path, files)
    with pytest.raises(CollectionError) as info:
        collect_rule_map(root, Config())
    assert needle in info.value.output
    assert "fix collection" in str(info.value)


@pytest.mark.parametrize(
    "mutmut_args",
    [
        'pytest_add_cli_args = ["--no-such-option"]\n',
        'pytest_add_cli_args = ["-p", "no_such_plugin_xyz"]\n',
    ],
    ids=["unknown_option", "missing_plugin"],
)
def test_a_setup_problem_is_not_a_collection_error(tmp_path: Path, mutmut_args: str) -> None:
    root = project(tmp_path, {"test_a.py": OK_TEST}, mutmut_args)
    with pytest.raises(RulesError) as info:
        collect_rule_map(root, Config())
    assert not isinstance(info.value, CollectionError)
