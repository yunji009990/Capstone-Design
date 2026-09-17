"""Bounded PCM turn detection and a replaceable speech frontend.

운영 STT 는 Whisper large-v3(faster-whisper, GPU)다. 기존 SenseVoiceSmall(CPU)
구현은 명시적인 되돌리기 경로로만 남긴다. 자동 전환이나 대체 실행은 없다.
"""
from __future__ import annotations

import asyncio
import io
import math
import wave
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
FRAME_BYTES = 640  # mono PCM16, 20 ms
FRAME_SECONDS = FRAME_BYTES / 2 / SAMPLE_RATE
# 말끝 보완이 검토하는 최근 오디오 길이. speech_gate 의 ENDPOINT_WINDOW_SECONDS 와 같다.
# 이 모듈은 검증기를 import 하지 않으므로 값을 여기에 둔다. 상한이 있는 고정 버퍼다.
RECENT_SECONDS = 2.0
# 말끝 뒤에 남기는 꼬리. feed 의 기존 200 ms(quiet-10 프레임)와 같은 값이다.
KEEP_TAIL_SECONDS = 0.2
# 참조 WAV 는 8–96 kHz 로 들어온다(voice_reference.decode_reference).
MIN_INPUT_RATE, MAX_INPUT_RATE = 8000, 96000
STT_BACKENDS = ("whisper", "sensevoice")
WHISPER_DEVICES = ("cuda", "cpu")
WHISPER_COMPUTE_TYPES = ("float16", "int8_float16", "bfloat16", "float32", "int8")
# faster-whisper 가 세그먼트마다 실제로 주는 숫자. 이름 그대로 옮기기만 한다.
# 어느 것도 "맞았을 확률"이 아니다. avg_logprob 는 토큰 로그확률의 평균,
# no_speech_prob 는 모델이 <|nospeech|> 토큰에 준 값, compression_ratio 는
# 전사 문자열의 zlib 압축비다. 확률 보장으로 읽지 않는다.
QUALITY_FIELDS = ("avg_logprob", "no_speech_prob", "compression_ratio")


@dataclass
class Observation:
    text: str
    audio_event: str = "unknown"
    language: str = "unknown"
    prosody: dict = field(default_factory=dict)
    # 서버 내부 판정 전용 자료다. 세그먼트별 품질 지표 목록이고 비어 있으면 미측정이다.
    # metadata() 에서 명시적으로 빼므로 LLM 프롬프트·자막·Unity 로 나가지 않는다.
    quality: list = field(default_factory=list)

    def metadata(self):
        # 기존 계약 그대로 audio_event·language·prosody 만 남긴다.
        return {k: v for k, v in asdict(self).items() if k not in ("text", "quality")}


class TurnDetector:
    """Frame decisions are independent of ASR and LLM latency.

    Short pauses stay in the same turn. Oversized turns are discarded until
    silence instead of answering a sentence cut in the middle.

    이 프레임 판정(WebRTC VAD)은 연속 잡음을 speech 로 통과시킬 수 있고, 그때는
    voiced 프레임 하나가 계속 quiet 를 0으로 되돌려 말끝(`end`)이 나오지 않는다.
    이 클래스는 그 교착을 스스로 풀지 않는다. 판정 근거가 없기 때문이다. 대신
    최근 오디오(`recent_audio`)와 말끝 진입점(`endpoint`)을 제공하고, 실제
    비음성 근거는 호출자가 별도 검증기(Silero)로 확인해서 넘긴다.
    """

    def __init__(self, is_speech=None, silence_ms=700, max_seconds=30,
                 recent_seconds=RECENT_SECONDS):
        if is_speech is None:
            import webrtcvad
            vad = webrtcvad.Vad(2)
            is_speech = lambda frame: vad.is_speech(frame, SAMPLE_RATE)
        self.is_speech = is_speech
        self.silence_frames = max(10, math.ceil(silence_ms / 20))
        self.silence_seconds = self.silence_frames * FRAME_SECONDS
        self.max_bytes = int(max_seconds * SAMPLE_RATE * 2)
        self.pending = bytearray()
        self.pre_roll = deque(maxlen=15)
        self.votes = deque(maxlen=10)
        self.audio = bytearray()
        # 상태와 무관하게 최근 오디오만 고정 길이로 들고 있는다. 말끝 보완과
        # 길이 상한 폐기 재무장이 같은 창을 본다. 원본을 파일로 남기지 않는다.
        self.recent = deque(maxlen=max(1, round(recent_seconds / FRAME_SECONDS)))
        self.speaking = False
        self.discarding = False
        self.quiet = 0
        self.samples_seen = 0
        # 낡은 비동기 결과가 새 후보를 자르지 못하게 하는 식별값이다.
        self.segment_seq = 0
        self.opened_samples = 0
        self.segment_frames = 0
        self.voiced_frames = 0
        self.max_quiet = 0

    def reset(self):
        """Clear capture while preserving this connection's VAD settings."""
        self.pending.clear()
        self.pre_roll.clear()
        self.votes.clear()
        self.audio.clear()
        self.recent.clear()
        self.speaking = self.discarding = False
        self.quiet = self.samples_seen = 0
        self._new_segment()

    def _new_segment(self):
        """구간 식별값을 올리고 진단 계수를 되돌린다."""
        self.segment_seq += 1
        self.opened_samples = self.samples_seen
        self.segment_frames = self.voiced_frames = self.max_quiet = 0

    def _observe(self, voiced):
        """현재 구간의 WebRTC 판정을 센다. 진단 숫자일 뿐 판정에는 쓰지 않는다."""
        self.segment_frames += 1
        if voiced:
            self.voiced_frames += 1
        if self.quiet > self.max_quiet:
            self.max_quiet = self.quiet

    @property
    def open(self):
        """후보를 들고 있는 상태인가. 길이 상한 폐기 대기도 열린 상태로 본다."""
        return self.speaking or self.discarding

    def open_seconds(self):
        return (self.samples_seen - self.opened_samples) / SAMPLE_RATE if self.open else 0.0

    def stats(self):
        """지금 구간의 숫자. 원문·오디오는 담지 않는다."""
        frames = self.segment_frames
        return {"state": "speaking" if self.speaking else "discarding" if self.discarding else "idle",
                "segment": self.segment_seq,
                "age_sec": self.open_seconds(),
                "voiced_ratio": (self.voiced_frames / frames) if frames else 0.0,
                "max_quiet_sec": self.max_quiet * FRAME_SECONDS}

    def recent_audio(self):
        """최근 고정 길이 오디오. 상한은 recent 의 maxlen 이 강제한다."""
        return b"".join(self.recent)

    def feed(self, pcm):
        if not pcm or len(pcm) % 2 or len(pcm) > SAMPLE_RATE:
            raise ValueError("audio must be 1–500 ms of mono PCM16 at 16000 Hz")
        self.pending.extend(pcm)
        events = []
        while len(self.pending) >= FRAME_BYTES:
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            self.samples_seen += FRAME_BYTES // 2
            self.recent.append(frame)
            voiced = bool(self.is_speech(frame))
            if self.discarding:
                self.quiet = 0 if voiced else self.quiet + 1
                self._observe(voiced)
                if self.quiet >= self.silence_frames:
                    self.discarding = False
                    self.quiet = 0
                    self._new_segment()
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
                    self._new_segment()
                    events.append(("start", None))
                continue
            self.audio.extend(frame)
            self.quiet = 0 if voiced else self.quiet + 1
            self._observe(voiced)
            if self.quiet >= self.silence_frames:
                # Keep 200 ms of tail, but do not send the entire endpoint wait.
                tail = max(0, self.quiet - 10) * FRAME_BYTES
                utterance = bytes(self.audio[:-tail] if tail else self.audio)
                self.audio.clear()
                self.speaking = False
                self.quiet = 0
                self._new_segment()
                events.append(("end", utterance))
            elif len(self.audio) > self.max_bytes:
                self.audio.clear()
                self.speaking = False
                self.discarding = True
                self._new_segment()
                events.append(("too_long", None))
        return events

    def endpoint(self, window_end_samples, tail_seconds):
        """확인된 비음성 꼬리로 말끝을 만들거나 길이 상한 폐기를 재무장한다.

        tail_seconds 는 창의 끝(window_end_samples)을 기준으로 **다른 검증기가
        실제로 잰** 비음성 길이다. 이 값이 기존 silence_ms 기준에 못 미치면
        아무것도 하지 않는다. 길이나 시간만으로 발화를 끊지 않는다.

        창을 만든 뒤 오디오가 한 프레임이라도 더 들어왔다면 아무것도 하지 않는다.
        그 구간은 아직 아무도 검사하지 않았고, 짧은 새 발화가 거기서 시작했을 수
        있다. 그때는 호출자가 최신 창으로 다시 재는 쪽이 옳다.
        feed 와 같은 모양의 사건 목록을 돌려준다.
        """
        if self.samples_seen != window_end_samples:
            return []
        if tail_seconds + .001 < self.silence_seconds:
            return []
        if self.discarding:
            self.discarding = False
            self.quiet = 0
            self._new_segment()
            return [("rearmed", None)]
        if not self.speaking:
            return []
        cut = max(0, len(self.audio)
                  - int(max(0.0, tail_seconds - KEEP_TAIL_SECONDS) * SAMPLE_RATE) * 2)
        utterance = bytes(self.audio[:cut])
        self.audio.clear()
        self.speaking = False
        self.quiet = 0
        self._new_segment()
        return [("end", utterance)]


def prepare_audio(pcm, sample_rate):
    """원 PCM 과 16 kHz 모델 입력을 함께 돌려준다.

    참조 음성은 8–96 kHz 로 들어오는데 Whisper 입력은 16 kHz 고정이다. 레이트
    숫자만 16000 으로 바꾸면 재생 속도가 달라진 소리를 전사하게 되므로, 다른
    레이트는 메모리 WAV 로 감싸 faster-whisper 의 디코더(PyAV)로 실제 리샘플한다.
    길이·세기 지표는 리샘플 결과가 아니라 원 PCM 으로 계산한다.
    """
    if len(pcm) % 2:
        raise ValueError("audio must be mono PCM16 (even byte length)")
    # 정수 Hz 만 받는다. 문자열이나 소수를 조용히 바꾸면 길이 지표와 모델 입력이
    # 서로 다른 레이트를 기준으로 계산될 수 있다.
    if isinstance(sample_rate, bool) or not isinstance(sample_rate, int):
        raise ValueError("sample rate must be an integer number of Hz")
    if not MIN_INPUT_RATE <= sample_rate <= MAX_INPUT_RATE:
        raise ValueError(f"sample rate must be {MIN_INPUT_RATE}–{MAX_INPUT_RATE} Hz")
    samples = np.frombuffer(bytes(pcm), dtype="<i2").astype(np.float32) / 32768.0
    if not len(samples) or sample_rate == SAMPLE_RATE:
        return samples, samples
    from faster_whisper.audio import decode_audio
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(bytes(pcm))
    buffer.seek(0)
    return samples, np.asarray(decode_audio(buffer, sampling_rate=SAMPLE_RATE), dtype=np.float32)


def quality_number(value):
    """모델이 준 유한한 수만 남긴다. 없거나 NaN·inf·잘못된 타입이면 None 이다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def segment_quality(segment):
    """세그먼트 하나의 품질 지표. 재지 못한 항목은 아예 담지 않는다.

    chars 는 그 세그먼트에 표기 내용이 있었는지 가르는 길이다. 원문은 담지 않는다.
    """
    values = {name: quality_number(getattr(segment, name, None)) for name in QUALITY_FIELDS}
    text = getattr(segment, "text", "")
    values["chars"] = len(text.strip()) if isinstance(text, str) else 0
    return {name: value for name, value in values.items() if value is not None}


class WhisperFrontend:
    """운영 STT. faster-whisper large-v3 를 단일 실행기가 독점한다.

    모델 하나와 max_workers=1 실행기로 추론과 세그먼트 생성기 소비를 모두
    직렬화한다. faster-whisper 의 transcribe 는 지연 생성기를 돌려주므로 호출
    스레드에서 끝까지 소비해야 다음 호출의 GPU 추론과 겹치지 않는다. 호출자가
    취소돼도 실행기에 올라간 작업은 그대로 끝나고, 그 뒤에 다음 작업이 시작된다.
    """

    def __init__(self, model_dir, *, device="cuda", compute_type="float16", warmup=True):
        if device not in WHISPER_DEVICES:
            raise ValueError(f"DIALOGUE_STT_DEVICE must be one of {WHISPER_DEVICES}, got {device!r}")
        if compute_type not in WHISPER_COMPUTE_TYPES:
            raise ValueError(
                f"DIALOGUE_STT_COMPUTE_TYPE must be one of {WHISPER_COMPUTE_TYPES}, got {compute_type!r}")
        root = Path(model_dir)
        # 로컬 폴더만 허용한다. 시작 중에 모델을 내려받는 숨은 동작을 두지 않는다.
        missing = [name for name in ("model.bin", "config.json") if not (root / name).is_file()]
        if not root.is_dir() or missing:
            raise FileNotFoundError(
                f"Whisper model missing in {root} ({', '.join(missing) or 'no such directory'}); "
                "run setup_dialogue_models.py --whisper")
        from faster_whisper import WhisperModel
        self.model_dir, self.device, self.compute_type = root, device, compute_type
        self.display_name = f"faster-whisper {root.name} {device}/{compute_type}"
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech")
        try:
            self.model = WhisperModel(str(root), device=device, compute_type=compute_type,
                                      local_files_only=True)
            if warmup:
                # CUDA 네이티브 오류는 첫 추론에서야 드러난다. health 가 ready 를
                # 말하기 전에, 실제로 추론할 그 스레드에서 확인한다.
                self.executor.submit(self._warmup).result()
        except BaseException:
            self.executor.shutdown(wait=True, cancel_futures=True)
            self.model = None
            raise

    def _warmup(self):
        # 예열 결과는 읽지 않고 버린다. 어떤 대화·자막·기억에도 들어가지 않는다.
        segments, _ = self._infer(np.zeros(SAMPLE_RATE // 2, dtype=np.float32))
        for _ in segments:
            pass

    def _infer(self, audio):
        # 실측으로 정한 고정 설정이다. 한국어를 강제하고 타임스탬프는 쓰지 않는다.
        return self.model.transcribe(
            audio, language="ko", beam_size=5, temperature=0,
            condition_on_previous_text=False, vad_filter=False, without_timestamps=True)

    async def transcribe(self, pcm, sample_rate=SAMPLE_RATE):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self._decode, pcm, sample_rate)

    def _decode(self, pcm, sample_rate=SAMPLE_RATE):
        samples, audio = prepare_audio(pcm, sample_rate)
        text, quality = "", []
        if len(audio):
            segments, _ = self._infer(audio)
            # 생성기를 이 스레드에서 딱 한 번 끝까지 소비한다. 전사와 품질 지표를
            # 같은 순회에서 모으므로 디코드가 두 번 돌지 않는다. 반환 뒤에는
            # 추론이 남지 않는다.
            parts = []
            for segment in segments:
                parts.append(segment.text)
                quality.append(segment_quality(segment))
            text = "".join(parts).strip()
        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        return Observation(
            text=text,
            # Whisper 는 소리 이벤트를 주지 않는다. 지어내지 않고 unknown 으로 둔다.
            audio_event="unknown",
            # 요청에서 한국어를 강제했으므로 모델의 언어 판정 결과가 아니다.
            language="ko",
            prosody={"duration_sec": round(len(samples) / sample_rate, 3),
                     "rms_dbfs": round(20 * math.log10(max(rms, 1e-6)), 1)},
            # 서버 내부 판정 전용. metadata() 가 빼므로 밖으로 나가지 않는다.
            quality=quality)

    def close(self):
        # 진행 중인 작업이 끝난 뒤에 GPU 모델을 놓는다.
        self.executor.shutdown(wait=True, cancel_futures=True)
        self.model = None


class SenseVoiceFrontend:
    """Windowed ASR with provisional transcripts; this is not a causal ASR model.

    A single executor owns the recognizer. Cancelling a caller never starts a
    second concurrent inference on the same model or publishes a stale result.
    """

    def __init__(self, model_dir, num_threads=2):
        import sherpa_onnx
        self.display_name = "SenseVoiceSmall CPU"
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
            text=result.text.strip(),
            audio_event=label("event"), language=label("lang"),
            prosody={"duration_sec": round(len(samples) / sample_rate, 3),
                     "rms_dbfs": round(20 * math.log10(max(rms, 1e-6)), 1)})

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)


def create_frontend(backend, model_dir, *, device="cuda", compute_type="float16"):
    """설정이 고른 STT 를 만든다. 실패를 다른 백엔드로 대신하지 않는다."""
    if backend == "whisper":
        return WhisperFrontend(model_dir, device=device, compute_type=compute_type)
    if backend == "sensevoice":
        return SenseVoiceFrontend(model_dir)
    raise ValueError(
        f"DIALOGUE_STT_BACKEND must be one of {STT_BACKENDS}, got {backend!r}")


def frontend_name(frontend):
    """health 가 실제로 쓰는 frontend 를 말하게 한다. 고정 라벨을 쓰지 않는다."""
    return getattr(frontend, "display_name", None) or "injected"
