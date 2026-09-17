"""말끝 감지 사각지대와 그 보완을 검사한다.

재현 대상은 실측 trace e2909fe28fd5 다. 첫 응답 뒤 후보가 26초 동안 start 만 있고
end 가 없어 전사도 답변도 없었다. 여기서는 WebRTC 자리에 "표식이 있으면 말"이라고
보는 모의 판정기를 넣어 같은 교착을 만들고, Silero 자리에는 표식을 구별하는 모의
검증기를 넣어 꼬리 무음을 잰다.

이 검사는 구현을 그대로 비추지 않는다. 확인하는 것은 동작이다.
  1. 기존 WebRTC 판정이 연속 잡음을 말로 보면 말끝이 영영 오지 않는다(사각지대 자체).
  2. 그 상태에서 검증기가 꼬리 무음을 확인하면 말끝이 생기고 다음 정상 턴이 회복된다.
  3. 잡음만 있을 때 답변 중단 0 · 전사 0 이다.
  4. 700 ms 미만의 쉼으로 긴 진짜 말을 쪼개지 않는다.
  5. 늦게 도착한 비동기 결과는 새 후보를 자르지 못하고, 그 사이의 짧은 발화도 잃지 않는다.
  6. reset/close 가 검토 작업과 검토 일정을 함께 정리한다.
  7. 길이 상한으로 버린 뒤의 대기도 꼬리 무음이 확인되면 재무장한다.

모의 검증기의 통과 결과를 실제 Silero·Whisper 의 잡음 판별 성능으로 읽으면 안 된다.
실제 음향 검증은 메인이 공개/합성 음성과 연속 잡음으로 따로 진행한다.
"""
import asyncio
import logging
import sys
import unittest
from pathlib import Path
from unittest import mock

# 배포 위치(Server/tests)에서는 이 한 줄이면 된다. 아직 작업 폴더의 수정안 트리에
# 있는 동안에는 고친 파일만 있으므로, 나머지 모듈을 가진 Server 를 뒤에 덧붙인다.
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
if not (SERVER / "dialogue_server.py").is_file():
    full = next((parent / "Server" for parent in SERVER.parents
                 if (parent / "Server" / "dialogue_server.py").is_file()), None)
    if full is None:
        raise RuntimeError("고치지 않은 모듈을 가진 Server 폴더를 찾지 못했습니다")
    sys.path.append(str(full))

from dialogue_diagnostics import ConnectionDiagnostics           # noqa: E402
from interruption_policy import TurnDecision                     # noqa: E402
from realtime_audio import FRAME_BYTES, Observation, TurnDetector  # noqa: E402
from realtime_dialogue import Dialogue                           # noqa: E402
from speech_gate import SAMPLE_RATE, SpeechEvidence, TailEvidence  # noqa: E402

FRAME_SECONDS = FRAME_BYTES / 2 / SAMPLE_RATE

# 프레임 표식: 1 은 실제 말, 2 는 잡음, 0 은 무음이다.
# 모의 WebRTC 는 1·2 를 모두 말로 보고(실측의 false positive), 모의 검증기는 1 만 말로 본다.
SPEECH = b"\x01\x00" * (FRAME_BYTES // 2)
NOISE = b"\x02\x00" * (FRAME_BYTES // 2)
QUIET = bytes(FRAME_BYTES)


def detector(**kwargs):
    return TurnDetector(is_speech=lambda frame: frame[0] != 0, **kwargs)


def push(turn, frame, count):
    """판정기에 프레임을 하나씩 넣는다. feed 는 한 번에 500 ms 까지만 받는다."""
    events = []
    for _ in range(count):
        events += turn.feed(frame)
    return events


async def feed(dialogue, frame, count):
    for _ in range(count):
        await dialogue.audio(frame)
        # 오디오는 실시간으로 들어온다. 검토 작업이 돌 틈을 준다.
        await asyncio.sleep(0)


async def eventually(predicate, timeout=1):
    async def wait():
        while not predicate():
            await asyncio.sleep(.001)
    await asyncio.wait_for(wait(), timeout)


class Gate:
    """모의 음성 검증기. 표식 1 프레임만 말로 센다. 실제 Silero 가 아니다."""

    def __init__(self, min_speech_ms=150):
        self.min_speech_seconds = min_speech_ms / 1000.0
        self.verifies = 0
        self.tails = 0
        self.block = None          # 설정하면 tail_silence 가 여기서 멈춘다
        self.pretend_speech = False  # 꼬리를 0 으로 답해 "말이 이어진다"를 흉내 낸다

    def settings(self):
        return {"version": "test_gate", "acoustic": "fake"}

    @staticmethod
    def _frames(pcm):
        return [pcm[i] for i in range(0, len(pcm) - len(pcm) % FRAME_BYTES, FRAME_BYTES)]

    async def verify(self, pcm):
        self.verifies += 1
        voiced = sum(mark == 1 for mark in self._frames(pcm))
        seconds = voiced * FRAME_SECONDS
        return SpeechEvidence(seconds, len(pcm) / 2 / SAMPLE_RATE, 1 if voiced else 0,
                              seconds >= self.min_speech_seconds - .001, source="fake")

    async def tail_silence(self, pcm):
        self.tails += 1
        if self.block is not None:
            await self.block.wait()
        marks = self._frames(pcm)
        total = len(marks) * FRAME_SECONDS
        if self.pretend_speech:
            return TailEvidence(0.0, total, 1, source="fake")
        last = max((i for i, mark in enumerate(marks) if mark == 1), default=None)
        if last is None:
            return TailEvidence(total, total, 0, source="fake")
        return TailEvidence((len(marks) - 1 - last) * FRAME_SECONDS, total, 1, source="fake")

    def close(self):
        pass


class Frontend:
    def __init__(self):
        self.calls = 0

    async def transcribe(self, pcm):
        self.calls += 1
        return Observation("사용자의 말", audio_event="speech", language="ko")


class LLM:
    def __init__(self):
        self.block = asyncio.Event()
        self.block.set()
        self.judgments = []

    async def available(self, endpoint=None):
        return True

    async def route(self, messages):
        return "normal"

    async def decide_interruption(self, messages, pending):
        self.judgments.append(pending)
        return TurnDecision("switch")

    async def stream(self, messages, route):
        yield "네, 말씀하신 대로 준비할게요."
        await self.block.wait()


class BlindSpotTests(unittest.TestCase):
    """보완이 없을 때 판정기 스스로는 교착을 풀지 못한다는 것부터 고정한다."""

    def test_continuous_voiced_noise_never_ends_and_never_rearms(self):
        turn = detector()
        kinds = []
        for _ in range(1700):                     # 34초
            kinds += [kind for kind, _ in turn.feed(NOISE)]
        self.assertEqual(kinds, ["start", "too_long"])
        self.assertTrue(turn.discarding)
        for _ in range(500):                      # 10초를 더 줘도 재무장하지 못한다
            self.assertEqual(turn.feed(NOISE), [])
        self.assertTrue(turn.discarding)

    def test_sparse_false_positives_keep_a_candidate_open(self):
        turn = detector()
        self.assertEqual([kind for kind, _ in push(turn, NOISE, 12)], ["start"])
        for _ in range(20):                       # 30프레임(600 ms)마다 한 번씩만 voiced
            self.assertEqual(push(turn, QUIET, 30), [])
            self.assertEqual(push(turn, NOISE, 1), [])
        self.assertTrue(turn.speaking)
        self.assertLess(turn.max_quiet, turn.silence_frames)

    def test_a_verified_silent_tail_is_required_to_force_an_endpoint(self):
        turn = detector()
        push(turn, NOISE, 150)
        # 확인된 꼬리가 기준에 못 미치면 아무 일도 없다. 길이로 끊지 않는다.
        self.assertEqual(turn.endpoint(turn.samples_seen, .69), [])
        self.assertTrue(turn.speaking)
        events = turn.endpoint(turn.samples_seen, .70)
        self.assertEqual([kind for kind, _ in events], ["end"])
        self.assertFalse(turn.speaking)

    def test_a_window_that_is_no_longer_current_is_refused(self):
        turn = detector()
        push(turn, NOISE, 150)
        window_end = turn.samples_seen
        push(turn, SPEECH, 1)                     # 창을 만든 뒤 들어온 한 프레임
        # 검사하지 않은 구간이 생겼다. 그 뒤로는 어떤 꼬리 값으로도 자르지 않는다.
        self.assertEqual(turn.endpoint(window_end, 2.0), [])
        self.assertTrue(turn.speaking)
        # 최신 창으로 다시 재면 그때 판단한다.
        events = turn.endpoint(turn.samples_seen, 2.0)
        self.assertEqual([kind for kind, _ in events], ["end"])


class EndpointSupplementTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []

        async def emit(event):
            self.events.append(event)

        self.gate, self.frontend, self.llm = Gate(), Frontend(), LLM()
        self.diagnostics = ConnectionDiagnostics("0123456789ab")
        self.dialogue = Dialogue("인물", [], self.frontend, self.llm, emit,
                                 detector=detector(), partial_seconds=60,
                                 speech_gate=self.gate, diagnostics=self.diagnostics,
                                 semantic_interruptions=True)

    async def asyncTearDown(self):
        self.llm.block.set()
        await self.dialogue.close()

    @property
    def counts(self):
        return self.diagnostics.counts

    def kinds(self):
        return [event["type"] for event in self.events]

    def finals(self):
        return [event for event in self.events if event["type"] == "transcript.final"]

    async def utterance(self, frames=25, quiet=40):
        """기존 경로 그대로의 정상 발화. 말끝은 WebRTC 가 찾는다."""
        await feed(self.dialogue, SPEECH, frames)
        await feed(self.dialogue, QUIET, quiet)
        await self.dialogue.settle_input()
        state = self.dialogue.active
        if state is not None:
            await asyncio.wait_for(state.task, 2)

    async def test_noise_deadlock_is_broken_and_the_next_turn_recovers(self):
        await self.utterance()
        self.assertEqual(len(self.finals()), 1)
        answered, transcribed = self.dialogue.turn_id, self.frontend.calls

        # 첫 응답 뒤 연속 잡음. WebRTC 는 끝을 찾지 못한다.
        await feed(self.dialogue, NOISE, 250)
        await self.dialogue.settle_input()
        self.assertEqual(self.counts["vad_end"], 1)          # 늘지 않았다
        self.assertGreaterEqual(self.counts["endpoint_end"], 1)
        # 잡음만 있을 때: 답변 중단 0, 전사 0.
        self.assertEqual(self.frontend.calls, transcribed)
        self.assertEqual(len(self.finals()), 1)
        self.assertEqual(self.dialogue.turn_id, answered)
        self.assertNotIn("response.paused", self.kinds())
        self.assertNotIn("response.cancelled", self.kinds())
        self.assertGreaterEqual(self.dialogue.input_stats["no_speech"], 1)
        self.assertEqual(self.dialogue.input_stats["accepted"], 1)

        # 잡음이 계속되는 중에도 다음 진짜 발화가 회복된다.
        await self.utterance()
        self.assertEqual(self.dialogue.turn_id, answered + 1)
        self.assertEqual(len(self.finals()), 2)
        self.assertEqual(self.frontend.calls, transcribed + 1)

    async def test_noise_during_an_answer_never_interrupts_it(self):
        self.llm.block.clear()
        await self.dialogue.text("설명해 줘")
        await eventually(lambda: "response.delta" in self.kinds())
        state = self.dialogue.active
        await feed(self.dialogue, NOISE, 400)
        await self.dialogue.settle_input()
        self.assertGreaterEqual(self.counts["endpoint_end"], 1)
        self.assertIs(self.dialogue.active, state)
        self.assertIsNone(self.dialogue.held)
        self.assertTrue(state.running.is_set())
        self.assertNotIn("response.paused", self.kinds())
        self.assertNotIn("response.cancelled", self.kinds())
        self.assertEqual(self.llm.judgments, [])
        self.assertEqual(self.frontend.calls, 0)
        self.llm.block.set()
        await asyncio.wait_for(state.task, 2)
        self.assertIn("response.done", self.kinds())

    async def test_long_speech_with_short_pauses_is_not_split(self):
        await feed(self.dialogue, SPEECH, 25)
        for _ in range(8):
            await feed(self.dialogue, QUIET, 20)      # 400 ms 쉼: 기준 미만이다
            await feed(self.dialogue, SPEECH, 25)
        self.assertGreaterEqual(self.gate.tails, 1)   # 검토는 돌았다
        self.assertEqual(self.counts["endpoint_end"], 0)
        self.assertTrue(self.dialogue.detector.speaking)
        await feed(self.dialogue, QUIET, 40)          # 실제 말끝
        await self.dialogue.settle_input()
        state = self.dialogue.active
        if state is not None:
            await asyncio.wait_for(state.task, 2)
        self.assertEqual(self.counts["vad_end"], 1)
        self.assertEqual(len(self.finals()), 1)
        self.assertEqual(self.dialogue.input_stats["accepted"], 1)

    async def test_a_late_result_never_swallows_a_short_new_utterance(self):
        # 메인 재현(review-probe-result.json)의 회귀. 늦게 온 결과를 반영하면
        # 그 사이에 시작한 200 ms 발화가 통째로 사라졌다.
        self.gate.block = asyncio.Event()
        await feed(self.dialogue, NOISE, 115)
        await eventually(lambda: self.gate.tails == 1)
        late = self.dialogue.endpoint_task
        await feed(self.dialogue, SPEECH, 10)         # 200 ms 짧은 새 발화
        self.gate.block.set()
        await late
        self.gate.block = None
        self.assertEqual(self.counts["endpoint_stale"], 1)
        self.assertEqual(self.counts["endpoint_end"], 0)
        self.assertTrue(self.dialogue.detector.speaking)
        # 최신 창으로 다시 재면 말끝이 생기고, 짧은 발화는 그 안에 남아 있다.
        await feed(self.dialogue, NOISE, 140)
        await self.dialogue.settle_input()
        self.assertGreaterEqual(self.counts["endpoint_end"], 1)
        self.assertGreaterEqual(self.frontend.calls, 1)
        state = self.dialogue.active
        if state is not None:
            await asyncio.wait_for(state.task, 2)
        self.assertEqual(len(self.finals()), 1)

    async def test_reset_restores_the_check_schedule(self):
        # 메인 재현의 회귀. reset 은 samples_seen 을 0 으로 되돌리므로 일정 숫자를
        # 남겨 두면 그만큼 보완이 꺼진 채로 다음 체험이 시작된다.
        self.gate.pretend_speech = True               # 긴 이전 연결
        await feed(self.dialogue, SPEECH, 260)
        self.assertGreater(self.dialogue.next_endpoint_samples, 0)
        await self.dialogue.reset()
        self.assertEqual(self.dialogue.detector.samples_seen, 0)
        self.assertEqual(self.dialogue.next_endpoint_samples, 0)
        self.assertEqual(self.dialogue.next_endpoint_log_samples, 0)
        # reset 직후에 열린 후보도 2초면 검토가 돈다.
        self.gate.pretend_speech = False
        checks = self.gate.tails
        await feed(self.dialogue, NOISE, 110)
        await eventually(lambda: self.gate.tails > checks)
        await self.dialogue.settle_input()
        self.assertGreaterEqual(self.counts["endpoint_end"], 1)

    async def test_reset_discards_the_running_check(self):
        self.gate.block = asyncio.Event()
        await feed(self.dialogue, NOISE, 120)
        await eventually(lambda: self.gate.tails == 1)
        await self.dialogue.reset()
        self.assertIsNone(self.dialogue.endpoint_task)
        self.gate.block.set()
        await asyncio.sleep(0)
        self.assertEqual(self.counts["endpoint_end"], 0)
        self.assertFalse(self.dialogue.detector.speaking)
        self.assertEqual(self.dialogue.detector.recent_audio(), b"")

    async def test_close_discards_the_running_check(self):
        self.gate.block = asyncio.Event()
        await feed(self.dialogue, NOISE, 120)
        await eventually(lambda: self.gate.tails == 1)
        await self.dialogue.close()
        self.assertIsNone(self.dialogue.endpoint_task)
        self.gate.block.set()
        await asyncio.sleep(0)
        self.assertEqual(self.counts["endpoint_end"], 0)

    async def test_the_length_limit_discard_rearms_after_a_verified_tail(self):
        # 30초 동안은 검증기도 말이 이어진다고 본다. 상한 폐기까지 그대로 간다.
        self.gate.pretend_speech = True
        await feed(self.dialogue, NOISE, 1600)
        self.assertEqual(self.counts["vad_too_long"], 1)
        self.assertTrue(self.dialogue.detector.discarding)
        self.assertEqual(self.counts["endpoint_end"], 0)

        # 소리는 그대로인데 이제 검증기가 비음성 꼬리를 확인한다.
        self.gate.pretend_speech = False
        await feed(self.dialogue, NOISE, 40)
        await eventually(lambda: self.counts["endpoint_rearm"] == 1)
        self.assertFalse(self.dialogue.detector.discarding)
        # 재무장 뒤에는 다음 진짜 발화가 다시 턴을 만든다.
        await self.utterance()
        self.assertEqual(len(self.finals()), 1)
        self.assertEqual(self.dialogue.input_stats["accepted"], 1)

    async def test_a_failed_check_leaves_everything_alone(self):
        async def broken(pcm):
            raise RuntimeError("검증기 실패")

        # 일부러 실패시키는 검사다. 예외 기록은 검사 출력에서 지운다.
        failures = logging.getLogger("realtime_dialogue")
        level = failures.level
        failures.setLevel(logging.CRITICAL)
        self.addCleanup(failures.setLevel, level)
        self.gate.tail_silence = broken
        self.llm.block.clear()
        await self.dialogue.text("설명해 줘")
        await eventually(lambda: "response.delta" in self.kinds())
        state = self.dialogue.active
        await feed(self.dialogue, NOISE, 200)
        await eventually(lambda: self.counts["endpoint_failed"] >= 1)
        self.assertIs(self.dialogue.active, state)
        self.assertTrue(state.running.is_set())
        self.assertTrue(self.dialogue.detector.speaking)
        self.assertEqual(self.counts["endpoint_end"], 0)
        self.llm.block.set()
        await asyncio.wait_for(state.task, 2)

    async def test_diagnostics_carry_numbers_and_fixed_codes_only(self):
        lines = []
        # 진단 로거는 프로세스 전역 singleton 이다. 직접 대입하면 이 검사가 끝난 뒤에도
        # info 가 바뀐 채로 남아 뒤따르는 검사의 로그를 삼킨다. 끝나면 되돌린다.
        capture = mock.patch.object(
            self.diagnostics.log, "info",
            lambda message, *args: lines.append(message % args if args else message))
        capture.start()
        self.addCleanup(capture.stop)
        await feed(self.dialogue, NOISE, 250)
        await self.dialogue.settle_input()
        endpoint = [line for line in lines if "event=input.endpoint" in line]
        self.assertTrue(endpoint)
        self.assertTrue(any("reason=silence_tail" in line for line in endpoint))
        for line in endpoint:
            for key in ("age_ms=", "voiced_pct=", "max_quiet_ms=", "tail_ms="):
                self.assertIn(key, line)
            for field in line.split():
                name, _, value = field.partition("=")
                if name in ("v", "trace", "event", "reason"):
                    continue
                float(value)          # 숫자 아닌 값이 섞이면 여기서 실패한다
        self.assertGreater(self.counts["endpoint_checks"], 0)


if __name__ == "__main__":
    unittest.main()
