"""끼어들기로 답변을 바꿀 때의 멈춤 경계 계약.

합성 PCM 과 가짜 모델만 쓴다. 실제 음성 품질이나 사람의 청취 판정이 아니다.
"""
import asyncio
import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_diagnostics import ConnectionDiagnostics
from dialogue_server import Settings, create_app
from interruption_policy import TurnDecision
from realtime_dialogue import PAUSE_GRACE_DEFAULT_MS, Dialogue
from test_dialogue import Frontend, Gate, detector, eventually

# 한 델타에 두 구절이 들어 있다. 둘째 구절은 큐에서 대기하므로 "보류 중에는 다음
# 구절을 시작하지 않는다"를 실제 경로로 확인할 수 있다.
FIRST = "첫 안내입니다. "
REST = "두 번째 안내입니다. "
PACKETS = 4


class SlowTTS:
    """한 구절을 여러 조각으로 보낸다. 지정한 조각에서 문을 잡아 두면 멈춤 요청이
    구절 한가운데에 도착하는 상황을 그대로 만들 수 있다."""

    def __init__(self, hold_index=1):
        self.calls = []
        self.hold_index = hold_index
        self.reached_hold = asyncio.Event()
        self.release = asyncio.Event()

    async def stream(self, text):
        self.calls.append(text)
        for index in range(PACKETS):
            if index == self.hold_index:
                self.reached_hold.set()
                await self.release.wait()
            yield base64.b64encode(bytes([1, 0]) * 2400).decode(), 2400


class PauseHarness:
    """한 답변을 구절 한가운데까지 보낸 뒤 새 입력으로 멈춤을 요청하는 공통 무대."""

    hold_index = 1          # 몇 번째 조각에서 멈춤 요청을 받을 것인가
    pause_ack = True        # 멈춘 지점을 보고할 수 있는 클라이언트인가

    async def asyncSetUp(self):
        self.events = []
        self.judged = asyncio.Event()
        self.judge_release = asyncio.Event()
        self.decisions = []
        self.streams = 0
        self.stream_messages = []
        owner = self

        class LLM:
            async def route(self, messages):
                return "normal"

            async def decide_interruption(self, messages, pending):
                owner.judged.set()
                await owner.judge_release.wait()
                return owner.decisions.pop(0) if owner.decisions else TurnDecision("switch")

            async def stream(self, messages, route):
                owner.streams += 1
                owner.stream_messages.append(messages)
                yield FIRST + REST

        async def emit(event):
            self.events.append(event)

        self.tts = SlowTTS(self.hold_index)
        self.diagnostics = ConnectionDiagnostics("0123456789ab")
        self.dialogue = Dialogue("인물", [], Frontend(), LLM(), emit, detector=detector(),
                                 tts=self.tts, partial_seconds=60, speech_gate=Gate(),
                                 diagnostics=self.diagnostics, pause_ack=self.pause_ack)
        self.dialogue.semantic_interruptions = True

    async def asyncTearDown(self):
        self.judge_release.set()
        self.tts.release.set()
        await self.dialogue.close()

    def events_of(self, kind):
        return [e for e in self.events if e["type"] == kind]

    def counts(self, name):
        return self.diagnostics.counts[name]

    async def begin(self):
        """첫 답변이 구절 한가운데까지 나간 상태에서 새 입력으로 멈추게 한다."""
        await self.dialogue.text("산책 준비를 설명해 줘")
        await asyncio.wait_for(self.tts.reached_hold.wait(), 1)
        old = self.dialogue.active
        await self.dialogue.text("그건 됐고 고양이를 설명해 줘")
        await eventually(lambda: self.events_of("response.paused"))
        await asyncio.wait_for(self.judged.wait(), 1)
        return old

    def ack(self, state, *, pause_id=None, reason="boundary", chars=None):
        """클라이언트가 보내는 playback.paused 한 통."""
        self.dialogue.playback({
            "type": "playback.paused", "response_id": state.response_id,
            "pause_id": state.pause_id if pause_id is None else pause_id,
            "reason": reason,
            "text_chars": state.sent_chars if chars is None else chars})


class PauseBoundaryTests(PauseHarness, unittest.IsolatedAsyncioTestCase):
    async def test_revised_prompt_includes_the_phrase_completed_while_judging(self):
        self.decisions.append(TurnDecision("revise"))
        old = await self.begin()
        self.assertEqual(old.spoken_chars, 0)
        self.tts.release.set()
        await eventually(lambda: self.events_of("audio.boundary"))
        self.judge_release.set()
        await eventually(lambda: self.events_of("interruption.decision"))
        self.ack(old)
        expected_spoken = old.text[:old.spoken_chars]
        self.assertTrue(expected_spoken.strip())
        await eventually(lambda: self.streams == 2)
        prompt = self.stream_messages[1][0]["content"]
        # JSON으로 전달된 청취 범위가 초기(빈 값)가 아니라 최종 ACK 위치여야 한다.
        self.assertIn(json.dumps("spoken_text") + ": " + json.dumps(expected_spoken, ensure_ascii=False), prompt)
        self.assertIn(expected_spoken.strip(), " ".join(
            m["content"] for m in self.stream_messages[1] if m["role"] == "assistant"))

    async def test_ack_during_a_phrase_freezes_further_packets_until_resume(self):
        old = await self.begin()
        self.ack(old, chars=0, reason="grace_expired")
        self.tts.release.set()
        await asyncio.sleep(.03)
        self.assertEqual(len(self.events_of("response.audio")), 1)
        self.assertFalse(old.finish_phrase)
        self.assertEqual(self.counts("pause_cut"), 1)

    async def test_expired_grace_stops_phrase_flush_even_while_judge_is_pending(self):
        self.dialogue.pause_grace_ms = 0
        self.dialogue.pause_margin = .01
        await self.begin()
        await asyncio.sleep(.03)
        self.tts.release.set()
        await asyncio.sleep(.03)
        self.assertEqual(len(self.events_of("response.audio")), 1)
        self.assertFalse(self.events_of("response.cancelled"))

    async def test_natural_playback_completion_satisfies_the_wait_without_timeout(self):
        self.tts.release.set()
        await self.dialogue.text("산책 준비를 설명해 줘")
        await eventually(lambda: self.events_of("response.done"))
        old = self.dialogue.active
        await self.dialogue.text("다른 이야기를 해 줘")
        self.judge_release.set()
        await eventually(lambda: self.events_of("interruption.decision"))
        self.dialogue.playback({"type": "playback.done", "response_id": old.response_id,
                                "text_chars": old.sent_chars})
        await eventually(lambda: self.streams == 2)
        self.assertEqual(self.counts("pause_timeout"), 0)
        self.assertFalse([e for e in self.events_of("response.paused")
                          if e.get("pause_mode") == "immediate"])

    async def test_pause_event_declares_the_round_grace_and_phrase_mode(self):
        old = await self.begin()
        paused = self.events_of("response.paused")
        self.assertEqual(len(paused), 1)
        self.assertEqual(paused[0]["response_id"], old.response_id)
        self.assertGreater(paused[0]["pause_id"], 0)
        self.assertEqual(paused[0]["grace_ms"], PAUSE_GRACE_DEFAULT_MS)
        self.assertEqual(paused[0]["pause_mode"], "phrase")

    async def test_in_flight_phrase_finishes_but_no_next_phrase_starts(self):
        old = await self.begin()
        self.assertEqual(len(self.events_of("response.audio")), 1)   # 구절 한가운데다
        self.assertFalse(old.running.is_set())
        self.tts.release.set()
        # 진행 중인 구절은 끝까지 나가고 그 경계까지 알린다.
        await eventually(lambda: self.events_of("audio.boundary"))
        self.assertEqual(len(self.events_of("response.audio")), PACKETS)
        boundary = self.events_of("audio.boundary")[0]
        self.assertEqual(boundary["response_id"], old.response_id)
        self.assertEqual(boundary["text_chars"], old.sent_chars)
        # 둘째 구절은 큐에 있지만 보류 중에는 시작하지 않는다.
        await asyncio.sleep(.05)
        self.assertEqual(self.tts.calls, [FIRST.strip()])
        self.assertEqual(len(self.events_of("response.audio")), PACKETS)

    async def test_new_answer_waits_for_the_matching_report(self):
        old = await self.begin()
        self.tts.release.set()
        await eventually(lambda: self.events_of("audio.boundary"))
        self.judge_release.set()
        await eventually(lambda: self.events_of("interruption.decision"))
        await asyncio.sleep(.05)
        # 보고 전에는 취소도 새 생성도 없다. 구절 꼬리가 잘리지 않는다.
        self.assertFalse(self.events_of("response.cancelled"))
        self.assertEqual(self.streams, 1)
        self.ack(old)
        self.assertEqual(old.spoken_chars, old.sent_chars)
        await eventually(lambda: self.events_of("response.cancelled"))
        await eventually(lambda: self.streams == 2)
        self.assertEqual(self.counts("pause_boundary"), 1)

    async def test_report_from_an_earlier_round_cannot_release_a_later_pause(self):
        self.decisions.append(TurnDecision("resume"))
        old = await self.begin()
        stale_id = old.pause_id
        self.tts.release.set()
        self.judge_release.set()
        await eventually(lambda: self.events_of("response.resumed"))
        self.assertEqual(old.pause_id, 0)
        self.judge_release.clear()          # 다음 판정은 검사가 직접 연다
        self.judged.clear()
        await self.dialogue.text("잠깐, 다른 걸 물어볼게")
        self.assertEqual(len(self.events_of("response.paused")), 2)
        self.assertGreater(old.pause_id, stale_id)
        spoken = old.spoken_chars
        # 재개 뒤에 도착한 이전 회차의 보고. 위치가 유효한 값이어도 쓰지 않는다.
        self.ack(old, pause_id=stale_id)
        self.assertFalse(old.paused_ack.is_set())
        self.assertEqual(old.spoken_chars, spoken)
        self.assertEqual(self.counts("pause_stale"), 1)
        self.assertEqual(self.counts("pause_boundary"), 0)
        await asyncio.sleep(.02)
        self.assertFalse(self.events_of("response.cancelled"))

    async def test_duplicate_report_is_counted_once_and_cannot_move_the_position_back(self):
        old = await self.begin()
        self.tts.release.set()
        await eventually(lambda: self.events_of("audio.boundary"))
        self.ack(old)
        self.assertTrue(old.paused_ack.is_set())
        position = old.spoken_chars
        self.assertEqual(position, old.sent_chars)
        self.ack(old, reason="grace_expired", chars=0)
        self.assertEqual(old.spoken_chars, position)
        self.assertEqual(self.counts("pause_boundary"), 1)
        self.assertEqual(self.counts("pause_cut"), 0)

    async def test_missing_report_times_out_into_an_immediate_pause(self):
        self.dialogue.pause_grace_ms = 40
        self.dialogue.pause_margin = .05
        old = await self.begin()
        self.tts.release.set()
        await eventually(lambda: self.events_of("audio.boundary"))
        self.judge_release.set()
        await eventually(lambda: self.events_of("response.cancelled"), timeout=2)
        paused = self.events_of("response.paused")
        self.assertEqual(len(paused), 2)
        self.assertEqual(paused[1]["pause_id"], paused[0]["pause_id"])
        self.assertEqual(paused[1]["pause_mode"], "immediate")
        self.assertEqual(paused[1]["grace_ms"], 0)
        self.assertFalse(old.paused_ack.is_set())
        self.assertEqual(self.counts("pause_timeout"), 1)
        await eventually(lambda: self.streams == 2)

    async def test_hold_cuts_the_current_phrase_without_a_new_round(self):
        self.decisions.append(TurnDecision("hold"))
        old = await self.begin()
        self.judge_release.set()
        await eventually(lambda: len(self.events_of("response.paused")) == 2)
        paused = self.events_of("response.paused")
        self.assertEqual(paused[1]["pause_id"], paused[0]["pause_id"])
        self.assertEqual(paused[1]["pause_mode"], "immediate")
        self.assertEqual(paused[1]["grace_ms"], 0)
        self.assertFalse(old.finish_phrase)
        self.assertTrue(old.hold_requested)
        self.assertIs(self.dialogue.held, old)
        # 남은 조각을 풀어도 즉시 멈춤 뒤에는 더 나가지 않는다.
        self.tts.release.set()
        await asyncio.sleep(.05)
        self.assertEqual(len(self.events_of("response.audio")), 1)
        self.assertFalse(self.events_of("audio.boundary"))
        self.assertFalse(self.events_of("response.cancelled"))

    async def test_reset_while_waiting_for_a_report_stops_at_once(self):
        old = await self.begin()
        self.tts.release.set()
        await eventually(lambda: self.events_of("audio.boundary"))
        self.judge_release.set()
        await eventually(lambda: self.events_of("interruption.decision"))
        await asyncio.sleep(.02)
        await asyncio.wait_for(self.dialogue.reset(), 1)
        self.assertIsNone(self.dialogue.active)
        self.assertIsNone(self.dialogue.held)
        self.assertEqual(old.pause_id, 0)
        self.assertEqual(self.streams, 1)       # 교체 답변은 만들어지지 않았다
        self.assertEqual([e["reason"] for e in self.events_of("response.cancelled")], ["reset"])

    async def test_close_while_waiting_releases_both_tasks(self):
        old = await self.begin()
        self.tts.release.set()
        await eventually(lambda: self.events_of("audio.boundary"))
        self.judge_release.set()
        await eventually(lambda: self.events_of("interruption.decision"))
        waiting = self.dialogue.active.task
        await asyncio.wait_for(self.dialogue.close(), 1)
        self.assertTrue(waiting.done() and old.task.done())
        self.assertEqual(self.streams, 1)


class NoAudioPauseTests(PauseHarness, unittest.IsolatedAsyncioTestCase):
    hold_index = 0          # 첫 조각을 내보내기 전에 멈춤 요청이 온다

    async def test_answer_that_was_never_heard_does_not_wait_for_a_report(self):
        old = await self.begin()
        self.assertEqual(old.audio_samples, 0)
        self.assertFalse(self.events_of("response.audio"))
        self.judge_release.set()
        # 기한(1.2초 + 여유)보다 훨씬 이르다. 보고를 기다리지 않았다는 뜻이다.
        await eventually(lambda: self.events_of("response.cancelled"), timeout=.5)
        await eventually(lambda: self.streams == 2, timeout=.5)
        self.assertFalse(old.paused_ack.is_set())


class LegacyClientTests(PauseHarness, unittest.IsolatedAsyncioTestCase):
    pause_ack = False       # 멈춘 지점을 보고하지 않는 기존 클라이언트

    async def test_legacy_client_keeps_the_per_packet_pause_and_no_waiting(self):
        old = await self.begin()
        paused = self.events_of("response.paused")[0]
        for key in ("pause_id", "grace_ms", "pause_mode"):
            self.assertNotIn(key, paused)
        self.assertEqual(old.pause_id, 0)
        self.assertFalse(old.finish_phrase)
        # 조각을 풀어도 보류 중에는 더 나가지 않는다. 지금까지의 동작 그대로다.
        self.tts.release.set()
        await asyncio.sleep(.05)
        self.assertEqual(len(self.events_of("response.audio")), 1)
        self.assertFalse(self.events_of("audio.boundary"))
        self.judge_release.set()
        await eventually(lambda: self.events_of("response.cancelled"), timeout=.5)
        await eventually(lambda: self.streams == 2, timeout=.5)


class HelloLLM:
    async def available(self, endpoint=None):
        return True

    async def route(self, messages):
        return "normal"

    async def stream(self, messages, route):
        yield "짧은 답변입니다."


class HelloContractTests(unittest.TestCase):
    """hello 의 능력 선언과 ready·health 가 알리는 계약. 모델 없이 프로토콜만 본다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        settings = Settings(self.tmp.name, "unused", token="test-token", allow_test_mode=True,
                            system_lines_enabled=False)
        self.app = create_app(settings, frontend=Frontend(), llm=HelloLLM(),
                              detector_factory=detector, speech_gate=Gate())
        self.hello = {"type": "start", "protocol": 1, "test_mode": True,
                      "test_persona": "가상 도우미", "sample_rate": 16000,
                      "channels": 1, "format": "pcm_s16le"}

    def connect(self, web):
        return web.websocket_connect("/dialogue", headers={"X-Token": "test-token"})

    def test_opt_in_client_is_advertised_and_its_report_type_is_accepted(self):
        with TestClient(self.app) as web:
            contract = web.get("/health").json()["pause_boundary"]
            self.assertEqual(contract["grace_ms"], PAUSE_GRACE_DEFAULT_MS)
            self.assertEqual(contract["ack_event"], "playback.paused")
            self.assertEqual(contract["modes"], ["phrase", "immediate"])
            with self.connect(web) as ws:
                ws.send_json(dict(self.hello, pause_ack=True))
                ready = ws.receive_json()
                self.assertEqual(ready["type"], "ready")
                self.assertTrue(ready["pause_boundary"]["enabled"])
                self.assertEqual(ready["pause_boundary"]["grace_ms"], PAUSE_GRACE_DEFAULT_MS)
                # 끝난 응답의 늦은 보고는 조용히 버린다. 연결은 그대로 살아 있다.
                ws.send_json({"type": "playback.paused", "response_id": "gone",
                              "pause_id": 1, "text_chars": 0, "reason": "boundary"})
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")

    def test_client_without_the_declaration_is_not_advertised_as_reporting(self):
        with TestClient(self.app) as web:
            with self.connect(web) as ws:
                ws.send_json(self.hello)
                ready = ws.receive_json()
                self.assertEqual(ready["type"], "ready")
                self.assertFalse(ready["pause_boundary"]["enabled"])


if __name__ == "__main__":
    unittest.main()
