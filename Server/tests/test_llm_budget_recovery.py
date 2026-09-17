"""추론이 본답변 없이 생성 한도를 소진했을 때의 복구를 검사한다.

실제 서버 9턴 검사에서 확인된 실패다. 두 번째 발화가 Whisper·추론 시작까지 갔지만
숨은 추론만으로 2048 토큰을 다 써 finish_reason=length 로 끝났고, 리액션 음성만
나간 채 본답변 델타·음성이 0개였다(turn_failed).

여기서 확인하는 것은 **오류 시 대화 회복 정책**이다. 추론이 오래 걸리는 문제나
한도 자체를 해결하지 않는다. 모델은 호출하지 않고 SSE 응답만 모의한다.
"""
import asyncio
import json
import logging
import sys
import unittest
from pathlib import Path

# 배포 위치(Server/tests)에서는 이 한 줄이면 된다. 아직 작업 폴더의 수정안 트리에
# 있는 동안에는 고친 파일만 있으므로, 나머지 모듈을 가진 Server 를 뒤에 덧붙인다.
SERVER = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER))
if not (SERVER / "dialogue_server.py").is_file():
    full = next((parent / "Server" for parent in SERVER.parents
                 if (parent / "Server" / "dialogue_server.py").is_file()), None)
    if full is None:
        raise RuntimeError("고치지 않은 모듈을 가진 Server 폴더를 찾지 못했습니다")
    sys.path.append(str(full))

import httpx                                                     # noqa: E402

from realtime_audio import Observation                           # noqa: E402
from realtime_dialogue import Dialogue                           # noqa: E402
from realtime_llm import (AnswerBudgetError, ContextLimitError,  # noqa: E402
                          LLMClient, ModelEndpoint)

NORMAL = "http://normal/chat/completions"
REASON = "http://reason/chat/completions"
MESSAGES = [{"role": "system", "content": "인물 설정"},
            {"role": "user", "content": "조건을 비교해서 정해 줘"}]


def sse(*packets, done=True):
    body = "".join("data: " + json.dumps(packet) + "\n\n" for packet in packets)
    if done:
        body += "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def delta(text, reason=None):
    choice = {"delta": {"content": text}}
    if reason:
        choice["finish_reason"] = reason
    return {"choices": [choice]}


def hidden_thought(text="숨은 추론"):
    """분리 채널 추론. 사용자에게는 절대 나가지 않는다."""
    return {"choices": [{"delta": {"reasoning_content": text}}]}


def budget_exhausted():
    return {"choices": [{"delta": {}, "finish_reason": "length"}]}


class Recorder:
    """어느 엔드포인트에 어떤 메시지로 갔는지만 센다. 원문은 검사 안에서만 쓴다."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request):
        body = json.loads(request.content)
        url = str(request.url)
        self.calls.append((url, body))
        response = self.routes[url]
        return response(url, body) if callable(response) else response


class BudgetRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def client(self, routes, *, reasoning=True, context_limit=0):
        self.recorder = Recorder(routes)
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.recorder))
        self.addAsyncCleanup(self.http.aclose)
        return LLMClient(ModelEndpoint(NORMAL, "normal", max_tokens=256),
                         ModelEndpoint(REASON, "reason", max_tokens=2048) if reasoning else None,
                         client=self.http, context_limit=context_limit)

    @property
    def urls(self):
        return [url for url, _ in self.recorder.calls]

    async def collect(self, llm, route="reasoning"):
        return "".join([text async for text in llm.stream(MESSAGES, route)])

    async def test_hidden_reasoning_that_exhausts_the_budget_falls_back_once(self):
        llm = self.client({REASON: sse(hidden_thought(), budget_exhausted()),
                           NORMAL: sse(delta("바로 답변드릴게요."), delta("", "stop"))})
        with self.assertLogs("realtime_llm", level=logging.INFO) as logged:
            answer = await self.collect(llm)
        self.assertEqual(answer, "바로 답변드릴게요.")
        self.assertEqual(self.urls, [REASON, NORMAL])
        # 재요청 메시지는 처음과 같다. 중간 추론·초안이 섞이지 않는다.
        self.assertEqual(self.recorder.calls[1][1]["messages"], MESSAGES)
        self.assertEqual(self.recorder.calls[1][1]["model"], "normal")
        line = "".join(logged.output)
        self.assertIn("answer.budget_fallback", line)
        self.assertIn("route=reasoning->normal", line)
        for secret in ("인물 설정", "조건을 비교", "숨은 추론", "바로 답변"):
            self.assertNotIn(secret, line)

    async def test_text_already_sent_keeps_the_original_error(self):
        llm = self.client({REASON: sse(delta("생각해 보면 "), budget_exhausted()),
                           NORMAL: sse(delta("두 번째 시도"), delta("", "stop"))})
        parts = []
        with self.assertRaises(AnswerBudgetError):
            async for text in llm.stream(MESSAGES, "reasoning"):
                parts.append(text)
        # 부분 답변을 완료로 위장하지 않고, 이어쓰기도 시키지 않는다.
        self.assertEqual(parts, ["생각해 보면 "])
        self.assertEqual(self.urls, [REASON])

    async def test_the_normal_route_is_never_retried(self):
        llm = self.client({NORMAL: sse(hidden_thought(), budget_exhausted())})
        with self.assertRaises(AnswerBudgetError):
            await self.collect(llm, route="normal")
        self.assertEqual(self.urls, [NORMAL])

    async def test_the_fallback_itself_is_not_retried(self):
        llm = self.client({REASON: sse(hidden_thought(), budget_exhausted()),
                           NORMAL: sse(hidden_thought(), budget_exhausted())})
        with self.assertRaises(AnswerBudgetError):
            await self.collect(llm)
        self.assertEqual(self.urls, [REASON, NORMAL])      # 루프가 없다

    async def test_a_truncated_fallback_is_not_reported_as_success(self):
        llm = self.client({REASON: sse(hidden_thought(), budget_exhausted()),
                           NORMAL: sse(delta("반쪽 답변"), done=False)})
        with self.assertRaises(RuntimeError) as caught:
            await self.collect(llm)
        self.assertNotIsInstance(caught.exception, AnswerBudgetError)
        self.assertEqual(self.urls, [REASON, NORMAL])

    async def test_http_errors_are_not_recovered(self):
        llm = self.client({REASON: httpx.Response(503, text="unavailable"),
                           NORMAL: sse(delta("대신 답변"), delta("", "stop"))})
        with self.assertRaises(httpx.HTTPStatusError):
            await self.collect(llm)
        self.assertEqual(self.urls, [REASON])

    async def test_a_stream_error_packet_is_not_recovered(self):
        llm = self.client({REASON: sse({"error": {"message": "boom"}}),
                           NORMAL: sse(delta("대신 답변"), delta("", "stop"))})
        with self.assertRaises(RuntimeError) as caught:
            await self.collect(llm)
        self.assertNotIsInstance(caught.exception, AnswerBudgetError)
        self.assertEqual(self.urls, [REASON])

    async def test_cancellation_never_starts_a_retry(self):
        entered = asyncio.Event()

        async def consume(llm):
            async for _ in llm.stream(MESSAGES, "reasoning"):
                entered.set()
                await asyncio.sleep(10)

        llm = self.client({REASON: sse(delta("천천히 "), budget_exhausted()),
                           NORMAL: sse(delta("대신 답변"), delta("", "stop"))})
        task = asyncio.create_task(consume(llm))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.urls, [REASON])

    async def test_both_attempts_close_their_http_streams(self):
        opened, closed = [], []

        class Watching(httpx.MockTransport):
            async def handle_async_request(self, request):
                response = await super().handle_async_request(request)
                url = str(request.url)
                opened.append(url)
                original = response.aclose

                async def aclose():
                    closed.append(url)
                    await original()

                response.aclose = aclose
                return response

        self.recorder = Recorder({REASON: sse(hidden_thought(), budget_exhausted()),
                                  NORMAL: sse(delta("답변"), delta("", "stop"))})
        self.http = httpx.AsyncClient(transport=Watching(self.recorder))
        self.addAsyncCleanup(self.http.aclose)
        llm = LLMClient(ModelEndpoint(NORMAL, "normal"), ModelEndpoint(REASON, "reason"),
                        client=self.http)
        self.assertEqual(await self.collect(llm), "답변")
        self.assertEqual(opened, [REASON, NORMAL])
        self.assertEqual(closed, [REASON, NORMAL])

    async def test_each_attempt_fits_the_context_for_its_own_endpoint(self):
        counted = []

        def tokenize(url, body):
            counted.append((url, body["model"]))
            return httpx.Response(200, json={"count": 10, "max_model_len": 4096})

        llm = self.client({"http://reason/tokenize": tokenize,
                           "http://normal/tokenize": tokenize,
                           REASON: sse(hidden_thought(), budget_exhausted()),
                           NORMAL: sse(delta("답변"), delta("", "stop"))},
                          context_limit=4096)
        self.assertEqual(await self.collect(llm), "답변")
        # 두 경로가 각자의 엔드포인트·모델로 따로 맞춘다.
        self.assertEqual(counted, [("http://reason/tokenize", "reason"),
                                   ("http://normal/tokenize", "normal")])

    async def test_a_context_limit_is_not_turned_into_a_fallback(self):
        def tokenize(url, body):
            return httpx.Response(200, json={"count": 9000, "max_model_len": 4096})

        llm = self.client({"http://reason/tokenize": tokenize,
                           REASON: sse(delta("답변"), delta("", "stop")),
                           NORMAL: sse(delta("대신 답변"), delta("", "stop"))},
                          context_limit=4096)
        with self.assertRaises(ContextLimitError):
            await self.collect(llm)
        self.assertNotIn(NORMAL, self.urls)

    async def test_thought_and_channel_tokens_stay_hidden_on_the_fallback(self):
        llm = self.client({REASON: sse(hidden_thought(), budget_exhausted()),
                           NORMAL: sse(delta("<think>안 보여야 한다</think>"),
                                       delta("<|channel>숨김<channel|>보이는 답변"),
                                       delta("", "stop"))})
        self.assertEqual(await self.collect(llm), "보이는 답변")


class DialogueIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """리액션이 이미 나간 뒤에도 본답변만 이어 나가고 다음 턴이 가능한지 본다."""

    async def asyncSetUp(self):
        self.events = []

        # 실제 순서를 그대로 만든다. 추론이 끝나기 전에 대기 리액션이 먼저 나간다.
        # 시간에 기대지 않도록, 리액션이 나간 뒤에야 추론 응답이 돌아오게 묶는다.
        self.reacted = asyncio.Event()
        self.spoken = 0

        async def emit(event):
            self.events.append(event)
            if event["type"] == "response.delta" and event["kind"] == "reaction":
                owner.reacted.set()
            # Unity 가 보내는 재생 확인을 대신한다. 프로토콜은 그대로다.
            if event["type"] == "audio.boundary":
                owner.spoken = event["text_chars"]
                owner.dialogue.playback({"type": "playback.progress", "text_chars": owner.spoken,
                                         "response_id": event["response_id"]})
            if event["type"] == "response.done":
                owner.dialogue.playback({"type": "playback.done", "text_chars": owner.spoken,
                                         "response_id": event["response_id"]})

        owner = self

        class Frontend:
            async def transcribe(self, pcm):
                return Observation("조건을 비교해서 정해 줘")

        class TTS:
            streaming_mode = "test"

            async def stream(self, phrase):
                yield "", 240      # 한 조각만 보낸다. 실제 합성은 하지 않는다.

        class Clip:
            text = "잠시만요"

            def packets(self):
                return [("", 240)]

        class Reactions:
            delay = 0

            def take(self):
                return Clip()

        async def reasoning_after_the_reaction(url, body):
            await owner.reacted.wait()
            owner.reacted.clear()
            return sse(hidden_thought(), budget_exhausted())

        self.recorder = Recorder({REASON: reasoning_after_the_reaction,
                                  NORMAL: lambda url, body: sse(delta(owner.answer), delta("", "stop"))})
        self.http = httpx.AsyncClient(transport=httpx.MockTransport(self.recorder))
        self.addAsyncCleanup(self.http.aclose)
        self.answer = "첫 번째 답변입니다."
        llm = LLMClient(ModelEndpoint(NORMAL, "normal"), ModelEndpoint(REASON, "reason"),
                        client=self.http)
        llm.route = self.route
        self.dialogue = Dialogue("인물", [], Frontend(), llm, emit,
                                 tts=TTS(), reactions=Reactions(),
                                 semantic_interruptions=True)
        self.addAsyncCleanup(self.dialogue.close)

    async def route(self, messages):
        return "reasoning"

    def kinds(self):
        return [event["type"] for event in self.events]

    def answers(self):
        return "".join(event["text"] for event in self.events
                       if event["type"] == "response.delta" and event["kind"] == "answer")

    async def turn(self, text):
        await self.dialogue.text(text)
        await asyncio.wait_for(self.dialogue.active.task, 3)

    async def test_the_answer_completes_after_a_reaction_and_the_next_turn_works(self):
        await self.turn("조건을 비교해서 정해 줘")
        self.assertIn("response.done", self.kinds())
        self.assertNotIn("error", self.kinds())
        # 리액션은 이미 나갔고, 본답변은 그 뒤에 한 번만 이어진다.
        reactions = [e for e in self.events
                     if e["type"] == "response.delta" and e["kind"] == "reaction"]
        self.assertEqual(len(reactions), 1)
        self.assertEqual(self.answers(), "첫 번째 답변입니다.")
        done = next(e for e in self.events if e["type"] == "response.done")
        self.assertEqual(done["text"], "잠시만요 첫 번째 답변입니다.")
        # 대화 기록에는 본답변만 남는다. 리액션도 숨은 추론도 들어가지 않는다.
        self.assertEqual(self.dialogue.history[-1],
                         {"role": "assistant", "content": "첫 번째 답변입니다."})

        self.answer = "두 번째 답변입니다."
        self.events.clear()
        await self.turn("그럼 다음은?")
        self.assertIn("response.done", self.kinds())
        self.assertEqual(self.answers(), "두 번째 답변입니다.")
        self.assertEqual(self.urls_for(REASON), 2)

    def urls_for(self, url):
        return sum(call[0] == url for call in self.recorder.calls)


if __name__ == "__main__":
    unittest.main()
