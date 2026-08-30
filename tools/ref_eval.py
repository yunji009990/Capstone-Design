"""참조 음성 실험 계측기 — 참조 길이가 안정성과 닮음에 어떻게 작용하는지 잰다.

재는 것 둘.
  안정성 — 같은 조건을 여러 번 돌렸을 때 매번 제대로 나오는가.
           `ref_metrics.analyze()` 가 꼬리 무음·내부 쉼·재시작·지지직으로 판정한다.
  닮음   — 합성된 목소리가 그 사람 같은가.
           TitaNet 화자 임베딩 코사인. 실측으로 다른 화자 0.16 · 같은 화자 0.94 라
           깨끗하게 갈린다.

**닮음은 참조가 아니라 `B_대조` 와 견준다.** 생성된 소리를 참조 그 자체와 비교하면
목소리가 아니라 그 파일의 녹음 특성을 재게 된다. 참조로 쓰지 않은 다른 녹음과
견줘야 "그 사람 같은가"를 잰 것이 된다.

**판정은 사람이 한다.** 여기서 나오는 숫자는 어느 조건을 들어볼지 고르는 데 쓴다.
지지직·먹먹함·떨림은 숫자로 못 잡는다는 것이 2026-08-01 에 지표 네 개를 버리며
확인한 것이다. `listen` 이 만드는 폴더를 귀로 들어야 결론이 난다.

  python tools/ref_eval.py cut      A_참조 에서 길이 조건 파일을 만든다
  python tools/ref_eval.py run      조건마다 등록하고 합성한다 (오래 걸린다)
  python tools/ref_eval.py score    지표를 표로 낸다
  python tools/ref_eval.py listen   들어볼 파일을 블라인드로 정리한다
"""
import glob
import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile

import librosa
import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ref_metrics                                            # noqa: E402

ROOT = os.path.expanduser("~/Desktop/테스트녹음파일/참조길이실험")
LISTEN = os.path.expanduser("~/Desktop/테스트녹음파일/들어볼것")
COND, OUT = os.path.join(ROOT, "조건"), os.path.join(ROOT, "출력")

# 5 초는 깨질 것으로 보고 넣는다 — 실측에서 6.3 초가 같은 문장에 0/8.6/4.3 초를 냈다.
# 45 초는 서버 권장 상한(40) 밖이라 넣는다. 경계를 안 넘기면 경계를 못 찾는다.
LENGTHS = [5, 8, 12, 20, 30, 45]

# 억양 실험 — 길이를 고정하고 말하는 방식만 바꾼다. 1차에서 길이는 귀로 구별되지
# 않았고(상관 +0.12) 사용자가 짚은 것은 억양이었다. 조건 파일은 번호로 두고
# 이름표는 `조건/_이름.json` 에 따로 적는다 — 뒤 단계를 안 건드리려고.
STYLES = [("01", "낭독조", "A_낭독"), ("02", "설명조", "A_설명"), ("03", "대화체", "A_대화")]
STYLE_SEC = 18          # 세 조건 모두 이 길이로 맞춘다. 2차에서 12/20/30초가
                        # 다 14~15/15 로 같았으므로 이 대역 안이면 길이는 문제가 아니다.
INPUTS = ["C1", "C2", "C3", "C4", "C5"]
ROUNDS = 3
SR = 24000

RAON = os.environ.get("RAON_URL", "http://220.69.208.201:8000")
TOKEN = os.environ.get("RAON_TOKEN", "23605a891e448b5aa46f82c8640b554c")
NEMO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    "Web", "extraction", "nemo_env", "Scripts", "python.exe")

# 조건 사이에 달라지면 안 되는 것이라 여기 박아 둔다. 120자 미만이면 서버가 경고한다.
PERSONA = """너는 예순쯤 된 사람이다. 상대를 오래 알고 지낸 사이라 말이 편하다.
한 번에 한두 문장만 말한다. 길게 늘어놓지 않는다.
상대가 한 말을 받아서 짧게 되묻거나 맞장구를 친다.
"그렇구나", "그랬어" 같은 말을 자주 쓴다. 존댓말은 쓰지 않는다."""


def _label(tag):
    """조건 번호를 사람이 읽는 이름으로. 없으면 길이 실험이라 초 단위로 본다."""
    f = os.path.join(COND, "_이름.json")
    if os.path.exists(f):
        m = json.load(io.open(f, encoding="utf-8"))
        if str(tag).zfill(2) in m:
            return m[str(tag).zfill(2)]
    return f"{tag}초"


def _hdr():
    return {"X-Token": TOKEN} if TOKEN else {}


def _base():
    """닮음의 기준선. 억양 실험부터는 `B_기준`(대화체)을 쓴다. 1차의 `B_대조` 는
    대본을 읽은 것이라 귀 판정의 기준으로 쓸 수 없었다 — 사용자가 "원본인데
    내 목소리 같지 않다"고 했다."""
    for stem in ("B_기준", "B_대조"):
        if glob.glob(os.path.join(ROOT, stem + ".*")):
            return _find(stem)
    sys.exit("기준 녹음이 없습니다 (B_기준)")


def _find(stem):
    """확장자를 가리지 않고 찾는다. 휴대폰 녹음은 m4a 로 나온다."""
    hit = [p for p in glob.glob(os.path.join(ROOT, stem + ".*"))
           if os.path.splitext(p)[1].lower() in (".wav", ".m4a", ".mp3", ".flac", ".ogg")]
    if not hit:
        sys.exit(f"없음: {os.path.join(ROOT, stem)}.wav — _녹음안내.txt 를 보고 넣어주세요")
    return hit[0]


def _norm(y):
    """Web/app.py `_to_wav24` 의 음량 처리와 같아야 한다. 거기가 바뀌면 여기도 바꾼다."""
    peak = float(np.abs(y).max())
    return y * (min(10.0, 0.8 / peak) if peak > 1e-6 else 1.0)


def _quiet(y):
    """쉼 비율. 0.20 을 넘으면 제품(`_cut_silence`)이 참조를 잘라낸다 — 변수가 하나 는다."""
    w = 2400
    n = len(y) // w
    if n < 2:
        return 0.0
    r = np.sqrt((y[:n * w].reshape(n, w) ** 2).mean(axis=1))
    return float((r < r.max() * 0.05).mean()) if r.max() > 0 else 0.0


WEBAPP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "Web", "app.py")


def _cut_silence(y):
    """`Web/app.py` 의 같은 이름 함수를 그 자리에서 꺼내 쓴다. 앱을 import 하면
    FastAPI 와 DB 까지 깨어나고, 베껴 두면 저쪽이 바뀔 때 조용히 어긋난다."""
    src = io.open(WEBAPP, encoding="utf-8").read()
    ns = {"np": np}
    for line in src.splitlines():
        if line.startswith(("CUT_BIN", "CUT_KEEP", "CUT_XF")):
            exec(line, ns)
    exec(src[src.index("def _cut_silence"):src.index("def _to_wav24")], ns)
    return ns["_cut_silence"](y)


# ─────────────────────────── cut ───────────────────────────
def cut():
    src = _find("A_참조")
    y, _ = librosa.load(src, sr=SR, mono=True)
    total = len(y) / SR
    print(f"원본 {os.path.basename(src)} — {total:.1f}초\n")
    if total < max(LENGTHS):
        print(f"  ! {max(LENGTHS)}초가 안 됩니다. 그 위 조건은 건너뜁니다.\n")
    # 쉼은 **자른 다음에 자른다.** 조건마다 따로 자르면 잘려 나가는 양이 달라져서
    # 길이가 유일한 변수가 아니게 된다. 원본에서 한 번만 잘라 놓고 거기서 앞부분을
    # 떼면 모든 조건이 같은 전처리를 지나고 길이만 달라진다.
    # 제품(`_to_wav24`)도 20%를 넘으면 자르므로 실제 경로에도 이쪽이 가깝다.
    if _quiet(y) >= 0.20:
        was = len(y) / SR
        y = _cut_silence(y)
        print(f"쉼이 20% 를 넘어 원본에서 한 번 잘라냈다 "
              f"— {was:.1f} -> {len(y) / SR:.1f}초. 제품도 같은 일을 한다.")
    total = len(y) / SR
    if total < max(LENGTHS):
        print(f"  ! {total:.1f}초뿐이라 만들 수 없는 조건이 있다: "
              f"{[n for n in LENGTHS if n > total]}")
    print()
    os.makedirs(COND, exist_ok=True)

    print(f"{'조건':>6} {'길이':>7} {'쉼비율':>7} {'peak':>6} {'게인':>5}   비고")
    print("-" * 62)
    for n in LENGTHS:
        if total < n:
            continue
        seg = _norm(y[:n * SR])
        q = _quiet(seg)
        sf.write(os.path.join(COND, f"ref_{n:02d}.wav"), seg, SR, subtype="PCM_16")
        note = "제품이라면 쉼을 잘라낸다 (변수 하나 늘어남)" if q >= 0.20 else ""
        peak = float(np.abs(y[:n * SR]).max())
        print(f"ref_{n:02d} {n:6.1f}초 {q:7.3f} {peak:6.3f} "
              f"{min(10.0, 0.8 / max(peak, 1e-6)):5.2f}   {note}")
    print(f"\n-> {COND}")


def cut_style():
    """억양 실험용. 세 녹음을 같은 길이로 잘라 조건으로 만든다.

    길이 실험과 달리 원본이 셋이므로 앞부분을 떼는 대신 **각각을** 같은 길이로
    맞춘다. 길이가 유일하게 같아야 하는 것이고, 달라야 하는 것은 말하는 방식뿐이다."""
    os.makedirs(COND, exist_ok=True)
    names = {}
    print(f"{'조건':>8} {'원본':>8} {'말한시간':>9} {'쉼':>7} {'잘라낸뒤':>9}")
    print("-" * 50)
    for tag, label, stem in STYLES:
        y, _ = librosa.load(_find(stem), sr=SR, mono=True)
        said = len(y) / SR - _quiet(y) * len(y) / SR
        if _quiet(y) >= 0.20:
            y = _cut_silence(y)
        if len(y) / SR < STYLE_SEC:
            print(f"  ! {stem} 이 잘라낸 뒤 {len(y)/SR:.1f}초뿐입니다 ({STYLE_SEC}초 필요)")
            continue
        seg = _norm(y[:STYLE_SEC * SR])
        sf.write(os.path.join(COND, f"ref_{tag}.wav"), seg, SR, subtype="PCM_16")
        names[tag] = label
        print(f"{label:>8} {stem:>8} {said:8.1f}초 {_quiet(seg):7.3f} {len(y)/SR:8.1f}초")
    json.dump(names, io.open(os.path.join(COND, "_이름.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    print()
    print(f"-> {COND}")


BOOT_SEC = 18            # 1세대 참조도 다른 조건과 같은 길이로 맞춘다
BOOT_GAP = 0.15          # 이어붙이는 자리에 두는 쉼. 0 이면 숨 가쁘게 들린다


def bootstrap(sub, skip=0):
    """`sub` 회차의 출력을 이어붙여 **1세대 합성 참조**를 만든다.

    사용자 발상 — 원본 녹음은 지금 대화와 아무 상관 없는 내용이라 이음매가
    억지스럽다. 그러니 원본으로 한 번 합성해 두고, 그 소리를 참조로 쓰면
    참조 자체가 이미 "그 사람이 다정하게 반말로 한 말"이 된다.

    **되받아적기가 정확했던 것만 고른다.** 첫 낱말이 샌 것을 참조로 쓰면
    그 결함이 다음 세대에 그대로 박힌다.

    재야 할 것은 이음매가 아니라 **드리프트**다 — 복사의 복사라 원본에서
    멀어질 수 있고, 이 제품은 "그 사람 목소리 같은가"가 전부다."""
    import re
    src = os.path.join(ROOT, sub, "출력")
    G = {os.path.basename(k): v for k, v in
         json.load(io.open(os.path.join(src, "_되받아적기.json"), encoding="utf-8")).items()}
    first = lambda s: (re.findall(r"[가-힣]+", s or "") or [""])[0]

    os.makedirs(COND, exist_ok=True)
    names, gap = {}, np.zeros(int(BOOT_GAP * SR), dtype=np.float32)
    print(f"{'조건':>10} {'쓴 조각':>8} {'길이':>7}   전사에 쓸 글")
    print("-" * 78)
    for tag, label, _ in STYLES:
        parts, texts = [], []
        # 회차가 아니라 **입력** 순으로 돈다. 파일명 순으로 고르면 같은 입력의
        # 세 회차가 먼저 잡혀 "그랬어 비가 왔네"만 되풀이하는 참조가 된다.
        cand = [os.path.join(src, f"out_{tag}_{c}_{rd}.wav")
                for rd in range(1, ROUNDS + 1) for c in INPUTS]
        cand = [x for x in cand if os.path.exists(x)]
        cand = cand[skip:] + cand[:skip]      # 회차를 돌려 서로 다른 참조를 만든다
        for q in cand:
            want = io.open(q[:-4] + ".txt", encoding="utf-8").read().strip()
            if first(want) != first(G.get(os.path.basename(q)) or ""):
                continue                      # 샌 것은 안 쓴다
            y, _ = librosa.load(q, sr=SR, mono=True)
            y = np.trim_zeros(y, "fb")
            if (sum(len(x) for x in parts) + len(y)) / SR > BOOT_SEC and parts:
                break                 # 넘치면 안 넣는다. 잘라 넣지 않는다.
            parts.append(y); texts.append(want)
        if not parts:
            print(f"{label:>10}   쓸 조각이 없습니다")
            continue
        out = _norm(np.concatenate([v for x in parts for v in (x, gap)][:-1]))
        sf.write(os.path.join(COND, f"ref_{tag}.wav"), out, SR, subtype="PCM_16")
        names[tag] = label + "1세대"
        io.open(os.path.join(COND, f"ref_{tag}.txt"), "w", encoding="utf-8").write(" ".join(texts))
        print(f"{label:>10} {len(parts):6}개 {len(out)/SR:6.1f}초   {' '.join(texts)[:44]}")
    json.dump(names, io.open(os.path.join(COND, "_이름.json"), "w", encoding="utf-8"),
              ensure_ascii=False)
    print()
    print(f"-> {COND}")


# ─────────────────────────── run ───────────────────────────
def run():
    import httpx

    refs = sorted(glob.glob(os.path.join(COND, "ref_*.wav")))
    if not refs:
        sys.exit("조건 파일이 없습니다. 먼저 `cut` 을 돌리세요")
    ins = {c: _find(c) for c in INPUTS}
    os.makedirs(OUT, exist_ok=True)

    # 입력 발화는 조건 사이에 완전히 같아야 한다. 한 번만 24k 로 맞춰 두고 재사용한다.
    prepped = {}
    for c, p in ins.items():
        y, _ = librosa.load(p, sr=SR, mono=True)
        t = os.path.join(tempfile.gettempdir(), f"_refeval_{c}.wav")
        sf.write(t, y, SR, subtype="PCM_16")
        prepped[c] = t

    done = fail = 0
    for ref in refs:
        tag = os.path.basename(ref)[4:6]
        sess = f"reflen_{tag}"
        with open(ref, "rb") as f:
            r = httpx.post(f"{RAON}/session/start", headers=_hdr(),
                           data={"persona": PERSONA, "session": sess},
                           files={"voice": ("voice.wav", f.read(), "audio/wav")},
                           timeout=300)
        if r.status_code != 200:
            print(f"[{tag}] 등록 실패 {r.status_code} — {r.text[:200]}")
            continue
        print(f"[{tag}] 등록됨")

        for rd in range(1, ROUNDS + 1):
            # 회차마다 이력을 비운다. 안 비우면 뒤 회차가 앞 회차의 대화를 이어받아
            # 답변 길이가 달라지고, 조건이 아니라 턴 수를 재게 된다.
            httpx.post(f"{RAON}/reset", headers=_hdr(), data={"session": sess}, timeout=30)
            for c in INPUTS:
                stem = os.path.join(OUT, f"out_{tag}_{c}_{rd}")
                if os.path.exists(stem + ".wav"):
                    done += 1
                    continue
                with open(prepped[c], "rb") as f:
                    t = httpx.post(f"{RAON}/talk", headers=_hdr(),
                                   data={"session": sess, "show_heard": "1"},
                                   files={"file": ("in.wav", f.read(), "audio/wav")},
                                   timeout=300)
                if t.status_code != 200:
                    print(f"  [{tag} {c} {rd}] 실패 {t.status_code}")
                    fail += 1
                    continue
                from urllib.parse import unquote
                with open(stem + ".wav", "wb") as o:
                    o.write(t.content)
                with open(stem + ".txt", "w", encoding="utf-8") as o:
                    o.write(unquote(t.headers.get("X-Answer", "")))
                done += 1
            print(f"  [{tag}] {rd}회차 끝 — 누적 {done}개")
    print(f"\n끝. 성공 {done} · 실패 {fail}\n-> {OUT}")


# ─────────────────────────── score ───────────────────────────
EMB = r'''
import warnings, logging, os, sys, json
warnings.filterwarnings("ignore")
logging.getLogger("nemo_logger").setLevel(logging.ERROR)
from nemo.collections.asr.models import EncDecSpeakerLabelModel
m = EncDecSpeakerLabelModel.from_pretrained("titanet_large", map_location="cpu").eval()
paths = json.load(open(sys.argv[1], encoding="utf-8"))
out = {}
for p in paths:
    try:
        out[p] = m.get_embedding(p).squeeze().detach().numpy().tolist()
    except Exception as e:
        out[p] = None
json.dump(out, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
'''


def _embeddings(paths):
    """TitaNet 은 nemo_env 에만 있다. 별도 프로세스로 돌려 임베딩만 받아온다."""
    if not os.path.exists(NEMO):
        print(f"  ! nemo_env 가 없습니다 ({NEMO}) — 닮음은 건너뜁니다")
        return {}
    d = tempfile.gettempdir()
    sp, ip, op = (os.path.join(d, "_refemb.py"), os.path.join(d, "_refemb_in.json"),
                  os.path.join(d, "_refemb_out.json"))
    with open(sp, "w", encoding="utf-8") as f:
        f.write(EMB)
    with open(ip, "w", encoding="utf-8") as f:
        json.dump(paths, f, ensure_ascii=False)
    print(f"  화자 임베딩 {len(paths)}개 계산 중 (몇 분 걸립니다)...")
    r = subprocess.run([NEMO, sp, ip, op], capture_output=True)
    if not os.path.exists(op):
        print(f"  ! 임베딩 실패 — {r.stderr.decode('utf-8', 'replace')[-400:]}")
        return {}
    with open(op, encoding="utf-8") as f:
        return {k: np.array(v) for k, v in json.load(f).items() if v}


def _cos(a, b):
    return float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b)))


def _spoken(paths):
    """생성된 소리를 되받아적어 답변 텍스트와 맞춰본다.

    **소리의 모양만 보면 못 잡는 고장이 있다.** 45개 중 7개에서 첫 낱말이 다른
    말로 나왔는데(`그랬어?` -> `어렵겠어?`) 꼬리무음·쉼·지지직 어느 것도 안 걸렸다.
    사용자가 귀로 먼저 찾았다. 문서에도 적혀 있다 — "계측기를 검증할 때는 반드시
    `/stt` 로 내용까지 볼 것."

    한 번 받아적으면 `출력/_되받아적기.json` 에 남겨 두고 다시 부르지 않는다."""
    cache = os.path.join(OUT, "_되받아적기.json")
    got = json.load(io.open(cache, encoding="utf-8")) if os.path.exists(cache) else {}
    if not isinstance(got, dict):        # 모양이 다르면 버리고 새로 받는다
        got = {}
    todo = [q for q in paths if q not in got]
    if todo:
        import httpx
        print(f"  되받아적기 {len(todo)}개...")
        for q in todo:
            try:
                with open(q, "rb") as f:
                    got[q] = httpx.post(f"{RAON}/stt", headers=_hdr(),
                                        files={"file": ("x.wav", f.read(), "audio/wav")},
                                        timeout=120).json().get("text", "")
            except Exception as e:
                print(f"  ! {os.path.basename(q)} — {e}")
                got[q] = None
        json.dump(got, io.open(cache, "w", encoding="utf-8"), ensure_ascii=False)
    return got


def _repeated(got):
    """되받아적은 글 안에서 같은 말이 되풀이되는가.

    소리 온도를 올리면 사람 말하는 느낌이 살아나는 대신 이게 늘어난다.
    사용자가 "똑같은 말을 두 번 한다"고 먼저 찾았고 기존 지표는 아무것도
    못 잡았다 — 꼬리·쉼·지지직·속도 어느 것도 되풀이를 안 본다.

      '어제 산책? 비 오는데 산책했어? 기분 전환 산책했어? 기분 전환에...'

    붙어 있는 되풀이(3낱말·2낱말)를 먼저 보고, 없으면 글 전체에서 같은
    두 낱말 짝이 두 번 나오는지 본다."""
    w = re.findall(r"[가-힣]+", got or "")
    for n in (3, 2):
        for i in range(len(w) - n * 2 + 1):
            if w[i:i + n] == w[i + n:i + 2 * n]:
                return " ".join(w[i:i + n])
    seen = set()
    for i in range(len(w) - 1):
        k = " ".join(w[i:i + 2])
        if k in seen:
            return k
        seen.add(k)
    return ""


def _same_first(want, got):
    """첫 낱말이 같은가. 뒷부분은 대체로 멀쩡하고 앞에서만 무너진다."""
    w = re.findall(r"[가-힣]+", want or "")
    g = re.findall(r"[가-힣]+", got or "")
    return bool(w) and bool(g) and w[0] == g[0]


def score():
    outs = sorted(glob.glob(os.path.join(OUT, "out_*.wav")))
    if not outs:
        sys.exit("출력이 없습니다. 먼저 `run` 을 돌리세요")
    base = _base()

    rows = []
    for p in outs:
        tag, c, rd = os.path.basename(p)[4:-4].split("_")
        ans = ""
        if os.path.exists(p[:-4] + ".txt"):
            with open(p[:-4] + ".txt", encoding="utf-8") as f:
                ans = f.read()
        a = ref_metrics.analyze(p.replace(os.sep, "/"), ans)
        a["답변"] = ans
        a.update({"길이": int(tag), "입력": c, "회차": int(rd), "경로": p})
        rows.append(a)

    said = _spoken([r["경로"] for r in rows])
    for r in rows:
        r["말바뀜"] = not _same_first(r.get("답변", ""), said.get(r["경로"]))
        r["되풀이"] = _repeated(said.get(r["경로"]))

    emb = _embeddings([base] + [r["경로"] for r in rows]
                      + sorted(glob.glob(os.path.join(COND, "ref_*.wav"))))
    if base in emb:
        for r in rows:
            if r["경로"] in emb:
                r["닮음"] = round(_cos(emb[r["경로"]], emb[base]), 4)

    # 상수가 아니라 실제로 들어온 것을 센다. 중간에 끊긴 실행을 다 돌린 것처럼
    # 읽으면 빠진 조건을 못 알아챈다.
    seen = lambda k: len({r[k] for r in rows})
    lines = ["# 참조 음성 실험 결과", "",
             f"조건 {seen('길이')} · 입력 {seen('입력')} · 회차 {seen('회차')} · "
             f"출력 {len(rows)}개 (기대 {seen('길이')*len(INPUTS)*ROUNDS}개)", "",
             "닮음은 기준 녹음(참조로 쓰지 않은 것)과의 화자 임베딩 코사인이다.", "",
             "**귀 판정과 상관이 없었다(-0.10).** 이 모델은 음색으로 화자를 가리는지라",
             "억양이 달라도 같은 사람으로 본다. 사용자가 실제로 듣는 것은 억양이다.", "",
             "| 조건 | 정상 | 깨짐 | 첫낱말 바뀜 | 되풀이 | 꼬리무음 중앙 | 체감속도 중앙 | 지지직 중앙 | 닮음 중앙 | 닮음 최저 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    order = {t: i for i, t in enumerate(str(x).zfill(2) for x in LENGTHS)}
    for n in sorted({r["길이"] for r in rows}, key=lambda t: order.get(str(t).zfill(2), 99)):
        g = [r for r in rows if r["길이"] == n]
        ok = sum(1 for r in g if r.get("판정") == "정상")
        sim = [r["닮음"] for r in g if "닮음" in r]
        med = lambda k: np.median([r[k] for r in g if k in r]) if any(k in r for r in g) else float("nan")
        lines.append(
            f"| {_label(n)} | {ok}/{len(g)} | {len(g)-ok} | {sum(1 for r in g if r.get('말바뀜'))}/{len(g)} | {sum(1 for r in g if r.get('되풀이'))}/{len(g)} | {med('꼬리무음'):.2f} | "
            f"{med('체감속도'):.3f} | {med('지지직'):.2f} | "
            f"{np.median(sim):.4f} | {min(sim):.4f} |" if sim else
            f"| {_label(n)} | {ok}/{len(g)} | {len(g)-ok} | {sum(1 for r in g if r.get('말바뀜'))}/{len(g)} | {sum(1 for r in g if r.get('되풀이'))}/{len(g)} | {med('꼬리무음'):.2f} | "
            f"{med('체감속도'):.3f} | {med('지지직'):.2f} | - | - |")

    lines += ["", "체감속도 정상 범위는 0.103~0.128 초/글자다. 벗어나면 쉼이 낀 것이다.", "",
              "## 깨진 것", ""]
    bad = [r for r in rows if r.get("판정") != "정상"]
    if bad:
        lines += ["| 파일 | 길이 | 판정 | 총 | 꼬리무음 | 재시작 |", "|---|---|---|---|---|---|"]
        lines += [f"| {r['파일']} | {_label(r['길이'])} | {r['판정']} | {r['총']} | "
                  f"{r.get('꼬리무음','-')} | {r.get('재시작','-')} |" for r in bad]
    else:
        lines.append("없다.")

    lines += ["", "## 참조 자체의 닮음 (정상성 확인)", "",
              "조건 참조가 `B_대조` 와 얼마나 닮았는지. 여기서 낮으면 자른 구간 탓이지",
              "모델 탓이 아니다.", "", "| 조건 | 닮음 |", "|---|---|"]
    for p in sorted(glob.glob(os.path.join(COND, "ref_*.wav"))):
        if p in emb and base in emb:
            lines.append(f"| {os.path.basename(p)} | {_cos(emb[p], emb[base]):.4f} |")

    path = os.path.join(ROOT, "결과.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines[:14]))
    print(f"\n-> {path}")


# ─────────────────────────── listen ───────────────────────────
HEAR = ["C1", "C3", "C5"]  # 조건이 셋이라 입력 셋이면 9개. 귀로 볼 만한 양이다.
                           # 조건이 여섯일 때는 둘로 줄여 12개로 맞췄었다.


def listen(clear=False, sub=""):
    """`sub` 를 주면 그 하위 폴더의 출력을 쓴다 — 회차를 보관해 두고 되짚어 볼 때."""
    global OUT
    if sub:
        OUT = os.path.join(ROOT, sub, "출력")
        if not os.path.isdir(OUT):
            sys.exit(f"없음: {OUT}")
        print(f"{sub} 회차를 씁니다")
    stale = [f for f in os.listdir(LISTEN) if f != "이전"] if os.path.isdir(LISTEN) else []
    if stale and not clear:
        sys.exit(f"{LISTEN} 가 비어 있지 않습니다. 비우려면 --clear 를 붙이세요")
    if stale:
        # 지우지 않고 옮긴다. 여기에는 지난 가림 청취 자료가 들어 있고 그게
        # 문서에 실린 결론의 근거다. 한 번 지우면 되돌릴 방법이 없다.
        old_dir = os.path.join(LISTEN, "이전")
        os.makedirs(old_dir, exist_ok=True)
        for f in stale:
            dst, n = os.path.join(old_dir, f), 1
            while os.path.exists(dst):
                base, ext = os.path.splitext(f)
                dst = os.path.join(old_dir, f"{base}_{n}{ext}")
                n += 1
            os.replace(os.path.join(LISTEN, f), dst)
        print(f"이전 것 {len(stale)}개를 {old_dir} 로 옮겼습니다")
    os.makedirs(LISTEN, exist_ok=True)

    TAGS = sorted({os.path.basename(q)[4:6] for q in glob.glob(os.path.join(OUT, "out_*.wav"))})
    picks = []
    for gi, c in enumerate(HEAR, 1):
        # 조건 태그는 파일에서 읽는다. 길이 실험이면 05/12/…, 억양 실험이면 01/02/03 —
        # 상수에 박아 두면 실험을 갈아탈 때마다 조용히 0개가 나온다.
        got = [(t, os.path.join(OUT, f"out_{t}_{c}_1.wav")) for t in TAGS]
        got = [(t, q) for t, q in got if os.path.exists(q)]
        # 이름에 조건이 보이면 판정이 오염된다. 가설을 이미 말씀드린 뒤라 블라인드로 낸다.
        # 씨앗을 박아 두어야 _정답.txt 와 어긋나지 않는다.
        random.Random(20260830 + gi).shuffle(got)
        for si, (n, p) in enumerate(got):
            name = f"{gi}-{'가나다라마바사'[si]}.wav"
            shutil.copy(p, os.path.join(LISTEN, name))
            picks.append((name, n, c))

    shutil.copy(_base(), os.path.join(LISTEN, "기준_원본목소리.wav"))

    with open(os.path.join(LISTEN, "_들어보기.txt"), "w", encoding="utf-8") as f:
        f.write("""참조 음성 실험 — 들어보기
==================================

먼저 `기준_원본목소리.wav` 를 한 번 들으세요. 이게 기준입니다.
지난번과 달리 대본을 읽은 게 아니라 평소 말투로 녹음한 것입니다.

그 다음 1-가 부터 순서대로 들으시면서 아래에 적어주세요.
같은 묶음(1-*)은 전부 같은 말에 대한 답이고, 참조 녹음의 말투만 다릅니다.

  닮음   기준과 얼마나 같은 사람으로 들리는가.  1(다른 사람) ~ 5(그 사람)
  억양   말의 오르내림·속도·쉬는 자리가 기준과 같은가.  1(딴판) ~ 5(똑같다)
  깨짐   중간에 끊기거나, 끝나고도 소리가 계속되거나, 지지직거리는가.  O / X

닮음과 억양을 나눠 적는 것이 이번의 핵심입니다.
지난번에 「억양이 다르다」고 하신 것을 따로 재려는 것이라서요.
헷갈리면 억양만 적으셔도 됩니다.

조건은 일부러 섞어 두었습니다. 순서에 뜻이 없습니다.
다 들으신 뒤에 `_정답.txt` 를 여세요.

        닮음(1~5)   억양(1~5)   깨짐(O/X)   메모
""")
        for name, _, _ in picks:
            f.write(f"  {name[:-4]:8}                                \n")

    with open(os.path.join(LISTEN, "_정답.txt"), "w", encoding="utf-8") as f:
        f.write("다 들으신 뒤에 여세요.\n\n")
        for name, n, c in picks:
            f.write(f"  {name[:-4]:8} = {_label(n)} (입력 {c})\n")

    print(f"{len(picks)}개 + 기준 1개 -> {LISTEN}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "cut":
        cut_style() if "style" in sys.argv else cut()
    elif cmd == "boot":
        bootstrap(sys.argv[2] if len(sys.argv) > 2 else "3차_무음2",
                  int(sys.argv[3]) if len(sys.argv) > 3 else 0)
    elif cmd == "run":
        run()
    elif cmd == "score":
        score()
    elif cmd == "listen":
        rest = [a for a in sys.argv[2:] if not a.startswith("--")]
        listen("--clear" in sys.argv, rest[0] if rest else "")
    else:
        print(__doc__)
