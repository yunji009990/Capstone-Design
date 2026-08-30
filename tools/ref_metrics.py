# 바탕화면\테스트녹음파일nalyze.py 를 저장소로 들여온 것이다. 2026-08-01 청취
# 판정 90여 개에 맞춰 고르고 두 번 고친 지표라 새로 짜지 않는다. 고칠 일이 생기면
# 여기를 고치고, 바탕화면 쪽은 버린다.
"""합성 결과를 귀에 맞춰 재는 계측기.

처음엔 전대역 RMS 로 쟀는데 두 번 틀렸다 —
  1) 출력이 저역(50~150Hz)에 몰려 있어서, 말이 끝난 뒤에도 RMS 는 절반이나 남는다.
     그래서 무음 꼬리를 "소리 있음"으로 봤다.
  2) 끝에 말이 다시 시작하면 "소리끝=총길이"가 되어 중간 6초 공백이 지워졌다.
사람이 말로 듣는 것은 명료도 대역이라 300~4000Hz 로 걸러서 잰다.

판정은 사용자가 직접 들은 5개(talk1~5)에 맞춰 검증했다."""
import sys, re, json, urllib.parse
import numpy as np, soundfile as sf
from scipy.signal import butter, sosfiltfilt

BIN = 0.1          # 초
GAP = 0.3          # 이보다 긴 무음을 "쉼"으로 센다
RESTART = 1.5      # 이보다 긴 무음 뒤에 말이 다시 나오면 재시작으로 본다

def envelope(y, sr):
    sos = butter(4, [300, 4000], btype="band", fs=sr, output="sos")
    v = sosfiltfilt(sos, y)
    w = max(1, int(sr * BIN))
    n = len(v) // w
    if n < 1:
        return np.zeros(0), np.zeros(0, bool)
    r = np.sqrt((v[:n*w].reshape(n, w) ** 2).mean(axis=1))
    return r, r > np.percentile(r, 95) * 0.06     # 최댓값 대신 95분위 — 튐에 안 흔들린다

def artifact(y, sr):
    """지지직(고주파 아티팩트). 6kHz 이상 에너지 비율(%).

    청취 판정 90여 개에 맞춰 고른 지표다. 후보였던 거칠기(순서가 반대로 나옴),
    4~6k/1~4k 비, 고역 평탄도, 바닥 흔들림(잡음이 아니라 쉼을 재고 있었다)은 버렸다.

    기준을 두 번 고쳤다. 처음엔 good 5 / bad 5 만 보고 0.5 로 잡았는데, 같은 화자가
    톤·음높이·마이크 거리를 바꿔 녹음한 참조 4개가 0.02~0.78 로 나오면서 **전부
    깨끗하게 들렸다.** 지금 기준은 —
      1.0 미만   깨끗    (확인된 범위 0.02~0.78)
      1.0~1.2    애매    (cut 조건이 0.79~2.04 로 걸쳐 있다)
      1.2 이상   지지직  (bad 1.94~3.88, 잡음제거 2.44~7.24)

    **참조를 스펙트럼 가공한 조건에는 쓰지 말 것.** 저역통과(0.23)와 대역보정
    75%(0.21)가 깨끗하다고 나왔지만 실제로는 지지직이 들렸다. 출력의 6kHz 대역은
    참조의 그 대역을 따라가므로 측정 대상을 손댄 셈이 된다."""
    n, hop = 1024, 256
    if len(y) < n:
        return 0.0
    w = np.hanning(n + 1)[:-1]
    m = 1 + (len(y) - n) // hop
    idx = np.arange(n)[None, :] + hop * np.arange(m)[:, None]
    S = np.abs(np.fft.rfft(y[idx] * w, axis=1)) ** 2
    f = np.fft.rfftfreq(n, 1 / sr)
    return float(100 * S[:, f >= 6000].sum() / max(S.sum(), 1e-12))

def runs(mask, val):
    """연속 구간을 (시작, 길이) 로. 길이 단위는 초."""
    out, i = [], 0
    while i < len(mask):
        if mask[i] == val:
            j = i
            while j < len(mask) and mask[j] == val:
                j += 1
            out.append((i * BIN, (j - i) * BIN))
            i = j
        else:
            i += 1
    return out

def analyze(path, answer=""):
    y, sr = sf.read(path)
    if y.ndim > 1:
        y = y.mean(axis=1)
    r, sp = envelope(y, sr)
    tot = len(y) / sr
    # 절대 음량을 먼저 본다. 판정 기준이 상대값(95분위의 6%)이라, 잡음 바닥만 있는
    # 파일도 그 안에서 큰 쪽을 말소리로 봤다 — 사용자가 "아예 무음"이라고 한 파일을
    # '쉼과다'로 쟀다. 정상 출력의 말 대역 RMS 는 0.054~0.096, 무음은 0.0002 이하다.
    lvl = float(np.sqrt((sosfiltfilt(butter(4, [300, 4000], btype="band",
                                            fs=sr, output="sos"), y) ** 2).mean()))
    if lvl < 0.01 or not sp.any():
        return {"파일": path.split("/")[-1], "총": round(tot, 2),
                "말대역RMS": round(lvl, 5), "판정": "무음"}
    first, last = np.argmax(sp), len(sp) - 1 - np.argmax(sp[::-1])
    head = first * BIN
    end = (last + 1) * BIN
    inner = [(s, d) for s, d in runs(sp, False) if s > head and s + d < end and d >= GAP]
    restart = [(s, d) for s, d in inner if d >= RESTART]
    voiced = float(sp.sum()) * BIN
    n = len(re.sub(r"\s", "", answer))
    res = {
        "파일": path.split("/")[-1],
        "총": round(tot, 2),
        "말시작": round(head, 1),
        "말끝": round(end, 1),
        "꼬리무음": round(tot - end, 2),
        "내부쉼수": len(inner),
        "내부쉼합": round(sum(d for _, d in inner), 2),
        "최장쉼": round(max([d for _, d in inner], default=0), 2),
        "재시작": len(restart),
        "지지직": round(artifact(y, sr), 3),
    }
    if n:
        res["자수"] = n
        res["발화속도"] = round(voiced / n, 3)      # 쉼 뺀 순수 말하기 속도
        # 사람이 "느리다"고 느끼는 것은 쉼까지 포함한 쪽이다. 순수 발화속도가
        # 같은데도 체감이 갈린 사례(talk3 대 talk4)로 확인했다.
        res["체감속도"] = round((end - head) / n, 3)
    # 쉼 계열과 지지직은 원인이 다르므로 따로 판정하고 합쳐서 보여준다.
    pace = ("무음후재시작" if restart else
            "쉼과다" if res["내부쉼합"] >= 1.5 else
            "꼬리김" if res["꼬리무음"] >= 1.5 else "")
    res["판정"] = "+".join([x for x in (pace, "지지직" if res["지지직"] >= 1.2 else ("지지직?" if res["지지직"] >= 1.0 else "")) if x]) or "정상"
    return res

if __name__ == "__main__":
    rows = []
    for p in sys.argv[1:]:
        a = ""
        m = re.match(r"(.*)\.wav$", p)
        for cand in (p.replace("talk", "th").replace(".wav", ".txt"),
                     re.sub(r"talk_(\w+)_(\d+)\.wav", r"hdr_\1_\2.txt", p)):
            try:
                hd = dict(re.findall(r"^([\w-]+): (.*)$",
                                     open(cand, encoding="utf8", errors="replace").read(), re.M))
                a = urllib.parse.unquote(hd.get("x-answer", ""))
                break
            except OSError:
                pass
        rows.append(analyze(p, a))
    keys = ["파일", "총", "말끝", "꼬리무음", "내부쉼수", "내부쉼합", "최장쉼", "재시작", "자수", "발화속도", "체감속도", "지지직", "판정"]
    print("  ".join(f"{k:>7s}" for k in keys))
    for r in rows:
        print("  ".join(f"{str(r.get(k,'-')):>7s}" for k in keys))
