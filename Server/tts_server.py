"""Qwen3-TTS worker: 생성 중 PCM 전송, 연결별 참조 음성, 끊김 시 추론 취소.

기본 vllm_omni 엔진은 별도 환경에서 실행한다. TTS_BACKEND=legacy 설정은
이전의 구절 완성 후 전송 방식으로 돌아가는 명시적 복구 경로다.
"""
from __future__ import annotations

import asyncio
import base64
import hmac
import json
import logging
import os
import threading
import time
import uuid
import anyio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)


@asynccontextmanager
async def closing_stream(stream):
    try:
        yield stream
    finally:
        with anyio.CancelScope(shield=True):
            await stream.aclose()


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    voice_id: str = Field(min_length=32, max_length=32)


class ReferenceRequest(BaseModel):
    pcm: str = Field(min_length=1, max_length=3072000)  # 12 s, 96 kHz mono PCM16
    sample_rate: int = Field(ge=8000, le=96000)
    text: str = Field(min_length=1, max_length=2000)


class QwenVoice:
    streaming = "phrase_pcm"

    def __init__(self):
        import torch
        from qwen_tts import Qwen3TTSModel
        self.model_id = os.environ.get("TTS_MODEL", "Qwen/Qwen3-TTS-12Hz-1.7B-Base")
        self.model = Qwen3TTSModel.from_pretrained(
            self.model_id, device_map="cuda:0", dtype=torch.bfloat16,
            attn_implementation="sdpa")
        if self.model.model.tts_model_type != "base":
            raise ValueError("Reference cloning requires a Qwen3-TTS Base checkpoint")

    def create_prompt(self, pcm, rate, text):
        import numpy as np
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.
        # ICL retains reference audio codes AND its transcript, in addition to
        # speaker identity. Keep the returned list: it also carries ref_text.
        return self.model.create_voice_clone_prompt(
            ref_audio=(audio, rate), ref_text=text, x_vector_only_mode=False)

    def synthesize(self, text, prompt, cancelled):
        import numpy as np
        from transformers import StoppingCriteria, StoppingCriteriaList

        class StopOnDisconnect(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return cancelled.is_set()

        started = time.monotonic()
        wavs, rate = self.model.generate_voice_clone(
            text=text, language="Korean", voice_clone_prompt=prompt,
            non_streaming_mode=False,
            max_new_tokens=600, stopping_criteria=StoppingCriteriaList([StopOnDisconnect()]))
        if cancelled.is_set():
            return b"", rate, 0
        samples = np.asarray(wavs[0], dtype=np.float32).reshape(-1)
        if not len(samples) or not np.isfinite(samples).all() or rate != 24000:
            raise ValueError("Unexpected Qwen audio output")
        return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes(), rate, time.monotonic() - started


def create_app(voice=None, token=None):
    token = os.environ.get("TTS_TOKEN", "") if token is None else token
    gate = None
    prompts = {}

    def auth(value):
        if token and not hmac.compare_digest(value, token):
            raise HTTPException(403, "Unauthorized")

    async def release_prompt(prompt):
        if hasattr(app.state.voice, "release_prompt"):
            await app.state.voice.release_prompt(prompt)

    async def purge():
        # Normal connection teardown deletes immediately. Bound orphaned prompts
        # after a client/process failure, including a cancelled preparation.
        for key, (prompt, used) in list(prompts.items()):
            if time.monotonic() - used > 6 * 3600:
                await release_prompt(prompt)
                del prompts[key]

    @asynccontextmanager
    async def lifespan(app):
        nonlocal gate
        gate = asyncio.Lock()
        backend = os.environ.get("TTS_BACKEND", "vllm_omni")
        if voice is not None:
            app.state.voice = voice
        elif backend == "legacy":
            app.state.voice = await asyncio.to_thread(QwenVoice)
        elif backend == "vllm_omni":
            from tts_omni import OmniVoice
            app.state.voice = OmniVoice()
        else:
            raise ValueError("TTS_BACKEND must be vllm_omni or legacy")
        try:
            if hasattr(app.state.voice, "start"):
                await app.state.voice.start()
            yield
        finally:
            prompts.clear()
            if hasattr(app.state.voice, "close"):
                await app.state.voice.close()

    app = FastAPI(title="Qwen3-TTS worker", lifespan=lifespan)

    @app.get("/health")
    async def health():
        ready = (await app.state.voice.available()
                 if hasattr(app.state.voice, "available") else True)
        return {"status": "ready" if ready else "unavailable", "model": app.state.voice.model_id,
                "voice_mode": "reference_icl", "device": "cuda:0",
                "streaming": getattr(app.state.voice, "streaming", "phrase_pcm"),
                "busy": gate.locked(), "references": len(prompts)}

    @app.post("/voices")
    async def prepare(body: ReferenceRequest, x_token: str = Header("")):
        auth(x_token)
        try:
            pcm = base64.b64decode(body.pcm, validate=True)
        except ValueError as exc:
            raise HTTPException(400, "Invalid reference PCM") from exc
        if (len(pcm) % 2 or not 3 <= len(pcm) / (body.sample_rate * 2) <= 12
                or not body.text.strip()):
            raise HTTPException(400, "Reference requires 3–12 seconds of PCM and its transcript")
        async with gate:
            await purge()
            if len(prompts) >= 8:
                raise HTTPException(503, "Reference prompt capacity reached")
            worker = app.state.voice
            preparation = (worker.create_prompt(pcm, body.sample_rate, body.text.strip())
                           if hasattr(worker, "stream") else asyncio.to_thread(
                               worker.create_prompt, pcm, body.sample_rate, body.text.strip()))
            task = asyncio.create_task(preparation)
            try:
                prompt = await asyncio.shield(task)
            except asyncio.CancelledError:
                with anyio.CancelScope(shield=True):
                    with suppress(Exception, asyncio.CancelledError):
                        prompt = await asyncio.shield(task)
                        await release_prompt(prompt)
                raise
            voice_id = uuid.uuid4().hex
            prompts[voice_id] = prompt, time.monotonic()
        return {"voice_id": voice_id, "voice_mode": "reference_icl"}

    @app.delete("/voices/{voice_id}")
    async def release(voice_id: str, x_token: str = Header("")):
        auth(x_token)
        async with gate:
            if voice_id in prompts:
                await release_prompt(prompts[voice_id][0])
                del prompts[voice_id]
        return {"released": True}

    @app.post("/synthesize")
    async def synthesize(body: SpeechRequest, x_token: str = Header("")):
        auth(x_token)
        if not body.text.strip():
            raise HTTPException(400, "Empty speech")
        if body.voice_id not in prompts:
            raise HTTPException(404, "Reference expired; reconnect to prepare it again")

        async def packets():
            def line(data):
                return json.dumps(data, ensure_ascii=False) + "\n"
            cancelled = threading.Event()
            task = None
            # Disconnect cancels this generator, including while waiting for gate.
            async with gate:
                try:
                    await purge()
                    if body.voice_id not in prompts:
                        raise ValueError("Reference was released")
                    prompt, _ = prompts[body.voice_id]
                    prompts[body.voice_id] = prompt, time.monotonic()
                    yield line({"type": "started"})
                    started = time.monotonic()
                    audio_bytes = 0
                    if hasattr(app.state.voice, "stream"):
                        # Explicit aclose propagates a downstream disconnect even
                        # when this generator was suspended while yielding PCM.
                        async with closing_stream(app.state.voice.stream(body.text.strip(), prompt)) as stream:
                            async for pcm in stream:
                                if not pcm or len(pcm) > 9600 or len(pcm) % 2:
                                    raise ValueError("Invalid streaming PCM packet")
                                audio_bytes += len(pcm)
                                yield line({"type": "audio", "sample_rate": 24000,
                                            "pcm": base64.b64encode(pcm).decode()})
                        elapsed = time.monotonic() - started
                    else:
                        task = asyncio.create_task(asyncio.to_thread(
                            app.state.voice.synthesize, body.text.strip(), prompt, cancelled))
                        pcm, rate, elapsed = await asyncio.shield(task)
                        audio_bytes = len(pcm)
                        for offset in range(0, len(pcm), 9600):
                            yield line({"type": "audio", "sample_rate": rate,
                                        "pcm": base64.b64encode(pcm[offset:offset + 9600]).decode()})
                    yield line({"type": "done", "synthesis_sec": elapsed,
                                "audio_sec": audio_bytes / 48000})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Qwen synthesis failed")
                    yield line({"type": "error", "message": "Qwen3-TTS synthesis failed"})
                finally:
                    cancelled.set()
                    if task is not None:
                        # Keep gate held until the GPU thread really stops.
                        with anyio.CancelScope(shield=True):
                            with suppress(Exception, asyncio.CancelledError):
                                await asyncio.shield(task)

        return StreamingResponse(packets(), media_type="application/x-ndjson")

    return app


app = create_app()
