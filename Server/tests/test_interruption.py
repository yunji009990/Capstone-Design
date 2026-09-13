import asyncio
import base64
import json
import sys
import unittest
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from interruption_policy import TurnDecision
from realtime_audio import Observation
from realtime_dialogue import Dialogue
from realtime_llm import LLMClient, ModelEndpoint
from test_dialogue import SPEECH, QUIET, detector, feed


FIRST = "이미 전달한 안내입니다."
REST = " 아직 말하지 않은 안내입니다."


async def eventually(predicate, timeout=1):
    async def wait():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), timeout)


class SemanticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.words = ["산책 준비를 설명해 줘"]
        owner = self
        class Frontend:
            async def transcribe(self, pcm): return Observation(owner.words.pop(0), emotion="neutral")
        class LLM:
            def __init__(self):
                self.decisions = []
                self.calls = []
                self.routes = []
                self.judgments = []
                self.stream_cancelled = asyncio.Event()
                self.block_stream = False
                self.stream_release = asyncio.Event()
            async def route(self, messages): return "normal"
            async def decide_interruption(self, messages, pending):
                self.judgments.append((messages, pending))
                value = self.decisions.pop(0)
                if isinstance(value, Exception): raise value
                return value
            async def stream(self, messages, route):
                self.calls.append(messages)
                self.routes.append(route)
                try:
                    yield FIRST + " "
                    if self.block_stream: await self.stream_release.wait()
                    yield REST.lstrip()
                finally:
                    self.stream_cancelled.set()
        class TTS:
            def __init__(self): self.calls = []
            async def stream(self, text):
                self.calls.append(text)
                yield base64.b64encode(bytes([len(self.calls), 0]) * 4800).decode(), 4800
        async def emit(event): self.events.append(event)
        self.llm, self.tts = LLM(), TTS()
        self.dialogue = Dialogue("인물", [], Frontend(), self.llm, emit,
                                 detector=detector(), tts=self.tts, partial_seconds=60)

    async def asyncTearDown(self): await self.dialogue.close()

    def events_of(self, kind): return [e for e in self.events if e["type"] == kind]

    async def start_answer(self, complete=True):
        await self.dialogue.text(self.words.pop(0))
        if complete:
            await eventually(lambda: self.events_of("response.done"))
        else:
            await eventually(lambda: self.events_of("audio.boundary"))
        old = self.dialogue.active
        boundary = self.events_of("audio.boundary")[0]
        self.dialogue.playback({"type": "playback.progress", "response_id": old.response_id,
                                "text_chars": boundary["text_chars"]})
        return old

    async def start_input(self):
        await feed(self.dialogue, SPEECH, 25)

    async def finish_input(self, text):
        self.words.append(text)
        await feed(self.dialogue, QUIET, 40)
        task = self.dialogue.active.task
        return task

    async def complete_playback(self, state):
        await eventually(lambda: any(e["response_id"] == state.response_id for e in self.events_of("response.done")))
        self.dialogue.playback({"type": "playback.done", "response_id": state.response_id,
                                "text_chars": state.sent_chars})
        await asyncio.wait_for(state.task, 1)

    async def test_resume_reuses_same_answer_audio_and_records_interjection_in_order(self):
        old = await self.start_answer()
        before = list(self.events_of("response.audio"))
        self.llm.decisions.append(TurnDecision("resume", reason="계속 설명 요청"))
        await self.start_input()
        self.assertIs(self.dialogue.held, old)
        self.assertFalse(old.running.is_set())
        self.assertFalse(self.events_of("response.cancelled"))
        task = await self.finish_input("응, 계속 말해")
        await asyncio.wait_for(task, 1)
        self.assertIs(self.dialogue.active, old)
        self.assertTrue(old.running.is_set())
        self.assertEqual(self.events_of("response.resumed")[0]["response_id"], old.response_id)
        self.assertEqual(self.events_of("response.audio"), before)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(len(self.tts.calls), 2)
        await self.complete_playback(old)
        self.assertEqual([m["role"] for m in self.dialogue.history], ["user", "assistant", "user", "assistant"])
        self.assertEqual(" ".join(m["content"] for m in self.dialogue.history if m["role"] == "assistant"), FIRST + REST)

    async def test_pending_generation_is_quiet_during_pause_and_continues_without_restart(self):
        self.llm.block_stream = True
        old = await self.start_answer(complete=False)
        await self.start_input()
        count = len(self.events_of("response.audio"))
        self.llm.stream_release.set()
        await asyncio.sleep(.03)
        self.assertEqual(len(self.events_of("response.audio")), count)
        self.assertFalse(self.llm.stream_cancelled.is_set())
        self.llm.decisions.append(TurnDecision("resume"))
        task = await self.finish_input("계속해 줘")
        await task
        await self.complete_playback(old)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(len(self.tts.calls), 2)
        self.assertEqual(len(self.events_of("response.audio")), 2)

    async def test_text_generation_uses_same_judge_without_tts_or_playback_ack(self):
        self.dialogue.tts = None
        self.dialogue.semantic_interruptions = True
        self.llm.block_stream = True
        self.llm.decisions.append(TurnDecision("resume"))
        await self.dialogue.text("산책 준비를 설명해 줘")
        await eventually(lambda: self.events_of("response.delta"))
        old = self.dialogue.active
        await self.dialogue.text("응, 계속 말해")
        judge_task = self.dialogue.active.task
        await asyncio.wait_for(judge_task, 1)
        self.assertEqual(self.llm.judgments[0][1]["spoken_text"], FIRST + " ")
        self.assertEqual(self.llm.judgments[0][1]["unspoken_draft"], "")
        self.assertTrue(self.events_of("response.paused"))
        self.assertEqual(self.events_of("response.resumed")[0]["response_id"], old.response_id)
        self.llm.stream_release.set()
        await asyncio.wait_for(old.task, 1)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(self.tts.calls, [])
        self.assertFalse(self.events_of("response.audio"))
        self.assertIsNone(self.dialogue.active)
        self.assertEqual(" ".join(m["content"] for m in self.dialogue.history if m["role"] == "assistant"), FIRST + REST)

    async def test_revise_keeps_spoken_history_and_labels_unspoken_draft(self):
        old = await self.start_answer()
        self.llm.decisions.append(TurnDecision("revise", "reasoning", "조건을 수정함"))
        await self.start_input()
        await self.finish_input("비가 오는 날로 바꿔 줘")
        await eventually(lambda: len(self.llm.calls) == 2)
        new = self.dialogue.active
        self.assertNotEqual(new.response_id, old.response_id)
        self.assertEqual(self.llm.routes[-1], "reasoning")
        self.assertEqual(self.events_of("response.cancelled")[0]["reason"], "revise")
        messages = self.llm.calls[-1]
        self.assertIn("전달되지 않은 참고 자료", messages[0]["content"])
        self.assertIn(REST.strip(), messages[0]["content"])
        assistants = [m["content"] for m in messages if m["role"] == "assistant"]
        self.assertEqual(assistants, [FIRST])
        await self.complete_playback(new)

    async def test_switch_drops_unplayed_draft_but_keeps_conversation(self):
        old = await self.start_answer()
        self.llm.decisions.append(TurnDecision("switch"))
        await self.start_input()
        await self.finish_input("그건 됐고 고양이를 설명해 줘")
        await eventually(lambda: len(self.llm.calls) == 2)
        new = self.dialogue.active
        messages = self.llm.calls[-1]
        self.assertNotIn(REST.strip(), json.dumps(messages, ensure_ascii=False))
        self.assertIn(FIRST, json.dumps(messages, ensure_ascii=False))
        self.dialogue.playback({"type": "playback.done", "response_id": old.response_id, "text_chars": 9999})
        self.assertFalse(new.playback_done.is_set())
        await self.complete_playback(new)

    async def test_hold_survives_false_detection_and_resumes_only_after_request(self):
        old = await self.start_answer()
        self.llm.decisions.append(TurnDecision("hold"))
        await self.start_input()
        await (await self.finish_input("잠깐 기다려"))
        self.assertIs(self.dialogue.held, old)
        self.assertTrue(old.hold_requested)
        await self.start_input()
        await (await self.finish_input(""))
        self.assertFalse(old.running.is_set())
        self.assertEqual(len(self.llm.judgments), 1)
        self.assertEqual(len(self.llm.calls), 1)
        self.llm.decisions.append(TurnDecision("resume"))
        await self.start_input()
        await (await self.finish_input("이제 계속 말해 줘"))
        await self.complete_playback(old)
        self.assertEqual(len(self.llm.calls), 1)

    async def test_empty_false_detection_resumes_without_judge_or_empty_history_entry(self):
        old = await self.start_answer()
        await self.start_input()
        await (await self.finish_input(""))
        self.assertEqual(self.llm.judgments, [])
        self.assertTrue(old.running.is_set())
        self.assertEqual(len([m for m in self.dialogue.history if m["role"] == "user"]), 1)
        await self.complete_playback(old)

    async def test_failed_judge_asks_for_clarification_instead_of_resuming(self):
        await self.start_answer()
        self.llm.decisions.append(ValueError("bad JSON"))
        await self.start_input()
        with self.assertLogs("realtime_dialogue", level="ERROR"):
            await self.finish_input("그게 좀")
            await eventually(lambda: self.events_of("interruption.decision"))
        await eventually(lambda: len(self.events_of("response.done")) == 2)
        self.assertEqual(self.events_of("interruption.decision")[-1]["action"], "clarify")
        self.assertFalse(self.events_of("response.resumed"))
        self.assertEqual(len(self.llm.calls), 1)
        self.assertIn("내용을 바꿔서", self.tts.calls[-1])
        await self.complete_playback(self.dialogue.active)

    async def test_newer_utterance_invalidates_a_late_resume_decision(self):
        old = await self.start_answer()
        entered = asyncio.Event()
        calls = []
        async def judge(messages, pending):
            calls.append(messages)
            if len(calls) == 1:
                entered.set()
                try: await asyncio.Event().wait()
                except asyncio.CancelledError: return TurnDecision("resume")
            return TurnDecision("switch")
        self.llm.decide_interruption = judge
        await self.start_input()
        await self.finish_input("계속")
        await asyncio.wait_for(entered.wait(), 1)
        await self.start_input()
        self.assertIs(self.dialogue.held, old)
        self.assertFalse(self.events_of("response.resumed"))
        await self.finish_input("아니 다른 얘기를 해 줘")
        await eventually(lambda: len(self.llm.calls) == 2)
        self.assertEqual([e["action"] for e in self.events_of("interruption.decision")], ["switch"])
        await self.complete_playback(self.dialogue.active)

    async def test_reset_and_timeout_release_suspended_work_without_auto_playback(self):
        self.dialogue.hold_seconds = .03
        await self.start_answer()
        await self.start_input()
        await eventually(lambda: self.events_of("response.cancelled"))
        self.assertIsNone(self.dialogue.held)
        self.assertEqual(self.events_of("response.cancelled")[-1]["reason"], "hold_timeout")
        self.assertFalse(self.events_of("response.resumed"))
        await self.dialogue.reset()
        self.assertEqual(self.dialogue.history, [])
        self.assertIsNone(self.dialogue.active)

    async def test_close_cancels_pending_judge_and_suspended_answer(self):
        old = await self.start_answer()
        entered = asyncio.Event()
        async def judge(messages, pending):
            entered.set()
            await asyncio.Event().wait()
        self.llm.decide_interruption = judge
        await self.start_input()
        pending = await self.finish_input("잠깐")
        await asyncio.wait_for(entered.wait(), 1)
        await self.dialogue.close()
        count = len(self.events)
        await asyncio.sleep(.01)
        self.assertTrue(pending.done() and old.task.done())
        self.assertEqual(len(self.events), count)
        self.assertIsNone(self.dialogue.held)

    async def test_failed_stt_releases_the_suspended_answer_with_the_client(self):
        old = await self.start_answer()
        async def fail(pcm): raise RuntimeError("recognition failed")
        self.dialogue.frontend.transcribe = fail
        await self.start_input()
        with self.assertLogs("realtime_dialogue", level="ERROR"):
            task = await self.finish_input("unused")
            await task
        self.assertIsNone(self.dialogue.active)
        self.assertIsNone(self.dialogue.held)
        self.assertTrue(old.task.done())
        self.assertEqual(self.events_of("response.cancelled")[-1]["reason"], "turn_failed")
        self.assertFalse(self.events_of("response.resumed"))


    async def test_text_stream_eof_during_hold_does_not_finish_or_forget_answer(self):
        self.dialogue.tts = None
        self.dialogue.semantic_interruptions = True
        release = asyncio.Event()
        async def one_sentence(messages, route):
            yield FIRST
            await release.wait()
        self.llm.stream = one_sentence
        self.llm.decisions.extend([TurnDecision("hold"), TurnDecision("resume")])
        await self.dialogue.text("산책 준비를 설명해 줘")
        await eventually(lambda: self.events_of("response.delta"))
        old = self.dialogue.active
        await self.dialogue.text("잠깐 기다려")
        await self.dialogue.active.task
        release.set()
        await asyncio.sleep(.02)
        self.assertIs(self.dialogue.held, old)
        self.assertFalse(self.events_of("response.done"))
        self.assertFalse(old.task.done())
        await self.dialogue.text("이제 계속해")
        await self.dialogue.active.task
        await asyncio.wait_for(old.task, 1)
        self.assertEqual(len(self.events_of("response.done")), 1)
        self.assertIsNone(self.dialogue.held)


class DecisionTests(unittest.IsolatedAsyncioTestCase):
    async def test_single_model_call_combines_action_route_and_pending_context(self):
        requests = []
        def handler(request):
            requests.append(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
                "content": '{"action":"revise","route":"reasoning","reason":"조건 비교"}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            llm = LLMClient(ModelEndpoint("http://llm/chat/completions", "normal"),
                            ModelEndpoint("http://llm/chat/completions", "reason"), client=http)
            result = await llm.decide_interruption([{"role": "user", "content": "새 조건"}],
                                                  {"spoken_text": "전달됨", "unspoken_draft": "미전달"})
        self.assertEqual((result.action, result.route), ("revise", "reasoning"))
        self.assertEqual(len(requests), 1)
        self.assertFalse(requests[0]["stream"])
        self.assertEqual(requests[0]["model"], "normal")
        self.assertIn("전달됨", requests[0]["messages"][-1]["content"])
        self.assertNotIn("미전달", requests[0]["messages"][-1]["content"])
        self.assertNotIn("unspoken_draft", requests[0]["messages"][-1]["content"])

    def test_invalid_decisions_are_not_coerced_to_resume(self):
        for value in (None, {}, {"action": "unknown", "route": "normal"},
                      {"action": "resume", "route": "reasoning", "reason": []}):
            with self.assertRaises(ValueError): TurnDecision.parse(value)
        self.assertEqual(TurnDecision.parse({"action": "revise", "route": "reasoning"}).route, "normal")


if __name__ == "__main__": unittest.main()
