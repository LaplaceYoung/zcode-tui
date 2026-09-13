"""Read-only access to ZCode scheduled automations (tasks-index.sqlite)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

ZCODE_TASKS_DB = __import__("pathlib").Path.home() / ".zcode" / "v2" / "tasks-index.sqlite"


@dataclass
class AutomationRow:
    id: str
    title: str
    cron: str
    enabled: bool
    running: bool
    run_count: int
    next_run_ms: int
    last_run_ms: int
    last_outcome: str
    last_error: str
    workspace: str


def list_automations(limit: int = 50) -> list[AutomationRow] | None:
    if not ZCODE_TASKS_DB.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{ZCODE_TASKS_DB}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return None
    try:
        rows = conn.execute(
            """
            SELECT a.automation_id, a.title, a.cron_expr, a.enabled, a.running,
                   a.run_count, a.next_run_at, a.last_run_at, a.last_error,
                   a.workspace_path,
              (SELECT r.outcome FROM automation_runs r
                WHERE r.automation_id = a.automation_id
                ORDER BY r.created_at DESC LIMIT 1) AS last_outcome
            FROM automations a
            ORDER BY a.enabled DESC, a.next_run_at ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    return [
        AutomationRow(
            id=r[0], title=r[1] or "(untitled)", cron=r[2] or "-",
            enabled=bool(r[3]), running=bool(r[4]), run_count=r[5] or 0,
            next_run_ms=r[6] or 0, last_run_ms=r[7] or 0,
            last_error=r[8] or "", workspace=r[9] or "",
            last_outcome=r[10] or "-",
        )
        for r in rows
    ]


def fmt_ms(ms: int) -> str:
    if not ms:
        return "-"
    delta = ms / 1000 - time.time()
    if delta > 0:
        if delta < 3600:
            return f"in {int(delta / 60)}m"
        if delta < 86400:
            return f"in {delta / 3600:.1f}h"
        return f"in {int(delta / 86400)}d"
    delta = -delta
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{delta / 3600:.1f}h ago"
    return f"{int(delta / 86400)}d ago"
