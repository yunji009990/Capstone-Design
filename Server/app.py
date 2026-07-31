import os, io, re, time, asyncio, difflib, tempfile, warnings
warnings.filterwarnings("ignore")
os.environ.setdefault("TQDM_DISABLE", "1")
import torch, numpy as np, soundfile as sf
from contextlib import asynccontextmanager
from urllib.parse import quote
from fastapi import FastAPI, UploadFile, File, Form, Header, HTTPException, BackgroundTasks
from fastapi.responses import Response, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

BASE  = os.environ.get("RAON_BASE", os.path.expanduser("~"))
MODEL = os.path.join(BASE, "models", "AX-K2-Raon-Speech")
SRV   = os.path.join(BASE, "server")
VOICE = os.environ.get("RAON_VOICE", os.path.join(SRV, "voice.wav"))
MEMFR = float(os.environ.get("RAON_MEM_FRACTION", "0.60"))
TOKENS= int(os.environ.get("RAON_ANSWER_TOKENS", "200"))
TURNS = int(os.environ.get("RAON_MAX_TURNS", "6"))
FRAME_CHUNK= int(os.environ.get("RAON_FRAME_CHUNK", "8"))
VERIFY= os.environ.get("RAON_VERIFY", "1") == "1"
TOKEN = os.environ.get("RAON_TOKEN", "")

S = {"pipe": None, "system": "", "loaded_at": 0.0}
HIST, LOCK = {}, asyncio.Lock()

def build_system():
    parts = []
    for f in ("persona.md", "knowledge.md"):
        p = os.path.join(SRV, f)
        if os.path.exists(p):
            parts.append(open(p, encoding="utf-8").read().strip())
    return "\n\n".join(parts)

def to_wav(res):
    wav, sr = res
    a = wav.detach().cpu().float().numpy()
    if a.ndim > 1: a = a.squeeze()
    buf = io.BytesIO(); sf.write(buf, a, sr, format="WAV", subtype="PCM_16")
    return buf.getvalue()

_norm = lambda s: re.sub(r"[^가-힣a-zA-Z0-9]", "", s or "")
def _sim(a, b):
    a, b = _norm(a), _norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0

def synth(text, tries=2):
    """음성 합성 + 검증 + 실패 시 재생성"""
    pipe = S["pipe"]
    for i in range(tries):
        res = pipe.tts(text, speaker_audio=VOICE)
        data = to_wav(res)
        if not VERIFY:
            return data, None
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
            t.write(data); tmp = t.name
        try:
            heard = pipe.stt(tmp)
        finally:
            os.unlink(tmp)
        if _sim(heard, text) >= 0.4:
            return data, heard
        print(f"[검증실패 {i+1}회] 기대={text!r} 실제={heard!r}", flush=True)
    return data, heard

@asynccontextmanager
async def lifespan(app):
    t0 = time.time()
    torch.cuda.set_per_process_memory_fraction(MEMFR)
    from transformers import AutoConfig
    from transformers.dynamic_module_utils import get_class_from_dynamic_module
    cfg = AutoConfig.from_pretrained(MODEL, trust_remote_code=True)
    RP = get_class_from_dynamic_module("modeling_raon.RaonPipeline", MODEL,
                                       revision=getattr(cfg, "_commit_hash", None))
    S["pipe"] = RP(MODEL, device="cuda", dtype="bfloat16")
    S["system"] = build_system()
    if os.environ.get("RAON_COMPILE", "1") == "1":
        try:
            _m = S["pipe"].model
            _m = _m.get_model() if hasattr(_m, "get_model") else _m
            _m.code_predictor.forward = torch.compile(
                _m.code_predictor.forward, fullgraph=False)
            print("[컴파일] code_predictor 적용", flush=True)
        except Exception as e:
            print(f"[컴파일 실패] {e}", flush=True)
    print(f"[로딩] {time.time()-t0:.1f}초", flush=True)
    S["pipe"].tts("준비 완료.", speaker_audio=VOICE)   # 첫 생성 불안정 방지 예열
    S["loaded_at"] = time.time()
    print(f"[준비완료] VRAM {torch.cuda.memory_allocated()/1024**3:.1f}GB", flush=True)
    yield
    S["pipe"] = None

app = FastAPI(lifespan=lifespan)

def auth(tok):
    if TOKEN and tok != TOKEN:
        raise HTTPException(401, "invalid token")

@app.get("/health")
def health():
    return {"status": "ready" if S["pipe"] else "loading",
            "vram_gb": round(torch.cuda.memory_allocated()/1024**3, 1),
            "uptime_sec": round(time.time() - S["loaded_at"]) if S["loaded_at"] else 0,
            "voice": os.path.basename(VOICE), "sessions": len(HIST)}

@app.post("/reload")
def reload_prompt(x_token: str = Header("")):
    auth(x_token); S["system"] = build_system()
    return {"ok": True, "system_chars": len(S["system"])}

@app.post("/reset")
def reset(session: str = Form("default"), x_token: str = Header("")):
    auth(x_token); HIST.pop(session, None)
    return {"ok": True}

@app.post("/tts")
async def tts_ep(text: str = Form(...), x_token: str = Header("")):
    auth(x_token)
    async with LOCK:
        t0 = time.time(); data, _ = synth(text)
    return Response(data, media_type="audio/wav",
                    headers={"X-Elapsed": f"{time.time()-t0:.2f}"})

@app.post("/stt")
async def stt_ep(file: UploadFile = File(...), x_token: str = Header("")):
    auth(x_token)
    raw = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        t.write(raw); p = t.name
    try:
        async with LOCK:
            return {"text": S["pipe"].stt(p)}
    finally:
        os.unlink(p)

async def _record_history(session: str, path: str, answer: str):
    """응답을 보낸 뒤 사용자 발화를 받아적어 히스토리를 채운다."""
    try:
        async with LOCK:
            heard = S["pipe"].stt(path)
        h = HIST.setdefault(session, [])
        h += [{"role": "user", "content": heard},
              {"role": "assistant", "content": answer}]
        del h[:-TURNS*2]
        print(f"[{session}] (후처리) {heard!r}", flush=True)
    except Exception as e:
        print(f"[후처리 실패] {e}", flush=True)
    finally:
        try: os.unlink(path)
        except OSError: pass


@app.post("/talk")
async def talk(background: BackgroundTasks, file: UploadFile = File(...),
               session: str = Form("default"), show_heard: str = Form("1"),
               x_token: str = Header("")):
    auth(x_token)
    if not S["pipe"]:
        raise HTTPException(503, "model not ready")
    want_heard = show_heard not in ("0", "false", "False", "")
    raw = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        t.write(raw); p = t.name

    deferred = False
    try:
        async with LOCK:
            pipe, t0 = S["pipe"], time.time()
            heard = pipe.stt(p) if want_heard else ""
            t1 = time.time()
            msgs = [{"role": "system", "content": S["system"]}]
            msgs += HIST.get(session, [])
            msgs.append({"role": "user", "content": [{"type": "audio", "audio": p}]})
            answer = pipe.chat(msgs, max_new_tokens=TOKENS, temperature=0.7)
            t2 = time.time()
            data, _ = synth(answer)
            t3 = time.time()
            if want_heard:
                h = HIST.setdefault(session, [])
                h += [{"role": "user", "content": heard},
                      {"role": "assistant", "content": answer}]
                del h[:-TURNS*2]
            print(f"[{session}] {heard!r} -> {answer!r} "
                  f"(인식{t1-t0:.1f} 생성{t2-t1:.1f} 합성{t3-t2:.1f} 총{t3-t0:.1f}초)", flush=True)

        headers = {"X-Answer": quote(answer), "X-Elapsed": f"{t3-t0:.2f}"}
        if want_heard:
            headers["X-Heard"] = quote(heard)
        else:
            background.add_task(_record_history, session, p, answer)
            deferred = True
        return Response(data, media_type="audio/wav", headers=headers)
    finally:
        if not deferred:
            try: os.unlink(p)
            except OSError: pass


@app.post("/talk_stream")
async def talk_stream(file: UploadFile = File(...), session: str = Form("default"),
                      show_heard: str = Form("1"), x_token: str = Header("")):
    auth(x_token)
    if not S["pipe"]:
        raise HTTPException(503, "model not ready")
    want_heard = show_heard not in ("0", "false", "False", "")
    raw = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        t.write(raw); p = t.name

    # 답변 텍스트는 헤더로 먼저 나가야 하므로 여기서 확정한다
    async with LOCK:
        pipe, t0 = S["pipe"], time.time()
        heard = pipe.stt(p) if want_heard else ""
        msgs = [{"role": "system", "content": S["system"]}]
        msgs += HIST.get(session, [])
        msgs.append({"role": "user", "content": [{"type": "audio", "audio": p}]})
        answer = pipe.chat(msgs, max_new_tokens=TOKENS, temperature=0.7)
        if want_heard:
            h = HIST.setdefault(session, [])
            h += [{"role": "user", "content": heard},
                  {"role": "assistant", "content": answer}]
            del h[:-TURNS*2]
        t1 = time.time()

    print(f"[{session}] {heard!r} -> {answer!r} (텍스트 {t1-t0:.1f}초)", flush=True)

    async def body():
        loop = asyncio.get_running_loop()
        q: asyncio.Queue = asyncio.Queue()

        def cb(w):
            a = w.detach().cpu().float().numpy()
            a = (np.clip(a, -1.0, 1.0) * 32767).astype("<i2")
            loop.call_soon_threadsafe(q.put_nowait, a.tobytes())

        def run():
            m = S["pipe"].model
            g = getattr(m, "get_model", None)
            ts = [m]
            if callable(g) and g() is not m:
                ts.append(g())
            for t in ts:
                t._frame_callback = cb
                t._frame_chunk = FRAME_CHUNK
            try:
                S["pipe"].tts(answer, speaker_audio=VOICE)
            except Exception as e:
                print(f"[{session}] 합성 실패: {e}", flush=True)
            finally:
                for t in ts:
                    t._frame_callback = None
                loop.call_soon_threadsafe(q.put_nowait, None)

        try:
            async with LOCK:
                worker = asyncio.create_task(asyncio.to_thread(run))
                n, first = 0, True
                while True:
                    it = await q.get()
                    if it is None:
                        break
                    if first:
                        el = time.time() - t0
                        print(f"[{session}]   첫 프레임 {el:.2f}초", flush=True)
                        first = False
                    n += len(it)
                    yield it
                await worker
            el = time.time() - t0
            print(f"[{session}]   완료 {el:.2f}초 {n/48000:.1f}초분", flush=True)
        finally:
            if want_heard:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    bg = None if want_heard else BackgroundTask(_record_history, session, p, answer)
    return StreamingResponse(body(), media_type="application/octet-stream", background=bg, headers={
        "X-Heard": quote(heard), "X-Answer": quote(answer),
        "X-Sample-Rate": "24000", "X-Channels": "1",
    })
