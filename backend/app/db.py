"""SQLite persistence. A single connection-per-call helper keeps the code simple and thread-safe."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable

from .config import settings

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS workspaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    report_json TEXT,
    report_at REAL
);
CREATE TABLE IF NOT EXISTS resources (
    id TEXT PRIMARY KEY,
    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    origin TEXT DEFAULT '',
    stored_path TEXT DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT DEFAULT '',
    warnings TEXT DEFAULT '[]',
    stats TEXT DEFAULT '{}',
    position INTEGER DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS units (
    id TEXT PRIMARY KEY,
    resource_id TEXT NOT NULL REFERENCES resources(id) ON DELETE CASCADE,
    workspace_id TEXT NOT NULL,
    idx INTEGER NOT NULL,
    text TEXT NOT NULL,
    ctx TEXT DEFAULT '',
    label TEXT DEFAULT '',
    page INTEGER,
    t_start REAL,
    kind TEXT DEFAULT 'text'
);
CREATE INDEX IF NOT EXISTS idx_units_ws ON units(workspace_id);
CREATE INDEX IF NOT EXISTS idx_res_ws ON resources(workspace_id);
CREATE TABLE IF NOT EXISTS jobs (
    workspace_id TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(settings.db_path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys = ON")
        try:
            yield c
            c.commit()
        finally:
            c.close()


def init_db() -> None:
    settings.ensure_dirs()
    with conn() as c:
        c.executescript(SCHEMA)


def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:12]


def q(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, tuple(args)).fetchall()]


def q1(sql: str, args: Iterable[Any] = ()) -> dict | None:
    rows = q(sql, args)
    return rows[0] if rows else None


def x(sql: str, args: Iterable[Any] = ()) -> None:
    with conn() as c:
        c.execute(sql, tuple(args))


def xmany(sql: str, rows: list[tuple]) -> None:
    with conn() as c:
        c.executemany(sql, rows)


# ---- workspaces -----------------------------------------------------------
def create_workspace(name: str, description: str) -> dict:
    wid = new_id("w_")
    now = time.time()
    x("INSERT INTO workspaces(id,name,description,created_at,updated_at) VALUES(?,?,?,?,?)",
      (wid, name, description, now, now))
    return get_workspace(wid)  # type: ignore[return-value]


def get_workspace(wid: str) -> dict | None:
    return q1("SELECT id,name,description,created_at,updated_at,report_at FROM workspaces WHERE id=?", (wid,))


def touch_workspace(wid: str) -> None:
    x("UPDATE workspaces SET updated_at=? WHERE id=?", (time.time(), wid))


# ---- resources ------------------------------------------------------------
def next_source_id(wid: str) -> tuple[str, int]:
    rows = q("SELECT COALESCE(MAX(position),0) AS m FROM resources WHERE workspace_id=?", (wid,))
    pos = (rows[0]["m"] or 0) + 1
    return f"S{pos}", pos


def add_resource(wid: str, kind: str, name: str, origin: str = "", stored_path: str = "") -> dict:
    rid = new_id("r_")
    sid, pos = next_source_id(wid)
    x("""INSERT INTO resources(id,workspace_id,source_id,kind,name,origin,stored_path,status,position,created_at)
         VALUES(?,?,?,?,?,?,?,'queued',?,?)""",
      (rid, wid, sid, kind, name, origin, stored_path, pos, time.time()))
    touch_workspace(wid)
    return get_resource(rid)  # type: ignore[return-value]


def _res(r: dict | None) -> dict | None:
    if r:
        r["warnings"] = json.loads(r.get("warnings") or "[]")
        r["stats"] = json.loads(r.get("stats") or "{}")
    return r


def get_resource(rid: str) -> dict | None:
    return _res(q1("SELECT * FROM resources WHERE id=?", (rid,)))


def list_resources(wid: str) -> list[dict]:
    return [_res(r) for r in q("SELECT * FROM resources WHERE workspace_id=? ORDER BY position", (wid,))]  # type: ignore[misc]


def set_resource_status(rid: str, status: str, error: str = "", warnings: list[str] | None = None,
                        stats: dict | None = None) -> None:
    x("UPDATE resources SET status=?, error=?, warnings=?, stats=? WHERE id=?",
      (status, error, json.dumps(warnings or []), json.dumps(stats or {}), rid))


def delete_resource(rid: str) -> None:
    x("DELETE FROM units WHERE resource_id=?", (rid,))
    x("DELETE FROM resources WHERE id=?", (rid,))


def replace_units(rid: str, wid: str, units: list[dict]) -> None:
    with conn() as c:
        c.execute("DELETE FROM units WHERE resource_id=?", (rid,))
        c.executemany(
            "INSERT INTO units(id,resource_id,workspace_id,idx,text,ctx,label,page,t_start,kind) VALUES(?,?,?,?,?,?,?,?,?,?)",
            [(u["id"], rid, wid, u["idx"], u["text"], u.get("ctx", ""), u.get("label", ""),
              u.get("page"), u.get("t_start"), u.get("kind", "text")) for u in units])


def list_units(wid: str) -> list[dict]:
    return q("""SELECT u.*, r.source_id AS source_id, r.name AS source_name, r.kind AS source_kind
                FROM units u JOIN resources r ON r.id=u.resource_id
                WHERE u.workspace_id=? AND r.status='ready'
                ORDER BY r.position, u.idx""", (wid,))


def units_for_resource(rid: str, limit: int = 400) -> list[dict]:
    return q("SELECT * FROM units WHERE resource_id=? ORDER BY idx LIMIT ?", (rid, limit))


# ---- reports / jobs -------------------------------------------------------
def save_report(wid: str, report: dict) -> None:
    x("UPDATE workspaces SET report_json=?, report_at=?, updated_at=? WHERE id=?",
      (json.dumps(report), time.time(), time.time(), wid))


def load_report(wid: str) -> dict | None:
    r = q1("SELECT report_json FROM workspaces WHERE id=?", (wid,))
    return json.loads(r["report_json"]) if r and r["report_json"] else None


def set_job(wid: str, state: dict) -> None:
    x("INSERT INTO jobs(workspace_id,state,updated_at) VALUES(?,?,?) "
      "ON CONFLICT(workspace_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
      (wid, json.dumps(state), time.time()))


def get_job(wid: str) -> dict | None:
    r = q1("SELECT state FROM jobs WHERE workspace_id=?", (wid,))
    return json.loads(r["state"]) if r else None
