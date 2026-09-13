"""Read-only access to ZCode dynamic workflow runs (db.sqlite dwf_* tables)."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from ..zconfig import ZCODE_DB


@dataclass
class WorkflowRunRow:
    run_id: str
    name: str
    cwd: str
    status: str
    spent_tokens: int
    created_ms: int
    updated_ms: int
    conclusion: str


@dataclass
class ActorRow:
    name: str
    model: str


@dataclass
class RunDetail:
    run: WorkflowRunRow
    actors: list[ActorRow]
    node_kinds: dict[str, int]
    node_status: dict[str, int]
    conclusion: str
    failure: str


def available() -> bool:
    return ZCODE_DB.exists()


def list_runs(limit: int = 30) -> list[WorkflowRunRow]:
    try:
        conn = sqlite3.connect(f"file:{ZCODE_DB}?mode=ro", uri=True, timeout=5)
    except sqlite3.Error:
        return []
    try:
        rows = conn.execute(
            """
            SELECT id, name, cwd, status, spent_tokens, time_created, time_updated, result_json
            FROM dwf_run ORDER BY time_updated DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()
    out: list[WorkflowRunRow] = []
    for rid, name, cwd, status, tokens, created, updated, result in rows:
        conclusion = ""
        if result:
            try:
                conclusion = json.loads(result).get("conclusion", "")[:200]
            except ValueError:
                conclusion = result[:200]
        out.append(
            WorkflowRunRow(
                run_id=rid, name=name or "", cwd=cwd or "", status=status or "unknown",
                spent_tokens=tokens or 0, created_ms=created or 0, updated_ms=updated or 0,
                conclusion=conclusion,
            )
        )
    return out


def run_detail(run_id: str) -> RunDetail | None:
    conn = sqlite3.connect(f"file:{ZCODE_DB}?mode=ro", uri=True, timeout=5)
    try:
        row = conn.execute(
            "SELECT id, name, cwd, status, spent_tokens, time_created, time_updated, "
            "result_json, failure_json FROM dwf_run WHERE id=?",
            (run_id,),
        ).fetchone()
        if not row:
            return None
        rid, name, cwd, status, tokens, created, updated, result, failure = row
        actors = conn.execute(
            "SELECT name, resolved_model FROM dwf_actor WHERE run_id=? ORDER BY ordinal",
            (run_id,),
        ).fetchall()
        kinds = conn.execute(
            "SELECT kind, COUNT(*) FROM dwf_node WHERE run_id=? GROUP BY kind",
            (run_id,),
        ).fetchall()
        statuses = conn.execute(
            "SELECT status, COUNT(*) FROM dwf_node WHERE run_id=? GROUP BY status",
            (run_id,),
        ).fetchall()
    finally:
        conn.close()

    conclusion = ""
    if result:
        try:
            data = json.loads(result)
            conclusion = data.get("conclusion") or json.dumps(data, ensure_ascii=False)[:2000]
        except ValueError:
            conclusion = result[:2000]
    fail_text = ""
    if failure:
        try:
            fdata = json.loads(failure)
            fail_text = fdata.get("message") or fdata.get("error") or json.dumps(fdata, ensure_ascii=False)[:800]
        except ValueError:
            fail_text = failure[:800]
    run = WorkflowRunRow(rid, name or "", cwd or "", status or "unknown", tokens or 0,
                         created or 0, updated or 0, conclusion[:200])
    return RunDetail(
        run=run,
        actors=[ActorRow(a[0] or "(unnamed)", a[1] or "") for a in actors],
        node_kinds={k: c for k, c in kinds},
        node_status={s: c for s, c in statuses},
        conclusion=conclusion,
        failure=fail_text,
    )
