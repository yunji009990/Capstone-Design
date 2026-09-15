"""핵심 기억의 근거·수명·취소·삭제·긴 대화 및 토큰 예산 회귀 검사."""
import asyncio
import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

import httpx
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_memory import SessionMemory, looks_like_forget
from dialogue_server import Settings, create_app
from realtime_dialogue import Dialogue, ResponseState, build_persona
from realtime_llm import ContextLimitError, LLMClient, ModelEndpoint
from test_dialogue import detector, Frontend, FakeLLM, SPEECH, feed


def fact(key, ids, priority=2, ttl="connection"):
    return {"key": key, "source_ids": ids, "priority": priority, "ttl": ttl}


def update(*facts, summary=()):
    return {"upserts": list(facts), "summary_ids": list(summary)}


async def drain(memory):
    memory.kick(force=True)
    if memory.task:
        await asyncio.wait_for(memory.task, 2)


class MemoryValidationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 9, 13, 10, tzinfo=timezone.utc)
        self.memory = SessionMemory(None, clock=lambda: self.now)

    def test_user_evidence_is_copied_and_correction_replaces_the_same_fact(self):
        old = self.memory.observe("user", "발표는 수요일 오후 3시야.", 1)
        self.memory.apply(update(fact("사용자.발표일정", [old])), self.memory.payload())
        newer = self.memory.observe("user", "정정할게. 발표는 목요일 오후 4시야.", 2)
        self.memory.apply(update(fact("사용자.발표일정", [newer])), self.memory.payload())
        rows = self.memory.fact_rows("발표")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sources"][0]["text"], "정정할게. 발표는 목요일 오후 4시야.")
        self.assertNotIn("수요일", self.memory.prompt("발표"))

    def test_partial_correction_retains_ordered_evidence(self):
        first = self.memory.observe("user", "발표는 수요일 오후 3시야.", 1)
        self.memory.apply(update(fact("사용자.발표일정", [first])), self.memory.payload())
        second = self.memory.observe("user", "시간만 오후 4시로 바꿀게.", 2)
        self.memory.apply(update(fact("사용자.발표일정", [second, first])), self.memory.payload())
        self.assertEqual(self.memory.facts["사용자.발표일정"].source_ids, (first, second))

    def test_invalid_or_assistant_facts_are_rejected_atomically(self):
        user = self.memory.observe("user", "보리차를 좋아해.", 1)
        assistant = self.memory.observe("assistant", "당신은 부산에 살아요.", 1)
        payload = self.memory.payload()
        invalid = [fact("거주지", [assistant]), fact("없는 근거", [999]), fact("불리언", [True]),
                   dict(fact("취향", [user]), value="제공하지 않은 사실")]
        for value in invalid:
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.memory.apply(update(fact("사용자.음료", [user]), value), payload)
            self.assertEqual(self.memory.facts, {})
            self.assertEqual(len(self.memory.pending), 2)

    def test_today_state_expires_and_unrelated_memory_survives(self):
        first = self.memory.observe("user", "오늘은 피곤해.", 1)
        second = self.memory.observe("user", "보리차를 좋아해.", 2)
        self.memory.apply(update(fact("오늘상태", [first], ttl="today"), fact("취향", [second])), self.memory.payload())
        self.now += timedelta(days=1)
        self.memory.prune()
        self.assertNotIn("오늘상태", self.memory.facts)
        self.assertIn("취향", self.memory.facts)

    def test_capacity_is_bounded_and_loss_is_reported(self):
        memory = SessionMemory(None, max_facts=2, max_pending=3)
        first = memory.observe("user", "중요한 약속", 1)
        memory.apply(update(fact("약속", [first], 3)), memory.payload())
        for turn in range(2, 8):
            sid = memory.observe("user", "잠깐의 이야기 " + str(turn), turn)
            memory.apply(update(fact("상태" + str(turn), [sid], 1)), memory.payload())
        self.assertEqual(len(memory.facts), 2)
        self.assertIn("약속", memory.facts)
        for turn in range(10, 30): memory.observe("user", str(turn), turn)
        self.assertLessEqual(len(memory.pending), 3)
        self.assertLessEqual(len(memory.events), 5)
        self.assertGreater(memory.status()["dropped"], 0)

    def test_forget_candidate_does_not_include_do_not_forget(self):
        self.assertTrue(looks_like_forget("발표 일정은 잊어줘."))
        self.assertTrue(looks_like_forget("그 취향은 저장하지 마."))
        self.assertFalse(looks_like_forget("내 이름 잊지 마."))
        self.assertFalse(looks_like_forget("일정을 잊어버리지 마."))


class MemoryLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_input_invalidates_a_result_even_if_backend_ignores_cancel(self):
        entered = asyncio.Event()
        async def slow(payload):
            entered.set()
            try:
                await asyncio.Future()
            except asyncio.CancelledError:
                return update(fact("취향", [payload["events"][0]["id"]]))
        memory = SessionMemory(slow, idle_delay=0)
        memory.observe("user", "커피를 좋아해.", 1)
        memory.kick(force=True)
        await entered.wait()
        await memory.pause()
        self.assertEqual(memory.facts, {})
        self.assertEqual(memory.status()["pending_users"], 1)
        await memory.clear(close=True)
        self.assertEqual(memory.events, {})

    async def test_reset_isolates_connections_and_late_results(self):
        async def extract(payload):
            return update(fact("취향", [payload["events"][0]["id"]]))
        first, second = SessionMemory(extract, idle_delay=0), SessionMemory(extract, idle_delay=0)
        first.observe("user", "보리차를 좋아해.", 1)
        second.observe("user", "오렌지주스를 좋아해.", 1)
        await drain(first)
        await drain(second)
        await first.clear(close=True)
        self.assertEqual(first.events, {})
        self.assertIn("오렌지주스", second.prompt("취향"))
        self.assertNotIn("보리차", second.prompt("취향"))
        await second.clear()

    async def test_clear_while_extractor_is_running_cannot_restore_old_memory(self):
        entered = asyncio.Event()
        async def stale(payload):
            entered.set()
            try: await asyncio.Future()
            except asyncio.CancelledError:
                return update(fact("옛 정보", [payload["events"][0]["id"]]))
        memory = SessionMemory(stale, idle_delay=0)
        memory.observe("user", "이전 연결의 정보", 1)
        memory.kick(force=True)
        await entered.wait()
        await memory.clear(close=True)
        self.assertFalse(memory.events or memory.pending or memory.facts)
        self.assertIsNone(memory.task)
        self.assertIsNone(memory.observe("user", "늦게 도착한 발화", 2))

    async def test_failed_extraction_keeps_pending_source_for_retry(self):
        async def fail(payload): raise ValueError("invalid model result")
        memory = SessionMemory(fail, idle_delay=0)
        source = memory.observe("user", "중요한 약속을 기억해 줘.", 1)
        await drain(memory)
        self.assertIn(source, memory.events)
        self.assertEqual(memory.status()["failures"], 1)
        async def recover(payload): return update(fact("약속", [source]))
        memory.extract = recover
        await drain(memory)
        self.assertIn("약속", memory.facts)
        await memory.clear()

    async def test_targeted_forget_removes_sources_summary_and_pending_but_not_other_facts(self):
        async def resolver(payload):
            return {"intent": "forget", "all": False, "keys": ["발표일정"], "source_ids": []}
        memory = SessionMemory(None, resolver)
        target = memory.observe("user", "발표는 목요일 4시야.", 1)
        answer = memory.observe("assistant", "목요일 4시로 기억할게요.", 1)
        keep = memory.observe("user", "보리차를 좋아해.", 2)
        memory.apply(update(fact("발표일정", [target]), fact("음료취향", [keep]), summary=[answer]), memory.payload())
        outcome, removed = await memory.forget("발표 일정은 잊어줘.")
        self.assertEqual(outcome, "forgotten")
        self.assertEqual(removed, {target, answer})
        self.assertEqual(memory.summary_ids, [])
        self.assertNotIn("목요일", json.dumps(memory.fact_rows(), ensure_ascii=False))
        self.assertIn("보리차", memory.prompt("음료"))
        await memory.clear()

    async def test_unrequested_whole_delete_and_invalid_later_chunk_do_not_partly_delete(self):
        calls = []
        async def resolver(payload):
            calls.append(payload)
            if len(calls) == 2: return {"bad": "schema"}
            return {"intent": "forget", "all": False, "keys": [], "source_ids": [payload["sources"][0]["id"]]}
        memory = SessionMemory(None, resolver)
        for turn in range(5): memory.observe("user", "긴 원문" * 350, turn)
        original = copy.deepcopy(memory.events)
        with self.assertRaises(ValueError): await memory.forget("첫 원문은 잊어줘.")
        self.assertEqual(memory.events, original)
        async def overdelete(payload):
            return {"intent": "forget", "all": True, "keys": [], "source_ids": []}
        memory.forget_resolver = overdelete
        with self.assertRaises(ValueError): await memory.forget("첫 원문은 잊어줘.")
        self.assertEqual(memory.events, original)
        await memory.clear()

    async def test_forget_accepts_visible_fact_ids_across_chunks_but_rejects_invention(self):
        async def resolver(payload):
            return {"intent": "forget", "all": False, "keys": ["발표"], "source_ids": [target]}
        memory = SessionMemory(None, resolver)
        target = memory.observe("user", "발표는 목요일이야.", 1)
        memory.apply(update(fact("발표", [target])), memory.payload())
        for turn in range(2, 7): memory.observe("user", "다른 이야기" * 250, turn)
        outcome, removed = await memory.forget("발표를 잊어줘.")
        self.assertEqual(outcome, "forgotten")
        self.assertEqual(removed, {target})
        self.assertEqual(len(memory.events), 5)
        async def invalid(payload):
            return {"intent": "forget", "all": False, "keys": [], "source_ids": [99999]}
        memory.forget_resolver = invalid
        original = dict(memory.events)
        with self.assertRaises(ValueError): await memory.forget("다른 이야기도 잊어줘.")
        self.assertEqual(memory.events, original)
        await memory.clear()

    async def test_forget_includes_delivery_during_resolution_and_leaves_quotes_alone(self):
        async def resolver(payload):
            if "인용" in payload["request"]:
                return {"intent": "other", "all": False, "keys": [], "source_ids": []}
            return {"intent": "forget", "all": False, "keys": ["발표"], "source_ids": []}
        memory = SessionMemory(None, resolver)
        sid = memory.observe("user", "발표는 목요일이야.", 1)
        memory.apply(update(fact("발표", [sid])), memory.payload())
        delivered = []
        async def finish_held():
            delivered.append(memory.observe("assistant", "목요일 발표군요.", 1))
        outcome, _ = await memory.forget("'잊어줘'라는 말을 인용했어.", before_apply=finish_held)
        self.assertEqual(outcome, "other")
        self.assertEqual(delivered, [])
        outcome, removed = await memory.forget("발표는 잊어줘.", before_apply=finish_held)
        self.assertEqual(outcome, "forgotten")
        self.assertEqual(removed, {sid, delivered[0]})
        self.assertFalse(memory.events)


class DialogueMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_final_user_and_only_delivered_text_are_observed_on_interrupt(self):
        llm = FakeLLM()
        llm.block = True
        events = []
        async def emit(event): events.append(event)
        memory = SessionMemory(None)
        dialogue = Dialogue("인물", [], Frontend(), llm, emit, detector=detector(),
                            partial_seconds=60, memory=memory)
        try:
            await dialogue.text("확정한 사용자 발화")
            await asyncio.wait_for(llm.entered.wait(), 1)
            await feed(dialogue, SPEECH, 10)
            sources = list(memory.events.values())
            self.assertEqual([(e.role, e.text) for e in sources],
                             [("user", "확정한 사용자 발화"), ("assistant", "먼저 보낸 말.")])
            self.assertIsNone(dialogue.active)
            self.assertNotIn("transcript.final", [e["type"] for e in events[4:]])
        finally: await dialogue.close()

    async def test_tts_acknowledged_prefix_and_long_raw_text_keep_deletion_mapping(self):
        async def emit(event): pass
        async def resolver(payload):
            return {"intent": "forget", "all": False, "keys": [],
                    "source_ids": [e["id"] for e in payload["sources"] if e["role"] == "user"]}
        memory = SessionMemory(None, resolver)
        dialogue = Dialogue("인물", [], None, None, emit, detector=detector(), tts=object(), memory=memory)
        long_text = "긴 원문 " * 410
        sid = memory.observe("user", long_text, 1)
        dialogue.append_history({"role": "user", "content": long_text}, sid)
        state = ResponseState("test", 1, user_added=True, text="전달한 문장. 미전달 초안.", spoken_chars=7)
        dialogue.record(state)
        self.assertIn(sid, memory.events)
        self.assertNotIn(sid, memory.pending)
        self.assertEqual([e.text for e in memory.events.values() if e.role == "assistant"], ["전달한 문장."])
        _, removed = await memory.forget("긴 원문은 잊어줘.")
        self.assertEqual(removed, set(dialogue.history_sources.values()))
        await dialogue.close()

    async def test_55_turns_keep_a_fact_after_raw_history_expires_then_forget_and_reset(self):
        async def extract(payload):
            facts = []
            for event in payload["events"]:
                if event["role"] == "user" and "발표는" in event["text"]:
                    facts = [fact("사용자.발표일정", [event["id"]])]
            return update(*facts)
        async def forget(payload):
            return {"intent": "forget", "all": False, "keys": ["사용자.발표일정"],
                    "source_ids": [e["id"] for e in payload["sources"] if "목요일" in e["text"]]}
        class LLM:
            calls = []
            async def route(self, messages): return "normal"
            async def stream(self, messages, route):
                self.calls.append(messages)
                yield "잘 들었어요."
        events = []
        async def emit(event): events.append(event)
        memory = SessionMemory(extract, forget, idle_delay=0)
        llm = LLM()
        system, examples = build_persona("친절한 대화 도우미.")
        dialogue = Dialogue(system, examples, None, llm, emit, detector=detector(), memory=memory)
        try:
            for turn in range(55):
                text = "발표는 수요일 3시야." if turn == 0 else (
                    "발표는 목요일 4시로 바뀌었어." if turn == 4 else "중간 이야기 " + str(turn))
                await dialogue.text(text)
                await dialogue.active.task
                if turn % 3 == 2: await drain(memory)
            await drain(memory)
            self.assertLessEqual(len(dialogue.history), 60)
            self.assertNotIn("목요일", json.dumps(dialogue.history, ensure_ascii=False))
            await dialogue.text("발표 일정 다시 말해줘.")
            await dialogue.active.task
            self.assertIn("목요일", llm.calls[-1][0]["content"])
            self.assertNotIn("수요일", llm.calls[-1][0]["content"])
            await dialogue.text("발표 일정은 잊어줘.")
            await dialogue.active.task
            self.assertIn("지웠어요", events[-1]["text"])
            self.assertNotIn("목요일", json.dumps(dialogue.history, ensure_ascii=False))
            self.assertNotIn("목요일", memory.prompt("발표"))
            await dialogue.reset()
            self.assertEqual(dialogue.history, [])
            self.assertEqual(memory.events, {})
            self.assertEqual(memory.history_ids, set())
        finally:
            await dialogue.close()


class TokenBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_count_trims_complete_old_turns_and_preserves_current_message(self):
        requests = []
        def transport(request):
            body = json.loads(request.content)
            requests.append(body)
            self.assertEqual(request.url.path, "/tokenize")
            return httpx.Response(200, json={"count": sum(len(m["content"]) for m in body["messages"]),
                                            "max_model_len": 160})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            endpoint = ModelEndpoint("http://llm/v1/chat/completions", "model", max_tokens=20)
            llm = LLMClient(endpoint, client=http, context_limit=160)
            messages = [{"role": "system", "content": "persona"},
                        {"role": "user", "content": "a" * 60}, {"role": "assistant", "content": "b" * 20},
                        {"role": "user", "content": "recent"}, {"role": "assistant", "content": "answer"},
                        {"role": "user", "content": "current"}]
            fitted = await llm.fit_messages(messages, endpoint)
            self.assertEqual(fitted, [messages[0]] + messages[3:])
            self.assertEqual(len(messages), 6)
            self.assertTrue(requests[0]["add_generation_prompt"])

    async def test_oversized_persona_is_not_silently_cut(self):
        def transport(request): return httpx.Response(200, json={"count": 200, "max_model_len": 160})
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as http:
            endpoint = ModelEndpoint("http://llm/v1/chat/completions", "model", max_tokens=20)
            llm = LLMClient(endpoint, client=http, context_limit=160)
            with self.assertRaises(ContextLimitError):
                await llm.fit_messages([{"role": "system", "content": "large persona"},
                                        {"role": "user", "content": "question"}], endpoint)


class MemoryProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        class LLM:
            async def available(self, endpoint=None): return True
            async def route(self, messages): return "normal"
            async def stream(self, messages, route): yield "잘 들었어요."
            async def extract_memory(self, payload):
                users = [e for e in payload["events"] if e["role"] == "user"]
                return update(fact("취향", [users[-1]["id"]])) if users else update()
            async def resolve_memory_forget(self, payload):
                return {"intent": "clarify", "all": False, "keys": [], "source_ids": []}
        self.settings = Settings(self.tmp.name, "unused", token="test-token", allow_test_mode=True)
        self.app = create_app(self.settings, frontend=Frontend(), llm=LLM(), detector_factory=detector)
        self.hello = {"type": "start", "protocol": 1, "sample_rate": 16000,
                      "channels": 1, "format": "pcm_s16le", "test_mode": True, "test_persona": "가상 도우미"}

    def connect(self, web):
        return web.websocket_connect("/dialogue", headers={"X-Token": "test-token"})

    def receive(self, ws, kind):
        for _ in range(12):
            event = ws.receive_json()
            if event["type"] == kind: return event
            self.assertNotEqual(event["type"], "error", event)
        self.fail("Expected event: " + kind)

    def snapshot(self, ws):
        ws.send_json({"type": "memory.inspect"})
        return self.receive(ws, "memory.snapshot")

    def test_authenticated_inspect_flush_reset_and_reconnect_are_connection_local(self):
        with TestClient(self.app) as web:
            self.assertTrue(web.get("/health").json()["memory"]["enabled"])
            with self.assertRaises(WebSocketDisconnect):
                with web.websocket_connect("/dialogue"): pass
            with self.connect(web) as ws:
                ws.send_json(self.hello)
                self.assertEqual(self.receive(ws, "ready")["memory"]["scope"], "connection")
                ws.send_json({"type": "text", "text": "나는 보리차를 좋아해."})
                self.receive(ws, "response.done")
                ws.send_json({"type": "memory.flush"})
                self.assertTrue(self.receive(ws, "memory.scheduled")["idle"])
                for _ in range(40):
                    snapshot = self.snapshot(ws)
                    if snapshot["status"]["updates"]: break
                    time.sleep(.025)
                self.assertEqual(snapshot["status"]["facts"], 1)
                self.assertIn("보리차", json.dumps(snapshot, ensure_ascii=False))
                ws.send_json({"type": "reset"})
                self.receive(ws, "reset.done")
                self.assertEqual(self.snapshot(ws)["facts"], [])
                ws.send_json({"type": "stop"})
                self.receive(ws, "stopped")
            with self.connect(web) as ws:
                ws.send_json(self.hello)
                self.receive(ws, "ready")
                self.assertEqual(self.snapshot(ws)["status"]["updates"], 0)
                self.assertEqual(self.snapshot(ws)["facts"], [])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_registered_connection_cannot_dump_memory(self):
        folder = Path(self.tmp.name) / "synthetic"
        folder.mkdir()
        (folder / "persona.md").write_text("가상 도우미", encoding="utf-8")
        with TestClient(self.app) as web, self.connect(web) as ws:
            ws.send_json(dict(self.hello, test_mode=False, session="synthetic"))
            self.receive(ws, "ready")
            ws.send_json({"type": "memory.inspect"})
            self.assertEqual(ws.receive_json()["code"], "protocol_error")

    def test_disabled_memory_keeps_normal_answering(self):
        self.settings.memory_enabled = False
        with TestClient(self.app) as web, self.connect(web) as ws:
            ws.send_json(self.hello)
            self.assertFalse(self.receive(ws, "ready")["memory"]["enabled"])
            ws.send_json({"type": "text", "text": "안녕."})
            self.assertEqual(self.receive(ws, "response.done")["text"], "잘 들었어요.")
            ws.send_json({"type": "memory.inspect"})
            self.assertEqual(ws.receive_json()["code"], "protocol_error")


if __name__ == "__main__":
    unittest.main()
