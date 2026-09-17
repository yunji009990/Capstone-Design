"""엔진 조각 그대로 보내는 TTS 전송의 원음 보존, 무패딩 종료, 오류·취소 계약 검사."""
import asyncio
import base64
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tts_server import ReferenceRequest, SpeechRequest, create_app


class StreamingVoice:
    model_id = 'fake'
    streaming = 'generation_pcm'

    def __init__(self, parts, *, fail=False, wait=False):
        self.parts, self.fail, self.wait = parts, fail, wait
        self.closed = False
        self.waiting = asyncio.Event()

    async def create_prompt(self, *args):
        return 'prompt'

    async def stream(self, *args):
        try:
            for part in self.parts:
                yield part
            if self.fail:
                raise RuntimeError('upstream failed')
            if self.wait:
                self.waiting.set()
                await asyncio.Event().wait()
        finally:
            self.closed = True


@asynccontextmanager
async def worker(voice):
    app = create_app(voice, token='test')
    routes = {route.path: route.endpoint for route in app.routes}
    async with app.router.lifespan_context(app):
        ref = await routes['/voices'](ReferenceRequest(
            pcm=base64.b64encode(bytes(4 * 16000 * 2)).decode(),
            sample_rate=16000, text='검사용 전사'), 'test')

        async def response():
            return await routes['/synthesize'](SpeechRequest(text='검사 문장입니다.', voice_id=ref['voice_id']), 'test')

        yield routes, response


async def collect(response):
    return [json.loads(row) async for row in response.body_iterator]


class PacketFrameTests(unittest.IsolatedAsyncioTestCase):
    def audio(self, events):
        return [base64.b64decode(e['pcm'], validate=True) for e in events if e['type'] == 'audio']

    def assert_passthrough(self, events, parts):
        packets = self.audio(events)
        self.assertEqual(packets, list(parts))
        self.assertTrue(all(0 < len(p) <= 9600 and len(p) % 2 == 0 for p in packets))
        self.assertTrue(all(e['sample_rate'] == 24000 for e in events if e['type'] == 'audio'))
        source = b''.join(parts)
        self.assertEqual(events[-1]['type'], 'done')
        self.assertEqual(events[-1]['source_samples'], len(source) // 2)
        self.assertEqual(events[-1]['padding_samples'], 0)
        self.assertAlmostEqual(events[-1]['audio_sec'], len(source) / 48000)

    async def test_irregular_upstream_chunks_pass_through_unchanged(self):
        parts = [b'\x11\x11' * 4800, b'\x22\x22' * 1920, b'\x33\x33' * 37, b'\x44\x44' * 1440]
        voice = StreamingVoice(parts)
        async with worker(voice) as (routes, response):
            events = await collect(await response())
            self.assert_passthrough(events, parts)
            health = await routes['/health']()
            self.assertNotIn('audio_packet_ms', health)
            self.assertEqual(health['audio_packet_max_ms'], 200)
            self.assertEqual(health['audio_packet_mode'], 'passthrough')
            self.assertFalse(health['audio_tail_padding'])
            self.assertFalse(health['busy'])
        self.assertTrue(voice.closed)

    async def test_small_chunks_are_not_merged_into_fixed_packets(self):
        parts = [b'\x11\x22' * 50, b'\x33\x44' * 7, b'\x55\x66' * 120]
        async with worker(StreamingVoice(parts)) as (_, response):
            events = await collect(await response())
        self.assert_passthrough(events, parts)
        self.assertEqual(len(self.audio(events)), 3)

    async def test_single_sample_tail_chunk_is_preserved_without_padding(self):
        parts = [b'\x55\x22' * 317, b'\xff\x7f']
        async with worker(StreamingVoice(parts)) as (_, response):
            events = await collect(await response())
        self.assert_passthrough(events, parts)
        self.assertEqual(self.audio(events)[-1], b'\xff\x7f')

    async def test_first_chunk_does_not_wait_for_generation_end(self):
        voice = StreamingVoice([b'\x11\x22' * 1000], wait=True)
        async with worker(voice) as (routes, response):
            stream = (await response()).body_iterator
            self.assertEqual(json.loads(await stream.__anext__())['type'], 'started')
            packet = json.loads(await asyncio.wait_for(stream.__anext__(), .5))
            self.assertEqual(base64.b64decode(packet['pcm']), b'\x11\x22' * 1000)
            self.assertTrue((await routes['/health']())['busy'])
            await stream.aclose()
            self.assertTrue(voice.closed)
            self.assertFalse((await routes['/health']())['busy'])

    async def test_error_sends_no_extra_audio_and_no_done(self):
        for parts in [[b'\x11\x22' * 50], [b'\x33\x44' * 4800, b'\x55\x66' * 50]]:
            voice = StreamingVoice(parts, fail=True)
            async with worker(voice) as (routes, response):
                with self.assertLogs('tts_server', level='ERROR'):
                    events = await collect(await response())
                self.assertEqual([e['type'] for e in events],
                                 ['started'] + ['audio'] * len(parts) + ['error'])
                self.assertEqual(self.audio(events), parts)
                self.assertTrue(voice.closed)
                self.assertFalse((await routes['/health']())['busy'])

    async def test_cancel_mid_stream_releases_stream_and_gate(self):
        voice = StreamingVoice([b'\x11\x22' * 1000], wait=True)
        async with worker(voice) as (routes, response):
            stream = (await response()).body_iterator
            await stream.__anext__()
            await stream.__anext__()
            pending = asyncio.create_task(stream.__anext__())
            await asyncio.wait_for(voice.waiting.wait(), .5)
            pending.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await pending
            self.assertTrue(voice.closed)
            self.assertFalse((await routes['/health']())['busy'])
            voice.wait = False
            self.assert_passthrough(await collect(await response()), voice.parts)

    async def test_legacy_path_splits_into_max_packets_without_padding(self):
        source = b'\x11\x22' * 6001

        class Voice:
            model_id = 'fake-legacy'
            def create_prompt(self, *args): return 'prompt'
            def synthesize(self, *args): return source, 24000, .1

        async with worker(Voice()) as (_, response):
            events = await collect(await response())
        self.assert_passthrough(events, [source[:9600], source[9600:]])


if __name__ == '__main__':
    unittest.main()
