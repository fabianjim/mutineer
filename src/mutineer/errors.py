"""Errors that stop Mutineer. Each message says what to fix, in one line."""

from __future__ import annotations


class MutineerError(Exception):
    """Mutineer could not run. The message must say what to fix, in one line."""

    exit_code = 4


class ConfigError(MutineerError): ...


class RulesError(MutineerError): ...


class CollectionError(RulesError):
    """pytest could not collect the project's tests: the suite itself is broken.

    ``run`` and ``recheck`` turn this into a ``baseline_failed`` BLOCK instead of exiting 4.
    ``output`` holds the tail of pytest's output.
    """

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


class EngineError(MutineerError): ...


class NeedsHumanError(MutineerError):
    """The ``recheck`` round cap was reached."""

    exit_code = 3
