"""Tripo AI Image-to-3D 클라이언트.

설계 원칙
─────────
- API 키가 secrets에 없으면 자동으로 stub 모드 (네트워크 호출 X).
- 호출부(jobs.py)는 키 유무를 신경 쓰지 않고 동일 인터페이스 사용 (MeshyClient와 호환).
- 외부 의존성은 표준 라이브러리만 (urllib).

사용 흐름
─────────
    from core import tripo
    client = tripo.TripoClient.from_secrets()      # 키 없으면 stub 모드
    task_id = client.submit_image_to_3d(image_path)
    result = client.wait_until_ready(task_id)
    if result.status == "ready":
        client.download_glb(result.glb_url, dest_path)

실제 API 키 채울 때
───────────────────
    .streamlit/secrets.toml 에 한 줄만 추가:
        tripo_api_key = "tsk_..."

참고 (Tripo 공식 API): https://platform.tripo3d.ai/docs/
"""
from __future__ import annotations

import os

import json
import logging
import mimetypes
import shutil
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("tripo")

API_BASE = "https://api.tripo3d.ai/v2/openapi"
ENDPOINT_UPLOAD = f"{API_BASE}/upload/sts"
ENDPOINT_TASK = f"{API_BASE}/task"


def _ssl_context() -> ssl.SSLContext:
    """Windows 환경에서 certifi의 CA 번들을 명시적으로 사용.
    certifi가 없으면 시스템 기본값으로 폴백."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


@dataclass
class TaskResult:
    status: str               # "ready" | "failed" | "stub"
    glb_url: str | None = None
    error: str | None = None


class TripoClient:
    """API 키 없으면 stub 모드로 동작 — DB만 'stub' 상태로 표시하고 종료."""

    def __init__(self, api_key: str | None):
        self.api_key = (api_key or "").strip() or None

    @classmethod
    def from_secrets(cls) -> "TripoClient":
        # 환경변수를 먼저 본다. Streamlit 밖(FastAPI 등)에서도 쓰기 위해서다.
        api_key: str | None = os.environ.get("TRIPO_API_KEY") or None
        if not api_key:
            try:
                import streamlit as st
                api_key = st.secrets.get("tripo_api_key", None)
            except Exception:
                pass
        return cls(api_key)

    @property
    def is_stub(self) -> bool:
        return self.api_key is None

    # ── 실제 호출 ───────────────────────────────────────────────
    def submit_image_to_3d(
        self,
        image_path: Path,
        *,
        face_limit: int = 30_000,
        texture: bool = True,
        pbr: bool = True,
        model_version: str = "v2.5-20250123",
    ) -> str:
        """이미지 업로드 → image_to_model 작업 생성. task_id 반환."""
        if self.is_stub:
            log.info("[stub] submit_image_to_3d 건너뜀 (API 키 없음)")
            return ""

        # 1) 이미지 업로드 → image_token
        image_token = self._upload_image(image_path)
        log.info(f"Tripo image uploaded: token={image_token[:8]}...")

        # 2) 작업 생성
        ext = image_path.suffix.lower().lstrip(".")
        if ext == "jpeg":
            ext = "jpg"
        body = {
            "type": "image_to_model",
            "file": {"type": ext, "file_token": image_token},
            "model_version": model_version,
            "face_limit": face_limit,
            "texture": texture,
            "pbr": pbr,
        }
        resp = self._post_json(ENDPOINT_TASK, body)
        data = resp.get("data") or {}
        task_id = data.get("task_id") or ""
        if not task_id:
            raise RuntimeError(f"Tripo 응답에서 task_id를 찾지 못함: {resp}")
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any]:
        if self.is_stub:
            return {"data": {"status": "stub"}}
        return self._get(f"{ENDPOINT_TASK}/{task_id}")

    def wait_until_ready(
        self,
        task_id: str,
        *,
        poll_interval_sec: float = 5.0,
        timeout_sec: float = 900.0,
    ) -> TaskResult:
        if self.is_stub:
            return TaskResult(status="stub")

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            resp = self.get_task(task_id)
            data = resp.get("data") or {}
            status = (data.get("status") or "").lower()
            if status == "success":
                output = data.get("output") or {}
                # PBR 모델이 있으면 우선, 없으면 기본 모델
                glb = output.get("pbr_model") or output.get("model")
                if not glb:
                    return TaskResult(status="failed", error=f"응답에 model URL 없음: {output}")
                return TaskResult(status="ready", glb_url=glb)
            if status in {"failed", "banned", "expired", "cancelled", "canceled"}:
                err = data.get("error") or data.get("message") or status
                return TaskResult(status="failed", error=str(err))
            # queued | running → 계속 폴링
            progress = data.get("progress")
            if progress is not None:
                log.info(f"[{task_id}] status={status} progress={progress}")
            time.sleep(poll_interval_sec)
        return TaskResult(status="failed", error="timeout")

    def download_glb(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url, timeout=300, context=_SSL_CTX) as r, dest.open("wb") as f:
            shutil.copyfileobj(r, f)
        return dest

    # ── HTTP 헬퍼 ──────────────────────────────────────────────
    def _headers_json(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers_json(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60, context=_SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Tripo POST 실패 [{e.code}]: {e.read().decode('utf-8', errors='replace')}")

    def _get(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self.api_key}", "Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Tripo GET 실패 [{e.code}]: {e.read().decode('utf-8', errors='replace')}")

    def _upload_image(self, image_path: Path) -> str:
        """multipart/form-data로 이미지 업로드 → image_token 반환."""
        boundary = uuid.uuid4().hex
        mime, _ = mimetypes.guess_type(str(image_path))
        mime = mime or "application/octet-stream"

        body = b"".join([
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="file"; filename="{image_path.name}"\r\n'.encode(),
            f"Content-Type: {mime}\r\n\r\n".encode(),
            image_path.read_bytes(),
            f"\r\n--{boundary}--\r\n".encode(),
        ])

        req = urllib.request.Request(
            ENDPOINT_UPLOAD,
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=120, context=_SSL_CTX) as r:
                resp = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Tripo upload 실패 [{e.code}]: {e.read().decode('utf-8', errors='replace')}")

        data = resp.get("data") or {}
        token = data.get("image_token") or data.get("file_token")
        if not token:
            raise RuntimeError(f"Tripo upload 응답에 image_token 없음: {resp}")
        return token
