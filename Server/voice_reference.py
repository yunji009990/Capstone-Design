"""Bounded, connection-independent reference recordings; originals are never edited."""
from __future__ import annotations

import io
import json
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

MAX_UPLOAD = 30 * 1024 * 1024
MAX_SECONDS = 12


@dataclass
class Reference:
    pcm: bytes
    sample_rate: int
    source: str
    original_seconds: float
    start_seconds: float
    text: str = ""
    reference_id: str = ""

    def details(self):
        duration = len(self.pcm) / (self.sample_rate * 2)
        cropped = self.start_seconds > 0 or self.original_seconds - duration > .02
        return {"reference_id": self.reference_id, "text": self.text, "source": self.source,
                "sample_rate": self.sample_rate, "duration_sec": round(duration, 3),
                "original_sec": round(self.original_seconds, 3),
                "start_sec": round(self.start_seconds, 3), "cropped": cropped,
                "notice": "최대 12초의 연속 구간을 사용합니다. 미리 듣고 전사를 확인하세요." if cropped else ""}

    def wav(self):
        out = io.BytesIO()
        with wave.open(out, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(self.sample_rate)
            wav.writeframes(self.pcm)
        return out.getvalue()


def decode_reference(raw, source):
    if len(raw) > MAX_UPLOAD:
        raise ValueError("참조 WAV는 30 MB 이하여야 합니다.")
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            rate, channels, frames = wav.getframerate(), wav.getnchannels(), wav.getnframes()
            if (wav.getcomptype() != "NONE" or wav.getsampwidth() != 2 or channels not in (1, 2)
                    or not 8000 <= rate <= 96000 or frames < rate * 3):
                raise ValueError("참조 음성은 3초 이상, 모노/스테레오 16-bit PCM WAV(8–96 kHz)여야 합니다.")
            pcm = wav.readframes(frames)
            if len(pcm) != frames * channels * 2:
                raise ValueError("참조 WAV가 손상되었습니다.")
    except (wave.Error, EOFError) as exc:
        raise ValueError("참조 음성을 16-bit PCM WAV로 저장해 주세요.") from exc
    samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32).reshape(-1, channels).mean(axis=1)
    hop = max(1, int(rate * .02))
    levels = np.sqrt(np.mean(samples[:len(samples) // hop * hop].reshape(-1, hop) ** 2, axis=1))
    if not len(levels) or float(levels.max()) < 40:
        raise ValueError("참조 음성에서 소리를 찾지 못했습니다. 한 사람이 말하는 녹음을 선택하세요.")
    start, end = 0, len(samples)
    if frames > rate * MAX_SECONDS:
        threshold = max(40., float(levels.max()) * .06)
        voiced = np.flatnonzero(levels > threshold)
        start = max(0, int(voiced[0]) * hop - int(rate * .1))
        end = min(len(samples), start + rate * MAX_SECONDS)
        # Prefer a quiet sentence boundary in the latter half of the clip. Keep
        # a contiguous span: deleting internal pauses would change its cadence.
        for i in range(end // hop - 8, (start + rate * 6) // hop, -1):
            if np.all(levels[i:i + 8] <= threshold):
                end = (i + 8) * hop
                break
    if end - start < rate * 3:
        raise ValueError("참조 음성의 발화 구간이 너무 짧습니다. 3–12초의 녹음을 선택하세요.")
    return Reference(np.clip(samples[start:end], -32768, 32767).astype("<i2").tobytes(),
                     rate, source, frames / rate, start / rate)


def session_recording(root, session=None):
    root = Path(root).resolve()
    if session is None:
        current = root / "current.json"
        if current.is_symlink():
            raise ValueError("잘못된 세션 경로입니다.")
        try:
            session = json.loads(current.read_text(encoding="utf-8")).get("session")
        except (OSError, ValueError):
            session = None
    if (not isinstance(session, str) or not session or len(session) > 100 or session.startswith(".")
            or any(c in session for c in "/\\\x00")):
        raise ValueError("등록된 참조 음성이 없습니다. 참조 음성 설정에서 WAV를 선택하세요.")
    folder = root / session
    path = folder / "voice.wav"
    if folder.is_symlink() or path.is_symlink() or path.resolve().parent.parent != root:
        raise ValueError("잘못된 참조 음성 경로입니다.")
    try:
        with path.open("rb") as wav:
            raw = wav.read(MAX_UPLOAD + 1)
    except OSError as exc:
        raise ValueError("이 세션에 참조 음성이 없습니다. 참조 WAV를 등록해 주세요.") from exc
    return raw, "등록 음성 · " + session


class ReferenceStore:
    """Test uploads expire after 30 minutes; at most eight bounded clips in RAM."""
    def __init__(self, ttl=1800, capacity=8, clock=time.monotonic):
        self.ttl, self.capacity, self.clock = ttl, capacity, clock
        self.items = {}

    def purge(self):
        now = self.clock()
        for key, (_, used) in list(self.items.items()):
            if now - used >= self.ttl:
                del self.items[key]

    def put(self, reference):
        self.purge()
        if len(self.items) >= self.capacity:
            raise ValueError("임시 참조 음성이 가득 찼습니다. 사용하지 않는 참조를 해제하고 다시 시도하세요.")
        reference.reference_id = uuid.uuid4().hex
        self.items[reference.reference_id] = reference, self.clock()
        return reference

    def get(self, key):
        self.purge()
        if not isinstance(key, str) or key not in self.items:
            raise ValueError("참조 음성이 만료되었습니다. 참조 음성 설정에서 다시 불러오세요.")
        reference, _ = self.items[key]
        self.items[key] = reference, self.clock()
        return reference

    def delete(self, key):
        self.items.pop(key, None)
