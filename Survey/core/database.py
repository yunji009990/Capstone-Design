"""SQLite 저장소. 세션 메타 + JSON 페이로드만 보관하고,
큰 자산(이미지·음성)은 storage 모듈이 파일시스템에 따로 저장한다."""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    status              TEXT NOT NULL DEFAULT 'submitted',   -- submitted | used | deleted
    consent_image       INTEGER NOT NULL DEFAULT 0,
    consent_voice       INTEGER NOT NULL DEFAULT 0,
    consent_understand  INTEGER NOT NULL DEFAULT 0,
    bereavement_weeks   INTEGER,                              -- 사별 후 경과 주
    payload_json        TEXT NOT NULL,                        -- 설문 응답 전체
    has_image           INTEGER NOT NULL DEFAULT 0,
    has_voice           INTEGER NOT NULL DEFAULT 0,
    deleted_at          TEXT,
    -- 3D 모델 (Meshy 등) 작업 추적 ───────────────────
    -- model_status: stub | queued | processing | ready | failed
    --   stub      = API 키 없거나 미설정 (실제 호출 안 함)
    --   queued    = 작업 대기
    --   processing= Meshy 작업 진행 중
    --   ready     = .glb 파일 저장 완료, Unity가 가져갈 수 있음
    --   failed    = 실패. model_error에 사유
    model_status        TEXT NOT NULL DEFAULT 'stub',
    meshy_task_id       TEXT,
    model_glb_path      TEXT,                                  -- 절대 경로
    model_error         TEXT,
    model_updated_at    TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_created_at ON sessions(created_at);
CREATE INDEX IF NOT EXISTS idx_sessions_status     ON sessions(status);
"""

# 기존 DB가 있을 때 누락 컬럼만 살짝 채워주는 부드러운 마이그레이션.
# CREATE TABLE IF NOT EXISTS 이후에 실행되며, 모두 try/except로 감싸 멱등.
_MIGRATIONS: list[str] = [
    "ALTER TABLE sessions ADD COLUMN model_status TEXT NOT NULL DEFAULT 'stub'",
    "ALTER TABLE sessions ADD COLUMN meshy_task_id TEXT",
    "ALTER TABLE sessions ADD COLUMN model_glb_path TEXT",
    "ALTER TABLE sessions ADD COLUMN model_error TEXT",
    "ALTER TABLE sessions ADD COLUMN model_updated_at TEXT",
]

# 마이그레이션으로 추가된 컬럼들의 인덱스는 마이그레이션 뒤에 따로 생성.
_POST_MIGRATION_INDEX = "CREATE INDEX IF NOT EXISTS idx_sessions_model ON sessions(model_status)"


def _apply_migrations(conn: sqlite3.Connection) -> None:
    for stmt in _MIGRATIONS:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass  # 컬럼 이미 존재 — 무시
    try:
        conn.execute(_POST_MIGRATION_INDEX)
    except sqlite3.OperationalError:
        pass


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _apply_migrations(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def next_session_id() -> str:
    """yyyymmdd-NNN 형식. 같은 날 내 순번 자동 증가."""
    today = datetime.now().strftime("%Y%m%d")
    with connect() as c:
        row = c.execute(
            "SELECT session_id FROM sessions WHERE session_id LIKE ? ORDER BY session_id DESC LIMIT 1",
            (f"{today}-%",),
        ).fetchone()
    if row is None:
        return f"{today}-001"
    last_n = int(row["session_id"].split("-")[-1])
    return f"{today}-{last_n + 1:03d}"


def insert_session(
    session_id: str,
    payload: dict[str, Any],
    consent_image: bool,
    consent_voice: bool,
    consent_understand: bool,
    bereavement_weeks: int | None,
    has_image: bool,
    has_voice: bool,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as c:
        c.execute(
            """
            INSERT INTO sessions
                (session_id, created_at, updated_at, status,
                 consent_image, consent_voice, consent_understand,
                 bereavement_weeks, payload_json, has_image, has_voice)
            VALUES (?, ?, ?, 'submitted', ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                session_id, now, now,
                int(consent_image), int(consent_voice), int(consent_understand),
                bereavement_weeks,
                json.dumps(payload, ensure_ascii=False, indent=2),
                int(has_image), int(has_voice),
            ),
        )


def get_session(session_id: str) -> dict[str, Any] | None:
    with connect() as c:
        row = c.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["payload"] = json.loads(d.pop("payload_json"))
    return d


def list_sessions(include_deleted: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT session_id, created_at, status, has_image, has_voice, bereavement_weeks FROM sessions"
    if not include_deleted:
        sql += " WHERE status != 'deleted'"
    sql += " ORDER BY created_at DESC"
    with connect() as c:
        return [dict(r) for r in c.execute(sql).fetchall()]


def soft_delete(session_id: str) -> None:
    """본인 요청으로 즉시 폐기. 자산 파일은 storage.purge_assets()가 따로 지운다."""
    now = datetime.now().isoformat(timespec="seconds")
    with connect() as c:
        c.execute(
            "UPDATE sessions SET status='deleted', deleted_at=?, payload_json='{}' WHERE session_id=?",
            (now, session_id),
        )


def hard_delete(session_id: str) -> None:
    """관리자가 영구 삭제."""
    with connect() as c:
        c.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))


def mark_used(session_id: str) -> None:
    with connect() as c:
        c.execute("UPDATE sessions SET status='used', updated_at=? WHERE session_id=?",
                  (datetime.now().isoformat(timespec="seconds"), session_id))


# ── 3D 모델 작업 상태 관리 ─────────────────────────────────────
def set_model_status(
    session_id: str,
    status: str,
    *,
    meshy_task_id: str | None = None,
    model_glb_path: str | None = None,
    model_error: str | None = None,
) -> None:
    """상태 + 부수 필드 부분 업데이트. None인 인자는 SET에서 제외."""
    fields = ["model_status = ?", "model_updated_at = ?"]
    values: list[Any] = [status, datetime.now().isoformat(timespec="seconds")]
    if meshy_task_id is not None:
        fields.append("meshy_task_id = ?"); values.append(meshy_task_id)
    if model_glb_path is not None:
        fields.append("model_glb_path = ?"); values.append(model_glb_path)
    if model_error is not None:
        fields.append("model_error = ?"); values.append(model_error)
    values.append(session_id)
    with connect() as c:
        c.execute(f"UPDATE sessions SET {', '.join(fields)} WHERE session_id = ?", values)


def list_sessions_needing_model() -> list[dict[str, Any]]:
    """대기/실패 상태이며 이미지가 있는 세션. 재시도용."""
    with connect() as c:
        rows = c.execute(
            """
            SELECT * FROM sessions
            WHERE has_image = 1 AND status != 'deleted'
              AND model_status IN ('stub', 'queued', 'failed')
            ORDER BY created_at DESC
            """
        ).fetchall()
    return [dict(r) for r in rows]
