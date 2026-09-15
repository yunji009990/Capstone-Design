"""Registration/dialogue contract tests; no model weights or real person data."""
import hashlib
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from registration.app import create_app
from persona_client import PersonaClient


def wav_bytes(value=0):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes([value, 0]) * 16000 * 3)
    return output.getvalue()


class PersonaBoundaryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = create_app(self.temp.name, "writer", "reader")
        self.web = TestClient(self.app)
        self.sid = self.register()

    def tearDown(self):
        self.web.close()
        self.temp.cleanup()

    def register(self, value=0):
        response = self.web.post("/session/start", headers={"X-Token": "writer"},
            data={"persona": "테스트 인물", "knowledge": "테스트 지식"},
            files={"voice": ("voice.wav", wav_bytes(value))})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["session"]

    async def test_reader_contract_and_voice_hash_without_shared_files(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app)) as transport:
            client = PersonaClient("http://registration", "reader", client=transport)
            bundle = await client.get(self.sid)
            self.assertEqual(bundle["schema_version"], 1)
            self.assertEqual(bundle["knowledge"], "테스트 지식")
            raw, source = await client.voice(bundle)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), bundle["voice_sha256"])
            self.assertEqual((await client.get())["session"], self.sid)
            self.assertNotIn("model_glb_path", bundle)
            self.assertNotIn("token", bundle)
            self.assertEqual(self.web.post("/session/end", headers={"X-Token": "reader"},
                data={"session": self.sid}).status_code, 403)
            self.assertEqual(self.web.get(f"/internal/personas/{self.sid}",
                headers={"X-Persona-Token": "writer"}).status_code, 403)
            self.assertTrue((Path(self.temp.name) / self.sid / "persona.md").is_file())

    async def test_voice_update_cannot_mix_an_old_bundle_with_new_voice(self):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app)) as transport:
            client = PersonaClient("http://registration", "reader", client=transport)
            old = await client.get(self.sid)
            # 신규 등록은 덮어쓰지 않는다. 기존 자료가 외부에서 바뀌는 경우의 해시 계약은 유지한다.
            (Path(self.temp.name) / self.sid / "voice.wav").write_bytes(wav_bytes(1))
            new = await client.get(self.sid)
            self.assertNotEqual(old["revision"], new["revision"])
            with self.assertRaisesRegex(ValueError, "변경"):
                await client.voice(old)
            self.assertEqual((await client.voice(new))[0], wav_bytes(1))

    async def test_unavailable_or_wrong_schema_does_not_fall_back_to_files(self):
        def unavailable(request):
            raise httpx.ConnectError("offline", request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as transport:
            client = PersonaClient("http://registration", "reader", client=transport)
            with self.assertRaisesRegex(ValueError, "연결"):
                await client.get("person-a")
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"schema_version": 999}))) as transport:
            client = PersonaClient("http://registration", "reader", client=transport)
            with self.assertRaisesRegex(ValueError, "규격"):
                await client.get("person-a")

    async def test_reader_auth_and_identifiers_are_required(self):
        with self.assertRaises(ValueError):
            PersonaClient("http://registration", "")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app)) as transport:
            client = PersonaClient("http://registration", "wrong", client=transport)
            with self.assertRaisesRegex(ValueError, "인증"):
                await client.get("person-a")
            for sid in ("..", "a/b", "a?b", "a%2fb", "", None):
                with self.assertRaises(ValueError):
                    PersonaClient.valid_session(sid)


if __name__ == "__main__":
    unittest.main()
