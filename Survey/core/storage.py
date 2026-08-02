"""세션별 자산(이미지·음성) 파일 저장/조회/삭제."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import IO

ASSETS_ROOT = Path(__file__).resolve().parent.parent / "data" / "sessions"

# 허용 확장자 (Streamlit에서 1차 필터, 여기서 2차 확인)
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
ALLOWED_AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".ogg", ".flac"}


def session_dir(session_id: str) -> Path:
    p = ASSETS_ROOT / session_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _safe_ext(filename: str, allowed: set[str]) -> str:
    ext = Path(filename).suffix.lower()
    if ext not in allowed:
        raise ValueError(f"허용되지 않은 확장자: {ext}")
    return ext


def save_image(session_id: str, uploaded_file) -> Path:
    """Streamlit UploadedFile 객체를 받아 front{ext}로 저장."""
    ext = _safe_ext(uploaded_file.name, ALLOWED_IMAGE_EXTS)
    dest = session_dir(session_id) / f"front{ext}"
    with dest.open("wb") as f:
        f.write(uploaded_file.getbuffer())
    return dest


def save_voice(session_id: str, uploaded_file) -> Path:
    ext = _safe_ext(uploaded_file.name, ALLOWED_AUDIO_EXTS)
    dest = session_dir(session_id) / f"voice{ext}"
    with dest.open("wb") as f:
        f.write(uploaded_file.getbuffer())
    return dest


def find_image(session_id: str) -> Path | None:
    for ext in ALLOWED_IMAGE_EXTS:
        p = ASSETS_ROOT / session_id / f"front{ext}"
        if p.exists():
            return p
    return None


def find_voice(session_id: str) -> Path | None:
    for ext in ALLOWED_AUDIO_EXTS:
        p = ASSETS_ROOT / session_id / f"voice{ext}"
        if p.exists():
            return p
    return None


def purge_assets(session_id: str) -> None:
    """세션 폴더 자체를 통째로 삭제."""
    folder = ASSETS_ROOT / session_id
    if folder.exists():
        shutil.rmtree(folder, ignore_errors=True)
