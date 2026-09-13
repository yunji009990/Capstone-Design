"""공통 프롬프트, 예시/실제 기록 경계, 기존 등록 규칙의 호환 검사."""
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_server import Settings, create_app
from persona_context import BASE_RULES, LEGACY_BASE_RULES, PROMPT_VERSION
from realtime_audio import Observation
from realtime_dialogue import Dialogue, build_persona, load_persona
from test_dialogue import detector, SPEECH, QUIET


PERSONA = """친근한 반말을 쓰는 가상의 형.
[예시 사용법] 아래는 말투의 본보기입니다.
[대화 예시]
사용자: 예시 여행에서 뭘 먹었지?
형: 예시 항구에서 생선구이를 먹었어.
[별도 정보]
취미는 바둑."""


class PromptTests(unittest.TestCase):
    def test_examples_are_system_material_and_never_real_conversation(self):
        system, examples = build_persona(PERSONA, "좋아하는 차는 보리차.")
        dialogue = Dialogue(system, examples, None, None, None, detector=object())
        history = [{"role": "user", "content": "내 발표는 수요일이야."},
                   {"role": "assistant", "content": "수요일 발표구나."}]
        dialogue.history = list(history)
        messages = dialogue.messages(Observation("내 발표 언제라고 했지?"))
        self.assertEqual([m["role"] for m in messages], ["system", "user", "assistant", "user"])
        self.assertEqual(messages[1:-1], history)
        self.assertIn("예시 항구", messages[0]["content"])
        self.assertIn("취미는 바둑", messages[0]["content"])
        self.assertIn("보리차", messages[0]["content"])
        self.assertFalse(any("예시 항구" in m["content"] for m in messages[1:]))
        self.assertEqual(dialogue.history, history)

    def test_custom_rules_supplement_common_rules_and_legacy_default_is_replaced(self):
        custom = "항상 존댓말을 쓰고 상대를 선생님이라고 부릅니다."
        for rules in (custom, LEGACY_BASE_RULES + "\n\n" + custom,
                      LEGACY_BASE_RULES.replace("\n", "\r\n") + "\r\n" + custom):
            with self.subTest(rules=rules[:12]):
                system, _ = build_persona("인물", rules=rules)
                self.assertTrue(system.startswith(BASE_RULES))
                self.assertIn(custom, system)
                self.assertNotIn("40자", system)
        self.assertEqual(build_persona("인물"), build_persona("인물", rules=LEGACY_BASE_RULES))

    def test_unrecognized_custom_rules_and_incomplete_examples_are_preserved(self):
        custom = "짧은 요청에는 40자 안팎으로 말합니다."
        persona = "인물 설정\n[대화 예시]\n사용자: 미완성 예시"
        system, examples = build_persona(persona, rules=custom)
        self.assertIn(persona, system)
        self.assertIn(custom, system)
        self.assertEqual(examples, [])

    def test_legacy_file_loading_does_not_rewrite_persona_files(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "person"
            folder.mkdir()
            originals = {"persona.md": PERSONA, "knowledge.md": "보리차를 좋아한다.",
                         "rules.md": LEGACY_BASE_RULES}
            for name, value in originals.items():
                (folder / name).write_text(value, encoding="utf-8")
            self.assertEqual(load_persona(root, "person"), build_persona(PERSONA, originals["knowledge.md"]))
            self.assertEqual({p.name: p.read_text(encoding="utf-8") for p in folder.iterdir()}, originals)

    def test_registered_and_test_connections_use_the_same_prompt(self):
        captured = []
        class LLM:
            async def available(self, endpoint=None): return True
            async def route(self, messages): return "normal"
            async def stream(self, messages, route):
                captured.append(messages)
                yield "대답"
        class Frontend:
            async def transcribe(self, pcm):
                return Observation("안녕", audio_event="text", language="ko")
        class Personas:
            calls = 0
            async def get(self, session):
                self.calls += 1
                return {"persona": PERSONA, "knowledge": "", "rules": "", "revision": "a" * 64}
        personas = Personas()
        with tempfile.TemporaryDirectory() as root:
            settings = Settings(root, "unused", allow_test_mode=True)
            app = create_app(settings, frontend=Frontend(), llm=LLM(),
                             detector_factory=detector, personas=personas)
            with TestClient(app) as web:
                self.assertEqual(web.get("/health").json()["prompt_version"], PROMPT_VERSION)
                for test_mode in (False, True):
                    with web.websocket_connect("/dialogue") as ws:
                        ws.send_json({"type": "start", "protocol": 1, "sample_rate": 16000,
                                      "channels": 1, "format": "pcm_s16le", "session": "person",
                                      "test_mode": test_mode, "test_persona": PERSONA})
                        self.assertEqual(ws.receive_json()["prompt_version"], PROMPT_VERSION)
                        if test_mode:
                            ws.send_json({"type": "text", "text": "안녕"})
                        else:
                            for _ in range(25): ws.send_bytes(SPEECH)
                            for _ in range(40): ws.send_bytes(QUIET)
                        for _ in range(12):
                            event = ws.receive_json()
                            if event["type"] in ("response.done", "error"): break
                        self.assertEqual(event["type"], "response.done", event)
                        ws.send_json({"type": "stop"})
                        self.assertEqual(ws.receive_json()["type"], "stopped")
            self.assertEqual(captured[0], captured[1])
            self.assertEqual(len(captured[0]), 2)
            self.assertEqual(personas.calls, 1)
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
