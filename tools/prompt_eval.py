"""페르소나 프롬프트 A/B 도구.

서버에 프롬프트를 갈아 끼우고 정해진 대화를 걸어, **답변 텍스트만** 기계적으로 채점한다.
소리(지지직·억양·속도)는 채점하지 않는다 — 그건 귀로 들어야 한다.

    python tools/prompt_eval.py --list
    python tools/prompt_eval.py baseline            # 1회
    python tools/prompt_eval.py baseline v2 -n 3    # 두 변형을 3회씩 비교

프롬프트 변형은 tools/prompts/<이름>.persona.md 와 <이름>.knowledge.md 두 파일이다.
결과는 tools/_work/ 에 JSON 으로 남는다(저장소에 안 올라간다).

주의: 실행하면 서버의 등록 인물이 'eval' 로 교체된다. 서버는 한 번에 한 인물만 둔다.
"""
import argparse, difflib, hashlib, json, os, re, statistics, sys, time
from pathlib import Path
import requests

ROOT   = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "tools" / "prompts"
WORK   = ROOT / "tools" / "_work"
SERVER = os.environ.get("RAON_URL", "http://220.69.208.201:8000")
SID    = "eval"

# 참조 음성은 실존 인물의 목소리라 저장소에 두지 않는다. 경로만 환경변수로 받는다.
VOICE  = os.environ.get("RAON_EVAL_VOICE", "")

def token():
    """start.sh 에 이미 있는 토큰을 재사용한다. 새로 적어 두면 관리할 곳이 하나 더 는다."""
    if os.environ.get("RAON_TOKEN"):
        return os.environ["RAON_TOKEN"]
    m = re.search(r"RAON_TOKEN=(\S+)", (ROOT / "Server" / "start.sh").read_text(encoding="utf-8"))
    return m.group(1) if m else ""

HDR = {}

# ── 시험 대화 ────────────────────────────────────────────────────────
# 각 대화는 3턴이고, 페르소나가 약속한 것을 하나씩 겨냥한다.
CONVS = {
    "인사": ["야 오랜만이다 잘 지냈어?",
             "나야 뭐 그냥 그렇지. 너는?",
             "요즘 뭐 하고 지내?"],
    "위로": ["요즘 좀 힘들다",
             "회사에서 계속 깨지는 것 같아",
             "그만둘까 고민 중이야"],
    "조언": ["나 이직해야 할까?",
             "연봉은 비슷한데 일이 더 재밌어 보여",
             "네가 나라면 어떻게 할 거야?"],
    "기억": ["우리 예전에 강릉 갔던 거 기억나?",
             "그때 터미널에서 얼마나 기다렸더라",
             "학교 앞 분식집도 자주 갔었잖아"],
    "정체": ["너 혹시 AI야?",
             "진짜로 물어보는 거야",
             "내 이름 뭔지 알아?"],
}

# ── 채점 ────────────────────────────────────────────────────────────
POLITE = re.compile(r"(습니다|입니다|세요|셔요|해요|예요|이에요|네요|는데요|거든요|"
                    r"더라고요|잖아요|군요|나요|가요|시죠|죠)(?=[\s.,!?)\"']|$)")
AGENT  = re.compile(r"도와드릴|말씀해|무엇을 도와|안녕하세요|죄송합니다|도움이 되")
AI     = re.compile(r"\bAI\b|인공지능|언어\s*모델|어시스턴트|챗봇")
# 음성으로 읽히므로 이모지·목록기호·굵은글씨는 그대로 사고가 된다.
JUNK   = re.compile(r"[^가-힣ㄱ-ㆎa-zA-Z0-9\s.,?!~'\"·…\-()]")

def examples(persona):
    """페르소나의 대화 예시 중 '친구:' 줄. 이걸 그대로 베끼는지 본다."""
    return [l.split(":", 1)[1].strip() for l in persona.splitlines()
            if l.startswith("친구:")]

def sim(a, b):
    n = lambda s: re.sub(r"[^가-힣a-zA-Z0-9]", "", s or "")
    a, b = n(a), n(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0

def score(answer, prev, exs):
    return {
        "글자수":   len(answer),
        "문장수":   len([s for s in re.split(r"[.!?\n]", answer) if s.strip()]),
        "존댓말":   len(POLITE.findall(answer)),
        "상담원":   len(AGENT.findall(answer)),
        "정체노출": len(AI.findall(answer)),
        "기호":     len(JUNK.findall(answer)),
        "호칭오류": answer.count("준호"),          # 준호는 AI 쪽 이름. 사용자를 이렇게 부르면 안 된다
        "예시베낌": round(max([sim(answer, e) for e in exs] or [0]), 2),
        "직전반복": round(sim(answer, prev), 2),
        "되묻기":   int(answer.strip().endswith("?")),
    }

# 낮을수록 좋은 것들. 0 이 아니면 위반으로 센다.
VIOLATIONS = ["존댓말", "상담원", "정체노출", "기호", "호칭오류"]

# ── 서버 ────────────────────────────────────────────────────────────
def post(path, data=None, files=None, timeout=180):
    r = requests.post(SERVER + path, data=data, files=files, headers=HDR, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"{path} {r.status_code}: {r.text[:200]}")
    return r

def register(persona, knowledge):
    with open(VOICE, "rb") as f:
        out = post("/session/start",
                   data={"persona": persona, "knowledge": knowledge, "session": SID},
                   files={"voice": ("voice.wav", f, "audio/wav")}).json()
    if out.get("warning"):
        print(f"  [경고] {out['warning']}")
    return out

def question_wav(text):
    """질문을 음성으로 만들어 캐시한다. STT 로 되읽어 원문과 다르면 시험이 오염되므로 막는다."""
    WORK.mkdir(exist_ok=True)
    cache = WORK / "질문"
    cache.mkdir(exist_ok=True)
    p = cache / (hashlib.md5(text.encode()).hexdigest()[:10] + ".wav")
    if not p.exists():
        p.write_bytes(post("/tts", data={"text": text, "session": SID}).content)
        with open(p, "rb") as f:
            heard = post("/stt", files={"file": ("q.wav", f, "audio/wav")}).json()["text"]
        s = sim(heard, text)
        print(f"  질문합성 {s:.2f} {text!r}" + ("" if s >= 0.8 else f"  ← 되읽기 {heard!r}"))
        if s < 0.8:
            p.unlink()
            raise RuntimeError(f"질문 음성이 원문과 다릅니다({s:.2f}). 문장을 바꾸세요: {text!r}")
    return p

def turn(wav):
    with open(wav, "rb") as f:
        r = post("/talk", data={"session": SID, "show_heard": "1"},
                 files={"file": ("in.wav", f, "audio/wav")})
    from urllib.parse import unquote
    return unquote(r.headers.get("X-Answer", "")), float(r.headers.get("X-Elapsed", 0))

# ── 실행 ────────────────────────────────────────────────────────────
def run_variant(name, reps):
    persona   = (PROMPT / f"{name}.persona.md").read_text(encoding="utf-8")
    knowledge = (PROMPT / f"{name}.knowledge.md").read_text(encoding="utf-8")
    exs = examples(persona)
    print(f"\n=== {name} — 페르소나 {len(persona)}자 / 사전지식 {len(knowledge)}자 ===")
    register(persona, knowledge)

    rows = []
    for rep in range(reps):
        for conv, qs in CONVS.items():
            post("/reset", data={"session": SID})
            prev = ""
            for i, q in enumerate(qs):
                answer, el = turn(question_wav(q))
                row = {"변형": name, "회차": rep + 1, "대화": conv, "턴": i + 1,
                       "질문": q, "답변": answer, "초": round(el, 1)}
                row.update(score(answer, prev, exs))
                rows.append(row)
                prev = answer
                print(f"  [{rep+1}/{conv}/{i+1}] {el:4.1f}초 {answer}")
    return rows

def summarize(rows):
    out = {}
    for name in dict.fromkeys(r["변형"] for r in rows):
        rs = [r for r in rows if r["변형"] == name]
        d = {"턴": len(rs)}
        for k in ["글자수", "문장수", "예시베낌", "직전반복", "되묻기"]:
            d[k] = round(statistics.mean(r[k] for r in rs), 2)
        for k in VIOLATIONS:
            d[k] = sum(1 for r in rs if r[k])
        d["첫턴글자"] = round(statistics.mean(r["글자수"] for r in rs if r["턴"] == 1), 1)
        out[name] = d
    return out

def table(summary):
    cols = ["턴", "글자수", "첫턴글자", "문장수", "되묻기", "예시베낌", "직전반복"] + VIOLATIONS
    w = max(len(n) for n in summary) + 2
    print("\n" + "항목".ljust(10) + "".join(n.rjust(w) for n in summary))
    for c in cols:
        print(c.ljust(10) + "".join(str(summary[n][c]).rjust(w) for n in summary))
    print("\n위반 열(존댓말·상담원·정체노출·기호·호칭오류)은 '그런 턴이 몇 개인가'다. 0 이 목표.")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("variants", nargs="*")
    ap.add_argument("-n", "--reps", type=int, default=1)
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args()

    if a.list or not a.variants:
        for p in sorted(PROMPT.glob("*.persona.md")):
            print(p.name.replace(".persona.md", ""))
        return
    if not VOICE or not os.path.exists(VOICE):
        sys.exit("참조 음성이 없습니다. RAON_EVAL_VOICE 에 wav 경로를 넣으세요.")

    HDR["X-Token"] = token()
    rows = []
    for v in a.variants:
        rows += run_variant(v, a.reps)

    WORK.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    (WORK / f"{stamp}.json").write_text(
        json.dumps({"요약": summarize(rows), "턴": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    table(summarize(rows))
    print(f"\n기록: tools/_work/{stamp}.json")

if __name__ == "__main__":
    main()
