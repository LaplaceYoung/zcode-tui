"""Read-only access to ZCode GUI session history (sqlite).

ztui never writes here; connections are opened with mode=ro.
Translates zcode's opencode-style (session/message/part) rows into ztui's
message events so GUI sessions can be browsed and resumed inside ztui.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from ..zconfig import ZCODE_DB

SKIP_PART_TYPES = {"step-start", "step-finish", "timeline", "compaction", "snapshot"}


@dataclass
class ZSessionRow:
    id: str
    title: str
    directory: str
    updated_ms: int
    model_label: str


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{ZCODE_DB}?mode=ro", uri=True, timeout=5)
    return conn


def available() -> bool:
    return ZCODE_DB.exists()


def list_zcode_sessions(limit: int = 100) -> list[ZSessionRow]:
    rows: list[ZSessionRow] = []
    try:
        conn = _connect()
        try:
            cur = conn.execute(
                """
                SELECT s.id, s.title, s.directory, s.time_updated,
                  (SELECT json_extract(m.data, '$.modelID')
                     FROM message m
                    WHERE m.session_id = s.id
                      AND json_type(m.data, '$.modelID') IS NOT NULL
                    ORDER BY m.sequence DESC LIMIT 1) AS model
                FROM session s
                ORDER BY s.time_updated DESC
                LIMIT ?
                """,
                (limit,),
            )
            for sid, title, directory, updated, model in cur.fetchall():
                rows.append(
                    ZSessionRow(
                        id=sid,
                        title=title or "(untitled)",
                        directory=directory or "",
                        updated_ms=updated or 0,
                        model_label=model or "",
                    )
                )
        finally:
            conn.close()
    except sqlite3.Error:
        pass
    return rows


def last_model_pair(conn: sqlite3.Connection, sid: str) -> tuple[str | None, str | None]:
    cur = conn.execute(
        "SELECT json_extract(data, '$.providerID'), json_extract(data, '$.modelID') "
        "FROM message WHERE session_id=? AND json_type(data, '$.providerID') IS NOT NULL "
        "ORDER BY sequence DESC LIMIT 1",
        (sid,),
    )
    row = cur.fetchone()
    if row:
        return row[0], row[1]
    return None, None


def load_zcode_session(sid: str) -> tuple[dict, list[dict]]:
    """Return (meta, ztui message events) for one GUI session."""
    conn = _connect()
    try:
        meta_cur = conn.execute(
            "SELECT id, title, directory, time_created FROM session WHERE id=?", (sid,)
        )
        row = meta_cur.fetchone()
        if not row:
            raise KeyError(f"session {sid} not found")
        meta = {
            "id": row[0],
            "title": row[1] or "(untitled)",
            "directory": row[2] or "",
            "time_created": row[3] or 0,
        }
        meta["provider_id"], meta["model_id"] = last_model_pair(conn, sid)

        messages = conn.execute(
            "SELECT id, data, sequence FROM message WHERE session_id=? ORDER BY sequence",
            (sid,),
        ).fetchall()
        parts = conn.execute(
            "SELECT message_id, data, sequence FROM part WHERE session_id=? ORDER BY sequence",
            (sid,),
        ).fetchall()
    finally:
        conn.close()

    parts_by_msg: dict[str, list[dict]] = {}
    for mid, data, _seq in parts:
        try:
            pdata = json.loads(data)
        except (json.JSONDecodeError, TypeError):
            continue
        parts_by_msg.setdefault(mid, []).append(pdata)

    events: list[dict] = []
    for mid, mdata, _seq in messages:
        try:
            m = json.loads(mdata)
        except (json.JSONDecodeError, TypeError):
            continue
        role = m.get("role")
        mparts = parts_by_msg.get(mid, [])
        content: list[dict] = []
        tool_results: list[dict] = []
        for p in mparts:
            ptype = p.get("type")
            if ptype == "text":
                text = p.get("text", "").strip()
                if text:
                    content.append({"type": "text", "text": text})
            elif ptype == "tool":
                state = p.get("state", {}) or {}
                call_id = p.get("callID", "")
                tool_input = state.get("input", {})
                output = state.get("output", "")
                status = state.get("status", "completed")
                if role == "assistant" and call_id:
                    content.append({
                        "type": "tool_use",
                        "id": call_id,
                        "name": p.get("tool", "tool"),
                        "input": tool_input if isinstance(tool_input, dict) else {},
                    })
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)[:60000],
                        "is_error": status == "error",
                    })
            elif ptype == "file":
                # show attachments as text notes
                fname = p.get("filename") or "file"
                content.append({"type": "text", "text": f"[attachment: {fname}]"})
        if content and role in ("user", "assistant"):
            events.append({"type": "message", "role": role, "content": content})
        if tool_results:
            events.append({"type": "message", "role": "user", "content": tool_results})
    return meta, events
