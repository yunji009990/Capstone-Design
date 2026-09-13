"""Durable model jobs shared by the registration web and its separate worker."""
from __future__ import annotations

import json
import time
from contextlib import contextmanager

from . import database

SCHEMA = """
CREATE TABLE IF NOT EXISTS model_jobs (
    session_id TEXT PRIMARY KEY, state TEXT NOT NULL, owner TEXT,
    lease_until REAL NOT NULL DEFAULT 0, updated_at REAL NOT NULL,
    steps_json TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS model_worker_status (
    worker_id TEXT PRIMARY KEY, heartbeat REAL NOT NULL,
    active_session TEXT, configured INTEGER NOT NULL
);
"""


@contextmanager
def connect():
    with database.connect() as conn:
        conn.executescript(SCHEMA)
        yield conn


def enqueue(session_id):
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        person = conn.execute("SELECT status, has_image FROM sessions WHERE session_id=?",
                              (session_id,)).fetchone()
        if not person or person["status"] == "deleted":
            return "no-session"
        if not person["has_image"]:
            return "no-image"
        job = conn.execute("SELECT * FROM model_jobs WHERE session_id=?", (session_id,)).fetchone()
        if job and job["state"] in ("queued", "running", "ready", "submission_unknown"):
            return job["state"]
        conn.execute("""INSERT INTO model_jobs(session_id,state,updated_at) VALUES (?,'queued',?)
            ON CONFLICT(session_id) DO UPDATE SET state='queued',owner=NULL,lease_until=0,
                updated_at=excluded.updated_at,error=''""", (session_id, time.time()))
        conn.execute("UPDATE sessions SET model_status='queued',model_error='' WHERE session_id=?",
                     (session_id,))
        return "queued"


def get_job(session_id):
    with connect() as conn:
        row = conn.execute("SELECT * FROM model_jobs WHERE session_id=?", (session_id,)).fetchone()
    if row is None:
        return None
    data = dict(row)
    data["steps"] = json.loads(data.pop("steps_json"))
    return data


def claim(owner, lease_seconds=90):
    now = time.time()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("""SELECT j.session_id FROM model_jobs j JOIN sessions s
            ON s.session_id=j.session_id WHERE s.status!='deleted'
            AND (j.state='queued' OR (j.state='running' AND j.lease_until<?))
            ORDER BY j.updated_at LIMIT 1""", (now,)).fetchone()
        if row is None:
            return None
        sid = row["session_id"]
        conn.execute("""UPDATE model_jobs SET state='running',owner=?,lease_until=?,updated_at=?
                        WHERE session_id=?""", (owner, now + lease_seconds, now, sid))
    return get_job(sid)


def heartbeat(owner, session_id=None, configured=False, lease_seconds=90):
    now = time.time()
    with connect() as conn:
        conn.execute("INSERT OR REPLACE INTO model_worker_status VALUES (?,?,?,?)",
                     (owner, now, session_id, int(configured)))
        conn.execute("DELETE FROM model_worker_status WHERE heartbeat<?", (now - 86400,))
        if session_id:
            changed = conn.execute("""UPDATE model_jobs SET lease_until=?,updated_at=?
                WHERE session_id=? AND owner=? AND state='running'
                AND EXISTS (SELECT 1 FROM sessions WHERE session_id=? AND status!='deleted')""",
                (now + lease_seconds, now, session_id, owner, session_id)).rowcount
            if not changed:
                raise RuntimeError("job_lease_lost")


def save(job, owner, *, state="running", error="", model_path=None):
    now = time.time()
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute("""UPDATE model_jobs SET state=?,steps_json=?,error=?,updated_at=?
            WHERE session_id=? AND owner=? AND state='running'
            AND EXISTS (SELECT 1 FROM sessions WHERE session_id=? AND status!='deleted')""",
            (state, json.dumps(job["steps"], ensure_ascii=False), error, now,
             job["session_id"], owner, job["session_id"])).rowcount
        if not changed:
            raise RuntimeError("job_lease_lost")
        visible = {"running": "processing", "blocked": "stub",
                   "submission_unknown": "failed"}.get(state, state)
        conn.execute("""UPDATE sessions SET model_status=?,model_error=?,
            model_glb_path=COALESCE(?,model_glb_path),model_updated_at=? WHERE session_id=?""",
            (visible, error, model_path, str(now), job["session_id"]))


def status():
    now = time.time()
    with connect() as conn:
        workers = conn.execute("""SELECT heartbeat,configured,active_session FROM model_worker_status
                                WHERE heartbeat>?""", (now - 45,)).fetchall()
        jobs = dict(conn.execute("SELECT state,COUNT(*) FROM model_jobs GROUP BY state").fetchall())
    return {"online": bool(workers), "configured": any(row["configured"] for row in workers),
            "active_jobs": sum(bool(row["active_session"]) for row in workers), "jobs": jobs}
