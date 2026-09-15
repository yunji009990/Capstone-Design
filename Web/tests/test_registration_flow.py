"""웹 등록 회귀 검사. 실제 경로 함수·로컬 등록 API를 쓰고 음성 변환·Tripo만 대체한다."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
import wave
from unittest.mock import Mock

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Server"))
sys.path.insert(0, str(ROOT / "Survey"))
from Web import persona, registration_input
from core import database
from registration.app import create_app


def audio():
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(bytes(16000 * 2 * 3))
    return stream.getvalue()


class WebRegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        provider = TestClient(create_app(self.root / "people", "", "reader"))
        self.provider = provider
        self.addCleanup(provider.close)
        old_db = database.DB_PATH
        database.DB_PATH = self.root / "survey.db"
        self.addCleanup(setattr, database, "DB_PATH", old_db)
        (self.root / "extract").mkdir()
        (self.root / "extract/ref.wav").write_bytes(audio())
        def directory(sid):
            dest = self.root / "assets" / sid
            dest.mkdir(parents=True, exist_ok=True)
            return dest
        self.post = Mock(side_effect=lambda url, **kw: provider.post("/session/start", data=kw["data"], files=kw["files"]))
        self.convert = Mock(return_value=(audio(), 3.0, {}))
        app = FastAPI()
        self.scope = dict(app=app, Form=Form, File=File, UploadFile=UploadFile, HTTPException=HTTPException,
            os=os, json=json, persona_builder=persona, parse_survey=registration_input.parse_survey,
            registration_survey=registration_input.registration_survey, survey_revision=registration_input.survey_revision,
            survey_db=database, survey_store=types.SimpleNamespace(session_dir=directory),
            survey_jobs=types.SimpleNamespace(dispatch_model_job=Mock(return_value="queued")),
            httpx=types.SimpleNamespace(post=self.post), SESSION_URL="http://local", _headers=lambda: {},
            _to_wav24=self.convert, LAST_REF=str(self.root / "last.wav"), ALLOWED={".wav"},
            JOBS={"test-job": {"result": {"speakers": [{"spk_id": "speaker", "ref": {"file": "ref.wav"}}]}}},
            job_dir=lambda job: str(self.root))
        # Web.app의 무거운 음성 라이브러리·환경 로딩을 실행하지 않고, 수정 대상 함수 그대로 라우팅한다.
        tree = ast.parse((ROOT / "Web/app.py").read_text(encoding="utf-8"))
        names = {"_record_and_model", "persona_from_survey", "publish", "publish_direct"}
        selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.assertEqual(len(selected), len(names))
        exec(compile(ast.Module(body=selected, type_ignores=[]), "Web/app.py", "exec"), self.scope)
        self.web = TestClient(app)
        self.addCleanup(self.web.close)
        self.answers = {"relation": "할머니", "user_name": "가상사용자", "shared_memories": ["가상 추억"],
                        "consent_image": True, "consent_voice": True, "consent_understand": True}

    def draft(self, answers=None):
        raw = json.dumps(answers or self.answers, ensure_ascii=False)
        response = self.web.post("/persona", data={"survey": raw})
        self.assertEqual(response.status_code, 200, response.text)
        return {"survey": raw, **response.json()}

    def submit(self, form, route="/publish_direct", image=False):
        files = {"voice": ("voice.wav", audio())} if route == "/publish_direct" else {}
        if route == "/publish":
            form = {**form, "job": "test-job", "spk_id": "speaker"}
        if image:
            files["image"] = ("front.png", b"synthetic image")
        return self.web.post(route, data=form, files=files)

    def test_both_voice_routes_register_reviewed_text_with_new_ids_and_complete_survey(self):
        form = self.draft()
        form["persona"] += "\n확인하면서 직접 편집한 문장"
        ids = []
        for route in ("/publish_direct", "/publish"):
            result = self.submit(form, route, image=True)
            self.assertEqual(result.status_code, 200, result.text)
            item = result.json()
            ids.append(item["session"])
            self.assertTrue(item["survey_saved"])
            self.assertTrue(item["model_queued"])
            self.assertNotIn("session", self.post.call_args.kwargs["data"])
            bundle = self.provider.get(f"/internal/personas/{item['session']}", headers={"X-Persona-Token": "reader"}).json()
            self.assertEqual(bundle["persona"], form["persona"])
            self.assertEqual(bundle["knowledge"], form["knowledge"])
            self.assertEqual(database.get_session(item["session"])["payload"], self.answers)
        self.assertNotEqual(*ids)

    def test_empty_unprepared_stale_or_client_named_registration_is_rejected_before_audio_work(self):
        good = self.draft()
        cases = [{**good, "persona": " "}, {**good, "survey_revision": ""},
                 {**good, "survey": "{}"}, {**good, "survey": "[]"},
                 {**good, "survey": json.dumps({**self.answers, "relation": "어머니"})},
                 {**good, "session": "old-id"}]
        for route in ("/publish_direct", "/publish"):
            for form in cases:
                with self.subTest(route=route, form=list(form)):
                    self.assertEqual(self.submit(form, route).status_code, 400)
        self.post.assert_not_called()
        self.convert.assert_not_called()

    def test_changed_survey_gets_new_revision_and_new_persona(self):
        first = self.draft()
        changed = self.draft({**self.answers, "relation": "어머니", "shared_memories": ["새 추억"]})
        self.assertNotEqual(first["survey_revision"], changed["survey_revision"])
        self.assertIn("어머니", changed["persona"])
        self.assertIn("새 추억", changed["knowledge"])
        self.assertEqual(self.submit(changed).status_code, 200)

    def test_no_relation_or_invalid_text_fields_cannot_make_a_default_person(self):
        for value in ({}, [], {"relation": " "}, {"relation": "친구", "user_name": []},
                      {"relation": "친구", "shared_memories": "wrong"}):
            self.assertEqual(self.web.post("/persona", data={"survey": json.dumps(value)}).status_code, 400)

    def test_survey_write_failure_is_reported_and_does_not_queue_a_model(self):
        insert = Mock(side_effect=RuntimeError("synthetic storage failure"))
        self.scope["survey_db"] = types.SimpleNamespace(insert_session=insert)
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.submit(self.draft(), image=True).json()
        self.assertFalse(result["survey_saved"])
        self.assertFalse(result["model_queued"])
        self.assertIn("설문 기록", result["registration_warning"])
        self.scope["survey_jobs"].dispatch_model_job.assert_not_called()

    def test_model_queue_failure_is_distinct_from_successful_person_and_survey(self):
        self.scope["survey_jobs"].dispatch_model_job.side_effect = RuntimeError("synthetic queue failure")
        with contextlib.redirect_stdout(io.StringIO()):
            result = self.submit(self.draft(), image=True).json()
        self.assertTrue(result["survey_saved"])
        self.assertFalse(result["model_queued"])
        self.assertIn("모델 작업", result["registration_warning"])

    def test_browser_script_regressions(self):
        node = shutil.which("node")
        self.assertIsNotNone(node, "웹 화면 회귀 검사에는 Node.js 22 이상이 필요합니다.")
        result = subprocess.run([node, "--test", "--test-reporter=tap", str(Path(__file__).with_suffix(".cjs"))],
                                capture_output=True, text=True, encoding="utf-8", timeout=40)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("# fail 0", result.stdout)


if __name__ == "__main__":
    unittest.main()
