"""VoxCPM2를 공통 TTS API의 24kHz PCM 스트리밍 계약에 연결한다.

검증된 실험 어댑터의 리샘플러 flush·취소·워커 직렬화 동작을 유지한다.
참조 원음은 tmpfs에서 준비하고 즉시 지우며, 화자 캐시는 연결 수명 동안 RAM에만 둔다.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
# (의도 확인용: asyncio.Queue 만 사용한다)
import sys
import tempfile
import threading
import time
import wave
from contextlib import suppress

import anyio
from pathlib import Path

log = logging.getLogger(__name__)

_SHM = "/dev/shm"
_OUT_RATE = 24000
_MAX_PACKET = 9600
_QUEUE_MAX = 8

class VoxVoice:
    """Candidate(VoxCPM2) 를 tts_server 백엔드 계약에 맞춘 비동기 어댑터."""

    streaming = "generation_pcm"
    voice_mode = "reference_audio"

    def __init__(self):
        self._path = os.path.expanduser(os.environ.get("TTS_VOX_MODEL", "openbmb/VoxCPM2"))
        self._mode = "reference"
        # 참조 전용 복제 방식은 별도 voice_mode 필드로 보고한다.
        self.model_id = "openbmb/VoxCPM2"
        self._vox = None
        self._rate = 0
        self._thread = None
        self._cancel = threading.Event()

    # --- 수명 -------------------------------------------------------- #
    async def start(self):
        source = os.environ.get("TTS_VOX_SOURCE", "")
        if source:
            folder = Path(source).expanduser().resolve()
            if not (folder / "voxcpm").is_dir():
                raise ValueError("TTS_VOX_SOURCE must contain the voxcpm package")
            if str(folder) not in sys.path:
                sys.path.append(str(folder))
        from tts_vox_native import Candidate  # torch 로딩을 기동 시점으로 미룬다

        self._vox = await asyncio.to_thread(
            Candidate, model_path=self._path, mode=self._mode,
            optimize=False, device="cuda:0")
        self._rate = int(self._vox.rate)
        log.info("vox ready: mode=%s in_rate=%d", self._mode, self._rate)

    async def available(self):
        return self._vox is not None

    async def close(self):
        await self._drain_worker()
        vox, self._vox = self._vox, None
        if vox is not None:
            await asyncio.to_thread(vox.close)

    # --- 참조 음성 ---------------------------------------------------- #
    async def create_prompt(self, pcm, rate, text):
        if self._vox is None:
            raise RuntimeError("model not ready")
        transcript = text if self._mode == "ultimate" else None
        return await asyncio.to_thread(self._prepare_sync, pcm, int(rate), transcript)

    def _prepare_sync(self, pcm, rate, transcript):
        """참조 PCM 을 /dev/shm 임시 wav 로만 넘기고 즉시 지운다. 캐시는 RAM 에 남는다."""
        if not os.path.isdir(_SHM):
            raise RuntimeError("/dev/shm 이 없다. 참조 음성을 디스크에 쓰지 않는다")
        folder = tempfile.mkdtemp(prefix="voxref-", dir=_SHM)
        os.chmod(folder, 0o700)
        path = os.path.join(folder, "ref.wav")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as raw, contextlib.closing(wave.open(raw, "wb")) as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(rate)
                out.writeframes(pcm)
            return self._vox.prepare(path, transcript)
        finally:
            with suppress(OSError):
                os.remove(path)
            with suppress(OSError):
                os.rmdir(folder)

    async def release_prompt(self, prompt):
        if self._vox is not None:
            self._vox.close_prompt(prompt)
        elif isinstance(prompt, dict):
            prompt.clear()

    # --- 스트리밍 ----------------------------------------------------- #
    async def stream(self, text, prompt):
        if self._vox is None:
            raise RuntimeError("model not ready")
        await self._drain_worker()  # 이전 워커가 끝나기 전에 다음 워커를 띄우지 않는다

        loop = asyncio.get_running_loop()
        items: asyncio.Queue = asyncio.Queue()       # 이벤트 루프 전용
        slots = threading.Semaphore(_QUEUE_MAX)      # 실제 상한은 이 세마포어가 잡는다
        cancel = threading.Event()
        worker = threading.Thread(
            target=self._worker, name="vox-stream", daemon=True,
            args=(text, prompt, loop, items, slots, cancel))
        self._thread, self._cancel = worker, cancel

        started = time.monotonic()
        packets = 0
        finished = False
        worker.start()
        try:
            while True:
                kind, payload = await items.get()
                if kind == "pcm":
                    slots.release()
                    packets += 1
                    yield payload
                elif kind == "error":
                    raise payload            # 워커 예외를 삼키지 않는다
                else:
                    finished = True
                    break
        finally:
            cancel.set()
            join_at = time.monotonic()
            with anyio.CancelScope(shield=True):
                await asyncio.to_thread(worker.join)
            self._thread = None
            log.info("vox stream: packets=%d finished=%s join_ms=%.1f total_ms=%.1f",
                     packets, finished, (time.monotonic() - join_at) * 1000,
                     (time.monotonic() - started) * 1000)

    async def _drain_worker(self):
        worker = self._thread
        if worker is not None and worker.is_alive():
            self._cancel.set()
            with anyio.CancelScope(shield=True):
                await asyncio.to_thread(worker.join)
        self._thread = None

    def _worker(self, text, prompt, loop, items, slots, cancel):
        """네이티브 청크를 받아 24k PCM16 패킷으로 잘라 넘긴다. gen.close() 도 이 스레드에서."""
        def emit(item):
            loop.call_soon_threadsafe(items.put_nowait, item)

        def push(samples):
            data = np.asarray(samples, dtype=np.float32).reshape(-1)
            if data.size == 0:
                return True
            if not np.isfinite(data).all():
                raise ValueError("Vox returned non-finite audio")
            pcm = np.rint(data * 32768.0).clip(-32768, 32767).astype("<i2").tobytes()
            for off in range(0, len(pcm), _MAX_PACKET):
                while not slots.acquire(timeout=0.05):
                    if cancel.is_set():
                        return False       # 큐가 차 있어도 정리를 막지 않는다
                if cancel.is_set():
                    slots.release()
                    return False
                emit(("pcm", pcm[off:off + _MAX_PACKET]))
            return True

        gen = None
        try:
            # 의존성 적재 실패도 error 패킷으로 알린다. 소비자를 매달지 않는다.
            import numpy as np
            import soxr
            empty = np.zeros(0, dtype=np.float32)
            stream = soxr.ResampleStream(self._rate, _OUT_RATE, 1,
                                         dtype="float32", quality="HQ")
            gen = self._vox.stream(text, prompt)
            completed = True
            for chunk in gen:
                if cancel.is_set() or not push(stream.resample_chunk(chunk)):
                    completed = False      # 현재 네이티브 청크까지만 처리하고 협조적으로 멈춘다
                    break
            if completed:
                push(stream.resample_chunk(empty, last=True))  # 정상 종료에서만 flush
        except Exception as exc:           # BaseException 은 잡지 않는다
            emit(("error", exc))
            return
        finally:
            if gen is not None:
                with suppress(Exception):
                    gen.close()
        emit(("end", None))

