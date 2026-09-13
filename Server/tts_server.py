"""Qwen3-TTS worker. One GPU generation at a time, cancelled on disconnect.

The official Python API returns a complete phrase. This service streams its PCM
in bounded packets; the dialogue service submits phrases before the LLM finishes.
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


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    voice_id: str = Field(min_length=32, max_length=32)


class ReferenceRequest(BaseModel):
    pcm: str = Field(min_length=1, max_length=3072000)  # 12 s, 96 kHz mono PCM16
    sample_rate: int = Field(ge=8000, le=96000)
    text: str = Field(min_length=1, max_length=2000)


class QwenVoice:
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

    def purge():
        # Normal connection teardown deletes immediately. Bound orphaned prompts
        # after a client/process failure, including a cancelled preparation.
        for key, (_, used) in list(prompts.items()):
            if time.monotonic() - used > 6 * 3600:
                del prompts[key]

    @asynccontextmanager
    async def lifespan(app):
        nonlocal gate
        gate = asyncio.Lock()
        app.state.voice = voice or await asyncio.to_thread(QwenVoice)
        yield
        prompts.clear()

    app = FastAPI(title="Qwen3-TTS worker", lifespan=lifespan)

    @app.get("/health")
    def health():
        return {"status": "ready", "model": app.state.voice.model_id,
                "voice_mode": "reference_icl", "device": "cuda:0",
                "streaming": "phrase_pcm", "busy": gate.locked(), "references": len(prompts)}

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
            purge()
            if len(prompts) >= 8:
                raise HTTPException(503, "Reference prompt capacity reached")
            task = asyncio.create_task(asyncio.to_thread(
                app.state.voice.create_prompt, pcm, body.sample_rate, body.text.strip()))
            try:
                prompt = await asyncio.shield(task)
            finally:
                with anyio.CancelScope(shield=True):
                    with suppress(Exception, asyncio.CancelledError):
                        await asyncio.shield(task)
            voice_id = uuid.uuid4().hex
            prompts[voice_id] = prompt, time.monotonic()
        return {"voice_id": voice_id, "voice_mode": "reference_icl"}

    @app.delete("/voices/{voice_id}")
    async def release(voice_id: str, x_token: str = Header("")):
        auth(x_token)
        async with gate:
            prompts.pop(voice_id, None)
        return {"released": True}

    @app.post("/synthesize")
    async def synthesize(body: SpeechRequest, x_token: str = Header("")):
        auth(x_token)
        if not body.text.strip():
            raise HTTPException(400, "Empty speech")
        purge()
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
                    if body.voice_id not in prompts:
                        raise ValueError("Reference was released")
                    prompt, _ = prompts[body.voice_id]
                    prompts[body.voice_id] = prompt, time.monotonic()
                    yield line({"type": "started"})
                    task = asyncio.create_task(asyncio.to_thread(
                        app.state.voice.synthesize, body.text.strip(), prompt, cancelled))
                    pcm, rate, elapsed = await asyncio.shield(task)
                    for offset in range(0, len(pcm), 9600):  # 200 ms, 12.8 KB JSON
                        yield line({"type": "audio", "sample_rate": rate,
                                    "pcm": base64.b64encode(pcm[offset:offset + 9600]).decode()})
                    yield line({"type": "done", "synthesis_sec": elapsed,
                                "audio_sec": len(pcm) / (rate * 2)})
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
