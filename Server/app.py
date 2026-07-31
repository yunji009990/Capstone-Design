import os, io, re, time, asyncio, difflib, shutil, tempfile, warnings
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
MEMFR = float(os.environ.get("RAON_MEM_FRACTION", "0.60"))
TOKENS= int(os.environ.get("RAON_ANSWER_TOKENS", "200"))
TURNS = int(os.environ.get("RAON_MAX_TURNS", "6"))
FRAME_CHUNK= int(os.environ.get("RAON_FRAME_CHUNK", "8"))
VERIFY= os.environ.get("RAON_VERIFY", "1") == "1"
CONT  = os.environ.get("RAON_CONT", "0") == "1"   # 시작값. 실제 판단은 S["cont"]
# 반복 억제 기본값은 실측으로 정했다. 창을 넓히는 게 임계값을 낮추는 것보다 낫다 —
# 창 100(8초)이면 1.5초 쉼은 19%라 안 걸리고, 8초를 뒤덮는 루프는 70%가 넘어 걸린다.
# 창 40 / 임계 0.2 는 루프를 잡긴 했지만 무음에서도 발동할 여지가 컸다.
RAS   = os.environ.get("RAON_RAS", "1") == "1"
RAS_WIN  = int(os.environ.get("RAON_RAS_WINDOW", "100"))
RAS_THR  = float(os.environ.get("RAON_RAS_THRESHOLD", "0.35"))
CONT_FRAMES = int(os.environ.get("RAON_CONT_FRAMES", "200"))   # 200프레임 = 16초
TOKEN = os.environ.get("RAON_TOKEN", "")

S = {"pipe": None, "loaded_at": 0.0, "cont": CONT}
HIST, LOCK = {}, asyncio.Lock()

# ── 세션 ────────────────────────────────────────────────────────────
# 인물은 반드시 웹에서 등록한다. 서버에 기본 음성·기본 인물을 두지 않는다.
# 폴백이 있으면 "지금 무엇이 쓰이는지" 알 수 없고, 등록을 잊었을 때
# 오류가 아니라 엉뚱한 목소리로 답해서 코드 결함처럼 보인다.
SESS_DIR = os.path.join(SRV, "sessions")
SESS = {}                       # sid -> {"voice": path, "system": str}
CURRENT = {"session": None}

# 세션 페르소나 앞에 항상 붙는 규칙.
# 이건 캐릭터 설정이 아니라 음성 대화에 필요한 기술 제약이라, 웹이 매번
# 적어 보내게 하면 반드시 빠뜨린다. 특히 첫 줄이 중요하다 — 짧은 페르소나는
# 베이스 모델의 "AI 비서" 기본 성격을 못 이겨서 목록과 설명을 늘어놓는다.
BASE_RULES = """당신은 AI 비서가 아니라 아래 [인물]에 설명된 사람입니다.
설명·조언·목록을 늘어놓지 말고, 그 사람이 되어 사람처럼 대화하세요.

말하기 규칙
- 한 문장, 길어도 두 문장으로 답하세요. 40자 안팎이면 충분합니다.
- 한 번에 길게 말하지 말고 상대가 대답할 틈을 주세요.
- 이모지, 특수기호, 목록, 번호, 굵은 글씨를 쓰지 마세요. 음성으로 읽힙니다.
- "무엇을 도와드릴까요", "말씀해 주세요" 같은 상담원 말투를 쓰지 마세요.
- 되묻기만 하지 말고 당신 이야기도 하세요.
- 직전에 한 말과 같은 문장을 반복하지 마세요.
- 모르면 솔직하게 모른다고 말하세요."""

def _sess_path(sid, *parts):
    return os.path.join(SESS_DIR, sid, *parts)

def _sess_system(sid):
    """공통 규칙 + 인물 설명 + 사전지식. 웹은 인물만 보내면 된다."""
    persona = knowledge = ""
    p = _sess_path(sid, "persona.md")
    if os.path.exists(p):
        persona = open(p, encoding="utf-8").read().strip()
    k = _sess_path(sid, "knowledge.md")
    if os.path.exists(k):
        knowledge = open(k, encoding="utf-8").read().strip()
    if not persona:
        return ""
    out = f"{BASE_RULES}\n\n[인물]\n{persona}"
    if knowledge:
        out += f"\n\n[사전지식]\n{knowledge}"
    return out

def load_sessions():
    """재시작해도 등록된 세션이 살아 있도록 디스크에서 복원한다."""
    if not os.path.isdir(SESS_DIR):
        return
    for sid in sorted(os.listdir(SESS_DIR)):
        v = _sess_path(sid, "voice.wav")
        if os.path.exists(v):
            SESS[sid] = {"voice": v, "system": _sess_system(sid)}
            CURRENT["session"] = sid
    if SESS:
        print(f"[세션] {len(SESS)}개 복원, 현재={CURRENT['session']}", flush=True)

def need_session(sid):
    """등록되지 않은 세션이면 409. 폴백 없이 분명하게 거절한다."""
    s = SESS.get(sid)
    if not s or not s.get("system"):
        raise HTTPException(409, f"등록되지 않은 세션입니다: {sid or '(없음)'}. "
                                 f"웹에서 인물을 먼저 등록하세요.")
    return s

def sess_voice(sid):
    return need_session(sid)["voice"]

def sess_system(sid):
    return need_session(sid)["system"]

def _save_atomic(data, path):
    """임시 파일에 쓴 뒤 rename. 합성 중 반쪽 파일을 읽는 사고를 막는다."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".part"
    with open(tmp, "wb") as o:
        o.write(data)
    os.replace(tmp, path)

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

REF_TEXT = {}                   # 참조음성 경로 -> 전사

def ref_text(voice):
    """tts_continuation 은 참조 전사가 필요한데, 없으면 합성마다 STT 를 다시 돈다.
    참조 음성은 세션당 고정이므로 한 번만 전사해 둔다."""
    if voice not in REF_TEXT:
        t0 = time.time()
        REF_TEXT[voice] = S["pipe"].stt(voice)
        print(f"[참조전사] {time.time()-t0:.1f}초 — {REF_TEXT[voice]!r}", flush=True)
    return REF_TEXT[voice]

def frame_targets():
    """프레임 훅을 걸 대상. 래퍼와 실제 모델이 다를 수 있어 둘 다 건다."""
    m = S["pipe"].model
    g = getattr(m, "get_model", None)
    return [m, g()] if callable(g) and g() is not m else [m]

def generate(text, voice):
    """오디오 생성. 반환 파형 대신 프레임 훅으로 생성 중에 직접 모은다.

    tts_continuation 의 반환값은 프리필한 참조 프레임을 빼는 길이 계산이 어긋나
    거의 비어 있다(같은 세션·같은 참조로 반환 0.3초 / 훅 4.5초). 훅으로 모으면
    스트리밍과 비스트리밍이 같은 오디오를 보게 되고, 계측도 믿을 수 있다."""
    chunks = []
    ts = frame_targets()
    for t in ts:
        t._frame_callback = lambda w: chunks.append(w.detach().cpu().float().numpy())
        t._frame_chunk = FRAME_CHUNK
    try:
        if S["cont"]:
            S["pipe"].tts_continuation(text, ref_audio=voice, ref_text=ref_text(voice))
        else:
            S["pipe"].tts(text, speaker_audio=voice)
    finally:
        for t in ts:
            t._frame_callback = None
    a = np.concatenate(chunks) if chunks else np.zeros(0, dtype="float32")
    return torch.from_numpy(a), 24000

def warm_pipe(voice):
    """첫 생성은 코드 경로가 처음 도느라 느리다. continuation 은 참조를 토큰화·프리필하는
    별도 경로라 tts() 로 예열해도 소용없다. 참조 전사도 여기서 캐시된다."""
    t0 = time.time()
    if S["cont"]:
        S["pipe"].tts_continuation("준비 완료.", ref_audio=voice, ref_text=ref_text(voice))
    else:
        S["pipe"].tts("준비 완료.", speaker_audio=voice)
    print(f"[예열] {time.time()-t0:.1f}초", flush=True)

def synth(text, voice, tries=2):
    """음성 합성 + 검증 + 실패 시 재생성. 참조는 호출자가 반드시 준다."""
    pipe = S["pipe"]
    for i in range(tries):
        t0 = time.time()
        res = generate(text, voice)
        print(f"[합성] {res[0].numel()/res[1]:.1f}초 분량 / {time.time()-t0:.1f}초 소요"
              f"{' (continuation)' if S['cont'] else ''}", flush=True)
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

    # 반복 억제(RAS)가 기본으로 꺼져 있다. 꺼두면 오디오가 "아아아아안아아안…"
    # 처럼 같은 소리를 반복하다 max_new_tokens 에 걸려서야 끊긴다(512프레임=41초).
    #
    # RAS 는 "방금 뽑은 토큰이 최근 window 프레임의 threshold 비율을 넘으면 다시
    # 뽑는다"는 규칙이다. 기본 0.5 는 느슨해서 두세 토큰이 번갈아 도는 패턴을
    # 놓친다. 같은 파일의 다른 호출부가 0.1/40 을 쓰기에 그쪽에 맞춰 조인다.
    # 다만 무음도 같은 토큰이 이어지므로 너무 낮추면 쉼에 잡음이 낀다 — 0.2 로 둔다.
    #
    # 폭주해도 16초에서 끊기게 상한도 낮춘다. 40자 답변이면 5초면 충분하다.
    tp = S["pipe"].task_params
    cont = dict(tp.get("tts_continuation", tp.get("tts", {})))
    cont.update({"ras_enabled": RAS, "ras_window_size": RAS_WIN,
                 "ras_repetition_threshold": RAS_THR,
                 "max_new_tokens": CONT_FRAMES})
    tp["tts_continuation"] = cont
    print(f"[설정] tts_continuation — {cont}", flush=True)

    load_sessions()
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
    # 첫 생성 불안정 방지 예열. continuation 은 참조를 토큰화·프리필하는 다른 코드
    # 경로라 tts() 로 예열해도 소용없다. 현재 세션의 참조로 예열하면 전사까지 캐시된다.
    if CURRENT["session"]:
        warm_pipe(sess_voice(CURRENT["session"]))
    else:
        print("[예열] 등록된 세션이 없어 건너뜀 — 첫 등록 때 예열한다", flush=True)
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
            "sessions": len(HIST), "registered": len(SESS),
            "current": CURRENT["session"], "ready_to_talk": bool(CURRENT["session"]),
            "cont": S["cont"]}

@app.post("/mode")
async def set_mode(cont: str = Form(...), x_token: str = Header("")):
    """억양 복제(continuation)를 재시작 없이 켜고 끈다.

    둘을 번갈아 들어보며 비교하려면 재시작이 25초씩 드는데, 그 사이 기억이
    흐려져서 비교가 안 된다. 바꾼 뒤 현재 세션 참조로 예열까지 해 둔다."""
    auth(x_token)
    new = cont not in ("0", "false", "False", "")
    if new != S["cont"]:
        S["cont"] = new
        print(f"[설정] CONT={'1 (억양 복제)' if new else '0 (음색만)'}", flush=True)
        if CURRENT["session"]:
            async with LOCK:
                await asyncio.to_thread(warm_pipe, sess_voice(CURRENT["session"]))
    return {"cont": S["cont"], "session": CURRENT["session"]}

@app.post("/reset")
def reset(session: str = Form("default"), x_token: str = Header("")):
    auth(x_token); HIST.pop(session, None)
    return {"ok": True}

@app.post("/tts")
async def tts_ep(text: str = Form(...), session: str = Form(""), x_token: str = Header("")):
    auth(x_token)
    voice = sess_voice(session or CURRENT["session"])
    async with LOCK:
        t0 = time.time(); data, _ = synth(text, voice)
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
    need_session(session)        # 임시 파일을 만들기 전에 먼저 거절한다
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
            msgs = [{"role": "system", "content": sess_system(session)}]
            msgs += HIST.get(session, [])
            msgs.append({"role": "user", "content": [{"type": "audio", "audio": p}]})
            answer = pipe.chat(msgs, max_new_tokens=TOKENS, temperature=0.7)
            t2 = time.time()
            data, _ = synth(answer, sess_voice(session))
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
    need_session(session)        # 임시 파일을 만들기 전에 먼저 거절한다
    want_heard = show_heard not in ("0", "false", "False", "")
    raw = await file.read()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as t:
        t.write(raw); p = t.name

    # 답변 텍스트는 헤더로 먼저 나가야 하므로 여기서 확정한다
    async with LOCK:
        pipe, t0 = S["pipe"], time.time()
        heard = pipe.stt(p) if want_heard else ""
        msgs = [{"role": "system", "content": sess_system(session)}]
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
            ts = frame_targets()
            for t in ts:
                t._frame_callback = cb
                t._frame_chunk = FRAME_CHUNK
            try:
                v = sess_voice(session)
                if S["cont"]:
                    S["pipe"].tts_continuation(answer, ref_audio=v, ref_text=ref_text(v))
                else:
                    S["pipe"].tts(answer, speaker_audio=v)
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


# ── 세션 등록 (웹 백엔드 → 서버) ────────────────────────────────────

@app.post("/session/start")
async def session_start(persona: str = Form(...), knowledge: str = Form(""),
                        session: str = Form(""), voice: UploadFile = File(...),
                        model: UploadFile = File(None), x_token: str = Header("")):
    auth(x_token)
    sid = (session or time.strftime("%Y%m%d_%H%M%S")).strip()
    if "/" in sid or "\\" in sid or sid.startswith("."):
        raise HTTPException(400, "잘못된 세션 ID")

    raw = await voice.read()
    try:
        info = sf.info(io.BytesIO(raw))
    except Exception as e:
        raise HTTPException(400, f"참조 음성이 WAV가 아니거나 손상됨: {e}")
    if info.duration < 3:
        raise HTTPException(400, f"참조 음성이 너무 짧습니다({info.duration:.1f}초). 10~30초를 권장합니다")
    if not (8 <= info.duration <= 40):
        print(f"[세션] 경고: 참조 음성 {info.duration:.1f}초 (권장 10~30초)", flush=True)

    # 짧은 페르소나는 베이스 모델의 비서 성격을 못 이긴다. 말투와 대화 예시가 있어야 한다.
    warn = ""
    if len(persona.strip()) < 120:
        warn = (f"페르소나가 {len(persona.strip())}자로 짧습니다. 말투와 대화 예시를 포함해 "
                f"200자 이상을 권장합니다. 짧으면 캐릭터가 아니라 AI 비서처럼 답합니다")
        print(f"[세션] 경고: {warn}", flush=True)

    _save_atomic(raw, _sess_path(sid, "voice.wav"))
    _save_atomic(persona.strip().encode("utf-8"), _sess_path(sid, "persona.md"))
    if knowledge.strip():
        _save_atomic(knowledge.strip().encode("utf-8"), _sess_path(sid, "knowledge.md"))
    has_model = model is not None and model.filename
    if has_model:
        _save_atomic(await model.read(), _sess_path(sid, "model.glb"))

    SESS[sid] = {"voice": _sess_path(sid, "voice.wav"), "system": _sess_system(sid)}
    CURRENT["session"] = sid
    HIST.pop(sid, None)          # 인물이 바뀌었으므로 이전 대화는 버린다
    REF_TEXT.pop(SESS[sid]["voice"], None)   # 같은 경로에 다른 음성이 덮였다

    # 서버는 한 번에 한 인물만 보관한다. 세션 ID 를 비우면 시각으로 자동 생성되므로
    # 지우지 않으면 등록할 때마다 쌓인다. 원본은 웹에 있으니 언제든 다시 만들 수 있고,
    # 실존 인물의 음성을 서버에 오래 두지 않는 편이 낫다.
    for old in [s for s in SESS if s != sid]:
        SESS.pop(old, None)
        HIST.pop(old, None)
        REF_TEXT.pop(_sess_path(old, "voice.wav"), None)
        shutil.rmtree(_sess_path(old), ignore_errors=True)
        print(f"[세션] {old} 정리", flush=True)
    async with LOCK:
        # 새 참조로 예열해 둔다. 등록은 지연에 민감하지 않고, 첫 대화는 민감하다.
        await asyncio.to_thread(warm_pipe, SESS[sid]["voice"])
    print(f"[세션] {sid} 등록 — 음성 {info.duration:.1f}초, "
          f"페르소나 {len(persona)}자, 모델 {'있음' if has_model else '없음'}", flush=True)
    out = {"session": sid, "voice_sec": round(info.duration, 1), "has_model": bool(has_model)}
    if warn:
        out["warning"] = warn
    return out


@app.post("/session/{sid}/model")
async def session_model_put(sid: str, model: UploadFile = File(...), x_token: str = Header("")):
    """Meshy 생성이 늦게 끝나는 경우 모델만 나중에 올린다."""
    auth(x_token)
    if sid not in SESS:
        raise HTTPException(404, "등록되지 않은 세션")
    _save_atomic(await model.read(), _sess_path(sid, "model.glb"))
    print(f"[세션] {sid} 모델 등록", flush=True)
    return {"ok": True}


@app.get("/session/current")
def session_current(x_token: str = Header("")):
    auth(x_token)
    sid = CURRENT["session"]
    if not sid:
        return {"session": None}
    return {"session": sid, "has_model": os.path.exists(_sess_path(sid, "model.glb"))}


@app.get("/session/{sid}/model.glb")
def session_model_get(sid: str, x_token: str = Header("")):
    auth(x_token)
    p = _sess_path(sid, "model.glb")
    if not os.path.exists(p):
        raise HTTPException(404, "모델이 아직 없습니다")
    with open(p, "rb") as f:
        return Response(f.read(), media_type="model/gltf-binary")


@app.post("/session/end")
def session_end(session: str = Form(...), x_token: str = Header("")):
    """세션의 음성·모델·대화기록을 지운다. 실존 인물의 자료이므로 확실히 삭제한다."""
    auth(x_token)
    s = SESS.pop(session, None)
    if s:
        REF_TEXT.pop(s["voice"], None)
    HIST.pop(session, None)
    if CURRENT["session"] == session:
        CURRENT["session"] = None
    d = _sess_path(session)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    print(f"[세션] {session} 종료·삭제", flush=True)
    return {"ok": True}
