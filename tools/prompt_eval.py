"""페르소나 프롬프트 시험 도구.

한 세션에서 20턴을 끊지 않고 이어가며 답변 글을 기계적으로 채점한다. 턴이 쌓일수록
규칙이 언제 풀리는지, 앞에서 한 말을 어디서 잊는지, 같은 질문을 언제 다시 하는지를 본다.
소리(지지직·억양·속도)는 채점하지 않는다 — 그건 귀로 들어야 한다.

    python tools/prompt_eval.py --list
    python tools/prompt_eval.py baseline
    python tools/prompt_eval.py baseline v2 -n 3      # 두 변형을 3회씩

프롬프트 변형은 tools/prompts/<이름>.persona.md 와 <이름>.knowledge.md 두 파일이다.
결과는 tools/_work/ 에 JSON 으로 남는다(저장소에 안 올라간다).

주의: 실행하면 서버의 등록 인물이 'eval' 로 교체된다. 서버는 한 번에 한 인물만 둔다.
"""
import argparse, difflib, json, os, re, statistics, sys, time
from pathlib import Path
import requests

ROOT   = Path(__file__).resolve().parent.parent
PROMPT = ROOT / "tools" / "prompts"
WORK   = ROOT / "tools" / "_work"
SERVER = os.environ.get("RAON_URL", "http://220.69.208.201:8000")
SID    = "eval"
# 참조 음성은 실존 인물의 목소리라 저장소에 두지 않는다. 경로만 환경변수로 받는다.
VOICE  = os.environ.get("RAON_EVAL_VOICE", "")
HDR    = {}

def token():
    """start.sh 에 이미 있는 토큰을 재사용한다. 새로 적어 두면 관리할 곳이 하나 더 는다."""
    if os.environ.get("RAON_TOKEN"):
        return os.environ["RAON_TOKEN"]
    m = re.search(r"RAON_TOKEN=(\S+)", (ROOT / "Server" / "start.sh").read_text(encoding="utf-8"))
    return m.group(1) if m else ""

# ── 대본 ────────────────────────────────────────────────────────────
# 한 세션을 끊지 않고 20턴 간다. 앞에서 심은 사실을 뒤에서 되묻는 것이 핵심이다.
# (사용자 말, 답에 있어야 할 것)  — 없으면 None
SCRIPT = [
    ("야 오랜만이다",                              None),
    ("나야 뭐 그냥 그렇지. 너는?",                  None),
    ("나 다음 주 화요일에 면접 봐",                 None),          # 사실 A 심기
    ("좀 떨린다 솔직히",                            None),
    ("준비는 그럭저럭 했는데 잘 될지 모르겠어",      None),
    ("요즘 회사에서 계속 깨져서 자신감이 없어",      None),
    ("그만둘까 고민도 했었고",                      None),
    ("아 맞다 나 고양이 키우기 시작했어. 이름은 나비", None),        # 사실 B 심기
    ("응 삼색이야. 되게 순해",                      None),
    ("근데 밤마다 울어서 잠을 못 자",                None),
    ("너는 요즘 뭐 하고 지내?",                     None),
    ("주말에는 뭐 해?",                             None),
    ("우리 예전에 강릉 갔던 거 기억나?",            ["강릉", "터미널", "첫차"]),
    ("그때 터미널에서 얼마나 기다렸더라",            ["세 시간", "세시간", "3시간"]),
    ("내가 다음 주에 뭐 있다고 했지?",              ["면접"]),       # 사실 A 되묻기
    ("무슨 요일이라고 했지?",                       ["화"]),         # 사실 A 되묻기
    ("우리 고양이 이름 기억나?",                    ["나비"]),       # 사실 B 되묻기
    ("무슨 색이라고 했더라",                        ["삼색"]),       # 사실 B 되묻기
    ("내 이름 뭐야?",                               ["민수"]),       # 사전지식
    ("너 이름은?",                                  ["준호"]),       # 사전지식
]

# ── 채점 ────────────────────────────────────────────────────────────
POLITE = re.compile(r"(습니다|입니다|세요|셔요|해요|예요|이에요|네요|는데요|거든요|"
                    r"더라고요|잖아요|군요|나요|가요|시죠|죠)(?=[\s.,!?)\"']|$)")
AGENT  = re.compile(r"도와드릴|말씀해|무엇을 도와|안녕하세요|죄송합니다|도움이 되|필요하시")
AI     = re.compile(r"\bAI\b|인공지능|언어\s*모델|어시스턴트|챗봇")
# 음성으로 읽히므로 이모지·목록기호·굵은글씨는 그대로 사고가 된다.
JUNK   = re.compile(r"[^가-힣ㄱ-ㆎa-zA-Z0-9\s.,?!~'\"·…\-()]")

def norm(s):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", s or "")

def sim(a, b):
    a, b = norm(a), norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0

def questions(text):
    """답변 안의 물음 문장들. 같은 걸 또 묻는지 보려면 물음만 따로 봐야 한다."""
    return [s.strip() for s in re.split(r"(?<=[?])\s*", text) if s.strip().endswith("?")]

def examples(persona):
    """페르소나의 대화 예시 줄. 이걸 그대로 베끼는지 본다."""
    return [l.split(":", 1)[1].strip() for l in persona.splitlines()
            if re.match(r"^\s*(친구|나|상대)\s*:", l)]

def score(answer, prev_answers, prev_questions, exs, want):
    qs = questions(answer)
    return {
        "글자수":   len(answer),
        "문장수":   len([s for s in re.split(r"[.!?\n]", answer) if s.strip()]),
        "존댓말":   len(POLITE.findall(answer)),
        "상담원":   len(AGENT.findall(answer)),
        "정체노출": len(AI.findall(answer)),
        "기호":     len(JUNK.findall(answer)),
        "호칭오류": answer.count("준호야"),   # 준호는 AI 쪽 이름. 사용자를 이렇게 부르면 안 된다
        "예시베낌": round(max([sim(answer, e) for e in exs] or [0]), 2),
        "답변반복": round(max([sim(answer, a) for a in prev_answers] or [0]), 2),
        "질문반복": round(max([sim(q, p) for q in qs for p in prev_questions] or [0]), 2),
        "되묻기":   int(bool(qs)),
        # want 가 있는 턴만 기억을 채점한다. 없으면 None 이라 평균에서 빠진다.
        "기억":     None if not want else int(any(w in answer for w in want)),
    }

VIOLATIONS = ["존댓말", "상담원", "정체노출", "기호", "호칭오류"]

# ── 서버 ────────────────────────────────────────────────────────────
def post(path, data=None, files=None, timeout=300):
    r = requests.post(SERVER + path, data=data, files=files, headers=HDR, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"{path} {r.status_code}: {r.text[:300]}")
    return r

def register(persona, knowledge):
    with open(VOICE, "rb") as f:
        out = post("/session/start",
                   data={"persona": persona, "knowledge": knowledge, "session": SID},
                   files={"voice": ("voice.wav", f, "audio/wav")}).json()
    if out.get("warning"):
        print(f"  [경고] {out['warning']}")

def run_once(name, persona, knowledge, rep):
    exs = examples(persona)
    post("/reset", data={"session": SID})
    rows, prev_a, prev_q, summary = [], [], [], ""
    for i, (say, want) in enumerate(SCRIPT, 1):
        r = post("/chat", data={"text": say, "session": SID}).json()
        ans = r["answer"]
        row = {"변형": name, "회차": rep, "턴": i, "질문": say, "답변": ans,
               "초": r["elapsed"], "남은턴": r["turns"]}
        row.update(score(ans, prev_a, prev_q, exs, want))
        rows.append(row)
        mark = "" if row["기억"] is None else ("  기억 O" if row["기억"] else "  기억 X")
        flags = "".join(f" [{k}]" for k in VIOLATIONS if row[k])
        print(f"  {i:2d}. {ans}{flags}{mark}")
        if r["summary"] != summary:
            summary = r["summary"]
            print(f"      ── 요약 갱신 ──\n      " + summary.replace("\n", "\n      "))
        prev_a.append(ans)
        prev_q += questions(ans)
    return rows

def run_variant(name, reps):
    persona   = (PROMPT / f"{name}.persona.md").read_text(encoding="utf-8")
    knowledge = (PROMPT / f"{name}.knowledge.md").read_text(encoding="utf-8")
    print(f"\n{'='*70}\n{name} — 페르소나 {len(persona)}자 / 사전지식 {len(knowledge)}자\n{'='*70}")
    register(persona, knowledge)
    rows = []
    for rep in range(1, reps + 1):
        print(f"\n-- {rep}회차 --")
        rows += run_once(name, persona, knowledge, rep)
    return rows

# ── 정리 ────────────────────────────────────────────────────────────
def summarize(rows):
    out = {}
    for name in dict.fromkeys(r["변형"] for r in rows):
        rs = [r for r in rows if r["변형"] == name]
        mem = [r for r in rs if r["기억"] is not None]
        d = {"턴": len(rs)}
        for k in ["글자수", "문장수", "예시베낌", "답변반복", "질문반복", "되묻기"]:
            d[k] = round(statistics.mean(r[k] for r in rs), 2)
        for k in VIOLATIONS:
            d[k] = sum(1 for r in rs if r[k])
        d["기억"] = f"{sum(r['기억'] for r in mem)}/{len(mem)}"
        # 뒤로 갈수록 규칙이 풀리는지 본다. 앞 절반과 뒤 절반을 가른다.
        half = len(SCRIPT) // 2
        for lbl, sel in [("앞10턴", lambda r: r["턴"] <= half), ("뒤10턴", lambda r: r["턴"] > half)]:
            part = [r for r in rs if sel(r)]
            d[lbl + "위반"] = sum(1 for r in part for k in VIOLATIONS if r[k])
            d[lbl + "글자"] = round(statistics.mean(r["글자수"] for r in part), 1)
        out[name] = d
    return out

def table(summary):
    cols = ["턴", "글자수", "문장수", "되묻기", "기억", "예시베낌", "답변반복", "질문반복"] \
           + VIOLATIONS + ["앞10턴위반", "뒤10턴위반", "앞10턴글자", "뒤10턴글자"]
    w = max(max(len(n) for n in summary) + 2, 10)
    print("\n" + "항목".ljust(12) + "".join(n.rjust(w) for n in summary))
    for c in cols:
        print(c.ljust(12) + "".join(str(summary[n][c]).rjust(w) for n in summary))
    print("\n위반 열은 '그런 턴이 몇 개인가'다. 0 이 목표.")

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
