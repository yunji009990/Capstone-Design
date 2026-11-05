"""준비된 친구 자료로 세션을 시작한다. 사진 생성 작업자를 호출하지 않는다."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import wave

import httpx
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import Response

import survey_v2

LIMITS = {"survey.json": 131072, "voice.wav": 30 * 1024 * 1024,
          "model.glb": 100 * 1024 * 1024, "photo.png": 20 * 1024 * 1024,
          "photo.jpg": 20 * 1024 * 1024}


def load_bundle(directory):
    """서버가 지정한 파일만 읽고, 준비 시점 이후 변경·누락을 거절한다."""
    if not directory:
        raise HTTPException(503, "준비된 친구 자료가 없습니다. 로컬 prepare-friend 설정을 먼저 실행해 주세요.")
    root = Path(directory)
    try:
        manifest_path = root / "manifest.json"
        if root.is_symlink() or manifest_path.is_symlink() or manifest_path.stat().st_size > 16384:
            raise ValueError("invalid manifest")
        manifest_bytes = manifest_path.read_bytes()
        manifest = json.loads(manifest_bytes)
        photo = manifest["photo"]
        names = {"survey.json", "voice.wav", "model.glb", photo}
        if manifest["version"] != 1 or photo not in ("photo.jpg", "photo.png") or set(manifest["sha256"]) != names:
            raise ValueError("invalid asset list")
        files = {}
        for name in names:
            path = root / name
            if path.is_symlink() or not 0 < path.stat().st_size <= LIMITS[name]:
                raise ValueError("invalid asset")
            data = path.read_bytes()
            if hashlib.sha256(data).hexdigest() != manifest["sha256"][name]:
                raise ValueError("asset changed")
            files[name] = data
        if not files["model.glb"].startswith(b"glTF"):
            raise ValueError("invalid model")
        signature = b"\x89PNG\r\n\x1a\n" if photo.endswith(".png") else b"\xff\xd8\xff"
        if not files[photo].startswith(signature):
            raise ValueError("invalid photo")
        with wave.open(io.BytesIO(files["voice.wav"])) as sound:
            seconds = sound.getnframes() / sound.getframerate()
            if seconds < 3 or sound.getcomptype() != "NONE":
                raise ValueError("invalid voice")
        answers = survey_v2.validate(json.loads(files["survey.json"]))
        compiled = survey_v2.compile_survey(answers)
        if compiled["issues"] or answers["person"]["relation"] != "친구":
            raise ValueError("invalid survey")
        if compiled["survey_revision"] != manifest["survey_revision"]:
            raise ValueError("survey changed")
        return {"revision": hashlib.sha256(manifest_bytes).hexdigest(), "photo": photo,
                "files": files, "answers": answers, "compiled": compiled, "voice_sec": round(seconds, 1)}
    except (OSError, ValueError, KeyError, TypeError, wave.Error, EOFError):
        raise HTTPException(503, "준비된 친구의 사진·음성·모델을 확인하지 못했습니다. 자료를 다시 준비해 주세요.") from None


def record_ready(sid, bundle, directory):
    # 일반 등록의 dispatch_model_job을 사용하지 않는다. 이미 전달된 GLB만 기록한다.
    from core import database, storage
    answers, compiled = bundle["answers"], bundle["compiled"]
    image_ok, voice_ok, understand_ok = survey_v2.consent_flags(answers)
    database.insert_session(session_id=sid, payload=survey_v2.stored_payload(answers, compiled),
        consent_image=image_ok, consent_voice=voice_ok, consent_understand=understand_ok,
        bereavement_weeks=answers.get("bereavement_weeks"), has_image=True, has_voice=True)
    photo = storage.session_dir(sid) / ("front" + Path(bundle["photo"]).suffix)
    photo.write_bytes(bundle["files"][bundle["photo"]])
    database.set_model_status(sid, "ready", model_glb_path=str((Path(directory) / "model.glb").resolve()))


def create_router(directory, session_url, session_token):
    router = APIRouter(prefix="/test/friend")

    def asset_url(kind, revision):
        return "/test/friend/" + kind + "?revision=" + revision

    @router.get("")
    def prepared():
        bundle = load_bundle(directory)
        revision = bundle["revision"]
        info = {"ready": True, "revision": revision, "voice_sec": bundle["voice_sec"],
                "photo_url": asset_url("photo", revision), "voice_url": asset_url("voice.wav", revision),
                "photo_name": bundle["photo"], "model_ready": True,
                "preview_revision": bundle["compiled"]["preview_revision"]}
        return Response(json.dumps(info), media_type="application/json", headers={"Cache-Control": "no-store"})

    def media(kind, revision):
        bundle = load_bundle(directory)
        if revision != bundle["revision"]:
            raise HTTPException(409, "준비된 자료가 바뀌었습니다. 친구 버튼을 다시 눌러 주세요.")
        name = bundle["photo"] if kind == "photo" else "voice.wav"
        content_type = "audio/wav" if kind == "voice" else "image/png" if name.endswith(".png") else "image/jpeg"
        return Response(bundle["files"][name], media_type=content_type, headers={"Cache-Control": "no-store"})

    @router.get("/photo")
    def photo(revision: str):
        return media("photo", revision)

    @router.get("/voice.wav")
    def voice(revision: str):
        return media("voice", revision)

    @router.post("/start")
    def start(revision: str = Form(...)):
        bundle = load_bundle(directory)
        if revision != bundle["revision"]:
            raise HTTPException(409, "준비된 자료가 바뀌었습니다. 친구 버튼을 다시 눌러 주세요.")
        compiled, assets = bundle["compiled"], bundle["files"]
        try:
            with httpx.Client(timeout=180, trust_env=False) as client:
                result = client.post(session_url.rstrip("/") + "/session/start",
                    headers={"X-Token": session_token}, data={k: compiled[k] for k in ("persona", "knowledge", "rules")},
                    files={"voice": ("voice.wav", assets["voice.wav"], "audio/wav"),
                           "model": ("model.glb", assets["model.glb"], "model/gltf-binary")})
            result.raise_for_status()
            data = result.json()
            if not re.fullmatch(r"[0-9a-f]{32}", str(data.get("session", ""))) or data.get("has_model") is not True:
                raise ValueError("incomplete session")
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "준비된 친구의 세션 시작을 확인하지 못했습니다. 등록 서버 연결을 확인해 주세요.") from None
        saved, warning = True, ""
        try:
            record_ready(data["session"], bundle, directory)
        except Exception:
            saved, warning = False, "친구 세션은 시작됐지만 설문 기록 저장에 실패했습니다. 운영자에게 알려 주세요."
        return {**data, "survey_saved": saved, "model_queued": False, "model_reused": True,
                "registration_warning": warning, "photo_url": asset_url("photo", revision),
                "voice_url": asset_url("voice.wav", revision), "sent": {"sec": bundle["voice_sec"]}}

    return router
