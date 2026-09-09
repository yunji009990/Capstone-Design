# -*- coding: utf-8 -*-
"""후보 STT 를 Raon 과 같은 20문장으로 잰다. 서버에서 실행한다.

정답표는 Raon 이 살아 있을 때 떠 둔 합성 음성이다 — 무슨 말인지 아는 소리다.
**깨끗한 합성 소리라 쉬운 쪽이다.** 실제 마이크 소리는 둘 다 나빠진다.
그래도 「바꾸면 나아지나」의 첫 관문으로는 쓸 수 있다.

Raon 실측 (2026-09-08): A_원본참조 CER 2.0% / B_1세대참조 CER 1.6%
"""
import io, os, re, sys, time, json, glob

OUT = "/home/crc_unity/비교군"
SETS = ["A_원본참조", "B_1세대참조"]


def norm(s):
    """문장부호·공백은 안 센다 — 받아적기 규격이 서로 다르다."""
    return re.sub(r"[^가-힣0-9a-zA-Z]", "", s or "")


def cer(ref, hyp):
    a, b = norm(ref), norm(hyp)
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (x != y))
    return d[len(b)] / max(1, len(a))


def run_faster_whisper(size):
    from faster_whisper import WhisperModel
    m = WhisperModel(size, device="cuda", compute_type="float16")

    def f(path):
        segs, _ = m.transcribe(path, language="ko", beam_size=5)
        return " ".join(s.text for s in segs).strip()
    return f


if __name__ == "__main__":
    size = sys.argv[1] if len(sys.argv) > 1 else "large-v3"
    name = f"faster-whisper {size}"
    tr = run_faster_whisper(size)
    result = {}
    for sub in SETS:
        d = os.path.join(OUT, sub)
        tot = n = el = 0
        bad = []
        for p in sorted(glob.glob(os.path.join(d, "*.wav"))):
            ref = io.open(p[:-4] + ".txt", encoding="utf-8").read().strip()
            t0 = time.time()
            hyp = tr(p)
            el += time.time() - t0
            c = cer(ref, hyp)
            tot += c; n += 1
            if c > 0:
                bad.append((os.path.basename(p)[:-4], round(c, 3), ref, hyp))
        result[sub] = {"CER": round(tot / n, 4), "문장": n,
                       "평균초": round(el / n, 2), "틀린것": bad}
        print(f"\n{name}  {sub}   CER {tot/n:.3f}   문장당 {el/n:.2f}초   ({n}문장)")
        for t, c, r, h in bad:
            print(f"  {t} CER {c}\n     정답 {r}\n     받음 {h}")
    io.open(os.path.join(OUT, f"stt_{size}.json"), "w", encoding="utf-8").write(
        json.dumps({"후보": name, **result}, ensure_ascii=False, indent=1))
