"""잡음 후보가 기존 답변을 멈추지 못하고, 확인된 발화만 입력 턴을 만드는지 검사한다.

Gate 는 실제 Silero 가 아니라 표식 프레임만 세는 모의 검증기다. 여기의 통과 결과를
실제 모델의 잡음 판별 성능으로 읽으면 안 된다. 실제 VAD·SenseVoice 검사는 별도다.
이 버전은 발화가 끝난 뒤에만 검증한다. 채택 전에는 어떤 이벤트도 나가지 않는다.
"""
import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_memory import SessionMemory
from interruption_policy import TurnDecision
from realtime_audio import Observation, TurnDetector
from realtime_dialogue import MAX_PENDING_CANDIDATES, Dialogue
from speech_gate import meaningful
from test_dialogue import (NOISE, QUIET, SPEECH, Gate, detector, eventually, feed)


class MeaningfulTextTests(unittest.TestCase):
    def test_punctuation_whitespace_and_model_tags_are_not_content(self):
        for value in (".", "。", " ", "  \n\t", "...", "…", "?!", "·", "-", "",
                      "<|nospeech|>", "<think></think>", "<|ko|><|NEUTRAL|>", None, 3):
            with self.subTest(value=value):
                self.assertFalse(meaningful(value))

    def test_normal_short_answers_are_kept(self):
        for value in ("네", "응", "아니", "아니요", "잠깐", "그만", "박", "3", "3시",
                      "ㅇㅇ", "네.", "음...", "yes", "제주도."):
            with self.subTest(value=value):
                self.assertTrue(meaningful(value))

    def test_noise_transcripts_with_letters_need_the_acoustic_gate(self):
        # 실측 잡음 전사 "그."에는 표기 문자가 있어 전사 검사만으로는 거를 수 없다.
        # 이 경우의 방어선은 음향 검증이다. 특정 단어 목록을 쓰지 않는다.
        self.assertTrue(meaningful("그."))


class InputGateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.heard = "안녕하세요"
        # Whisper 세그먼트 지표. 비어 있으면 미측정이라 약한 전사 거절이 동작하지 않는다.
        # 숫자는 메인의 2026-09-17 실측에서 가져왔다.
        self.quality = []
        owner = self

        class Frontend:
            async def transcribe(self, pcm):
                return Observation(owner.heard, quality=list(owner.quality))

        class LLM:
            def __init__(self):
                self.calls, self.judgments = [], []
                self.block = asyncio.Event()
                self.block.set()

            async def route(self, messages):
                return "normal"

            async def decide_interruption(self, messages, pending):
                self.judgments.append(pending)
                return TurnDecision("resume")

            async def stream(self, messages, route):
                self.calls.append(messages)
                yield "안내를 시작합니다."
                await self.block.wait()
                yield " 이어지는 안내입니다."

        async def emit(event):
            self.events.append(event)

        self.llm, self.gate = LLM(), Gate()
        self.memory = SessionMemory(None)
        self.dialogue = Dialogue("인물", [], Frontend(), self.llm, emit, detector=detector(),
                                 partial_seconds=60, speech_gate=self.gate, memory=self.memory,
                                 semantic_interruptions=True)

    async def asyncTearDown(self):
        await self.dialogue.close()

    def kinds(self):
        return [event["type"] for event in self.events]

    def finals(self):
        return [event["text"] for event in self.events if event["type"] == "transcript.final"]

    async def answer(self, text="설명해 줘"):
        await self.dialogue.text(text)
        await asyncio.wait_for(self.dialogue.active.task, 1)

    async def speak(self, frame=SPEECH, frames=25):
        await feed(self.dialogue, frame, frames)
        await feed(self.dialogue, QUIET, 40)
        await self.dialogue.settle_input()

    async def test_raw_detection_alone_changes_no_turn_event_or_memory(self):
        await self.answer()
        before, turn = list(self.kinds()), self.dialogue.turn_id
        history, sources = list(self.dialogue.history), len(self.memory.events)
        await feed(self.dialogue, NOISE, 60)
        # raw VAD 시작만으로는 보류·취소·턴 증가·화면 전사·기억 정리가 없어야 한다.
        self.assertIsNotNone(self.dialogue.candidate)
        self.assertFalse(self.dialogue.candidate.accepted)
        self.assertEqual(self.kinds(), before)
        self.assertEqual(self.dialogue.turn_id, turn)
        await feed(self.dialogue, QUIET, 40)
        await self.dialogue.settle_input()
        self.assertEqual(self.kinds(), before)
        self.assertEqual(self.dialogue.turn_id, turn)
        self.assertEqual(self.dialogue.history, history)
        self.assertEqual(len(self.memory.events), sources)
        self.assertIsNone(self.dialogue.candidate)
        self.assertEqual(self.dialogue.input_stats["no_speech"], 1)

    async def test_noise_during_generation_never_pauses_or_cancels_the_answer(self):
        self.llm.block.clear()
        await self.dialogue.text("설명해 줘")
        await eventually(lambda: [e for e in self.events if e["type"] == "response.delta"])
        state = self.dialogue.active
        await self.speak(NOISE, 60)
        self.assertIs(self.dialogue.active, state)
        self.assertIsNone(self.dialogue.held)
        self.assertTrue(state.running.is_set())
        self.assertNotIn("response.paused", self.kinds())
        self.assertNotIn("response.cancelled", self.kinds())
        self.assertEqual(self.llm.judgments, [])
        self.llm.block.set()
        await asyncio.wait_for(state.task, 1)
        self.assertIn("response.done", self.kinds())

    async def test_noise_while_the_answer_is_still_routing_keeps_the_pending_turn(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow_route(messages):
            entered.set()
            await release.wait()
            return "normal"

        self.llm.route = slow_route
        await self.speak()
        await asyncio.wait_for(entered.wait(), 1)
        state = self.dialogue.active
        self.assertFalse(state.announced)
        await self.speak(NOISE, 60)
        # 미발표 응답이 잡음 때문에 newer_utterance 로 사라지지 않는다.
        self.assertIs(self.dialogue.active, state)
        self.assertNotIn("response.cancelled", self.kinds())
        release.set()
        await asyncio.wait_for(state.task, 1)
        self.assertIn("response.done", self.kinds())
        self.assertEqual(self.finals(), ["안녕하세요"])

    async def test_meaningless_final_transcript_is_not_accepted_despite_speech(self):
        await self.answer()
        before, turn = list(self.kinds()), self.dialogue.turn_id
        for value in (".", "  ", "<|nospeech|>"):
            with self.subTest(value=value):
                self.heard = value
                await self.speak()
                self.assertEqual(self.kinds(), before)
                self.assertEqual(self.dialogue.turn_id, turn)
        self.assertEqual(self.dialogue.input_stats["no_text"], 3)
        self.assertEqual(self.dialogue.input_stats["accepted"], 0)

    async def test_weak_transcript_during_an_answer_pauses_nothing_and_keeps_memory(self):
        # 음향 검증(SPEECH)은 통과하고 표기 문자도 있지만, 실측 잡음 지표를 가진 전사다.
        self.llm.block.clear()
        await self.dialogue.text("설명해 줘")
        await eventually(lambda: [e for e in self.events if e["type"] == "response.delta"])
        state = self.dialogue.active
        before, turn = list(self.kinds()), self.dialogue.turn_id
        history, sources = list(self.dialogue.history), len(self.memory.events)
        calls = len(self.llm.calls)
        self.heard = "그."
        self.quality = [{"chars": 2, "no_speech_prob": .789, "avg_logprob": -.486}]
        await self.speak()
        self.assertIs(self.dialogue.active, state)
        self.assertIsNone(self.dialogue.held)
        self.assertTrue(state.running.is_set())
        self.assertEqual(self.kinds(), before)
        self.assertEqual(self.dialogue.turn_id, turn)
        self.assertEqual(self.dialogue.history, history)
        self.assertEqual(len(self.memory.events), sources)
        self.assertEqual(len(self.llm.calls), calls)
        self.assertEqual(self.llm.judgments, [])
        self.assertEqual(self.dialogue.input_stats["weak_text"], 1)
        self.assertEqual(self.dialogue.input_stats["accepted"], 0)
        # 진행 중이던 답변은 그대로 끝난다.
        self.llm.block.set()
        await asyncio.wait_for(state.task, 1)
        self.assertIn("response.done", self.kinds())

    async def test_measured_short_acknowledgement_still_opens_a_turn(self):
        # 실측 "으음": avg_logprob 는 잡음보다 낮지만 no_speech_prob 가 낮다.
        self.heard = "으음"
        self.quality = [{"chars": 2, "no_speech_prob": .603, "avg_logprob": -.835}]
        await self.speak()
        self.assertIn("speech.started", self.kinds())
        self.assertEqual(self.dialogue.input_stats["accepted"], 1)
        self.assertEqual(self.dialogue.input_stats["weak_text"], 0)

    async def test_a_real_utterance_right_after_a_weak_one_still_opens_a_turn(self):
        self.heard = "그."
        self.quality = [{"chars": 2, "no_speech_prob": .862, "avg_logprob": -.466}]
        await self.speak()
        self.assertEqual(self.events, [])
        # 거절은 후보만 닫는다. 다음 발화의 검증·채택 경로가 그대로 다시 열린다.
        self.heard = "어제 뭐 했어?"
        self.quality = [{"chars": 8, "no_speech_prob": .106, "avg_logprob": -.292}]
        await self.speak()
        self.assertIn("speech.started", self.kinds())
        self.assertEqual(self.dialogue.input_stats["weak_text"], 1)
        self.assertEqual(self.dialogue.input_stats["accepted"], 1)

    async def test_no_transcript_reaches_the_screen_before_acceptance(self):
        self.heard = "."
        self.dialogue.partial_bytes = 0
        await feed(self.dialogue, SPEECH, 60)
        self.assertNotIn("transcript.partial", self.kinds())
        self.assertNotIn("speech.started", self.kinds())
        await feed(self.dialogue, QUIET, 40)
        await self.dialogue.settle_input()
        self.assertEqual(self.events, [])

    async def test_short_real_answers_still_open_a_turn(self):
        for value in ("네", "응", "아니", "아니요", "잠깐", "그만", "박", "3"):
            with self.subTest(value=value):
                await self.dialogue.reset()
                self.events.clear()
                self.heard = value
                await self.speak(SPEECH, 10)  # 0.2초 남짓의 짧은 발화
                await eventually(lambda: "response.done" in self.kinds())
                self.assertIn("speech.started", self.kinds())
                self.assertEqual(self.finals(), [value])

    async def test_a_later_raw_candidate_does_not_discard_the_earlier_one(self):
        entered, release = asyncio.Event(), asyncio.Event()
        original = self.dialogue.frontend.transcribe

        async def slow(pcm):
            entered.set()
            await release.wait()
            return await original(pcm)

        self.dialogue.frontend.transcribe = slow
        await feed(self.dialogue, SPEECH, 25)
        await feed(self.dialogue, QUIET, 40)
        await asyncio.wait_for(entered.wait(), 1)
        await feed(self.dialogue, NOISE, 25)  # 다음 raw 후보가 시작됐다는 이유만으로
        self.assertIsNotNone(self.dialogue.candidate)
        release.set()
        await self.dialogue.settle_input()
        await eventually(lambda: "response.done" in self.kinds())
        self.assertEqual(self.finals(), ["안녕하세요"])  # 앞선 정상 입력이 살아 있다

    async def test_an_accepted_input_invalidates_an_older_pending_candidate(self):
        released = asyncio.Event()

        class Ordered:
            async def transcribe(self, pcm):
                if pcm == b"first":
                    await released.wait()
                    return Observation("먼저 시작한 발화")
                return Observation("나중 발화")

        self.dialogue.frontend = Ordered()
        self.dialogue.speech_gate = None  # 음향 검증은 이 검사의 변수가 아니다
        self.dialogue.open_candidate()
        first = self.dialogue.candidate
        await self.dialogue.close_candidate(b"first")
        self.dialogue.open_candidate()
        second = self.dialogue.candidate
        await self.dialogue.close_candidate(b"second")
        await eventually(lambda: second.settled)
        self.assertTrue(second.accepted)
        released.set()
        await self.dialogue.settle_input()
        # 더 새로운 입력이 채택된 뒤 도착한 과거 결과는 턴을 만들지 못한다.
        self.assertFalse(first.accepted)
        self.assertEqual(self.kinds().count("speech.started"), 1)
        self.assertEqual(self.finals(), ["나중 발화"])

    async def test_a_late_preliminary_check_cannot_touch_the_existing_answer(self):
        """조기 확인 경로를 쓰지 않더라도 예비 검증은 절대 채택하지 못한다."""
        released = asyncio.Event()

        class Reordered:
            async def transcribe(self, pcm):
                if pcm == b"partial":
                    await released.wait()
                    return Observation("응")
                return Observation(".")

        self.llm.block.clear()
        await self.dialogue.text("설명해 줘")
        await eventually(lambda: [e for e in self.events if e["type"] == "response.delta"])
        state = self.dialogue.active
        self.dialogue.frontend = Reordered()
        self.dialogue.speech_gate = None
        self.dialogue.open_candidate()
        candidate = self.dialogue.candidate
        self.dialogue.spawn_candidate(candidate, b"partial", final=False)
        await asyncio.sleep(0)
        await self.dialogue.close_candidate(b"final")
        await eventually(lambda: candidate.settled)
        released.set()
        await self.dialogue.settle_input()
        self.assertFalse(candidate.accepted)
        self.assertIs(self.dialogue.active, state)
        self.assertEqual(self.kinds().count("speech.started"), 1)  # 직접 입력분뿐이다
        self.assertNotIn("response.paused", self.kinds())
        self.llm.block.set()
        await asyncio.wait_for(state.task, 1)

    async def test_pending_candidates_are_bounded_and_the_oldest_is_dropped(self):
        release = asyncio.Event()

        async def blocked(pcm):
            await release.wait()
            return Observation("늦은 발화")

        self.dialogue.frontend.transcribe = blocked
        for _ in range(MAX_PENDING_CANDIDATES + 2):
            await feed(self.dialogue, SPEECH, 25)
            await feed(self.dialogue, QUIET, 40)
        self.assertLessEqual(len(self.dialogue.pending_candidates), MAX_PENDING_CANDIDATES)
        self.assertEqual(self.dialogue.input_stats["dropped"], 2)
        release.set()
        await self.dialogue.settle_input()
        # 버려진 2개는 턴을 만들지 않는다. 남은 후보는 한도 안에서 서로를 대체한다.
        turns = self.kinds().count("speech.started")
        self.assertTrue(0 < turns <= MAX_PENDING_CANDIDATES, turns)
        # 남은 후보는 서로를 대체하므로 실제 답변 턴은 그보다 적을 수 있다.
        self.assertTrue(self.finals())
        self.assertEqual(set(self.finals()), {"늦은 발화"})
        self.assertLessEqual(len(self.finals()), turns)
        self.assertFalse(self.dialogue.pending_candidates)
        await eventually(lambda: "response.done" in self.kinds())

    async def parked_judge(self):
        """판정을 멈춰 세워 새 입력의 응답이 활성 상태로 남게 한다."""
        async def parked(messages, pending):
            await asyncio.Event().wait()
        self.llm.decide_interruption = parked

    async def numbered_answer(self):
        """진행 중인 답변 하나와 두 발화를 구분해 읽는 인식기를 준비한다."""
        class Numbered:
            async def transcribe(self, pcm):
                return Observation("첫 번째" if pcm == b"first" else "두 번째")

        self.dialogue.frontend = Numbered()
        self.dialogue.speech_gate = None
        await self.parked_judge()
        self.llm.block.clear()
        await self.dialogue.text("설명해 줘")
        await eventually(lambda: [e for e in self.events if e["type"] == "response.delta"])

    async def test_a_stale_acceptance_cannot_overwrite_a_newer_confirmed_input(self):
        """메인의 독립 재현 세 번째 사례. 채택 도중 송신 await 에서 순서가 뒤집힌다."""
        paused, release = asyncio.Event(), asyncio.Event()
        await self.numbered_answer()
        original = self.dialogue.emit

        async def slow_emit(event):
            await original(event)
            if event["type"] == "response.paused" and not paused.is_set():
                paused.set()
                await release.wait()

        self.dialogue.emit = slow_emit
        self.dialogue.open_candidate()
        first = asyncio.create_task(self.dialogue.close_candidate(b"first"))
        await asyncio.wait_for(paused.wait(), 1)
        self.dialogue.open_candidate()
        await self.dialogue.close_candidate(b"second")
        await asyncio.sleep(.02)  # 두 번째 채택이 잠금 앞에서 기다리게 둔다
        release.set()
        await first
        await self.dialogue.settle_input()
        # 늦게 끝난 옛 채택이 새 입력의 응답을 덮어쓰거나 취소하지 않는다.
        await eventually(lambda: self.finals()[-1:] == ["두 번째"])
        self.assertEqual(self.dialogue.active.heard, "두 번째")

    async def test_memory_cleanup_wait_cannot_cancel_a_newer_confirmed_input(self):
        """메인의 독립 재현 네 번째 사례. begin_input 내부에서 순서가 뒤집힌다."""
        entered, release = asyncio.Event(), asyncio.Event()

        async def cleanup():
            try:
                await asyncio.Event().wait()
            finally:
                entered.set()
                await release.wait()

        await self.numbered_answer()
        self.memory.task = asyncio.create_task(cleanup())
        await asyncio.sleep(0)
        self.dialogue.open_candidate()
        first = asyncio.create_task(self.dialogue.close_candidate(b"first"))
        # 기억 정리 취소를 기다리는 동안 다음 발화가 확인된다.
        await asyncio.wait_for(entered.wait(), 1)
        self.dialogue.open_candidate()
        await self.dialogue.close_candidate(b"second")
        await asyncio.sleep(.02)
        release.set()
        await first
        await self.dialogue.settle_input()
        await eventually(lambda: self.finals()[-1:] == ["두 번째"])
        self.assertEqual(self.dialogue.active.heard, "두 번째")

    async def test_close_discards_a_candidate_still_being_recognised(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(pcm):
            entered.set()
            await release.wait()
            return Observation("늦게 도착한 발화")

        self.dialogue.frontend.transcribe = slow
        await feed(self.dialogue, SPEECH, 25)
        await feed(self.dialogue, QUIET, 40)
        await asyncio.wait_for(entered.wait(), 1)
        await self.dialogue.close()
        count = len(self.events)
        release.set()
        await self.dialogue.settle_input()
        self.assertEqual(len(self.events), count)
        self.assertIsNone(self.dialogue.active)
        self.assertNotIn("speech.started", self.kinds())

    async def test_reset_during_acceptance_does_not_revive_the_turn(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def slow(pcm):
            entered.set()
            await release.wait()
            return Observation("리셋 직전 발화")

        self.dialogue.frontend.transcribe = slow
        await feed(self.dialogue, SPEECH, 25)
        await feed(self.dialogue, QUIET, 40)
        await asyncio.wait_for(entered.wait(), 1)
        await self.dialogue.reset()
        self.events.clear()
        release.set()
        await self.dialogue.settle_input()
        self.assertEqual(self.events, [])
        self.assertIsNone(self.dialogue.active)
        self.assertEqual(self.dialogue.history, [])

    async def test_overlong_unverified_input_is_dropped_without_touching_the_answer(self):
        self.dialogue.detector = TurnDetector(is_speech=lambda frame: frame[0] != 0, max_seconds=.5)
        self.llm.block.clear()
        await self.dialogue.text("설명해 줘")
        await eventually(lambda: [e for e in self.events if e["type"] == "response.delta"])
        state, before = self.dialogue.active, len(self.events)
        await feed(self.dialogue, NOISE, 60)
        await self.dialogue.settle_input()
        self.assertTrue(self.dialogue.detector.discarding)
        self.assertEqual(len(self.events), before)
        self.assertIs(self.dialogue.active, state)
        self.assertEqual(self.dialogue.input_stats["dropped"], 1)
        self.llm.block.set()
        await asyncio.wait_for(state.task, 1)



if __name__ == "__main__":
    unittest.main()
