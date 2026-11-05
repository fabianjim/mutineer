"""Shared contracts (build spec section 5).

Every package builds on these. Changes are additive only: a new optional field, a new
enum member or a new model, logged in ``docs/handoff.md`` in the same commit.
Nothing here may be renamed, removed or retyped.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "1"


class _Model(BaseModel):
    """Base for every contract: immutable once built."""

    model_config = ConfigDict(frozen=True)


class Severity(StrEnum):
    BLOCK = "block"
    WARN = "warn"
    INFO = "info"


class Audience(StrEnum):
    AGENT = "agent"
    HUMAN = "human"


class Location(_Model):
    file: str
    """Repo-relative path, forward slashes."""
    line: int | None = None
    """1-based, head version (tamper findings for deleted tests use the base version)."""
    end_line: int | None = None


# ---- rules ----

RULE_ID_PATTERN = r"^[A-Z][A-Z0-9]*-[0-9]{3,}$"


class Rule(_Model, extra="forbid"):
    id: str = Field(pattern=RULE_ID_PATTERN)
    statement: str
    seam: str | None = None
    """Dotted path to the public entry point the rule is tested through."""
    status: Literal["active", "retired"] = "active"


class RulesFile(_Model, extra="forbid", populate_by_name=True):
    version: Literal[1] = 1
    rules: list[Rule] = Field(default_factory=list, alias="rule")
    """TOML ``[[rule]]`` tables."""


RuleMap = dict[str, list[str]]
"""pytest node id -> sorted rule ids."""


# ---- diff ----


class Hunk(_Model):
    start: int
    """Head-side first line, 1-based (from ``git diff -U0``).

    For a pure deletion (``length == 0``) this is the line after the deletion point
    (git's new_start + 1).
    """
    length: int
    """Head-side line count. 0 means a pure deletion."""


class ChangedFile(_Model):
    path: str
    """Head path."""
    old_path: str | None = None
    """Base path, for renames."""
    status: Literal["added", "modified", "deleted", "renamed"]
    hunks: list[Hunk]


class Diff(_Model):
    base: str
    """Merge-base sha."""
    head: str
    """Always ``"WORKTREE"`` in v0.1."""
    files: list[ChangedFile]
    rules_file_changed_ids: list[str] = []
    """Ids whose ``[[rule]]`` entry differs between base and head."""


# ---- engine ----


class MutantStatus(StrEnum):
    KILLED = "killed"
    SURVIVED = "survived"
    NO_TESTS = "no_tests"
    TIMEOUT = "timeout"
    SUSPICIOUS = "suspicious"
    SKIPPED = "skipped"
    TYPE_CHECK = "caught_by_type_check"
    NOT_CHECKED = "not_checked"
    SEGFAULT = "segfault"
    INTERRUPTED = "interrupted"


class FunctionRef(_Model):
    mutmut_name: str
    """``"shop.cart.xǁCartǁadd"`` (U+01C1) or ``"shop.pricing.x_discount"``."""
    file: str
    qualname: str
    """``"Cart.add"``."""
    start_line: int
    """The ``def`` line (decorators excluded)."""
    end_line: int


class MutantResult(_Model):
    name: str
    """Full mutmut mutant name."""
    function: str
    """``FunctionRef.mutmut_name``."""
    run: Literal["all", "rule"]
    status: MutantStatus
    line: int | None = None
    """Head file line of the mutated source line."""
    original: str | None = None
    """That line, stripped."""
    mutated: str | None = None


class UnmeasuredLocation(Location):
    reason: Literal[
        "decorated", "module_level", "outside_source_paths", "excluded_by_config", "no_mutants"
    ]
    """``no_mutants`` (added by WP3): a changed function mutmut generates no mutants for,
    such as one with nothing mutable in it, ``__new__``/``__setattr__``/``__getattribute__``,
    an enum's ``__init__``/``_generate_next_value_``, or a ``# pragma: no mutate``."""


class EngineRun(_Model):
    baseline_failed: bool
    baseline_output: str | None = None
    """Last 60 lines of test output."""
    functions: list[FunctionRef]
    """Changed, mutable functions."""
    unmeasured: list[UnmeasuredLocation]
    """Changed lines mutmut cannot mutate (build spec section 3 item 5)."""
    unreached: list[str] = []
    """Function names with no covering tests."""
    results: list[MutantResult]
    tests_by_function: dict[str, list[str]]
    """From the all pass."""
    duration_s: float


# ---- output ----


class Finding(_Model):
    id: str = ""
    """Assigned by ``verdict.decide``: ``"<check>-<nnnn>"``. Producers leave it empty."""
    check: Literal["tamper", "rules", "mutation"]
    kind: str
    """Stable kind string from the finding catalogue (build spec section 7)."""
    severity: Severity
    audience: Audience
    rules: list[str] = []
    location: Location | None = None
    message: str
    """One sentence, plain English."""
    evidence: dict[str, Any] = {}
    next_action: str | None = None


class Score(_Model):
    killed: int
    total: int
    """Per build spec section 3 item 9."""


class Verdict(_Model):
    schema_version: str = SCHEMA_VERSION
    status: Literal["pass", "warn", "block"]
    base: str
    head: str
    rule_score: Score | None
    """None when no function is rule-covered."""
    diff_score: Score | None
    """None when mutation didn't run."""
    findings: list[Finding]
    duration_s: float


def verdict_json_schema() -> dict[str, Any]:
    """The JSON schema checked in as ``docs/schema/verdict.schema.json``."""
    schema = Verdict.model_json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return schema
