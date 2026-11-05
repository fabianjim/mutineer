"""``[tool.mutineer]`` in the project's ``pyproject.toml``."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mutineer.errors import ConfigError


class Config(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    base: str | None = None
    """Base branch for the diff. None: ``main`` if it exists, else ``master``."""
    rules_file: str = ".mutineer/rules.toml"
    """Relative to the project root."""
    test_paths: list[str] = Field(default_factory=lambda: ["tests"])
    max_rounds: int = Field(default=3, ge=1)
    """``recheck`` round cap."""


def load_config(root: Path) -> Config:
    """Read ``[tool.mutineer]`` from ``root/pyproject.toml``; defaults if absent."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"pyproject.toml is not valid TOML ({exc}); fix the file") from exc
    table = data.get("tool", {}).get("mutineer", {})
    if not isinstance(table, dict):
        raise ConfigError("[tool.mutineer] in pyproject.toml must be a table")
    try:
        return Config.model_validate(table)
    except ValidationError as exc:
        raise ConfigError(f"[tool.mutineer] in pyproject.toml: {_first_error(exc)}") from exc


def _first_error(exc: ValidationError) -> str:
    err = exc.errors()[0]
    where = ".".join(str(p) for p in err["loc"]) or "table"
    if err["type"] == "extra_forbidden":
        known = ", ".join(Config.model_fields)
        return f"unknown key '{where}'; known keys: {known}"
    return f"'{where}': {err['msg']}"
