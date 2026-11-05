"""API 변환·개인 자료 복사·서비스 분리를 모의 검사한다. 실제 모델 검사는 별도다."""
import asyncio
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch
import wave

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "Server"))
sys.path.insert(0, str(ROOT / "tools"))
import local_demo
from local_demo_config import DEFAULTS, connection, read_config, save_json
from local_demo_providers import OpenAIFrontend, OpenAILLMClient
from realtime_llm import ContextLimitError, ModelEndpoint


def endpoint(**extra):
    return ModelEndpoint("https://api.openai.com/v1/chat/completions", "gpt-5.4-mini",
                         {"reasoning_effort": "none", **extra}, "test-secret", 384)


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_memory_request_removes_gemma_parameters(self):
        calls = []
        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{}'}}]})
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        llm = OpenAILLMClient(endpoint(skip_special_tokens=False), client=client, context_limit=24000)
        try:
            self.assertEqual(await llm.memory_json("JSON으로 판단하세요.", {"text": "안녕"}, 768), {})
            self.assertEqual(len(calls), 1)  # vLLM /tokenize 요청을 보내지 않는다.
            body = json.loads(calls[0].content)
            self.assertFalse(body["store"])
            self.assertEqual(body["response_format"]["type"], "json_object")
            self.assertEqual(body["reasoning_effort"], "none")
            for field in ("chat_template_kwargs", "skip_special_tokens", "temperature", "top_p"):
                self.assertNotIn(field, body)
        finally:
            await llm.close()

    async def test_context_trim_preserves_system_and_current_user(self):
        llm = OpenAILLMClient(endpoint(), context_limit=800)
        messages = [{"role": "system", "content": "인물 설정"},
                    {"role": "user", "content": "과거" * 500},
                    {"role": "assistant", "content": "옛 답변"},
                    {"role": "user", "content": "지금 질문"}]
        try:
            self.assertEqual(await llm.fit_messages(messages, llm.normal), [messages[0], messages[-1]])
            with self.assertRaises(ContextLimitError):
                await llm.fit_messages(messages, llm.normal, trim=False)
            with self.assertRaises(ContextLimitError):
                await llm.fit_messages([messages[0], {"role": "user", "content": "질문" * 600}], llm.normal)
        finally:
            await llm.close()

    async def test_stream_uses_openai_and_filters_hidden_reasoning(self):
        packet = {"choices": [{"delta": {"content": "안녕.", "reasoning_content": "숨은 내용"}, "finish_reason": "stop"}]}
        def respond(request):
            body = json.loads(request.content)
            self.assertTrue(body["stream"])
            self.assertEqual(body["reasoning_effort"], "low")
            return httpx.Response(200, text="data: " + json.dumps(packet) + "\n\ndata: [DONE]\n\n")
        client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        llm = OpenAILLMClient(endpoint(), endpoint(reasoning_effort="low"), client=client)
        try:
            texts = [v async for v in llm.stream([{"role": "user", "content": "안녕"}], "reasoning")]
            self.assertEqual("".join(texts), "안녕.")
        finally:
            await llm.close()

    async def test_stt_uploads_pcm_as_wav_and_preserves_observation_contract(self):
        pcm = b"\x00\x10" * 24000
        def respond(request):
            self.assertEqual(str(request.url), "https://api.openai.com/v1/audio/transcriptions")
            content = request.content
            start = content.index(b"RIFF")
            size = int.from_bytes(content[start + 4:start + 8], "little") + 8
            with wave.open(io.BytesIO(content[start:start + size]), "rb") as sound:
                self.assertEqual(sound.getframerate(), 24000)
                self.assertEqual(sound.readframes(sound.getnframes()), pcm)
            self.assertIn(b"gpt-transcribe", content)
            return httpx.Response(200, json={"text": " 안녕하세요. "})
        frontend = OpenAIFrontend("test-secret", client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        try:
            observation = await frontend.transcribe(pcm, 24000)
            self.assertEqual(observation.text, "안녕하세요.")
            self.assertEqual(observation.quality, [])
            self.assertEqual(observation.audio_event, "unknown")
            self.assertEqual(observation.prosody["duration_sec"], 1)
            self.assertNotIn("quality", observation.metadata())
        finally:
            await frontend.aclose()

    async def test_silent_and_invalid_audio_does_not_call_api(self):
        def respond(request):
            raise AssertionError("API must not be called")
        frontend = OpenAIFrontend("test-secret", client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        try:
            self.assertEqual((await frontend.transcribe(bytes(16000))).text, "")
            for pcm, rate in ((b"x", 16000), (b"xx", 1), (bytes(16000 * 62), 16000)):
                with self.assertRaises(ValueError):
                    await frontend.transcribe(pcm, rate)
        finally:
            await frontend.aclose()

    async def test_stt_cancel_closes_request_and_does_not_finish_stale_transcript(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def respond(request):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        frontend = OpenAIFrontend("test-secret", client=httpx.AsyncClient(transport=httpx.MockTransport(respond)))
        task = asyncio.create_task(frontend.transcribe(b"\x00\x10" * 1600))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(cancelled.is_set())
        finally:
            await frontend.aclose()

    async def test_stt_failure_does_not_return_empty_success(self):
        frontend = OpenAIFrontend("test-secret", client=httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(429, json={"error": {"code": "limit"}}))))
        try:
            with self.assertRaises(httpx.HTTPStatusError):
                await frontend.transcribe(b"\x00\x10" * 1600)
        finally:
            await frontend.aclose()


class IsolationTests(unittest.TestCase):
    def test_stop_before_http_ready_cleans_only_recorded_service(self):
        state = {"tts": {"pid": 123, "created": 1, "instance": "owned"},
                 "registration": {"pid": 456}}
        record = state["tts"]
        with patch.object(local_demo, "owned", return_value=True), \
                patch.object(local_demo, "fetch", side_effect=ConnectionError), \
                patch.object(local_demo, "terminate_owned_tree") as terminate, \
                patch.object(local_demo, "save_json"):
            local_demo.stop_service(DEFAULTS, state, "tts")
        terminate.assert_called_once_with(record, "tts")
        self.assertEqual(state, {"registration": {"pid": 456}})

    def test_stop_refuses_mismatched_service_identity(self):
        state = {"tts": {"pid": 123, "created": 1, "instance": "owned"}}
        with patch.object(local_demo, "owned", return_value=True), \
                patch.object(local_demo, "fetch", return_value={"service": "tts", "instance": "different"}), \
                patch.object(local_demo, "terminate_owned_tree") as terminate:
            with self.assertRaises(RuntimeError):
                local_demo.stop_service(DEFAULTS, state, "tts")
        terminate.assert_not_called()
        self.assertIn("tts", state)

    def test_force_stop_refuses_unowned_child_before_termination(self):
        root, child = MagicMock(), MagicMock()
        root.children.return_value = [child]
        child.cmdline.return_value = [str(local_demo.PYTHON), str(local_demo.SCRIPT), "_serve", "tts"]
        child.environ.return_value = {"LOCAL_DEMO_INSTANCE": "not-owned"}
        from types import SimpleNamespace
        fake = SimpleNamespace(Process=lambda _: root, NoSuchProcess=ProcessLookupError)
        with patch.object(local_demo, "owned", return_value=True), patch.dict("sys.modules", {"psutil": fake}):
            with self.assertRaises(RuntimeError):
                local_demo.terminate_owned_tree({"pid": 123, "instance": "owned"}, "tts")
        root.terminate.assert_not_called()
        child.terminate.assert_not_called()

    def test_config_has_separate_ports_and_no_api_key(self):
        config = {**DEFAULTS, "token": "x" * 64}
        value = connection(config, True)
        self.assertEqual(value["serverUrl"], "http://127.0.0.1:18000")
        self.assertEqual(value["dialogueServerUrl"], "http://127.0.0.1:18002")
        self.assertNotIn("OPENAI_API_KEY", value)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "config.json"
            config["tts_port"] = config["registration_port"]
            save_json(path, config)
            with self.assertRaises(ValueError):
                read_config(path)

    def archive(self, *, extra=None):
        buffer = io.BytesIO()
        entries = {"current.json": b'{"session":"test-person"}', "test-person/persona.md": b"profile",
                   "test-person/voice.wav": b"voice", "test-person/model.glb": b"model"}
        if extra:
            entries.update(extra)
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            for name, raw in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(raw)
                archive.addfile(info, io.BytesIO(raw))
        return buffer.getvalue()

    def test_import_is_local_and_changed_person_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(local_demo, "DATA", Path(folder)):
            with patch("builtins.print"):
                local_demo.import_archive(self.archive())
                local_demo.import_archive(self.archive())
            model = Path(folder) / "sessions/test-person/model.glb"
            model.write_bytes(b"user-edit")
            with self.assertRaises(ValueError):
                local_demo.import_archive(self.archive())
            self.assertEqual(model.read_bytes(), b"user-edit")

    def test_import_rejects_traversal_before_writing_assets(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(local_demo, "DATA", Path(folder)):
            with self.assertRaises(ValueError):
                local_demo.import_archive(self.archive(extra={"test-person/../../escape": b"bad"}))
            self.assertFalse((Path(folder) / "sessions").exists())

    def test_tts_reference_is_in_memory_on_windows(self):
        from local_demo_apps import tts_app
        from fastapi.testclient import TestClient
        from tts_vox import VoxVoice
        seen = []
        class FakeNative:
            def prepare(self, file, transcript):
                self.outer.assertIsInstance(file, io.BytesIO)
                with wave.open(file, "rb") as sound:
                    seen.append(sound.getnframes())
                return {"mode": "reference"}
            def close(self): pass
        native = FakeNative()
        native.outer = self
        async def begin(voice): voice._vox = native
        config = {**DEFAULTS, "token": "x" * 64}
        native._model = object()
        with patch.dict("os.environ"), patch.dict("sys.modules", {"torch": MagicMock()}), patch.object(VoxVoice, "start", begin), \
                patch("local_demo_apps.configure_local_vox"), patch("local_demo_apps.release_cuda_cache"):
            app = tts_app(config)
            with TestClient(app):
                prompt = app.state.voice._prepare_sync(b"\x00\x10" * 16000, 16000, None)
            self.assertEqual(prompt["mode"], "reference")
            self.assertEqual(seen, [16000])


if __name__ == "__main__":
    unittest.main()
