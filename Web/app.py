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
        j["state"], j["stage"] = "done", "완료"
    except Exception as e:
        j["state"], j["error"] = "error", str(e)


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


def _src_path(job):
    for f in os.listdir(job_dir(job)):
        if f.startswith("input"):
            return os.path.join(job_dir(job), f)
    return None


def best_window(job, spk_id, win=14.0, min_sec=8.0):
    """화자 분리 결과에서 **연속된 한 구간**을 고른다.

    분리기가 만드는 ref.wav 는 원본 곳곳의 조각을 이어붙인 것이다. 음색만 쓰는
    tts() 에는 문제없지만, 참조를 그대로 이어 말하는 tts_continuation 에는 맞지
    않는다 — 뚝뚝 끊긴 소리에서 이어가라는 셈이 된다.

    고르는 기준은 실측으로 정했다(docs/Raon모델_분석.md §6): 단일 화자,
    SNR 이 높고, 쉬는 구간이 있는 곳. **쉼은 제거하지 않는다** — 숨 쉬는 간격이
    참조의 일부이고, 그게 없으면 모델이 멈출 줄을 모른다.
    """
    j = JOBS.get(job)
    r = j["result"]
    spk = next((s for s in r["speakers"] if s["spk_id"] == spk_id), None)
    src = _src_path(job)
    if not spk or not src:
        return None
    cluster, segs = spk["cluster"], r["viz"]["segments"]
    y, sr = librosa.load(src, sr=24000, mono=True)

    # 0.1초 격자로 화자 점유를 펴 놓고 창을 밀면서 훑는다. 구간을 병합해 앞부분만
    # 보면 긴 발화 안쪽의 더 좋은 자리를 놓친다.
    hop, dur = 0.1, len(y) / sr
    n = max(1, int(dur / hop))
    tgt = np.zeros(n, bool)
    oth = np.zeros(n, bool)
    for s in segs:
        i0, i1 = int(s["start"] / hop), min(n, int(s["end"] / hop))
        (tgt if s["spk"] == cluster else oth)[i0:i1] = True

    win = min(win, max(min_sec, dur * 0.95))   # 짧은 원본에서는 창을 줄인다
    W, step = int(win / hop), max(1, int(0.5 / hop))

    # SNR 과 쉼은 선형 트레이드오프가 아니라 **문턱**이다(실측: 성공 39~58dB·15~28%,
    # 실패 16~25dB·0~9%). 문턱 아래는 급격히 감점해서 "SNR 은 낮지만 겹침이 없는"
    # 창이 이기지 못하게 한다.
    fade = int(0.02 * sr)

    def mute_other(seg, a):
        """창 안에 들어온 다른 화자 구간만 무음으로 만든다.

        분리기의 ref.wav 는 조각나지만 순수하고, 연속 창은 이어지지만 오염된다.
        지워버리면 둘 다 가질 수 있다 — 대상 화자의 흐름은 유지되고, 비워진
        자리는 자연스러운 쉼이 된다. 경계는 페이드로 눌러 클릭을 막는다."""
        for s in segs:
            if s["spk"] == cluster:
                continue
            i0 = max(0, int((s["start"] - a) * sr))
            i1 = min(len(seg), int((s["end"] - a) * sr))
            if i1 <= i0:
                continue
            seg[i0:i1] = 0.0
            if i0 - fade >= 0:
                seg[i0 - fade:i0] *= np.linspace(1, 0, fade)
            if i1 + fade <= len(seg):
                seg[i1:i1 + fade] *= np.linspace(0, 1, fade)
        return seg

    def scan(min_share):
        out = None
        for i in range(0, max(1, n - W), step):
            share = tgt[i:i + W].mean()
            if share < min_share:
                continue          # 대상 화자가 창의 상당 부분을 말해야 한다
            a = i * hop
            raw = y[int(a * sr):int((a + win) * sr)].copy()
            if len(raw) < sr * min(min_sec, win):
                continue
            muted = oth[i:i + W].sum() * hop
            pk = float(np.abs(raw).max())
            g = min(10.0, 0.8 / pk) if pk > 1e-6 else 1.0
            # SNR 은 **지우기 전** 원본에서 잰다. 지운 자리는 완전한 0 이라 바닥값이
            # 0 이 되어 SNR 이 무한대로 튄다(실제로 165dB 가 나왔다).
            snr = _quality(raw * g)[0] or 0.0
            # 쉼은 **실제로 내보낼 오디오**에서 잰다. 지운 자리가 곧 쉼이 되므로
            # 원본에서 재면 "쉬지 않고 말하는 창"이 부당하게 유리해진다.
            out_seg = mute_other(raw.copy(), a) * g
            quiet = _quality(out_seg)[1] or 0.0
            # 많이 지웠다는 건 대상 화자의 발화가 그만큼 적다는 뜻이다
            score = (min(snr, 45) + min(quiet, 0.35) * 150 + share * 20 - muted * 5
                     - (0 if snr >= 30 else (30 - snr) * 4)
                     - (0 if quiet >= 0.10 else (0.10 - quiet) * 300))
            if out is None or score > out[0]:
                out = (score, a, a + win, out_seg, snr, quiet,
                       _warn_text(snr, quiet), muted)
        return out

    best = scan(0.55) or scan(0.30)   # 발화가 많은 자리를 먼저, 없으면 완화
    if best is None:
        return None
    _, a, b, seg, snr, quiet, warn, muted = best
    buf = io.BytesIO()
    sf.write(buf, seg, 24000, format="WAV", subtype="PCM_16")
    return buf.getvalue(), {"start": round(a, 1), "end": round(b, 1),
                            "sec": round(b - a, 1), "snr_db": snr,
                            "quiet_ratio": quiet, "muted_sec": round(muted, 1),
                            "speech_sec": round((b - a) * (1 - quiet), 1),
                            "level": round(_level(seg), 3), "warning": warn}


@app.get("/window/{job}/{spk_id}")
def window_info(job: str, spk_id: str, audio: int = 0):
    """등록 전에 실제로 보낼 구간을 들어보고 지표를 확인한다."""
    if job not in JOBS or not JOBS[job].get("result"):
        raise HTTPException(404, "없는 작업")
    got = best_window(job, spk_id)
    if not got:
        raise HTTPException(404, "연속 구간을 찾지 못했습니다")
    wav, info = got
    if audio:
        return Response(wav, media_type="audio/wav")
    return info


@app.post("/publish")
def publish(job: str = Form(...), spk_id: str = Form(...), session: str = Form(""),
            persona: str = Form(...), knowledge: str = Form("")):
    j = JOBS.get(job)
    if not j or not j.get("result"):
        raise HTTPException(400, "추출이 끝나지 않았습니다")
    spk = next((s for s in j["result"]["speakers"] if s["spk_id"] == spk_id), None)
    if not spk:
        raise HTTPException(400, "없는 화자")

    got = best_window(job, spk_id)
    if got:
        wav, info = got
    else:
        # 연속 구간이 없으면 분리기가 만든 조각 참조로 물러난다
        ref = os.path.join(job_dir(job), "extract", spk["ref"]["file"].replace("/", os.sep))
        if not os.path.exists(ref):
            raise HTTPException(400, "참조 음성 파일이 없습니다")
        with open(ref, "rb") as f:
            wav = f.read()
        info = {"fallback": "조각 이어붙인 참조 — 연속 구간을 찾지 못했습니다"}
    with open(LAST_REF, "wb") as o:
        o.write(wav)
    try:
        r = httpx.post(f"{RAON_URL}/session/start", headers=_headers(),
                       data={"persona": persona, "knowledge": knowledge, "session": session},
                       files={"voice": ("voice.wav", wav, "audio/wav")}, timeout=180)
    except Exception as e:
        raise HTTPException(502, f"Raon 서버에 연결할 수 없습니다: {e}")
    if r.status_code != 200:
        raise HTTPException(r.status_code, f"등록 실패: {r.text}")
    return {**r.json(), "sent": info}


def _to_wav24(raw, name):
    """무엇이 올라오든 24kHz 모노 wav 로 맞춘다. 서버가 참조로 쓰는 형식이다.

    음량도 맞춘다. 영상에서 딴 소리는 작게 녹음된 경우가 많은데, 참조가 작으면
    합성 결과가 눌리고 억양도 제대로 안 따라온다. 실측으로 잘 됐던 참조가
    peak RMS 0.14~0.20 이었고 실패한 것이 0.05 였다."""
    tmp = os.path.join(WORK, "_upload" + os.path.splitext(name)[1].lower())
    with open(tmp, "wb") as o:
        o.write(raw)
    try:
        y, _ = librosa.load(tmp, sr=24000, mono=True)
    finally:
        os.remove(tmp)
    if len(y) == 0:
        raise HTTPException(400, "오디오를 읽지 못했습니다")

    peak = float(np.abs(y).max())
    gain = min(10.0, 0.8 / peak) if peak > 1e-6 else 1.0   # 과증폭은 10배로 제한
    y = y * gain
    snr, quiet, warn = _quality(y)
    buf = io.BytesIO()
    sf.write(buf, y, 24000, format="WAV", subtype="PCM_16")
    return buf.getvalue(), len(y) / 24000, {
        "level": round(_level(y), 3), "gain": round(gain, 1),
        "snr_db": snr, "quiet_ratio": quiet, "warning": warn}


def _level(y):
    """0.1초 창 RMS 의 최댓값. 사람이 느끼는 음량에 가깝다."""
    w = 2400
    if len(y) < w:
        return float(np.sqrt((y ** 2).mean()))
    n = len(y) // w
    return float(np.sqrt((y[:n * w].reshape(n, w) ** 2).mean(axis=1)).max())


def _warn_text(snr, quiet):
    bad = []
    if snr is not None and snr < 30:
        bad.append(f"SNR {snr:.0f}dB (30dB 이상 권장)")
    if quiet is not None and quiet < 0.10:
        bad.append(f"쉬는 구간 {quiet:.0%} (10% 이상 권장)")
    if not bad:
        return ""
    return ("참조 품질이 낮습니다 — " + ", ".join(bad) +
            ". 오디오가 잘리거나 같은 소리를 반복할 수 있습니다.")


def _quality(y):
    """참조로 쓸 만한지 본다. 실측으로 갈린 두 지표만 본다 —

    잘 된 참조: SNR 39~58dB, 무음 15~28%.  실패한 참조: SNR 16~25dB, 무음 0~9%.
    배경음이 깔려 있어도 SNR 이 확보되면 잘 된다(노래 틀고 녹음한 것도 통과했다).
    쉼이 없는 참조는 모델이 멈출 줄 몰라서 같은 소리를 반복하는 일이 생긴다."""
    w = 2400
    n = len(y) // w
    if n < 2:
        return None, None, ""
    r = np.sqrt((y[:n * w].reshape(n, w) ** 2).mean(axis=1))
    peak, floor = float(r.max()), float(np.percentile(r, 10))
    snr = 20 * np.log10(max(peak, 1e-9) / max(floor, 1e-9))
    quiet = float((r < peak * 0.05).mean())
    return round(snr, 1), round(quiet, 3), _warn_text(snr, quiet)


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
