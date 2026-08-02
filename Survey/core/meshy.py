"""Meshy AI Image-to-3D 클라이언트.

설계 원칙
─────────
- API 키가 secrets에 없으면 자동으로 stub 모드 (네트워크 호출 X).
- 호출부(jobs.py)는 키 유무를 신경 쓰지 않고 동일 인터페이스 사용.
- 외부 의존성은 표준 라이브러리만 (urllib). Streamlit 환경에 별도 패키지 설치 불필요.

사용 흐름
─────────
    from core import meshy
    client = meshy.MeshyClient.from_secrets()   # 키 없으면 stub 모드 인스턴스
    task_id = client.submit_image_to_3d(image_path)
    status, glb_url = client.wait_until_ready(task_id)
    client.download_glb(glb_url, dest_path)

실제 API 키 채울 때
───────────────────
    .streamlit/secrets.toml 에 한 줄만 추가:
        meshy_api_key = "msy-...."
    그 뒤 Streamlit만 재시작하면 자동으로 실모드로 전환.

참고 (Meshy 공식 API): https://docs.meshy.ai/api/image-to-3d
"""
from __future__ import annotations

import os

import json
import logging
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger("meshy")

# Meshy Image-to-3D 엔드포인트 (v1 기준). 실제 키 받으면 docs 확인 후 수정 가능.
API_BASE = "https://api.meshy.ai/openapi/v1"
ENDPOINT_IMAGE_TO_3D = f"{API_BASE}/image-to-3d"


@dataclass
class TaskResult:
    status: str               # "ready" | "failed" | "stub"
    glb_url: str | None = None
    error: str | None = None


class MeshyClient:
    """API 키 없으면 stub 모드로 동작 — DB만 'stub' 상태로 표시하고 종료."""

    def __init__(self, api_key: str | None):
        self.api_key = (api_key or "").strip() or None

    @classmethod
    def from_secrets(cls) -> "MeshyClient":
        # 환경변수를 먼저 본다. Streamlit 밖(FastAPI 등)에서도 쓰기 위해서다.
        api_key: str | None = os.environ.get("MESHY_API_KEY") or None
        if not api_key:
            try:
                import streamlit as st  # 지연 임포트
                api_key = st.secrets.get("meshy_api_key", None)
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
        topology: str = "triangle",
        target_polycount: int = 30_000,
        enable_pbr: bool = True,
        ai_model: str = "meshy-4",
    ) -> str:
        """이미지로 3D 모델 생성 작업을 요청. task_id 반환.

        stub 모드면 빈 문자열 반환 (호출부에서 status='stub'로 기록).
        """
        if self.is_stub:
            log.info("[stub] submit_image_to_3d 건너뜀 (API 키 없음)")
            return ""

        # Meshy는 image_url 또는 base64를 받는다. 여기서는 멀티파트 대신
        # data URL로 묶어서 보내는 가장 단순한 경로 사용.
        import base64
        data_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
        mime = _guess_mime(image_path.suffix)
        image_url = f"data:{mime};base64,{data_b64}"

        body = {
            "image_url": image_url,
            "topology": topology,
            "target_polycount": target_polycount,
            "enable_pbr": enable_pbr,
            "ai_model": ai_model,
        }
        resp = self._post(ENDPOINT_IMAGE_TO_3D, body)
        task_id = resp.get("result") or resp.get("task_id") or ""
        if not task_id:
            raise RuntimeError(f"Meshy 응답에서 task_id를 찾지 못함: {resp}")
        return task_id

    def get_task(self, task_id: str) -> dict[str, Any]:
        if self.is_stub:
            return {"status": "STUB"}
        return self._get(f"{ENDPOINT_IMAGE_TO_3D}/{task_id}")

    def wait_until_ready(
        self,
        task_id: str,
        *,
        poll_interval_sec: float = 5.0,
        timeout_sec: float = 900.0,
    ) -> TaskResult:
        """ready/failed가 될 때까지 폴링. stub 모드에서는 즉시 stub 반환."""
        if self.is_stub:
            return TaskResult(status="stub")

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            data = self.get_task(task_id)
            status = (data.get("status") or "").upper()
            if status == "SUCCEEDED":
                model_urls = data.get("model_urls") or {}
                glb = model_urls.get("glb")
                if not glb:
                    return TaskResult(status="failed", error="응답에 glb URL 없음")
                return TaskResult(status="ready", glb_url=glb)
            if status in {"FAILED", "EXPIRED", "CANCELED"}:
                return TaskResult(status="failed", error=data.get("task_error") or status)
            time.sleep(poll_interval_sec)
        return TaskResult(status="failed", error="timeout")

    def download_glb(self, url: str, dest: Path) -> Path:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(url) as r, dest.open("wb") as f:
            shutil.copyfileobj(r, f)
        return dest

    # ── HTTP 헬퍼 ──────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Meshy POST 실패 [{e.code}]: {e.read().decode('utf-8', errors='replace')}")

    def _get(self, url: str) -> dict[str, Any]:
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"Meshy GET 실패 [{e.code}]: {e.read().decode('utf-8', errors='replace')}")


def _guess_mime(suffix: str) -> str:
    return {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}.get(
        suffix.lower(), "application/octet-stream"
    )
