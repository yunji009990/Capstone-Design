"""Read-only HTTP boundary between registration and dialogue.

No registration database, file layout, or model-generation code is imported here.
The caller keeps a returned bundle for the lifetime of one experience.
"""
from __future__ import annotations

import hashlib
import json
import re

import httpx


class PersonaClient:
    def __init__(self, base_url, token, client=None):
        if not token:
            raise ValueError("DIALOGUE_PERSONA_TOKEN을 설정해 주세요")
        self.base_url = base_url.rstrip("/")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("잘못된 인물 API 주소")
        self.token = token
        self.owns_client = client is None
        self.client = client or httpx.AsyncClient(timeout=httpx.Timeout(15, connect=3),
                                                 follow_redirects=False, trust_env=False)

    @staticmethod
    def valid_session(session):
        if (not isinstance(session, str) or not session or len(session) > 100
                or session.startswith(".") or any(c in session for c in "/\\\x00?#%")):
            raise ValueError("잘못된 인물 ID")
        return session

    async def _read(self, path, limit, params=None):
        try:
            async with self.client.stream("GET", self.base_url + path, params=params,
                    headers={"X-Persona-Token": self.token}) as response:
                if response.status_code == 403:
                    raise ValueError("인물 API 조회 인증을 확인해 주세요")
                if response.status_code == 404:
                    raise ValueError("등록된 인물 자료가 없습니다")
                if response.status_code == 409:
                    raise ValueError("인물 자료가 변경되었습니다. 체험을 다시 준비해 주세요")
                if response.status_code != 200:
                    raise ValueError("인물 API가 자료를 제공하지 못했습니다")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise ValueError("인물 API 응답 크기 제한 초과")
                return bytes(data)
        except httpx.HTTPError as exc:
            # Do not echo response bodies, credentials, or private URLs to Unity.
            raise ValueError("인물 등록 서비스에 연결할 수 없습니다") from exc

    async def get(self, session=None):
        path = ("/internal/current-persona" if session is None else
                "/internal/personas/" + self.valid_session(session))
        try:
            data = json.loads(await self._read(path, 512 * 1024))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("잘못된 인물 API 응답") from exc
        if not isinstance(data, dict) or data.get("schema_version") != 1:
            raise ValueError("지원하지 않는 인물 API 규격")
        self.valid_session(data.get("session"))
        if session is not None and data["session"] != session:
            raise ValueError("요청한 인물과 응답이 일치하지 않습니다")
        for key in ("persona", "knowledge", "rules"):
            value = data.get(key)
            if not isinstance(value, str) or len(value.encode("utf-8")) > 131072:
                raise ValueError("잘못된 인물 정보")
        if not data["persona"].strip():
            raise ValueError("인물 설정이 비어 있습니다")
        for key in ("revision", "voice_sha256"):
            value = data.get(key)
            if key == "voice_sha256" and value is None:
                continue
            if not isinstance(value, str) or not re.fullmatch(r"[a-f0-9]{64}", value):
                raise ValueError("잘못된 인물 자료 버전")
        return data

    async def voice(self, bundle):
        expected = bundle.get("voice_sha256")
        if not expected:
            raise ValueError("이 인물에 참조 음성이 없습니다")
        sid = self.valid_session(bundle["session"])
        raw = await self._read("/internal/personas/" + sid + "/voice.wav",
                               30 * 1024 * 1024, {"sha256": expected})
        if hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError("참조 음성 해시가 일치하지 않습니다")
        return raw, "등록 음성 · " + sid

    async def close(self):
        if self.owns_client:
            await self.client.aclose()
