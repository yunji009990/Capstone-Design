"""연결별 진단 집계. 실제 설정에서도 INFO 한 줄이 남도록 전용 로거를 쓴다.

전역 logging.basicConfig 를 건드리지 않는다. `dialogue.diagnostics` 이름의 로거에만
RotatingFileHandler 를 붙이고 propagate 를 끈다. 기존 검사의 로깅 부작용을 피하기 위해
핸들러는 명시적으로 설정할 때만 만든다. 경로·권한 오류로 서비스가 죽지 않도록
설정 실패는 진단만 끄고 넘어간다.

남기는 것: **미리 정한 코드**와 숫자뿐이다. 사건 이름·원인은 아래 화이트리스트에
있는 값만 기록하고 그 밖의 값은 `other` 로 접는다. trace 는 고정 길이 16진수만 받고
형식이 어긋나면 서버가 새 UUID 를 만든다.

남기지 않는 것: 사용자 발화·모델 답변 원문, 모델이 만든 자유 문장(reason 포함),
실제 세션 ID, 등록 인물 정보, 토큰·URL·경로, PCM, 환경변수.
safe_code 는 문자 치환일 뿐 비밀을 가리는 필터가 아니므로 호출부에서 코드만 넘긴다.
"""
from __future__ import annotations

import logging
import logging.handlers
import math
import os
import re
import time
import uuid

# v3 에서 말끝 보완 계수와 input.endpoint 사건이 늘었다. v4 는 단계별 사건
# (route.done, llm.first_text, tts.phrase_*, turn.timing, playback.ack)만 더한다.
# 기존 v3 사건·키·계수의 의미는 그대로다.
# 약한 전사 거절은 기존 input.rejected·input.verified 에 이유와 숫자만 더한다.
# 사건 이름과 기존 키의 뜻이 바뀌지 않으므로 버전은 v4 를 유지한다.
# 멈춤 경계 보고도 같다. 기존 playback.ack 에 reason=paused 와 pause_id 를 더하고
# 연결 계수 네 개가 늘 뿐이다. 새 사건 이름이 없으므로 v4 를 유지한다.
DIAGNOSTICS_VERSION = "dialogue_diag_v4"
LOGGER_NAME = "dialogue.diagnostics"
MAX_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 5

_TRACE = re.compile(r"\A[0-9a-f]{12}\Z|\A[0-9a-f]{32}\Z")
_SAFE = re.compile(r"[^A-Za-z0-9_.:-]")

# 기록을 허용하는 사건과 원인. 여기 없는 값은 other 로 접는다.
EVENTS = frozenset({
    "connection.open", "connection.close", "input.candidate", "input.rejected",
    "input.verified", "input.endpoint", "turn.started", "turn.done", "turn.cancelled",
    "turn.held", "turn.resumed", "heartbeat",
    # v4 단계 사건. 모두 자기 turn 을 명시한다.
    # input.assigned 는 채택된 후보와 응답 turn 을 잇는 유일한 줄이다.
    "input.assigned", "route.done", "llm.first_text", "tts.phrase_started",
    "tts.phrase_first_audio", "tts.phrase_done", "turn.timing", "playback.ack",
    # 생성 없이 미리 준비한 안내 문장을 읽은 턴. 일반 생성은 llm.first_text 로 남는다.
    "answer.fixed",
})
REASONS = frozenset({
    # 연결
    "registered", "test", "client_stop", "client_cancel", "disconnect",
    "protocol_error", "voice_error", "server_error", "shutdown", "close",
    # 입력 검증
    "accepted", "no_speech", "no_text", "verify_failed", "dropped", "too_long",
    # 표기 문자는 있으나 모든 전사 세그먼트의 Whisper 지표가 비음성을 가리킨 입력.
    # 이유는 이 고정 코드뿐이고 전사 내용은 남기지 않는다.
    "weak_text",
    # 입력 후보 수명
    "started", "closed", "queue_full",
    # 말끝 보완: 확인된 비음성 꼬리로 말끝, 상한 폐기 재무장, 말이 이어짐,
    # 검증기 실패, 창을 만든 뒤 오디오가 너무 흘러 쓰지 않은 결과
    "silence_tail", "rearmed", "speech", "failed", "stale",
    # 응답
    "normal", "reasoning", "resume", "revise", "switch", "hold", "clarify",
    "turn_failed", "context_too_long", "newer_utterance", "hold_timeout",
    "memory_forget", "utterance_too_long", "reset", "experience_end", "user_speech",
    # answer.fixed 의 출처. 기억 삭제 성공과 미수행을 구별한다.
    "memory_forgotten", "memory_clarify",
    # 종료
    "receive_timeout", "server_shutdown", "input",
    # v4 문장 TTS 구간의 종류. 대기 리액션과 본답변을 구별한다.
    "answer", "reaction",
    # playback.ack 가 멈춘 지점의 보고라는 뜻. 재생 진행·완료 보고와 구별한다.
    "paused",
    "other",
})

# 사건에 덧붙일 수 있는 숫자 항목. 이름도 미리 정해 둔다.
NUMBERS = frozenset({"candidate", "turn", "samples", "asr_ms", "seconds", "code",
                     # 말끝 보완: 후보 나이, WebRTC voiced 비율, 최대 연속 무음,
                     # 검증기가 잰 비음성 꼬리(재지 못하면 -1)
                     "age_ms", "voiced_pct", "max_quiet_ms", "tail_ms",
                     # v4: 판정 시간, 생성 시작 기준 첫 텍스트, 문장 TTS 계수,
                     # 응답 단계 시간(재지 못하면 -1), 클라이언트 재생 보고
                     # first_text_ms 는 respond 진입 기준, llm_first_ms 는 생성 호출 기준이다.
                     "route_ms", "first_text_ms", "llm_first_ms", "phrase", "chars", "packets",
                     "tts_first_ms", "tts_total_ms", "tts_max_gap_ms",
                     # 입력 품질 집계. 채택(input.verified)과 거절(input.rejected)에
                     # 같은 모양으로 붙는다. x100 은 아래 소수 첫째 자리 반올림에서
                     # 해상도를 잃지 않으려고 100 을 곱한 값이다. 재지 못한 항목은
                     # 줄에서 빠진다. -1 로 적지 않는다(-1 이 실제 값일 수 있다).
                     "no_speech_pct", "avg_logprob_x100", "compression_x100",
                     "text_segments", "weak_segments",
                     "first_text_sec", "first_audio_sec", "first_reaction_audio_sec",
                     "first_any_audio_sec", "total_sec", "done",
                     # 멈춤 회차. 이 보고가 어느 멈춤 요청에 대한 것인지 잇는 번호다.
                     "pause_id"})
HEARTBEAT_SECONDS = 5.0
MISSING = -1.0          # 재지 못한 수치. 0 과 구별한다.


def safe_code(value, limit=48):
    """문자 치환만 한다. 비밀을 가리지 못하므로 코드에만 쓴다."""
    return _SAFE.sub("_", str(value))[:limit] if value else ""


def known(value, allowed):
    """화이트리스트에 있는 코드만 통과시키고 나머지는 other 로 접는다."""
    code = safe_code(value)
    return code if code in allowed else "other"


def measured(value):
    """잰 값이면 숫자로, 재지 못했으면 -1 로 바꾼다. NaN·inf 도 미측정으로 본다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return MISSING
    return float(value) if math.isfinite(value) else MISSING


def valid_trace(value):
    """클라이언트가 보낸 가명 ID. 고정 길이 16진수가 아니면 새로 만든다."""
    text = str(value or "").strip().lower()
    return text if _TRACE.match(text) else uuid.uuid4().hex[:12]


class _UtcFormatter(logging.Formatter):
    converter = time.gmtime


def configure(path="", level=logging.INFO):
    """전용 로거에 회전 파일 핸들러를 붙인다. 실패하면 진단만 끄고 서비스는 계속한다."""
    log = logging.getLogger(LOGGER_NAME)
    log.propagate = False
    if not path:
        return log
    try:
        resolved = os.path.abspath(os.path.expanduser(path))
        for handler in log.handlers:
            if getattr(handler, "baseFilename", None) == resolved:
                return log
        os.makedirs(os.path.dirname(resolved) or ".", exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            resolved, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
        handler.setFormatter(_UtcFormatter("%(asctime)sZ %(levelname)s %(message)s"))
        handler.setLevel(level)
        log.addHandler(handler)
        log.setLevel(level)
    except OSError:
        # 경로·권한 문제로 서비스 시작을 막지 않는다. 진단만 없는 상태로 둔다.
        logging.getLogger(__name__).warning("diagnostics log disabled: cannot open the configured path")
    return log


def active():
    """실제로 파일 핸들러가 붙어 있는가. 설정 문자열이 있다는 뜻이 아니다."""
    return any(isinstance(h, logging.handlers.RotatingFileHandler)
               for h in logging.getLogger(LOGGER_NAME).handlers)


class ConnectionDiagnostics:
    """한 연결의 계측을 모아 두었다가 경계마다 한 줄로 남긴다.

    카운터는 상한이 있는 정수·시간뿐이다. 내용은 담지 않는다.
    클라이언트와 같은 trace, 같은 turn 으로 맞춰 양쪽 로그를 대조할 수 있게 한다.
    """

    def __init__(self, trace_id="", clock=time.monotonic):
        self.trace = valid_trace(trace_id)
        self.clock = clock
        self.log = logging.getLogger(LOGGER_NAME)
        self.started = clock()
        self.turn = 0
        self.last_audio = None
        self.last_heartbeat = clock()
        self.closed = False
        self.counts = {
            "audio_chunks": 0, "vad_start": 0, "vad_end": 0, "vad_too_long": 0,
            "accepted": 0, "no_speech": 0, "no_text": 0, "verify_failed": 0,
            "dropped": 0, "turns": 0, "audio_packets": 0, "held": 0, "resumed": 0,
            # 약한 전사로 거절한 입력 수.
            "weak_text": 0,
            "endpoint_checks": 0, "endpoint_end": 0, "endpoint_rearm": 0,
            "endpoint_failed": 0, "endpoint_stale": 0,
            # 멈춘 지점: 구절 경계 / 그 밖의 정지 / 신원이 맞지 않는 늦은 보고 /
            # 기한 안에 보고가 오지 않아 즉시 멈춤으로 넘어간 경우.
            "pause_boundary": 0, "pause_cut": 0, "pause_stale": 0, "pause_timeout": 0,
        }
        self.max_input_gap_ms = 0.0
        self.asr_ms_total = 0.0
        self.asr_calls = 0
        self.max_asr_ms = 0.0
        self.sent_audio_samples = 0
        self._reset_response()

    def _reset_response(self):
        self.response_packets = 0
        self.response_samples = 0
        self.response_first = None
        self.response_last = None
        self.response_max_gap_ms = 0.0
        # 문장 단위 TTS 구간. 응답이 바뀌면 번호와 계수를 처음부터 다시 센다.
        self.phrase_seq = 0
        self.phrase_kind = ""
        self.phrase_open = None
        self.phrase_packets = 0
        self.phrase_samples = 0
        self.phrase_first = None
        self.phrase_last = None
        self.phrase_max_gap_ms = 0.0

    # --- 수신 ---
    def audio_chunk(self, samples):
        now = self.clock()
        if self.last_audio is not None:
            gap = (now - self.last_audio) * 1000.0
            if gap > self.max_input_gap_ms:
                self.max_input_gap_ms = gap
        self.last_audio = now
        self.counts["audio_chunks"] += 1

    def count(self, name, amount=1):
        key = safe_code(name)
        if key in self.counts:
            self.counts[key] += amount

    def asr(self, seconds):
        self.asr_calls += 1
        millis = seconds * 1000.0
        self.asr_ms_total += millis
        if millis > self.max_asr_ms:
            self.max_asr_ms = millis

    # --- 응답 ---
    def turn_started(self, turn, route):
        self.turn = int(turn) if isinstance(turn, int) else 0
        self.counts["turns"] += 1
        self._reset_response()
        self.event("turn.started", known(route, REASONS))

    def phrase_started(self, turn, kind, chars):
        """한 문장의 TTS 구간을 연다. 내용은 담지 않고 길이만 센다.

        PCM 계수는 answer_audio 가 실제 전송 성공 뒤에 센다. 구간이 취소로 끝나면
        tts.phrase_done 이 없다. 그 부재를 완료나 0 으로 읽지 않는다.
        """
        self.phrase_seq += 1
        self.phrase_kind = known(kind, REASONS)
        self.phrase_open = self.clock()
        self.phrase_packets = 0
        self.phrase_samples = 0
        self.phrase_first = None
        self.phrase_last = None
        self.phrase_max_gap_ms = 0.0
        self.event("tts.phrase_started", self.phrase_kind, turn=turn,
                   phrase=self.phrase_seq, chars=chars)

    def phrase_done(self, turn, chars):
        """열린 구간을 닫는다. 샘플 수는 합성 길이일 뿐 품질 근거가 아니다."""
        if self.phrase_open is None:
            return
        first_ms = MISSING if self.phrase_first is None else (self.phrase_first - self.phrase_open) * 1000.0
        self.event("tts.phrase_done", self.phrase_kind, turn=turn, phrase=self.phrase_seq,
                   chars=chars, samples=self.phrase_samples, packets=self.phrase_packets,
                   tts_first_ms=first_ms,
                   tts_total_ms=(self.clock() - self.phrase_open) * 1000.0,
                   tts_max_gap_ms=self.phrase_max_gap_ms)
        self.phrase_open = None

    def _phrase_audio(self, now, samples):
        """전송에 성공한 PCM 만 문장 계수에 넣는다."""
        if self.phrase_open is None:
            return
        if self.phrase_last is not None:
            gap = (now - self.phrase_last) * 1000.0
            if gap > self.phrase_max_gap_ms:
                self.phrase_max_gap_ms = gap
        self.phrase_last = now
        self.phrase_packets += 1
        self.phrase_samples += samples
        if self.phrase_first is None:
            self.phrase_first = now
            self.event("tts.phrase_first_audio", self.phrase_kind, turn=self.turn,
                       phrase=self.phrase_seq, samples=samples,
                       tts_first_ms=(now - self.phrase_open) * 1000.0)

    def answer_audio(self, samples):
        """대기 리액션과 본답변을 모두 센다. 답변별 첫/끝/최대 간격을 남긴다."""
        now = self.clock()
        if self.response_last is not None:
            gap = (now - self.response_last) * 1000.0
            if gap > self.response_max_gap_ms:
                self.response_max_gap_ms = gap
        if self.response_first is None:
            self.response_first = now
        self.response_last = now
        self.response_packets += 1
        self.response_samples += samples
        self.counts["audio_packets"] += 1
        self.sent_audio_samples += samples
        self._phrase_audio(now, samples)

    # --- 출력 ---
    def snapshot(self):
        span = 0.0
        if self.response_first is not None and self.response_last is not None:
            span = self.response_last - self.response_first
        value = {"v": DIAGNOSTICS_VERSION, "trace": self.trace, "turn": self.turn,
                 "uptime_s": round(self.clock() - self.started, 1),
                 "max_input_gap_ms": round(self.max_input_gap_ms, 1),
                 "asr_calls": self.asr_calls,
                 "asr_avg_ms": round(self.asr_ms_total / self.asr_calls, 1) if self.asr_calls else 0.0,
                 "asr_max_ms": round(self.max_asr_ms, 1),
                 "resp_packets": self.response_packets,
                 "resp_samples": self.response_samples,
                 "resp_span_s": round(span, 2),
                 "resp_max_gap_ms": round(self.response_max_gap_ms, 1),
                 "answer_audio_sec": round(self.sent_audio_samples / 24000.0, 2)}
        value.update(self.counts)
        return value

    def heartbeat(self):
        """연결이 살아 있는지 주기적으로 남긴다. 패킷마다 쓰지 않는다."""
        now = self.clock()
        if now - self.last_heartbeat < HEARTBEAT_SECONDS:
            return
        self.last_heartbeat = now
        self.event("heartbeat", "input")

    def event(self, name, reason="", **numbers):
        """경계 사건 한 줄. 미리 정한 코드와 숫자만 남긴다.

        numbers 는 NUMBERS 에 있는 이름의 정수/실수만 받는다. 문자열은 받지 않으므로
        모델 출력이나 예외 원문이 실수로 섞여도 파일에 나가지 않는다. NaN·inf 는
        버린다. 재지 못한 수치는 호출부가 measured() 로 -1 을 넘긴다.

        한 줄에는 연결 누계(snapshot)와 이번 사건의 숫자가 함께 들어간다. 읽을 때
        asr_*·audio_packets·counts 같은 누계는 연결 전체의 값이고, 단계 사건의
        상태는 그 줄의 turn= 과 phrase= 로만 판단한다. 누계를 특정 turn 의 값으로
        읽지 않는다.
        """
        if name == "connection.close":
            if self.closed:
                return          # 종료 경로가 겹쳐도 한 번만 남긴다
            self.closed = True
        fields = self.snapshot()
        fields["event"] = known(name, EVENTS)
        if reason:
            fields["reason"] = known(reason, REASONS)
        for key, value in numbers.items():
            if key not in NUMBERS or isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
            if isinstance(value, float):
                if not math.isfinite(value):
                    continue        # NaN·inf 는 남기지 않는다. 항목 자체가 없다.
                value = round(value, 3 if key.endswith("_sec") else 1)
            fields[key] = value
        # 마지막 검증. 누계 snapshot 에 NaN·inf 가 섞여도 파일에는 그 항목이 나가지 않는다.
        # 계산은 그대로 두고 기록만 거른다.
        line = " ".join(f"{key}={value}" for key, value in fields.items()
                        if not (isinstance(value, float) and not math.isfinite(value)))
        try:
            self.log.info(line)
        except Exception:
            # 진단 기록 실패가 응답·재생을 멈추게 하지 않는다.
            pass
