"""Read-only marketplace/plugin inventory for /plugins.

Sources: ~/.zcode/cli/plugins/marketplaces/**/* plus ~/.agents/skills.
Note: zcode loads plugins enabled via ~/.zcode/cli/config.json (read-only here);
ztui exposes every marketplace plugin's skills as slash commands regardless.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

MARKETPLACES = Path.home() / ".zcode" / "cli" / "plugins" / "marketplaces"
BCACHE = Path.home() / ".zcode" / "cli" / "plugins" / "cache"


@dataclass
class PluginEntry:
    name: str
    marketplace: str
    skills: list[str] = field(default_factory=list)
    details: str = ""


def list_marketplace_plugins() -> list[PluginEntry]:
    out: list[PluginEntry] = []
    for root, tag in ((MARKETPLACES, "marketplace"), (BCACHE, "cache")):
        if not root.exists():
            continue
        for market_dir in sorted(root.iterdir()):
            if not market_dir.is_dir():
                continue
            for plugin_dir in sorted(market_dir.iterdir()):
                if not plugin_dir.is_dir():
                    continue
                skills = sorted({p.parent.name for p in plugin_dir.rglob("SKILL.md")})
                out.append(
                    PluginEntry(
                        name=plugin_dir.name,
                        marketplace=f"{tag}/{market_dir.name}",
                        skills=skills,
                    )
                )
    return out
