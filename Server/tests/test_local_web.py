"""로컬 웹의 주소·자료 격리와 기존 설정 호환을 검증한다. 유료 생성은 호출하지 않는다."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "Server"), str(ROOT / "tools")]
import local_demo
import local_demo_web as web
import local_demo_web_setup as setup
from local_demo_config import DEFAULTS, read_config, save_json


class LocalWebTests(unittest.TestCase):
    def test_old_three_service_profile_keeps_token_models_and_ports(self):
        original = {**DEFAULTS, "token": "test-" * 10, "registration_port": 19000, "llm_model": "chosen"}
        del original["web_port"], original["worker_port"]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            save_json(path, original)
            actual = read_config(path)
        for key, value in original.items():
            self.assertEqual(actual[key], value)
        self.assertEqual(actual["web_port"], 18500)
        self.assertEqual(actual["worker_port"], 18004)

    def test_web_port_must_not_collide_with_existing_service(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "profile.json"
            save_json(path, {**DEFAULTS, "token": "x" * 64, "web_port": 18000})
            with self.assertRaises(ValueError):
                read_config(path)

    def test_local_routes_and_storage_override_inherited_remote_and_fixture(self):
        config = {**DEFAULTS, "token": "x" * 64}
        with tempfile.TemporaryDirectory() as folder, patch.object(web, "DATA", Path(folder)), \
                patch.dict(os.environ, {"SESSION_URL": "http://school.invalid", "MODEL_FIXTURE_GLB": "old.glb"}):
            forced = web.environment(config)
            self.assertEqual(forced["SESSION_URL"], "http://127.0.0.1:18000")
            self.assertEqual(forced["SESSION_TOKEN"], config["token"])
            self.assertEqual(forced["MODEL_FIXTURE_GLB"], "")
            for key in ("SURVEY_DATA_DIR", "WEB_WORKSPACE_DIR", "MODEL_WORKER_ENV", "WEB_ENV_FILE", "INSIGHTFACE_ROOT"):
                self.assertTrue(Path(forced[key]).is_relative_to(folder))

    def test_start_web_does_not_require_openai_or_load_voice_models(self):
        config = {**DEFAULTS, "token": "x" * 64}
        state = {service: {"instance": "test"} for service in local_demo.WEB_SERVICES}
        seen = []
        def fetch(config, service, path, **kwargs):
            seen.append(service)
            return {"instance": "test", "status": "ready"}
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch.dict(sys.modules, {"psutil": object()}), \
                patch.object(web, "preflight"), \
                patch.object(local_demo, "load_state", return_value=state), \
                patch.object(local_demo, "owned", return_value=True), \
                patch.object(local_demo, "port_open", return_value=True), \
                patch.object(local_demo, "fetch", side_effect=fetch), \
                patch.object(Path, "is_file", return_value=True), contextlib.redirect_stdout(io.StringIO()):
            local_demo.start(config, local_demo.WEB_SERVICES)
        self.assertEqual(set(seen), {"registration", "worker", "web"})

    def archive(self, extra=None):
        entries = {"settings.json": json.dumps({"TRIPO_API_KEY": "fake-test-key", "HEAD_BODY_BONE": "head.x"}).encode(),
                   "body.glb": b"glTF-body", "cutout.tflite": b"test-model"}
        entries.update({"face/" + name: b"test-model" for name in setup.FACE_FILES})
        entries.update(extra or {})
        output = io.BytesIO()
        with tarfile.open(fileobj=output, mode="w") as archive:
            for name, data in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        return output.getvalue()

    def test_asset_import_does_not_replace_current_person_or_existing_settings(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(setup, "DATA", Path(folder)):
            root = Path(folder)
            save_json(root / "sessions/current.json", {"session": "preserve"})
            setup.install_archive(self.archive(), Path("blender.exe"))
            self.assertEqual(json.loads((root / "sessions/current.json").read_text())["session"], "preserve")
            env = root / "web.env"
            original = env.read_bytes()
            self.assertNotIn("SESSION_TOKEN", original.decode())
            with self.assertRaises(ValueError):
                setup.install_archive(self.archive(), Path("another.exe"))
            self.assertEqual(env.read_bytes(), original)

    def test_asset_import_rejects_unlisted_paths_before_any_write(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(setup, "DATA", Path(folder)):
            with self.assertRaises(ValueError):
                setup.install_archive(self.archive({"../unexpected": b"no"}), Path("blender.exe"))
            self.assertEqual(list(Path(folder).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
