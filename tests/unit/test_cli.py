from __future__ import annotations

import pytest

from mutineer import __version__
from mutineer.cli import main
from mutineer.errors import ConfigError, EngineError, MutineerError, NeedsHumanError, RulesError


def test_help_lists_every_command(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    for command in ("run", "recheck", "rules", "init"):
        assert command in out


def test_no_arguments_shows_help(capsys):
    assert main([]) == 0
    assert "run" in capsys.readouterr().out


def test_version(capsys):
    assert main(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["bogus"],
        ["run", "--format", "nope"],
        ["run", "--base"],
        ["run", "--no-such-flag"],
        ["recheck", "--max-rounds", "zero"],
        ["rules", "bogus"],
    ],
)
def test_usage_errors_exit_4(argv):
    assert main(argv) == 4


@pytest.mark.parametrize(
    "argv",
    [
        ["run"],
        ["run", "--base", "main", "--format", "json", "--no-mutation", "--strict"],
        ["recheck", "--max-rounds", "2"],
    ],
)
def test_stubs_exit_4_with_a_message(argv, capsys):
    assert main(argv) == 4
    assert "not implemented yet" in capsys.readouterr().err


def test_error_exit_codes():
    assert MutineerError.exit_code == 4
    for cls in (ConfigError, RulesError, EngineError):
        assert issubclass(cls, MutineerError)
        assert cls.exit_code == 4
    assert issubclass(NeedsHumanError, MutineerError)
    assert NeedsHumanError.exit_code == 3


def test_mutineer_errors_set_the_exit_code(monkeypatch, capsys):
    import mutineer.cli as cli

    def boom(*args, **kwargs):
        raise NeedsHumanError("round cap reached; a human needs to look")

    command = cli.typer.main.get_command(cli.app)
    monkeypatch.setattr(type(command), "main", boom)
    assert main(["recheck"]) == 3
    assert "round cap reached" in capsys.readouterr().err
