"""다시, 봄 — 세션 등록 도구.

한 사람의 목소리가 담긴 음성/영상 파일 하나를 받아 참조 음성으로 만들고, 인물
설정과 함께 등록 서버에 등록한다. 화자 분리는 하지 않는다.

실행:  python -m uvicorn app:app --port 8500
"""
import io
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

import httpx
import librosa
import numpy as np
import soundfile as sf

# 배포용 정적 ffmpeg 바이너리. 없으면 PATH 의 ffmpeg 를 찾고, 그것도 없으면
# 변환이 필요한 형식만 명확한 오류로 거절한다. 설치는 Web/requirements.txt 를 따른다.
try:
    import imageio_ffmpeg
except ImportError:
    imageio_ffmpeg = None

import survey_v2

# 설문 저장과 3D 모델 생성은 Survey/ 의 것을 그대로 쓴다. 스키마와 작업 추적이 이미
# 있어서 다시 만들 이유가 없고, Survey/pages/2_관리자.py 로 같은 DB 를 들여다볼 수 있다.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "Survey"))
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

# 우리 로그도 UTF-8 로 내보낸다. 자식 프로세스와 같은 이유다 — 파이썬은 stdout 이
# 파이프면 콘솔이 아니라 로케일(이 PC 는 cp949)로 인코딩한다. 터미널에서 직접 띄우면
# 콘솔이 받아주니 멀쩡해 보이고, 로그를 파일이나 파이프로 넘기는 순간 한글이 깨진다.
# 스트림 객체를 바꿔치기하지 않고 그 자리에서 고치므로 uvicorn 이 이미 잡아둔
# 핸들러에도 그대로 적용된다.
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:                      # 파이프가 아닌 것으로 바꿔치기된 경우
        pass

# 되풀이되는 폴링 로그가 나머지를 덮는다. 웹 화면과 Unity 창이 저마다 5초마다
# /status 를 두드리고, 서버는 그때마다 등록 서버에 health·current 두 번을 물어본다.
# 창이 셋이면 분당 100줄이 넘어서 [모델]·[설문저장 실패] 같은 것이 위로 밀려 사라진다.
#
# 끄는 게 아니라 **잘 돌아간 폴링만** 가린다. 200 이 아니면 그대로 올라오므로
# 서버가 죽거나 경로가 틀린 것은 여전히 보인다.
POLLING = ("/status",)


class _HidePolling(logging.Filter):
    """uvicorn 접근 로그에서 성공한 폴링 요청을 지운다.

    uvicorn 은 record.args 에 (주소, 메서드, 경로, HTTP버전, 상태코드) 를 넣는다.
    모양이 다르면 판단하지 않고 통과시킨다 — 가리려다 놓치는 쪽이 더 나쁘다.
    """

    def filter(self, record):
        a = getattr(record, "args", None)
        if not isinstance(a, tuple) or len(a) < 5:
            return True
        path = str(a[2]).split("?")[0]
        if not str(a[4]).startswith("2"):
            return True
        return not path.startswith(POLLING)


logging.getLogger("uvicorn.access").addFilter(_HidePolling())
logging.getLogger("httpx").setLevel(logging.WARNING)

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "workspace")


def _load_env():
    """`Web/.env` 를 읽어 환경변수로 올린다.

    API 키를 저장소에 넣지 않으면서 PC 마다 다르게 두려면 파일이 하나 필요하다.
    시스템 환경변수는 등록이 번거롭고, Unity 에서 띄울 때는 에디터를 재시작해야
    반영된다. 이 파일은 `.gitignore` 로 빠지고 `.env.example` 이 서식을 보여준다.

    **이미 설정된 환경변수는 덮지 않는다.** 한 번만 다른 값으로 띄워보고 싶을 때
    터미널에서 `set` 한 것이 파일에 먹히면 곤란하다.
    """
    path = os.path.join(HERE, ".env")
    if not os.path.exists(path):
        return
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            n += 1
    print(f"[설정] .env 에서 {n}개 읽음", flush=True)


_load_env()

from core import database as survey_db          # noqa: E402
from core import storage as survey_store        # noqa: E402
from core import jobs as survey_jobs            # noqa: E402
from core import model_queue                   # noqa: E402

SESSION_URL = os.environ.get("SESSION_URL", "http://220.69.208.201:8000")
SESSION_TOKEN = os.environ.get("SESSION_TOKEN", "")

ALLOWED = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".webm", ".mov", ".mkv"}

os.makedirs(WORK, exist_ok=True)
app = FastAPI(title="다시, 봄 세션 등록")

LAST_REF = os.path.join(WORK, "last_ref.wav")


def _headers():
    return {"X-Token": SESSION_TOKEN} if SESSION_TOKEN else {}


def _record_and_model(sid: str, answers: dict, compiled: dict, image: bytes, image_name: str):
    """등록 후 저장·모델 접수 결과를 별도로 반환하여 부분 실패를 화면에 알린다.

    설문 원문과 등록 스냅샷(변환기 버전·최종 세 텍스트)을 같은 payload에 함께 남긴다."""
    result = {"survey_saved": False, "model_queued": False, "registration_warning": ""}
    image_ok, voice_ok, understand_ok = survey_v2.consent_flags(answers)
    try:
        survey_db.insert_session(
            session_id=sid, payload=survey_v2.stored_payload(answers, compiled),
            consent_image=image_ok, consent_voice=voice_ok, consent_understand=understand_ok,
            bereavement_weeks=answers.get("bereavement_weeks"),
            has_image=bool(image), has_voice=True,
        )
    except Exception as e:
        print(f"[설문저장 실패] {sid}: {e}", flush=True)
        result["registration_warning"] = "인물은 등록됐지만 설문 기록을 저장하지 못했습니다. 운영자에게 알려 주세요. 사진·모델 작업은 접수되지 않았습니다."
        return result
    result["survey_saved"] = True
    if not image:
        return result
    try:
        ext = os.path.splitext(image_name)[1].lower() or ".jpg"
        dest = survey_store.session_dir(sid) / f"front{ext}"
        dest.write_bytes(image)
        survey_db.set_model_status(sid, "queued")
        print(f"[모델] {sid} 작업 시작 — {survey_jobs.dispatch_model_job(sid)}", flush=True)
        result["model_queued"] = True
    except Exception as e:
        print(f"[모델 준비 실패] {sid}: {e}", flush=True)
        result["registration_warning"] = "인물과 설문은 등록됐지만 사진·모델 작업을 접수하지 못했습니다. 운영자에게 알려 주세요."
    return result



# ── 접근 코드 · 관리자 ──────────────────────────────────────────────
# 설문 앱에 있던 것을 그대로 옮겼다. 참여자의 사진·음성·이야기를 다루므로
# 아무나 열 수 있으면 안 되고, 본인이 원할 때 지울 수 있어야 한다.
ACCESS_CODE = os.environ.get("SURVEY_ACCESS_CODE", "").strip()
ADMIN_PW = os.environ.get("ADMIN_PASSWORD", "").strip()


def _admin(pw: str):
    if not ADMIN_PW:
        raise HTTPException(503, "관리자 비밀번호를 서버에 설정해 주세요")
    if (pw or "").strip() != ADMIN_PW:
        raise HTTPException(401, "관리자 비밀번호가 다릅니다")


@app.get("/gate")
def gate_needed():
    """게이트가 필요한지만 알려준다. 코드 자체는 절대 내보내지 않는다."""
    return {"required": bool(ACCESS_CODE)}


@app.post("/gate")
def gate_check(code: str = Form(...)):
    if not ACCESS_CODE:
        return {"ok": True}
    return {"ok": code.strip().upper() == ACCESS_CODE.upper()}


@app.post("/purge")
def purge_mine(session: str = Form(...)):
    """본인 세션 코드로 즉시 폐기. 비밀번호가 필요 없다 — 지울 권리는 본인 것이다."""
    sid = session.strip()
    s = survey_db.get_session(sid)
    if not s or s.get("status") == "deleted":
        raise HTTPException(404, "해당 코드를 찾을 수 없거나 이미 폐기된 자료입니다")
    survey_store.purge_assets(sid)
    survey_db.soft_delete(sid)
    try:      # 서버에 아직 올라가 있으면 그것도 지운다
        httpx.post(f"{SESSION_URL}/session/end", data={"session": sid},
                   headers=_headers(), timeout=30)
    except Exception:
        pass
    return {"ok": True}


@app.post("/admin/login")
def admin_login(password: str = Form(...)):
    _admin(password)
    return {"ok": True}


# ── 3D 모델 생성 키 ──────────────────────────────────────────────────
# 웹이 서버로 옮겨간 뒤(2026-09-09) 생긴 자리다. 전에는 각자 PC 의 `.env` 를
# 고치면 됐는데, 이제 서버 파일이라 손이 안 닿는다.
#
# **키 자체는 절대 돌려주지 않는다.** 들어 있는지(bool)와 앞 네 글자만 알려준다.
# 그것만으로도 「내가 넣은 그 키가 맞나」는 확인된다.
#
# **이 서버는 공용이다**(`uc` 사용자가 같이 쓴다). 돈이 나가는 키를 여기 두는
# 것이므로 — 전용 키를 따로 발급하고, 지출 상한을 걸고, 시연이 끝나면 지운다.
# 지우는 것도 이 엔드포인트로 한다(빈 값을 보내면 지워진다).

def _env_path():
    return os.path.join(HERE, ".env")


def _write_env_key(key, value):
    """`.env` 의 한 줄만 바꾼다. 나머지 줄과 주석은 그대로 둔다."""
    path = _env_path()
    lines = []
    if os.path.exists(path):
        with io.open(path, encoding="utf-8") as f:
            lines = f.read().split("\n")
    hit = False
    for i, ln in enumerate(lines):
        t = ln.lstrip()
        if t.startswith("#") or not t.startswith(key + "="):
            continue
        lines[i] = f"{key}={value}"
        hit = True
        break
    if not hit:
        lines.append(f"{key}={value}")
    # BOM 을 붙이면 파이썬이 첫 키 이름에 \ufeff 를 달고 읽어 안 잡힌다.
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines).rstrip("\n") + "\n")
    try:
        os.chmod(path, 0o600)     # 공용 기계다. 남이 읽을 이유가 없다.
    except OSError:
        pass


@app.get("/admin/tripo")
def admin_tripo_get(x_admin_pw: str = Header("")):
    _admin(x_admin_pw)
    k = (os.environ.get("TRIPO_API_KEY") or "").strip()
    # 앞 네 글자만. 전부 보여 주면 화면에 띄운 사람이 어깨너머로 잃는다.
    return {"set": bool(k), "hint": (k[:4] + "…") if k else ""}


@app.post("/admin/tripo")
def admin_tripo_set(key: str = Form(""), x_admin_pw: str = Header("")):
    _admin(x_admin_pw)
    v = (key or "").strip()      # 붙여넣기에 공백·줄바꿈이 섞여 오는 일이 잦다
    if v and not v.startswith("tsk_"):
        raise HTTPException(400, "Tripo 키는 tsk_ 로 시작합니다")
    # **환경변수를 먼저 바꾼다.** TripoClient.from_secrets() 가 부를 때마다
    # os.environ 을 읽으므로 서버를 껐다 켤 필요가 없다.
    if v:
        os.environ["TRIPO_API_KEY"] = v
    else:
        os.environ.pop("TRIPO_API_KEY", None)
    _write_env_key("TRIPO_API_KEY", v)     # 다음 기동에도 남게
    print(f"[관리자] 3D 키를 {'넣었습니다' if v else '지웠습니다'}", flush=True)
    return {"ok": True, "set": bool(v)}


@app.get("/admin/sessions")
def admin_sessions(include_deleted: bool = False, x_admin_pw: str = Header("")):
    _admin(x_admin_pw)
    return {"sessions": survey_db.list_sessions(include_deleted=include_deleted)}


@app.get("/admin/session/{sid}")
def admin_session(sid: str, x_admin_pw: str = Header("")):
    _admin(x_admin_pw)
    s = survey_db.get_session(sid)
    if not s:
        raise HTTPException(404, "없는 세션")
    img, voice = survey_store.find_image(sid), survey_store.find_voice(sid)
    return {**s, "has_image_file": bool(img), "has_voice_file": bool(voice)}


@app.get("/admin/asset/{sid}/{kind}")
def admin_asset(sid: str, kind: str, x_admin_pw: str = Header("")):
    _admin(x_admin_pw)
    if kind == "survey":
        s = survey_db.get_session(sid)
        if not s:
            raise HTTPException(404, "없는 세션")
        return Response(json.dumps(s["payload"], ensure_ascii=False, indent=2),
                        media_type="application/json",
                        headers={"Content-Disposition": f'attachment; filename="{sid}.json"'})
    p = {"image": survey_store.find_image, "voice": survey_store.find_voice}.get(kind)
    if p:
        f = p(sid)
        if not f:
            raise HTTPException(404, "파일 없음")
        return FileResponse(str(f), filename=f.name)
    if kind == "model":
        s = survey_db.get_session(sid)
        g = (s or {}).get("model_glb_path")
        if not g or not os.path.exists(g):
            raise HTTPException(404, "모델 없음")
        return FileResponse(g, filename="model.glb")
    raise HTTPException(400, f"알 수 없는 자료: {kind}")


@app.post("/admin/action")
def admin_action(password: str = Form(...), session: str = Form(...), action: str = Form(...)):
    _admin(password)
    sid = session.strip()
    if action == "purge":
        survey_store.purge_assets(sid); survey_db.soft_delete(sid)
    elif action == "hard":
        survey_store.purge_assets(sid); survey_db.hard_delete(sid)
    elif action == "used":
        survey_db.mark_used(sid)
    elif action == "retry":
        msg = survey_jobs.retry(sid)
        return {"ok": True, "result": msg}
    else:
        raise HTTPException(400, f"알 수 없는 동작: {action}")
    return {"ok": True}


@app.get("/after")
def page_after():
    return FileResponse(os.path.join(HERE, "static", "after.html"))


@app.get("/admin")
def page_admin():
    return FileResponse(os.path.join(HERE, "static", "admin.html"))


@app.get("/model/{sid}")
def model_state(sid: str):
    """진행 상황 조회. 웹이 이걸 보고 '모델 만드는 중'을 띄운다."""
    s = survey_db.get_session(sid)
    if not s:
        return {"status": None}
    return {"status": s.get("model_status"), "error": s.get("model_error")}


@app.post("/persona")
async def persona_from_survey(survey: str = Form(...)):
    """설문 응답을 인물·사전지식·추가 규칙과 확인 화면 자료로 바꾼다.

    결정적 변환이므로 사람이 고치는 것은 설문뿐이다. 편집한 프롬프트를 따로 두지 않아
    등록 때 서버가 같은 설문에서 다시 만든 결과와 어긋나지 않는다."""
    try:
        return survey_v2.build(survey)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ── 업로드 오디오 디코딩 ──────────────────────────────────────────
# libsndfile 이 직접 읽는 형식은 빌드와 버전에 따라 다르다(WAV·FLAC·OGG 는 기본이고
# MP3 처럼 빌드에 따라 읽히는 것도 있다). 운영 환경의 M4A/AAC 는 읽지 못해
# 「Format not recognised」가 났고, 처리되지 않은 그 예외가 text/plain 「Internal Server
# Error」 500 으로 나가 화면이 JSON 으로 읽다가 터졌다(2026-09-16 사용자 보고).
# 그래서 먼저 그대로 읽어 보고, 안 되면 ffmpeg 로 넘기며, 그것도 안 되면 뜻이 분명한
# JSON 오류를 준다. 어느 쪽으로 읽었든 결과는 같은 24kHz 모노 파형이다.
TARGET_SR = 24000
FFMPEG_TIMEOUT_SEC = 120


def _ffmpeg_path():
    """번들 바이너리 → PATH 순서로 찾는다. 둘 다 없으면 None."""
    if imageio_ffmpeg is not None:
        try:
            path = imageio_ffmpeg.get_ffmpeg_exe()
            if path and os.path.exists(path):
                return path
        except Exception as e:                      # 다운로드 실패·플랫폼 미지원 등
            print(f"[변환] 번들 ffmpeg 를 쓸 수 없습니다: {type(e).__name__}", flush=True)
    return shutil.which("ffmpeg")


def _temp_upload(raw, name):
    """요청마다 다른 임시 파일을 만든다.

    예전에는 `_upload<확장자>` 한 이름을 함께 썼다. 두 사람이 같은 순간에 올리면
    서로의 파일을 덮어쓰고, 한쪽 정리가 다른 쪽을 지웠다.
    확장자는 이름에서 가져오되 짧은 영숫자만 허용한다 — 디코더는 확장자가 아니라
    내용으로 형식을 찾으므로, 이상한 이름은 그냥 `.bin` 으로 둔다."""
    ext = os.path.splitext(name or "")[1].lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,5}", ext):
        ext = ".bin"
    handle, path = tempfile.mkstemp(prefix="upload-", suffix=ext, dir=WORK)
    with os.fdopen(handle, "wb") as out:
        out.write(raw)
    return path


def _decode_libsndfile(path):
    """기존 경로. 이 환경의 libsndfile 이 읽는 형식은 여기서 끝나고 ffmpeg 를 거치지 않는다."""
    y, _ = librosa.load(path, sr=TARGET_SR, mono=True)
    return y


def _decode_ffmpeg(path, ffmpeg):
    """첫 오디오 트랙만 24kHz 모노 float 로 뽑는다. 쉼과 길이는 그대로 둔다."""
    command = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
               "-i", path, "-vn", "-map", "0:a:0", "-ac", "1",
               "-ar", str(TARGET_SR), "-f", "f32le", "-"]
    # 셸을 거치지 않는 인수 배열이고, 사용자 입력은 파일 내용으로만 들어간다.
    done = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          timeout=FFMPEG_TIMEOUT_SEC)
    if done.returncode != 0:
        # stderr 원문에는 서버 경로가 들어 있다. 로그에만 남기고 화면에는 보내지 않는다.
        print(f"[변환] ffmpeg 실패({done.returncode}): "
              f"{done.stderr.decode('utf-8', 'replace').strip()[-400:]}", flush=True)
        raise HTTPException(400, "이 파일에서 소리를 읽지 못했습니다. "
                                 "손상되었거나 소리가 없는 파일일 수 있습니다. 다른 파일을 올려 주세요.")
    return np.frombuffer(done.stdout, dtype="<f4").astype(np.float32)


def _decode_media(raw, name):
    """업로드 바이트 → 24kHz 모노 float 파형. 실패는 원인별로 구분해 알린다."""
    if not raw:
        raise HTTPException(400, "업로드된 파일이 비어 있습니다. 파일을 다시 골라 주세요.")
    path = _temp_upload(raw, name)
    try:
        try:
            return _decode_libsndfile(path)
        except Exception as e:          # 형식 미지원·손상 모두 여기로 온다
            reason = type(e).__name__
        ffmpeg = _ffmpeg_path()
        if not ffmpeg:
            print(f"[변환] {reason} 이후 쓸 수 있는 ffmpeg 가 없습니다", flush=True)
            raise HTTPException(503, "이 형식을 변환할 도구가 서버에 준비되지 않았습니다. "
                                     "WAV 파일로 올리시거나 운영자에게 알려 주세요.")
        try:
            return _decode_ffmpeg(path, ffmpeg)
        except subprocess.TimeoutExpired:
            print("[변환] ffmpeg 시간 초과", flush=True)
            raise HTTPException(504, "변환이 너무 오래 걸립니다. 더 짧은 녹음을 올려 주세요.") from None
        except OSError as e:
            # 경로는 찾았지만 실행되지 않는 경우다(지워짐·권한 없음·플랫폼 불일치).
            # 파일 잘못이 아니므로 400 으로 돌리지 않고, 실행 오류 원문도 화면에 보내지 않는다.
            print(f"[변환] ffmpeg 를 실행하지 못했습니다: {type(e).__name__}", flush=True)
            raise HTTPException(503, "이 형식을 변환할 도구가 서버에 준비되지 않았습니다. "
                                     "WAV 파일로 올리시거나 운영자에게 알려 주세요.") from None
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


def _to_wav24(raw, name):
    """Convert to mono PCM24k and scale volume, retaining pauses and cadence.

    The dialogue service chooses a contiguous reference span and transcribes
    that exact span. The former Raon silence-splicing rule does not apply.
    """
    y = _decode_media(raw, name)
    if len(y) == 0:
        raise HTTPException(400, "오디오를 읽지 못했습니다. 소리가 들어 있는 파일인지 확인해 주세요.")

    quiet_before = _quality(y)[1]

    peak = float(np.abs(y).max())
    gain = min(10.0, 0.8 / peak) if peak > 1e-6 else 1.0   # 과증폭은 10배로 제한
    y = y * gain
    snr, quiet, mid, warn = _quality(y)
    buf = io.BytesIO()
    sf.write(buf, y, 24000, format="WAV", subtype="PCM_16")
    return buf.getvalue(), len(y) / 24000, {
        "level": round(_level(y), 3), "gain": round(gain, 1),
        "snr_db": snr, "quiet_ratio": quiet, "mid_ratio": mid, "warning": warn,
        "silence_cut": False, "quiet_before": quiet_before,
        "cut_from_sec": None}


def _level(y):
    """0.1초 창 RMS 의 최댓값. 사람이 느끼는 음량에 가깝다."""
    w = 2400
    if len(y) < w:
        return float(np.sqrt((y ** 2).mean()))
    n = len(y) // w
    return float(np.sqrt((y[:n * w].reshape(n, w) ** 2).mean(axis=1)).max())


REF_MIN_SEC, REF_MAX_SEC = 6, 12

def _warn_text(snr, mid=None, dur=None):
    """Recording guidance. Former Raon failure thresholds do not describe Qwen."""
    out = []
    if dur is not None and dur < REF_MIN_SEC:
        out.append(f"참조는 {dur:.1f}초입니다. 최소 3초가 필요하며 6~12초의 선명한 녹음을 권장합니다.")
    elif dur is not None and dur > REF_MAX_SEC:
        out.append(f"참조는 {dur:.1f}초입니다. 대화 시작 시 최대 12초의 연속 구간을 사용합니다.")
    return " ".join(out)


def _quality(y, sr=24000):
    """참조의 잡음 정도를 **대역별로** 잰다.

    처음에는 0.1초 창 RMS 의 최댓값과 하위 10퍼센타일 비로 쟀는데, 그건 잡음이
    아니라 다이내믹 레인지를 재는 것이었다. 쉬는 구간을 잘라낸 깨끗한 녹음이
    14.9dB 로 나왔다(실제로는 잡음이 없고 정상 동작한다). 대역마다 시간축 하위
    분위수를 잡음 바닥으로 보면 쉼이 없어도 추정된다.

    쉼 비율과 대역 비율은 녹음 진단 정보다. 내부 쉼을 자르거나
    Qwen의 합성 성공률·목소리 유사도 점수로 해석하지 않는다.

    이 SNR 은 시간축 하위 분위수를 잡음 바닥으로 본다. 그래서 **계속 변하는
    배경음은 못 잡는다** — 사용자가 "목소리가 묻힐 정도"라고 한 참조가 30.0dB 로
    나왔다. 일정한 잡음에만 쓸 것."""
    dur = len(y) / sr
    if len(y) < sr // 2:
        return None, None, None, _warn_text(None, None, dur)
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=256))
    f = np.fft.rfftfreq(1024, 1 / sr)
    b = (f >= 200) & (f <= 6000)                 # 음성 대역만 본다
    floor = float(np.percentile(S, 10, axis=1)[b].mean())
    sig = float(np.percentile(S, 90, axis=1)[b].mean())
    snr = float(20 * np.log10(max(sig, 1e-9) / max(floor, 1e-9)))
    P = S ** 2
    mid = float(P[(f >= 1000) & (f < 4000)].sum() / max(P.sum(), 1e-12))
    w = 2400
    n = len(y) // w
    r = np.sqrt((y[:n * w].reshape(n, w) ** 2).mean(axis=1)) if n >= 2 else np.array([0.0])
    quiet = float((r < r.max() * 0.05).mean()) if r.max() > 0 else 0.0
    # 파이썬 float 이어야 JSON 직렬화된다
    return round(snr, 1), round(quiet, 3), round(mid, 3), _warn_text(snr, mid, dur)


@app.post("/publish_direct")
async def publish_direct(voice: UploadFile = File(...), session: str = Form(""),
                         survey: str = Form(""), survey_revision: str = Form(""),
                         preview_revision: str = Form(""), image: UploadFile = File(None)):
    """올린 오디오를 그대로 참조로 등록한다. 한 사람이 말하는 녹음 하나를 받는다.
    PCM 변환·음량 조정 후 등록하며 원래 쉼과 말하기 속도는 보존한다."""
    try:
        answers, compiled = survey_v2.registration_bundle(survey, survey_revision,
                                                          preview_revision, session)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    ext = os.path.splitext(voice.filename)[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"지원하지 않는 형식입니다: {ext}")
    wav, dur, qual = _to_wav24(await voice.read(), voice.filename)
    with open(LAST_REF, "wb") as o:      # 보낸 것을 그대로 들어볼 수 있게 남긴다
        o.write(wav)
    try:
        r = httpx.post(f"{SESSION_URL}/session/start", headers=_headers(),
                       data={"persona": compiled["persona"], "knowledge": compiled["knowledge"],
                             "rules": compiled["rules"]},
                       files={"voice": ("voice.wav", wav, "audio/wav")}, timeout=180)
    except Exception as e:
        raise HTTPException(502, f"등록 서버에 연결할 수 없습니다: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"등록 실패: {r.text}")
    out = r.json()
    img = await image.read() if image is not None and image.filename else b""
    recorded = _record_and_model(out["session"], answers, compiled, img, image.filename if image else "")
    return {**out, **recorded,
            "sent": {"file": voice.filename, "sec": round(dur, 2), **qual}}


@app.get("/last_ref.wav")
def last_ref():
    """직전에 등록한 참조 음성. 서버로 넘어간 것과 바이트 단위로 같다."""
    if not os.path.exists(LAST_REF):
        raise HTTPException(404, "아직 직접 등록한 참조가 없습니다")
    return FileResponse(LAST_REF, media_type="audio/wav")


@app.post("/end")
def end(session: str = Form(...)):
    try:
        return httpx.post(f"{SESSION_URL}/session/end", headers=_headers(),
                          data={"session": session}, timeout=30).json()
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/status")
def status():
    # 키 자체는 절대 내보내지 않는다. 있는지 없는지만 알면 "3D 모델이 왜 안 뜨지"에
    # 답할 수 있고, 그 이상은 화면에 띄울 이유가 없다.
    # engine 은 화자 분리를 쓰던 구형 Unity 상태창을 위한 호환 필드다. 늘 false 다.
    out = {"url": SESSION_URL, "engine": False,
           "tripo": bool(os.environ.get("TRIPO_API_KEY")),
           "model_worker": model_queue.status()}
    try:
        out["health"] = httpx.get(f"{SESSION_URL}/health", timeout=10).json()
        out["current"] = httpx.get(f"{SESSION_URL}/session/current",
                                   headers=_headers(), timeout=10).json()
    except Exception as e:
        out["error"] = str(e)
    return out


app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
