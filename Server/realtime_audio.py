"""Bounded PCM turn detection and a replaceable, CPU speech frontend."""
from __future__ import annotations

import asyncio
import math
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME_BYTES = 640  # mono PCM16, 20 ms


@dataclass
class Observation:
    text: str
    emotion: str = "unknown"
    audio_event: str = "unknown"
    language: str = "unknown"
    # SenseVoice exposes labels, not calibrated emotion probabilities.
    emotion_confidence: float | None = None
    prosody: dict = field(default_factory=dict)

    def metadata(self):
        return {k: v for k, v in asdict(self).items() if k != "text"}


class TurnDetector:
    """Frame decisions are independent of ASR and LLM latency.

    Short pauses stay in the same turn. Oversized turns are discarded until
    silence instead of answering a sentence cut in the middle.
    """

    def __init__(self, is_speech=None, silence_ms=700, max_seconds=30):
        if is_speech is None:
            import webrtcvad
            vad = webrtcvad.Vad(2)
            is_speech = lambda frame: vad.is_speech(frame, SAMPLE_RATE)
        self.is_speech = is_speech
        self.silence_frames = max(10, math.ceil(silence_ms / 20))
        self.max_bytes = int(max_seconds * SAMPLE_RATE * 2)
        self.pending = bytearray()
        self.pre_roll = deque(maxlen=15)
        self.votes = deque(maxlen=10)
        self.audio = bytearray()
        self.speaking = False
        self.discarding = False
        self.quiet = 0
        self.samples_seen = 0

    def reset(self):
        """Clear capture while preserving this connection's VAD settings."""
        self.pending.clear()
        self.pre_roll.clear()
        self.votes.clear()
        self.audio.clear()
        self.speaking = self.discarding = False
        self.quiet = self.samples_seen = 0

    def feed(self, pcm):
        if not pcm or len(pcm) % 2 or len(pcm) > SAMPLE_RATE:
            raise ValueError("audio must be 1–500 ms of mono PCM16 at 16000 Hz")
        self.pending.extend(pcm)
        events = []
        while len(self.pending) >= FRAME_BYTES:
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            self.samples_seen += FRAME_BYTES // 2
            voiced = bool(self.is_speech(frame))
            if self.discarding:
                self.quiet = 0 if voiced else self.quiet + 1
                if self.quiet >= self.silence_frames:
                    self.discarding = False
                    self.quiet = 0
                continue
            if not self.speaking:
                self.pre_roll.append(frame)
                self.votes.append(voiced)
                if voiced and sum(self.votes) >= 8:
                    self.speaking = True
                    self.quiet = 0
                    self.audio = bytearray(b"".join(self.pre_roll))
                    self.pre_roll.clear()
                    self.votes.clear()
                    events.append(("start", None))
                continue
            self.audio.extend(frame)
            self.quiet = 0 if voiced else self.quiet + 1
            if self.quiet >= self.silence_frames:
                # Keep 200 ms of tail, but do not send the entire endpoint wait.
                tail = max(0, self.quiet - 10) * FRAME_BYTES
                utterance = bytes(self.audio[:-tail] if tail else self.audio)
                self.audio.clear()
                self.speaking = False
                self.quiet = 0
                events.append(("end", utterance))
            elif len(self.audio) > self.max_bytes:
                self.audio.clear()
                self.speaking = False
                self.discarding = True
                events.append(("too_long", None))
        return events


class SenseVoiceFrontend:
    """Windowed ASR with provisional transcripts; this is not a causal ASR model.

    A single executor owns the recognizer. Cancelling a caller never starts a
    second concurrent inference on the same model or publishes a stale result.
    """

    def __init__(self, model_dir, num_threads=2):
        import sherpa_onnx
        root = Path(model_dir)
        model = root / "model.int8.onnx"
        tokens = root / "tokens.txt"
        if not model.is_file() or not tokens.is_file():
            raise FileNotFoundError(
                f"SenseVoice model missing in {root}; run setup_dialogue_models.py")
        self.recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(model), tokens=str(tokens), language="ko", use_itn=True,
            provider="cpu", num_threads=num_threads)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech")

    async def transcribe(self, pcm, sample_rate=SAMPLE_RATE):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self._decode, pcm, sample_rate)

    def _decode(self, pcm, sample_rate=SAMPLE_RATE):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        stream = self.recognizer.create_stream()
        # sherpa-onnx resamples references to the recognizer's rate internally.
        stream.accept_waveform(sample_rate, samples)
        self.recognizer.decode_stream(stream)
        result = stream.result

        def label(name):
            return str(getattr(result, name, "") or "unknown").replace("<|", "").replace("|>", "").lower()

        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        return Observation(
            text=result.text.strip(), emotion=label("emotion"),
            audio_event=label("event"), language=label("lang"),
            prosody={"duration_sec": round(len(samples) / sample_rate, 3),
                     "rms_dbfs": round(20 * math.log10(max(rms, 1e-6)), 1)})

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
