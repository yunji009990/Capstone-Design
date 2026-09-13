import asyncio
import base64
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from realtime_dialogue import Dialogue
from realtime_tts import PhraseBuffer
from test_dialogue import FakeLLM, Frontend, detector


class StreamingVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_audio_starts_while_llm_is_generating_and_cancels_worker(self):
        class TTS:
            def __init__(self):
                self.cancelled = asyncio.Event()
            async def stream(self, text):
                try:
                    yield base64.b64encode(bytes(9600)).decode(), 4800
                    await asyncio.Event().wait()
                finally:
                    self.cancelled.set()
        class LLM(FakeLLM):
            async def stream(self, messages, route):
                try:
                    yield "먼저 보낸 말. "
                    self.entered.set()
                    await self.release.wait()
                finally:
                    self.cancelled.set()
        llm, tts, events = LLM(), TTS(), []
        llm.block = True
        got_audio = asyncio.Event()
        async def emit(event):
            events.append(event)
            if event['type'] == 'response.audio': got_audio.set()
        dialogue = Dialogue("test", [], Frontend(), llm, emit, detector=detector(), tts=tts)
        await dialogue.text("안녕하세요")
        await asyncio.wait_for(got_audio.wait(), 1)
        self.assertFalse(llm.release.is_set())
        state = dialogue.active
        await dialogue.interrupt("user_speech")
        self.assertTrue(tts.cancelled.is_set())
        self.assertTrue(llm.cancelled.is_set())
        self.assertFalse(any(e['type'] == 'response.done' for e in events))
        self.assertEqual(dialogue.history[-1]['role'], 'user')  # unplayed answer excluded
        dialogue.playback({'type': 'playback.done', 'response_id': state.response_id, 'text_chars': 100})
        await dialogue.close()

    async def test_playback_ack_keeps_answer_cancellable_and_records_spoken_prefix(self):
        class TTS:
            async def stream(self, text):
                yield base64.b64encode(bytes(9600)).decode(), 4800
        events, done = [], asyncio.Event()
        async def emit(event):
            events.append(event)
            if event['type'] == 'response.done': done.set()
        dialogue = Dialogue("test", [], Frontend(), FakeLLM(), emit, detector=detector(), tts=TTS())
        await dialogue.text("안녕")
        await asyncio.wait_for(done.wait(), 1)
        state = dialogue.active
        self.assertIsNotNone(state)
        first = next(e for e in events if e['type'] == 'audio.boundary')
        dialogue.playback({'type': 'playback.progress', 'response_id': state.response_id,
                           'text_chars': first['text_chars']})
        await dialogue.interrupt("user_speech")
        self.assertEqual(dialogue.history[-1]['content'], "먼저 보낸 말.")
        await dialogue.close()

    def test_phrase_lengths_and_original_text_order(self):
        text = "첫 문장입니다. 다음 문장은 " + "아주 긴 문장 " * 30 + "끝."
        buffer, phrases = PhraseBuffer(), []
        for ch in text:
            phrases.extend(buffer.push(ch))
        phrases.extend(buffer.push("", final=True))
        self.assertEqual(''.join(phrases), text)
        self.assertTrue(all(len(p) <= 100 for p in phrases))

    async def test_blank_llm_lines_do_not_request_empty_tts_and_keep_playback_offsets(self):
        answer = "\n\n첫 안내입니다.\n\n둘째 안내입니다.\n"
        calls, events = [], []
        class LLM(FakeLLM):
            async def stream(self, messages, route):
                for chunk in ("\n", "\n첫 안내입니다.", "\n\n", "둘째 안내입니다.\n"):
                    yield chunk
        class TTS:
            async def stream(self, text):
                if not text.strip(): raise ValueError("Empty speech")
                calls.append(text.strip())
                yield base64.b64encode(bytes(9600)).decode(), 4800
        done = asyncio.Event()
        async def emit(event):
            events.append(event)
            if event["type"] == "response.done": done.set()
        dialogue = Dialogue("test", [], Frontend(), LLM(), emit, detector=detector(), tts=TTS())
        try:
            await dialogue.text("안내해 줘")
            await asyncio.wait_for(done.wait(), 1)
            state = dialogue.active
            self.assertEqual(calls, ["첫 안내입니다.", "둘째 안내입니다."])
            boundary = [e for e in events if e["type"] == "audio.boundary"][-1]
            self.assertEqual(answer[:boundary["text_chars"]].strip(), answer.strip())
            dialogue.playback({"type": "playback.done", "response_id": state.response_id,
                               "text_chars": boundary["text_chars"]})
            await asyncio.wait_for(state.task, 1)
            self.assertEqual(dialogue.history[-1]["content"], answer.strip())
        finally:
            await dialogue.close()

    def test_decimal_across_deltas_and_large_llm_chunk(self):
        buffer = PhraseBuffer()
        self.assertEqual(buffer.push("값은 3."), [])
        self.assertEqual(buffer.push("14입니다. "), ["값은 3.14입니다."])
        pieces = buffer.push("긴 문장 " * 50 + ". ", final=True)
        self.assertTrue(all(len(p) <= 100 for p in pieces))


if __name__ == '__main__':
    unittest.main()
