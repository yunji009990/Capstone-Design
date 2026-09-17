"""브라우저 TTS 청취 점검용 로컬 프록시. 127.0.0.1에서만 동작한다.

원격 워커를 stdin 코드로만 실행하고 NDJSON을 그대로 중계한다.
사용자 텍스트는 명령줄에 넣지 않고 stdin JSON으로만 전달한다.
"""
import argparse
import base64
import hashlib
import importlib.util
import io
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import wave
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.abspath(__file__))
MAX_BODY = 8192
MAX_LINE = 20000
SSH_TIMEOUT = 180
MAX_TEXT = 500
MODES = ("phrase", "whole")
EVENT_TYPES = ("status", "heartbeat", "started", "audio", "boundary", "server_wav",
               "done", "error")
ERROR_CODES = ("bad_request", "bad_host", "bad_origin", "too_large", "busy", "ssh_failed",
               "incomplete", "timeout", "cancelled", "unity_active", "service_unavailable",
               "reference_failed", "tts_failed", "invalid_audio", "too_long",
               "cleanup_failed", "server_error")
RATE = 24000
MAX_SAMPLES = 45 * RATE
WAV_PACKET_BYTES = 9600
MAX_WAV_BYTES = MAX_SAMPLES * 2 + 44
MAX_WAV_CHUNKS = (MAX_WAV_BYTES + WAV_PACKET_BYTES - 1) // WAV_PACKET_BYTES
CACHE_TTL = 600  # 완성 WAV 캐시를 메모리에 두는 최대 시간(초).
AUDIO_PATH = re.compile(r"^/audio/([0-9a-f]{32})\.wav$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
FILES = {"/": ("tts_browser_check.html", "text/html; charset=utf-8"),
         "/app.js": ("tts_browser_check.js", "application/javascript; charset=utf-8")}
REMOTE_PYTHON = "~/venv/dialogue/bin/python"
# 원격에서 실행할 고정 부트스트랩. 실제 워커 코드는 stdin envelope로만 전달한다.
BOOTSTRAP = ("import json,sys\n"
             "e=json.loads(sys.stdin.readline())\n"
             "exec(compile(e['code'],'tts_browser_worker.py','exec'),"
             "{'__name__':'__main__','REQUEST':e['request']})\n")

LOCK = threading.Lock()


class BadRequest(Exception):
    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


class BadEvent(Exception):
    """워커가 보낸 완성 WAV 가 계약과 다르다."""

    def __init__(self, code):
        Exception.__init__(self, code)
        self.code = code


def is_count(value, low, high):
    """실제 개수만 허용한다. bool 은 int 이지만 개수가 아니다."""
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def is_hex64(value):
    return isinstance(value, str) and HEX64.match(value) is not None


class ServerWavCollector(object):
    """워커가 보낸 완성 WAV 조각을 검증해 메모리에만 모은다."""

    def __init__(self):
        self.seq = 0
        self.parts = bytearray()
        self.started = False

    def add(self, event):
        self.started = True
        seq, data = event.get("seq"), event.get("data")
        if not is_count(seq, 1, MAX_WAV_CHUNKS) or seq != self.seq + 1:
            raise BadEvent("invalid_audio")
        if not isinstance(data, str):
            raise BadEvent("invalid_audio")
        try:
            chunk = base64.b64decode(data, validate=True)
        except (ValueError, TypeError):
            raise BadEvent("invalid_audio")
        if not chunk or len(chunk) > WAV_PACKET_BYTES:
            raise BadEvent("invalid_audio")
        if len(self.parts) + len(chunk) > MAX_WAV_BYTES:
            raise BadEvent("invalid_audio")
        self.seq = seq
        self.parts += chunk

    def finish(self, done):
        """done 기재값·WAV 형식·PCM 해시가 모두 맞을 때만 완성 WAV 를 돌려준다."""
        if not self.seq:
            raise BadEvent("incomplete")
        if not is_count(done.get("server_wav_bytes"), 44, MAX_WAV_BYTES):
            raise BadEvent("incomplete")
        if done["server_wav_bytes"] != len(self.parts):
            raise BadEvent("incomplete")
        if not is_count(done.get("server_wav_chunks"), 1, MAX_WAV_CHUNKS):
            raise BadEvent("incomplete")
        if done["server_wav_chunks"] != self.seq:
            raise BadEvent("incomplete")
        data = bytes(self.parts)
        if not is_hex64(done.get("server_wav_sha256")):
            raise BadEvent("invalid_audio")
        if done["server_wav_sha256"] != hashlib.sha256(data).hexdigest():
            raise BadEvent("invalid_audio")
        try:
            with wave.open(io.BytesIO(data), "rb") as wav:
                shape = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(),
                         wav.getcomptype())
                count = wav.getnframes()
                frames = wav.readframes(count)
        except (wave.Error, EOFError):
            raise BadEvent("invalid_audio")
        if shape != (1, 2, RATE, "NONE") or len(frames) != count * 2:
            raise BadEvent("invalid_audio")
        if not is_count(done.get("samples"), 1, MAX_SAMPLES) or done["samples"] != count:
            raise BadEvent("invalid_audio")
        if not is_hex64(done.get("sha256")):
            raise BadEvent("invalid_audio")
        if done["sha256"] != hashlib.sha256(frames).hexdigest():
            # 같은 한 번의 합성이 아니면 원인 불명 음원이다. 공개하지 않는다.
            raise BadEvent("invalid_audio")
        return data


class WavCache(object):
    """완성 WAV 한 개만 메모리에 둔다. 여러 HTTP 요청이 같은 잠금으로 접근한다."""

    def __init__(self):
        self.lock = threading.Lock()
        self.token = None
        self.data = None
        self.timer = None

    def store(self, data):
        token = os.urandom(16).hex()
        timer = threading.Timer(CACHE_TTL, self.expire, (token,))
        timer.daemon = True
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
            self.token, self.data, self.timer = token, data, timer
        timer.start()
        return token

    def expire(self, token):
        """이전 타이머가 새 캐시를 지우지 않도록 자기 토큰일 때만 비운다."""
        with self.lock:
            if self.token == token:
                if self.timer is not None:
                    self.timer.cancel()
                self.token, self.data, self.timer = None, None, None

    def get(self, token):
        with self.lock:
            if token and self.token == token:
                return self.data
        return None

    def clear(self):
        with self.lock:
            if self.timer is not None:
                self.timer.cancel()
            self.token, self.data, self.timer = None, None, None


CACHE = WavCache()


def check_host(host, port):
    """Host 헤더가 이 루프백 서버를 가리키는지 확인한다."""
    if host not in ("127.0.0.1:%d" % port, "localhost:%d" % port):
        raise BadRequest("bad_host")


def check_origin(origin, port):
    """Origin이 없으면(curl) 허용하고, 있으면 동일 출처만 허용한다."""
    if origin is None:
        return
    if origin not in ("http://127.0.0.1:%d" % port, "http://localhost:%d" % port):
        raise BadRequest("bad_origin")


def parse_request(content_type, body):
    """본문을 검증해 {text, mode}로 정리한다."""
    if (content_type or "").split(";")[0].strip().lower() != "application/json":
        raise BadRequest("bad_request")
    if len(body) > MAX_BODY:
        raise BadRequest("too_large")
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BadRequest("bad_request")
    if not isinstance(data, dict):
        raise BadRequest("bad_request")
    text, mode = data.get("text"), data.get("mode")
    if not isinstance(text, str) or not text.strip() or len(text) > MAX_TEXT:
        raise BadRequest("bad_request")
    if mode not in MODES:
        raise BadRequest("bad_request")
    return {"text": text, "mode": mode}


def sanitize(line):
    """워커 출력 한 줄을 허용된 이벤트로만 정리한다. 아니면 None."""
    if not line or len(line) > MAX_LINE:
        return None
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict) or event.get("type") not in EVENT_TYPES:
        return None
    if event["type"] == "error" and event.get("code") not in ERROR_CODES:
        return {"type": "error", "code": "server_error"}
    return event


def ssh_command(alias):
    """고정 인수만 쓰는 SSH 명령. 사용자 텍스트는 포함하지 않는다."""
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias,
            REMOTE_PYTHON + " -u -c " + shlex.quote(BOOTSTRAP)]


def envelope(code, request):
    """워커에 보낼 한 줄 JSON."""
    return json.dumps({"code": code, "request": request}, ensure_ascii=False) + "\n"


def worker_code():
    with open(os.path.join(ROOT, "tts_browser_worker.py"), "r", encoding="utf-8") as handle:
        return handle.read()


def load_baseline(path):
    """비교용 기존 WAV 한 개를 미리 읽어 둔다. 경로는 명령줄로만 받고 HTTP 로는 받지 않는다."""
    if path is None:
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read(MAX_WAV_BYTES + 1)
        if len(data) > MAX_WAV_BYTES:
            raise ValueError("too_large")
        with wave.open(io.BytesIO(data), "rb") as wav:
            shape = (wav.getnchannels(), wav.getsampwidth(), wav.getframerate(),
                     wav.getcomptype())
            count = wav.getnframes()
            frames = wav.readframes(count)
        if shape != (1, 2, RATE, "NONE"):
            raise ValueError("shape")
        if not is_count(count, 1, MAX_SAMPLES) or len(frames) != count * 2:
            raise ValueError("frames")
    except (OSError, ValueError, EOFError, wave.Error):
        raise ValueError("비교 WAV를 읽을 수 없습니다.")
    return data  # 변환 없이 파일 바이트 그대로 내보낸다.


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ttscheck"

    def log_message(self, *args):
        pass  # 본문·텍스트 유출 방지를 위해 접근 로그를 남기지 않는다.

    def _send(self, code, body, ctype):
        self.close_connection = True
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _fail(self, status, code):
        self._send(status, json.dumps({"type": "error", "code": code}).encode(),
                   "application/json; charset=utf-8")

    def _event(self, event):
        self.wfile.write((json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8"))
        self.wfile.flush()

    def do_GET(self):
        port = self.server.server_address[1]
        try:
            check_host(self.headers.get("Host"), port)
        except BadRequest as exc:
            return self._fail(400, exc.code)
        if self.path == "/health":
            body = json.dumps({"ready": True, "busy": LOCK.locked()}).encode()
            return self._send(200, body, "application/json; charset=utf-8")
        if self.path == "/baseline.wav":
            # 명령줄로 지정한 기존 파일 한 개만 내보낸다. 새 합성은 하지 않는다.
            data = self.server.baseline_wav
            if data is None:
                return self._fail(404, "bad_request")
            return self._send(200, data, "audio/wav")
        match = AUDIO_PATH.match(self.path)
        if match:
            # 임의 파일 경로가 아니라 방금 만든 캐시 한 개만 내보낸다.
            data = CACHE.get(match.group(1))
            if data is None:
                return self._fail(404, "bad_request")
            return self._send(200, data, "audio/wav")  # 짧은 WAV 라 Range 없이 전체를 준다.
        entry = FILES.get(self.path)
        if not entry:
            return self._fail(404, "bad_request")
        with open(os.path.join(ROOT, entry[0]), "rb") as handle:
            self._send(200, handle.read(), entry[1])

    def do_POST(self):
        port = self.server.server_address[1]
        try:
            check_host(self.headers.get("Host"), port)
            check_origin(self.headers.get("Origin"), port)
            if self.path != "/api/generate":
                raise BadRequest("bad_request")
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                raise BadRequest("too_large")
            request = parse_request(self.headers.get("Content-Type"), self.rfile.read(length))
        except (BadRequest, ValueError) as exc:
            return self._fail(400, getattr(exc, "code", "bad_request"))
        if not LOCK.acquire(False):
            return self._fail(409, "busy")
        try:
            self._stream(request)
        finally:
            LOCK.release()

    def _stream(self, request):
        self.close_connection = True
        CACHE.clear()  # 새 생성을 시작하면 이전 완성 WAV 는 즉시 버린다.
        try:
            proc = subprocess.Popen(ssh_command(self.server.ssh_alias), shell=False, cwd=ROOT,
                                    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            return self._fail(502, "ssh_failed")
        expired = []
        timer = threading.Timer(SSH_TIMEOUT, lambda: (expired.append(True), proc.terminate()))
        timer.start()
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        done = False
        broken = False
        collector = ServerWavCollector()
        pending_done = None
        try:
            self._event({"type": "status", "stage": "preparing"})
            proc.stdin.write(envelope(worker_code(), request).encode("utf-8"))
            proc.stdin.flush()
            proc.stdin.close()
            for raw in proc.stdout:
                text = raw.decode("utf-8", "replace").strip()
                if not text:
                    continue
                event = sanitize(text)
                if event is None:
                    # 해석할 수 없는 줄을 조용히 넘기면 깨진 음원이 정상처럼 통과한다.
                    self._event({"type": "error", "code": "server_error"})
                    broken = True
                    break
                kind = event["type"]
                if done and kind != "heartbeat":
                    # done/error 뒤에 다시 이벤트가 오면 같은 한 번의 합성이 아니다.
                    self._event({"type": "error", "code": "invalid_audio"})
                    broken = True
                    break
                if kind == "server_wav":
                    # 조각은 브라우저에 전달하지 않는다. 검증해 메모리에만 모은다.
                    try:
                        collector.add(event)
                    except BadEvent as exc:
                        self._event({"type": "error", "code": exc.code})
                        broken = True
                        break
                    continue
                if kind in ("audio", "boundary") and collector.started:
                    # 완성 WAV 뒤에 다시 오디오가 오면 같은 한 번의 합성이 아니다.
                    self._event({"type": "error", "code": "invalid_audio"})
                    broken = True
                    break
                if kind == "done":
                    # done 은 원격 종료 코드 0 을 확인한 뒤 URL 과 함께 내보낸다.
                    pending_done = event
                    done = True
                    continue
                self._event(event)
                if kind == "error":
                    done = True
            if not broken:
                try:
                    status = proc.wait(10)
                except subprocess.TimeoutExpired:
                    status = None
                if expired:
                    self._event({"type": "error", "code": "timeout"})
                elif status != 0:
                    # 종료 코드가 0이 아니면 done 을 받았어도 성공으로 세지 않는다.
                    self._event({"type": "error", "code": "ssh_failed"})
                elif pending_done is not None:
                    try:
                        data = collector.finish(pending_done)
                    except BadEvent as exc:
                        self._event({"type": "error", "code": exc.code})
                    else:
                        token = CACHE.store(data)
                        final = dict(pending_done)
                        final["server_wav_url"] = "/audio/%s.wav" % token
                        try:
                            self._event(final)
                        except Exception:
                            # URL 을 알리지 못했으면 자기 캐시를 바로 폐기한다.
                            CACHE.expire(token)
                            raise
                elif not done:
                    self._event({"type": "error", "code": "incomplete"})
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass  # 브라우저가 끊었다. 아래에서 원격 프로세스를 정리한다.
        finally:
            timer.cancel()
            if proc.stdin and not proc.stdin.closed:
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(10)
                except subprocess.TimeoutExpired:
                    proc.kill()  # 로컬 ssh 프로세스만 정리한다. 원격 GPU 프로세스는 건드리지 않는다.
            proc.stdout.close()


def run_checks():
    path = os.path.join(ROOT, "tests", "test_tts_browser_check.py")
    spec = importlib.util.spec_from_file_location("test_tts_browser_check", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    import unittest
    suite = unittest.defaultTestLoader.loadTestsFromModule(module)
    return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1


def main(argv=None):
    parser = argparse.ArgumentParser(description="TTS 청취 점검 로컬 프록시")
    parser.add_argument("--port", type=int, default=8772)
    parser.add_argument("--ssh", default="raon")
    parser.add_argument("--baseline-wav", default=None, dest="baseline_wav")
    parser.add_argument("--open", action="store_true", dest="open_browser")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.check:
        return run_checks()
    try:
        baseline = load_baseline(args.baseline_wav)
    except ValueError:
        parser.error("비교 WAV를 읽을 수 없습니다.")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.ssh_alias = args.ssh
    server.baseline_wav = baseline
    url = "http://127.0.0.1:%d/" % args.port
    print("local check server: " + url)
    if args.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
