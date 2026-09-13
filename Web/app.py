"""다시, 봄 — 세션 등록 도구.

영상/오디오에서 화자를 분리해 목소리를 고르고, 인물 설정과 함께 등록 서버에
등록한다. 화자 분리는 새로 만들지 않고 기존 voice_clone_studio 의 NeMo MSDD
엔진을 그대로 호출한다(engine/extraction).

실행:  python -m uvicorn app:app --port 8500
"""
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid

import httpx
import librosa
import numpy as np
import soundfile as sf

import persona as persona_builder

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
POLLING = ("/status", "/extract/")


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

# 화자 분리 엔진 — 저장소 안에 있다. 예전에는 바탕화면의 voice_clone_studio 를
# VCS_DIR 로 가리켰는데, 그 PC 에서만 돌아서 코드를 들여왔다. 무거운 nemo_env 만
# 저장소 밖이며, 만드는 법은 extraction/README.md 에 있다.
EXTRACT_DIR = os.path.join(HERE, "extraction")
EXTRACT_RUNNER = os.path.join(EXTRACT_DIR, "extract_runner.py")

SESSION_URL = os.environ.get("SESSION_URL", "http://220.69.208.201:8000")
SESSION_TOKEN = os.environ.get("SESSION_TOKEN", "")

ALLOWED = {".mp3", ".wav", ".m4a", ".flac", ".ogg", ".aac", ".mp4", ".webm", ".mov", ".mkv"}

os.makedirs(WORK, exist_ok=True)
app = FastAPI(title="다시, 봄 세션 등록")

JOBS = {}       # job_id -> {"state","stage","messages","result","error"}
LAST_REF = os.path.join(WORK, "last_ref.wav")


def load_jobs():
    """재시작해도 추출 결과가 살아 있게 디스크에서 복원한다.
    CPU 화자 분리는 몇 분씩 걸리므로 메모리에만 두면 재시작마다 다시 돌려야 한다."""
    for d in sorted(os.listdir(WORK)) if os.path.isdir(WORK) else []:
        rp = os.path.join(WORK, d, "extract", "result.json")
        if not os.path.exists(rp):
            continue
        try:
            JOBS[d] = {"state": "done", "stage": "완료", "messages": [], "error": None,
                       "result": json.load(open(rp, encoding="utf-8")), "name": d}
        except Exception:
            pass
    if JOBS:
        print(f"[작업] {len(JOBS)}개 복원", flush=True)


load_jobs()


def _headers():
    return {"X-Token": SESSION_TOKEN} if SESSION_TOKEN else {}


def job_dir(job):
    return os.path.join(WORK, job)


def _utf8_env():
    """자식 프로세스를 UTF-8 로 못박는다.

    윈도우 파이썬은 출력이 파이프일 때 콘솔이 아니라 **로케일**(이 PC 는 cp949)로
    인코딩한다. 우리는 utf-8 로 읽으므로 그대로 두면 진행 문구의 한글이 전부
    깨져서 화면에 나온다.

    두 가지를 함께 넘긴다. 화자 분리는 세 겹으로 실행되고(여기 → extract_runner
    → nemo_env 의 nemo_diarize), 가운데 단은 손자를 `text=True` 로만 읽어 로케일
    인코딩을 쓴다. PYTHONIOENCODING 만 넘기면 자식은 utf-8 로 쓰는데 가운데 단은
    cp949 로 읽어 이번엔 거기서 깨진다. PYTHONUTF8 은 로케일 자체를 utf-8 로
    바꿔서 그 단까지 함께 맞춘다."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_extract(job, src, n_speakers):
    """NeMo 화자 분리를 서브프로세스로 돌린다. CPU 라 몇 분 걸릴 수 있다."""
    j = JOBS[job]
    out = os.path.join(job_dir(job), "extract")
    os.makedirs(out, exist_ok=True)
    cmd = [sys.executable, EXTRACT_RUNNER,
           "--extraction-dir", EXTRACT_DIR, "--input", src,
           "--out-dir", out, "--engine", "nemo"]
    if n_speakers:
        cmd += ["--speakers", str(n_speakers)]
    try:
        # NeMo 하위 프로세스가 config 를 상대경로로 열기 때문에 cwd 를 맞춰야 한다
        p = subprocess.Popen(cmd, cwd=EXTRACT_DIR, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, env=_utf8_env(),
                             encoding="utf-8", errors="replace", bufsize=1)
        for line in p.stdout:
            line = line.strip()
            if not line or line == "EXTRACT_DONE":
                continue
            j["stage"] = line
            j["messages"].append(line)
            del j["messages"][:-40]
        p.wait()
        rp = os.path.join(out, "result.json")
        if p.returncode != 0 or not os.path.exists(rp):
            j["state"], j["error"] = "error", "\n".join(j["messages"][-8:]) or "추출 실패"
            return
        j["result"] = json.load(open(rp, encoding="utf-8"))
        j["stage"] = "품질 측정"
        _measure_speakers(job)
        j["state"], j["stage"] = "done", "완료"
    except Exception as e:
        j["state"], j["error"] = "error", str(e)


def _measure_speakers(job):
    """화자별 참조를 **실제 등록 경로에 태워** 품질을 미리 재둔다.

    사용자가 화자를 고를 때 근거가 필요하다. 분리기가 주는 SNR 만으로는 못 고른다 —
    SNR 은 길이 폭주와는 관계있지만 **지지직과는 무관**하다는 것이 실측으로 확인됐다
    (35dB 가 통과하며 실패하고, 59dB 로 올려도 그대로였다).

    지지직을 가르는 것은 **1~4kHz 비율**이고, 꼬리 무음을 가르는 것은 **쉼 비율**이다.
    `_to_wav24` 를 그대로 통과시키므로 여기 나온 값이 실제로 서버에 갈 음성의 값이다."""
    j = JOBS[job]
    for s in (j.get("result") or {}).get("speakers", []):
        p = os.path.join(job_dir(job), "extract", s["ref"]["file"].replace("/", os.sep))
        try:
            _, dur, q = _to_wav24(open(p, "rb").read(), "ref.wav")
            s["quality"] = {**q, "sec": round(dur, 1)}
        except Exception as e:
            s["quality"] = {"error": str(e)}


def _record_and_model(sid: str, survey_json: str, image: bytes, image_name: str):
    """등록이 끝난 뒤 설문을 남기고, 사진이 있으면 3D 모델 작업을 띄운다.

    등록 자체가 실패하면 안 되므로 여기서 나는 오류는 전부 삼킨다 — 대화는 사진 없이도
    되고, 모델은 나중에 관리자 페이지에서 재시도할 수 있다."""
    try:
        d = json.loads(survey_json) if survey_json else {}
    except json.JSONDecodeError:
        d = {}
    try:
        survey_db.insert_session(
            session_id=sid, payload=d,
            consent_image=bool(d.get("consent_image")),
            consent_voice=bool(d.get("consent_voice")),
            consent_understand=bool(d.get("consent_understand")),
            bereavement_weeks=d.get("bereavement_weeks"),
            has_image=bool(image), has_voice=True,
        )
    except Exception as e:
        print(f"[설문저장 실패] {sid}: {e}", flush=True)
        return
    if not image:
        return
    try:
        ext = os.path.splitext(image_name)[1].lower() or ".jpg"
        dest = survey_store.session_dir(sid) / f"front{ext}"
        dest.write_bytes(image)
        survey_db.set_model_status(sid, "queued")
        print(f"[모델] {sid} 작업 시작 — {survey_jobs.dispatch_model_job(sid)}", flush=True)
    except Exception as e:
        print(f"[모델 준비 실패] {sid}: {e}", flush=True)



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
    """설문 응답을 인물·사전지식으로 바꾼다.

    자동 생성이 끝이 아니라 시작이다 — 웹은 이 결과를 편집 가능한 상자에 채워 넣고,
    운영자가 확인하고 고친 뒤에 등록한다. 설문 답이 부실할 때 손쓸 데가 있어야 한다."""
    try:
        d = json.loads(survey)
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"설문 형식이 잘못됐습니다: {e}")
    if not (d.get("relation") or "").strip():
        raise HTTPException(400, "관계는 반드시 있어야 합니다. 말투 전체가 여기서 정해집니다.")
    return persona_builder.build(d)


@app.post("/extract")
async def extract(file: UploadFile = File(...), speakers: int = Form(0)):
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"지원하지 않는 형식입니다: {ext}")
    if not os.path.exists(EXTRACT_RUNNER):
        raise HTTPException(500, f"화자 분리 엔진을 찾을 수 없습니다: {EXTRACT_RUNNER}")

    job = uuid.uuid4().hex[:12]
    os.makedirs(job_dir(job), exist_ok=True)
    src = os.path.join(job_dir(job), "input" + ext)
    with open(src, "wb") as o:
        o.write(await file.read())

    JOBS[job] = {"state": "running", "stage": "시작", "messages": [],
                 "result": None, "error": None, "name": file.filename}
    threading.Thread(target=_run_extract, args=(job, src, speakers), daemon=True).start()
    return {"job": job}


@app.get("/extract/{job}")
def extract_state(job: str):
    j = JOBS.get(job)
    if not j:
        raise HTTPException(404, "없는 작업")
    return {"state": j["state"], "stage": j["stage"], "error": j["error"],
            "messages": j["messages"][-6:], "result": j["result"]}


@app.get("/file/{job}/{path:path}")
def file(job: str, path: str):
    p = os.path.normpath(os.path.join(job_dir(job), "extract", path))
    if not p.startswith(os.path.normpath(job_dir(job))) or not os.path.exists(p):
        raise HTTPException(404, "없는 파일")
    mt = "audio/mpeg" if p.endswith(".mp3") else "audio/wav"
    return FileResponse(p, media_type=mt)



@app.post("/publish")
async def publish(job: str = Form(...), spk_id: str = Form(...), session: str = Form(""),
                  persona: str = Form(...), knowledge: str = Form(""),
                  survey: str = Form(""), image: UploadFile = File(None)):
    j = JOBS.get(job)
    if not j or not j.get("result"):
        raise HTTPException(400, "추출이 끝나지 않았습니다")
    spk = next((s for s in j["result"]["speakers"] if s["spk_id"] == spk_id), None)
    if not spk:
        raise HTTPException(400, "없는 화자")

    ref = os.path.join(job_dir(job), "extract", spk["ref"]["file"].replace("/", os.sep))
    if not os.path.exists(ref):
        raise HTTPException(400, "참조 음성 파일이 없습니다")

    # 분리기와 직접 지정 경로 모두 PCM 변환·음량 조정만 한다. 내부 쉼은 보존한다.
    wav, dur, qual = _to_wav24(open(ref, "rb").read(), "ref.wav")
    with open(LAST_REF, "wb") as o:      # 보낸 것을 그대로 들어볼 수 있게 남긴다
        o.write(wav)
    try:
        r = httpx.post(f"{SESSION_URL}/session/start", headers=_headers(),
                       data={"persona": persona, "knowledge": knowledge, "session": session},
                       files={"voice": ("voice.wav", wav, "audio/wav")}, timeout=180)
    except Exception as e:
        raise HTTPException(502, f"등록 서버에 연결할 수 없습니다: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"등록 실패: {r.text}")
    out = r.json()
    img = await image.read() if image is not None and image.filename else b""
    _record_and_model(out["session"], survey, img, image.filename if image else "")
    return {**out,
            "sent": {"spk_id": spk_id, "sec": round(dur, 2), **qual}}


def _to_wav24(raw, name):
    """Convert to mono PCM24k and scale volume, retaining pauses and cadence.

    The dialogue service chooses a contiguous reference span and transcribes
    that exact span. The former Raon silence-splicing rule does not apply.
    """
    tmp = os.path.join(WORK, "_upload" + os.path.splitext(name)[1].lower())
    with open(tmp, "wb") as o:
        o.write(raw)
    try:
        y, _ = librosa.load(tmp, sr=24000, mono=True)
    finally:
        os.remove(tmp)
    if len(y) == 0:
        raise HTTPException(400, "오디오를 읽지 못했습니다")

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
async def publish_direct(voice: UploadFile = File(...), persona: str = Form(...),
                         knowledge: str = Form(""), session: str = Form(""),
                         survey: str = Form(""), image: UploadFile = File(None)):
    """화자 분리를 건너뛰고 올린 오디오를 그대로 참조로 등록한다.
    분리기가 만든 참조는 조각을 이어붙인 것이라 무엇이 넘어갔는지 알기 어렵다.
    직접 지정하면 보낸 것과 서버가 쓰는 것이 같다는 게 보장된다."""
    ext = os.path.splitext(voice.filename)[1].lower()
    if ext not in ALLOWED:
        raise HTTPException(400, f"지원하지 않는 형식입니다: {ext}")
    wav, dur, qual = _to_wav24(await voice.read(), voice.filename)
    with open(LAST_REF, "wb") as o:      # 보낸 것을 그대로 들어볼 수 있게 남긴다
        o.write(wav)
    try:
        r = httpx.post(f"{SESSION_URL}/session/start", headers=_headers(),
                       data={"persona": persona, "knowledge": knowledge, "session": session},
                       files={"voice": ("voice.wav", wav, "audio/wav")}, timeout=180)
    except Exception as e:
        raise HTTPException(502, f"등록 서버에 연결할 수 없습니다: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"등록 실패: {r.text}")
    out = r.json()
    img = await image.read() if image is not None and image.filename else b""
    _record_and_model(out["session"], survey, img, image.filename if image else "")
    return {**out,
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
    out = {"url": SESSION_URL, "engine": os.path.exists(EXTRACT_RUNNER),
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
