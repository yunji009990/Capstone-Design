"""Cancellable HTTP streaming, reasoning routing and spoken-text filtering."""
from __future__ import annotations

import json
from dataclasses import dataclass, field

import httpx

from interruption_policy import INSTRUCTION, TurnDecision


@dataclass
class ModelEndpoint:
    url: str
    model: str
    extra: dict = field(default_factory=dict)
    key: str = ""
    max_tokens: int = 256


class SpokenTextFilter:
    """Remove split thinking blocks and model control tokens before any output.

    Supports separated reasoning_content (ignored by the SSE reader), Qwen
    <think> blocks and Gemma thought channels. Buffer incomplete tags so they
    cannot escape one network chunk at a time.
    """

    def __init__(self):
        self.pending = ""
        self.hidden = False

    def feed(self, text, final=False):
        self.pending += text
        out = []
        while self.pending:
            start = self.pending.find("<")
            if start < 0:
                if not self.hidden:
                    out.append(self.pending)
                self.pending = ""
                break
            if start:
                if not self.hidden:
                    out.append(self.pending[:start])
                self.pending = self.pending[start:]
            prefixes = ("<think>", "</think>", "<|", "<channel|>", "<turn|>")
            if not any(self.pending.startswith(p) or p.startswith(self.pending) for p in prefixes):
                if not self.hidden:
                    out.append("<")
                self.pending = self.pending[1:]
                continue
            end = self.pending.find(">")
            if end < 0:
                if final:
                    self.pending = ""  # truncated control tokens are never speech
                elif len(self.pending) > 256:
                    raise RuntimeError("invalid model control token")
                break
            tag = self.pending[:end + 1]
            self.pending = self.pending[end + 1:]
            if tag in ("<think>", "<|channel>"):
                self.hidden = True
            elif tag in ("</think>", "<channel|>"):
                self.hidden = False
            elif tag.startswith("<|") or tag.endswith("|>"):
                pass
            elif not self.hidden:
                out.append(tag)
        return "".join(out)


class LLMClient:
    def __init__(self, normal, reasoning=None, *, client=None):
        self.normal = normal
        self.reasoning = reasoning
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(45.0, connect=5.0), trust_env=False)

    @staticmethod
    def headers(endpoint):
        return {"Authorization": f"Bearer {endpoint.key}"} if endpoint.key else {}

    @staticmethod
    def body(endpoint, messages, **overrides):
        # Protocol fields must not be silently overridden by model-specific extras.
        return {**endpoint.extra, "model": endpoint.model, "messages": messages,
                "max_completion_tokens": endpoint.max_tokens, **overrides}

    async def available(self, endpoint=None):
        endpoint = endpoint or self.normal
        url = endpoint.url.rsplit("/chat/completions", 1)[0] + "/models"
        try:
            response = await self.client.get(url, headers=self.headers(endpoint), timeout=3)
            response.raise_for_status()
            return any(m.get("id") == endpoint.model for m in response.json().get("data", []))
        except (httpx.HTTPError, ValueError, TypeError):
            return False

    async def route(self, messages):
        if self.reasoning is None:
            return "normal"
        instruction = (
            "대화의 다음 답변에 필요한 계산량만 분류합니다. 답변 자체는 하지 않습니다. "
            "인사, 일상 대화, 감정에 반응하기, 주어진 인물 정보나 기억을 찾기는 normal입니다. "
            "여러 조건을 비교해 결정하기, 계산, 논리 문제, 여러 단계의 계획은 reasoning입니다. "
            "정보가 없다는 이유만으로 reasoning을 고르지 않습니다. "
            '출력은 {"route":"normal"} 또는 {"route":"reasoning"} JSON 하나입니다.')
        response = await self.client.post(
            self.normal.url, headers=self.headers(self.normal),
            json=self.body(self.normal,
                           [{"role": "system", "content": instruction}]
                           + [m for m in messages if m["role"] != "system"][-7:],
                           stream=False, max_completion_tokens=64, temperature=0,
                           response_format={"type": "json_object"}), timeout=8)
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"].get("content") or ""
        clean = SpokenTextFilter().feed(raw, final=True).strip()
        route = json.loads(clean)["route"]
        if route not in ("normal", "reasoning"):
            raise ValueError("invalid routing result")
        return route

    async def stream(self, messages, route="normal"):
        endpoint = self.reasoning if route == "reasoning" else self.normal
        if endpoint is None:
            raise RuntimeError("reasoning endpoint is not configured")
        filtered = SpokenTextFilter()
        got_text = False
        finished = False
        async with self.client.stream(
                "POST", endpoint.url, headers=self.headers(endpoint),
                json=self.body(endpoint, messages, stream=True)) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    finished = True
                    break
                if not data:
                    continue
                payload = json.loads(data)
                if payload.get("error"):
                    raise RuntimeError("LLM stream returned an error")
                choices = payload.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                reason = choice.get("finish_reason")
                if reason == "length":
                    raise RuntimeError("answer token budget exhausted")
                if reason and reason != "stop":
                    raise RuntimeError(f"unexpected answer finish reason: {reason}")
                # reasoning / reasoning_content is deliberately never forwarded.
                content = choice.get("delta", {}).get("content") or ""
                if not isinstance(content, str):
                    raise RuntimeError("LLM content is not text")
                text = filtered.feed(content)
                if text:
                    got_text = got_text or bool(text.strip())
                    yield text
                if reason == "stop":
                    finished = True
            tail = filtered.feed("", final=True)
            if tail:
                got_text = got_text or bool(tail.strip())
                yield tail
        if not finished or not got_text or filtered.hidden:
            raise RuntimeError("LLM stream ended without a complete spoken answer")

    async def decide_interruption(self, messages, pending):
        # Reuse the loaded normal model; choose the action and compute route in
        # one short request. Never forward its JSON as spoken answer text.
        # Only delivered speech can make a new "yes" an answer to our question.
        # Unheard drafts are for rewriting AFTER a revise decision, never for
        # deciding what the user has responded to.
        delivered = {key: pending[key] for key in ("question", "spoken_text", "hold_requested") if key in pending}
        context = {"conversation": [m for m in messages if m["role"] != "system"][-9:],
                   "suspended_answer": delivered}
        response = await self.client.post(
            self.normal.url, headers=self.headers(self.normal),
            json=self.body(self.normal, [{"role": "system", "content": INSTRUCTION},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False)}],
                stream=False, max_completion_tokens=160, temperature=0,
                response_format={"type": "json_object"}), timeout=6)
        response.raise_for_status()
        choice = response.json()["choices"][0]
        if choice.get("finish_reason") != "stop":
            raise ValueError("Incomplete interruption decision")
        raw = choice["message"].get("content") or ""
        clean = SpokenTextFilter().feed(raw, final=True).strip()
        return TurnDecision.parse(json.loads(clean), self.reasoning is not None)

    async def close(self):
        await self.client.aclose()
