"""Spike: run mutmut with an in-memory config override, no file edits."""
import dataclasses, json, sys
import mutmut.configuration as mc

overrides = json.loads(sys.argv[1])
cfg = mc._load_config()
mc._config = dataclasses.replace(cfg, **overrides)

from mutmut.__main__ import cli  # imported after the override on purpose
sys.argv = ["mutmut", *sys.argv[2:]]
cli()
