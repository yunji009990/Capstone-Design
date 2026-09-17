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
from realtime_dialogue import CLARIFY_GUIDANCE, Dialogue
from realtime_llm import LLMClient, ModelEndpoint
from test_dialogue import SPEECH, QUIET, Gate, NOISE, detector, eventually, feed


FIRST = "이미 전달한 안내입니다."
REST = " 아직 말하지 않은 안내입니다."


class SemanticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.heard = "말하는 중입니다"
        owner = self
        class Frontend:
            async def transcribe(self, pcm): return Observation(owner.heard)
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
        self.llm, self.tts, self.gate = LLM(), TTS(), Gate()
        self.dialogue = Dialogue("인물", [], Frontend(), self.llm, emit,
                                 detector=detector(), tts=self.tts, partial_seconds=60,
                                 speech_gate=self.gate)

    async def asyncTearDown(self): await self.dialogue.close()

    def events_of(self, kind): return [e for e in self.events if e["type"] == kind]

    async def start_answer(self, complete=True):
        await self.dialogue.text("산책 준비를 설명해 줘")
        if complete:
            await eventually(lambda: self.events_of("response.done"))
        else:
            await eventually(lambda: self.events_of("audio.boundary"))
        old = self.dialogue.active
        boundary = self.events_of("audio.boundary")[0]
        self.dialogue.playback({"type": "playback.progress", "response_id": old.response_id,
                                "text_chars": boundary["text_chars"]})
        return old

    async def start_input(self, text="말하는 중입니다"):
        """실제 발화를 말하기 시작한다. 이 단계에서는 아직 아무것도 멈추지 않는다."""
        self.heard = text
        await feed(self.dialogue, SPEECH, 25)
        self.assertFalse(self.dialogue.candidate.accepted)

    async def finish_input(self, text):
        """발화가 끝난다. 검증을 통과했을 때만 새 입력 턴의 작업을 돌려준다."""
        self.heard = text
        before = self.dialogue.input_stats["accepted"]
        await feed(self.dialogue, QUIET, 40)
        await self.dialogue.settle_input()
        if self.dialogue.input_stats["accepted"] == before:
            return None
        return self.dialogue.active.task if self.dialogue.active is not None else None

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
        # 말하는 동안에는 아직 멈추지 않는다. 확인은 발화가 끝난 뒤에 이뤄진다.
        self.assertIsNone(self.dialogue.held)
        self.assertTrue(old.running.is_set())
        self.assertFalse(self.events_of("response.paused"))
        task = await self.finish_input("응, 계속 말해")
        await asyncio.wait_for(task, 1)
        self.assertTrue(self.events_of("response.paused"))
        self.assertFalse(self.events_of("response.cancelled"))
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
        hold = asyncio.Event()

        async def judge(messages, pending):
            await hold.wait()
            return TurnDecision("resume")

        self.llm.decide_interruption = judge
        await self.start_input()
        task = await self.finish_input("계속해 줘")
        await eventually(lambda: self.events_of("response.paused"))
        count = len(self.events_of("response.audio"))
        self.llm.stream_release.set()
        await asyncio.sleep(.03)
        self.assertEqual(len(self.events_of("response.audio")), count)
        self.assertFalse(self.llm.stream_cancelled.is_set())
        hold.set()
        await asyncio.wait_for(task, 1)
        await self.complete_playback(old)
        self.assertEqual(len(self.llm.calls), 1)
        self.assertEqual(len(self.tts.calls), 2)
        self.assertEqual([base64.b64decode(e["pcm"]) for e in self.events_of("response.audio")],
                         [b"\x01\x00" * 4800, bytes(7200), b"\x02\x00" * 4800])

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
        # 정정 경로에는 되묻기 지시가 붙지 않는다.
        self.assertNotIn(CLARIFY_GUIDANCE, messages[0]["content"])
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
        self.assertNotIn(CLARIFY_GUIDANCE, messages[0]["content"])
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
        # 무의미한 전사는 턴 자체를 만들지 않는다. 보류 상태는 그대로 유지된다.
        self.assertIsNone(await self.finish_input(""))
        self.assertFalse(old.running.is_set())
        self.assertIs(self.dialogue.held, old)
        self.assertEqual(len(self.llm.judgments), 1)
        self.assertEqual(len(self.llm.calls), 1)
        self.llm.decisions.append(TurnDecision("resume"))
        await self.start_input()
        await (await self.finish_input("이제 계속 말해 줘"))
        await self.complete_playback(old)
        self.assertEqual(len(self.llm.calls), 1)

    async def test_noise_keeps_playback_running_and_lets_it_finish_during_verification(self):
        old = await self.start_answer()
        paused, started = len(self.events_of("response.paused")), len(self.events_of("speech.started"))
        await feed(self.dialogue, NOISE, 60)
        await feed(self.dialogue, QUIET, 40)
        # 후보 검증이 진행되는 동안 원래 답변의 재생 완료가 도착해도 계약이 유지된다.
        self.dialogue.playback({"type": "playback.done", "response_id": old.response_id,
                                "text_chars": old.sent_chars})
        await self.dialogue.settle_input()
        self.assertEqual(len(self.events_of("response.paused")), paused)
        self.assertEqual(len(self.events_of("speech.started")), started)
        self.assertEqual(self.llm.judgments, [])
        self.assertFalse(self.events_of("response.cancelled"))
        await asyncio.wait_for(old.task, 1)
        self.assertEqual(len(self.llm.calls), 1)

    async def test_empty_false_detection_never_pauses_or_reaches_the_judge(self):
        old = await self.start_answer()
        await self.start_input()
        self.assertIsNone(await self.finish_input(""))
        self.assertEqual(self.llm.judgments, [])
        self.assertTrue(old.running.is_set())
        self.assertFalse(self.events_of("response.paused"))
        self.assertEqual(len([m for m in self.dialogue.history if m["role"] == "user"]), 1)
        await self.complete_playback(old)

    async def test_clarify_asks_about_the_content_through_the_ordinary_generator(self):
        old = await self.start_answer()
        self.llm.decisions.append(TurnDecision("clarify", reason="발화가 끊겨 의도를 확인합니다"))
        await self.start_input()
        await self.finish_input("아니 그게 좀")
        await eventually(lambda: len(self.llm.calls) == 2)
        new = self.dialogue.active
        messages = self.llm.calls[-1]
        # 되묻기도 인물의 보통 답변 생성 경로를 지나고 판정 호출은 늘지 않는다.
        self.assertEqual(len(self.llm.judgments), 1)
        self.assertEqual(self.llm.routes[-1], "normal")
        self.assertIn(CLARIFY_GUIDANCE, messages[0]["content"])
        body = json.dumps(messages, ensure_ascii=False)
        # 사용자가 방금 한 말은 들어가고, 판정 이유·분류 이름·미전달 초안은 들어가지 않는다.
        self.assertIn("아니 그게 좀", body)
        self.assertNotIn("발화가 끊겨", body)
        self.assertNotIn("clarify", body)
        self.assertNotIn(REST.strip(), body)
        # 진행 방식 선택지를 프롬프트로 시키지 않는다.
        self.assertNotIn("계속할까", body)
        # 기존 취소·턴 계약은 그대로다.
        self.assertEqual(self.events_of("response.cancelled")[0]["reason"], "clarify")
        self.assertEqual(self.events_of("response.cancelled")[0]["response_id"], old.response_id)
        self.assertNotEqual(new.response_id, old.response_id)
        await self.complete_playback(new)

    async def test_failed_judge_asks_for_clarification_instead_of_resuming(self):
        await self.start_answer()
        self.llm.decisions.append(ValueError("bad JSON"))
        await self.start_input()
        with self.assertLogs("realtime_dialogue", level="ERROR"):
            await self.finish_input("그게 좀")
            await eventually(lambda: self.events_of("interruption.decision"))
        await eventually(lambda: len(self.llm.calls) == 2)
        self.assertEqual(self.events_of("interruption.decision")[-1]["action"], "clarify")
        self.assertFalse(self.events_of("response.resumed"))
        # 판정 실패도 같은 경로로 되묻는다. 고정 안내 문장은 더 이상 나가지 않는다.
        self.assertIn(CLARIFY_GUIDANCE, self.llm.calls[-1][0]["content"])
        self.assertNotIn("바꿔서", " ".join(self.tts.calls))
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
        # 보류가 성립할 시간은 주고, 만료는 검사가 기다릴 수 있을 만큼만 짧게 둔다.
        # 더 짧게 잡으면 판정이 끝나기 전에 만료돼 hold 자체가 성립하지 않는다.
        self.dialogue.hold_seconds = .3
        old = await self.start_answer()
        self.llm.decisions.append(TurnDecision("hold"))
        await self.start_input()
        await (await self.finish_input("잠깐 기다려"))
        self.assertIs(self.dialogue.held, old)
        self.assertTrue(old.hold_requested)
        await eventually(lambda: self.events_of("response.cancelled"), timeout=3)
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

    async def test_failed_recognition_never_stops_the_original_answer(self):
        """확인되지 않은 입력은 인식이 실패해도 기존 답변을 멈추지 않는다."""
        old = await self.start_answer()
        async def fail(pcm): raise RuntimeError("recognition failed")
        self.dialogue.frontend.transcribe = fail
        with self.assertLogs("realtime_dialogue", level="ERROR"):
            await feed(self.dialogue, SPEECH, 25)
            await feed(self.dialogue, QUIET, 40)
            await self.dialogue.settle_input()
        self.assertIs(self.dialogue.active, old)
        self.assertIsNone(self.dialogue.held)
        self.assertTrue(old.running.is_set())
        self.assertFalse(self.events_of("response.paused"))
        self.assertFalse(self.events_of("response.cancelled"))
        self.assertEqual(len(self.events_of("speech.started")), 1)  # 첫 질문뿐이다
        self.assertEqual(self.dialogue.input_stats["failed"], 1)
        await self.complete_playback(old)

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
