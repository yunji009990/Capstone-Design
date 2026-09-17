import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_server import Settings, create_app
from realtime_audio import FRAME_BYTES, SAMPLE_RATE, Observation, TurnDetector
from realtime_dialogue import Dialogue, UnityContext, load_persona
from realtime_llm import LLMClient, ModelEndpoint, SpokenTextFilter
from speech_gate import SpeechEvidence

SPEECH = b"\x01\x00" * (FRAME_BYTES // 2)
# TurnDetector 는 말로 보지만 음성 검증기는 기각하는 입력. 실측에서 WebRTC VAD(2)가
# hum·white 를 speech 로 통과시킨 상황을 모의한다.
NOISE = b"\x02\x00" * (FRAME_BYTES // 2)
QUIET = bytes(FRAME_BYTES)


def detector():
    return TurnDetector(is_speech=lambda frame: frame[0] != 0)


class Gate:
    """검사용 음성 검증기. 실제 Silero 가 아니라 SPEECH 표식 프레임만 센다.

    모의 검사 결과를 실제 모델의 잡음 판별 성능으로 읽으면 안 된다.
    """

    def __init__(self, min_speech_ms=150):
        self.min_speech_seconds = min_speech_ms / 1000.0
        self.calls = []

    def settings(self):
        return {"version": "test_gate", "acoustic": "fake",
                "min_speech_ms": round(self.min_speech_seconds * 1000)}

    async def verify(self, pcm):
        self.calls.append(len(pcm))
        voiced = sum(pcm[i] == 1 for i in range(0, len(pcm), FRAME_BYTES))
        seconds = voiced * (FRAME_BYTES / 2) / SAMPLE_RATE
        return SpeechEvidence(seconds, len(pcm) / 2 / SAMPLE_RATE, 1 if voiced else 0,
                              seconds >= self.min_speech_seconds - .001, source="fake")

    def close(self):
        pass


async def eventually(predicate, timeout=1):
    async def wait():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), timeout)


class Frontend:
    async def transcribe(self, pcm):
        return Observation("사용자의 말", audio_event="speech", language="ko")


class FakeLLM:
    def __init__(self):
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.messages = []
        self.block = False

    async def available(self, endpoint=None):
        return True

    async def route(self, messages):
        return "normal"

    async def stream(self, messages, route):
        self.messages = messages
        try:
            yield "먼저 보낸 말."
            self.entered.set()
            if self.block:
                await self.release.wait()
            yield " 마지막 말."
        finally:
            self.cancelled.set()


async def feed(dialogue, frame, count):
    for _ in range(count):
        await dialogue.audio(frame)


class AudioAndContextTests(unittest.TestCase):
    def test_silence_noise_pauses_and_bounded_capture(self):
        d = detector()
        for _ in range(5000):
            self.assertEqual(d.feed(QUIET), [])
        self.assertLessEqual(len(d.pre_roll), 15)
        self.assertEqual(len(d.audio), 0)
        for _ in range(4):
            self.assertEqual(d.feed(SPEECH), [])
        for _ in range(20):
            self.assertEqual(d.feed(QUIET), [])
        events = []
        for _ in range(20): events.extend(d.feed(SPEECH))
        for _ in range(15): events.extend(d.feed(QUIET))  # a pause inside one sentence
        for _ in range(20): events.extend(d.feed(SPEECH))
        for _ in range(40): events.extend(d.feed(QUIET))
        self.assertEqual([e[0] for e in events], ["start", "end"])
        self.assertIn(QUIET * 15, events[-1][1])

    def test_oversized_utterance_is_not_answered_mid_sentence(self):
        d = TurnDetector(is_speech=lambda frame: frame[0] != 0, max_seconds=1)
        events = []
        for _ in range(120): events.extend(d.feed(SPEECH))
        self.assertEqual([e[0] for e in events], ["start", "too_long"])
        self.assertEqual(len(d.audio), 0)
        for _ in range(40): d.feed(QUIET)
        self.assertFalse(d.discarding)
        self.assertTrue(any(d.feed(SPEECH) for _ in range(10)))

    def test_pcm_fragment_boundaries_and_invalid_messages(self):
        d = detector()
        self.assertEqual(d.feed(SPEECH[:200]), [])
        self.assertEqual(len(d.pending), 200)
        d.feed(SPEECH[200:])
        self.assertEqual(len(d.pending), 0)
        for invalid in (b"", b"\x00", bytes(16002)):
            with self.assertRaises(ValueError): d.feed(invalid)

    def test_context_expires_and_actions_are_bounded(self):
        now = [0.0]
        context = UnityContext(clock=lambda: now[0])
        context.update({"kind": "state", "text": "사진을 바라본다"})
        for i in range(100): context.update({"kind": "action", "text": str(i)})
        self.assertEqual(len(context.snapshot()["recent_actions"]), 16)
        now[0] = 6
        self.assertEqual(context.snapshot()["current_state"], "")
        now[0] = 16
        self.assertEqual(context.snapshot()["recent_actions"], [])

    def test_persona_does_not_require_tts_and_rejects_traversal(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "person"
            folder.mkdir()
            (folder / "persona.md").write_text("등록한 인물", encoding="utf-8")
            (folder / "knowledge.md").write_text("함께 꽃을 심었다", encoding="utf-8")
            system, examples = load_persona(root, "person")
            self.assertIn("함께 꽃을 심었다", system)
            self.assertFalse((folder / "voice.wav").exists())
            for invalid in ("..", "../person", "person/..", "person\\..", None, ""):
                with self.assertRaises(ValueError): load_persona(root, invalid)

    def test_thinking_never_leaks_across_arbitrary_sse_boundaries(self):
        for source, expected in (
            ("<think>비공개 추론</think>최종 답변<|im_end|>", "최종 답변"),
            ("<|channel>thought\n비공개 추론<channel|>최종 답변<turn|>", "최종 답변"),
            ("<think>중간에 끊긴 추론", ""),
        ):
            for width in (1, 2, 7, 128):
                f = SpokenTextFilter()
                out = "".join(f.feed(source[i:i + width]) for i in range(0, len(source), width))
                out += f.feed("", final=True)
                self.assertEqual(out, expected)

    def test_less_than_sign_is_preserved_in_spoken_text(self):
        f = SpokenTextFilter()
        self.assertEqual(f.feed("2 <") + f.feed(" 3입니다.") + f.feed("", final=True), "2 < 3입니다.")


class DialogueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.llm = FakeLLM()

        async def emit(event): self.events.append(event)
        self.gate = Gate()
        self.dialogue = Dialogue("인물 설정", [], Frontend(), self.llm, emit,
                                 detector=detector(), partial_seconds=60, speech_gate=self.gate)

    async def asyncTearDown(self):
        await self.dialogue.close()

    async def utterance(self):
        await feed(self.dialogue, SPEECH, 25)
        await feed(self.dialogue, QUIET, 40)
        await self.dialogue.settle_input()

    async def test_voice_and_unity_context_reach_llm_then_stream_to_client(self):
        self.dialogue.context.update({"kind": "action", "text": "사진을 집어 들었다"})
        await self.utterance()
        await eventually(lambda: self.events[-1]["type"] == "response.done")
        types = [e["type"] for e in self.events]
        self.assertLess(types.index("transcript.final"), types.index("response.started"))
        self.assertEqual(types.count("response.delta"), 2)
        self.assertEqual(types[-1], "response.done")
        prompt = self.llm.messages[-1]["content"]
        self.assertIn("사진을 집어 들었다", prompt)
        self.assertIn('"audio_event": "speech"', prompt)
        self.assertNotIn("emotion", prompt)
        self.assertEqual(self.dialogue.history[-1]["content"], "먼저 보낸 말. 마지막 말.")

    async def test_barge_in_cancels_llm_and_retains_only_delivered_prefix(self):
        self.llm.block = True
        await self.utterance()
        await asyncio.wait_for(self.llm.entered.wait(), timeout=1)
        # 확인된 발화만 기존 답변을 멈춘다. 검증을 통과한 다음 발화로 끼어든다.
        await self.utterance()
        self.assertTrue(self.llm.cancelled.is_set())
        self.assertEqual([m["content"] for m in self.dialogue.history
                          if m["role"] == "assistant"], ["먼저 보낸 말."])
        types = [e["type"] for e in self.events]
        cancelled = [e for e in self.events if e["type"] == "response.cancelled"]
        self.assertEqual(cancelled[0]["reason"], "user_speech")
        self.assertLess(types.index("response.cancelled"), types.index("speech.started", 1))
        first_id = cancelled[0]["response_id"]
        self.llm.release.set()
        await asyncio.sleep(0)
        leaked = [e for e in self.events if e.get("response_id") == first_id]
        self.assertNotIn("마지막 말", json.dumps(leaked, ensure_ascii=False))
        self.assertNotIn("response.done", [e["type"] for e in leaked])

    async def test_text_interrupts_response_and_reset_preserves_vad_settings(self):
        self.llm.block = True
        await self.utterance()
        await asyncio.wait_for(self.llm.entered.wait(), timeout=1)
        self.llm.block = False
        await self.dialogue.text("직접 입력한 질문")
        await self.dialogue.active.task
        self.assertIn("response.cancelled", [e["type"] for e in self.events])
        self.assertEqual(self.events[-1]["type"], "response.done")
        final = [e for e in self.events if e["type"] == "transcript.final"][-1]
        self.assertEqual(final["text"], "직접 입력한 질문")
        self.assertNotIn("emotion", final["audio"])
        original_detector = self.dialogue.detector
        original_detector.silence_frames = 42
        await self.dialogue.reset()
        self.assertIs(self.dialogue.detector, original_detector)
        self.assertEqual(self.dialogue.detector.silence_frames, 42)
        self.assertEqual(self.dialogue.history, [])
        self.assertEqual(self.dialogue.context.snapshot()["recent_actions"], [])

    async def test_reset_during_candidate_recognition_discards_the_late_result(self):
        entered, release = asyncio.Event(), asyncio.Event()

        class SlowFrontend:
            async def transcribe(self, pcm):
                entered.set()
                await release.wait()
                return Observation("옛 발화")

        self.dialogue.frontend = SlowFrontend()
        await feed(self.dialogue, SPEECH, 25)
        await feed(self.dialogue, QUIET, 40)
        await asyncio.wait_for(entered.wait(), timeout=1)
        # 검증이 끝나기 전에는 입력 턴도 화면 전사도 만들어지지 않는다.
        self.assertIsNone(self.dialogue.active)
        self.assertNotIn("speech.started", [e["type"] for e in self.events])
        await self.dialogue.reset()
        release.set()
        await self.dialogue.settle_input()
        self.assertEqual(self.dialogue.history, [])
        self.assertIsNone(self.dialogue.active)
        self.assertNotIn("transcript.final", [e["type"] for e in self.events])
        self.assertNotIn("speech.started", [e["type"] for e in self.events])

    async def test_end_experience_cancels_and_does_not_emit_late_events(self):
        self.llm.block = True
        await self.utterance()
        await asyncio.wait_for(self.llm.entered.wait(), timeout=1)
        await self.dialogue.close()
        count = len(self.events)
        self.llm.release.set()
        await feed(self.dialogue, SPEECH, 30)
        self.assertEqual(len(self.events), count)
        self.assertEqual(self.dialogue.history, [])


class LLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_reasoning_endpoint_and_separated_reasoning_output(self):
        requests = []

        def handler(request):
            data = json.loads(request.content)
            requests.append((str(request.url), data))
            if not data["stream"]:
                return httpx.Response(200, json={"choices": [{"message": {"content": '{"route":"reasoning"}'}}]})
            packets = [
                {"choices": [{"delta": {"reasoning_content": "절대 표시하면 안 되는 추론"}}]},
                {"choices": [{"delta": {"content": "답변입니다."}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
            body = "".join("data: " + json.dumps(x) + "\n\n" for x in packets) + "data: [DONE]\n\n"
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            llm = LLMClient(ModelEndpoint("http://normal/chat/completions", "normal"),
                            ModelEndpoint("http://reason/chat/completions", "reason"), client=http)
            messages = [{"role": "system", "content": "인물 설정"}, {"role": "user", "content": "계산해줘"}]
            route = await llm.route(messages)
            answer = "".join([x async for x in llm.stream(messages, route)])
        self.assertEqual(answer, "답변입니다.")
        self.assertEqual(requests[1][0], "http://reason/chat/completions")
        self.assertEqual(sum(m["role"] == "system" for m in requests[0][1]["messages"]), 1)

    async def test_truncated_stream_is_not_reported_as_success(self):
        def handler(request):
            payload = {"choices": [{"delta": {"content": "반쪽 답변"}}]}
            return httpx.Response(200, text="data: " + json.dumps(payload) + "\n\n")
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            llm = LLMClient(ModelEndpoint("http://llm/chat/completions", "model"), client=http)
            with self.assertRaises(RuntimeError):
                _ = [x async for x in llm.stream([])]


class WebSocketTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name) / "person"
        folder.mkdir()
        (folder / "persona.md").write_text("인물 설정", encoding="utf-8")
        self.settings = Settings(self.tmp.name, "unused", token="test-token")
        class WireLLM:
            async def available(self, endpoint=None): return True
            async def route(self, messages): return "normal"
            async def stream(self, messages, route):
                yield "먼저 보낸 말."
                yield " 마지막 말."
        self.app = create_app(self.settings, frontend=Frontend(), llm=WireLLM(),
                              detector_factory=detector, speech_gate=Gate())
        self.hello = {"type": "start", "protocol": 1, "session": "person",
                      "sample_rate": 16000, "channels": 1, "format": "pcm_s16le"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_real_websocket_protocol_audio_context_answer_and_stop(self):
        with TestClient(self.app) as client:
            with client.websocket_connect("/dialogue", headers={"X-Token": "test-token"}) as ws:
                ws.send_json(self.hello)
                self.assertEqual(ws.receive_json()["type"], "ready")
                ws.send_json({"type": "context", "kind": "action", "text": "사진을 집어 들었다"})
                for _ in range(25): ws.send_bytes(SPEECH)
                for _ in range(40): ws.send_bytes(QUIET)
                events = []
                for _ in range(10):
                    event = ws.receive_json()
                    events.append(event)
                    if event["type"] == "response.done": break
                self.assertEqual(events[-1]["type"], "response.done")
                self.assertEqual(events[-1]["text"], "먼저 보낸 말. 마지막 말.")
                ws.send_json({"type": "stop"})
                self.assertEqual(ws.receive_json()["type"], "stopped")

    def test_auth_format_and_busy_connection_are_rejected(self):
        with TestClient(self.app) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/dialogue"): pass
            with client.websocket_connect("/dialogue", headers={"X-Token": "test-token"}) as first:
                first.send_json(self.hello)
                first.receive_json()
                with client.websocket_connect("/dialogue", headers={"X-Token": "test-token"}) as second:
                    second.send_json(self.hello)
                    self.assertEqual(second.receive_json()["code"], "server_busy")
            with client.websocket_connect("/dialogue", headers={"X-Token": "test-token"}) as ws:
                ws.send_bytes(SPEECH)
                self.assertEqual(ws.receive_json()["code"], "protocol_error")


class TestSceneProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.messages = []
        messages = self.messages

        class TextOnlyFrontend:
            async def transcribe(self, pcm):
                raise AssertionError("typed test input must not invoke STT")

        class CapturingLLM:
            async def available(self, endpoint=None): return True
            async def route(self, input): return "normal"
            async def stream(self, input, route):
                messages.append(input)
                yield "테스트 답변"

        self.settings = Settings(self.tmp.name, "unused", token="test-token", allow_test_mode=True)
        self.frontend, self.llm = TextOnlyFrontend(), CapturingLLM()
        self.app = create_app(self.settings, frontend=self.frontend, llm=self.llm, speech_gate=Gate())
        self.hello = {"type": "start", "protocol": 1, "test_mode": True,
                      "test_persona": "임시 테스트 도우미", "sample_rate": 16000,
                      "channels": 1, "format": "pcm_s16le"}

    def tearDown(self):
        self.tmp.cleanup()

    def connect(self, client):
        return client.websocket_connect("/dialogue", headers={"X-Token": "test-token"})

    def answer(self, ws, text):
        ws.send_json({"type": "text", "text": text})
        events = []
        for _ in range(10):
            event = ws.receive_json()
            events.append(event)
            if event["type"] in ("response.done", "error"): break
        self.assertEqual(events[-1]["type"], "response.done")
        return events

    def test_no_registration_typed_context_history_reset_and_no_disk_writes(self):
        with TestClient(self.app) as client:
            self.assertTrue(client.get("/health").json()["test_mode_available"])
            with self.connect(client) as ws:
                ws.send_json(self.hello)
                self.assertTrue(ws.receive_json()["test_mode"])
                ws.send_json({"type": "context", "kind": "action", "text": "사진을 집어 들었다"})
                events = self.answer(ws, "첫 질문")
                final = next(e for e in events if e["type"] == "transcript.final")
                self.assertNotIn("emotion", final["audio"])
                self.assertEqual(final["audio"]["audio_event"], "text")
                self.assertIn("임시 테스트 도우미", self.messages[-1][0]["content"])
                self.assertIn("사진을 집어 들었다", self.messages[-1][-1]["content"])
                self.answer(ws, "두 번째 질문")
                self.assertEqual(len(self.messages[-1]), 4)
                ws.send_json({"type": "reset"})
                self.assertEqual(ws.receive_json()["type"], "reset.done")
                self.answer(ws, "새 대화")
                self.assertEqual(len(self.messages[-1]), 2)
                self.assertNotIn("사진을 집어 들었다", self.messages[-1][-1]["content"])
                ws.send_json({"type": "ping"})
                self.assertEqual(ws.receive_json()["type"], "pong")
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_test_mode_requires_auth_and_explicit_server_enable(self):
        with TestClient(self.app) as client:
            with self.assertRaises(WebSocketDisconnect):
                with client.websocket_connect("/dialogue"): pass
        self.settings.allow_test_mode = False
        with TestClient(self.app) as client:
            with self.connect(client) as ws:
                ws.send_json(self.hello)
                self.assertEqual(ws.receive_json()["code"], "protocol_error")

    def test_invalid_test_profile_and_text_are_rejected(self):
        with TestClient(self.app) as client:
            for invalid in ("", " " * 5, None, ["text"], "x" * 3001):
                with self.subTest(profile=type(invalid).__name__), self.connect(client) as ws:
                    ws.send_json(dict(self.hello, test_persona=invalid))
                    self.assertEqual(ws.receive_json()["code"], "protocol_error")
            for invalid in (None, "", "x" * 2001):
                with self.connect(client) as ws:
                    ws.send_json(self.hello)
                    self.assertEqual(ws.receive_json()["type"], "ready")
                    ws.send_json({"type": "text", "text": invalid})
                    self.assertEqual(ws.receive_json()["code"], "protocol_error")

    def test_regular_session_still_requires_registration(self):
        with TestClient(self.app) as client:
            with self.connect(client) as ws:
                ws.send_json(dict(self.hello, test_mode=False, session="missing"))
                self.assertEqual(ws.receive_json()["code"], "protocol_error")


if __name__ == "__main__":
    unittest.main()
