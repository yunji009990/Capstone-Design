"""vLLM-Omni의 생성 중 오디오를 기존 Unity PCM 계약으로 전달한다.

엔진은 별도 Python 환경/프로세스다. HTTP 연결을 닫으면 진행 중인 추론도
취소된다. 기본 Qwen Python API의 완성된 WAV를 쪼개는 경로와 구분한다.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import os
from pathlib import Path
import signal
import socket
import struct
import tempfile
import time
from urllib.parse import urlsplit
import uuid
import wave

import httpx

log = logging.getLogger(__name__)
MODEL = "Qwen/Qwen3-TTS-12Hz-1.7B-Base"


class OmniVoice:
    streaming = "generation_pcm"

    def __init__(self, url=None, *, client=None, managed=None):
        self.model_id = os.environ.get("TTS_MODEL", MODEL)
        self.url = (url or os.environ.get("TTS_ENGINE_URL", "http://127.0.0.1:8004")).rstrip("/")
        parsed = urlsplit(self.url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or parsed.path:
            raise ValueError("TTS engine must use an HTTP IPv4 loopback URL")
        self.port = parsed.port or 80
        self.managed = (os.environ.get("TTS_ENGINE_MANAGED", "1") == "1"
                        if managed is None else managed)
        self.token = os.environ.get("TTS_TOKEN", "")
        self.http = client or httpx.AsyncClient(
            timeout=httpx.Timeout(90, connect=5), trust_env=False,
            headers={"Authorization": "Bearer " + self.token} if self.token else {})
        self.process = None
        self.voice_dir = None
        self.log_file = None
        self.owned_voices = set()

    async def start(self):
        try:
            if self.managed:
                with socket.socket() as probe:
                    if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                        raise RuntimeError("TTS engine port is already occupied; no process was replaced")
                folder = Path(__file__).resolve().parent
                python = Path(os.environ.get("TTS_ENGINE_PYTHON", "~/venv/qwentts-stream/bin/python")).expanduser()
                config = Path(os.environ.get("TTS_ENGINE_CONFIG", str(folder / "tts_streaming.yaml"))).expanduser()
                if not python.is_file() or not config.is_file():
                    raise RuntimeError("Install requirements-tts-streaming.txt and configure TTS_ENGINE_PYTHON")
                # Each owned engine gets a private, ephemeral reference directory.
                # Restarting cannot restore a previous connection's voice upload.
                self.voice_dir = tempfile.TemporaryDirectory(prefix="capstone-tts-voices-")
                env = os.environ.copy()
                env["PATH"] = str(python.parent) + os.pathsep + env.get("PATH", "")
                env["VIRTUAL_ENV"] = str(python.parent.parent)
                env.update(SPEAKER_SAMPLES_DIR=self.voice_dir.name, SPEAKER_MAX_UPLOADED="8",
                           OMP_NUM_THREADS="2", TOKENIZERS_PARALLELISM="false",
                           MAX_JOBS="2", VLLM_WORKER_MULTIPROC_METHOD="spawn",
                           VLLM_LOGGING_LEVEL="WARNING")  # Upstream INFO logs speech text.
                # Torch wheels include CUDA 13 nvcc. Do not accidentally select
                # an older system toolkit on Blackwell or change system CUDA.
                cuda = os.environ.get("TTS_ENGINE_CUDA_HOME")
                bundled = list(python.parent.parent.glob("lib/python*/site-packages/nvidia/cu13/bin/nvcc"))
                if cuda:
                    env["CUDA_HOME"] = str(Path(cuda).expanduser())
                elif len(bundled) == 1:
                    env["CUDA_HOME"] = str(bundled[0].parent.parent)
                if self.token:
                    env["VLLM_API_KEY"] = self.token
                self.log_file = (folder / "tts-engine.log").open("ab", buffering=0)
                self.process = await asyncio.create_subprocess_exec(
                    str(python), "-m", "vllm.entrypoints.cli.main", "serve", self.model_id,
                    "--omni", "--host", "127.0.0.1", "--port", str(self.port),
                    "--deploy-config", str(config), cwd=str(folder), env=env,
                    stdin=asyncio.subprocess.DEVNULL, stdout=self.log_file,
                    stderr=self.log_file, start_new_session=True)
            deadline = time.monotonic() + float(os.environ.get("TTS_ENGINE_START_TIMEOUT", "600"))
            while not await self.available():
                if self.process is not None and self.process.returncode is not None:
                    raise RuntimeError("TTS engine exited during startup; see tts-engine.log")
                if time.monotonic() >= deadline:
                    raise TimeoutError("TTS engine startup timed out; see tts-engine.log")
                await asyncio.sleep(.5)
            response = await self.http.get(self.url + "/v1/models")
            response.raise_for_status()
            if self.model_id not in {item["id"] for item in response.json()["data"]}:
                raise RuntimeError("TTS engine is serving a different checkpoint")
            if self.managed and os.environ.get("TTS_ENGINE_WARMUP", "1") == "1":
                # Exercise Base ICL + decoder JIT before declaring the worker
                # ready. Synthetic tone only; no participant recording persists.
                pcm = struct.pack("<64000h", *(int(1800 * math.sin(i * 2 * math.pi * 220 / 16000))
                                               for i in range(64000)))
                prompt = await self.create_prompt(pcm, 16000, "음성 준비를 확인합니다.")
                try:
                    warmup_text = "음성 연결을 준비하고 있습니다. 여러 문장을 자연스럽게 이어서 말하는 과정을 확인합니다. " * 4
                    async for _ in self.stream(warmup_text, prompt, max_tokens=192):
                        pass
                finally:
                    await self.release_prompt(prompt)
        except BaseException:
            await self.close()
            raise

    async def available(self):
        if self.process is not None and self.process.returncode is not None:
            return False
        try:
            response = await self.http.get(self.url + "/health", timeout=2)
            return response.status_code == 200
        except httpx.HTTPError:
            return False

    async def create_prompt(self, pcm, rate, text):
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(rate)
            wav.writeframes(pcm)
        name = "capstone_" + uuid.uuid4().hex
        # Add before POST so shutdown can also remove an upload whose response
        # was lost. The caller shields preparation and releases on cancellation.
        self.owned_voices.add(name)
        try:
            response = await self.http.post(self.url + "/v1/audio/voices",
                files={"audio_sample": ("reference.wav", buffer.getvalue(), "audio/wav")},
                data={"name": name, "consent": "connection_reference", "ref_text": text})
            response.raise_for_status()
            result = response.json()
            if result.get("success") is not True or result.get("voice", {}).get("name") != name:
                raise ValueError("Unexpected TTS reference response")
            return name
        except Exception:
            await self.release_prompt(name)
            raise

    async def release_prompt(self, prompt):
        response = await self.http.delete(self.url + "/v1/audio/voices/" + prompt)
        if response.status_code != 404:
            response.raise_for_status()
        self.owned_voices.discard(prompt)

    async def stream(self, text, prompt, *, max_tokens=600):
        request = {"model": self.model_id, "input": text, "voice": prompt,
                   "task_type": "Base", "language": "Korean", "x_vector_only_mode": False,
                   "non_streaming_mode": False, "max_new_tokens": max_tokens,
                   "stream": True, "stream_format": "sse", "response_format": "pcm"}
        received = False
        async with self.http.stream("POST", self.url + "/v1/audio/speech", json=request) as response:
            response.raise_for_status()
            if "text/event-stream" not in response.headers.get("content-type", ""):
                raise ValueError("TTS engine did not return a generation stream")
            data_lines = []
            size = 0
            # Parse SSE incrementally, including multi-line data and CRLF.
            async for line in response.aiter_lines():
                size += len(line)
                if size > 1024 * 1024:
                    raise ValueError("Oversized TTS engine event")
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip(" "))
                elif not line:
                    size = 0
                    if not data_lines:
                        continue
                    data = json.loads("\n".join(data_lines))
                    data_lines.clear()
                    kind = data.get("type")
                    if kind == "speech.audio.delta":
                        pcm = base64.b64decode(data["audio"], validate=True)
                        if data.get("response_format") != "pcm" or not pcm or len(pcm) % 2:
                            raise ValueError("Invalid streaming PCM from TTS engine")
                        received = True
                        for offset in range(0, len(pcm), 9600):
                            yield pcm[offset:offset + 9600]
                    elif kind == "speech.audio.done":
                        if not received:
                            raise RuntimeError("TTS engine returned empty audio")
                        return
                    elif kind == "speech.audio.error":
                        raise RuntimeError("TTS engine reported a synthesis error")
                    else:
                        raise ValueError("Unexpected TTS engine event")
        raise RuntimeError("TTS engine stream ended before completion")

    async def close(self):
        # Close owned references before the owned engine; never stop an external
        # engine selected with TTS_ENGINE_MANAGED=0.
        async def release(name):
            try:
                await asyncio.wait_for(self.release_prompt(name), timeout=2)
            except Exception:
                log.warning("Could not release TTS reference on shutdown")
        await asyncio.gather(*(release(name) for name in tuple(self.owned_voices)))
        if self.process is not None:
            pid = self.process.pid
            try:
                os.killpg(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(self.process.wait(), timeout=8)
            except asyncio.TimeoutError:
                pass
            # The API parent can exit before its model workers. They still
            # belong to the process group we created, so reap that group too.
            try:
                os.killpg(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await self.process.wait()
            self.process = None
        if self.log_file is not None:
            self.log_file.close()
            self.log_file = None
        if self.voice_dir is not None:
            self.voice_dir.cleanup()
            self.voice_dir = None
        await self.http.aclose()
