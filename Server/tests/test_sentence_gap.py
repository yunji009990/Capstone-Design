import asyncio
import base64
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from realtime_audio import Observation
from realtime_dialogue import SENTENCE_GAP_PCM, SENTENCE_GAP_SAMPLES, Dialogue
from realtime_tts import ends_sentence
from test_dialogue import Gate, detector, eventually


class Frontend:
    async def transcribe(self, pcm):
        return Observation("사용자의 말")


class LLM:
    def __init__(self, deltas):
        self.deltas = deltas

    async def route(self, messages):
        return "normal"

    async def stream(self, messages, route):
        for delta in self.deltas:
            yield delta


class TTS:
    """호출 순서마다 다른 표식 PCM 을 돌려주는 검사용 TTS."""

    def __init__(self, empty_after=None):
        self.calls = []
        self.empty_after = empty_after

    def pcm(self, call):
        return bytes([call, 1]) * 240

    async def stream(self, text):
        self.calls.append(text)
        if self.empty_after is not None and len(self.calls) > self.empty_after:
            return
        yield base64.b64encode(self.pcm(len(self.calls))).decode(), 240


class SentenceGapTests(unittest.IsolatedAsyncioTestCase):
    async def run_turn(self, deltas, empty_after=None, pause_on_gap=False):
        self.events = []

        async def emit(event):
            self.events.append(event)
            if pause_on_gap and event.get("pcm") == SENTENCE_GAP_PCM:
                # 네트워크 전송 중 들어온 즉시 보류를 모의한다.
                self.dialogue.active.running.clear()

        self.tts = TTS(empty_after)
        self.dialogue = Dialogue("인물", [], Frontend(), LLM(deltas), emit,
                                 detector=detector(), tts=self.tts, partial_seconds=60,
                                 speech_gate=Gate())
        self.addAsyncCleanup(self.dialogue.close)
        await self.dialogue.text("설명해 줘")
        if pause_on_gap:
            await eventually(lambda: any(e.get("pcm") == SENTENCE_GAP_PCM for e in self.events))
        else:
            await eventually(lambda: [e for e in self.events if e["type"] == "response.done"])

    def audio(self):
        return [e for e in self.events if e["type"] == "response.audio" and e.get("kind") == "answer"]

    def pcm_of(self, event):
        return base64.b64decode(event["pcm"])

    async def test_gap_only_between_sentences_and_model_pcm_untouched(self):
        await self.run_turn(["첫 문장입니다. ", "두 번째 문장입니다."])
        packets = self.audio()
        self.assertEqual(len(self.tts.calls), 2)
        self.assertEqual(len(packets), 3)
        # 첫 음성과 마지막 꼬리에는 무음을 붙이지 않는다.
        self.assertEqual(self.pcm_of(packets[0]), self.tts.pcm(1))
        self.assertEqual(self.pcm_of(packets[2]), self.tts.pcm(2))
        self.assertEqual(self.pcm_of(packets[1]), bytes(SENTENCE_GAP_SAMPLES * 2))
        self.assertEqual(len(self.pcm_of(packets[1])), 7200)
        bounds = [e["samples"] for e in self.events if e["type"] == "audio.boundary"]
        self.assertEqual(bounds, [240, 240 + SENTENCE_GAP_SAMPLES + 240])
        chars = [e["text_chars"] for e in self.events if e["type"] == "audio.boundary"]
        self.assertEqual(chars, [len(self.tts.calls[0]), sum(map(len, self.tts.calls))])

    async def test_forced_length_split_gets_no_gap(self):
        await self.run_turn(["가" * 150])
        packets = self.audio()
        self.assertEqual(len(self.tts.calls), 2)
        self.assertEqual(len(packets), 2)
        self.assertEqual([self.pcm_of(p) for p in packets], [self.tts.pcm(1), self.tts.pcm(2)])

    async def test_no_gap_when_next_phrase_produces_no_audio(self):
        await self.run_turn(["첫 문장입니다. ", "두 번째 문장입니다."], empty_after=1)
        packets = self.audio()
        self.assertEqual(len(self.tts.calls), 2)
        self.assertEqual(len(packets), 1)
        self.assertEqual(self.pcm_of(packets[0]), self.tts.pcm(1))

    async def test_pause_during_gap_blocks_next_audio_until_resume(self):
        await self.run_turn(["첫 문장입니다. ", "두 번째 문장입니다."], pause_on_gap=True)
        await asyncio.sleep(.02)
        self.assertEqual(len(self.audio()), 2)
        self.assertFalse(any(e["type"] == "response.done" for e in self.events))
        self.dialogue.active.running.set()
        await eventually(lambda: any(e["type"] == "response.done" for e in self.events))
        self.assertEqual([self.pcm_of(p) for p in self.audio()],
                         [self.tts.pcm(1), bytes(7200), self.tts.pcm(2)])

    def test_sentence_end_recognizes_quotes_and_newlines(self):
        for text in ['문장입니다. ', '"괜찮아?"', '생각 중…', '문장\n']:
            with self.subTest(text=text):
                self.assertTrue(ends_sentence(text))
        self.assertFalse(ends_sentence('길이 때문에 잘린 구절 '))


if __name__ == "__main__":
    unittest.main()
