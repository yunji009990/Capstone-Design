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
TURNS = int(os.environ.get("RAON_MAX_TURNS", "30"))
# 요약은 껐다(2026-08-26). 원래는 6턴마다 앞쪽을 접고 3턴만 원문으로 남겼는데,
# 재보니 접는 것이 순전히 손해였다. final 8회차로 나란히 잰 결과다.
#
#            기억     길이초과  꼬리고착  응답 평균  3초 넘김
#   요약 켬   58/64    27/160      5      1.06초    5/160
#   요약 끔   62/64    20/160      0      0.70초    0/160
#
# 접는 이유로 적혀 있던 "뒤로 갈수록 말투 규칙이 풀리고 이미 한 질문을 또 한다"는
# 재현되지 않았다 — 존댓말·상담원 0 그대로고 질문반복은 0.17 → 0.15 였다.
# 요약이 사실을 버리는 문제로 하루에 여섯 번을 고쳤는데, 없애는 것이 답이었다.
#
# 원래 TURNS 는 6이었고 왜 6인지 기록이 없다. 요약은 그 위에 얹은 처방이었다 —
# 6턴을 넘으면 앞이 잘리니 잘린 것을 살리자는 것이다. 전제를 아무도 안 건드렸다.
#
# **30 을 함부로 올리지 말 것.** 100턴 대본을 걸었더니 70턴 근처에서 OOM 으로 죽었다.
# 터지는 것은 KV 캐시가 아니라 어텐션 행렬이고, 문맥 길이의 제곱으로 커진다.
# 문맥 창이 131,072 토큰이라 토큰만 세면 3,600턴이 들어가지만 메모리로는 70턴이다.
# 늘리려면 VRAM 을 먼저 재라 — RAON_MEM_FRACTION 은 0.70 그대로 둔다.
#
# 30 자체는 근거 없이 정해진 값이지만 결과적으로 안전한 쪽이다. 체험이 몇 턴인지는
# 아무 데도 없으니 헤드셋 테스트 때 세어 볼 것. 되돌리려면 RAON_SUMMARY=1.
KEEP  = int(os.environ.get("RAON_KEEP_TURNS", "3"))
SUMM  = os.environ.get("RAON_SUMMARY", "0") == "1"
# 대화 중에 사용자가 말한 사실을 사전지식 옆에 적립한다. 0 이면 끈다.
#
# 요약(SUMM)과 헷갈리지 말 것. 요약은 **접기**다 — 원문 턴을 버리고 압축본으로
# 바꾼다. 그래서 기억이 62/64 에서 50/64 로 무너져 껐다. 적립은 **더하기**라
# 버리는 것이 없고, 30턴을 넘겨 앞이 잘릴 때 사실만 건져 두는 쪽으로 작동한다.
LEARN = os.environ.get("RAON_LEARN", "1") == "1"
LEARN_MAX = int(os.environ.get("RAON_LEARN_CHARS", "600"))
# 이보다 짧은 발화는 모델에 안 물어본다. "응", "그러게", "보고 싶었어" 에서 건질
# 것은 없는데 호출값은 똑같이 든다. 짧은 사실을 놓치는 쪽이 안전한 실패다.
LEARN_MIN = int(os.environ.get("RAON_LEARN_MIN", "8"))
# 앞선 답변과 마지막 문장이 이만큼 닮으면 다시 뽑는다. 0 이면 끈다.
REGEN = float(os.environ.get("RAON_REGEN_SIM", "0.6"))
# 요약이 이보다 길어질 때만 통째로 다시 접는다. 매번 다시 접으면 사실이 깎인다.
SUMM_MAX = int(os.environ.get("RAON_SUMMARY_CHARS", "600"))
FRAME_CHUNK= int(os.environ.get("RAON_FRAME_CHUNK", "8"))
VERIFY= os.environ.get("RAON_VERIFY", "1") == "1"
CONT  = os.environ.get("RAON_CONT", "0") == "1"   # 시작값. 실제 판단은 S["cont"]
# 반복 억제 기본값은 실측으로 정했다. 창을 넓히는 게 임계값을 낮추는 것보다 낫다 —
# 창 100(8초)이면 1.5초 쉼은 19%라 안 걸리고, 8초를 뒤덮는 루프는 70%가 넘어 걸린다.
# 창 40 / 임계 0.2 는 루프를 잡긴 했지만 무음에서도 발동할 여지가 컸다.
RAS   = os.environ.get("RAON_RAS", "1") == "1"
RAS_WIN  = int(os.environ.get("RAON_RAS_WINDOW", "100"))
RAS_THR  = float(os.environ.get("RAON_RAS_THRESHOLD", "0.35"))
# 답변 생성 온도. **아래 RAON_TEMP 와 다른 것이다** — 이건 글, 저건 소리다.
# 문서 「하지 말 것」의 "temperature 낮추기"는 소리(지지직) 얘기라 여기와 무관하다.
#
# 0.7 은 근거 없이 박혀 있던 값이다. 낮추면 흔들림이 줄 것으로 보인다 — 판정기를
# 0.1 로 돌리면 18번 중 17번 맞힌다. 대신 첫 인사가 더 획일화된다(127번에 서로
# 다른 답이 16가지, 그중 둘이 68%). 꼬리고착도 이 온도에 기대어 잡혀 있다.
# **재보고 정할 것.** 환경변수로 뺀 이유가 그것이다 — 재시작만으로 훑을 수 있다.
CHAT_TEMP = float(os.environ.get("RAON_CHAT_TEMP", "0.7"))
# 아래는 **소리 합성** 파라미터. 답변 텍스트와 무관하다.
# 기본값은 모델이 쓰던 값 그대로다 — 지정하지 않으면 동작이 안 바뀐다.
# temperature 는 모델 task_params 가 1.2 로 덮고 있었다(함수 기본값은 1.0).
TEMP  = float(os.environ.get("RAON_TEMP", "1.2"))
TOPK  = int(os.environ.get("RAON_TOP_K", "20"))
TOPP  = float(os.environ.get("RAON_TOP_P", "0.8"))
CONT_FRAMES = int(os.environ.get("RAON_CONT_FRAMES", "200"))   # 200프레임 = 16초
TOKEN = os.environ.get("RAON_TOKEN", "")

S = {"pipe": None, "loaded_at": 0.0, "cont": CONT}
HIST, LOCK = {}, asyncio.Lock()
SUMM_TEXT = {}                  # session -> 접어둔 앞부분의 요약
KNOWN = {}                      # session -> 대화 중에 사용자에게 들은 사실 (줄 목록)
JUDGED = {}                     # session -> 판정기가 마지막으로 뱉은 말 (디버그)

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
# 전부 지시형으로 쓴다. 금지형("~하지 마세요")은 무엇을 하라는 말이 없어서 모델이
# 빈칸을 제 기본값으로 채운다. 20턴 대화 한 회차에서 길이 규칙을 어긴 턴이
# 15.0 에서 6.5 로 줄었다(회차 편차 2~3). 자세한 근거는 docs/페르소나_작성규격.md.
BASE_RULES = """당신은 아래 [인물]에 적힌 사람입니다. 그 사람이 되어 사람처럼 대화하세요.

말하기 규칙
- 한 번에 한두 문장만 말하고 상대가 대답할 차례를 남깁니다. 40자 안팎이면 충분합니다.
- 글자와 쉼표, 마침표, 물음표만 씁니다. 이 말은 소리로 읽힙니다.
- 아는 사이끼리 하는 말로 합니다.
- 상대가 방금 한 말에 반응하고, 거기서 이어지는 것을 말합니다.
- 매번 새 문장을 만듭니다. 특히 마지막 문장은 앞서 한 말과 다르게 끝냅니다.
- 되물을 때도 있고 당신 이야기를 할 때도 있습니다. 번갈아 합니다.
- 확실한 것만 말하고, 기억이 흐릿하면 되묻습니다."""

def _sess_path(sid, *parts):
    return os.path.join(SESS_DIR, sid, *parts)

EX_HEAD = "[대화 예시]"
EX_NOTE = "[예시 사용법]"

def _split_examples(persona):
    """[대화 예시] 블록을 페르소나에서 떼어 진짜 대화 턴으로 만든다.

    시스템 프롬프트 안의 글로 두면 모델이 '설명'으로 읽고, user/assistant 턴으로
    두면 '내가 이렇게 말했다'로 읽는다. 예시가 하는 일은 말투 유지가 아니라 어투
    유지이고(docs/페르소나_작성규격.md) 빼면 설교조가 네 배로 는다 — 더 강하게
    따르게 할 값어치가 있다.

    형식은 Web/persona.py 가 만드는 것을 따른다 — "사용자:" 다음 줄이 답이다.
    답하는 쪽 이름은 관계어(엄마·형·친구…)이고 설문 자유 입력이라, 이름으로
    찾지 않고 차례로만 짝짓는다.

    블록이 없거나 형식이 어긋나면 원본을 그대로 돌려준다. 파싱 실패로 예시가
    통째로 사라지느니 예전처럼 글로라도 두는 게 낫다."""
    lines = persona.splitlines()
    head = next((n for n, l in enumerate(lines) if l.strip() == EX_HEAD), -1)
    if head < 0:
        return persona, []
    turns, body, n = [], lines[head + 1:], 0
    while n + 1 < len(body):
        u, a = body[n].strip(), body[n + 1].strip()
        if not u.startswith("사용자:") or ":" not in a:
            break
        turns += [{"role": "user", "content": u.split(":", 1)[1].strip()},
                  {"role": "assistant", "content": a.split(":", 1)[1].strip()}]
        n += 2
    if not turns:
        return persona, []
    # "아래는 말투의 본보기입니다" 가 가리킬 아래가 없어졌다.
    rest = [f"{EX_NOTE} 앞서 나눈 대화는 말투의 본보기입니다. "
            "상황에 맞는 문장을 새로 만들어 말합니다."
            if l.startswith(EX_NOTE) else l
            for l in lines[:head] + body[n:]]
    return "\n".join(rest).strip(), turns

def _sess_build(sid):
    """공통 규칙 + 인물 설명 + 사전지식, 그리고 대화 예시 턴. 웹은 인물만 보내면 된다.

    규칙은 등록할 때 rules 로 덮어쓸 수 있다. 기본값을 서버가 갖고 있는 이유는
    위와 같지만, 이 규칙 자체가 답변 품질을 좌우해서 재배포 없이 바꿔가며 재볼 수
    있어야 한다. 안 보내면 BASE_RULES 가 그대로 쓰인다."""
    persona = knowledge = ""
    rules = BASE_RULES
    r = _sess_path(sid, "rules.md")
    if os.path.exists(r):
        rules = open(r, encoding="utf-8").read().strip() or BASE_RULES
    p = _sess_path(sid, "persona.md")
    if os.path.exists(p):
        persona = open(p, encoding="utf-8").read().strip()
    k = _sess_path(sid, "knowledge.md")
    if os.path.exists(k):
        knowledge = open(k, encoding="utf-8").read().strip()
    if not persona:
        return {"system": "", "examples": []}
    persona, examples = _split_examples(persona)
    out = f"{rules}\n\n[인물]\n{persona}"
    if knowledge:
        out += f"\n\n[사전지식]\n{knowledge}"
    # 사전지식을 따로도 들고 있는다. 판정기는 규칙과 인물 설명을 보면 안 된다 —
    # 물음의 답이 있는지만 가려야 하는데 말투 규칙이 섞이면 연기를 시작한다.
    return {"system": out, "examples": examples, "knowledge": knowledge}

def load_sessions():
    """재시작해도 등록된 세션이 살아 있도록 디스크에서 복원한다."""
    if not os.path.isdir(SESS_DIR):
        return
    for sid in sorted(os.listdir(SESS_DIR)):
        v = _sess_path(sid, "voice.wav")
        if os.path.exists(v):
            SESS[sid] = {"voice": v, **_sess_build(sid)}
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

def sess_examples(sid):
    return need_session(sid).get("examples") or []

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
                 "temperature": TEMP, "top_k": TOPK, "top_p": TOPP,
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
    auth(x_token); HIST.pop(session, None); SUMM_TEXT.pop(session, None)
    KNOWN.pop(session, None); JUDGED.pop(session, None)
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

# ── 대화 기록 ───────────────────────────────────────────────────────
# 지시를 대화 뒤에 둔다. 앞에 두면 소제목이 대화의 일부로 읽힌다 — 실제로 "지키는 것"
# 이라는 소제목이 새어나가 요약이 상대를 "지키"라는 이름으로 부르기 시작했다.
# 지켜야 할 것을 열거하면 모델은 목록에 없는 것을 버린다. "이름·숫자·날짜·색깔"만
# 적었더니 3턴에 심은 "면접"이 요약에서 사라지고 "다음 주 화요일"만 남았다 — 날짜는
# 목록에 있고 사건은 없었다. 15턴 되묻기에서 요약에 면접이 있으면 7/9, 없으면 0/7 이라
# 상관이 거의 완벽하다. 모델이 보고도 안 쓴 게 아니라 볼 것이 없었다.
# 그 한 줄만으로는 안 됐다(3/8). 요약이 두 꼴로 갈리는데 한쪽이 첫 낱말만 베낀다 —
# "야 / 다음 주 화요일 / 떨린다". 평서문으로 적으라고 써 뒀는데 "야"는 평서문이 아니다.
# 이 프로젝트가 이미 아는 것을 그대로 쓴다 — 규칙만으로는 안 되고 본보기가 있어야
# 한다(docs/페르소나_작성규격.md). 본보기는 실제 대화와 안 겹치는 낱말로 쓴다.
# 겹치면 모델이 그것을 사실로 옮겨 적을 수 있다.
SUMM_PROMPT = """===== 대화 시작 =====
{log}
===== 대화 끝 =====

위 대화에서, 뒤에 이어 말할 때 필요한 사실만 세 줄로 적어라.
대화에 나온 이름과 숫자와 낱말은 바꾸지 말고 그대로 옮긴다.
한 줄에 한 가지씩, 누가 무엇을 했는지가 드러나는 평서문으로 적는다.
"야", "떨린다" 처럼 낱말만 떼어 놓지 마라.
대화에 없는 것은 적지 마라. 없는 것을 없다고 적지도 마라.
글자와 쉼표, 마침표만 쓴다."""

# 요약에 섞여 나오는 지시문 뼈대. 프롬프트를 고쳐도 완전히는 안 막힌다.
SUMM_JUNK = re.compile(r"^\s*(지키는 것|남길 것|간추린 내용|세 줄|요약|대화|사실)\s*:?\s*$", re.M)

# 요약이 "언급되지 않았다"로 한 줄을 쓰는 일이 있다. 지킬 것을 열거해 주면 모델은
# 그 칸을 반드시 채우려 들고, 채울 것이 없으면 지어내거나 없다고 적는다. 틀을 세 칸
# 보였더니 160턴 중 112턴에 이런 줄이 들어갔고 "상대의 가족 이름은 삼색이고 나이는
# 순하다" 같은 허구까지 나왔다. 열거는 없앴지만 이 꼴은 남을 수 있으니 걷어낸다 —
# 대화에 무엇이 없었다는 말은 뒤에 이어 말하는 데 아무 쓸모가 없다.
SUMM_EMPTY = re.compile(r"(언급되지 않|언급이 없|나오지 않|알 수 없|확인되지 않|명시되지 않)")

def clean_summary(s, tag="요약"):
    """요약은 시스템 프롬프트로 되돌아간다. 목록기호나 굵은 글씨가 섞이면 그 서식이
    답변에 옮아붙고 그대로 음성으로 읽힌다. 소제목이 남으면 이름으로 오해된다."""
    s = re.sub(r"[*#`_=]", "", s)
    s = re.sub(r"^\s*[-•·]\s*", "", s, flags=re.M)
    s = SUMM_JUNK.sub("", s)
    out, copied = [], 0
    for l in (x.strip() for x in s.splitlines()):
        if not l:
            continue
        if SUMM_EMPTY.search(l):
            copied += 1
            continue
        out.append(l)
    if copied:
        print(f"[{tag}] 없다고 적은 {copied}줄 걷어냄", flush=True)
    return "\n".join(out)[:SUMM_MAX]


# 사용자 발화에서 사실을 뽑는 지시. **갈래를 정해 주지 않는다** —
# "이름과 숫자와 날짜를 옮겨라" 처럼 열거하면 모델은 목록에 없는 것을 버리거나
# (면접을 지웠다) 목록에 있는데 대화엔 없는 것을 지어낸다(160턴 중 112턴).
# 꼴만 말하고 무엇을 뽑을지는 안 정한다. §「열거하지 말 것」.
#
# 마지막 줄이 이 프롬프트에서 제일 중요하다. 적립은 **매 턴 돌고 대부분의 턴에는
# 새 사실이 없다.** 빈손으로 돌아올 수 없으면 매 턴 지어낸다.
LEARN_PROMPT = """상대가 방금 이렇게 말했다.
"{heard}"

이 말에서, 뒤에 이어 말할 때 알고 있어야 할 사실만 적어라.
상대가 직접 말한 것만 적는다. 짐작해서 채우지 마라.
한 줄에 한 가지씩, 누가 무엇인지가 드러나는 평서문으로 적는다.
"야", "떨린다" 처럼 낱말만 떼어 놓지 마라.
적을 것이 없으면 아무것도 적지 마라. 없다고 적지도 마라.
글자와 쉼표, 마침표만 쓴다."""

# **"다"로 끝나는 줄만 받는다.** 마지막 줄("적을 것이 없으면 적지 마라")은 안 먹혔다 —
# 8개 발화로 재보니 5개에서 발화를 그대로 베껴 적었다. 그런데 **베낀 것과 뽑은 것은
# 끝맺음이 다르다.** 시키는 대로 평서문을 만들면 "-다."로 끝나고, 베끼면 원문의
# 구어체 끝이 그대로 남는다.
#
#   남길 것   내 동생 이름은 지훈이다. / 지훈은 나보다 세 살 아래이다. /
#             다음 주 화요일에 면접이 있다.
#   버릴 것   내 동생 이름 / 우리 같이 본 영화 뭐였지? / 그냥 요즘 좀 그래. /
#             별일은 없어. / 보고 싶었어 진짜로
#
# 9줄 중 8줄이 이 한 줄로 갈렸다. 남은 하나는 아래 LEARN_JUNK 가 잡는다.
LEARN_OK = re.compile(r"다\s*\.?$")

# 모델이 지시문을 그대로 베껴 적는다. "상대가 직접 말한 내용만 적는다." 가 적립된
# 적이 있다 — 이것도 "다."로 끝나서 위 규칙을 통과한다. 프롬프트의 줄과 닮으면 버린다.
LEARN_JUNK = [l.strip() for l in LEARN_PROMPT.splitlines()
              if l.strip() and "{heard}" not in l]


def learn(session, heard):
    """사용자 발화에서 사실을 뽑아 사전지식 옆에 쌓는다. LOCK 을 쥐고 불러야 한다.

    **모델 답변에서는 절대 뽑지 않는다.** 사전지식에 없는 것을 물으면 모델은
    36번 중 26번 없는 사실을 만든다(2026-08-27 실측). "민수? 응, 잘 지내고 있지."
    를 적립하면 지어낸 것이 사전지식으로 굳어 되돌릴 방법이 없다. 지어내기를
    고치려다 지어내기를 영구화하는 것이라, 사용자가 한 말만 받는다.

    같은 이유로 **앞 턴을 맥락으로 주지 않는다.** "지훈이야" 한 마디만으로는
    아무것도 안 쌓이지만, 앞 턴을 같이 주면 모델이 지어낸 물음("동생 이름이
    민수였지?")이 사실로 새어 든다. **못 배우는 쪽이 잘못 배우는 쪽보다 낫다.**
    """
    if not LEARN:
        return
    heard = (heard or "").strip()
    lines = KNOWN.setdefault(session, [])
    if len(heard) < LEARN_MIN or sum(len(l) for l in lines) >= LEARN_MAX:
        return
    try:
        t0 = time.time()
        out = clean_summary(S["pipe"].chat(
            [{"role": "user", "content": LEARN_PROMPT.format(heard=heard)}],
            max_new_tokens=120, temperature=0.3), tag="적립")
    except Exception as e:
        print(f"[{session}] 적립 실패, 넘어간다 — {e}", flush=True)
        return
    # 이미 아는 것은 안 쌓는다. 사전지식을 되받아 적는 일이 있고, 같은 말을 두 번
    # 하면 두 줄이 된다. 글자로만 거른다 — 뜻으로 거르려면 또 모델을 써야 한다.
    olds = [x.strip() for x in (sess_system(session) + "\n" + "\n".join(lines)).splitlines()
            if x.strip()]
    fresh = [l for l in (x.strip() for x in out.splitlines())
             if len(l) >= 6 and LEARN_OK.search(l)
             and not any(_sim(l, j) > 0.6 for j in LEARN_JUNK)
             and not any(_sim(l, p) > 0.7 for p in olds)]
    if fresh:
        lines += fresh
        print(f"[{session}] 적립 +{len(fresh)} ({time.time()-t0:.1f}초) — {fresh}", flush=True)


# ── 모르는 것을 묻는가 ──────────────────────────────────────────────
# 사전지식에 없는 것을 물으면 36번 중 27번 지어낸다. 프롬프트로 누르는 것은
# **세 번 실패했다** — 공통 규칙("확실한 것만 말하고"), 본보기 쌍, 지식 경계선.
# 셋 다 문항별로 보면 형제·생일·동생이름·작년여름이 6/6 그대로였다.
#
# 실패의 성질은 **전제 수용**이다. "내 동생 이름 기억나?" 에 동생이 있다고 치고
# 시작한다. 그래서 답을 뽑는 자리에서 누르지 않고, **뽑기 전에 따로 묻는다.**
# 연기 중에 상대 비위를 맞추는 것과 연기 밖에서 예/아니오를 가리는 것은 다른 일이다.
JUDGE = os.environ.get("RAON_JUDGE", "1") == "1"

# 판정을 걸어 볼 발화인가. **느슨해도 된다** — 여기서 통과해도 판정기가 "묻는 말이
# 아니면 있다"로 되돌린다. 이 자리는 호출값을 아끼는 것이지 판단하는 곳이 아니다.
ASKING = re.compile(r"\?|기억\s*(나|해|하)|알아|아니야|뭐(야|였|지|더라|랬)|"
                    r"무슨|어디|언제|누구|누가|왜|어떻게|몇|어느|더라")

JUDGE_PROMPT = """어떤 사람에 대해 알려진 것은 아래가 전부다.
===== 아는 것 =====
{known}
===== 끝 =====

상대가 이렇게 말했다.
"{heard}"

이 말에 답하는 데 필요한 것이 위에 있으면 "있다", 없으면 "없다" 라고만 적어라.
묻는 말이 아니면 "있다" 라고 적어라.
물음이 무언가를 전제하더라도 그 전제가 위에 없으면 "없다" 이다.
물음이 가리키는 때가 위에 적힌 때와 다르면 "없다" 이다.
다른 말은 적지 마라."""

# 모른다고 판정됐을 때 그 턴에만 붙인다.
#
# **시스템 프롬프트 끝에 두면 무시된다** — 판정은 8/8 로 맞는데 답은 그대로
# 지어냈다. 그 자리 뒤로 예시 10턴과 대화 이력이 통째로 오기 때문이다.
# §「글로 두면 설명으로 읽고, 턴으로 두면 제가 한 말로 읽는다」와 같은 일이다.
# 그래서 사용자 발화에 붙여 **맨 끝**, 답을 뽑기 바로 앞에 둔다.
JUDGE_NOTE = " (모르는 것이다. 모른다고 말하고 되물어라. 지어내지 마라.)"


def unknown(session, heard):
    """이 물음의 답이 사전지식에 없는가. LOCK 을 쥐고 불러야 한다.

    **답을 뽑기 전에 불러야 하므로 미룰 수 없다.** 적립과 달리 이번 턴에 쓰인다.
    그래서 물음일 때만 건다 — 서술문에까지 걸면 호출이 매 턴 하나씩 는다.

    판정기에는 사전지식과 적립만 보인다. 규칙과 인물 설명을 같이 주면 연기를
    시작해서 "있다"를 남발한다."""
    if not JUDGE or not heard or not ASKING.search(heard):
        return False
    # 판정기도 오늘을 알아야 한다. 사별 뒤의 일인지 가리는 자리가 여기다.
    known = "\n".join(x for x in [f"오늘은 {today()}이다.",
                                  need_session(session).get("knowledge", ""),
                                  "\n".join(KNOWN.get(session, []))] if x)
    if not known:
        return False
    try:
        out = S["pipe"].chat(
            [{"role": "user", "content": JUDGE_PROMPT.format(known=known, heard=heard)}],
            max_new_tokens=8, temperature=0.1)
    except Exception as e:
        print(f"[{session}] 판정 실패, 그냥 답한다 — {e}", flush=True)
        return False
    # "없다"가 들어 있을 때만 건다. 애매하면 안 거는 쪽이다 — 아는 것까지
    # 모른다고 하면 기억 62/64 가 무너진다.
    v = "없다" in out
    # 판정기가 무엇을 뱉었는지 남긴다. 안 걸렸을 때 "있다"라고 한 것인지 물음에
    # 답해 버린 것인지 구별이 안 되면 고칠 수가 없다.
    JUDGED[session] = out.strip()[:30]
    print(f"[{session}] 판정 {'모른다' if v else '안다'} — {heard!r} -> {out.strip()[:30]!r}",
          flush=True)
    return v


TASKS = set()                   # 배경 작업 참조. 안 잡아 두면 가비지 컬렉터가 가져간다


def learn_later(session, heard):
    """적립을 답변 뒤로 미룬다. **LOCK 을 놓은 뒤에** 불러야 한다.

    적립 결과는 이번 턴이 아니라 다음 턴에 필요하다. 그런데 그냥 두면 답변 경로
    한가운데서 모델을 한 번 더 쓰게 돼 첫 소리까지의 시간이 늘어난다 —
    실측 0.70초에서 1.14~1.70초로 두 배가 됐다. 3초 넘김 0/160 으로 만들어 둔
    여유를 갉아먹는다.

    체험자가 답을 듣고 생각하는 몇 초가 통째로 비어 있으므로 그때 돌린다.

    **LOCK 을 쥔 채로 부르면 안 된다.** asyncio.Lock 은 기다린 순서대로 깨우므로,
    합성이 LOCK 을 다시 잡기 전에 이 작업이 끼어들어 순서가 뒤집힌다. 그래서
    record() 안에서 부르지 않고 경로마다 제자리에 둔다."""
    async def go():
        try:
            async with LOCK:
                learn(session, heard)
        except Exception as e:
            print(f"[{session}] 적립 건너뜀 — {e}", flush=True)
    t = asyncio.ensure_future(go())
    TASKS.add(t)
    t.add_done_callback(TASKS.discard)

def today():
    """모델에게 알려줄 오늘. **프롬프트 어디에도 오늘이 없었다.**

    그래서 "작년", "지난달", "요즘" 같은 말을 셈하지 못하고 사전지식에서 아무거나
    갖다 붙였다 — "작년 여름에 우리 뭐 했지?" 에 스무 살 때 수박 얘기를 꺼낸다.
    모델이 제 감각으로 아는 지금은 학습이 끝난 때에 멈춰 있다(대통령을 물으면
    윤석열이라고 한다).

    한 줄 넣으니 사별 뒤의 일을 묻는 물음이 4/9 에서 8/9 로 잡혔다(3회차).
    "요즘 나 어떻게 지냈는지 알아?" 는 0/3 에서 3/3 이 됐다.

    **사별 시점은 같이 넣지 않는다.** "마지막으로 함께한 것은 약 3년 전이다"를
    넣으면 판정기가 그것을 "빈 구간이 있다"로 읽고 뭘 물어도 모른다 쪽으로 기운다.
    아는 것도 놓치고(12/12 -> 10/12) 정작 모르는 것도 덜 잡는다(8/9 -> 6/9).
    정보를 준 게 아니라 의심을 심는다.

    등록 때 한 번 박아 넣으면 다음 날 낡으므로 매 턴 만든다."""
    t = time.localtime()
    return f"{t.tm_year}년 {t.tm_mon}월 {t.tm_mday}일"


def build_msgs(session, user_content, note=""):
    """시스템(+요약) + 대화 예시 턴 + 최근 원문 + 이번 발화.

    요약을 시스템 프롬프트 안에 넣는 이유는, 대화가 길어져도 밀려나지 않게 하려는 것이다.
    별도 메시지로 앞에 두면 원문 턴들에 파묻혀 말투 규칙과 함께 무시된다.
    세 경로(/chat·/talk·/talk_stream)가 같은 것을 보도록 한 곳에 둔다.

    대화 예시는 시스템 프롬프트 안의 글이 아니라 요약 뒤·실제 대화 앞의 턴으로
    넣는다. 글로 두면 설명으로 읽고, 턴으로 두면 제가 한 말로 읽는다."""
    sysmsg = f"[오늘] {today()}\n\n" + sess_system(session)
    # 적립한 사실은 사전지식 바로 뒤에 둔다. 요약과 달리 접은 것이 아니라 더한 것이라
    # 원문 턴과 같이 있어도 겹치지 않는다.
    if KNOWN.get(session):
        sysmsg += "\n\n[대화 중에 알게 된 것]\n" + "\n".join(KNOWN[session])
    if SUMM_TEXT.get(session):
        sysmsg += f"\n\n[지금까지 나눈 이야기]\n{SUMM_TEXT[session]}"
    # note 는 이번 턴에만 붙고, **사용자 발화 뒤에** 붙는다. 시스템 프롬프트에 두면
    # 그 뒤로 예시와 이력이 다 오는 탓에 묻혀서 무시된다(실측). 소리 경로에서는
    # 발화가 오디오라 글 조각을 하나 덧붙인다 — 모델이 섞어 받는 꼴이다.
    if note:
        c = user_content["content"]
        user_content = {**user_content,
                        "content": (c + [{"type": "text", "text": note}]
                                    if isinstance(c, list) else f"{c}{note}")}
    return ([{"role": "system", "content": sysmsg}]
            + sess_examples(session)
            + list(HIST.get(session, []))
            + [user_content])

def _tail(text):
    ss = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").strip()) if s.strip()]
    return ss[-1] if ss else ""

def answer_for(session, msgs, tries=2):
    """답변을 뽑되, 앞선 답변과 마지막 문장이 겹치면 한 번 더 뽑는다.

    실측에서 16턴부터 20턴까지 "면접 끝나면 연락해."가 다섯 턴 연속 붙었다. 모델이
    히스토리에 있는 제 답변을 베끼는 것이라, 금지형("반복하지 마세요")으로도
    지시형("마지막 문장은 다르게 끝냅니다")으로도 막히지 않았다. 그래서 합성 검증과
    같은 방식으로, 뽑은 다음 재보고 걸리면 다시 뽑는다.

    "너는?" 같은 짧은 되물음은 반복이 아니라 자연스러운 대화라서 여덟 자를 넘을
    때만 본다. 다시 뽑을 때는 온도를 올려야 같은 것이 또 나오지 않는다."""
    prev = [_tail(m["content"]) for m in HIST.get(session, []) if m["role"] == "assistant"]
    a = ""
    for i in range(tries):
        a = S["pipe"].chat(msgs, max_new_tokens=TOKENS, temperature=CHAT_TEMP + 0.3 * i)
        t = _tail(a)
        if not REGEN or not prev or len(t) < 8:
            return a
        if max((_sim(t, p) for p in prev), default=0.0) < REGEN:
            return a
        print(f"[{session}] 꼬리 반복, 다시 뽑는다 — {t!r}", flush=True)
    return a

def record(session, heard, answer):
    """대화를 기록하고, 길어지면 앞쪽을 요약으로 접는다.

    호출자가 LOCK 을 쥐고 있어야 한다 — 접을 때 모델을 한 번 더 쓴다.
    6턴을 넘기면 오래된 것을 접어 3턴만 원문으로 남긴다. 세 턴에 한 번 꼴로 돈다."""
    h = HIST.setdefault(session, [])
    h += [{"role": "user", "content": heard},
          {"role": "assistant", "content": answer}]
    # 적립은 여기서 안 한다 — learn_later() 주석 참고. 경로마다 답을 내보낸 뒤에 부른다.
    if not SUMM or len(h) <= TURNS * 2:
        del h[:-TURNS*2]                 # 요약을 끄면 예전처럼 자르기만 한다
        return
    old, keep = h[:-KEEP*2], h[-KEEP*2:]
    log = "\n".join(f"{'상대' if m['role'] == 'user' else '나'}: {m['content']}" for m in old)
    try:
        t0 = time.time()
        new = clean_summary(S["pipe"].chat(
            [{"role": "user", "content": SUMM_PROMPT.format(log=log)}],
            max_new_tokens=200, temperature=0.3))
        # 새로 접은 것만 덧붙인다. 접은 것을 또 접으면 사실이 깎인다 — 3턴에 심은
        # "면접"이 세 번 다시 요약되며 사라져, 15턴에서 8회 중 7회를 틀렸다.
        merged = f"{SUMM_TEXT.get(session, '')}\n{new}".strip()
        if len(merged) > SUMM_MAX:
            merged = clean_summary(S["pipe"].chat(
                [{"role": "user", "content": SUMM_PROMPT.format(log=merged)}],
                max_new_tokens=300, temperature=0.3))
            print(f"[{session}] 요약이 길어져 다시 접음", flush=True)
        SUMM_TEXT[session], h[:] = merged, keep
        print(f"[{session}] 요약 {len(old)}개 접음 ({time.time()-t0:.1f}초) — {new!r}", flush=True)
    except Exception as e:
        # 요약이 실패해도 대화는 이어져야 한다. 예전처럼 자르기로 물러선다.
        print(f"[{session}] 요약 실패, 자르기로 대체: {e}", flush=True)
        del h[:-TURNS*2]


@app.post("/chat")
async def chat_ep(text: str = Form(...), session: str = Form("default"),
                  x_token: str = Header("")):
    """텍스트로 묻고 텍스트로 답한다. 페르소나 프롬프트를 재보기 위한 것이다.

    /talk 은 오디오만 받는다. 그래서 프롬프트 한 줄을 고칠 때마다 인식과 합성까지 도는데,
    둘 다 답변 글의 품질과 상관이 없으면서 턴당 몇 초를 더 쓰고 오차를 섞는다. 실제로
    질문을 합성했더니 "회사에서 계속 깨지는 것 같아"가 "어."로 나온 적이 있다.

    시스템 프롬프트와 히스토리는 /talk 과 같은 것을 쓴다. 여기서 좋아진 프롬프트는
    /talk 에서도 그대로 좋아진다. 유니티는 이 경로를 쓰지 않는다."""
    auth(x_token)
    if not S["pipe"]:
        raise HTTPException(503, "model not ready")
    need_session(session)
    async with LOCK:
        t0 = time.time()
        note = JUDGE_NOTE if unknown(session, text) else ""
        msgs = build_msgs(session, {"role": "user", "content": text}, note)
        answer = answer_for(session, msgs)
        record(session, text, answer)
        # 여기만 동기로 적립한다. 계측기가 쓰는 길이라 이번 응답에 결과가 실려야
        # 하고, 글 경로에는 합성이 없어 첫 소리 지연에 영향을 주지 않는다.
        learn(session, text)
        el = time.time() - t0
    print(f"[{session}] (글) {text!r} -> {answer!r} ({el:.1f}초)", flush=True)
    # 요약과 남은 턴 수를 같이 돌려준다. 로그를 못 보는 자리에서도 접기가 제대로
    # 도는지 확인할 수 있어야 한다.
    return {"answer": answer, "elapsed": round(el, 2),
            "turns": len(HIST.get(session, [])) // 2,
            "summary": SUMM_TEXT.get(session, ""),
            "learned": KNOWN.get(session, []),
            "judged": JUDGED.get(session, "")}


async def _record_history(session: str, path: str, answer: str):
    """응답을 보낸 뒤 사용자 발화를 받아적어 히스토리를 채운다."""
    try:
        async with LOCK:
            heard = S["pipe"].stt(path)
            record(session, heard, answer)
            learn(session, heard)       # 이미 응답을 보낸 뒤라 미룰 것이 없다
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

    # **자막을 보여줄지와 받아적기를 언제 할지는 별개다.** 판정기가 켜져 있으면
    # 답을 만들기 전에 글이 있어야 하므로, 자막을 안 보내더라도 먼저 받아적는다.
    # 묶어 두면 자막 스위치 하나가 지어내기 27/36 을 되살린다 — 운영자 화면에서
    # 끌 수 있는 스위치라 더 위험하다.
    early = want_heard or JUDGE
    deferred = False
    try:
        async with LOCK:
            pipe, t0 = S["pipe"], time.time()
            heard = pipe.stt(p) if early else ""
            t1 = time.time()
            note = JUDGE_NOTE if unknown(session, heard) else ""
            msgs = build_msgs(session, {"role": "user",
                                        "content": [{"type": "audio", "audio": p}]}, note)
            answer = answer_for(session, msgs)
            t2 = time.time()
            data, _ = synth(answer, sess_voice(session))
            t3 = time.time()
            if early:
                record(session, heard, answer)
            print(f"[{session}] {heard!r} -> {answer!r} "
                  f"(인식{t1-t0:.1f} 생성{t2-t1:.1f} 합성{t3-t2:.1f} 총{t3-t0:.1f}초)", flush=True)

        headers = {"X-Answer": quote(answer), "X-Elapsed": f"{t3-t0:.2f}"}
        if want_heard:
            headers["X-Heard"] = quote(heard)   # 받아적어도 안 보낼 수 있다
        if early:
            learn_later(session, heard)     # LOCK 을 놓은 뒤라야 한다
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

    # 자막을 안 보내더라도 판정기가 켜져 있으면 먼저 받아적는다 — /talk 주석 참고.
    early = want_heard or JUDGE

    # 답변 텍스트는 헤더로 먼저 나가야 하므로 여기서 확정한다
    async with LOCK:
        pipe, t0 = S["pipe"], time.time()
        heard = pipe.stt(p) if early else ""
        note = JUDGE_NOTE if unknown(session, heard) else ""
        msgs = build_msgs(session, {"role": "user",
                                    "content": [{"type": "audio", "audio": p}]}, note)
        answer = answer_for(session, msgs)
        if early:
            record(session, heard, answer)
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
            if early:
                learn_later(session, heard)  # 체험자가 답을 듣는 동안 돈다
        finally:
            if early:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    bg = None if early else BackgroundTask(_record_history, session, p, answer)
    return StreamingResponse(body(), media_type="application/octet-stream", background=bg, headers={
        "X-Heard": quote(heard if want_heard else ""),   # 받아적어도 안 보낼 수 있다
        "X-Answer": quote(answer),
        "X-Sample-Rate": "24000", "X-Channels": "1",
    })


# ── 세션 등록 (웹 백엔드 → 서버) ────────────────────────────────────

@app.post("/session/start")
async def session_start(persona: str = Form(...), knowledge: str = Form(""),
                        rules: str = Form(""), session: str = Form(""),
                        voice: UploadFile = File(...),
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
    # 같은 세션 ID 로 다시 등록하면서 규칙을 안 보내면 기본값으로 돌아가야 한다.
    # 지우지 않으면 예전에 보낸 규칙이 남아 무엇이 쓰이는지 알 수 없게 된다.
    rp = _sess_path(sid, "rules.md")
    if rules.strip():
        _save_atomic(rules.strip().encode("utf-8"), rp)
    elif os.path.exists(rp):
        os.remove(rp)
    has_model = model is not None and model.filename
    if has_model:
        _save_atomic(await model.read(), _sess_path(sid, "model.glb"))

    SESS[sid] = {"voice": _sess_path(sid, "voice.wav"), **_sess_build(sid)}
    CURRENT["session"] = sid
    HIST.pop(sid, None)          # 인물이 바뀌었으므로 이전 대화는 버린다
    SUMM_TEXT.pop(sid, None); KNOWN.pop(sid, None)
    REF_TEXT.pop(SESS[sid]["voice"], None)   # 같은 경로에 다른 음성이 덮였다

    # 서버는 한 번에 한 인물만 보관한다. 세션 ID 를 비우면 시각으로 자동 생성되므로
    # 지우지 않으면 등록할 때마다 쌓인다. 원본은 웹에 있으니 언제든 다시 만들 수 있고,
    # 실존 인물의 음성을 서버에 오래 두지 않는 편이 낫다.
    for old in [s for s in SESS if s != sid]:
        SESS.pop(old, None)
        HIST.pop(old, None)
        SUMM_TEXT.pop(old, None); KNOWN.pop(old, None)
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
    SUMM_TEXT.pop(session, None); KNOWN.pop(session, None)
    if CURRENT["session"] == session:
        CURRENT["session"] = None
    d = _sess_path(session)
    if os.path.isdir(d):
        shutil.rmtree(d, ignore_errors=True)
    print(f"[세션] {session} 종료·삭제", flush=True)
    return {"ok": True}
