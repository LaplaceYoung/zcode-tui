"""Load zcode plugin skills (~/.zcode/cli/plugins/cache + ~/.agents/skills).

A skill directory holds a SKILL.md with frontmatter (name/description) and a
markdown body. Skills become slash commands whose body is injected as the
task playbook for the agent — mirroring zcode's own skill invocation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CACHE_ROOT = Path.home() / ".zcode" / "cli" / "plugins" / "cache"
AGENTS_ROOT = Path.home() / ".agents" / "skills"

MAX_BODY = 24_000


@dataclass
class Skill:
    name: str
    description: str
    path: Path
    content: str
    source: str  # where it was loaded from


def _parse(path: Path, source: str) -> "Skill | None":
    try:
        raw = path.read_text(errors="replace")
    except OSError:
        return None
    name = path.parent.name
    description = ""
    body = raw
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n", raw, re.DOTALL)
    if m:
        body = raw[m.end():]
        for line in m.group(1).splitlines():
            k, _, v = line.partition(":")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k == "name" and v:
                name = v
            elif k == "description" and v:
                description = v.split("。", 1)[0] + ("。" if "。" in v else "")
    return Skill(name, description, path, body.strip()[:MAX_BODY], source)


def scan_skills() -> list[Skill]:
    out: dict[str, Skill] = {}
    if CACHE_ROOT.exists():
        for path in sorted(CACHE_ROOT.glob("*/*/*/skills/*/SKILL.md")):
            skill = _parse(path, "zcode")
            if skill:
                out.setdefault(skill.name, skill)
    if AGENTS_ROOT.exists():
        for path in sorted(AGENTS_ROOT.glob("*/SKILL.md")):
            skill = _parse(path, "user")
            if skill:
                out.setdefault(skill.name, skill)
    return list(out.values())


def skill_prompt(skill: Skill, task: str) -> str:
    return (
        "You are executing a special playbook (a \"skill\"). Follow its instructions "
        "precisely to accomplish the user's task. If the skill references helper "
        "scripts or files, resolve their paths relative to the skill's directory "
        f"({skill.path.parent}) and use them.\n\n"
        f"# Skill: {skill.name}\n\n{skill.content}\n\n"
        f"# Task\n\n{task.strip() or 'Use the skill with sensible defaults for this workspace.'}"
    )
