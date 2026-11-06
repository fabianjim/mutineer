"""``--format terminal`` (the default): a rich report, BLOCK first."""

from __future__ import annotations

import re
from io import StringIO

from rich.console import Console
from rich.markup import escape
from rich.padding import Padding

from mutineer.models import Finding, Severity, Verdict
from mutineer.reporters._common import detail_lines, score_text, short_sha, where

_STYLE = {
    "pass": "bold green",
    "warn": "bold yellow",
    "block": "bold red",
    Severity.BLOCK: "red",
    Severity.WARN: "yellow",
    Severity.INFO: "cyan",
}
_SEVERITY_ORDER = {Severity.BLOCK: 0, Severity.WARN: 1, Severity.INFO: 2}
INDENT = 7
_TRAILING = re.compile(r"[ \t]+((?:\x1b\[[0-9;]*m)*)$", re.MULTILINE)


def render(verdict: Verdict, *, color: bool = False, width: int = 100) -> str:
    """The report as text; ANSI colours only when ``color`` is true."""
    buf = StringIO()
    console = Console(
        file=buf,
        force_terminal=color,
        no_color=not color,
        color_system="standard" if color else None,
        width=width,
        highlight=False,
        emoji=False,
    )
    style = _STYLE[verdict.status]
    console.print(
        f"Mutineer [{style}]{verdict.status.upper()}[/]"
        f"  rule score {score_text(verdict.rule_score)}"
        f"  diff score {score_text(verdict.diff_score)}"
        f"  base {short_sha(verdict)}  {verdict.duration_s:.1f} s"
    )
    findings = sorted(verdict.findings, key=lambda f: _SEVERITY_ORDER[f.severity])
    if not findings:
        console.print("No findings.")
    for f in findings:
        console.print()
        _finding(console, f)
    # rich pads wrapped lines to the wrap width; drop that padding, before any reset code
    return "".join(_TRAILING.sub(r"\1", line) for line in buf.getvalue().splitlines(True))


def _finding(console: Console, f: Finding) -> None:
    sev = f.severity
    head = [f"[{_STYLE[sev]}]{sev.upper():<5}[/]", f.id, f"[bold]{f.kind}[/]"]
    loc = where(f)
    if loc:
        head.append(escape(loc))
    if f.rules:
        head.append(escape(f"[{', '.join(f.rules)}]"))
    console.print("  ".join(head))
    console.print(Padding(escape(f.message), (0, 0, 0, INDENT), expand=False))
    for line in detail_lines(f, str):
        console.print(Padding(escape(line), (0, 0, 0, INDENT + 2), expand=False))
    if f.next_action:
        console.print(
            Padding(f"[dim]next:[/] {escape(f.next_action)}", (0, 0, 0, INDENT), expand=False)
        )
