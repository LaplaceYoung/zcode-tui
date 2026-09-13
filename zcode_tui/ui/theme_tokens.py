"""Mutable palette + Textual theme definitions for ztui's /theme command.

Rich renderables are rebuilt every frame, so mutating these module strings
recolors live content; the TCSS side uses Textual theme variables registered
from the same palettes via register_themes().
"""

from __future__ import annotations

from textual.theme import Theme

PALETTES: dict[str, dict[str, str]] = {
    "python": {
        "desc": "默认 · Python 黄蓝（黄 accent + 蓝辅）",
        "ACCENT": "#FFD43B", "ACCENT_LIGHT": "#4B8BBE",
        "DIM": "#7d8fa0", "BODY": "#a9b9c8",
        "background": "#152030", "surface": "#1c2a3a", "edge": "#2c4258",
        "text": "#e6ecf2", "success": "#57ab5a", "error": "#e5534b", "warning": "#e0af68",
    },
    "claude-dark": {
        "desc": "暗夜 + Claude 橙",
        "ACCENT": "#D97757", "ACCENT_LIGHT": "#f0b28c",
        "DIM": "#6b6d78", "BODY": "#9a9ba5",
        "background": "#1a1b1e", "surface": "#222328", "edge": "#3c3f4a",
        "text": "#d7d8dd", "success": "#57ab5a", "error": "#e5534b", "warning": "#e0af68",
    },
    "gruvbox": {
        "desc": "Gruvbox 暖棕",
        "ACCENT": "#fe8019", "ACCENT_LIGHT": "#fabd2f",
        "DIM": "#928374", "BODY": "#bdae93",
        "background": "#282828", "surface": "#3c3836", "edge": "#504945",
        "text": "#ebdbb2", "success": "#b8bb26", "error": "#fb4934", "warning": "#d79921",
    },
    "tokyo-night": {
        "desc": "Tokyo Night 蓝紫",
        "ACCENT": "#bb9af7", "ACCENT_LIGHT": "#7aa2f7",
        "DIM": "#565f89", "BODY": "#a9b1d6",
        "background": "#1a1b26", "surface": "#24283b", "edge": "#3b4261",
        "text": "#c0caf5", "success": "#9ece6a", "error": "#f7768e", "warning": "#e0af68",
    },
    "paper-light": {
        "desc": "浅色纸面",
        "ACCENT": "#D97757", "ACCENT_LIGHT": "#b3541e",
        "DIM": "#8b8b93", "BODY": "#4a4a50",
        "background": "#f5f4ef", "surface": "#ffffff", "edge": "#d8d6cf",
        "text": "#2b2b30", "success": "#4e7d2e", "error": "#c73e3e", "warning": "#967920",
    },
}

# Mutable live tokens (assigned by set_palette).
ACCENT = PALETTES["python"]["ACCENT"]
ACCENT_LIGHT = PALETTES["python"]["ACCENT_LIGHT"]
DIM = PALETTES["python"]["DIM"]
BODY = PALETTES["python"]["BODY"]
_current = "python"


def names() -> list[str]:
    return list(PALETTES)


def current() -> str:
    return _current


def set_palette(name: str) -> None:
    global ACCENT, ACCENT_LIGHT, DIM, BODY, _current
    p = PALETTES[name]
    ACCENT = p["ACCENT"]
    ACCENT_LIGHT = p["ACCENT_LIGHT"]
    DIM = p["DIM"]
    BODY = p["BODY"]
    _current = name


def register_themes(app) -> None:
    """Register one Textual Theme per palette (ui/theme var mapping)."""
    for name, p in PALETTES.items():
        app.register_theme(
            Theme(
                name=f"ztui-{name}",
                primary=p["ACCENT"],
                secondary=p["ACCENT_LIGHT"],
                accent=p["ACCENT_LIGHT"],
                warning=p["warning"],
                error=p["error"],
                success=p["success"],
                foreground=p["text"],
                background=p["background"],
                surface=p["surface"],
                panel=p["surface"],
                boost=p["surface"],
                dark=name != "paper-light",
                variables={
                    "edge": p["edge"],
                    "mute": p["DIM"],
                    "faded": p["BODY"],
                    "accent-light": p["ACCENT_LIGHT"],
                },
            )
        )
