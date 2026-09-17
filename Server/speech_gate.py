"""입력 후보가 실제 사람 말인지 확인하는 검증기.

WebRTC VAD(2)와 단순 에너지는 실측에서 hum(-30 dBFS)·white(-18 dBFS)를 모두
speech 로 통과시켰고 SenseVoice 는 그 잡음을 "그." 로 전사했다. 여기서는 별도의
CPU Silero VAD 로 음성 근거를 재확인하고, 전사에 표기 문자가 있는지를 함께 본다.
어휘 목록이나 길이 기준은 쓰지 않는다. 짧은 정상 단답을 버리면 안 되기 때문이다.

같은 모델로 **말끝 보완**도 한다(`tail_silence`). WebRTC 가 연속 잡음을 speech 로
보면 700 ms 연속 무음이 영영 오지 않아 후보가 열린 채 굳고, 그 뒤로는 전사도 답변도
없다. 그 교착만 풀기 위해 최근 창의 비음성 꼬리 길이를 재서 돌려준다. 채택 판단은
바뀌지 않는다. 말끝이 만들어진 뒤에도 기존 전체 검증과 전사 유효성 검사를 그대로 거친다.

여기에 **약한 전사 거절**(`weak_transcript`)이 더해진다. 음향 검증을 통과하고 표기
문자까지 있는데도 Whisper 자신의 세그먼트 지표가 비음성을 가리키는 경우만 버린다.
오디오를 보지 않는 순수 함수이고, 길이·어휘 목록·RMS 기준을 쓰지 않는다.
텍스트 모양(같은 음절 반복 등)으로는 아무것도 거절하지 않는다. 마이크 점검이나
음절 연습도 정당한 발화이기 때문이다.
"""
from __future__ import annotations

import asyncio
import math
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
GATE_VERSION = "verified_input_v1"

# 말끝 보완(endpoint supplement). 채택 정책이 아니라 **말끝 판정의 보조 근거**다.
# WebRTC 가 연속 잡음을 speech 로 보아 700 ms 연속 무음을 못 얻는 교착에서만 쓰인다.
# 여기서 쓰는 모델·실행기는 기존 검증기와 같다. 새 GPU·LLM 호출을 만들지 않는다.
ENDPOINT_VERSION = "silero_tail_v1"
ENDPOINT_WINDOW_SECONDS = 2.0      # 검토하는 최근 오디오 길이(고정 상한)
ENDPOINT_MIN_OPEN_SECONDS = 2.0    # 후보가 이만큼 열려 있어야 검토를 시작한다
ENDPOINT_CHECK_SECONDS = 0.4       # 검토 간격(입력 오디오 시간 기준)
ENDPOINT_LOG_SECONDS = 2.0         # 변화가 없을 때 진단을 남기는 간격
# 결과가 돌아왔을 때 오디오가 한 프레임이라도 더 들어왔으면 그 결과는 쓰지 않는다.
# 별도 허용 폭을 두지 않는다. 그 사이에 시작한 짧은 발화를 잃을 수 있기 때문이다.

# 모델이 붙이는 제어 태그는 전사 내용으로 세지 않는다.
_TAGS = re.compile(r"<\|[^|>]*\|>|</?[A-Za-z][\w:-]*>")

# --- 약한 전사 거절(weak transcript) ---
# 잡음이 문자로 전사돼 답변을 시작하거나 기존 답변을 보류하는 증상만 막는다.
#
# 임계값 근거: 메인이 2026-09-17 에 운영과 같은 GPU Whisper large-v3 float16 옵션과
# 실제 Silero 로 공개·합성 자료의 56 가지 입력 조건(같은 원본에 잡음·감쇠 변형을
# 더한 조건을 포함한다) ×3 회 = 168 건을 잰 결과
# (tools/_work/input_style_fix_20260917/asr-quality.json).
#   - 기존 gate 를 통과한 잡음 15 건(white·clicks·rustle -35 dBFS)의 지표는
#     no_speech_prob .781–.862, avg_logprob -.577–-.466 이었다.
#   - 정상 짧은 맞장구 "으음" 은 -.835/.603 으로 avg_logprob 가 잡음보다 더 낮다.
#     그래서 avg_logprob 만으로는 가를 수 없고, 실제로 가르는 축은 no_speech_prob 다.
#   - 다른 정상 단답은 숫자 "3" -.292/.585, 이름 "민수" -.100/.337,
#     "잠깐" -.504/.059 이고 공개 긴 발화·SNR 15/20 조건은 no_speech .106 이하였다.
#   - 실제 PCM 을 WebRTC→말끝→Silero→Whisper 로 통과시키면 같은 맞장구라도 잘린
#     후보 구간에서 값이 달라진다. "으음" 은 전체 클립에서 .603 이었지만 실제로
#     잘린 입력에서는 -0.791015625/0.7021484375 였다
#     (tools/_work/input_style_fix_20260917/short-candidate-probe.json).
#     no_speech 기준이 .7 이면 이 정상 맞장구를 3/3 거절한다. 다른 정상 단답은
#     같은 경로에서 .635 이하였으며 잡음 범위는 위 전체 클립 측정값이다.
# 아래 조합은 그 표본에서 잡음 15 건을 모두 걸러 내고 정상 지표를 모두 남긴다.
# 서로 다른 원본 56 개가 아니라 56 가지 입력 조건을 잰 작은 표본이므로 일반화를
# 보장하지 않는다. 미사용 자료와 운영 로그(input.verified 의
# no_speech_pct·avg_logprob_x100 분포)로 다시 본다.
WEAK_VERSION = "weak_transcript_v1"
# 잘린 후보의 정상 맞장구(.7021)와 잡음 하한(.781) 사이에 둔다. 정상 쪽에 가깝게
# 잡으면 다시 맞장구를 버리므로 기준을 높였다. 두 관측 범위 사이가 약 .08 뿐이라
# 이 값은 운영 로그의 input.verified 분포로 계속 확인한다.
WEAK_NO_SPEECH = .75
WEAK_AVG_LOGPROB = -.35
# compression_ratio 는 이 단계에서 거절에 쓰지 않는다. 진단으로만 모아 둔다.


def meaningful(text):
    """표기 문자(문자·숫자)가 하나라도 있는지만 본다.

    "." "…" "?" " " "<|nospeech|>" 는 거짓, "네" "응" "3" "박" 은 참이다.
    길이나 어휘로 판단하지 않으므로 정상 단답은 그대로 통과한다.
    """
    if not isinstance(text, str):
        return False
    return any(unicodedata.category(char)[0] in ("L", "N")
               for char in _TAGS.sub(" ", text))


def _finite(value):
    """유한한 수만 남긴다. realtime_audio.quality_number 와 같은 규칙이다.

    두 모듈은 서로를 import 하지 않는다. 규칙을 바꿀 때는 양쪽을 함께 본다.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _probability(value):
    """0–1 밖의 값은 모델이 준 확률로 보지 않는다. 미측정과 같게 다룬다."""
    number = _finite(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _logprob(value):
    """로그확률은 0 보다 클 수 없다. 범위를 벗어나면 미측정으로 본다."""
    number = _finite(value)
    return number if number is not None and number <= 0.0 else None


def _count(value):
    """글자 수는 비음수 정수뿐이다. 음수·소수·bool·문자열은 미측정으로 본다."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _segments(quality):
    """순회할 수 있는 세그먼트 목록만 돌려준다. 그 밖의 모양은 빈 목록이다.

    dict·str·bytes 는 그 자체로 순회되지만 세그먼트 목록이 아니다. 모양이 어긋난
    자료를 억지로 읽어 판단 근거로 삼지 않는다.
    """
    if quality is None or isinstance(quality, (str, bytes, dict)):
        return []
    try:
        return list(quality)
    except TypeError:
        return []


def weak_transcript(text, quality=None):
    """채택 직전의 순수 검사. 거절 이유 코드나 빈 문자열을 돌려준다.

    세그먼트끼리 값을 섞지 않는다. 같은 세그먼트 안에서 두 지표를 함께 보고,
    **표기 내용이 있는 모든 세그먼트가 약할 때만** 후보 전체를 거절한다.
    정상 세그먼트가 하나라도 있으면 보존한다. 한 세그먼트라도 지표가 없거나
    범위를 벗어나면 그 세그먼트는 unknown 이고, 모르는 것을 근거로 버리지 않으므로
    역시 보존한다. 글자 수가 0 인 공백 세그먼트만 근거가 아니라고 보고 건너뛴다.
    글자 수 자체가 미측정(음수·소수·bool·문자열·없음)이면 그 세그먼트도 unknown 이다.

    지표가 아예 없으면(SenseVoice·모의 frontend·빈 오디오) 종전대로 통과한다.
    오디오를 보지 않으므로 에코(실제 말소리)는 여기서 걸러지지 않는다.
    """
    if not isinstance(text, str):
        return ""
    weak = 0
    for segment in _segments(quality):
        if not isinstance(segment, dict):
            return ""           # 모양을 모르는 자료는 근거로 쓰지 않는다
        chars = _count(segment.get("chars"))
        if chars is None:
            return ""           # 글자 수를 모르면 보수적으로 보존한다
        if chars == 0:
            continue            # 공백뿐인 세그먼트
        no_speech = _probability(segment.get("no_speech_prob"))
        logprob = _logprob(segment.get("avg_logprob"))
        if no_speech is None or logprob is None:
            return ""           # 이 세그먼트는 unknown
        if no_speech < WEAK_NO_SPEECH or logprob > WEAK_AVG_LOGPROB:
            return ""           # 정상 세그먼트가 있다
        weak += 1
    return "weak_text" if weak else ""


def quality_summary(quality=None):
    """진단에 남길 집계. 채택과 거절 양쪽에 같은 모양으로 남긴다.

    표기 내용이 있는 세그먼트만 센다. no_speech_pct 는 최댓값, avg_logprob_x100 은
    최솟값, compression_x100 은 최댓값이라 **가장 약한 세그먼트 쪽 값**이다. 평균이
    아니므로 좋은 세그먼트에 가려지지 않는다. weak_segments 는 두 지표가 함께 약한
    세그먼트 수일 뿐 최종 판정이 아니다. 글자 수를 알 수 없는 세그먼트는 여기서
    아예 세지 않으므로, weak_segments 와 text_segments 가 같아도 weak_transcript 는
    그 unknown 세그먼트 때문에 보존을 택할 수 있다. 실제 판정은 input.rejected 의
    reason=weak_text 줄로만 읽는다. 집계 숫자로 대신하지 않는다.
    재지 못한 항목은 키 자체를 넣지 않는다. 0 으로 채우지 않는다.
    x100 은 진단의 소수 첫째 자리 반올림에서 해상도를 잃지 않으려고 곱한 값이다.
    원문·오디오는 담지 않는다.
    """
    counted = weak = 0
    no_speech = logprob = compression = None
    for segment in _segments(quality):
        if not isinstance(segment, dict):
            continue
        chars = _count(segment.get("chars"))
        if chars is None or chars == 0:
            continue
        counted += 1
        found = _probability(segment.get("no_speech_prob"))
        low = _logprob(segment.get("avg_logprob"))
        ratio = _finite(segment.get("compression_ratio"))
        if found is not None:
            no_speech = found if no_speech is None else max(no_speech, found)
        if low is not None:
            logprob = low if logprob is None else min(logprob, low)
        if ratio is not None and ratio >= 0:
            compression = ratio if compression is None else max(compression, ratio)
        if (found is not None and low is not None
                and found >= WEAK_NO_SPEECH and low <= WEAK_AVG_LOGPROB):
            weak += 1
    summary = {"text_segments": counted, "weak_segments": weak}
    if no_speech is not None:
        summary["no_speech_pct"] = no_speech * 100.0
    if logprob is not None:
        summary["avg_logprob_x100"] = logprob * 100.0
    if compression is not None:
        summary["compression_x100"] = compression * 100.0
    return summary


@dataclass(frozen=True)
class SpeechEvidence:
    """검증기가 실제로 잰 값. 모델이 주지 않는 신뢰도를 지어내지 않는다.

    segment_seconds 는 Silero 가 돌려준 발화 구간 길이의 합이다. 모델이 구간
    앞뒤에 붙이는 패딩이 포함되므로 순수 발성 시간보다 길다. 채택 판단의 실제
    기준은 모델의 native min_speech_duration 이고, min_speech_ms 는 그 값과
    어긋나지 않도록 두는 합계 하한이다.
    """
    segment_seconds: float
    total_seconds: float
    segments: int
    accepted: bool
    source: str = "silero_vad"

    def as_dict(self):
        return {"source": self.source, "segment_sec": round(self.segment_seconds, 3),
                "total_sec": round(self.total_seconds, 3), "segments": self.segments,
                "accepted": self.accepted}


@dataclass(frozen=True)
class TailEvidence:
    """창 끝 기준의 비음성 꼬리 길이. 채택 판단이 아니라 말끝 근거다.

    measured 가 거짓이면 모델이 구간 위치를 주지 않아 꼬리를 재지 못한 것이다.
    그때 tail_seconds 는 0 이고, 호출자는 아무것도 하지 않는다. 재지 못한 것을
    무음으로 읽지 않는다.
    """
    tail_seconds: float
    total_seconds: float
    segments: int
    measured: bool = True
    source: str = "silero_vad"

    def as_dict(self):
        return {"source": self.source, "tail_sec": round(self.tail_seconds, 3),
                "total_sec": round(self.total_seconds, 3),
                "segments": self.segments, "measured": self.measured}


class SileroSpeechGate:
    """연결이 공유하는 단일 소유 검증기.

    sherpa-onnx 의 VoiceActivityDetector 는 상태를 가지며 스레드 안전하지 않다.
    SenseVoiceFrontend 와 같은 방식으로 단일 워커 실행기가 모델을 독점하고,
    호출마다 reset() 으로 앞선 발화의 상태를 지운다.

    기본값 threshold=0.7 / min_speech_duration=0.2 는 메인의 실제 모델 단일변수
    비교에서 정한 값이다. 공개 한국어 1개와 합성 짧은 응답 10종을 3회씩 돌린
    33/33 을 모두 채택하고 hum·white·clicks 3종×3seed×3회 27/27 을 모두 거절했다.
    같은 설정의 0.7초 중간 구간 검사에서도 잡음 53건을 모두 거절했다.
    """

    def __init__(self, model_path, *, threshold=.7, min_speech_ms=200,
                 min_speech_duration=.2, window_size=512, num_threads=1, max_seconds=35):
        import sherpa_onnx
        path = Path(model_path)
        if not path.is_file():
            raise FileNotFoundError(
                f"Silero VAD model missing at {path}; "
                "run setup_dialogue_models.py --vad to install it")
        config = sherpa_onnx.VadModelConfig()
        config.silero_vad.model = str(path)
        config.silero_vad.threshold = threshold
        # 실제 채택 기준. 0.05는 hum·white·clicks 를 통과시켜 기각했다.
        config.silero_vad.min_speech_duration = min_speech_duration
        config.silero_vad.min_silence_duration = .1
        config.silero_vad.max_speech_duration = float(max_seconds)
        config.silero_vad.window_size = window_size
        config.sample_rate = SAMPLE_RATE
        config.num_threads = num_threads
        config.provider = "cpu"
        self.threshold = threshold
        self.window_size = window_size
        self.min_speech_duration = min_speech_duration
        self.min_speech_seconds = min_speech_ms / 1000.0
        self.detector = sherpa_onnx.VoiceActivityDetector(
            config, buffer_size_in_seconds=float(max_seconds))
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="speech-gate")

    def settings(self):
        return {"version": GATE_VERSION, "acoustic": "silero_vad",
                "threshold": self.threshold,
                "min_speech_duration_ms": round(self.min_speech_duration * 1000),
                "min_segment_sum_ms": round(self.min_speech_seconds * 1000),
                # 말끝 보완은 같은 모델·같은 실행기를 쓴다. 별도 토글은 두지 않는다.
                # 꼬리 기준 길이는 연결의 silence_ms 이므로 여기서는 말하지 않는다.
                "endpoint": {"version": ENDPOINT_VERSION,
                             "window_ms": round(ENDPOINT_WINDOW_SECONDS * 1000),
                             "min_open_ms": round(ENDPOINT_MIN_OPEN_SECONDS * 1000),
                             "check_ms": round(ENDPOINT_CHECK_SECONDS * 1000)},
                # 전사 뒤에 적용하는 모듈 규칙이다. 이 검증기의 음향 판정과는 별개다.
                # 배포된 임계값을 health 에서 그대로 확인하려고 싣는다.
                "weak_text": {"version": WEAK_VERSION, "no_speech": WEAK_NO_SPEECH,
                              "avg_logprob": WEAK_AVG_LOGPROB}}

    async def verify(self, pcm):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self._measure, pcm)

    async def tail_silence(self, pcm):
        """창의 마지막 음성 이후 비음성이 얼마나 이어졌는지 잰다.

        채택·전사와는 무관하다. 기존 검증기와 같은 단일 실행기에서 돌기 때문에
        최종 검증과 절대 겹치지 않는다. 결과는 호출자가 식별값으로 확인한 뒤에만
        쓴다.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self._measure_tail, pcm)

    def _drain_tail(self):
        """구간의 끝 위치(초)와 개수. reset 이후 누적 샘플 기준이다."""
        end, segments = -1.0, 0
        while not self.detector.empty():
            segment = self.detector.front
            start = getattr(segment, "start", None)
            if isinstance(start, (int, float)) and not isinstance(start, bool):
                end = max(end, (float(start) + len(segment.samples)) / SAMPLE_RATE)
            segments += 1
            self.detector.pop()
        return end, segments

    def _measure_tail(self, pcm):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        total = len(samples) / SAMPLE_RATE
        self.detector.reset()
        end, segments = -1.0, 0
        for start in range(0, len(samples), self.window_size):
            window = samples[start:start + self.window_size]
            if len(window) < self.window_size:
                window = np.pad(window, (0, self.window_size - len(window)))
            self.detector.accept_waveform(window)
            found, count = self._drain_tail()
            end, segments = max(end, found), segments + count
        self.detector.flush()
        found, count = self._drain_tail()
        end, segments = max(end, found), segments + count
        if not segments:
            # 창 전체가 비음성이다. 다만 native min_speech_duration(기본 0.2초)보다
            # 짧은 실제 발성은 구간으로 보고되지 않으므로, 이 값은 "0.2초 미만의
            # 짧은 소리는 없다고 볼 때의 꼬리"다. 그런 소리가 꼬리 안에 있었다면
            # 말끝이 실제보다 이르게 잡힐 수 있다. 이 한계는 남아 있다.
            return TailEvidence(total, total, 0)
        if end < 0:
            # 모델이 구간 위치를 주지 않았다. 재지 못한 것을 무음으로 읽지 않는다.
            return TailEvidence(0.0, total, segments, measured=False)
        # 모델은 구간 끝에 min_silence_duration 만큼의 여유를 붙인다. 그래서 이
        # 꼬리는 실제 무음보다 짧게 나오고, 판단은 늘 보수적인 쪽으로 기운다.
        return TailEvidence(max(0.0, total - end), total, segments)

    def _drain(self):
        spanned, segments = 0.0, 0
        while not self.detector.empty():
            spanned += len(self.detector.front.samples) / SAMPLE_RATE
            segments += 1
            self.detector.pop()
        return spanned, segments

    def _measure(self, pcm):
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
        total = len(samples) / SAMPLE_RATE
        self.detector.reset()
        spanned, segments = 0.0, 0
        for start in range(0, len(samples), self.window_size):
            window = samples[start:start + self.window_size]
            if len(window) < self.window_size:
                window = np.pad(window, (0, self.window_size - len(window)))
            self.detector.accept_waveform(window)
            found, count = self._drain()
            spanned += found
            segments += count
        self.detector.flush()
        found, count = self._drain()
        spanned += found
        segments += count
        # 구간이 하나라도 남았다는 것은 native min_speech_duration 을 넘겼다는 뜻이다.
        # 합계 비교는 1 ms 여유를 둔다. 경계값이 부동소수점 때문에 흔들리면 안 된다.
        accepted = segments > 0 and spanned >= self.min_speech_seconds - .001
        return SpeechEvidence(spanned, total, segments, accepted)

    def close(self):
        self.executor.shutdown(wait=True, cancel_futures=True)
