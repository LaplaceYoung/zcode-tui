"""Custom agent personas (~/.zcode/agents/*.md) usable by task and slash commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

AGENTS_DIR = Path.home() / ".zcode" / "agents"


@dataclass
class Persona:
    name: str
    description: str
    system_prompt: str
    path: Path
    color: str = ""
    inject_agents_md: bool = False


def _parse(path: Path) -> Persona | None:
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return None
    name = path.stem
    description, color = "", ""
    inject_agents_md = False
    body = raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if m:
        body = raw[m.end():].strip()
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "name" and v:
                name = v
            elif k == "description" and v:
                description = v
            elif k == "color":
                color = v
            elif k == "injectAgentsMd":
                inject_agents_md = v.strip().lower() in ("true", "1", "yes")
    if not description:
        description = next((l.strip() for l in body.splitlines() if l.strip()), "")
    return Persona(name, description, body, path, color, inject_agents_md)


def scan_personas() -> list[Persona]:
    if not AGENTS_DIR.exists():
        return []
    out: list[Persona] = []
    for path in sorted(AGENTS_DIR.glob("*.md")):
        persona = _parse(path)
        if persona:
            out.append(persona)
    return out


def resolve(name: str) -> Persona | None:
    name = name.strip().lstrip("/").lower()
    for persona in scan_personas():
        if persona.name.lower() == name or persona.path.stem.lower() == name:
            return persona
    return None
