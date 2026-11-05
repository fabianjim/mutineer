"""The rules file (``.mutineer/rules.toml``).

WP0 wrote ``load_rules``; WP2 owns the rest of this module.
"""

from __future__ import annotations

import tomllib
from collections import Counter

from pydantic import ValidationError

from mutineer.errors import RulesError
from mutineer.models import RulesFile


def load_rules(text: str) -> RulesFile:
    """Parse and validate the text of a rules file. Raises ``RulesError``."""
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise RulesError(f"rules file is not valid TOML ({exc}); fix the syntax") from exc
    if "rules" in data:
        # RulesFile accepts `rules` by field name (for Python and JSON); TOML must say [[rule]].
        raise RulesError("rules file: unknown key 'rules'; write each rule as a [[rule]] table")
    try:
        rules_file = RulesFile.model_validate(data)
    except ValidationError as exc:
        raise RulesError(f"rules file: {_describe(exc, data)}") from exc
    dupes = sorted(i for i, n in Counter(r.id for r in rules_file.rules).items() if n > 1)
    if dupes:
        raise RulesError(f"rules file: duplicate rule id {', '.join(dupes)}; ids must be unique")
    return rules_file


def _describe(exc: ValidationError, data: object) -> str:
    """One line naming the first problem and how to fix it."""
    err = exc.errors()[0]
    loc = list(err["loc"])
    where = "top level"
    if len(loc) >= 2 and loc[0] == "rule" and isinstance(loc[1], int):
        where = f"[[rule]] #{loc[1] + 1}{_rule_label(data, loc[1])}"
        loc = loc[2:]
    field = ".".join(str(p) for p in loc)
    kind = err["type"]
    if kind == "missing":
        return f"{where}: missing required key '{field}'"
    if kind == "extra_forbidden":
        return f"{where}: unknown key '{field}'"
    if field == "id" and kind == "string_pattern_mismatch":
        return (
            f"{where}: id {err['input']!r} must look like SHIP-003 "
            "(uppercase prefix, dash, 3+ digits)"
        )
    if field == "version":
        return f"{where}: version must be 1, got {err['input']!r}"
    return f"{where}: '{field}': {err['msg']}"


def _rule_label(data: object, index: int) -> str:
    try:
        rule_id = data["rule"][index]["id"]  # type: ignore[index]
    except (KeyError, IndexError, TypeError):
        return ""
    return f" ({rule_id})" if isinstance(rule_id, str) else ""
