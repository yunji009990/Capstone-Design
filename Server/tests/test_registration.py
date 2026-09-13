"""Registration-only tests runnable without any AI/STT packages."""
import io
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from fastapi.testclient import TestClient
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from registration.app import create_app


def reference():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(16000 * 2 * 3))
    return stream.getvalue()


class RegistrationTests(unittest.TestCase):
    def test_registration_persists_without_model_and_end_removes_only_target(self):
        with tempfile.TemporaryDirectory() as root:
            headers = {"X-Token": "test-secret"}
            with TestClient(create_app(root, "test-secret")) as client:
                self.assertEqual(client.get("/session/current").status_code, 403)
                for sid in ("first", "second"):
                    result = client.post("/session/start", headers=headers,
                        data={"session": sid, "persona": "테스트 인물"},
                        files={"voice": ("voice.wav", reference())})
                    self.assertEqual(result.status_code, 200, result.text)
                result = client.post("/session/second/model", headers=headers,
                                     files={"model": ("model.glb", b"glTF-test")})
                self.assertEqual(result.status_code, 200)
            with TestClient(create_app(root, "test-secret")) as client:
                self.assertEqual(client.get("/session/current", headers=headers).json(),
                                 {"session": "second", "has_model": True})
                self.assertEqual(client.get("/session/second/model.glb", headers=headers).content, b"glTF-test")
                client.post("/session/end", headers=headers, data={"session": "second"})
                self.assertIsNone(client.get("/session/current", headers=headers).json()["session"])
                self.assertTrue((Path(root) / "first/voice.wav").exists())
                self.assertFalse((Path(root) / "second").exists())
            with TestClient(create_app(root, "test-secret")) as client:
                self.assertIsNone(client.get("/session/current", headers=headers).json()["session"])

    def test_invalid_upload_or_path_does_not_create_or_delete_data(self):
        with tempfile.TemporaryDirectory() as root:
            sentinel = Path(root) / "keep.txt"
            sentinel.write_text("keep")
            with TestClient(create_app(root, "")) as client:
                for sid in ("..", "../outside", ".hidden", "a/b", "a\\b"):
                    self.assertEqual(client.post("/session/end", data={"session": sid}).status_code, 400)
                self.assertEqual(client.post("/session/start", data={"persona": "test", "session": "bad"},
                    files={"voice": ("voice.wav", b"broken")}).status_code, 400)
                self.assertFalse((Path(root) / "bad").exists())
            self.assertEqual(sentinel.read_text(), "keep")


if __name__ == '__main__':
    unittest.main()
