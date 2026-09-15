import asyncio
import base64
import json
from pathlib import Path
import sys
import unittest

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from realtime_tts import TTSClient
from tts_omni import OmniVoice
from tts_server import ReferenceRequest, SpeechRequest, create_app


async def anext(iterator):  # CPU harness also supports Python 3.9.
    return await iterator.__anext__()


def sse(kind, **fields):
    return ("event: " + kind + "\r\ndata: " + json.dumps(dict(type=kind, **fields)) + "\r\n\r\n").encode()


def audio(pcm=b"\x01\x00" * 7200):
    return sse("speech.audio.delta", audio=base64.b64encode(pcm).decode(), response_format="pcm")


class ByteStream(httpx.AsyncByteStream):
    def __init__(self, chunks, finish=None):
        self.chunks = chunks
        self.finish = finish
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            # Network fragmentation must not split SSE events or PCM samples.
            yield chunk[:17]
            yield chunk[17:]
        if self.finish:
            await self.finish.wait()
            yield sse("speech.audio.done")

    async def aclose(self):
        self.closed = True


class OmniTests(unittest.IsolatedAsyncioTestCase):
    def make_voice(self, stream):
        def handler(request):
            self.request = json.loads(request.content)
            return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=stream)
        return OmniVoice(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), managed=False)

    async def test_first_pcm_before_generation_completes_and_preserves_pcm_contract(self):
        finish = asyncio.Event()
        pcm = b"\x10\x20" * 7200
        upstream = ByteStream([audio(pcm)], finish)
        voice = self.make_voice(upstream)
        stream = voice.stream("안녕하세요.", "voice")
        first = await asyncio.wait_for(anext(stream), .5)
        second = await asyncio.wait_for(anext(stream), .5)
        self.assertEqual(len(first), 9600)
        self.assertEqual(first + second, pcm)
        self.assertFalse(finish.is_set())
        self.assertTrue(self.request["stream"])
        self.assertFalse(self.request["x_vector_only_mode"])
        self.assertEqual(self.request["task_type"], "Base")
        finish.set()
        with self.assertRaises(StopAsyncIteration):
            await anext(stream)
        self.assertTrue(upstream.closed)
        await voice.close()

    async def test_cancel_while_waiting_closes_engine_http_stream(self):
        upstream = ByteStream([audio()], asyncio.Event())
        voice = self.make_voice(upstream)
        stream = voice.stream("답변", "voice")
        await anext(stream)
        await anext(stream)
        pending = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        self.assertTrue(upstream.closed)
        await voice.close()

    async def test_closing_after_first_packet_closes_engine_request(self):
        upstream = ByteStream([audio()], asyncio.Event())
        voice = self.make_voice(upstream)
        stream = voice.stream("답변", "voice")
        await anext(stream)
        await stream.aclose()
        self.assertTrue(upstream.closed)
        await voice.close()

    async def test_engine_errors_truncation_and_invalid_audio_never_succeed(self):
        cases = [[audio()], [audio(), sse("speech.audio.error")],
                 [sse("speech.audio.done")], [audio(b"odd"), sse("speech.audio.done")],
                 [sse("speech.audio.delta", audio="bad", response_format="pcm")]]
        for chunks in cases:
            with self.subTest(chunks=len(chunks)):
                upstream = ByteStream(chunks)
                voice = self.make_voice(upstream)
                with self.assertRaises((ValueError, RuntimeError)):
                    async for _ in voice.stream("답변", "voice"):
                        pass
                self.assertTrue(upstream.closed)
                await voice.close()

    async def test_reference_upload_keeps_transcript_and_releases_owned_voice(self):
        requests = []
        def handler(request):
            requests.append(request)
            if request.method == "POST":
                fields = request.content.decode("latin1")
                name = fields.split('name="name"\r\n\r\n')[1].split("\r\n")[0]
                return httpx.Response(200, json={"success": True, "voice": {"name": name}})
            return httpx.Response(200, json={"deleted": True})
        voice = OmniVoice(client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), managed=False)
        name = await voice.create_prompt(bytes(4 * 16000 * 2), 16000, "참조 전사")
        self.assertIn("참조 전사".encode(), requests[0].content)
        self.assertIn(b"RIFF", requests[0].content)
        self.assertIn(name, voice.owned_voices)
        await voice.release_prompt(name)
        self.assertFalse(voice.owned_voices)
        self.assertEqual(requests[-1].url.path, "/v1/audio/voices/" + name)
        await voice.close()

    async def test_client_health_reports_actual_worker_mode_and_clears_stale_mode(self):
        ready = [True]
        def handler(request):
            return httpx.Response(200, json={"status": "ready" if ready[0] else "unavailable",
                "voice_mode": "reference_icl", "streaming": "generation_pcm"})
        client = TTSClient("http://test")
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        self.assertTrue(await client.available())
        self.assertEqual(client.streaming_mode, "generation_pcm")
        ready[0] = False
        self.assertFalse(await client.available())
        self.assertIsNone(client.streaming_mode)
        await client.close()


class StreamingWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelled_reference_preparation_releases_engine_reference(self):
        ready = asyncio.Event()
        finish = asyncio.Event()
        released = []
        class Voice:
            model_id = "fake"
            async def create_prompt(self, *args):
                ready.set()
                await finish.wait()
                return "prepared"
            async def release_prompt(self, prompt): released.append(prompt)
            async def stream(self, *args): yield bytes(8000)
        app = create_app(Voice(), token="test")
        routes = {route.path: route.endpoint for route in app.routes}
        async with app.router.lifespan_context(app):
            pending = asyncio.create_task(routes["/voices"](ReferenceRequest(
                pcm=base64.b64encode(bytes(4 * 16000 * 2)).decode(), sample_rate=16000, text="전사"), "test"))
            await ready.wait()
            pending.cancel()
            await asyncio.sleep(0)
            self.assertTrue((await routes["/health"]())["busy"])
            finish.set()
            with self.assertRaises(asyncio.CancelledError): await pending
            self.assertEqual(released, ["prepared"])
            self.assertFalse((await routes["/health"]())["busy"])
            self.assertEqual((await routes["/health"]())["references"], 0)

    async def test_worker_yields_early_closes_generator_and_releases_gate(self):
        class Voice:
            model_id = "fake"
            streaming = "generation_pcm"
            closed = False
            fail = False
            async def create_prompt(self, *args): return "prompt"
            async def stream(self, text, prompt):
                try:
                    yield bytes(8000)
                    if self.fail: raise RuntimeError("upstream failed")
                    await asyncio.Event().wait()
                finally:
                    self.closed = True
        voice = Voice()
        app = create_app(voice, token="test")
        routes = {route.path: route.endpoint for route in app.routes}
        async with app.router.lifespan_context(app):
            ref = await routes["/voices"](ReferenceRequest(
                pcm=base64.b64encode(bytes(4 * 16000 * 2)).decode(), sample_rate=16000, text="전사"), "test")
            response = await routes["/synthesize"](SpeechRequest(text="답변", voice_id=ref["voice_id"]), "test")
            stream = response.body_iterator
            self.assertEqual(json.loads(await anext(stream))["type"], "started")
            self.assertEqual(json.loads(await anext(stream))["type"], "audio")
            self.assertTrue((await routes["/health"]())["busy"])
            await stream.aclose()
            self.assertTrue(voice.closed)
            self.assertFalse((await routes["/health"]())["busy"])
            voice.fail = True
            response = await routes["/synthesize"](SpeechRequest(text="다음", voice_id=ref["voice_id"]), "test")
            with self.assertLogs("tts_server", level="ERROR"):
                packets = [json.loads(row) async for row in response.body_iterator]
            self.assertEqual([p["type"] for p in packets], ["started", "audio", "error"])
            self.assertFalse((await routes["/health"]())["busy"])


if __name__ == "__main__":
    unittest.main()
