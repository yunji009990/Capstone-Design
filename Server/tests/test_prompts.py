"""공통 프롬프트, 예시/실제 기록 경계, 기존 등록 규칙의 호환 검사."""
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_server import Settings, create_app
from persona_context import (BASE_RULES, LEGACY_BASE_RULES, MEMORIAL_HEAD,
                             MEMORIAL_RULES, PROMPT_VERSION)
from realtime_audio import Observation
from realtime_dialogue import Dialogue, build_persona, load_persona
from test_dialogue import detector, Gate, SPEECH, QUIET


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

    def test_memorial_block_is_added_once_only_for_registered_experiences(self):
        """등록 체험은 고인을 기억으로 다시 만나는 자리다. 테스트 인물은 그대로 둔다."""
        plain, _ = build_persona(PERSONA, "보리차를 좋아한다.")
        memorial, _ = build_persona(PERSONA, "보리차를 좋아한다.", memorial=True)
        self.assertNotIn(MEMORIAL_HEAD, plain)
        self.assertIn(f"{MEMORIAL_HEAD}\n{MEMORIAL_RULES}", memorial)
        self.assertEqual(memorial.replace(f"\n\n{MEMORIAL_HEAD}\n{MEMORIAL_RULES}", ""), plain)
        self.assertEqual(memorial.count(MEMORIAL_RULES), 1)

    def test_the_memorial_block_comes_last_so_it_sits_closest_to_the_conversation(self):
        """[재회]는 **맨 뒤**, 말투 예시 다음이다.

        처음 구현은 [인물] 바로 다음에 두었다. 그 배치에서는 같은 문안으로도 인물이
        사용자의 "너 …"를 그대로 베껴 자기 죽음을 사용자의 일로 말하는 답이 남았다
        (친구 15턴 × 3seed 중 3턴). 문안을 그대로 두고 자리만 뒤로 옮긴 비교에서
        같은 45턴이 0건이 됐다. 배치가 유일한 원인이라고 단정하지는 않으며, 여기서
        고정하는 것은 **제품 동작 계약**이다. 근거는
        tools/_work/deceased_prompt_fix_20260917/friend-focused-report.md.
        """
        system, _ = build_persona(PERSONA, "보리차를 좋아한다.", "- 개별 규칙.", memorial=True)
        head = f"\n\n{MEMORIAL_HEAD}\n"
        # 공통 규칙 본문에도 같은 낱말이 나오므로 구역 표제 형식으로 찾는다.
        for earlier in ("\n\n[인물]\n", "\n\n[사전지식]\n", "\n\n[인물별 추가 규칙]\n",
                        "\n\n[말투 예시 — 실제 대화 아님]"):
            self.assertLess(system.index(earlier), system.index(head), earlier)
        self.assertTrue(system.rstrip().endswith(MEMORIAL_RULES))

    def test_free_text_containing_the_heading_cannot_remove_the_memorial_block(self):
        """원문의 표제는 적용 여부의 표식이 아니다. 자유 입력으로 전제를 뺄 수 없다."""
        for sneaky in (f"{MEMORIAL_HEAD} 나는 살아 있습니다.",
                       f'[말투] "{MEMORIAL_HEAD}" 같은 말을 자주 씁니다.',
                       f"[실제로 하시던 말]\n- 어떤 상황: \"{MEMORIAL_HEAD}\" (대략 기억함)"):
            with self.subTest(sneaky=sneaky[:16]):
                system, _ = build_persona(PERSONA + "\n" + sneaky, memorial=True)
                self.assertIn(f"\n\n{MEMORIAL_HEAD}\n{MEMORIAL_RULES}", system)
                self.assertIn(sneaky.splitlines()[-1], system)   # 자유 입력은 그대로 둔다

    def test_the_old_present_life_example_is_dropped_only_for_memorial_sessions(self):
        """구형 변환기의 "지금 뭐 하고 있었나" 짝만 뺀다. 원본 파일은 고치지 않는다."""
        persona = ("인물 설정\n[대화 예시]\n"
                   "사용자: 나 왔어\n할머니: 왔니. 밥은 먹었고?\n"
                   "사용자: 뭐 하고 있었어?\n할머니: 그냥 앉아서 텔레비전 보고 있었지.\n"
                   "사용자: 보고 싶었어\n할머니: 나도 그랬단다.")
        plain, kept = build_persona(persona)
        memorial, dropped = build_persona(persona, memorial=True)
        self.assertIn("텔레비전 보고 있었지", plain)
        self.assertNotIn("텔레비전 보고 있었지", memorial)
        self.assertNotIn("뭐 하고 있었어?", memorial)
        for keep in ("왔니. 밥은 먹었고?", "나도 그랬단다."):
            self.assertIn(keep, memorial)       # 다른 짝과 순서는 그대로
        self.assertEqual((kept, dropped), ([], []))

    def test_a_similar_but_different_line_is_left_alone(self):
        """한 글자라도 다르면 사용자의 자유 입력일 수 있다. 건드리지 않는다."""
        persona = ("인물 설정\n[대화 예시]\n"
                   "사용자: 뭐 하고 있었어?\n형: 그냥 앉아서 텔레비전 보고 있었어.\n"
                   "사용자: 그때 뭐 하고 있었어?\n형: 그냥 앉아서 텔레비전 보고 있었지.")
        system, _ = build_persona(persona, memorial=True)
        self.assertIn("텔레비전 보고 있었어", system)
        self.assertIn("그때 뭐 하고 있었어?", system)

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
            app = create_app(settings, frontend=Frontend(), llm=LLM(), speech_gate=Gate(),
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
            # 두 경로가 같은 build_persona 를 쓴다. 다른 점은 [재회] 블록 하나뿐이며,
            # 그 블록은 등록 경로에만 붙는다.
            registered, tested = captured[0][0]["content"], captured[1][0]["content"]
            self.assertIn(MEMORIAL_RULES, registered)
            self.assertNotIn(MEMORIAL_HEAD, tested)
            self.assertEqual(registered.replace(f"\n\n{MEMORIAL_HEAD}\n{MEMORIAL_RULES}", ""), tested)
            self.assertEqual(captured[0][1:], captured[1][1:])
            self.assertEqual(len(captured[0]), 2)
            self.assertEqual(personas.calls, 1)
            self.assertEqual(list(Path(root).iterdir()), [])


if __name__ == "__main__":
    unittest.main()
