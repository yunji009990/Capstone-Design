"""진단 로거가 내용을 담지 않고 정해진 코드와 집계만 남기는지 검사한다.

전역 로깅을 건드리지 않는지, 경로 오류가 서비스를 막지 않는지, UTC 로 남는지,
trace 형식 검증과 중복 종료 한 번만 기록, 응답별 간격 초기화도 함께 본다.
v4 의 단계 사건(route.done, llm.first_text, tts.phrase_*, turn.timing, playback.ack)은
실제 Dialogue 경로로 확인한다. 모델·TTS 는 가짜이므로 품질 검사가 아니다.
"""
import asyncio
import base64
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient

from dialogue_diagnostics import (DIAGNOSTICS_VERSION, LOGGER_NAME, ConnectionDiagnostics,
                                  active, configure, known, measured, safe_code, valid_trace,
                                  EVENTS, REASONS)
from dialogue_reactions import ReactionBank, ReactionClip
from dialogue_server import Settings, create_app
from realtime_dialogue import Dialogue
from test_dialogue import QUIET, SPEECH, Frontend, Gate, detector, eventually


class WireLLM:
    async def available(self, endpoint=None): return True
    async def route(self, messages): return "normal"
    async def stream(self, messages, route):
        yield "먼저 보낸 말."


def drop_handlers():
    log = logging.getLogger(LOGGER_NAME)
    for handler in list(log.handlers):
        handler.close()
        log.removeHandler(handler)


class CodeTests(unittest.TestCase):
    def test_only_known_codes_are_logged(self):
        self.assertEqual(known("no_speech", REASONS), "no_speech")
        self.assertEqual(known("connection.close", EVENTS), "connection.close")
        # 모델이 만든 자유 문장이나 낯선 값은 통째로 other 가 된다.
        self.assertEqual(known("사용자가 발표 일정을 물었다", REASONS), "other")
        self.assertEqual(known("Bearer sk-secret", REASONS), "other")
        self.assertEqual(known("/home/user/voice.wav", REASONS), "other")
        self.assertEqual(known("", REASONS), "other")

    def test_safe_code_is_only_a_character_filter(self):
        # 치환만 하므로 비밀을 가리지 못한다. 그래서 호출부는 known 을 쓴다.
        self.assertEqual(safe_code("vad_start"), "vad_start")
        self.assertNotIn(" ", safe_code("a b"))
        self.assertEqual(safe_code("x" * 200), "x" * 48)

    def test_trace_accepts_only_fixed_hex_and_replaces_anything_else(self):
        self.assertEqual(valid_trace("0123456789ab"), "0123456789ab")
        self.assertEqual(valid_trace("A" * 32), "a" * 32)
        for invalid in ("", None, "trace one!", "0123456789", "zzzzzzzzzzzz", "x" * 64,
                        "../../etc/passwd", "session-c981c359"):
            with self.subTest(invalid=invalid):
                replaced = valid_trace(invalid)
                self.assertEqual(len(replaced), 12)
                self.assertNotEqual(replaced, str(invalid or "").lower())


class ConnectionDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.now = [0.0]
        self.diag = ConnectionDiagnostics("0123456789ab", clock=lambda: self.now[0])

    def test_input_gap_and_asr_are_bounded_numbers(self):
        self.diag.audio_chunk(1280)
        self.now[0] = 0.08
        self.diag.audio_chunk(1280)
        self.now[0] = 0.5
        self.diag.audio_chunk(1280)
        self.diag.asr(0.12)
        self.diag.asr(0.06)
        snapshot = self.diag.snapshot()
        self.assertEqual(snapshot["audio_chunks"], 3)
        self.assertAlmostEqual(snapshot["max_input_gap_ms"], 420.0, places=1)
        self.assertEqual(snapshot["asr_calls"], 2)
        self.assertAlmostEqual(snapshot["asr_max_ms"], 120.0, places=1)
        self.assertEqual(snapshot["v"], DIAGNOSTICS_VERSION)

    def test_response_gaps_reset_on_each_turn(self):
        self.diag.turn_started(1, "normal")
        self.diag.answer_audio(4800)
        self.now[0] = 0.2
        self.diag.answer_audio(4800)
        first = self.diag.snapshot()
        self.assertEqual(first["turn"], 1)
        self.assertEqual(first["resp_packets"], 2)
        self.assertAlmostEqual(first["resp_max_gap_ms"], 200.0, places=1)
        # 사용자가 오래 생각한 뒤의 다음 답변이 수신 지연으로 잡히면 안 된다.
        self.now[0] = 30.0
        self.diag.turn_started(2, "reasoning")
        self.diag.answer_audio(4800)
        second = self.diag.snapshot()
        self.assertEqual(second["turn"], 2)
        self.assertEqual(second["resp_packets"], 1)
        self.assertEqual(second["resp_max_gap_ms"], 0.0)
        # 연결 전체 누계는 유지된다.
        self.assertEqual(second["audio_packets"], 3)

    def test_missing_and_nonfinite_values_become_minus_one(self):
        self.assertEqual(measured(None), -1.0)         # 재지 못한 값
        self.assertEqual(measured(float("nan")), -1.0)
        self.assertEqual(measured(float("inf")), -1.0)
        self.assertEqual(measured(True), -1.0)         # 참/거짓은 수치가 아니다
        self.assertEqual(measured(0), 0.0)             # 0 은 미측정이 아니다
        self.assertAlmostEqual(measured(1.25), 1.25)

    def test_phrase_counters_count_sent_packets_and_reset_each_turn(self):
        self.diag.turn_started(1, "reasoning")
        self.diag.phrase_started(1, "reaction", 8)
        self.now[0] = 0.1
        self.diag.answer_audio(4800)
        self.now[0] = 0.3
        self.diag.answer_audio(4800)
        self.assertEqual(self.diag.phrase_packets, 2)
        self.assertEqual(self.diag.phrase_samples, 9600)
        self.assertAlmostEqual(self.diag.phrase_max_gap_ms, 200.0, places=1)
        self.diag.phrase_done(1, 8)
        self.assertIsNone(self.diag.phrase_open)
        self.diag.phrase_started(1, "answer", 12)
        self.assertEqual(self.diag.phrase_seq, 2)
        self.assertEqual(self.diag.phrase_packets, 0)  # 이전 문장 계수가 섞이지 않는다
        # 새 응답은 문장 번호도 계수도 처음부터 다시 센다.
        self.diag.turn_started(2, "normal")
        self.assertEqual(self.diag.phrase_seq, 0)
        self.assertIsNone(self.diag.phrase_open)
        self.diag.answer_audio(4800)                   # 열린 문장이 없어도 예외가 없다
        self.assertEqual(self.diag.snapshot()["audio_packets"], 3)

    def test_unknown_counters_are_ignored(self):
        self.diag.count("accepted")
        self.diag.count("not_a_counter")
        self.assertEqual(self.diag.snapshot()["accepted"], 1)
        self.assertNotIn("not_a_counter", self.diag.snapshot())


class DiagnosticLoggerTests(unittest.TestCase):
    def tearDown(self):
        drop_handlers()

    def test_dedicated_utc_handler_never_touches_the_root_logger(self):
        root_before = list(logging.getLogger().handlers)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "diagnostics.log"
            log = configure(str(path))
            self.assertFalse(log.propagate)
            self.assertEqual(len(log.handlers), 1)
            self.assertTrue(active())
            configure(str(path))                      # 다시 불러도 핸들러가 늘지 않는다
            self.assertEqual(len(log.handlers), 1)
            self.assertEqual(log.handlers[0].formatter.converter.__name__, "gmtime")

            diag = ConnectionDiagnostics("0123456789ab")
            diag.turn_started(3, "normal")
            diag.event("connection.close", "client_stop")
            diag.event("connection.close", "disconnect")   # 중복 종료는 한 번만
            written = path.read_text(encoding="utf-8")
            drop_handlers()

        self.assertEqual(logging.getLogger().handlers, root_before)
        self.assertEqual(written.count("event=connection.close"), 1)
        self.assertIn("event=turn.started", written)
        self.assertIn("reason=client_stop", written)
        self.assertIn("trace=0123456789ab", written)
        self.assertIn("turn=3", written)

    def test_free_text_reason_never_reaches_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.log"
            configure(str(path))
            ConnectionDiagnostics("0123456789ab").event("turn.cancelled", "사용자가 발표 일정을 물었다")
            written = path.read_text(encoding="utf-8")
            drop_handlers()
        self.assertIn("reason=other", written)
        self.assertNotIn("발표", written)

    def test_nonfinite_and_unknown_numbers_never_reach_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.log"
            configure(str(path))
            ConnectionDiagnostics("0123456789ab").event(
                "turn.timing", turn=4, first_text_sec=1.23456,
                first_audio_sec=measured(None), total_sec=float("nan"),
                route_ms=float("inf"), latency_ms=12, chars=True)
            written = path.read_text(encoding="utf-8")
            drop_handlers()
        self.assertIn("event=turn.timing", written)
        self.assertIn("turn=4", written)
        self.assertIn("first_text_sec=1.235", written)     # 초는 소수 셋째 자리
        self.assertIn("first_audio_sec=-1.0", written)     # 미측정은 0 이 아니다
        for absent in ("total_sec=", "route_ms=", "latency_ms", "chars=True"):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, written)

    def test_nonfinite_snapshot_totals_never_reach_the_file(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.log"
            configure(str(path))
            diag = ConnectionDiagnostics("0123456789ab")
            # 시계·계산이 어떤 이유로 NaN·inf 가 되어도 기록에는 나가지 않는다.
            diag.max_input_gap_ms = float("nan")
            diag.sent_audio_samples = float("inf")
            diag.event("heartbeat", "input")
            written = path.read_text(encoding="utf-8")
            drop_handlers()
        self.assertIn("event=heartbeat", written)
        self.assertNotIn("max_input_gap_ms", written)
        self.assertNotIn("answer_audio_sec", written)
        # levelname 의 INFO 와 섞이지 않게 값 위치로 본다.
        for token in ("=nan", "=inf", "=-inf"):
            with self.subTest(token=token):
                self.assertNotIn(token, written.lower())

    def test_connection_totals_and_turn_fields_are_separate(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "diagnostics.log"
            configure(str(path))
            diag = ConnectionDiagnostics("0123456789ab")
            diag.asr(0.2)
            diag.turn_started(1, "normal")
            diag.asr(0.4)
            diag.turn_started(2, "normal")
            diag.event("turn.timing", turn=1, first_text_sec=0.5)
            written = path.read_text(encoding="utf-8")
            drop_handlers()
        line = [l for l in written.splitlines() if "event=turn.timing" in l][0]
        # 누계는 연결 전체(ASR 2회)이고, 이 줄이 말하는 응답은 turn= 이 정한다.
        self.assertIn("asr_calls=2", line)
        self.assertIn("turn=1", line)
        self.assertNotIn("turn=2", line)

    def test_no_path_means_no_handler_and_no_side_effects(self):
        log = configure("")
        self.assertEqual(log.handlers, [])
        self.assertFalse(active())
        ConnectionDiagnostics("0123456789ab").event("connection.open")   # 예외가 나면 안 된다

    def test_unusable_path_disables_diagnostics_without_raising(self):
        with tempfile.TemporaryDirectory() as folder:
            # 파일을 폴더처럼 쓰려 하면 열 수 없다. 서비스는 계속되어야 한다.
            blocker = Path(folder) / "blocker"
            blocker.write_text("not a directory", encoding="utf-8")
            log = configure(str(blocker / "diagnostics.log"))
            self.assertEqual(log.handlers, [])
            self.assertFalse(active())
            ConnectionDiagnostics("0123456789ab").event("connection.open", "registered")
        self.assertFalse(os.path.isdir(str(blocker)))


class ConnectionContractTests(unittest.TestCase):
    """실제 create_app 의 WebSocket 경로가 어떤 사유로 닫혔는지 기록되는지 본다.

    가짜 frontend/LLM 을 쓰므로 실제 모델 검사가 아니다. 로깅 계약만 확인한다.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log = Path(self.tmp.name) / "diagnostics.log"
        self.settings = Settings(self.tmp.name, "unused", token="test-token",
                                 allow_test_mode=True, diagnostics_log=str(self.log))
        self.app = create_app(self.settings, frontend=Frontend(), llm=WireLLM(),
                              detector_factory=detector, speech_gate=Gate())
        self.hello = {"type": "start", "protocol": 1, "test_mode": True,
                      "test_persona": "가상 도우미", "sample_rate": 16000,
                      "channels": 1, "format": "pcm_s16le"}
        self.addCleanup(drop_handlers)

    def written(self):
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            handler.flush()
        return self.log.read_text(encoding="utf-8") if self.log.exists() else ""

    def connect(self, web):
        return web.websocket_connect("/dialogue", headers={"X-Token": "test-token"})

    def test_legacy_client_without_trace_still_gets_one(self):
        with TestClient(self.app) as web:
            self.assertTrue(web.get("/health").json()["diagnostics"]["file"])
            with self.connect(web) as ws:
                ws.send_json(self.hello)          # trace_id 없음 = 기존 클라이언트
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "stop"})
                self.assertEqual(ws.receive_json()["type"], "stopped")
        written = self.written()
        self.assertIn("event=connection.open", written)
        self.assertIn("reason=client_stop", written)
        self.assertIn("code=1000", written)
        traces = {line.split("trace=")[1].split()[0] for line in written.splitlines() if "trace=" in line}
        self.assertEqual(len(traces), 1)
        self.assertEqual(len(traces.pop()), 12)

    def test_invalid_trace_is_replaced_and_valid_trace_is_kept(self):
        with TestClient(self.app) as web:
            with self.connect(web) as ws:
                ws.send_json(dict(self.hello, trace_id="not a trace!"))
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "stop"})
                ws.receive_json()
            with self.connect(web) as ws:
                ws.send_json(dict(self.hello, trace_id="0123456789ab"))
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "stop"})
                ws.receive_json()
        written = self.written()
        self.assertNotIn("not a trace", written)
        self.assertNotIn("not_a_trace", written)
        self.assertIn("trace=0123456789ab", written)

    def test_normal_disconnect_is_recorded_with_a_close_code(self):
        with TestClient(self.app) as web:
            with self.connect(web) as ws:
                ws.send_json(self.hello)
                self.assertEqual(ws.receive_json()["type"], "ready")
            # with 를 벗어나면 클라이언트가 그냥 끊는다. stop 을 보내지 않았다.
        written = self.written()
        close = [line for line in written.splitlines() if "event=connection.close" in line]
        self.assertEqual(len(close), 1)          # 중복 종료는 한 번만
        self.assertIn("reason=disconnect", close[0])
        self.assertIn("code=", close[0])

    def test_protocol_error_is_recorded_as_its_own_reason(self):
        with TestClient(self.app) as web:
            with self.connect(web) as ws:
                ws.send_json({"type": "start", "protocol": 99})
                self.assertEqual(ws.receive_json()["code"], "protocol_error")
        written = self.written()
        # 연결 자체가 서지 않은 경우라 사유만 남고 대화 집계는 없다.
        self.assertNotIn("event=turn.started", written)

    def test_candidate_lifecycle_and_turn_share_one_trace(self):
        with TestClient(self.app) as web, self.connect(web) as ws:
            ws.send_json(dict(self.hello, trace_id="0123456789ab"))
            self.assertEqual(ws.receive_json()["type"], "ready")
            for _ in range(25): ws.send_bytes(SPEECH)
            for _ in range(40): ws.send_bytes(QUIET)
            for _ in range(12):
                event = ws.receive_json()
                if event["type"] in ("response.done", "error"): break
            self.assertEqual(event["type"], "response.done", event)
            ws.send_json({"type": "ping"})
            self.assertEqual(ws.receive_json()["type"], "pong")
            ws.send_json({"type": "stop"})
            ws.receive_json()
        written = self.written()
        for marker in ("event=input.candidate reason=started", "reason=closed",
                       "reason=accepted", "event=turn.started", "event=turn.done"):
            with self.subTest(marker=marker):
                self.assertIn(marker, written)
        self.assertIn("candidate=1", written)
        self.assertNotIn("사용자의 말", written)      # 전사 원문이 남으면 안 된다
        self.assertNotIn("가상 도우미", written)      # 인물 원문도 남으면 안 된다
        for line in written.splitlines():
            self.assertIn("trace=0123456789ab", line)

    def test_accepted_candidate_is_mapped_to_its_response_turn(self):
        """STT 를 어느 응답에 귀속할지 input.assigned 한 줄로 잇는다."""
        with TestClient(self.app) as web, self.connect(web) as ws:
            ws.send_json(dict(self.hello, trace_id="0123456789ab"))
            self.assertEqual(ws.receive_json()["type"], "ready")
            for _ in range(25): ws.send_bytes(SPEECH)
            for _ in range(40): ws.send_bytes(QUIET)
            for _ in range(12):
                event = ws.receive_json()
                if event["type"] in ("response.done", "error"): break
            self.assertEqual(event["type"], "response.done", event)
            turn_id = event["turn_id"]
            ws.send_json({"type": "stop"})
            ws.receive_json()
        lines = self.written().splitlines()

        def only(marker):
            found = [line for line in lines if marker in line]
            self.assertEqual(len(found), 1, marker)
            return dict(pair.split("=", 1) for pair in found[0].split() if "=" in pair)

        assigned = only("event=input.assigned")
        self.assertEqual(assigned["reason"], "accepted")
        self.assertEqual(int(assigned["turn"]), turn_id)
        # 이 turn 의 STT 는 같은 후보 번호의 input.verified 줄에 있다.
        verified = only("event=input.verified")
        self.assertEqual(verified["candidate"], assigned["candidate"])
        self.assertIn("asr_ms", verified)
        # 기존 줄의 위치와 의미는 그대로다. 채택 통지가 매핑보다 앞선다.
        accepted = [i for i, line in enumerate(lines)
                    if "event=input.candidate reason=accepted" in line]
        mapped = [i for i, line in enumerate(lines) if "event=input.assigned" in line]
        started = [i for i, line in enumerate(lines) if "event=turn.started" in line]
        self.assertEqual(len(accepted), 1)
        self.assertLess(accepted[0], mapped[0])
        self.assertLess(mapped[0], started[0])
        self.assertEqual(int(only("event=turn.done")["turn"]), turn_id)


PERSONA = "가상 도우미 인물 설명"
ANSWER = ["첫 문장입니다. ", "둘째 문장입니다."]
REACTION = "음, 잠시만요."


class Voice:
    """가짜 TTS. 문장마다 두 패킷을 보낸다. 실제 합성 품질과 무관하다."""

    def __init__(self):
        self.block = None

    async def stream(self, text):
        for index in range(2):
            if index and self.block is not None:
                await self.block.wait()
            yield base64.b64encode(b"\x00\x10" * 2400).decode("ascii"), 2400


class StageEventTests(unittest.IsolatedAsyncioTestCase):
    """실제 Dialogue 의 응답 경로에서 v4 단계 사건이 나오는지 본다."""

    async def asyncSetUp(self):
        owner = self
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "diagnostics.log"
        configure(str(self.path))
        self.addCleanup(drop_handlers)
        self.events = []
        self.release = asyncio.Event()
        self.generating = asyncio.Event()

        class LLM:
            async def route(self, messages):
                return "reasoning"

            async def stream(self, messages, route):
                owner.generating.set()
                await owner.release.wait()
                for delta in ANSWER:
                    yield delta

        async def emit(event):
            self.events.append(event)

        self.voice = Voice()
        self.diag = ConnectionDiagnostics("0123456789ab")
        self.dialogue = Dialogue(PERSONA, [], None, LLM(), emit, detector=detector(),
                                 tts=self.voice, reactions=ReactionBank(
                                     [ReactionClip(REACTION, b"\x00\x10" * 4800)], delay=.01),
                                 diagnostics=self.diag)

    async def asyncTearDown(self):
        await self.dialogue.close()

    def written(self):
        for handler in logging.getLogger(LOGGER_NAME).handlers:
            handler.flush()
        return self.path.read_text(encoding="utf-8") if self.path.exists() else ""

    def lines(self, marker):
        return [line for line in self.written().splitlines() if marker in line]

    def of(self, kind, audio_kind=None):
        return [e for e in self.events if e["type"] == kind
                and (audio_kind is None or e.get("kind") == audio_kind)]

    async def test_reaction_and_multi_phrase_answer_are_measured_per_phrase(self):
        await self.dialogue.text("여러 조건을 비교해 줘")
        state = self.dialogue.active
        await eventually(lambda: self.of("audio.boundary", "reaction"))
        self.release.set()
        await eventually(lambda: self.of("response.done"))
        self.dialogue.playback({"type": "playback.done", "response_id": state.response_id,
                                "text_chars": state.sent_chars})
        await asyncio.wait_for(state.task, 2)

        self.assertEqual(len(self.lines("event=route.done reason=reasoning")), 1)
        self.assertIn("route_ms=", self.lines("event=route.done")[0])
        first_text = self.lines("event=llm.first_text")
        self.assertEqual(len(first_text), 1)
        # 두 기준을 한 줄에 함께 남긴다. respond 진입 기준과 생성 호출 기준이다.
        self.assertIn("first_text_ms=", first_text[0])
        self.assertIn("llm_first_ms=", first_text[0])
        values = dict(pair.split("=", 1) for pair in first_text[0].split() if "=" in pair)
        self.assertGreaterEqual(float(values["first_text_ms"]), float(values["llm_first_ms"]))
        # turn.timing 의 first_text_sec 와 같은 기준이므로 1000 배 관계다.
        timing_line = self.lines("event=turn.timing")[0]
        timing_fields = dict(pair.split("=", 1) for pair in timing_line.split() if "=" in pair)
        self.assertAlmostEqual(float(timing_fields["first_text_sec"]) * 1000.0,
                               float(values["first_text_ms"]), delta=1.0)
        # 대기 리액션과 본답변 두 문장이 각각 열리고 닫힌다.
        self.assertEqual(len(self.lines("event=tts.phrase_started reason=reaction")), 1)
        self.assertEqual(len(self.lines("event=tts.phrase_done reason=reaction")), 1)
        self.assertEqual(len(self.lines("event=tts.phrase_started reason=answer")), len(ANSWER))
        self.assertEqual(len(self.lines("event=tts.phrase_done reason=answer")), len(ANSWER))
        self.assertEqual(len(self.lines("event=tts.phrase_first_audio")), len(ANSWER) + 1)
        answer_done = self.lines("event=tts.phrase_done reason=answer")
        self.assertIn("phrase=2", answer_done[0])   # 리액션이 1번, 본답변이 2·3번
        self.assertIn("phrase=3", answer_done[1])
        # 두 번째 문장 앞의 150ms 무음도 실제 전송한 오디오로 집계한다.
        for line, packets, samples in zip(answer_done, (2, 3), (4800, 8400)):
            fields = dict(pair.split("=", 1) for pair in line.split() if "=" in pair)
            self.assertEqual(int(fields["packets"]), packets)
            self.assertEqual(int(fields["samples"]), samples)
            self.assertEqual(fields["turn"], "1")
        timing = self.lines("event=turn.timing")
        self.assertEqual(len(timing), 1)
        for field in ("first_text_sec=", "first_audio_sec=", "first_reaction_audio_sec=",
                      "first_any_audio_sec=", "total_sec=", "turn=1"):
            with self.subTest(field=field):
                self.assertIn(field, timing[0])
        ack = self.lines("event=playback.ack")
        self.assertTrue(ack)
        self.assertIn("done=1", ack[-1])
        self.assertIn("samples=%d" % state.audio_samples, ack[-1])
        self.assertIn("chars=%d" % state.sent_chars, ack[-1])

        written = self.written()
        for secret in ("가상 도우미", "여러 조건", "첫 문장", "둘째", "잠시만요",
                       state.response_id, "nan", "inf", "unknown", "None"):
            with self.subTest(secret=secret):
                self.assertNotIn(secret, written)

    async def test_cancelled_phrase_leaves_no_done_and_no_timing(self):
        self.voice.block = asyncio.Event()
        self.release.set()
        await self.dialogue.text("이야기 하나 해줘")
        state = self.dialogue.active
        await eventually(lambda: self.lines("event=tts.phrase_started reason=answer"))
        await self.dialogue.interrupt("reset")

        self.assertTrue(self.lines("event=turn.cancelled"))
        # 끊긴 문장에는 완료 줄이 없다. 없음을 완료나 0 으로 읽지 않는다.
        self.assertFalse(self.lines("event=tts.phrase_done"))
        self.assertFalse(self.lines("event=turn.timing"))
        self.assertFalse(self.of("response.done"))
        self.assertFalse(self.dialogue.live(state))


if __name__ == "__main__":
    unittest.main()
