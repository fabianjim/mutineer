"""The rules file (``.mutineer/rules.toml``) and the runtime rule map.

- ``load_rules``: parse and validate a rules file (WP0).
- ``compare``: rules-file changes from base to head; deleting an id is an error.
- ``collect_rule_map``: which tests carry which rule ids, as pytest (and so mutmut)
  sees them.
- ``rule_findings``: markers that don't match the rules file, and unguarded rules.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from mutineer.config import Config
from mutineer.errors import ConfigError, RulesError
from mutineer.models import Audience, Finding, Location, Rule, RuleMap, RulesFile, Severity


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


# ---- compare ----


def compare(
    base: RulesFile | None, head: RulesFile, *, rules_file: str | None = None
) -> list[Finding]:
    """Changes to the rules file from ``base`` to ``head``.

    Raises ``RulesError`` when an id present at base is missing at head: rules are
    retired, never deleted (RULES-001). Returns one ``rules_file_changed`` (INFO) per
    added or changed entry, sorted by id. ``base`` is None when the file is new.
    ``rules_file`` (relative path) is used as the findings' location.
    """
    before = {r.id: r for r in (base.rules if base else [])}
    after = {r.id: r for r in head.rules}
    deleted = sorted(before.keys() - after.keys())
    if deleted:
        ids = ", ".join(deleted)
        raise RulesError(
            f"rule {ids} was deleted from the rules file; retire {ids} instead of deleting it "
            '(keep the [[rule]] entry and set status = "retired")'
        )
    findings = []
    for rule_id in sorted(after):
        old, new = before.get(rule_id), after[rule_id]
        if old == new:
            continue
        change = _change_kind(old, new)
        evidence: dict[str, Any] = {"change": change, "after": new.model_dump()}
        if old is not None:
            evidence["before"] = old.model_dump()
        findings.append(
            Finding(
                check="rules",
                kind="rules_file_changed",
                severity=Severity.INFO,
                audience=Audience.HUMAN,
                rules=[rule_id],
                location=Location(file=rules_file) if rules_file else None,
                message=f"Rule {rule_id} was {change} in the rules file.",
                evidence=evidence,
                next_action=f"Check that the change to {rule_id} was meant to be made.",
            )
        )
    return findings


def _change_kind(old: Rule | None, new: Rule) -> str:
    if old is None:
        return "added"
    if old.status != new.status:
        return "retired" if new.status == "retired" else "reactivated"
    return "modified"


# ---- rule findings ----


def rule_findings(rules: RulesFile, rule_map: RuleMap) -> list[Finding]:
    """Mismatches between test markers and the rules file.

    - ``unknown_rule_id`` (WARN): a marker id that isn't in the rules file;
    - ``retired_rule_marked`` (WARN): a marker id of a retired rule;
    - ``rule_unguarded`` (INFO): an active rule that no test is marked with.

    One finding per rule id, in that order, each sorted by id. The marked tests are in
    ``evidence["tests"]``, and the first one's file is the location.
    """
    tests_by_rule: dict[str, list[str]] = {}
    for node_id, ids in rule_map.items():
        for rule_id in ids:
            tests_by_rule.setdefault(rule_id, []).append(node_id)
    known = {r.id: r for r in rules.rules}
    unknown, retired = [], []
    for rule_id in sorted(tests_by_rule):
        tests = sorted(tests_by_rule[rule_id])
        rule = known.get(rule_id)
        if rule is None:
            unknown.append(
                _marker_finding(
                    "unknown_rule_id",
                    rule_id,
                    tests,
                    f"Tests are marked with rule {rule_id}, which is not in the rules file.",
                    f"Add {rule_id} to the rules file, or fix the marker.",
                )
            )
        elif rule.status == "retired":
            retired.append(
                _marker_finding(
                    "retired_rule_marked",
                    rule_id,
                    tests,
                    f"Tests are marked with rule {rule_id}, which is retired.",
                    f"Remove the {rule_id} marker, or make the rule active again.",
                )
            )
    unguarded = [
        Finding(
            check="rules",
            kind="rule_unguarded",
            severity=Severity.INFO,
            audience=Audience.HUMAN,
            rules=[rule.id],
            message=f"No test is marked with rule {rule.id}.",
            evidence={"tests": []},
            next_action=f'Mark a test that proves {rule.id} with @pytest.mark.rule("{rule.id}").',
        )
        for rule in sorted(rules.rules, key=lambda r: r.id)
        if rule.status == "active" and rule.id not in tests_by_rule
    ]
    return unknown + retired + unguarded


def _marker_finding(
    kind: str, rule_id: str, tests: list[str], message: str, next_action: str
) -> Finding:
    return Finding(
        check="rules",
        kind=kind,
        severity=Severity.WARN,
        audience=Audience.HUMAN,
        rules=[rule_id],
        location=Location(file=tests[0].split("::", 1)[0]),
        message=message,
        evidence={"tests": tests},
        next_action=next_action,
    )


# ---- rule map ----

_OUTPUT_TAIL = 60


def collect_rule_map(root: Path, config: Config) -> RuleMap:
    """Collect the rule map by running ``pytest --collect-only`` in ``root``.

    Uses the same pytest arguments as mutmut's own collection (``--rootdir=.``, the
    project's ``[tool.mutmut]`` ``pytest_add_cli_args_test_selection`` and
    ``pytest_add_cli_args``), so node ids match mutmut's ``tests_by_mangled_function_name``.
    Adds ``--ignore=mutants -p no:cacheprovider`` and ``PYTHONDONTWRITEBYTECODE=1``.
    Raises ``RulesError`` if collection fails. ``config`` is accepted for the contract;
    test selection comes from mutmut's settings, not ``test_paths``.
    """
    del config  # see docstring
    selection, extra = _mutmut_pytest_args(root)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    with tempfile.TemporaryDirectory(prefix="mutineer-rulemap-") as tmp:
        out_path = Path(tmp) / "rulemap.json"
        cmd = [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-q",
            "--rootdir=.",
            "--ignore=mutants",
            "-p",
            "no:cacheprovider",
            # The entry-point name: loads the plugin even with PYTEST_DISABLE_PLUGIN_AUTOLOAD,
            # and is a no-op when it is already autoloaded.
            "-p",
            "mutineer",
            f"--mutineer-rulemap={out_path}",
            *selection,
            *extra,
        ]
        proc = subprocess.run(
            cmd, cwd=root, env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL
        )
        # 5: nothing collected (or everything deselected); the map is still written.
        if proc.returncode not in (0, 5):
            raise RulesError(_collection_failure(proc))
        if not out_path.is_file():
            raise RulesError(
                "pytest ran but Mutineer's pytest plugin did not write the rule map; "
                "install mutineer in the project's environment and don't block "
                "the plugin with -p no:mutineer"
            )
        return _parse_rule_map(out_path.read_text(encoding="utf-8"))


def _mutmut_pytest_args(root: Path) -> tuple[list[str], list[str]]:
    """``[tool.mutmut]`` test-selection and extra pytest args, read as mutmut reads them."""
    path = root / "pyproject.toml"
    if not path.is_file():
        return [], []
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(f"pyproject.toml is not valid TOML ({exc}); fix the file") from exc
    table = data.get("tool", {}).get("mutmut", {})
    if not isinstance(table, dict):
        raise ConfigError("[tool.mutmut] in pyproject.toml must be a table")
    # mutmut appends the deprecated `tests_dir` to the test selection.
    selection = _str_list(table, "pytest_add_cli_args_test_selection") + _str_list(
        table, "tests_dir"
    )
    return selection, _str_list(table, "pytest_add_cli_args")


def _str_list(table: dict[str, Any], key: str) -> list[str]:
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ConfigError(f"[tool.mutmut] {key} in pyproject.toml must be a list of strings")
    return value


def _collection_failure(proc: subprocess.CompletedProcess[str]) -> str:
    lines = [ln.strip() for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    tail = lines[-_OUTPUT_TAIL:]
    errors = [ln for ln in tail if ln.startswith(("ERROR", "E ", "error:"))]
    detail = errors[-1] if errors else (tail[-1] if tail else "no output")
    return (
        f"pytest could not collect the tests (exit {proc.returncode}): {detail}; "
        "fix collection so `pytest --collect-only` passes, then rerun"
    )


def _parse_rule_map(text: str) -> RuleMap:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RulesError(f"the rule map from pytest is not valid JSON ({exc})") from exc
    if not isinstance(data, dict) or not all(
        isinstance(ids, list) and all(isinstance(i, str) for i in ids) for ids in data.values()
    ):
        raise RulesError("the rule map from pytest has an unexpected shape")
    return {str(node): sorted(set(ids)) for node, ids in data.items()}
