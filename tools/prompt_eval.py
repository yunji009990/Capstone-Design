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

# ── 지어내기 시험 ───────────────────────────────────────────────────
# 사전지식에 없는 것을 물으면 매번 다르게 지어낸다(2026-08-26). 유가족 앞에서
# 없던 형제를 만들어내는 것이라 격려·길이와 무게가 다른데, 재는 자리가 없었다.
#
# 두 가지를 같이 잰다. 없는 것에 얼버무리는가, 그리고 있는 것에 괜히 얼버무리지는
# 않는가. 앞만 재면 "다 모른다고 답하기"로 만점이 나온다.
#
# 흔들림도 본다. 같은 것을 여러 번 물어 답이 갈리면 지어내는 것이다 —
# "너 형제 있었나?"가 없어/있어/친구로 갈린 것이 문제를 드러낸 신호였다.
#
# 회차마다 /reset 하고 묻는다. 대화 맥락을 없애 사전지식만 놓고 보려는 것이다.
# 실제 체험에서는 대화 도중에 나올 물음이라 이대로가 전부는 아니다.
#
# (질문, 사전지식에 답이 있는가)
PROBE = [
    ("우리 예전에 강릉 갔던 거 기억나?", True),
    ("우리 어떻게 처음 만났더라?", True),
    ("내 이름 뭐야?", True),
    ("너 이름은?", True),
    ("너 형제 있었나?", False),
    ("내 생일 언제인지 알아?", False),
    ("내 동생 이름 기억나?", False),
    ("우리 같이 본 영화 뭐였지?", False),
    ("작년 여름에 우리 뭐 했지?", False),
    ("내가 어느 회사 다니는지 알아?", False),
]

# 모른다고 인정하거나 상대에게 되넘기는 말.
HEDGE = re.compile(r"기억\s*(이|은)?\s*(잘\s*)?안\s*나|기억이 가물|생각이\s*안\s*나|"
                   r"잘 모르|모르겠|가물가물|글쎄|헷갈|확실하지 않|네가 (얘기|말해)|"
                   # "아직 말 안 했잖아" 도 옳은 답이다 — 없는 것을 만들지 않고 되넘긴다.
                   r"말 안 했|얘기 안 했|못 들었|알려준 적 없|안 알려줬|처음 듣|"
                   # 상대에게 넘기는 말. 사실을 만들지 않으므로 얼버무림으로 센다.
                   r"(말|얘기)해 ?(봐|줘)|알려 ?(줘|주라)")


# "작년 가을이었지?" 는 물음표가 붙었지만 묻는 것이 아니라 확인이다. 얼버무린 뒤에
# 이렇게 붙이면 유가족에게 남는 것은 뒤쪽 사실이라 단정으로 센다.
TAG = re.compile(r"(지|잖아|었지|았지|맞지)\s*\?$")


def _asserts(s):
    """이 문장이 무언가를 단정하는가. 되묻기와 얼버무림은 아니다."""
    if len(s) <= 2 or HEDGE.search(s):
        return False
    return TAG.search(s) is not None or not s.endswith("?")


def hedged(ans):
    """얼버무렸는가. 얼버무린 뒤에 단정하면 얼버무린 것이 아니다.

    실측에서 "기억 안 나. 작년 가을이었잖아." 가 나왔다. 한 문장 안에서 모르겠다고
    해놓고 없는 사실을 말한다. 낱말만 찾으면 이것이 만점으로 잡히는데, 유가족에게
    남는 것은 뒤쪽 단정이다.

    되묻기로 넘기는 것("너는 뭐 보고 싶어?")은 얼버무림이 맞다 — 사실을 안 만든다.
    """
    ss = sentences(ans)
    for i, x in enumerate(ss):
        if HEDGE.search(x):
            # 뒤에 물음이 아닌 문장이 오면 거기서 무언가를 단정한 것이다.
            return not any(_asserts(y) for y in ss[i + 1:])
    return False


def probe_run(name, reps):
    rows = []
    for rep in range(1, reps + 1):
        print(f"\n-- {rep}회차 --")
        for q, known in PROBE:
            post("/reset", data={"session": SID})
            ans = post("/chat", data={"text": q, "session": SID}).json()["answer"]
            h = hedged(ans)
            rows.append({"변형": name, "회차": rep, "질문": q, "사전지식": known,
                         "답변": ans, "얼버무림": int(h)})
            bad = ("  <- 지어냄" if not h and not known else
                   "  <- 아는 걸 얼버무림" if h and known else "")
            print(f"  [{'있음' if known else '없음'}] {q}")
            print(f"      {ans}   ({'얼버무림' if h else '단정'}){bad}")
    return rows


def probe_report(rows):
    names = list(dict.fromkeys(r["변형"] for r in rows))
    print("\n" + "항목".ljust(16) + "".join(n.rjust(12) for n in names))

    def line(lbl, f):
        print(lbl.ljust(16) + "".join(
            str(f([r for r in rows if r["변형"] == n])).rjust(12) for n in names))

    line("지어내기", lambda rs: "%d/%d" % (
        sum(1 for r in rs if not r["사전지식"] and not r["얼버무림"]),
        sum(1 for r in rs if not r["사전지식"])))
    line("아는 걸 얼버무림", lambda rs: "%d/%d" % (
        sum(1 for r in rs if r["사전지식"] and r["얼버무림"]),
        sum(1 for r in rs if r["사전지식"])))

    def wobble(rs):
        v = []
        for q, _ in PROBE:
            a = [r["답변"] for r in rs if r["질문"] == q]
            if len(a) > 1:
                v.append(statistics.mean(sim(x, y)
                                         for i, x in enumerate(a) for y in a[i + 1:]))
        return round(statistics.mean(v), 2) if v else "-"

    line("답 일치도", wobble)
    print("\n지어내기 0 이 목표. 아는 걸 얼버무림도 0 이어야 한다 — 하나만 보면 속는다.")
    print("답 일치도는 같은 질문에 같은 답을 하는가다. 낮으면 즉석에서 만드는 것이다.")


# ── 채점 ────────────────────────────────────────────────────────────
POLITE = re.compile(r"(습니다|입니다|세요|셔요|해요|예요|이에요|네요|는데요|거든요|"
                    r"더라고요|잖아요|군요|나요|가요|시죠|죠)(?=[\s.,!?)\"']|$)")
AGENT  = re.compile(r"도와드릴|말씀해|무엇을 도와|안녕하세요|죄송합니다|도움이 되|필요하시")
AI     = re.compile(r"\bAI\b|인공지능|언어\s*모델|어시스턴트|챗봇")
# 상대를 평가하고 격려하는 상담사 말투. 친구는 이렇게 말하지 않는다.
CHEER  = re.compile(r"잘할 (거|수)|잘 할 (거|수)|충분히|넌 항상|너 원래|원래 잘|믿어|"
                    r"힘내|응원할게|괜찮아질|넌 충분|성장하고|자신감을 가지|넌 잘")
USER_NAME = "민수"
# 음성으로 읽히므로 이모지·목록기호·굵은글씨는 그대로 사고가 된다.
JUNK   = re.compile(r"[^가-힣ㄱ-ㆎa-zA-Z0-9\s.,?!~'\"·…\-()]")

def norm(s):
    return re.sub(r"[^가-힣a-zA-Z0-9]", "", s or "")

def sim(a, b):
    a, b = norm(a), norm(b)
    return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0

def sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]

def questions(text):
    """답변 안의 물음 문장들. 같은 걸 또 묻는지 보려면 물음만 따로 봐야 한다."""
    return [s for s in sentences(text) if s.endswith("?")]

def tail(text):
    """마지막 문장. 13턴부터 같은 꼬리가 끝까지 붙는 것을 잡으려면 여기를 봐야 한다."""
    ss = sentences(text)
    return ss[-1] if ss else ""

def wrong_name(answer):
    """사용자를 AI 쪽 이름(준호)으로 부르는 것만 센다.

    "나? 준호야." 는 이름을 묻는 말에 제대로 답한 것이라 오류가 아니다.
    호격으로 부를 때만 쉼표가 붙는다 — "준호야, 힘들었겠다"."""
    return len(re.findall(r"준호야\s*,", answer))

def examples(persona):
    """페르소나의 대화 예시 줄. 이걸 그대로 베끼는지 본다."""
    return [l.split(":", 1)[1].strip() for l in persona.splitlines()
            if re.match(r"^\s*(친구|나|상대)\s*:", l)]

def score(answer, prev_answers, prev_questions, prev_tails, exs, want):
    qs, ss = questions(answer), sentences(answer)
    return {
        "글자수":   len(answer),
        "문장수":   len(ss),
        "존댓말":   len(POLITE.findall(answer)),
        "상담원":   len(AGENT.findall(answer)),
        "정체노출": len(AI.findall(answer)),
        "기호":     len(JUNK.findall(answer)),
        "호칭오류": wrong_name(answer),
        # 실측에서 15턴 중 10턴이 "민수야"로 시작했다. 친구는 매번 이름을 안 부른다.
        "호명":     answer.count(USER_NAME),
        # "충분히 잘할 수 있을 거야" 류. 네 턴 연속 나오면 친구가 아니라 상담사다.
        "격려":     len(CHEER.findall(answer)),
        # 규칙은 "한 문장, 길어도 두 문장. 40자 안팎"이다. 셋을 넘으면 어긴 것으로 센다.
        "길이초과": int(len(ss) > 2),
        # 같은 꼬리가 끝까지 붙던 것. "너는?" 같은 짧은 되물음은 반복이 아니라
        # 자연스러운 대화라서 뺀다 — 여덟 자 넘는 꼬리가 겹칠 때만 붕괴로 센다.
        "꼬리고착": int(len(tail(answer)) >= 8
                     and max([sim(tail(answer), t) for t in prev_tails] or [0]) >= 0.6),
        "예시베낌": round(max([sim(answer, e) for e in exs] or [0]), 2),
        "답변반복": round(max([sim(answer, a) for a in prev_answers] or [0]), 2),
        "질문반복": round(max([sim(q, p) for q in qs for p in prev_questions] or [0]), 2),
        "되묻기":   int(bool(qs)),
        # want 가 있는 턴만 기억을 채점한다. 없으면 None 이라 평균에서 빠진다.
        "기억":     None if not want else int(any(w in answer for w in want)),
    }

VIOLATIONS = ["존댓말", "상담원", "정체노출", "기호", "호칭오류", "길이초과", "꼬리고착"]

# ── 서버 ────────────────────────────────────────────────────────────
def post(path, data=None, files=None, timeout=300):
    r = requests.post(SERVER + path, data=data, files=files, headers=HDR, timeout=timeout)
    if r.status_code >= 400:
        raise RuntimeError(f"{path} {r.status_code}: {r.text[:300]}")
    return r

def register(persona, knowledge, rules):
    data = {"persona": persona, "knowledge": knowledge, "session": SID}
    if rules:
        data["rules"] = rules          # 없으면 서버의 BASE_RULES 가 그대로 쓰인다
    with open(VOICE, "rb") as f:
        out = post("/session/start", data=data,
                   files={"voice": ("voice.wav", f, "audio/wav")}).json()
    if out.get("warning"):
        print(f"  [경고] {out['warning']}")

def run_once(name, exs, rep):
    post("/reset", data={"session": SID})
    rows, prev_a, prev_q, prev_t, summary = [], [], [], [], ""
    for i, (say, want) in enumerate(SCRIPT, 1):
        r = post("/chat", data={"text": say, "session": SID}).json()
        ans = r["answer"]
        # 요약을 턴마다 남긴다. 되묻기를 틀렸을 때 "그 사실이 눈앞에 있었나"를
        # 나중에 따져보려면 이게 있어야 한다 — 있는데 안 쓴 것과 요약이 버린 것은
        # 처방이 전혀 다르다.
        row = {"변형": name, "회차": rep, "턴": i, "질문": say, "답변": ans,
               "초": r["elapsed"], "남은턴": r["turns"], "요약": r["summary"]}
        row.update(score(ans, prev_a, prev_q, prev_t, exs, want))
        rows.append(row)
        mark = "" if row["기억"] is None else ("  기억 O" if row["기억"] else "  기억 X")
        flags = "".join(f" [{k}]" for k in VIOLATIONS if row[k])
        print(f"  {i:2d}. {ans}{flags}{mark}")
        if r["summary"] != summary:
            summary = r["summary"]
            print(f"      ── 요약 ──\n      " + summary.replace("\n", "\n      "))
        prev_a.append(ans)
        prev_q += questions(ans)
        prev_t.append(tail(ans))
    return rows

def read(name, kind):
    p = PROMPT / f"{name}.{kind}.md"
    return p.read_text(encoding="utf-8") if p.exists() else ""

def run_variant(name, reps):
    persona   = read(name, "persona")
    knowledge = read(name, "knowledge")
    rules     = read(name, "rules")
    exs = examples(persona)
    print(f"\n{'='*70}\n{name} — 페르소나 {len(persona)}자 / 사전지식 {len(knowledge)}자"
          f" / 규칙 {len(rules) or '서버 기본값'}\n{'='*70}")
    register(persona, knowledge, rules)
    rows = []
    for rep in range(1, reps + 1):
        print(f"\n-- {rep}회차 --")
        rows += run_once(name, exs, rep)
    return rows

# ── 정리 ────────────────────────────────────────────────────────────
def summarize(rows):
    out = {}
    for name in dict.fromkeys(r["변형"] for r in rows):
        rs = [r for r in rows if r["변형"] == name]
        mem = [r for r in rs if r["기억"] is not None]
        d = {"턴": len(rs)}
        for k in ["글자수", "문장수", "예시베낌", "답변반복", "질문반복", "되묻기", "호명", "격려"]:
            d[k] = round(statistics.mean(r[k] for r in rs), 2)
        for k in VIOLATIONS:
            d[k] = sum(1 for r in rs if r[k])
        d["기억"] = f"{sum(r['기억'] for r in mem)}/{len(mem)}"
        # 뒤로 갈수록 규칙이 풀리는지 본다. 앞 절반과 뒤 절반을 가른다.
        half = len(SCRIPT) // 2
        for lbl, sel in [(f"앞{half}턴", lambda r: r["턴"] <= half),
                         (f"뒤{half}턴", lambda r: r["턴"] > half)]:
            part = [r for r in rs if sel(r)]
            d[lbl + "위반"] = sum(1 for r in part for k in VIOLATIONS if r[k])
            d[lbl + "글자"] = round(statistics.mean(r["글자수"] for r in part), 1)
        out[name] = d
    return out

def table(summary):
    cols = ["턴", "글자수", "문장수", "되묻기", "호명", "격려", "기억",
            "예시베낌", "답변반복", "질문반복"] \
           + VIOLATIONS + [c for c in summary[next(iter(summary))] if c.startswith(("앞", "뒤"))]
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
    ap.add_argument("--probe", action="store_true",
                    help="대화 대신 지어내기를 잰다. 사전지식에 있는 것과 "
                         "없는 것을 섞어 묻고 얼버무리는지 본다")
    ap.add_argument("--turns", type=int, default=0,
                    help="대본을 이 턴에서 자른다. 되묻기 지점을 쪼개지 않게 고를 것")
    ap.add_argument("--long", action="store_true",
                    help="100턴 대본으로 돌린다(tools/script_long.py). "
                         "20·50·95턴에서 같은 것을 되물어 거리별로 본다")
    a = ap.parse_args()
    global SCRIPT

    if a.long:
        from script_long import SCRIPT as LONG
        SCRIPT = LONG
    if a.turns:
        SCRIPT = SCRIPT[:a.turns]

    if a.list or not a.variants:
        for p in sorted(PROMPT.glob("*.persona.md")):
            print(p.name.replace(".persona.md", ""))
        return
    if not VOICE or not os.path.exists(VOICE):
        sys.exit("참조 음성이 없습니다. RAON_EVAL_VOICE 에 wav 경로를 넣으세요.")

    HDR["X-Token"] = token()
    rows = []
    for v in a.variants:
        if a.probe:
            print(f"\n{'='*70}\n{v} — 지어내기 시험\n{'='*70}")
            register(read(v, "persona"), read(v, "knowledge"), read(v, "rules"))
            rows += probe_run(v, a.reps)
        else:
            rows += run_variant(v, a.reps)

    WORK.mkdir(exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S") + ("_probe" if a.probe else "")
    (WORK / f"{stamp}.json").write_text(
        json.dumps({"턴": rows} if a.probe else {"요약": summarize(rows), "턴": rows},
                   ensure_ascii=False, indent=2), encoding="utf-8")
    probe_report(rows) if a.probe else table(summarize(rows))
    print(f"\n기록: tools/_work/{stamp}.json")

if __name__ == "__main__":
    main()
