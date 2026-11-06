"""The last run, kept in ``.mutineer/last-run.json`` so ``recheck`` can build on it."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from pydantic import BaseModel, ConfigDict, ValidationError

from mutineer.errors import MutineerError
from mutineer.models import EngineRun, Verdict

STATE_FILE = ".mutineer/last-run.json"
STATE_VERSION = 1


class State(BaseModel):
    """What ``run`` leaves for ``recheck``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: int = STATE_VERSION
    base_ref: str
    """The base ref the diff was taken against (``--base``, config, or main/master)."""
    base: str
    """The merge-base sha at the time of the run."""
    rounds: int = 0
    """``recheck`` rounds since the last ``run``."""
    survivor_names: list[str] = []
    """Mutant names of the ``survivor_rule`` findings: what ``recheck`` reruns."""
    engine_run: EngineRun | None = None
    """None when mutation didn't run (``--no-mutation``)."""
    verdict: Verdict


def save(root: Path, state: State) -> Path:
    """Write ``state`` atomically to ``root/.mutineer/last-run.json``."""
    path = root / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".last-run-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(state.model_dump_json(indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def load(root: Path) -> State | None:
    """The saved state, or None if there is none. Raises if it can't be read."""
    path = root / STATE_FILE
    if not path.is_file():
        return None
    try:
        state = State.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValidationError) as exc:
        raise MutineerError(
            f"{STATE_FILE} can't be read ({type(exc).__name__}); run `mutineer run` again"
        ) from exc
    if state.version != STATE_VERSION:
        raise MutineerError(
            f"{STATE_FILE} is from another Mutineer version; run `mutineer run` again"
        )
    return state


__all__ = ["STATE_FILE", "State", "load", "save"]
