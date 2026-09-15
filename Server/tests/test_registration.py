"""Registration-only tests runnable without any AI/STT packages."""
import io
import sys
import tempfile
import unittest
import uuid
import wave
from unittest.mock import patch
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
                ids = []
                for _ in range(2):
                    result = client.post("/session/start", headers=headers,
                        data={"persona": "테스트 인물"},
                        files={"voice": ("voice.wav", reference())})
                    self.assertEqual(result.status_code, 200, result.text)
                    ids.append(result.json()["session"])
                first, second = ids
                self.assertNotEqual(first, second)
                self.assertTrue(all(uuid.UUID(sid).version == 4 for sid in ids))
                result = client.post(f"/session/{second}/model", headers=headers,
                                     files={"model": ("model.glb", b"glTF-test")})
                self.assertEqual(result.status_code, 200)
            with TestClient(create_app(root, "test-secret")) as client:
                self.assertEqual(client.get("/session/current", headers=headers).json(),
                                 {"session": second, "has_model": True})
                self.assertEqual(client.get(f"/session/{second}/model.glb", headers=headers).content, b"glTF-test")
                client.post("/session/end", headers=headers, data={"session": second})
                self.assertIsNone(client.get("/session/current", headers=headers).json()["session"])
                self.assertTrue((Path(root) / first / "voice.wav").exists())
                self.assertFalse((Path(root) / second).exists())
            with TestClient(create_app(root, "test-secret")) as client:
                self.assertIsNone(client.get("/session/current", headers=headers).json()["session"])

    def test_invalid_upload_or_path_does_not_create_or_delete_data(self):
        with tempfile.TemporaryDirectory() as root:
            sentinel = Path(root) / "keep.txt"
            sentinel.write_text("keep")
            with TestClient(create_app(root, "")) as client:
                for sid in ("..", "../outside", ".hidden", "a/b", "a\\b"):
                    self.assertEqual(client.post("/session/end", data={"session": sid}).status_code, 400)
                self.assertEqual(client.post("/session/start", data={"persona": "test"},
                    files={"voice": ("voice.wav", b"broken")}).status_code, 400)
                self.assertEqual(list(Path(root).iterdir()), [sentinel])
            self.assertEqual(sentinel.read_text(), "keep")

    def test_client_id_cannot_overwrite_an_existing_person(self):
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(root, "")) as client:
            first = client.post("/session/start", data={"persona": "처음 인물"},
                                files={"voice": ("voice.wav", reference())}).json()["session"]
            for sid in (first, "custom-id"):
                response = client.post("/session/start", data={"session": sid, "persona": "다른 인물"},
                                       files={"voice": ("voice.wav", reference())})
                self.assertEqual(response.status_code, 400)
            self.assertEqual((Path(root) / first / "persona.md").read_text(encoding="utf-8"), "처음 인물")
            self.assertEqual(client.get("/session/current").json()["session"], first)

    def test_collision_allocates_another_id_without_touching_old_files(self):
        with tempfile.TemporaryDirectory() as root, TestClient(create_app(root, "")) as client:
            collision, fresh = uuid.uuid4(), uuid.uuid4()
            old = Path(root) / collision.hex
            old.mkdir()
            (old / "persona.md").write_text("keep", encoding="utf-8")
            values = iter((collision, fresh))
            original = uuid.uuid4
            with patch("registration.app.uuid.uuid4", side_effect=lambda: next(values, None) or original()):
                response = client.post("/session/start", data={"persona": "새 인물"},
                                       files={"voice": ("voice.wav", reference())})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["session"], fresh.hex)
            self.assertEqual((old / "persona.md").read_text(), "keep")

    def test_old_folders_without_current_registration_are_not_auto_selected(self):
        with tempfile.TemporaryDirectory() as root:
            legacy = Path(root) / "old-example"
            legacy.mkdir()
            (legacy / "persona.md").write_text("old example")
            with TestClient(create_app(root, "", "reader")) as client:
                self.assertIsNone(client.get("/session/current").json()["session"])
                self.assertFalse(client.get("/health").json()["ready_to_talk"])
                self.assertEqual(client.get("/internal/current-persona", headers={"X-Persona-Token": "reader"}).status_code, 404)


if __name__ == '__main__':
    unittest.main()
