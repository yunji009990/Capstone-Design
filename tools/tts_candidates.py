# -*- coding: utf-8 -*-
"""후보 TTS 를 **실제 사람 목소리** 참조로 잰다. 서버에서 실행한다.

앞선 회차는 무효다 — 참조로 쓴 파일이 사람 녹음이 아니라 Raon 이 만들어낸
소리였다(2026-09-08 에 Whisper 전사로 드러났다). 복사의 복사를 재고 있었다.

이번 회차의 규격
  참조     6~12초. **14초를 넘기면 Qwen3-TTS 가 폭주한다**(20자에 655초 소리)
  전사     Whisper large-v3 가 받아적은 것. 참조 전사가 틀리면 닮음이 떨어진다
  절차     원본 참조 -> 열 문장 -> 그중 18초를 이어붙여 1세대 참조 -> 열 문장

    python cand2.py <후보> <참조이름>
    예) python cand2.py qwen3tts A
"""
import io, os, sys, json, time
import numpy as np
import soundfile as sf

BACK = "/home/crc_unity/ref_backup"
OUT = "/home/crc_unity/비교군2"
SR = 24000
BOOT_SEC, BOOT_GAP = 12.0, 0.15      # 1세대도 규격 안에 둔다 (전에는 18초였다)

SAY = json.load(io.open("/home/crc_unity/비교군/문장.json", encoding="utf-8"))["문장"]

REFS = {"A": ("ref_A10", "조혜정"), "B": ("ref_B", "사용자")}


def load_omnivoice():
    import torch
    from omnivoice import OmniVoice
    m = OmniVoice.from_pretrained("k2-fsa/OmniVoice", device_map="cuda:0", dtype=torch.float16)
    def f(text, ref_wav, ref_text):
        a = m.generate(text=text, ref_audio=ref_wav, ref_text=ref_text)
        return np.asarray(a[0] if isinstance(a, (list, tuple)) else a, dtype="float32"), SR
    return f


def load_chatterbox():
    from chatterbox import ChatterboxMultilingualTTS
    m = ChatterboxMultilingualTTS.from_pretrained(device="cuda")
    def f(text, ref_wav, ref_text):
        w = m.generate(text, language_id="ko", audio_prompt_path=ref_wav)
        return w.squeeze().detach().cpu().numpy().astype("float32"), getattr(m, "sr", 24000)
    return f


def load_qwen3tts():
    import torch
    from qwen_tts import Qwen3TTSModel
    m = Qwen3TTSModel.from_pretrained("Qwen/Qwen3-TTS-12Hz-1.7B-Base",
                                      dtype=torch.bfloat16, device_map="cuda:0")
    def f(text, ref_wav, ref_text):
        # **(파형 목록, 샘플레이트) 를 돌려준다.** 앞 회차에서 이걸 놓쳐
        # 샘플레이트를 24000 으로 넘겨짚고 655초라는 유령 숫자를 냈다.
        wavs, fs = m.generate_voice_clone(text=text, language="Korean",
                                          ref_audio=ref_wav, ref_text=ref_text)
        return np.asarray(wavs[0]).squeeze().astype("float32"), fs
    return f


LOADERS = {"omnivoice": ("OmniVoice", load_omnivoice),
           "chatterbox": ("Chatterbox", load_chatterbox),
           "qwen3tts": ("Qwen3-TTS", load_qwen3tts)}


def synth_all(f, root, sub, ref_wav, ref_text):
    d = os.path.join(root, sub)
    os.makedirs(d, exist_ok=True)
    for tag, text in SAY:
        t0 = time.time()
        y, sr = f(text, ref_wav, ref_text)
        if len(y) == 0:
            print(f"  {tag} 빈 소리 — 다시", flush=True)
            y, sr = f(text, ref_wav, ref_text)
        if len(y) == 0:
            print(f"  {tag} ★빈 소리", flush=True); continue
        if sr != SR:
            import librosa
            y = librosa.resample(y, orig_sr=sr, target_sr=SR)
        sf.write(os.path.join(d, f"{tag}.wav"), y, SR, subtype="PCM_16")
        io.open(os.path.join(d, f"{tag}.txt"), "w", encoding="utf-8").write(text)
        el = time.time() - t0
        print(f"  {tag} {len(y)/SR:5.2f}초  {el:5.1f}s  RTF {el/(len(y)/SR):.3f}", flush=True)
    return d


def build_1gen(d, root):
    gap = np.zeros(int(BOOT_GAP * SR), dtype="float32")
    parts, used = [], []
    for tag, _ in SAY:
        p = os.path.join(d, f"{tag}.wav")
        if not os.path.exists(p):
            continue
        y, _ = sf.read(p, dtype="float32")
        if y.ndim > 1:
            y = y.mean(1)
        y = np.trim_zeros(y, "fb")
        if parts and (sum(len(x) for x in parts) + len(y)) / SR > BOOT_SEC:
            break
        parts.append(y); used.append(tag)
    out = np.concatenate([v for x in parts for v in (x, gap)][:-1])
    out = (out / (float(abs(out).max()) or 1.0) * 0.95).astype("float32")
    p = os.path.join(root, "ref_1gen.wav")
    sf.write(p, out, SR, subtype="PCM_16")
    print(f"  1세대 참조 {len(out)/SR:.1f}초, 조각 {len(parts)}개 {used}")
    return p, used


if __name__ == "__main__":
    key, rk = sys.argv[1], sys.argv[2]
    name, loader = LOADERS[key]
    stem, who = REFS[rk]
    root = os.path.join(OUT, f"{rk}_{who}", name)
    os.makedirs(root, exist_ok=True)
    ref_wav = f"{BACK}/{stem}.wav"
    ref_text = io.open(f"{BACK}/{stem}.txt", encoding="utf-8").read().strip()
    print(f"== {name} / 참조 {stem} ({who})\n   전사: {ref_text[:60]}", flush=True)
    f = loader()
    print("[1] 원본 참조", flush=True)
    d = synth_all(f, root, "A_원본참조", ref_wav, ref_text)
    print("[2] 1세대 참조 만들기", flush=True)
    ref1, used = build_1gen(d, root)
    print("[3] 1세대 참조", flush=True)
    synth_all(f, root, "B_1세대참조", ref1, " ".join(t for tag, t in SAY if tag in used))
    print("끝")
