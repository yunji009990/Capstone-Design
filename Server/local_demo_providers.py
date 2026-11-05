"""API LLM·STT 어댑터. 운영 Gemma/Whisper 경로는 그대로 유지한다."""
from __future__ import annotations

import io
import math
import wave

import httpx
import numpy as np

from realtime_audio import Observation, SAMPLE_RATE
from realtime_llm import ContextLimitError, LLMClient


class OpenAILLMClient(LLMClient):
    """기존 스트리밍·메모리·끼어들기 계약을 OpenAI Chat Completions로 연결."""

    @staticmethod
    def body(endpoint, messages, **overrides):
        body = LLMClient.body(endpoint, messages, **overrides)
        # memory_json도 Gemma 전용 필드를 넣으므로 마지막 직렬화 경계에서 제거한다.
        for name in ("chat_template_kwargs", "skip_special_tokens", "top_k", "min_p", "repetition_penalty"):
            body.pop(name, None)
        # 추론 모델의 샘플링 옵션 지원 차이를 피한다. effort는 실행 설정에서 명시한다.
        body.pop("temperature", None)
        body.pop("top_p", None)
        body["store"] = False
        return body

    async def fit_messages(self, messages, endpoint, *, output_tokens=None, trim=True):
        """vLLM /tokenize 없이 UTF-8 바이트 상한으로 문맥을 보수적으로 제한한다.

        정확한 과금 토큰 수가 아니다. 현재 문자열 대화 계약에 한해 1바이트당
        1토큰과 메시지 여유를 잡는다. 인물·현재 발화는 절대 잘라내지 않는다.
        """
        budget = self.context_limit or 24000
        output = endpoint.max_tokens if output_tokens is None else output_tokens

        def fits(items):
            used = 128 + output
            for item in items:
                if not isinstance(item.get("content"), str):
                    raise ValueError("로컬 대화는 문자열 메시지만 지원합니다.")
                used += len(item["content"].encode("utf-8")) + 32
            return used <= budget

        if fits(messages):
            return messages
        if trim and messages and messages[0]["role"] == "system":
            for start in range(1, len(messages)):
                if messages[start]["role"] == "user":
                    candidate = [messages[0]] + messages[start:]
                    if fits(candidate):
                        return candidate
        raise ContextLimitError("인물 정보나 현재 발화가 너무 깁니다. 내용을 줄여 다시 시도해 주세요.")


class OpenAIFrontend:
    """완료된 발화 WAV를 API로 전사한다. 입력/참조 WAV는 디스크에 저장하지 않는다."""

    def __init__(self, key, model="gpt-transcribe", *, client=None):
        if not key:
            raise ValueError("OPENAI_API_KEY 환경 변수가 필요합니다.")
        self.key, self.model = key, model
        self.display_name = "OpenAI " + model
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(40, connect=5), trust_env=False)

    async def transcribe(self, pcm, sample_rate=SAMPLE_RATE):
        if type(sample_rate) is not int or not 8000 <= sample_rate <= 96000 or len(pcm) % 2:
            raise ValueError("잘못된 PCM 입력입니다.")
        if len(pcm) > sample_rate * 2 * 30:
            raise ValueError("전사할 음성은 30초 이하여야 합니다.")
        samples = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768
        rms = float(np.sqrt(np.mean(samples * samples))) if len(samples) else 0.0
        prosody = {"duration_sec": round(len(samples) / sample_rate, 3),
                   "rms_dbfs": round(20 * math.log10(max(rms, 1e-6)), 1)}
        if not len(samples) or rms == 0:
            return Observation("", prosody=prosody)
        with io.BytesIO() as buffer:
            with wave.open(buffer, "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(sample_rate)
                out.writeframes(pcm)
            response = await self.client.post(
                "https://api.openai.com/v1/audio/transcriptions",
                headers={"Authorization": "Bearer " + self.key},
                data={"model": self.model, "language": "ko", "response_format": "json"},
                files={"file": ("speech.wav", buffer.getvalue(), "audio/wav")})
        response.raise_for_status()
        result = response.json()
        if not isinstance(result.get("text"), str):
            raise ValueError("음성 인식 응답에 전사 문자열이 없습니다.")
        # Whisper의 avg_logprob/no_speech_prob는 이 모델이 제공하지 않는다.
        # 실측하지 않은 신뢰도·소리 이벤트를 만들지 않는다. Silero 검증은 계속 사용한다.
        return Observation(result["text"].strip(), language="ko", prosody=prosody)

    async def aclose(self):
        await self.client.aclose()
