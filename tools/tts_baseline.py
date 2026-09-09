# -*- coding: utf-8 -*-
"""Raon 비교군을 뜬다. **서버에서 실행한다** — 실존 인물의 음성을 안 내려받는다.

Raon 을 내리면 이 소리를 다시 못 뜬다. 후보를 들을 때 「Raon 보다 나은가」를
물어야 하므로, 내리기 전에 최적 작동점의 소리를 남겨 둔다.

**최적 작동점 = RAON_TEMP 1.6 + 1세대 합성 참조.** 네 칸 표에서 이 조합이
첫낱말 3/45 · 되풀이 0 · 체감 0.140 으로 제일 좋았다(작업현황 3순위).

절차
  1) 원본 참조로 문장 열 개 합성            -> A_원본참조/
  2) 그중 첫 낱말이 샌 것을 빼고 이어붙임   -> 1세대 참조 (18초, 쉼 0.15초)
  3) 1세대 참조를 등록하고 같은 열 개 합성  -> B_1세대참조/   ← 비교군
  4) 원본 참조로 되돌린다

**등록은 이전 세션을 rmtree 로 지운다.** 원본은 ~/ref_backup 에 사본이 있다.
"""
import io, os, json, wave, time, uuid, urllib.request, urllib.parse

URL = "http://127.0.0.1:8000"
TOK = "23605a891e448b5aa46f82c8640b554c"
SID = "test_0907_104634"
BACK = "/home/crc_unity/ref_backup"
OUT = "/home/crc_unity/비교군"
SR = 24000
BOOT_SEC, BOOT_GAP = 18.0, 0.15      # ref_eval.py 와 같은 값

# 실제 대화에서 나올 만한 것으로 짠다. 길이·물음·감정을 섞고, 이름과 숫자를
# 하나씩 넣는다 - 받아적기가 무너지는 자리가 대개 거기다.
SAY = [
    ("01", "응, 나도 방금 일어났어."),
    ("02", "밥은 먹었어? 굶지 말고 챙겨 먹어."),
    ("03", "정원아, 요즘 어떻게 지내?"),
    ("04", "그때 강릉 갔다가 첫차 놓쳐서 세 시간이나 기다렸잖아."),
    ("05", "진짜? 그런 일이 있었구나. 많이 힘들었겠다."),
    ("06", "시골집 마루에서 수박 먹던 여름이 자꾸 생각나."),
    ("07", "괜찮아, 천천히 해도 돼."),
    ("08", "나 오늘 좀 졸린데, 너는 안 피곤해?"),
    ("09", "보고 싶었어. 언제 한번 얼굴 보자."),
    ("10", "잘 지내고 있어. 걱정하지 마."),
]

first = lambda s: next((w for w in (s or "").split() if any("가" <= c <= "힣" for c in w)), "")


def post(path, form=None, files=None, raw=False, timeout=300):
    if files:
        b = uuid.uuid4().hex
        parts = []
        for k, v in (form or {}).items():
            parts += [b"--" + b.encode(),
                      f'Content-Disposition: form-data; name="{k}"'.encode(), b"", v.encode()]
        for k, (fn, blob) in files.items():
            parts += [b"--" + b.encode(),
                      f'Content-Disposition: form-data; name="{k}"; filename="{fn}"'.encode(),
                      b"Content-Type: audio/wav", b"", blob]
        parts += [b"--" + b.encode() + b"--", b""]
        body = b"\r\n".join(parts)
        head = {"X-Token": TOK, "Content-Type": f"multipart/form-data; boundary={b}"}
    else:
        body = urllib.parse.urlencode(form or {}).encode()
        head = {"X-Token": TOK}
    req = urllib.request.Request(URL + path, data=body, headers=head)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read() if raw else json.load(r)


def synth_all(sub):
    """열 문장을 합성해 저장하고, 되받아적기까지 해 둔다."""
    d = os.path.join(OUT, sub)
    os.makedirs(d, exist_ok=True)
    heard = {}
    for tag, text in SAY:
        p = os.path.join(d, f"{tag}.wav")
        t0 = time.time()
        wav = post("/tts", {"text": text, "session": SID}, raw=True)
        with open(p, "wb") as f:
            f.write(wav)
        io.open(p[:-4] + ".txt", "w", encoding="utf-8").write(text)
        h = post("/stt", files={"file": ("a.wav", wav)})["text"]
        heard[tag] = h
        w = wave.open(p); sec = w.getnframes() / w.getframerate(); w.close()
        ok = "O" if first(h) == first(text) else "X"
        print(f"  {tag} {sec:5.2f}초 {time.time()-t0:5.1f}s [{ok}] {h[:40]}")
    io.open(os.path.join(d, "_되받아적기.json"), "w", encoding="utf-8").write(
        json.dumps(heard, ensure_ascii=False, indent=1))
    return d, heard


def build_1gen(d, heard):
    """첫 낱말이 샌 것을 빼고 18초까지 이어붙인다."""
    import numpy as np, soundfile as sf
    gap = np.zeros(int(BOOT_GAP * SR), dtype=np.float32)
    parts, used = [], []
    for tag, text in SAY:
        if first(heard.get(tag, "")) != first(text):
            continue                                   # 샌 것은 안 쓴다
        y, sr = sf.read(os.path.join(d, f"{tag}.wav"), dtype="float32")
        if y.ndim > 1:
            y = y.mean(1)
        y = np.trim_zeros(y, "fb")
        if parts and (sum(len(x) for x in parts) + len(y)) / SR > BOOT_SEC:
            break
        parts.append(y); used.append(tag)
    if not parts:
        raise RuntimeError("쓸 조각이 없다 - 열 개가 다 샜다")
    out = np.concatenate([v for x in parts for v in (x, gap)][:-1])
    m = float(abs(out).max()) or 1.0
    out = (out / m * 0.95).astype("float32")
    p = os.path.join(OUT, "ref_1gen.wav")
    sf.write(p, out, SR, subtype="PCM_16")
    print(f"  1세대 참조 {len(out)/SR:.1f}초, 조각 {len(parts)}개 {used}")
    return p


def register(voice_path):
    with open(voice_path, "rb") as f:
        v = f.read()
    form = {"session": SID,
            "persona": io.open(f"{BACK}/persona.md", encoding="utf-8").read(),
            "knowledge": io.open(f"{BACK}/knowledge.md", encoding="utf-8").read()}
    r = post("/session/start", form, {"voice": ("voice.wav", v)})
    print(f"  등록 {r}")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print("[1] 원본 참조로 합성")
    d, heard = synth_all("A_원본참조")
    print("[2] 1세대 참조 만들기")
    ref = build_1gen(d, heard)
    print("[3] 1세대 참조 등록하고 다시 합성")
    register(ref)
    synth_all("B_1세대참조")
    print("[4] 원본 참조로 되돌리기")
    register(f"{BACK}/voice_orig.wav")
    io.open(os.path.join(OUT, "문장.json"), "w", encoding="utf-8").write(
        json.dumps({"문장": SAY, "온도": os.environ.get("RAON_TEMP", "1.6"),
                    "샘플레이트": SR}, ensure_ascii=False, indent=1))
    print("끝")
