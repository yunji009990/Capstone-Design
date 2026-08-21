"""NeMo 추출용 공용 헬퍼 (ECAPA 엔진 제거됨).

원래 이 파일은 ECAPA(VAD+ECAPA+KMeans) 엔진 전체를 포함했으나, 통합 앱은 NeMo MSDD
엔진만 사용하므로 ECAPA 파이프라인은 제거했다. extract_nemo.py 가 import 하는 공용
유틸리티(오디오 디코드·SNR 추정·클립 추출·시각화 기반)만 남긴다.

제거된 ECAPA 기능 요약은 docs/제거된_기능.md 참고.
"""
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

SR = 16000                 # 분석용 샘플레이트


def decode_to_wav(src: Path, sr: int, tmp_dir: Path) -> np.ndarray:
    out = tmp_dir / f"decoded_{sr}.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(src),
         "-ac", "1", "-ar", str(sr), "-f", "wav", str(out)],
        check=True,
    )
    wav, got_sr = sf.read(str(out), dtype="float32")
    assert got_sr == sr
    return wav


def estimate_snr_db(seg):
    frame = 400
    if len(seg) < frame * 4:
        return 0.0
    n = len(seg) // frame
    e = np.array([np.mean(seg[i * frame:(i + 1) * frame] ** 2) for i in range(n)])
    e = e[e > 0]
    if len(e) < 4:
        return 0.0
    noise, speech = np.percentile(e, 10), np.percentile(e, 90)
    return 40.0 if noise <= 0 else float(10 * np.log10(speech / noise))


def export_clip(src, start, end, dst, out_sr):
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
         "-ac", "1", "-ar", str(out_sr), str(dst)],
        check=True,
    )


def _viz_payload_base(wav16, total, src, out_dir):
    """엔진 공용 시각화 기반: 파형 피크 + 전체 재생 오디오(full.mp3)."""
    n_pk = 900
    step = max(1, len(wav16) // n_pk)
    peaks = [float(np.max(np.abs(wav16[i * step:(i + 1) * step])))
             for i in range(min(n_pk, len(wav16) // step))]
    mx = max(peaks) or 1.0
    peaks = [round(p / mx, 3) for p in peaks]
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(src),
                    "-ac", "1", "-b:a", "64k", str(out_dir / "full.mp3")], check=True)
    return dict(duration=round(total, 2), peaks=peaks, full_audio="full.mp3")
