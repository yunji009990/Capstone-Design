"""캐릭터 말투로 준비한 기억 삭제 안내의 검증·캐시·실제 발화 계약을 검사한다."""
import asyncio
from pathlib import Path
import sys
import tempfile
import unittest

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_diagnostics import EVENTS, REASONS, ConnectionDiagnostics, known
from dialogue_memory import SessionMemory
from dialogue_server import Settings, create_app
from dialogue_system_lines import (CASUAL, INSTRUCTION, KINDS, POLITE, SYSTEM_LINE_VERSION,
                                   SystemLineCache, SystemLines, fallback_lines,
                                   speech_style, validate_lines)
from persona_context import BASE_RULES
from realtime_dialogue import Dialogue, build_persona
from test_dialogue import Frontend, Gate, detector, eventually

# 생성기에 넘어간 자료를 낱말 검색으로 확인하기 위한 합성 표지. 공통 규칙에는 존댓말과
# 반말이 모두 적혀 있어 그 낱말로는 프로필을 구별할 수 없다.
CASUAL_PERSONA = "[성격] 무뚝뚝합니다. 표식하나\n[말투]\n- 반말로 말한다."
POLITE_PERSONA = "[성격] 다정합니다. 표식둘\n[말투]\n- 존댓말로 말합니다."
CASUAL_PROFILE = "무뚝뚝한 인물 자료 표식하나"
POLITE_PROFILE = "다정한 인물 자료 표식둘"
POLITE_LINES = {"memory_forgotten": "말씀하신 기억은 이번 대화에서 지웠어요.",
                "memory_clarify": "아직 못 지웠어요. 어떤 걸 잊으면 될지 알려 주세요."}
CASUAL_LINES = {"memory_forgotten": "말한 기억은 이번 대화에서 지웠어.",
                "memory_clarify": "아직 못 지웠어. 어떤 걸 잊으면 될지 말해 줘."}


class Diagnostics(ConnectionDiagnostics):
    """실제 화이트리스트를 지나간 코드만 기록한다. other 로 접히면 검사에서 드러난다."""

    def __init__(self):
        super().__init__()
        self.lines = []

    def event(self, name, reason="", **numbers):
        self.lines.append((known(name, EVENTS), known(reason, REASONS) if reason else ""))
        super().event(name, reason, **numbers)


class SpeechStyleTests(unittest.TestCase):
    def test_style_comes_from_the_declared_section_only(self):
        casual = (CASUAL_PERSONA + "\n[실제로 하시던 말]\n존댓말로 인사할 때도 있었습니다.\n"
                  "[대화 예시]\n사용자: 안녕\n엄마: 안녕하세요.")
        polite = POLITE_PERSONA + "\n[실제로 하시던 말]\n가끔 반말도 섞었다."
        self.assertEqual(speech_style(casual), "casual")
        self.assertEqual(speech_style(polite), "polite")
        self.assertEqual(fallback_lines(casual).lines, dict(CASUAL))
        self.assertEqual(fallback_lines(polite).lines, dict(POLITE))

    def test_common_rules_and_unclear_declarations_keep_the_polite_default(self):
        # 공통 규칙에는 존댓말·반말이 함께 적혀 있다. [말투] 선언이 아니므로 기본값이다.
        self.assertEqual(speech_style(BASE_RULES), "polite")
        self.assertEqual(speech_style(build_persona("")[0]), "polite")
        self.assertEqual(speech_style("[말투]\n- 편하게 말합니다."), "polite")
        self.assertEqual(speech_style("[말투]\n- 존댓말과 반말을 섞습니다."), "polite")
        self.assertEqual(speech_style(""), "polite")
        self.assertEqual(fallback_lines().lines, dict(POLITE))
        self.assertEqual(fallback_lines().source, "fallback")


class ValidationTests(unittest.TestCase):
    def test_fallback_sets_pass_the_same_checks(self):
        for lines in (POLITE, CASUAL, POLITE_LINES, CASUAL_LINES):
            self.assertEqual(validate_lines(dict(lines)), dict(lines))

    def test_clarify_is_no_longer_a_prepared_line(self):
        # 되묻기는 인물의 보통 답변 생성으로 옮겼다. 준비·검증 대상이 아니다.
        self.assertEqual(KINDS, ("memory_forgotten", "memory_clarify"))
        self.assertNotIn("clarify\"", INSTRUCTION.replace("memory_clarify\"", ""))
        self.assertNotIn("계속", INSTRUCTION)
        for lines in (POLITE, CASUAL):
            self.assertNotIn("clarify", set(lines) - {"memory_clarify"})
        with self.assertRaises(ValueError):
            validate_lines(dict(POLITE_LINES, clarify="계속할까요, 아니면 바꿔 말할까요?"))

    def test_broken_format_or_lost_meaning_is_rejected(self):
        for value in (
                {"memory_forgotten": POLITE["memory_forgotten"]},            # 키 부족
                dict(POLITE, memory_forgotten="네."),                        # 길이 미달
                dict(POLITE, memory_forgotten="이번 대화 기억에서 지웠어요." * 6),  # 길이 초과
                dict(POLITE, memory_clarify='{"memory_clarify": "아직 못 지웠어요?"}'),  # JSON·태그
                dict(POLITE, memory_forgotten="시스템이 이번 대화 기억을 지웠어요."),  # 내부 용어
                dict(POLITE, memory_forgotten="요청한 기억은 이제 지웠어요."),  # 지운 범위가 없다
                dict(POLITE, memory_clarify="어떤 내용을 잊을까요?"),        # 미수행을 밝히지 않는다
                dict(POLITE, memory_clarify=POLITE["memory_forgotten"])):    # 같은 문장
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_lines(value)

    def test_negated_success_and_claimed_deletion_are_rejected(self):
        for value in (
                dict(POLITE, memory_forgotten="그 얘기는 이번 대화 기억에서 안 지웠어요."),
                dict(POLITE, memory_forgotten="이번 대화 기억에서 지웠다고 말할 수 없어요."),
                dict(POLITE, memory_forgotten="이번 대화 기억은 아직 못 지웠어요."),
                dict(POLITE, memory_clarify="그 얘기는 지웠어요. 어떤 걸 더 잊을까요?"),
                dict(POLITE, memory_clarify="아직 못 지웠어요. 나머지는 지웠어요. 어떤 걸 더 잊을까요?"),
                dict(POLITE, memory_clarify="그 얘기는 지웠어요. 아직 어떤 걸 더 잊을지 못 정했어요.")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_lines(value)


class CacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.cache = SystemLineCache()
        self.calls = []
        self.block = None

    async def generate(self, profile):
        self.calls.append(profile)
        if self.block is not None:
            await self.block.wait()
        return dict(CASUAL_LINES if "표식하나" in profile else POLITE_LINES)

    async def prepare(self, profile, identity="gemma-test"):
        return await self.cache.prepare(profile, self.generate, generator_identity=identity)

    async def test_each_profile_keeps_its_own_lines_and_a_warm_cache_adds_no_call(self):
        polite, first = await self.prepare(POLITE_PROFILE)
        casual, _ = await self.prepare(CASUAL_PROFILE)
        self.assertEqual(first["version"], SYSTEM_LINE_VERSION)
        self.assertTrue(first["ready"])
        self.assertFalse(first["cache_hit"])
        self.assertEqual(polite.lines, POLITE_LINES)
        self.assertEqual(casual.lines, CASUAL_LINES)
        self.assertEqual(self.calls, [POLITE_PROFILE, CASUAL_PROFILE])
        again, warm = await self.prepare(POLITE_PROFILE)
        self.assertTrue(warm["cache_hit"])
        self.assertEqual(again.lines, POLITE_LINES)
        self.assertEqual(len(self.calls), 2)
        # 같은 인물이라도 생성 모델 설정이 바뀌면 다시 만든다.
        await self.prepare(POLITE_PROFILE, identity="gemma-other")
        self.assertEqual(len(self.calls), 3)

    async def test_concurrent_preparation_calls_the_model_once(self):
        self.block = asyncio.Event()
        tasks = [asyncio.create_task(self.prepare(POLITE_PROFILE)) for _ in range(3)]
        await eventually(lambda: self.calls)
        self.block.set()
        results = await asyncio.gather(*tasks)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual([lines.lines for lines, _ in results], [POLITE_LINES] * 3)

    async def test_cancelled_preparation_stores_nothing_and_frees_the_lock(self):
        self.block = asyncio.Event()
        task = asyncio.create_task(self.prepare(POLITE_PROFILE))
        await eventually(lambda: self.calls)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.cache.lines)
        self.block = None
        lines, info = await asyncio.wait_for(self.prepare(POLITE_PROFILE), 1)
        self.assertFalse(info["cache_hit"])
        self.assertEqual(lines.lines, POLITE_LINES)
        self.assertEqual(len(self.calls), 2)

    async def test_failure_is_not_cached_and_capacity_is_bounded(self):
        async def broken(profile):
            return {"memory_forgotten": "네."}
        with self.assertRaises(ValueError):
            await self.cache.prepare(POLITE_PROFILE, broken, generator_identity="gemma-test")
        self.assertFalse(self.cache.lines)
        self.cache.capacity = 2
        for index in range(3):
            await self.prepare("차분한 인물 " + str(index))
        self.assertEqual(len(self.cache.lines), 2)


class MemoryNoticeTests(unittest.IsolatedAsyncioTestCase):
    """TTS 없는 텍스트 대화에서도 같은 문장을 쓰고 삭제 의미를 지키는지 본다."""

    def build(self, intent):
        events = []

        async def extract(payload):
            return {"upserts": [], "summary_ids": []}

        async def resolve(payload):
            if intent != "forget":
                return {"intent": intent, "all": False, "keys": [], "source_ids": []}
            return {"intent": "forget", "all": False, "keys": [],
                    "source_ids": [e["id"] for e in payload["sources"] if "목요일" in e["text"]]}

        class LLM:
            async def route(self, messages):
                return "normal"

            async def stream(self, messages, route):
                yield "잘 들었어요."

        async def emit(event):
            events.append(event)

        memory = SessionMemory(extract, resolve, batch_users=100, idle_delay=0)
        diagnostics = Diagnostics()
        dialogue = Dialogue("반말로 말한다", [], None, LLM(), emit, detector=detector(),
                            memory=memory, diagnostics=diagnostics,
                            system_lines=SystemLines(CASUAL_LINES, "prepared"))
        return dialogue, events, diagnostics

    async def test_deletion_notice_matches_the_outcome_and_repeats_no_content(self):
        for intent, kind in (("forget", "memory_forgotten"), ("clarify", "memory_clarify")):
            with self.subTest(intent=intent):
                dialogue, events, diagnostics = self.build(intent)
                try:
                    await dialogue.text("발표는 목요일 4시야.")
                    await dialogue.active.task
                    notice_start = len(diagnostics.lines)
                    await dialogue.text("발표 일정은 잊어줘.")
                    await dialogue.active.task
                    spoken = [e for e in events if e["type"] == "response.done"][-1]["text"]
                    self.assertEqual(spoken, CASUAL_LINES[kind])
                    # 삭제 대상 원문과 요청 원문을 대사로 다시 읽지 않는다.
                    self.assertNotIn("목요일", spoken)
                    self.assertNotIn("잊어줘", spoken)
                    remaining = [e.text for e in dialogue.memory.events.values()]
                    if intent == "forget":
                        self.assertFalse([t for t in remaining if "목요일" in t])
                    else:
                        self.assertTrue([t for t in remaining if "목요일" in t])
                    self.assertIn(("answer.fixed", kind), diagnostics.lines)
                    # 앞선 일반 답변의 생성 로그와 구별해, 삭제 안내 턴만 검사한다.
                    self.assertNotIn("llm.first_text", [n for n, _ in diagnostics.lines[notice_start:]])
                finally:
                    await dialogue.close()


class ServerPreparationTests(unittest.TestCase):
    def app_for(self, generate, root):
        class LLM:
            async def available(self, endpoint=None):
                return True

            async def route(self, messages):
                return "normal"

            async def generate_system_lines(self, profile):
                return await generate(profile)

        settings = Settings(root, "unused", allow_test_mode=True)
        return create_app(settings, frontend=Frontend(), llm=LLM(), speech_gate=Gate())

    def ready(self, web, persona=None, session=None):
        """한 연결을 열고 ready 를 받은 뒤 정상 종료한다.

        max_connections 가 1이므로 다음 연결을 열기 전에 반드시 닫는다.
        """
        with web.websocket_connect("/dialogue") as ws:
            hello = {"type": "start", "protocol": 1, "sample_rate": 16000,
                     "channels": 1, "format": "pcm_s16le"}
            if session is None:
                hello.update(test_mode=True, test_persona=persona)
            else:
                hello["session"] = session
            ws.send_json(hello)
            event = ws.receive_json()
            while event["type"] == "voice.preparing":
                event = ws.receive_json()
            self.assertEqual(event["type"], "ready", event)
            ws.send_json({"type": "stop"})
            self.assertEqual(ws.receive_json()["type"], "stopped")
            return event

    def test_text_only_connection_prepares_once_and_reuses_the_cache(self):
        profiles = []

        async def generate(profile):
            profiles.append(profile)
            return dict(CASUAL_LINES if "표식하나" in profile else POLITE_LINES)

        with tempfile.TemporaryDirectory() as root:
            with TestClient(self.app_for(generate, root)) as web:
                self.assertEqual(web.get("/health").json()["system_lines"],
                                 {"enabled": True, "version": SYSTEM_LINE_VERSION})
                first = self.ready(web, CASUAL_PERSONA)
                self.assertTrue(first["system_lines"]["ready"])
                self.assertEqual(first["system_lines"]["source"], "prepared")
                self.assertFalse(first["system_lines"]["cache_hit"])
                self.assertFalse(first["tts"])
                second = self.ready(web, CASUAL_PERSONA)
                self.assertTrue(second["system_lines"]["cache_hit"])
                other = self.ready(web, POLITE_PERSONA)
                self.assertFalse(other["system_lines"]["cache_hit"])
        self.assertEqual(len(profiles), 2)
        # 인물별 자료가 서로 섞이지 않는다. 공통 규칙 낱말이 아니라 합성 표지로 본다.
        self.assertIn("표식하나", profiles[0])
        self.assertNotIn("표식둘", profiles[0])
        self.assertIn("표식둘", profiles[1])
        self.assertNotIn("표식하나", profiles[1])

    def test_registered_knowledge_and_extra_rules_stay_out_of_preparation(self):
        profiles = []

        async def generate(profile):
            profiles.append(profile)
            return dict(POLITE_LINES)

        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "person"
            folder.mkdir()
            (folder / "persona.md").write_text(POLITE_PERSONA, encoding="utf-8")
            (folder / "knowledge.md").write_text("- 새 소식 표식지식.", encoding="utf-8")
            (folder / "rules.md").write_text("- 먼저 꺼내지 않을 주제 표식규칙.", encoding="utf-8")
            with TestClient(self.app_for(generate, root)) as web:
                ready = self.ready(web, session="person")
                self.assertTrue(ready["system_lines"]["ready"])
        self.assertEqual(len(profiles), 1)
        self.assertIn("표식둘", profiles[0])
        self.assertNotIn("표식지식", profiles[0])
        self.assertNotIn("표식규칙", profiles[0])

    def test_preparation_failure_still_starts_the_experience(self):
        async def generate(profile):
            raise RuntimeError("model unavailable")

        with tempfile.TemporaryDirectory() as root:
            with TestClient(self.app_for(generate, root)) as web:
                with self.assertLogs("dialogue_server", level="WARNING"):
                    ready = self.ready(web, CASUAL_PERSONA)
        self.assertFalse(ready["system_lines"]["ready"])
        self.assertEqual(ready["system_lines"]["source"], "fallback")
        self.assertEqual(ready["system_lines"]["reason"], "preparation_failed")


if __name__ == "__main__":
    unittest.main()
