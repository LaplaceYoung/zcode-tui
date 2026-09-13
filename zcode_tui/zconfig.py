"""Read-only adapters over ZCode's own configuration files.

Everything this module touches under ~/.zcode is strictly read-only.
ztui's own state lives in ~/.config/zcode-tui/ and ~/.local/share/zcode-tui/.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ZCODE_HOME = Path.home() / ".zcode"
ZCODE_V2 = ZCODE_HOME / "v2"
ZCODE_CONFIG = ZCODE_V2 / "config.json"
ZCODE_SETTING = ZCODE_V2 / "setting.json"
ZCODE_DB = ZCODE_HOME / "cli" / "db" / "db.sqlite"

ZTUI_CONFIG_DIR = Path.home() / ".config" / "zcode-tui"
ZTUI_CONFIG = ZTUI_CONFIG_DIR / "config.toml"
ZTUI_DATA_DIR = Path.home() / ".local" / "share" / "zcode-tui"
ZTUI_SESSIONS_DIR = ZTUI_DATA_DIR / "sessions"

CATALOG_GLOB = "/Applications/ZCode.app/Contents/Resources/model-providers/models_catalog_*.json"


@dataclass
class ModelInfo:
    id: str  # identifier used in zcode config (display id)
    api_id: str  # identifier sent to the API (`name` field when present)
    provider_id: str
    reasoning_variants: list[str] = field(default_factory=list)
    reasoning_default: str | None = None
    context_limit: int = 128_000
    output_limit: int = 16_384
    input_modalities: list[str] = field(default_factory=lambda: ["text"])

    @property
    def supports_reasoning(self) -> bool:
        return bool(self.reasoning_variants)


@dataclass
class Provider:
    id: str
    name: str
    kind: str  # "anthropic" | "openai-compatible" | "openai"
    base_url: str
    api_key: str
    enabled: bool
    models: dict[str, ModelInfo] = field(default_factory=dict)

    def model(self, model_id: str) -> ModelInfo | None:
        return self.models.get(model_id)


@dataclass
class Defaults:
    provider_id: str | None = None
    model_id: str | None = None
    level: str | None = None
    mode: str = "build"


def _load_zcode_providers() -> list[Provider]:
    raw = json.loads(ZCODE_CONFIG.read_text())
    out: list[Provider] = []
    for pid, p in raw.get("provider", {}).items():
        # In zcode semantics a missing `enabled` means enabled.
        enabled = p.get("enabled", True) and not p.get("systemDisabledReason")
        opts = p.get("options", {})
        provider = Provider(
            id=pid,
            name=p.get("name", pid),
            kind=p.get("kind", "anthropic"),
            base_url=opts.get("baseURL", "").rstrip("/"),
            api_key=opts.get("apiKey", ""),
            enabled=enabled,
        )
        for mid, m in p.get("models", {}).items():
            reasoning = m.get("reasoning", {}) or {}
            limit = m.get("limit", {}) or {}
            modalities = m.get("modalities", {}) or {}
            provider.models[mid] = ModelInfo(
                id=mid,
                api_id=m.get("name") or mid,
                provider_id=pid,
                reasoning_variants=list(reasoning.get("variants", [])),
                reasoning_default=reasoning.get("defaultVariant"),
                context_limit=int(limit.get("context", 128_000)),
                output_limit=int(limit.get("output", 16_384)),
                input_modalities=list(modalities.get("input", ["text"])),
            )
        out.append(provider)
    return out


def _gui_selected_provider_ids() -> list[str]:
    """Provider ids the ZCode GUI user has chosen per family, best-effort order."""
    try:
        setting = json.loads(ZCODE_SETTING.read_text())
    except Exception:
        return []
    selected: list[str] = []
    for key in setting.get("modelProviderFamilySelectedKeys", {}).values():
        # Values look like "coding-plan:builtin:bigmodel-coding-plan".
        if ":" in key:
            pid = key.split(":", 1)[1]
            if pid not in selected:
                selected.append(pid)
    return selected


def _gui_default_reasoning_level() -> str | None:
    """zcode stores its default reasoning level in the GUI sqlite; read-only."""
    if not ZCODE_DB.exists():
        return None
    try:
        import sqlite3

        conn = sqlite3.connect(f"file:{ZCODE_DB}?mode=ro", uri=True)
        try:
            cur = conn.execute(
                "SELECT value FROM local_setting WHERE namespace='model' AND key='reasoningLevel' LIMIT 1"
            )
            row = cur.fetchone()
            if row:
                return json.loads(row[0]).get("level")
        finally:
            conn.close()
    except Exception:
        return None
    return None


class OwnConfig:
    """ztui's own persisted config (~/.config/zcode-tui/config.toml)."""

    def __init__(self, data: dict, path: Path = ZTUI_CONFIG):
        self._data = data
        self.path = path

    @classmethod
    def load(cls) -> "OwnConfig":
        if ZTUI_CONFIG.exists():
            try:
                return cls(tomllib.loads(ZTUI_CONFIG.read_text()))
            except Exception:
                pass
        return cls({})

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(self._render())

    def _render(self) -> str:
        lines: list[str] = []
        defaults = self._data.get("defaults", {})
        if defaults:
            lines.append("[defaults]")
            for k, v in defaults.items():
                if v is not None:
                    lines.append(f'{k} = {json.dumps(str(v))}')
            lines.append("")
        for rule in self._data.get("permissions", []):
            lines.append("[[permissions]]")
            for k in ("tool", "pattern", "decision"):
                if rule.get(k) is not None:
                    lines.append(f'{k} = {json.dumps(str(rule[k]))}')
            lines.append("")
        return "\n".join(lines)

    @property
    def defaults(self) -> dict:
        return self._data.setdefault("defaults", {})

    @property
    def permission_rules(self) -> list[dict]:
        return self._data.setdefault("permissions", [])

    def add_permission_rule(self, tool: str, pattern: str, decision: str) -> None:
        # Replace an existing identical (tool, pattern) rule.
        self.permission_rules[:] = [
            r for r in self.permission_rules
            if not (r.get("tool") == tool and r.get("pattern") == pattern)
        ]
        self.permission_rules.append(
            {"tool": tool, "pattern": pattern, "decision": decision}
        )
        self.save()

    def set_default(self, **kwargs) -> None:
        self.defaults.update({k: v for k, v in kwargs.items() if v is not None})
        self.save()


class ZConfig:
    def __init__(self) -> None:
        self.providers: list[Provider] = _load_zcode_providers()
        self.own = OwnConfig.load()

    @property
    def usable_providers(self) -> list[Provider]:
        """Enabled providers that actually have credentials and models."""
        usable = [p for p in self.providers if p.enabled and p.api_key and p.models]
        order = _gui_selected_provider_ids()
        usable.sort(
            key=lambda p: (order.index(p.id) if p.id in order else len(order), p.name)
        )
        return usable

    def resolve_defaults(self) -> Defaults:
        d = Defaults()
        own = self.own.defaults
        own_pid, own_mid, own_lvl = own.get("provider"), own.get("model"), own.get("level")
        providers = self.usable_providers
        if own_pid:
            p = next((x for x in providers if x.id == own_pid), None)
            if p and own_mid in p.models:
                d.provider_id, d.model_id = p.id, own_mid
                m = p.models[own_mid]
                d.level = own_lvl or m.reasoning_default
        if d.provider_id is None and providers:
            p = providers[0]
            mid = own_mid if own_mid in p.models else next(iter(p.models))
            d.provider_id, d.model_id = p.id, mid
            d.level = own_lvl or p.models[mid].reasoning_default
        if d.level is None:
            d.level = _gui_default_reasoning_level()
        d.mode = own.get("mode", "build") or "build"
        return d

    def provider(self, pid: str) -> Provider | None:
        return next((p for p in self.providers if p.id == pid), None)

    def catalog_path(self) -> Path | None:
        import glob as _glob

        paths = sorted(_glob.glob(CATALOG_GLOB))
        return Path(paths[-1]) if paths else None


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)
