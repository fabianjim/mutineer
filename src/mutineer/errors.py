"""Errors that stop Mutineer. Each message says what to fix, in one line."""

from __future__ import annotations


class MutineerError(Exception):
    """Mutineer could not run. The message must say what to fix, in one line."""

    exit_code = 4


class ConfigError(MutineerError): ...


class RulesError(MutineerError): ...


class EngineError(MutineerError): ...


class NeedsHumanError(MutineerError):
    """The ``recheck`` round cap was reached."""

    exit_code = 3
