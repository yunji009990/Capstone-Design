"""배포본의 경로 격리·환경변수 키·생성 차단 계약."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "Server"), str(ROOT / "tools")]
import local_demo_launcher as launcher
import local_demo_web as web


class BundleTests(unittest.TestCase):
    def test_packaged_config_survives_new_install_path_and_uses_fresh_local_credentials(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            server = base / "folder with spaces/Backend/Server"
            server.mkdir(parents=True)
            shutil.copy2(ROOT / "Server/local_demo_config.py", server / "local_demo_config.py")
            (server.parent / "bundle.json").write_text('{}')
            script = """import json,sys
sys.path.insert(0,sys.argv[1])
import local_demo_config as c
first=c.initialize();second=c.initialize()
print(json.dumps({'packaged':c.PACKAGED,'data':str(c.DATA),'models':str(c.MODELS),
 'same_token':first['token']==second['token'],'local':json.loads(c.CONNECTION.read_text())['enabled'],
 'ports':[first[n+'_port'] for n in ('registration','web','tts','dialogue')]}))
"""
            result = subprocess.run([sys.executable, "-c", script, str(server)], capture_output=True,
                                    text=True, check=True, env={**os.environ, "LOCALAPPDATA": str(base / "user-data")})
            data = json.loads(result.stdout)
            self.assertTrue(data["packaged"] and data["same_token"] and data["local"])
            self.assertTrue(Path(data["data"]).is_relative_to(base / "user-data"))
            self.assertTrue(Path(data["models"]).is_relative_to(server.parent))
            self.assertEqual(data["ports"], [19000, 19500, 19003, 19002])

    def test_launcher_uses_environment_key_and_refuses_to_start_without_it(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "synthetic-key"}):
            self.assertEqual(launcher.read_openai_key(), "synthetic-key")
        with patch.object(launcher, "PACKAGED", True), patch.object(Path, "is_file", return_value=True), \
                patch.object(launcher, "read_openai_key", return_value=""), patch.object(launcher.demo, "start") as start:
            with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
                launcher.run()
            start.assert_not_called()

    def test_packaged_web_accepts_friend_but_rejects_generation_and_admin_mutations(self):
        app = FastAPI()
        generate = Mock()
        @app.post("/publish_direct")
        def publish():
            generate()
            return {"queued": True}
        @app.post("/test/friend/start")
        def prepared():
            return {"model_reused": True}
        with patch.object(web, "PACKAGED", True), patch.object(web, "configure"), \
                patch.object(web.importlib, "import_module", return_value=types.SimpleNamespace(app=app)):
            api = web.web_app({})
        with TestClient(api) as client:
            self.assertTrue(client.post("/test/friend/start").json()["model_reused"])
            for path in ("/publish_direct", "/admin/action", "/tripo_key"):
                self.assertEqual(client.post(path).status_code, 403)
            self.assertEqual(client.get("/health").status_code, 200)
        generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
