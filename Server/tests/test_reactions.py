"""사전 준비·캐시 갱신과 실제 대화의 재생/취소/기억 계약을 검사한다."""
import asyncio
import base64
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_memory import SessionMemory
from dialogue_reactions import (MAX_PCM, ReactionBank, ReactionCache, ReactionClip,
                                validate_lines)
from dialogue_server import Settings, create_app
from interruption_policy import TurnDecision
from realtime_audio import Observation
from realtime_dialogue import Dialogue
from realtime_llm import LLMClient, ModelEndpoint
from voice_reference import Reference
from test_dialogue import detector
from test_interruption import eventually
from test_references import wav_bytes

LINES = ["음, 잠시만요.", "조금 생각해 볼게요.", "흠, 잠깐만요.",
         "잠시 생각해 볼게요.", "음… 생각 중이에요.", "잠깐만 기다려 주세요."]
PCM = b"\x00\x10" * 12000
ANSWER = "계산 결과는 42예요."


class PreparedVoice:
    def __init__(self):
        self.calls = []
        self.closed = 0
        self.releases = 0
        self.block = None
        self.long_first = False

    async def stream(self, text):
        self.calls.append(text)
        try:
            if self.block is not None:
                await self.block.wait()
            pcm = b"\x00\x10" * ((MAX_PCM + 9600) // 2) if self.long_first and text == LINES[0] else PCM
            for offset in range(0, len(pcm), 9600):
                raw = pcm[offset:offset + 9600]
                yield base64.b64encode(raw).decode(), len(raw) // 2
        finally:
            self.closed += 1

    async def release(self):
        self.releases += 1


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "cache"
        self.cache = ReactionCache(self.root)
        self.voice = PreparedVoice()
        self.generated = []
        self.reference = Reference(b"\x01\x02" * 64000, 16000, "public fixture", 4, 0, "참조 전사")

    async def generate(self, profile):
        self.generated.append(profile)
        return {"reactions": LINES}

    async def prepare(self, profile="차분한 존댓말", text="참조 전사", persistent=True, cache=None):
        return await (cache or self.cache).prepare(profile, "fixture", self.reference, text,
            self.voice, self.generate, generator_identity="gemma-test", synthesis_identity="qwen-test",
            persistent=persistent)

    async def test_reconnect_restart_voice_and_persona_invalidation(self):
        first, info = await self.prepare()
        self.assertFalse(info["audio_cache_hit"])
        self.assertEqual(len(first.clips), 6)
        self.assertEqual(len(self.generated), 1)
        self.assertEqual(len(self.voice.calls), 6)
        _, warm = await self.prepare()
        self.assertTrue(warm["lines_cache_hit"] and warm["audio_cache_hit"])
        _, restarted = await self.prepare(cache=ReactionCache(self.root))
        self.assertTrue(restarted["audio_cache_hit"])
        self.assertEqual(len(self.voice.calls), 6)
        # 참조 전사/목소리만 바뀌면 기존 페르소나 대사는 재사용한다.
        _, changed = await self.prepare(text="수정한 전사")
        self.assertTrue(changed["lines_cache_hit"])
        self.assertFalse(changed["audio_cache_hit"])
        self.assertEqual(len(self.generated), 1)
        self.reference.pcm = b"\x03\x04" * 64000
        _, changed_voice = await self.prepare(text="수정한 전사")
        self.assertFalse(changed_voice["audio_cache_hit"])
        self.assertEqual(len(self.generated), 1)
        await self.prepare(profile="친근한 반말")
        self.assertEqual(len(self.generated), 2)
        if os.name != "nt":
            self.assertEqual(next((self.root / "audio").glob("*.json")).stat().st_mode & 0o777, 0o600)

    async def test_temporary_persona_stays_in_ram_and_recent_choices_are_separate(self):
        one, _ = await self.prepare(persistent=False)
        two, info = await self.prepare(persistent=False)
        self.assertTrue(info["audio_cache_hit"])
        self.assertFalse(self.root.exists())
        one.choose = two.choose = lambda items: items[0]
        chosen = [one.take().text for _ in range(3)]
        self.assertEqual(len(set(chosen)), 3)
        self.assertEqual(two.take().text, chosen[0])
        self.assertNotIn(one.take().text, chosen[-2:])

    async def test_long_clip_is_discarded_whole_and_corrupt_cache_is_rebuilt(self):
        self.voice.long_first = True
        bank, _ = await self.prepare()
        self.assertEqual(len(bank.clips), 5)
        self.assertNotIn(LINES[0], [clip.text for clip in bank.clips])
        path = next((self.root / "audio").glob("*.json"))
        value = json.loads(path.read_text(encoding="utf-8"))
        value["data"][0]["sha256"] = "wrong"
        path.write_text(json.dumps(value), encoding="utf-8")
        calls = len(self.voice.calls)
        _, info = await self.prepare(cache=ReactionCache(self.root))
        self.assertFalse(info["audio_cache_hit"])
        self.assertGreater(len(self.voice.calls), calls)

    async def test_cancelled_preparation_closes_stream_and_keeps_no_partial_audio(self):
        self.voice.block = asyncio.Event()
        task = asyncio.create_task(self.prepare())
        await eventually(lambda: self.voice.calls)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.voice.closed, 1)
        self.assertFalse(self.cache.audio)
        self.voice.block = None
        _, info = await self.prepare()
        self.assertTrue(info["lines_cache_hit"])
        self.assertFalse(info["audio_cache_hit"])

    async def test_disk_and_memory_capacity_are_bounded(self):
        self.cache.capacity = 2
        for i in range(3):
            await self.prepare(profile="차분한 인물 " + str(i))
        for kind in ("lines", "audio"):
            self.assertEqual(len(getattr(self.cache, kind)), 2)
            self.assertEqual(len(list((self.root / kind).glob("*.json"))), 2)

    def test_incomplete_or_non_generic_lines_are_rejected(self):
        for value in ({"reactions": LINES[:2]}, {"reactions": [LINES[0]] * 6},
                      {"reactions": LINES[:5] + ["잠시만요. 검색해서 확인했어요."]},
                      {"reactions": LINES[:5] + ["음, 생각" * 20]},
                      {"reactions": LINES[:5] + ["<think>잠시만요</think>"]}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_lines(value)

    def test_generated_questions_are_removed_without_another_model_call(self):
        result = validate_lines({"reactions": LINES + ["잠시만 기다려 주시겠어요?",
                                                       "어떻게 말씀드리면 좋을까요."]}, select=True)
        self.assertEqual(result, tuple(LINES))
        with self.assertRaises(ValueError):
            validate_lines({"reactions": ["잠시만요?"] * 8}, select=True)

    async def test_gemma_preparation_uses_non_thinking_json_and_only_given_profile(self):
        requests = []
        def transport(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({"reactions": LINES}, ensure_ascii=False)}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            endpoint = ModelEndpoint("http://llm/v1/chat/completions", "gemma", extra={
                "chat_template_kwargs": {"enable_thinking": True}})
            llm = LLMClient(endpoint, client=client)
            result = await llm.generate_reactions("말이 짧고 존댓말을 사용한다")
        self.assertEqual(validate_lines(result), tuple(LINES))
        self.assertFalse(requests[0]["chat_template_kwargs"]["enable_thinking"])
        self.assertEqual(len(requests[0]["messages"]), 2)
        self.assertEqual(json.loads(requests[0]["messages"][1]["content"]),
                         {"profile": "말이 짧고 존댓말을 사용한다"})


class DialogueReactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        owner = self
        self.events = []
        self.packet_delay = 0
        self.route = "reasoning"
        self.decision = TurnDecision("resume")
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.failure = False
        self.answer_calls = 0
        class LLM:
            async def route(self, messages): return owner.route
            async def decide_interruption(self, messages, pending):
                owner.pending = pending
                return owner.decision
            async def stream(self, messages, route):
                owner.answer_calls += 1
                owner.started.set()
                try:
                    await owner.release.wait()
                    if owner.failure:
                        raise RuntimeError("test model failure")
                    yield ANSWER
                finally:
                    owner.closed.set()
        async def emit(event):
            self.events.append(event)
            if event["type"] == "response.audio" and event.get("kind") == "reaction":
                await asyncio.sleep(self.packet_delay)
        async def memory_extract(payload): return {"upserts": [], "summary_ids": []}
        self.memory = SessionMemory(memory_extract, batch_users=100)
        self.voice = PreparedVoice()
        self.bank = ReactionBank([ReactionClip(text, PCM) for text in LINES], delay=.015)
        self.dialogue = Dialogue("차분한 존댓말", [], None, LLM(), emit, detector=detector(),
                                 tts=self.voice, reactions=self.bank, memory=self.memory)

    async def asyncTearDown(self):
        await self.dialogue.close()

    def of(self, kind, audio_kind=None):
        return [e for e in self.events if e["type"] == kind and
                (audio_kind is None or e.get("kind") == audio_kind)]

    async def finish(self, state):
        self.release.set()
        await eventually(lambda: any(e["response_id"] == state.response_id for e in self.of("response.done")))
        self.dialogue.playback({"type": "playback.done", "response_id": state.response_id,
                                "text_chars": state.sent_chars})
        await asyncio.wait_for(state.task, 1)

    async def test_reaction_arrives_during_inference_and_does_not_enter_memory(self):
        await self.dialogue.text("여러 조건을 비교해 줘")
        state = self.dialogue.active
        await asyncio.wait_for(self.started.wait(), 1)
        await eventually(lambda: self.of("audio.boundary", "reaction"))
        self.assertFalse(self.release.is_set())
        self.assertEqual(self.voice.calls, [])  # 대화 중 리액션 합성 호출 0회
        boundary = self.of("audio.boundary", "reaction")[0]
        self.dialogue.playback({"type": "playback.progress", "response_id": state.response_id,
                                "text_chars": boundary["text_chars"]})
        self.dialogue.record(state, final=False)
        self.assertFalse([m for m in self.dialogue.history if m["role"] == "assistant"])
        await self.finish(state)
        answer = self.of("response.done")[0]
        self.assertTrue(answer["text"].endswith(ANSWER))
        # Windows Python 3.9의 monotonic은 같은 tick을 반환할 수 있다.
        self.assertLessEqual(answer["timing"]["first_reaction_audio_sec"], answer["timing"]["first_audio_sec"])
        self.assertLess(self.events.index(self.of("response.audio", "reaction")[0]),
                        self.events.index(self.of("response.audio", "answer")[0]))
        self.assertEqual(self.voice.calls, [ANSWER])
        self.assertEqual(self.answer_calls, 1)
        self.assertEqual([e.text for e in self.memory.events.values() if e.role == "assistant"], [ANSWER])
        self.assertEqual(self.of("audio.boundary")[-1]["text_chars"], len(state.text))
        self.assertEqual(self.of("audio.boundary")[-1]["samples"], state.audio_samples)

    async def test_fast_reasoning_and_normal_answers_skip_reaction(self):
        self.release.set()
        await self.dialogue.text("빨리 답하는 추론")
        await self.finish(self.dialogue.active)
        self.assertFalse(self.of("response.audio", "reaction"))
        self.route = "normal"
        self.release.clear()
        await self.dialogue.text("안녕하세요")
        await asyncio.sleep(.04)
        self.assertFalse(self.of("response.audio", "reaction"))
        await self.finish(self.dialogue.active)
        self.assertFalse(self.bank.recent)

    async def test_pause_and_resume_reuse_same_clip_and_response(self):
        self.packet_delay = .02
        await self.dialogue.text("여러 조건을 비교해 줘")
        state = self.dialogue.active
        await eventually(lambda: self.of("response.audio", "reaction"))
        await self.dialogue.begin_input()
        count = len(self.of("response.audio"))
        await asyncio.sleep(.06)
        self.assertEqual(len(self.of("response.audio")), count)
        await self.dialogue.text("계속 말해 줘")
        judge = self.dialogue.active.task
        await asyncio.wait_for(judge, 1)
        self.assertIs(self.dialogue.active, state)
        await self.finish(state)
        self.assertEqual(len(self.of("response.delta", "reaction")), 1)
        self.assertEqual(self.of("response.resumed")[0]["response_id"], state.response_id)
        self.assertEqual(self.answer_calls, 1)
        self.assertEqual(self.voice.calls, [ANSWER])

    async def test_reset_cancels_pending_reaction_and_generation(self):
        self.packet_delay = .02
        await self.dialogue.text("여러 조건을 비교해 줘")
        state = self.dialogue.active
        await eventually(lambda: self.of("response.audio", "reaction"))
        await self.dialogue.reset()
        count = len(self.of("response.audio"))
        self.release.set()
        await asyncio.sleep(.05)
        self.assertEqual(len(self.of("response.audio")), count)
        self.assertTrue(self.closed.is_set())
        self.assertTrue(state.task.done())
        self.assertFalse(self.voice.calls)
        self.assertFalse(self.memory.events)
        self.assertEqual(self.of("response.cancelled")[0]["response_id"], state.response_id)

    async def test_reaction_only_does_not_mask_answer_failure(self):
        await self.dialogue.text("여러 조건을 비교해 줘")
        state = self.dialogue.active
        await eventually(lambda: self.of("audio.boundary", "reaction"))
        self.failure = True
        self.release.set()
        await asyncio.wait_for(state.task, 1)
        self.assertFalse(self.of("response.done"))
        self.assertEqual(self.of("error")[0]["code"], "turn_failed")
        self.assertFalse([m for m in self.dialogue.history if m["role"] == "assistant"])

    async def test_switch_cancels_old_reaction_and_starts_only_new_answer(self):
        self.packet_delay = .02
        await self.dialogue.text("여러 조건을 비교해 줘")
        old = self.dialogue.active
        await eventually(lambda: self.of("response.audio", "reaction"))
        self.decision = TurnDecision("switch", "normal")
        await self.dialogue.text("그건 됐고 다른 이야기를 하자")
        new = self.dialogue.active
        await eventually(lambda: self.of("response.cancelled"))
        cancelled_at = len(self.events)
        await self.finish(new)
        self.assertTrue(old.task.done())
        self.assertNotEqual(old.response_id, new.response_id)
        self.assertFalse([e for e in self.events[cancelled_at:] if e["type"] == "response.audio"
                          and e["response_id"] == old.response_id])
        self.assertEqual(len(self.of("response.delta", "reaction")), 1)
        self.assertEqual(self.voice.calls, [ANSWER])


class ReactionProtocolTests(unittest.TestCase):
    def test_bad_preparation_keeps_voice_dialogue_available_and_releases_reference(self):
        with tempfile.TemporaryDirectory() as root:
            voice = PreparedVoice()
            class LLM:
                async def available(self, endpoint=None): return True
                async def generate_reactions(self, profile): return {"reactions": ["음?"] * 8}
            class Frontend:
                async def transcribe(self, pcm, sample_rate=16000): return Observation("참조 전사")
            class TTS:
                async def available(self): return True
                async def bind(self, reference, text=None): return voice
            app = create_app(Settings(root, "unused", allow_test_mode=True),
                             frontend=Frontend(), llm=LLM(), tts=TTS())
            with TestClient(app) as web:
                info = web.post("/references", files={"voice": ("public.wav", wav_bytes())}).json()
                with web.websocket_connect("/dialogue") as ws:
                    ws.send_json({"type": "start", "protocol": 1, "sample_rate": 16000, "channels": 1,
                                  "format": "pcm_s16le", "test_mode": True, "test_persona": "차분한 존댓말",
                                  "reference_id": info["reference_id"], "interruption_policy": "semantic_v1"})
                    event = ws.receive_json()
                    while event["type"] == "voice.preparing":
                        event = ws.receive_json()
                    self.assertEqual(event["type"], "ready")
                    self.assertTrue(event["tts"])
                    self.assertFalse(event["reactions"]["ready"])
                    self.assertEqual(event["reactions"]["reason"], "preparation_failed")
                    ws.send_json({"type": "stop"})
                    self.assertEqual(ws.receive_json()["type"], "stopped")
            self.assertEqual(voice.releases, 1)
            self.assertFalse(voice.calls)

    def test_ready_prepares_once_and_reconnect_reuses_temporary_cache(self):
        with tempfile.TemporaryDirectory() as root:
            generated, voices = [], []
            class LLM:
                async def available(self, endpoint=None): return True
                async def generate_reactions(self, profile):
                    generated.append(profile)
                    return {"reactions": LINES}
            class Frontend:
                async def transcribe(self, pcm, sample_rate=16000): return Observation("참조 전사")
            class TTS:
                async def available(self): return True
                async def bind(self, reference, text=None):
                    voice = PreparedVoice()
                    voices.append(voice)
                    return voice
            settings = Settings(root, "unused", token="token", allow_test_mode=True,
                                reaction_cache_dir=str(Path(root) / "cache"))
            app = create_app(settings, frontend=Frontend(), llm=LLM(), tts=TTS())
            headers = {"X-Token": "token"}
            with TestClient(app) as web:
                info = web.post("/references", headers=headers,
                                files={"voice": ("public.wav", wav_bytes())}).json()
                for index in range(2):
                    with web.websocket_connect("/dialogue", headers=headers) as ws:
                        ws.send_json({"type": "start", "protocol": 1, "sample_rate": 16000, "channels": 1,
                                      "format": "pcm_s16le", "test_mode": True, "test_persona": "차분한 존댓말",
                                      "reference_id": info["reference_id"], "interruption_policy": "semantic_v1"})
                        event = ws.receive_json()
                        while event["type"] == "voice.preparing":
                            event = ws.receive_json()
                        self.assertEqual(event["type"], "ready")
                        self.assertTrue(event["reactions"]["ready"])
                        self.assertEqual(event["reactions"]["audio_cache_hit"], bool(index))
                        ws.send_json({"type": "stop"})
                        self.assertEqual(ws.receive_json()["type"], "stopped")
            self.assertEqual(len(generated), 1)
            self.assertEqual([len(v.calls) for v in voices], [6, 0])
            self.assertEqual([v.releases for v in voices], [1, 1])
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
