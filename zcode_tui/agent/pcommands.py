"""Plugin slash commands (~/.zcode/cli/plugins/cache/**/commands/*.md).

Same markdown format zcode/CC use: frontmatter (description, argument-hint)
plus a body prompt; `$ARGUMENTS` is replaced by whatever the user types after
the command.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CACHE_ROOT = Path.home() / ".zcode" / "cli" / "plugins" / "cache"

MAX_BODY = 16_000


@dataclass
class PluginCommand:
    name: str
    description: str
    argument_hint: str
    body: str
    path: Path


def _parse(path: Path) -> PluginCommand | None:
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return None
    name = path.stem
    description, argument_hint = "", ""
    body = raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if m:
        body = raw[m.end():].strip()
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "description" and v:
                description = v
            elif k == "argument-hint" and v:
                argument_hint = v
    if not body:
        return None
    return PluginCommand(name, description, body[:MAX_BODY], path, argument_hint)


def scan_plugin_commands() -> list[PluginCommand]:
    out: dict[str, PluginCommand] = {}
    if CACHE_ROOT.exists():
        for path in sorted(CACHE_ROOT.glob("*/*/*/commands/*.md")):
            cmd = _parse(path)
            if cmd:
                out.setdefault(cmd.name, cmd)
    return list(out.values())


def command_prompt(cmd: PluginCommand, args: str) -> str:
    return cmd.body.replace("$ARGUMENTS", args.strip()).replace("$ARGS", args.strip())
