# -*- coding: utf-8 -*-
"""대화 2판이 제대로 된 시험인지 검증한다. 서버에서 실행한다.

**1판은 사람 쪽 글과 소리가 딴 문장이었다.** 그 사고를 다시 내지 않으려면
말로 「고쳤다」고 할 게 아니라 재야 한다. 네 가지를 본다.

  1) 사람 쪽 소리가 정말 그 글을 말하는가      — Whisper 로 받아적어 대조
  2) 할머니 쪽 소리가 정말 그 글을 말하는가    — 같은 방법
  3) 사람과 할머니가 다른 목소리인가          — 화자 임베딩 코사인
     (귀 판정과는 상관 낮지만 「같은 사람이냐 아니냐」는 갈린다:
      실측으로 다른 화자 0.16 · 같은 화자 0.94)
  4) 터진 칸(27초)이 되풀이인가               — 받아적은 글을 눈으로
"""
import io, os, re, sys, json, glob
import numpy as np

BACK = "/home/crc_unity/ref_backup"
USER_DIR = "/home/crc_unity/대화_사람"
OUT = "/home/crc_unity/moss결과2"
FOLDERS = [("A_조혜정_통짜", "조혜정 통짜"), ("A_조혜정", "조혜정 자른것"),
           ("B_사용자", "사용자")]


def norm(s):
    return re.sub(r"[^가-힣0-9a-zA-Z]", "", s or "")


def cer(ref, hyp):
    a, b = norm(ref), norm(hyp)
    d = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        p, d[0] = d[0], i
        for j, y in enumerate(b, 1):
            p, d[j] = d[j], min(d[j] + 1, d[j - 1] + 1, p + (x != y))
    return d[len(b)] / max(1, len(a))


def main():
    from faster_whisper import WhisperModel
    m = WhisperModel("large-v3", device="cuda", compute_type="float16")

    def hear(p):
        segs, _ = m.transcribe(p, language="ko", beam_size=5)
        return " ".join(s.text for s in segs).strip()

    print("=" * 74)
    print("1) 사람 쪽 소리가 그 글을 말하는가")
    print("=" * 74)
    ok1 = True
    for i in range(1, 6):
        want = io.open(f"{USER_DIR}/u{i}.txt", encoding="utf-8").read().strip()
        got = hear(f"{USER_DIR}/u{i}.wav")
        c = cer(want, got)
        ok1 &= c < 0.25
        print(f"  u{i} CER {c:.2f}  기대 {want}")
        print(f"        받음 {got}")

    print()
    print("=" * 74)
    print("2) 할머니 쪽 소리가 그 글을 말하는가  (3) 길이 이상")
    print("=" * 74)
    bad = []
    for folder, label in FOLDERS:
        d = f"{OUT}/{folder}/MOSS_대화"
        if not os.path.isdir(d):
            continue
        print(f"\n  [{label}]")
        for i in range(1, 6):
            p = f"{d}/turn{i}.wav"
            if not os.path.exists(p):
                continue
            want = io.open(f"{d}/turn{i}.txt", encoding="utf-8").read().split("\n")[1]
            want = want.replace("할머니: ", "").strip()
            got = hear(p)
            c = cer(want, got)
            import wave
            w = wave.open(p); sec = w.getnframes() / w.getframerate(); w.close()
            flag = "  ★" if c > 0.25 else ""
            print(f"   {i}턴 {sec:6.2f}초 CER {c:.2f}{flag}")
            if c > 0.25:
                print(f"        기대 {want}")
                print(f"        받음 {got[:160]}")
                bad.append((label, i, sec, want, got))

    print()
    print("=" * 74)
    print("4) 사람과 할머니가 다른 목소리인가")
    print("=" * 74)
    try:
        import torch, torchaudio
        from speechbrain.inference import EncoderClassifier
        enc = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir="/home/crc_unity/.sb_ecapa", run_opts={"device": "cuda"})

        def emb(p):
            w, sr = torchaudio.load(p)
            if sr != 16000:
                w = torchaudio.functional.resample(w, sr, 16000)
            if w.shape[0] > 1:
                w = w.mean(0, keepdim=True)
            with torch.no_grad():
                e = enc.encode_batch(w.cuda()).squeeze()
            return (e / e.norm()).cpu().numpy()

        u = emb(f"{USER_DIR}/u1.wav")
        for folder, label in FOLDERS:
            p = f"{OUT}/{folder}/MOSS_대화/turn1.wav"
            if os.path.exists(p):
                print(f"  사람 vs {label:14} 코사인 {float(u @ emb(p)):.3f}")
        print("  (다른 화자 0.16 · 같은 화자 0.94 가 실측 기준)")
    except Exception as e:
        print(f"  화자 비교 건너뜀 — {str(e)[:120]}")

    print()
    print("=" * 74)
    print(f"판정 — 사람 쪽 짝 맞음: {'예' if ok1 else '아니오'}   "
          f"할머니 쪽 어긋난 칸: {len(bad)}개")
    print("=" * 74)


if __name__ == "__main__":
    main()
