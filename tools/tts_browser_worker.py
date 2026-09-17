"""원격에서 stdin 코드로만 실행하는 TTS 청취 점검 워커.

서버에 파일을 남기지 않고, 실행 중인 대화 서버의 설정을 메모리로만 읽는다.
토큰·참조 ID·전사·PCM은 어떤 로그에도 남기지 않고 응답 이벤트로만 나간다.
"""
import asyncio
import base64
import hashlib
import io
import json
import os
import signal
import sys
import time
import wave
from types import SimpleNamespace

HOME = os.path.expanduser("~")
SERVER = os.path.join(HOME, "capstone-server")
API = "http://127.0.0.1:8002"
RATE = 24000
MAX_SAMPLES = 45 * RATE
MAX_PACKET_SAMPLES = 4800  # 조각 하나의 상한. 바이트로는 9600이다.
WAV_PACKET_BYTES = 9600  # 완성 WAV 를 나눠 보낼 때의 조각 상한.
CREATE_TIMEOUT = 30  # 취소돼도 생성 응답을 이만큼 기다려 서버가 만든 ID를 회수한다.
GEN_TIMEOUT = 150
CLEAN_TIMEOUT = 10
KEYS = ("DIALOGUE_TOKEN", "DIALOGUE_TTS_TOKEN", "DIALOGUE_TTS_URL")


class Failure(Exception):
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def emit(event):
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def service_env():
    """실행 중인 대화 서버 프로세스의 환경 변수를 메모리로만 읽는다."""
    try:
        with open(os.path.join(SERVER, "dialogue.pid"), "r") as handle:
            pid = int(handle.read().strip())
        with open("/proc/%d/environ" % pid, "rb") as handle:
            raw = handle.read()
    except (OSError, ValueError):
        raise Failure("service_unavailable")
    found = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, value = item.split(b"=", 1)
        name = key.decode("utf-8", "replace")
        if name in KEYS:
            found[name] = value.decode("utf-8", "replace")
    if not found.get("DIALOGUE_TTS_URL"):
        raise Failure("service_unavailable")
    return found


def load_modules():
    for path in (SERVER, os.path.join(SERVER, "Server")):
        if os.path.isdir(path) and path not in sys.path:
            sys.path.insert(0, path)
    try:
        import realtime_tts
        import realtime_dialogue
    except ImportError:
        raise Failure("service_unavailable")
    return realtime_tts, realtime_dialogue


def decode_wav(raw, text):
    """참조 오디오를 mono 16-bit로만 받아들인다."""
    try:
        with wave.open(io.BytesIO(raw), "rb") as wav:
            if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                raise Failure("invalid_audio")
            return SimpleNamespace(pcm=wav.readframes(wav.getnframes()),
                                   sample_rate=wav.getframerate(), text=text)
    except (wave.Error, EOFError):
        raise Failure("invalid_audio")


class Stats(object):
    def __init__(self):
        self.seq = 0
        self.samples = 0
        self.chunks = 0
        self.digest = hashlib.sha256()
        self.wav = bytearray()  # 같은 한 번의 합성 PCM 을 그대로 복제해 둔다.
        self.begin = time.monotonic()
        self.first = None


def is_count(value, low, high):
    """실제 개수만 허용한다. bool 은 int 이지만 개수가 아니다."""
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def emit_audio(stats, encoded, samples):
    if not isinstance(encoded, str) or not is_count(samples, 1, MAX_PACKET_SAMPLES):
        raise Failure("invalid_audio")
    try:
        pcm = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError):
        raise Failure("invalid_audio")
    if len(pcm) != samples * 2:
        # 수신 PCM 은 길이 그대로 보존한다. 자르거나 덧붙이지 않는다.
        raise Failure("invalid_audio")
    if stats.samples + samples > MAX_SAMPLES:
        raise Failure("too_long")
    stats.seq += 1
    stats.chunks += 1
    stats.samples += samples
    stats.digest.update(pcm)
    stats.wav += pcm  # 검증을 통과한 바로 그 PCM 만 모은다.
    if stats.first is None:
        stats.first = time.monotonic()
    emit({"type": "audio", "pcm": encoded, "samples": samples, "sample_rate": RATE,
          "channels": 1, "format": "pcm_s16le", "seq": stats.seq})


def build_wav(pcm):
    """모은 PCM 을 Python wave 로 완성 WAV 로 만든다. 디스크에는 남기지 않는다."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(pcm)
    return buffer.getvalue()


def emit_server_wav(data):
    """완성 WAV 를 조각으로 내보내고 조각 수를 돌려준다."""
    count = 0
    for start in range(0, len(data), WAV_PACKET_BYTES):
        count += 1
        chunk = data[start:start + WAV_PACKET_BYTES]
        emit({"type": "server_wav", "seq": count,
              "data": base64.b64encode(chunk).decode()})
    return count


async def close_stream(stream):
    """생성기를 명시적으로 닫는다. 취소 중에도 닫기를 끝낸 뒤 넘어간다."""
    closer = getattr(stream, "aclose", None)
    if closer is None:
        return
    task = asyncio.ensure_future(closer())
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await asyncio.wait([task])  # 닫기가 끝난 뒤에야 release 로 넘어간다.
        raise
    except Exception:
        raise Failure("cleanup_failed")


async def protect(coro):
    """생성 호출은 취소돼도 완료를 기다려 만들어진 자원 ID를 회수한다."""
    task = asyncio.ensure_future(coro)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await asyncio.wait_for(asyncio.shield(task), CREATE_TIMEOUT)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        raise


async def synthesize(request, voice, tts_mod, gap_pcm, gap_samples):
    text, mode = request["text"], request["mode"]
    if mode == "phrase":
        phrases = tts_mod.PhraseBuffer().push(text, final=True)
    else:
        phrases = [text]  # whole 모드에서는 문장을 나누지 않는다.
    phrases = [p for p in phrases if p.strip()]
    if not phrases:
        raise Failure("tts_failed")
    emit({"type": "started", "sample_rate": RATE, "channels": 1, "format": "pcm_s16le",
          "mode": mode, "phrases": len(phrases)})
    stats = Stats()
    gap_pending = False
    # SENTENCE_GAP_PCM 은 이미 base64 문자열이다. 다시 인코딩하지 않는다.
    encoded_gap = gap_pcm
    for index, phrase in enumerate(phrases, 1):
        first = True
        made_audio = False
        stream = voice.stream(phrase)
        try:
            async for encoded, samples in stream:
                if first and gap_pending:
                    # 생산과 같은 문장 간 무음을 실제로 넣는다.
                    emit_audio(stats, encoded_gap, gap_samples)
                    gap_pending = False
                first = False
                emit_audio(stats, encoded, samples)
                made_audio = True
        except (Failure, asyncio.CancelledError):
            raise
        except Exception:
            raise Failure("tts_failed")
        finally:
            await close_stream(stream)
        if not made_audio:
            # 문장 하나라도 오디오가 없으면 미완성이다. done 으로 끝내지 않는다.
            raise Failure("tts_failed")
        # 앞 문장이 실제 오디오를 낸 경우에만 다음 문장 앞에 간격을 둔다.
        gap_pending = tts_mod.ends_sentence(phrase)
        emit({"type": "boundary", "phrase": index, "samples": stats.samples,
              "chars": len(phrase)})
    if not stats.chunks:
        raise Failure("tts_failed")
    return stats


async def cleanup(http, tts, voice, reference_id):
    """생성 성패와 무관하게 임시 자원을 모두 되돌린다."""
    problems = []
    for step in ("voice", "reference", "tts", "http"):
        try:
            if step == "voice" and voice is not None:
                await voice.release()
            elif step == "reference" and reference_id:
                response = await http.delete("/references/" + reference_id)
                response.raise_for_status()
            elif step == "tts" and tts is not None:
                await tts.close()
            elif step == "http":
                await http.aclose()
        except Exception:
            problems.append(step)
    if problems:
        raise Failure("cleanup_failed")


async def run(request):
    import httpx
    tts_mod, dialogue_mod = load_modules()
    env = service_env()
    emit({"type": "status", "stage": "preparing"})
    http = httpx.AsyncClient(base_url=API, timeout=30, trust_env=False,
                             headers={"X-Token": env.get("DIALOGUE_TOKEN", "")})
    tts = voice = None
    reference_id = ""

    async def make_reference():
        """응답을 받는 즉시 ID를 바깥에 남겨, 취소돼도 삭제할 수 있게 한다."""
        nonlocal reference_id
        try:
            response = await http.post("/references", json={})
            response.raise_for_status()
            info = response.json()
            reference_id = info["reference_id"]
        except Exception:
            raise Failure("reference_failed")
        return info

    async def bind_voice(reference):
        """bind 결과도 응답 직후 바깥에 남겨 정리에서 반드시 풀게 한다."""
        nonlocal tts, voice
        try:
            tts = tts_mod.TTSClient(env["DIALOGUE_TTS_URL"], env.get("DIALOGUE_TTS_TOKEN", ""))
            voice = await tts.bind(reference)
        except Exception:
            raise Failure("tts_failed")

    try:
        try:
            response = await http.get("/health")
            response.raise_for_status()
            health = response.json()
        except Exception:
            raise Failure("service_unavailable")
        if health.get("connections", 0) != 0:
            raise Failure("unity_active")  # Unity 체험 중에는 생성하지 않는다.
        if not health.get("tts_ready"):
            raise Failure("service_unavailable")
        info = await protect(make_reference())
        try:
            audio = await http.get("/references/%s/audio.wav" % reference_id)
            audio.raise_for_status()
        except Exception:
            raise Failure("reference_failed")
        reference = decode_wav(audio.content, info.get("text", ""))
        emit({"type": "status", "stage": "generating"})
        await protect(bind_voice(reference))
        gap_pcm = dialogue_mod.SENTENCE_GAP_PCM
        gap_samples = dialogue_mod.SENTENCE_GAP_SAMPLES
        stats = await asyncio.wait_for(
            synthesize(request, voice, tts_mod, gap_pcm, gap_samples), GEN_TIMEOUT)
    finally:
        task = asyncio.ensure_future(cleanup(http, tts, voice, reference_id))
        try:
            # 정리에 실패하면 done 을 내보내지 않는다.
            await asyncio.wait_for(asyncio.shield(task), CLEAN_TIMEOUT)
        except asyncio.TimeoutError:
            raise Failure("cleanup_failed")
    # 생성과 자원 정리가 모두 끝난 뒤에 같은 PCM 으로 완성 WAV 를 만든다.
    data = build_wav(bytes(stats.wav))
    wav_chunks = emit_server_wav(data)
    now = time.monotonic()
    emit({"type": "done", "samples": stats.samples, "chunks": stats.chunks,
          "sha256": stats.digest.hexdigest(),
          "first_pcm_ms": int(((stats.first or now) - stats.begin) * 1000),
          "total_ms": int((now - stats.begin) * 1000),
          "server_wav_bytes": len(data), "server_wav_chunks": wav_chunks,
          "server_wav_sha256": hashlib.sha256(data).hexdigest()})


async def heartbeat():
    while True:
        await asyncio.sleep(1)
        emit({"type": "heartbeat"})  # 파이프가 끊기면 예외로 본 작업을 취소한다.


async def main_async(request):
    loop = asyncio.get_event_loop()
    task = loop.create_task(run(request))
    beat = loop.create_task(heartbeat())

    state = {"cancelled": False}

    def cancel_once(*_args):
        # 반복 SIGTERM·심박 실패가 진행 중인 정리를 다시 취소하지 않게 한 번만 취소한다.
        if not state["cancelled"]:
            state["cancelled"] = True
            task.cancel()

    def stop(_beat):
        if not _beat.cancelled() and _beat.exception() is not None:
            cancel_once()

    beat.add_done_callback(stop)
    for name in ("SIGTERM", "SIGHUP"):
        try:
            loop.add_signal_handler(getattr(signal, name), cancel_once)
        except (AttributeError, NotImplementedError, RuntimeError):
            pass
    try:
        await task
    finally:
        beat.cancel()


def start():
    request = globals().get("REQUEST") or {}
    if not isinstance(request, dict) or request.get("mode") not in ("phrase", "whole"):
        emit({"type": "error", "code": "bad_request"})
        return
    try:
        asyncio.run(asyncio.wait_for(main_async(request), 170))
    except Failure as exc:
        emit({"type": "error", "code": exc.code})
    except asyncio.TimeoutError:
        emit({"type": "error", "code": "timeout"})
    except asyncio.CancelledError:
        emit({"type": "error", "code": "cancelled"})
    except Exception:
        emit({"type": "error", "code": "server_error"})  # 예외 본문은 내보내지 않는다.


if __name__ == "__main__":
    start()
