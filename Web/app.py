"""다시, 봄 — 세션 등록 도구.

영상/오디오에서 화자를 분리해 목소리를 고르고, 인물 설정과 함께 Raon 서버에
등록한다. 화자 분리는 새로 만들지 않고 기존 voice_clone_studio 의 NeMo MSDD
엔진을 그대로 호출한다(engine/extraction).

실행:  python -m uvicorn app:app --port 8500
"""
import io
import json
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
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "workspace")

# 화자 분리 엔진 — voice_clone_studio 의 것을 재사용한다.
STUDIO = os.environ.get(
    "VCS_DIR", r"C:\Users\user\Desktop\capstone\voice_clone_studio")
EXTRACT_DIR = os.path.join(STUDIO, "engine", "extraction")
EXTRACT_RUNNER = os.path.join(STUDIO, "backend", "runners", "extract_runner.py")

RAON_URL = os.environ.get("RAON_URL", "http://220.69.208.201:8000")
RAON_TOKEN = os.environ.get("RAON_TOKEN", "23605a891e448b5aa46f82c8640b554c")

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
    return {"X-Token": RAON_TOKEN} if RAON_TOKEN else {}


def job_dir(job):
    return os.path.join(WORK, job)


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
                             stderr=subprocess.STDOUT, text=True,
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

    지지직을 가르는 것은 **저역 비율**이고, 꼬리 무음을 가르는 것은 **쉼 비율**이다.
    `_to_wav24` 를 그대로 통과시키므로 여기 나온 값이 실제로 서버에 갈 음성의 값이다."""
    j = JOBS[job]
    for s in (j.get("result") or {}).get("speakers", []):
        p = os.path.join(job_dir(job), "extract", s["ref"]["file"].replace("/", os.sep))
        try:
            _, dur, q = _to_wav24(open(p, "rb").read(), "ref.wav")
            s["quality"] = {**q, "sec": round(dur, 1)}
        except Exception as e:
            s["quality"] = {"error": str(e)}


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
def publish(job: str = Form(...), spk_id: str = Form(...), session: str = Form(""),
            persona: str = Form(...), knowledge: str = Form("")):
    j = JOBS.get(job)
    if not j or not j.get("result"):
        raise HTTPException(400, "추출이 끝나지 않았습니다")
    spk = next((s for s in j["result"]["speakers"] if s["spk_id"] == spk_id), None)
    if not spk:
        raise HTTPException(400, "없는 화자")

    ref = os.path.join(job_dir(job), "extract", spk["ref"]["file"].replace("/", os.sep))
    if not os.path.exists(ref):
        raise HTTPException(400, "참조 음성 파일이 없습니다")

    # 분리기가 만든 참조도 직접 지정과 **같은 처리**를 받아야 한다. 예전에는 이 경로만
    # 파일을 그대로 보내서, 음량 정규화도 쉼 제거도 품질 경고도 안 걸렸다.
    wav, dur, qual = _to_wav24(open(ref, "rb").read(), "ref.wav")
    with open(LAST_REF, "wb") as o:      # 보낸 것을 그대로 들어볼 수 있게 남긴다
        o.write(wav)
    try:
        r = httpx.post(f"{RAON_URL}/session/start", headers=_headers(),
                       data={"persona": persona, "knowledge": knowledge, "session": session},
                       files={"voice": ("voice.wav", wav, "audio/wav")}, timeout=180)
    except Exception as e:
        raise HTTPException(502, f"Raon 서버에 연결할 수 없습니다: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"등록 실패: {r.text}")
    return {**r.json(), "sent": {"spk_id": spk_id, "sec": round(dur, 2), **qual}}


CUT_BIN, CUT_KEEP, CUT_XF = 0.02, 0.12, 0.01
# 쉼이 이 비율을 넘을 때만 잘라낸다. 실측 경계 —
#   18%, 19% -> 손대지 않아도 5/5 정상.  22% -> 1/5,  27.6% -> 0/5.
CUT_THRESHOLD = 0.20

def _cut_silence(y, sr=24000):
    """참조에서 쉬는 구간을 줄인다. 합성 결과의 꼬리 무음을 없애는 처리다.

    참조가 많이 쉬면 합성된 목소리도 많이 쉰다. 문장을 다 말하고도 3~8초를 더
    생성하다 상한에 걸려 끝나고, 반이중 구조라 그동안 사용자가 말을 못 한다.

    같은 참조에서 쉼만 잘라 확인했다 —
      유튜버 클립  쉼 22%   -> 0%   꼬리 1.4~7.4초 -> 0.26~0.54초, 정상 1/5 -> 5/5
      직접 녹음    쉼 27.6% -> 6%   꼬리 0.0~8.4초 -> 0.22~0.64초, 정상 0/5 -> 5/5
    두 참조 모두 전사가 원본과 같았다. 말을 자르는 게 아니라 사이의 침묵만 줄인다.

    KEEP 만큼은 남긴다 — 0 으로 만들면 말이 붙어 숨 가쁘게 들린다.
    이어붙이는 자리마다 크로스페이드를 넣지 않으면 딸깍 소리가 난다."""
    from scipy.signal import butter, sosfiltfilt
    v = sosfiltfilt(butter(4, [300, 4000], btype="band", fs=sr, output="sos"), y)
    w = int(sr * CUT_BIN)
    n = len(v) // w
    if n < 2:
        return y
    r = np.sqrt((v[:n * w].reshape(n, w) ** 2).mean(axis=1))
    sp = r > np.percentile(r, 95) * 0.06

    runs, i = [], 0
    while i < len(sp):
        if sp[i]:
            j = i
            while j < len(sp) and sp[j]:
                j += 1
            runs.append([i, j]); i = j
        else:
            i += 1
    if not runs:
        return y
    keep = int(CUT_KEEP / CUT_BIN)
    merged = [runs[0]]
    for s, e in runs[1:]:
        if s - merged[-1][1] <= keep:      # 짧은 쉼은 그대로 둔다
            merged[-1][1] = e
        else:
            merged.append([s, e])

    pad, xf = int(CUT_KEEP / 2 / CUT_BIN), int(sr * CUT_XF)
    fade = np.linspace(0, 1, xf)
    parts = [y[max(0, s - pad) * w: min(len(y), (e + pad) * w)] for s, e in merged]
    out = parts[0]
    for p in parts[1:]:
        if len(out) >= xf and len(p) >= xf:
            out = np.concatenate([out[:-xf],
                                  out[-xf:] * fade[::-1] + p[:xf] * fade, p[xf:]])
        else:
            out = np.concatenate([out, p])
    return out


def _to_wav24(raw, name):
    """무엇이 올라오든 24kHz 모노 wav 로 맞춘다. 서버가 참조로 쓰는 형식이다.

    쉬는 구간을 줄이고(`_cut_silence`) 음량을 맞춘다. 참조가 작으면 합성 결과가
    눌리고 억양도 제대로 안 따라온다 — 실측으로 잘 됐던 참조가 peak RMS
    0.14~0.20 이었고 실패한 것이 0.05 였다."""
    tmp = os.path.join(WORK, "_upload" + os.path.splitext(name)[1].lower())
    with open(tmp, "wb") as o:
        o.write(raw)
    try:
        y, _ = librosa.load(tmp, sr=24000, mono=True)
    finally:
        os.remove(tmp)
    if len(y) == 0:
        raise HTTPException(400, "오디오를 읽지 못했습니다")

    before = len(y) / 24000
    quiet_before = _quality(y)[1]
    # 필요할 때만 자른다. 쉼이 적은 참조는 이미 잘 나오고, 굳이 자르면
    # 오히려 나빠진다 — 쉼 18% 짜리를 0% 로 만들었더니 꼬리는 그대로 좋은데
    # 지지직 지표가 0.07~0.39 에서 0.46~1.36 으로 올랐다.
    cut = quiet_before is not None and quiet_before >= CUT_THRESHOLD
    if cut:
        y = _cut_silence(y)
        if len(y) == 0:
            raise HTTPException(400, "말소리를 찾지 못했습니다")

    peak = float(np.abs(y).max())
    gain = min(10.0, 0.8 / peak) if peak > 1e-6 else 1.0   # 과증폭은 10배로 제한
    y = y * gain
    snr, quiet, low, warn = _quality(y)
    buf = io.BytesIO()
    sf.write(buf, y, 24000, format="WAV", subtype="PCM_16")
    return buf.getvalue(), len(y) / 24000, {
        "level": round(_level(y), 3), "gain": round(gain, 1),
        "snr_db": snr, "quiet_ratio": quiet, "low_ratio": low, "warning": warn,
        "silence_cut": cut, "quiet_before": quiet_before,
        "cut_from_sec": round(before, 2) if cut else None}


def _level(y):
    """0.1초 창 RMS 의 최댓값. 사람이 느끼는 음량에 가깝다."""
    w = 2400
    if len(y) < w:
        return float(np.sqrt((y ** 2).mean()))
    n = len(y) // w
    return float(np.sqrt((y[:n * w].reshape(n, w) ** 2).mean(axis=1)).max())


LOW_SAFE = 0.30      # 0~300Hz 비율이 이보다 낮으면 경고

def _warn_text(snr, low=None):
    """참조 품질 경고. **두 기준의 성격이 다르다는 점을 알고 쓸 것.**

    SNR 은 **길이 폭주**에만 유효하다. 통제 실험(같은 화자·내용에 핑크 잡음만
    추가)에서 40/26dB 는 정상, 21dB 부터 길이 폭주, 16dB 는 상한까지 반복이었다.
    **지지직에 대해서는 무의미하다** — 35dB 짜리가 심한 지지직을 냈고, 잡음 제거로
    59dB 까지 올려도 그대로였다(오히려 악화).

    저역 비율은 **지지직**을 가른다. 참조 8개를 오차 없이 갈라낸 유일한 지표다 —
    38.2~82.9% 는 전부 깨끗했고, 5.8% 짜리 하나만 실패했다. 스펙트럼을 서로
    맞바꾸는 실험으로 인과도 확인했다(깨끗한 참조를 5.5% 로 만들면 지지직이 생긴다).

    **경계는 모른다.** 5.8% 와 38.2% 사이에 표본이 없다. 그래서 보수적으로 30%
    미만에서만 경고한다. 아는 척해서 기준을 좁게 잡으면 SNR 25dB 때 한 실수를
    반복하게 된다."""
    out = []
    if low is not None and low < LOW_SAFE:
        out.append(f"저음이 지나치게 적습니다 (0~300Hz {low*100:.0f}%, 30% 이상 권장). "
                   f"합성 결과에 지지직이 낄 수 있습니다. 방송·영상용으로 후처리된 "
                   f"소리에서 나타납니다 — 직접 녹음한 음성을 쓰는 편이 안전합니다.")
    if snr is not None and snr < 25:
        out.append(f"참조 잡음이 많습니다 (SNR {snr:.0f}dB, 25dB 이상 권장). "
                   f"오디오가 길게 늘어질 수 있습니다.")
    return " ".join(out)


def _quality(y, sr=24000):
    """참조의 잡음 정도를 **대역별로** 잰다.

    처음에는 0.1초 창 RMS 의 최댓값과 하위 10퍼센타일 비로 쟀는데, 그건 잡음이
    아니라 다이내믹 레인지를 재는 것이었다. 쉬는 구간을 잘라낸 깨끗한 녹음이
    14.9dB 로 나왔다(실제로는 잡음이 없고 정상 동작한다). 대역마다 시간축 하위
    분위수를 잡음 바닥으로 보면 쉼이 없어도 추정된다.

    쉬는 구간 비율은 **20% 를 넘으면 잘라낸다**(`_cut_silence`). 참조가 많이 쉬면
    합성 결과도 많이 쉰다. 판정에는 안 쓰고 처리 여부만 정한다.

    저역 비율(0~300Hz)은 **지지직**을 가른다. 여기서 같이 재서 경고에 쓴다."""
    if len(y) < sr // 2:
        return None, None, None, ""
    S = np.abs(librosa.stft(y, n_fft=1024, hop_length=256))
    f = np.fft.rfftfreq(1024, 1 / sr)
    b = (f >= 200) & (f <= 6000)                 # 음성 대역만 본다
    floor = float(np.percentile(S, 10, axis=1)[b].mean())
    sig = float(np.percentile(S, 90, axis=1)[b].mean())
    snr = float(20 * np.log10(max(sig, 1e-9) / max(floor, 1e-9)))
    P = S ** 2
    low = float(P[f < 300].sum() / max(P.sum(), 1e-12))
    w = 2400
    n = len(y) // w
    r = np.sqrt((y[:n * w].reshape(n, w) ** 2).mean(axis=1)) if n >= 2 else np.array([0.0])
    quiet = float((r < r.max() * 0.05).mean()) if r.max() > 0 else 0.0
    # 파이썬 float 이어야 JSON 직렬화된다
    return round(snr, 1), round(quiet, 3), round(low, 3), _warn_text(snr, low)


@app.post("/publish_direct")
async def publish_direct(voice: UploadFile = File(...), persona: str = Form(...),
                         knowledge: str = Form(""), session: str = Form("")):
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
        r = httpx.post(f"{RAON_URL}/session/start", headers=_headers(),
                       data={"persona": persona, "knowledge": knowledge, "session": session},
                       files={"voice": ("voice.wav", wav, "audio/wav")}, timeout=180)
    except Exception as e:
        raise HTTPException(502, f"Raon 서버에 연결할 수 없습니다: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"등록 실패: {r.text}")
    return {**r.json(), "sent": {"file": voice.filename, "sec": round(dur, 2), **qual}}


@app.get("/last_ref.wav")
def last_ref():
    """직전에 등록한 참조 음성. 서버로 넘어간 것과 바이트 단위로 같다."""
    if not os.path.exists(LAST_REF):
        raise HTTPException(404, "아직 직접 등록한 참조가 없습니다")
    return FileResponse(LAST_REF, media_type="audio/wav")


@app.post("/end")
def end(session: str = Form(...)):
    try:
        return httpx.post(f"{RAON_URL}/session/end", headers=_headers(),
                          data={"session": session}, timeout=30).json()
    except Exception as e:
        raise HTTPException(502, str(e))


@app.post("/mode")
def mode(cont: str = Form(...)):
    """억양 복제 토글. 재시작 없이 바뀌므로 A/B 비교에 쓴다."""
    try:
        r = httpx.post(f"{RAON_URL}/mode", headers=_headers(),
                       data={"cont": cont}, timeout=180)
    except Exception as e:
        raise HTTPException(502, str(e))
    if r.status_code != 200:
        raise HTTPException(r.status_code, r.text)
    return r.json()


@app.get("/status")
def status():
    out = {"url": RAON_URL, "engine": os.path.exists(EXTRACT_RUNNER)}
    try:
        out["health"] = httpx.get(f"{RAON_URL}/health", timeout=10).json()
        out["current"] = httpx.get(f"{RAON_URL}/session/current",
                                   headers=_headers(), timeout=10).json()
    except Exception as e:
        out["error"] = str(e)
    return out


app.mount("/", StaticFiles(directory=os.path.join(HERE, "static"), html=True), name="static")
