"""The packaged Agent Skill: its frontmatter is valid and every command in it parses

The skill ships inside the package, so an agent reads the version that matches its CLI.
These tests keep it from drifting: a renamed option, a dropped command or a bad enum value
in SKILL.md fails here.
"""

from __future__ import annotations

import re
import shlex
from importlib.resources import files

import pytest
import typer
from typer.core import TyperGroup

from mutineer.cli import app

SKILL = files("mutineer").joinpath("skill", "SKILL.md")
NAME_PATTERN = r"[a-z0-9]+(-[a-z0-9]+)*"
SHELL_OPERATORS = frozenset({"|", "||", "&", "&&", ";", "<", ">", "<<", ">>", "(", ")"})


def _text() -> str:
    return SKILL.read_text(encoding="utf-8")


def _frontmatter(text: str) -> dict[str, str]:
    # the subset of YAML a skill header needs: one `key: plain value` per line
    lines = text.splitlines()
    assert lines[0] == "---", "SKILL.md must start with a frontmatter block"
    end = lines.index("---", 1)
    fields: dict[str, str] = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(": ")
        assert sep and re.fullmatch(r"[a-z][a-z0-9_-]*", key), f"not a `key: value` line: {line}"
        assert key not in fields, f"duplicate key {key}"
        value = value.strip()
        # keep values plain scalars, so any YAML parser reads them the same way
        assert value[:1] not in set("\"'[]{}>|&*!%@`#"), f"{key} must be a plain scalar"
        assert ": " not in value and " #" not in value, f"{key} would not parse as YAML"
        fields[key] = value
    return fields


def _commands(text: str) -> list[str]:
    """Every ``mutineer ...`` command line in fenced code blocks and inline code."""
    found: list[str] = []
    prose: list[str] = []
    fenced = False
    block: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            if fenced:
                # join backslash continuations before splitting into commands
                joined = "\n".join(block).replace("\\\n", " ")
                found += [c.strip() for c in joined.splitlines()]
                block = []
            fenced = not fenced
        elif fenced:
            block.append(line)
        else:
            prose.append(line)
    found += [" ".join(span.split()) for span in re.findall(r"`([^`]+)`", "\n".join(prose))]
    return [c for c in found if c.startswith("mutineer ")]


def _parse(command: str) -> None:
    """Parse ``command`` with the real CLI down to its leaf command, without running it."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    tokens = list(lexer)
    # the command ends at the first shell operator (a pipe, a redirection)
    cut = next((i for i, t in enumerate(tokens) if t in SHELL_OPERATORS), len(tokens))
    args = tokens[1:cut]
    cmd = typer.main.get_command(app)
    ctx = cmd.make_context("mutineer", args)
    while isinstance(cmd, TyperGroup):
        # what the group's invoke hands to the subcommand
        rest = [*ctx._protected_args, *ctx.args]
        if not rest:
            ctx.fail("Missing command.")
        name, sub, rest = cmd.resolve_command(ctx, rest)
        assert sub is not None and name is not None
        cmd = sub
        ctx = cmd.make_context(name, rest, parent=ctx)


def test_skill_is_readable_as_package_data():
    assert SKILL.is_file()
    assert "mutineer run --format agent" in _text()


def test_frontmatter_names_the_skill_and_says_when_to_use_it():
    fields = _frontmatter(_text())
    assert set(fields) == {"name", "description"}
    name = fields["name"]
    # the skill is installed as a folder named `mutineer`, and the name must match it
    assert name == "mutineer"
    assert re.fullmatch(NAME_PATTERN, name) and len(name) <= 64
    assert 0 < len(fields["description"]) <= 1024


@pytest.mark.parametrize("command", _commands(_text()))
def test_every_command_in_the_skill_parses(command):
    _parse(command)


def test_the_skill_names_commands():
    commands = _commands(_text())
    assert commands, "no `mutineer ...` command found in SKILL.md"
    for needed in ("mutineer run", "mutineer recheck", "mutineer dispute", "mutineer rules"):
        assert any(c.startswith(needed) for c in commands), needed


@pytest.mark.parametrize(
    "command",
    [
        "mutineer bogus",
        "mutineer run --format nope",
        "mutineer run --no-such-flag",
        "mutineer dispute ID",
        "mutineer rules bogus",
        "mutineer rules",
        "mutineer hook stop --agent nope",
    ],
)
def test_a_command_the_cli_would_refuse_fails_to_parse(command):
    with pytest.raises(typer.TyperException):
        _parse(command)


def test_extraction_reads_blocks_inline_code_and_continuations():
    text = (
        "Run `mutineer run --format agent` and `git status`.\n"
        "```console\n"
        "mutineer rules propose --prefix SHIP \\\n"
        "  --statement x  # a comment\n"
        "mutineer fresh\n"
        "```\n"
    )
    assert _commands(text) == [
        "mutineer rules propose --prefix SHIP    --statement x  # a comment",
        "mutineer fresh",
        "mutineer run --format agent",
    ]
