"""Registration domain: CPU-only person, avatar, and read-only dialogue data API."""
from __future__ import annotations

import hmac
import hashlib
import io
import json
import os
import shutil
import time
import uuid
import wave
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse


def create_app(root=None, token=None, persona_token=None):
    root = Path(root or os.environ.get("SESSION_DIR", str(Path.home() / "server/sessions"))).resolve()
    token = os.environ.get("SESSION_TOKEN", "") if token is None else token
    persona_token = os.environ.get("PERSONA_READ_TOKEN", "") if persona_token is None else persona_token
    current_file = root / "current.json"
    started = time.monotonic()
    app = FastAPI(title="Persona and avatar service")

    def auth(value):
        if token and not hmac.compare_digest(value, token):
            raise HTTPException(403, "인증 실패")

    def persona_auth(value):
        # This credential can only read the dialogue bundle, never mutate people.
        if not persona_token or not hmac.compare_digest(value, persona_token):
            raise HTTPException(403, "인물 조회 인증 실패")

    def folder(sid):
        if (not sid or len(sid) > 100 or sid.startswith(".")
                or any(c in sid for c in "/\\\x00")):
            raise HTTPException(400, "잘못된 세션 ID")
        path = (root / sid).resolve()
        if path.parent != root or (root / sid).is_symlink():
            raise HTTPException(400, "잘못된 세션 경로")
        return path

    def atomic(path, data):
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if path.is_symlink():
            raise HTTPException(400, "잘못된 파일 경로")
        tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".part")
        try:
            with open(tmp, "xb") as out:
                os.chmod(tmp, 0o600)
                out.write(data)
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)

    def registered():
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink()
                      and (p / "persona.md").is_file())

    def current():
        if current_file.exists():
            sid = json.loads(current_file.read_text(encoding="utf-8")).get("session")
            return sid if sid and (folder(sid) / "persona.md").is_file() else None
        # 예전 자료가 남아 있어도 임의로 체험 인물로 선택하지 않는다.
        return None

    def set_current(sid):
        atomic(current_file, json.dumps({"session": sid}).encode())

    def read_asset(parent, name, limit):
        path = parent / name
        if path.is_symlink() or path.resolve().parent != parent:
            raise HTTPException(400, "잘못된 자료 경로")
        if not path.is_file():
            return None
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(413, "자료 크기 제한 초과")
        return raw

    def persona_bundle(sid):
        parent = folder(sid)
        text = {}
        for key in ("persona", "knowledge", "rules"):
            raw = read_asset(parent, key + ".md", 131072)
            try:
                text[key] = raw.decode("utf-8").strip() if raw else ""
            except UnicodeDecodeError:
                raise HTTPException(422, "인물 정보 인코딩 오류")
        if not text["persona"]:
            raise HTTPException(404, "등록되지 않은 인물입니다")
        voice = read_asset(parent, "voice.wav", 30 * 1024 * 1024)
        voice_hash = hashlib.sha256(voice).hexdigest() if voice else None
        identity = {**text, "voice_sha256": voice_hash}
        revision = hashlib.sha256(json.dumps(identity, sort_keys=True,
            ensure_ascii=False).encode("utf-8")).hexdigest()
        return {"schema_version": 1, "session": sid, "revision": revision,
                **text, "voice_sha256": voice_hash}

    @app.get("/internal/current-persona")
    def current_persona(x_persona_token: str = Header("")):
        persona_auth(x_persona_token)
        sid = current()
        if not sid:
            raise HTTPException(404, "등록된 인물이 없습니다")
        return persona_bundle(sid)

    @app.get("/internal/personas/{sid}")
    def get_persona(sid: str, x_persona_token: str = Header("")):
        persona_auth(x_persona_token)
        return persona_bundle(sid)

    @app.get("/internal/personas/{sid}/voice.wav")
    def get_persona_voice(sid: str, sha256: str, x_persona_token: str = Header("")):
        persona_auth(x_persona_token)
        raw = read_asset(folder(sid), "voice.wav", 30 * 1024 * 1024)
        if raw is None:
            raise HTTPException(404, "참조 음성이 없습니다")
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), sha256):
            raise HTTPException(409, "참조 음성이 변경되었습니다. 체험을 다시 준비해 주세요")
        from fastapi.responses import Response
        return Response(raw, media_type="audio/wav", headers={"Cache-Control": "no-store"})

    async def upload(file, limit):
        raw = await file.read(limit + 1)
        if len(raw) > limit:
            raise HTTPException(413, "파일이 너무 큽니다")
        return raw

    @app.get("/health")
    def health():
        sid = current()
        return {"status": "ready", "service": "sessions", "device": "cpu",
                "registered": len(registered()), "current": sid,
                "ready_to_talk": bool(sid), "uptime_sec": time.monotonic() - started}

    @app.get("/session/current")
    def session_current(x_token: str = Header("")):
        auth(x_token)
        sid = current()
        return {"session": sid, "has_model": bool(sid and (folder(sid) / "model.glb").is_file())}

    @app.post("/session/start")
    async def start(persona: str = Form(...), knowledge: str = Form(""),
                    rules: str = Form(""), session: str = Form(""),
                    voice: UploadFile = File(...), model: UploadFile = File(None),
                    x_token: str = Header("")):
        auth(x_token)
        if session.strip():
            raise HTTPException(400, "세션 ID는 서버가 자동으로 발급합니다. 직접 지정할 수 없습니다.")
        if not persona.strip() or any(len(t.encode()) > 131072 for t in (persona, knowledge, rules)):
            raise HTTPException(400, "인물 설정이 비어 있거나 너무 깁니다")
        raw = await upload(voice, 30 * 1024 * 1024)
        try:
            with wave.open(io.BytesIO(raw)) as wav:
                duration = wav.getnframes() / wav.getframerate()
                if duration < 3 or wav.getcomptype() != "NONE":
                    raise ValueError("참조 음성은 3초 이상의 PCM WAV여야 합니다")
        except (wave.Error, EOFError, ValueError) as exc:
            raise HTTPException(400, str(exc) or "손상된 WAV") from exc
        mesh = await upload(model, 100 * 1024 * 1024) if model and model.filename else None
        if mesh is not None and not mesh.startswith(b"glTF"):
            raise HTTPException(400, "GLB 모델이 아닙니다")
        # UUID 충돌이나 동시 등록에서도 기존 인물 폴더를 덮어쓰지 않는다.
        for _ in range(8):
            sid = uuid.uuid4().hex
            path = folder(sid)
            try:
                path.mkdir(parents=True, exist_ok=False, mode=0o700)
                break
            except FileExistsError:
                continue
        else:
            raise HTTPException(503, "새 세션 ID를 발급하지 못했습니다. 다시 시도해 주세요.")
        for name, data in (("voice.wav", raw), ("persona.md", persona.strip().encode()),
                           ("knowledge.md", knowledge.strip().encode()), ("rules.md", rules.strip().encode())):
            atomic(path / name, data)
        if mesh is not None:
            atomic(path / "model.glb", mesh)
        set_current(sid)
        return {"session": sid, "voice_sec": round(duration, 1),
                "has_model": (path / "model.glb").is_file()}

    @app.post("/session/{sid}/model")
    async def put_model(sid: str, model: UploadFile = File(...), x_token: str = Header("")):
        auth(x_token)
        path = folder(sid)
        if not (path / "persona.md").is_file():
            raise HTTPException(404, "등록되지 않은 세션")
        raw = await upload(model, 100 * 1024 * 1024)
        if not raw.startswith(b"glTF"):
            raise HTTPException(400, "GLB 모델이 아닙니다")
        atomic(path / "model.glb", raw)
        return {"ok": True}

    @app.get("/session/{sid}/model.glb")
    def get_model(sid: str, x_token: str = Header("")):
        auth(x_token)
        parent = folder(sid)
        path = parent / "model.glb"
        if path.is_symlink() or not path.is_file():
            raise HTTPException(404, "모델이 아직 없습니다")
        return FileResponse(path, media_type="model/gltf-binary")

    @app.post("/session/end")
    def end(session: str = Form(...), x_token: str = Header("")):
        auth(x_token)
        path = folder(session)
        if current() == session:
            set_current(None)
        if path.is_dir():
            shutil.rmtree(path)  # explicit session/end, validated child only
        return {"ok": True}

    return app


app = create_app()
