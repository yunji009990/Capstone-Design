"""준비된 친구의 실제 등록 API 계약. 외부 모델·개인 자료를 사용하지 않는다."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import wave
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
for folder in ("Web", "Survey", "Server"):
    sys.path.insert(0, str(ROOT / folder))
import prepared_friend
import survey_v2
from core import database, storage
from registration.app import create_app


class PreparedFriendTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bundle = self.root / "prepared"
        self.bundle.mkdir()
        answers = json.loads((ROOT / "Web/static/presets_v2.json").read_text(encoding="utf-8"))["profiles"][0]["answers"]
        stream = io.BytesIO()
        with wave.open(stream, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(bytes(16000 * 2 * 3))
        self.assets = {"voice.wav": stream.getvalue(), "model.glb": b"glTF-synthetic-model",
                       "photo.png": b"\x89PNG\r\n\x1a\nsynthetic", "survey.json": json.dumps(answers).encode()}
        self.compiled = survey_v2.compile_survey(survey_v2.validate(answers))
        self.manifest = {"version": 1, "photo": "photo.png", "survey_revision": self.compiled["survey_revision"],
                         "sha256": {n: hashlib.sha256(b).hexdigest() for n, b in self.assets.items()}}
        for name, raw in self.assets.items():
            (self.bundle / name).write_bytes(raw)
        (self.bundle / "manifest.json").write_text(json.dumps(self.manifest), encoding="utf-8")
        for obj, name, value in ((database, "DB_PATH", self.root / "survey.db"),
                                  (storage, "ASSETS_ROOT", self.root / "photos")):
            old = getattr(obj, name)
            setattr(obj, name, value)
            self.addCleanup(setattr, obj, name, old)
        self.provider = TestClient(create_app(self.root / "sessions", "test-token", "reader"))
        self.addCleanup(self.provider.close)
        self.post = Mock(side_effect=lambda url, **kw: self.provider.post("/session/start", **kw))
        upstream = Mock()
        upstream.__enter__ = Mock(return_value=upstream)
        upstream.__exit__ = Mock(return_value=False)
        upstream.post = self.post
        transport = patch.object(prepared_friend.httpx, "Client", return_value=upstream)
        transport.start()
        self.addCleanup(transport.stop)
        app = FastAPI()
        app.include_router(prepared_friend.create_router(self.bundle, "http://registration", "test-token"))
        self.client = TestClient(app)
        self.addCleanup(self.client.close)
        self.info = self.client.get("/test/friend").json()

    def start(self, revision=None):
        return self.client.post("/test/friend/start", data={"revision": revision or self.info["revision"]})

    def test_ready_session_reuses_exact_voice_model_photo_and_does_not_queue_generation(self):
        result = self.start()
        self.assertEqual(result.status_code, 200)
        data = result.json()
        self.assertTrue(data["has_model"])
        self.assertTrue(data["model_reused"])
        self.assertFalse(data["model_queued"])
        self.assertTrue(data["survey_saved"])
        sid = data["session"]
        for name in ("voice.wav", "model.glb"):
            self.assertEqual((self.root / "sessions" / sid / name).read_bytes(), self.assets[name])
        self.assertEqual(storage.find_image(sid).read_bytes(), self.assets["photo.png"])
        self.assertEqual(database.get_session(sid)["model_status"], "ready")
        self.assertEqual(database.list_sessions_needing_model(), [])
        self.assertEqual(self.post.call_count, 1)
        self.assertEqual(set(self.post.call_args.kwargs["files"]), {"voice", "model"})
        self.assertEqual(self.post.call_args.kwargs["data"]["persona"], self.compiled["persona"])
        # 다시 눌러도 원본·기존 세션을 덮어쓰지 않고 새 ID를 발급한다.
        self.assertNotEqual(sid, self.start().json()["session"])
        self.assertEqual((self.bundle / "model.glb").read_bytes(), self.assets["model.glb"])

    def test_media_preview_is_exact_and_not_cached(self):
        for key, name in (("photo_url", "photo.png"), ("voice_url", "voice.wav")):
            result = self.client.get(self.info[key])
            self.assertEqual(result.content, self.assets[name])
            self.assertEqual(result.headers["cache-control"], "no-store")
        self.post.assert_not_called()

    def test_missing_or_changed_assets_never_start_or_generate(self):
        for name in ("photo.png", "voice.wav", "model.glb"):
            with self.subTest(name=name):
                (self.bundle / name).unlink()
                self.assertEqual(self.start().status_code, 503)
                (self.bundle / name).write_bytes(b"changed")
                self.assertEqual(self.start().status_code, 503)
                (self.bundle / name).write_bytes(self.assets[name])
        self.post.assert_not_called()

    def test_stale_revision_never_registers_and_no_fallback_when_unconfigured(self):
        self.assertEqual(self.start("0" * 64).status_code, 409)
        app = FastAPI()
        app.include_router(prepared_friend.create_router(None, "http://registration", "test-token"))
        with TestClient(app) as client:
            self.assertEqual(client.post("/test/friend/start", data={"revision": "0" * 64}).status_code, 503)
        self.post.assert_not_called()

    def test_registration_failure_is_not_reported_as_ready(self):
        self.post.side_effect = None
        self.post.return_value = Mock(json=lambda: {"session": "f" * 32, "has_model": False})
        self.assertEqual(self.start().status_code, 502)
        self.assertFalse((self.root / "survey.db").exists())


if __name__ == "__main__":
    unittest.main()
