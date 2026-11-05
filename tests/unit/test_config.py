from __future__ import annotations

import pytest

from mutineer.config import Config, load_config
from mutineer.errors import ConfigError


def test_defaults_without_pyproject(tmp_path):
    assert load_config(tmp_path) == Config()
    cfg = Config()
    assert (cfg.base, cfg.rules_file, cfg.test_paths, cfg.max_rounds) == (
        None,
        ".mutineer/rules.toml",
        ["tests"],
        3,
    )


def test_defaults_without_tool_table(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\n')
    assert load_config(tmp_path) == Config()


def test_reads_tool_mutineer(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.mutineer]\nbase = "develop"\nrules_file = "rules.toml"\n'
        'test_paths = ["t1", "t2"]\nmax_rounds = 5\n'
    )
    assert load_config(tmp_path) == Config(
        base="develop", rules_file="rules.toml", test_paths=["t1", "t2"], max_rounds=5
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("bsae = 'main'", "unknown key 'bsae'"),
        ("max_rounds = 'three'", "max_rounds"),
        ("max_rounds = 0", "max_rounds"),
        ("test_paths = 'tests'", "test_paths"),
    ],
)
def test_invalid_config_is_a_config_error(tmp_path, body, message):
    (tmp_path / "pyproject.toml").write_text(f"[tool.mutineer]\n{body}\n")
    with pytest.raises(ConfigError, match=message) as info:
        load_config(tmp_path)
    assert "\n" not in str(info.value)


def test_broken_toml_is_a_config_error(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.mutineer\n")
    with pytest.raises(ConfigError, match="not valid TOML"):
        load_config(tmp_path)


def test_mutineers_own_config_loads():
    from pathlib import Path

    cfg = load_config(Path(__file__).parents[2])
    assert cfg.rules_file == ".mutineer/rules.toml"
