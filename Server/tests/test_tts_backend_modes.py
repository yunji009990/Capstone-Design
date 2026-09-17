"""백엔드 교체 시 8003 공개 계약 검사.

참조 전용(reference_audio) 백엔드도 기존 NDJSON·PCM·해제 계약을 그대로 지키는지,
realtime_tts가 참조 기반 모드만 허용하는지 확인한다. 모델·GPU는 쓰지 않는다.
"""
import base64
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tts_server  # noqa: E402
from realtime_tts import TTSClient  # noqa: E402

REFERENCE_PCM = b"\x00\x00" * (16000 * 3)
REFERENCE_BODY = {"pcm": base64.b64encode(REFERENCE_PCM).decode(),
                  "sample_rate": 16000, "text": "참조 전사"}


class FakeVoxVoice:
    """참조 전용 스트리밍 백엔드의 최소 대역. 실제 구현을 복제하지 않는다."""

    model_id = "openbmb/VoxCPM2"
    voice_mode = "reference_audio"
    streaming = "generation_pcm"

    def __init__(self):
        self.started = False
        self.closed = False
        self.released = []

    async def start(self):
        self.started = True

    async def available(self):
        return True

    async def create_prompt(self, pcm, rate, text):
        return {"samples": len(pcm) // 2, "rate": rate, "text": text}

    async def stream(self, text, prompt):
        yield b"\x01\x00" * 4800
        yield b"\x02\x00" * 100

    async def release_prompt(self, prompt):
        self.released.append(prompt)

    async def close(self):
        self.closed = True


class ReferenceAudioContractTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.voice = FakeVoxVoice()
        self.app = tts_server.create_app(voice=self.voice, token="")
        self.lifespan = self.app.router.lifespan_context(self.app)
        await self.lifespan.__aenter__()
        self.http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://tts")

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.lifespan.__aexit__(None, None, None)
        self.assertTrue(self.voice.closed)

    async def client(self):
        client = TTSClient("http://tts")
        await client.close()
        client.http = self.http
        return client

    async def test_client_accepts_reference_audio_and_streams(self):
        client = await self.client()
        self.assertTrue(await client.available())
        self.assertEqual(client.streaming_mode, "generation_pcm")
        self.assertEqual(client.voice_mode, "reference_audio")
        bound = await client.bind(SimpleNamespace(
            pcm=REFERENCE_PCM, sample_rate=16000, text="참조 전사"))
        self.assertEqual(len(bound.voice_id), 32)
        self.assertEqual(bound.voice_mode, "reference_audio")
        packets = [samples async for _, samples in bound.stream("안녕하세요")]
        self.assertEqual(packets, [4800, 100])
        await bound.release()
        self.assertEqual(len(self.voice.released), 1)

    async def test_voices_and_done_report_actual_mode(self):
        prepared = await self.http.post("/voices", json=REFERENCE_BODY)
        self.assertEqual(prepared.json()["voice_mode"], "reference_audio")
        health = await self.http.get("/health")
        self.assertEqual(health.json()["voice_mode"], "reference_audio")
        self.assertEqual(health.json()["model"], "openbmb/VoxCPM2")
        response = await self.http.post("/synthesize", json={
            "text": "끝맺음", "voice_id": prepared.json()["voice_id"]})
        events = [json.loads(line) for line in response.text.splitlines() if line]
        self.assertEqual(events[0]["type"], "started")
        self.assertTrue(all(event["sample_rate"] == 24000 for event in events[1:-1]))
        self.assertTrue(all(len(base64.b64decode(event["pcm"], validate=True)) <= 9600
                            for event in events[1:-1]))
        self.assertEqual(events[-1]["type"], "done")
        self.assertEqual(events[-1]["source_samples"], 4900)
        self.assertEqual(events[-1]["padding_samples"], 0)

    async def test_release_endpoint_frees_prompt(self):
        prepared = await self.http.post("/voices", json=REFERENCE_BODY)
        voice_id = prepared.json()["voice_id"]
        self.assertTrue((await self.http.delete("/voices/" + voice_id)).json()["released"])
        self.assertEqual(len(self.voice.released), 1)
        missing = await self.http.post("/synthesize", json={"text": "x", "voice_id": voice_id})
        self.assertEqual(missing.status_code, 404)


class HealthModeTest(unittest.IsolatedAsyncioTestCase):
    async def health_client(self, mode, model="m"):
        client = TTSClient("http://tts")
        await client.close()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={
                "status": "ready", "voice_mode": mode, "model": model,
                "streaming": "generation_pcm"})))
        return client

    async def available(self, mode):
        client = await self.health_client(mode)
        try:
            return await client.available()
        finally:
            await client.http.aclose()

    async def test_only_reference_modes_are_accepted(self):
        self.assertTrue(await self.available("reference_icl"))
        self.assertTrue(await self.available("reference_audio"))
        self.assertFalse(await self.available("speaker_id"))
        self.assertFalse(await self.available(None))

    async def test_reaction_identity_differs_between_backends(self):
        qwen = await self.health_client("reference_icl", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
        vox = await self.health_client("reference_audio", "openbmb/VoxCPM2")
        try:
            self.assertNotEqual(await qwen.reaction_identity(), await vox.reaction_identity())
        finally:
            await qwen.http.aclose()
            await vox.http.aclose()


if __name__ == "__main__":
    unittest.main()
