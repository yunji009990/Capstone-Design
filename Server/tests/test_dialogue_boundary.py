"""Dialogue starts from an HTTP bundle and retains it during registration downtime."""
import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_server import Settings, create_app
from persona_client import PersonaClient
from test_dialogue import Frontend, SPEECH, QUIET, detector


class BundleLLM:
    # TestClient는 다른 스레드의 루프에서 실행한다. 이 검사는 대기 이벤트가 필요 없다.
    # Python 3.9에서 이전 비동기 검사가 닫은 메인 루프에 Event를 연결하지 않는다.
    def __init__(self):
        self.messages = []

    async def available(self, endpoint=None):
        return True

    async def route(self, messages):
        return "normal"

    async def stream(self, messages, route):
        self.messages = messages
        yield "등록된 인물 정보를 유지한 답변입니다."


class DialogueBoundaryTests(unittest.TestCase):
    def test_registered_dialogue_uses_http_and_survives_registration_downtime(self):
        available = True
        requests = []
        def response(request):
            requests.append(request.url.path)
            if not available:
                raise httpx.ConnectError("offline", request=request)
            return httpx.Response(200, json={"schema_version": 1, "session": "test-person",
                "revision": "a" * 64, "persona": "테스트 도우미", "knowledge": "테스트 지식",
                "rules": "간단히 대답합니다", "voice_sha256": None})
        transport = httpx.AsyncClient(transport=httpx.MockTransport(response))
        self.addCleanup(lambda: asyncio.run(transport.aclose()))
        source = PersonaClient("http://registration", "reader", client=transport)
        with tempfile.TemporaryDirectory() as empty_root:
            settings = Settings(empty_root, "unused", token="dialogue-token", persona_url="http://registration")
            llm = BundleLLM()
            app = create_app(settings, frontend=Frontend(), llm=llm,
                             detector_factory=detector, personas=source)
            with TestClient(app) as web:
                with web.websocket_connect("/dialogue", headers={"X-Token": "dialogue-token"}) as ws:
                    ws.send_json({"type": "start", "protocol": 1, "sample_rate": 16000,
                                  "channels": 1, "format": "pcm_s16le", "session": "test-person"})
                    ready = ws.receive_json()
                    self.assertEqual(ready["type"], "ready")
                    self.assertEqual(ready["persona_revision"], "a" * 64)
                    available = False
                    for _ in range(25):
                        ws.send_bytes(SPEECH)
                    for _ in range(40):
                        ws.send_bytes(QUIET)
                    events = []
                    for _ in range(20):
                        event = ws.receive_json()
                        events.append(event["type"])
                        if event["type"] == "response.done":
                            break
                    self.assertIn("response.done", events)
                    self.assertEqual(requests, ["/internal/personas/test-person"])
                    self.assertIn("테스트 지식", llm.messages[0]["content"])
                    ws.send_json({"type": "stop"})
                    self.assertEqual(ws.receive_json()["type"], "stopped")
                self.assertEqual(list(Path(empty_root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
