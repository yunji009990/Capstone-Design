"""
NeMo MSDD 화자분리 결과로 화자별 reference 클립을 만든다.

방식: 연속 7초 구간을 찾는 대신,
  - 각 화자의 '단일 화자 + 무클리핑' 깨끗한 조각들을 수집하고
  - 그중 깨끗한(긴/고SNR) 것들을 골라 이어붙여 7~12초 reference 를 구성한다.
빽빽한 대화라 연속 구간이 없어도, 깨끗한 조각만 조합해 만든다.

  - nemo_diarize.py 를 격리된 nemo_env 에서 subprocess 로 실행 -> diar.json
  - diar.json(프레임 단위 화자 구간 + overlap)에서 다른 화자·overlap 을 뺀 순수 구간만 사용
"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from extract_speaker_ref import (SR, decode_to_wav, estimate_snr_db,
                                 export_clip, _viz_payload_base)

# 기본은 이 파일 옆의 nemo_env. 1.8GB 라 저장소에는 안 들어가므로, 이미 만들어 둔
# 것이 딴 데 있으면 NEMO_PY 로 가리켜 복사를 피한다. 만드는 법은 README 참고.
NEMO_PY = Path(os.environ.get("NEMO_PY")
               or Path(__file__).resolve().parent / "nemo_env" / "Scripts" / "python.exe")
NEMO_SCRIPT = Path(__file__).resolve().parent / "nemo_diarize.py"
MIN_PIECE = 0.8          # 조각 최소 길이(초) — 너무 잘게 쪼개진 조각 제외
JOIN_SIL = 0.06          # 조각 사이 삽입 침묵(초)
FADE = 0.01              # 조각 경계 페이드(초) — 클릭음 방지


# --------------------------------------------------------------------------- #
def merge_intervals(ivs):
    out = []
    for s, e in sorted(ivs):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(a, b) for a, b in out]


def complement(ivs, lo, hi):
    free, cur = [], lo
    for s, e in merge_intervals(ivs):
        if s > cur:
            free.append((cur, min(s, hi)))
        cur = max(cur, e)
        if cur >= hi:
            break
    if cur < hi:
        free.append((cur, hi))
    return [(a, b) for a, b in free if b - a > 0]


def overlaps_of(segs):
    pts = []
    for s in segs:
        pts.append((s["start"], 1)); pts.append((s["end"], -1))
    pts.sort()
    ov, active, start = [], 0, None
    for t, d in pts:
        if active >= 2 and start is not None and t > start:
            ov.append((start, t))
        active += d
        start = t
    return merge_intervals(ov)


def clean_pieces(sp, segs, wav16, min_snr=6.0):
    """화자 sp 의 '다른 화자/overlap 이 전혀 없는' 깨끗한 조각들."""
    others = [(x["start"], x["end"]) for x in segs if x["spk"] != sp]
    mine = merge_intervals([(x["start"], x["end"]) for x in segs if x["spk"] == sp])
    pieces = []
    for a, b in mine:
        for cs, ce in complement(others, a, b):   # 다른 화자 시간 제거
            if ce - cs < MIN_PIECE:
                continue
            seg = wav16[int(cs * SR):int(ce * SR)]
            peak = float(np.max(np.abs(seg))) if len(seg) else 1.0
            snr = estimate_snr_db(seg)
            if peak < 0.98 and snr >= min_snr:    # 클리핑·저SNR 제외
                pieces.append(dict(start=round(cs, 3), end=round(ce, 3),
                                   dur=ce - cs, snr=snr, peak=peak))
    return pieces


def select_for_ref(pieces, min_len, max_len, target=10.0):
    """깨끗한 조각을 골라 목표 길이(~10초)로 조합. 이음 최소화를 위해 긴 조각 우선."""
    order = sorted(pieces, key=lambda p: -p["dur"])
    chosen, tot = [], 0.0
    for p in order:
        if tot >= target:
            break
        take = p
        if tot + p["dur"] > max_len:              # 마지막 조각은 필요한 만큼만
            room = max_len - tot
            if room < MIN_PIECE:
                continue
            take = {**p, "end": p["start"] + room, "dur": room}
        chosen.append(take)
        tot += take["dur"]
    if tot < min_len:                             # 깨끗한 조각이 부족하면 있는 대로
        chosen, tot = order[:], sum(p["dur"] for p in order)
    chosen.sort(key=lambda p: p["start"])         # 출력은 시간순
    return chosen, tot


def build_concat_ref(pieces, wav_hi, out_sr, dst):
    """선택 조각들을 페이드+짧은 침묵으로 이어붙여 저장."""
    sil = np.zeros(int(JOIN_SIL * out_sr), dtype=np.float32)
    fade = int(FADE * out_sr)
    parts = []
    for k, p in enumerate(pieces):
        seg = wav_hi[int(p["start"] * out_sr):int(p["end"] * out_sr)].copy()
        if len(seg) > 2 * fade:
            seg[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
            seg[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
        if k > 0:
            parts.append(sil)
        parts.append(seg)
    dst.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(dst), np.concatenate(parts) if parts else np.zeros(1, np.float32), out_sr)


# --------------------------------------------------------------------------- #
def diarize_with_nemo(src, work_dir, n_speakers, log, reuse=True):
    work_dir.mkdir(parents=True, exist_ok=True)
    diar_path = work_dir / "diar.json"
    if reuse and diar_path.exists():
        log("기존 NeMo 결과(diar.json) 재사용")
        return json.loads(diar_path.read_text(encoding="utf-8"))
    if not NEMO_PY.exists():
        raise RuntimeError(
            f"nemo_env 를 찾을 수 없습니다: {NEMO_PY}\n"
            "Web/extraction/README.md 를 보고 만들거나, 이미 있으면 "
            "환경변수 NEMO_PY 로 그 python.exe 를 가리키세요.")
    cmd = [str(NEMO_PY), str(NEMO_SCRIPT), "--input", str(src),
           "--out-dir", str(work_dir)]
    if n_speakers:
        cmd += ["--num-speakers", str(n_speakers)]
    log("NeMo MSDD 화자분리 실행 중… (VAD+다중스케일 임베딩+MSDD, 오디오 길이의 ~1/4 시간)")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError("NeMo 실행 실패:\n" + proc.stdout[-1500:] + proc.stderr[-1500:])
    return json.loads(diar_path.read_text(encoding="utf-8"))


def run_extraction_nemo(src, out_dir, min_len=7.0, max_len=12.0, out_sr=24000,
                        max_segments=6, n_speakers=None, viz=False,
                        reuse_diar=False, progress=None):
    def log(m):
        if progress:
            progress(m)

    src = Path(src); out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    diar = diarize_with_nemo(src, out_dir / "_nemo", n_speakers, log, reuse=reuse_diar)
    segs = diar["segments"]
    if not segs:
        raise RuntimeError("NeMo 가 발화 구간을 찾지 못했습니다.")

    log("오디오 디코딩 중…")
    with tempfile.TemporaryDirectory() as td:
        wav16 = decode_to_wav(src, SR, Path(td))
        wav_hi = decode_to_wav(src, out_sr, Path(td))
    total = len(wav16) / SR

    spk_ids = diar["speakers"]
    spk_speech = {sp: sum(s["end"] - s["start"] for s in segs if s["spk"] == sp)
                  for sp in spk_ids}
    ranked_spk = sorted(spk_ids, key=lambda sp: -spk_speech[sp])

    log("깨끗한 단일화자 조각 수집·조합 중…")
    per_spk = {}
    for sp in spk_ids:
        pieces = clean_pieces(sp, segs, wav16)
        clean_total = sum(p["dur"] for p in pieces)
        if clean_total < min_len * 0.6:           # 쓸 만한 깨끗한 조각이 거의 없음
            continue
        chosen, tot = select_for_ref(pieces, min_len, max_len)
        per_spk[sp] = dict(pieces=pieces, chosen=chosen, dur=tot)
    if not per_spk:
        raise RuntimeError("깨끗한 단일화자 조각을 충분히 찾지 못했습니다.")

    log("클립 저장 중…")
    ranked = sorted(per_spk, key=lambda sp: -per_spk[sp]["dur"])
    speakers = []
    for rank, sp in enumerate(ranked, 1):
        spk_id = f"talker{rank}"
        info = per_spk[sp]
        chosen = info["chosen"]
        ref_rel = f"{spk_id}/ref.wav"
        build_concat_ref(chosen, wav_hi, out_sr, out_dir / ref_rel)

        avg_snr = float(np.mean([p["snr"] for p in chosen]))
        max_peak = float(max(p["peak"] for p in chosen))
        # 대표 조각(듣기용): 긴 순 상위 N개, 시간순
        reps = sorted(info["pieces"], key=lambda p: -p["dur"])[:max_segments]
        reps.sort(key=lambda p: p["start"])
        seg_list = []
        for j, p in enumerate(reps, 1):
            seg_rel = f"{spk_id}/seg{j:02d}.wav"
            export_clip(src, p["start"], min(p["end"], p["start"] + 15), out_dir / seg_rel, out_sr)
            seg_list.append(dict(file=seg_rel, start=round(p["start"], 2),
                                 end=round(p["end"], 2), dur=round(p["dur"], 2)))

        speakers.append(dict(
            spk_id=spk_id, rank=rank, cluster=sp,
            total_speech_sec=round(spk_speech[sp]),
            ref=dict(file=ref_rel, dur=round(info["dur"], 2),
                     n_pieces=len(chosen), snr_db=round(avg_snr, 1),
                     peak=round(max_peak, 3),
                     pieces=[dict(start=round(p["start"], 2), end=round(p["end"], 2))
                             for p in chosen]),
            segments=seg_list,
        ))

    speech = sum(e - s for s, e in merge_intervals(
        [(s["start"], s["end"]) for s in segs]))
    result = dict(
        input=src.name, duration=round(total, 1), engine="NeMo MSDD",
        speech_sec=round(speech), speech_pct=round(speech / total * 100),
        n_speakers=diar["n_speakers"], speaker_mode=("지정" if n_speakers else "자동 추정"),
        speakers=speakers,
    )
    if viz:
        log("시각화 데이터 생성 중…")
        v = _viz_payload_base(wav_hi, total, src, out_dir)
        v["color_idx"] = {sp: i for i, sp in enumerate(ranked_spk)}
        v["segments"] = [dict(start=s["start"], end=s["end"], spk=s["spk"]) for s in segs]
        v["overlaps"] = [dict(start=round(a, 2), end=round(b, 2))
                         for a, b in overlaps_of(segs)]
        v["runs"] = [dict(spk=sp, start=round(p["start"], 2), end=round(p["end"], 2),
                          dur=round(p["dur"], 2))
                     for sp in per_spk for p in per_spk[sp]["chosen"]]
        result["viz"] = v

    with open(out_dir / "result.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    log("완료")
    return result


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="test_audio.mp3")
    ap.add_argument("--out-dir", default="output_nemo")
    ap.add_argument("--speakers", type=int, default=None)
    ap.add_argument("--reuse", action="store_true", help="기존 diar.json 재사용")
    a = ap.parse_args()
    r = run_extraction_nemo(a.input, a.out_dir, n_speakers=a.speakers,
                            reuse_diar=a.reuse, progress=lambda m: print("  " + m))
    print(f"\n엔진 {r['engine']} · 화자 {r['n_speakers']}명")
    for sp in r["speakers"]:
        x = sp["ref"]
        print(f"  #{sp['rank']} {sp['spk_id']} ({sp['cluster']}) ref {x['dur']}s "
              f"= {x['n_pieces']}조각 이어붙임, snr={x['snr_db']}dB 대표{len(sp['segments'])}")
