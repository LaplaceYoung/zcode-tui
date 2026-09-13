"""ztui's own session store: JSONL files under ~/.local/share/zcode-tui/.

(The ZCode GUI stores its sessions in a sqlite db we never write to; M2 adds
a read-only browser for it.)
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..zconfig import ZTUI_DATA_DIR, ZTUI_SESSIONS_DIR


@dataclass
class SessionMeta:
    id: str
    cwd: str
    provider_id: str
    model_id: str
    level: str | None
    created: float = field(default_factory=time.time)
    updated: float = field(default_factory=time.time)
    title: str = ""


class Session:
    def __init__(self, meta: SessionMeta, path: Path):
        self.meta = meta
        self.path = path

    @classmethod
    def create(cls, cwd: str, provider_id: str, model_id: str, level: str | None) -> "Session":
        sid = f"ztui_{uuid.uuid4().hex[:12]}"
        meta = SessionMeta(sid, cwd, provider_id, model_id, level)
        ZTUI_SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        session = cls(meta, ZTUI_SESSIONS_DIR / f"{sid}.jsonl")
        session.append({"type": "session", "meta": {**meta.__dict__}})
        return session

    def append(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        with self.path.open("a") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
        self.meta.updated = time.time()

    def touch_title(self, title: str) -> None:
        self.meta.title = title[:120]
        self.append({"type": "title", "title": self.meta.title})


@dataclass
class SessionSummary:
    id: str
    path: Path
    cwd: str
    provider_id: str
    model_id: str
    updated: float
    title: str


def list_sessions() -> list[SessionSummary]:
    if not ZTUI_SESSIONS_DIR.exists():
        return []
    out: list[SessionSummary] = []
    for p in ZTUI_SESSIONS_DIR.glob("*.jsonl"):
        meta = _read_meta(p)
        if meta:
            out.append(meta)
    out.sort(key=lambda s: s.updated, reverse=True)
    return out


def _read_meta(path: Path) -> SessionSummary | None:
    try:
        with path.open() as f:
            first = json.loads(f.readline())
        meta = first.get("meta", {})
        title = ""
        # Peek a few lines for a title/first user message.
        with path.open() as f:
            for i, line in enumerate(f):
                if i > 40:
                    break
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if ev.get("type") == "title":
                    title = ev.get("title", "")
                    break
                if ev.get("type") == "user":
                    title = str(ev.get("text", ""))[:120]
                    break
        return SessionSummary(
            id=meta.get("id", path.stem),
            path=path,
            cwd=meta.get("cwd", ""),
            provider_id=meta.get("provider_id", ""),
            model_id=meta.get("model_id", ""),
            updated=path.stat().st_mtime,
            title=title or "(untitled)",
        )
    except Exception:
        return None


def load_events(session_id: str) -> list[dict[str, Any]]:
    path = ZTUI_SESSIONS_DIR / f"{session_id}.jsonl"
    events: list[dict[str, Any]] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events
