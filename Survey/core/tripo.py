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
    환경변수 TRIPO_API_KEY 에 넣고 웹 백엔드를 다시 띄운다:
        $env:TRIPO_API_KEY = "tsk_..."

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

# 결과 glb 는 API 가 아니라 CDN(tripo-data.*.data.tripo3d.com)에서 받는데,
# 그쪽이 urllib 의 기본 User-Agent(`Python-urllib/3.x`)를 403 으로 막는다.
# API 호스트는 막지 않아서 제출·폴링까지 다 성공하고 마지막 내려받기만 실패한다.
_UA = "Mozilla/5.0 (compatible; dasibom/1.0)"


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
        return cls(os.environ.get("TRIPO_API_KEY") or None)

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

    # ── 자세 (리깅 → 리타겟) ────────────────────────────────────
    #
    # image_to_model 이 내주는 메시는 서 있는 자세다. 카페에 앉아 대화하는
    # 장면이라 앉은 자세가 필요한데, image_to_model 에는 자세를 지정하는
    # 파라미터가 없다(orientation 은 방향일 뿐이다). 앉은 사진을 넣으면 앉은
    # 메시가 나오긴 하지만, 유족이 가진 사진을 고를 수는 없다. 그래서 어떤
    # 사진이 들어와도 같은 자세가 나오도록 리깅 후 프리셋을 입힌다.

    def check_riggable(self, model_task_id: str) -> tuple[bool, str | None]:
        """리깅 가능 여부를 묻는다. 과금이 0 이라 항상 먼저 부른다.

        반환: (riggable, rig_type)"""
        if self.is_stub:
            return False, None
        task_id = self._submit({"type": "animate_prerigcheck",
                                "original_model_task_id": model_task_id})
        data = self._wait_raw(task_id, timeout_sec=300)
        if (data.get("status") or "").lower() != "success":
            return False, None
        out = data.get("output") or {}
        return bool(out.get("riggable")), out.get("rig_type")

    def rig_model(
        self,
        model_task_id: str,
        *,
        rig_type: str = "biped",
        model_version: str = "v1.0-20240301",
        out_format: str = "glb",
    ) -> str:
        """자동 리깅. rig task_id 반환.

        `spec` 을 인자로 받지 않고 "tripo" 로 고정한다. Unity Humanoid 자동
        매핑을 노리고 "mixamo" 로 뽑아 봤는데, 리깅 자체는 success 가 뜨지만
        이어지는 animate_retarget 이 전부 failed 로 떨어졌다. 에러 메시지도
        없이 output 이 비어서 원인이 안 보인다(preset:sit 뿐 아니라 문서에
        있는 preset:idle 도 똑같이 실패했다). 프리셋은 tripo 본 이름을 전제로
        한다. 대신 본 이름이 Mixamo 규격이 아니라 Humanoid 자동 매핑은 안 된다.

        model_version 도 v1.0-20240301 이어야 한다. v2.5 의 biped 프리셋은
        idle·walk·run·dive·climb·jump·slash·shoot·hurt·fall·turn 뿐이라
        sit 이 없다.
        """
        if self.is_stub:
            return ""
        return self._submit({
            "type": "animate_rig",
            "original_model_task_id": model_task_id,
            "model_version": model_version,
            "rig_type": rig_type,
            "spec": "tripo",
            "out_format": out_format,
        })

    def retarget_animation(
        self,
        rig_task_id: str,
        animation: str = "preset:sit",
        *,
        out_format: str = "glb",
        bake_animation: bool = True,
    ) -> str:
        """리깅된 모델에 프리셋 동작을 입힌다. retarget task_id 반환."""
        if self.is_stub:
            return ""
        return self._submit({
            "type": "animate_retarget",
            "original_model_task_id": rig_task_id,
            "animation": animation,
            "out_format": out_format,
            "bake_animation": bake_animation,
        })

    def get_task(self, task_id: str) -> dict[str, Any]:
        if self.is_stub:
            return {"data": {"status": "stub"}}
        return self._get(f"{ENDPOINT_TASK}/{task_id}")

    def _submit(self, body: dict[str, Any]) -> str:
        resp = self._post_json(ENDPOINT_TASK, body)
        task_id = (resp.get("data") or {}).get("task_id") or ""
        if not task_id:
            raise RuntimeError(f"Tripo 응답에서 task_id를 찾지 못함: {resp}")
        return task_id

    def _wait_raw(
        self,
        task_id: str,
        *,
        poll_interval_sec: float = 5.0,
        timeout_sec: float = 900.0,
    ) -> dict[str, Any]:
        """끝난 작업의 data 를 그대로 돌려준다. output 형태가 작업마다 달라
        (prerigcheck 는 riggable, 나머지는 model) wait_until_ready 로는 못 쓴다."""
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            data = self.get_task(task_id).get("data") or {}
            if (data.get("status") or "").lower() in {
                "success", "failed", "banned", "expired", "cancelled", "canceled", "unknown"
            }:
                return data
            time.sleep(poll_interval_sec)
        return {"status": "failed", "error": "timeout"}

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
        req = urllib.request.Request(url, headers={"User-Agent": _UA}, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=300, context=_SSL_CTX) as r, dest.open("wb") as f:
                shutil.copyfileobj(r, f)
        except urllib.error.HTTPError as e:
            # 다른 헬퍼들과 같은 형식으로 감싼다. 날것의 `HTTP Error 403: Forbidden`
            # 만 남으면 네 군데 중 어디서 터진 것인지 로그로 가려낼 수 없다.
            raise RuntimeError(f"Tripo 다운로드 실패 [{e.code}]: {url}")
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
