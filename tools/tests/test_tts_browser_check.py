"""로컬 프록시의 입력·계약 검사. 실제 모델·SSH 없이 순수 함수만 확인한다."""
import asyncio
import base64
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tts_browser_check as proxy  # noqa: E402
import tts_browser_worker as worker  # noqa: E402


class HeaderTest(unittest.TestCase):
    def test_host(self):
        proxy.check_host("127.0.0.1:8772", 8772)
        proxy.check_host("localhost:8772", 8772)
        for bad in ("example.com", "127.0.0.1:9000", None):
            self.assertRaises(proxy.BadRequest, proxy.check_host, bad, 8772)

    def test_origin(self):
        proxy.check_origin(None, 8772)  # curl 등 Origin 없는 요청은 허용
        proxy.check_origin("http://127.0.0.1:8772", 8772)
        for bad in ("null", "http://evil.test", "https://127.0.0.1:8772", "http://127.0.0.1:1"):
            self.assertRaises(proxy.BadRequest, proxy.check_origin, bad, 8772)


class BodyTest(unittest.TestCase):
    def body(self, data):
        return json.dumps(data).encode("utf-8")

    def test_ok(self):
        out = proxy.parse_request("application/json", self.body({"text": "안녕", "mode": "whole"}))
        self.assertEqual(out, {"text": "안녕", "mode": "whole"})

    def test_content_type(self):
        for ctype in ("text/plain", "", None):
            with self.assertRaises(proxy.BadRequest):
                proxy.parse_request(ctype, self.body({"text": "a", "mode": "phrase"}))
        proxy.parse_request("application/json; charset=utf-8",
                            self.body({"text": "a", "mode": "phrase"}))

    def test_too_large(self):
        raw = self.body({"text": "가" * 400, "mode": "phrase"}) + b" " * proxy.MAX_BODY
        with self.assertRaises(proxy.BadRequest) as ctx:
            proxy.parse_request("application/json", raw)
        self.assertEqual(ctx.exception.code, "too_large")

    def test_bad_values(self):
        cases = [{"text": "", "mode": "phrase"}, {"text": "  ", "mode": "phrase"},
                 {"text": "가" * 501, "mode": "phrase"}, {"text": 3, "mode": "phrase"},
                 {"text": "a", "mode": "stream"}, {"text": "a"}, {"mode": "phrase"}, []]
        for case in cases:
            with self.assertRaises(proxy.BadRequest):
                proxy.parse_request("application/json", self.body(case))
        with self.assertRaises(proxy.BadRequest):
            proxy.parse_request("application/json", b"{not json")


class CommandTest(unittest.TestCase):
    def test_text_not_in_command(self):
        secret = "비밀문장; rm -rf ~"
        command = proxy.ssh_command("raon")
        self.assertEqual(command[:7], ["ssh", "-T", "-o", "BatchMode=yes",
                                       "-o", "ConnectTimeout=8", "raon"])
        self.assertEqual(len(command), 8)
        self.assertNotIn(secret, " ".join(command))
        self.assertIn("-u -c ", command[7])

    def test_envelope_is_one_line(self):
        line = proxy.envelope("print(REQUEST)\n", {"text": "줄\n바꿈", "mode": "phrase"})
        self.assertEqual(line.count("\n"), 1)
        data = json.loads(line)
        self.assertEqual(data["request"]["text"], "줄\n바꿈")
        self.assertEqual(data["code"], "print(REQUEST)\n")


class EventTest(unittest.TestCase):
    def test_sanitize(self):
        self.assertIsNone(proxy.sanitize(""))
        self.assertIsNone(proxy.sanitize("not json"))
        self.assertIsNone(proxy.sanitize(json.dumps({"type": "debug", "raw": "x"})))
        self.assertIsNone(proxy.sanitize("x" * (proxy.MAX_LINE + 1)))
        self.assertEqual(proxy.sanitize(json.dumps({"type": "error", "code": "Traceback..."})),
                         {"type": "error", "code": "server_error"})
        keep = {"type": "audio", "pcm": "AAA=", "samples": 2, "sample_rate": 24000,
                "channels": 1, "format": "pcm_s16le", "seq": 1}
        self.assertEqual(proxy.sanitize(json.dumps(keep)), keep)

    def test_error_codes_are_fixed(self):
        for code in ("unity_active", "tts_failed", "invalid_audio", "too_long",
                     "reference_failed", "cleanup_failed", "timeout", "busy"):
            self.assertIn(code, proxy.ERROR_CODES)


class StubPhraseBuffer(object):
    """실제 모듈을 쓸 수 없을 때만 쓰는 최소 문장 분리기."""

    def push(self, text, final=False):
        return [part + "." for part in text.split(".") if part.strip()]


def stub_ends_sentence(phrase):
    return phrase.strip().endswith((".", "!", "?"))


def phrase_module():
    """문장 분리는 생산 코드를 검사한다. import 실패를 모의 구현으로 숨기지 않는다."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    server = os.path.join(root, "Server")
    if os.path.isdir(server) and server not in sys.path:
        sys.path.insert(0, server)
    import realtime_tts
    return realtime_tts


def pcm_chunk(value, samples):
    """모델 호출 없이 만든 조각. (base64 문자열, 샘플 수)"""
    return base64.b64encode(bytes([value]) * (samples * 2)).decode(), samples


class FakeStream(object):
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.closed = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.chunks:
            raise StopAsyncIteration
        return self.chunks.pop(0)

    async def aclose(self):
        self.closed = True


class BlockingStream(FakeStream):
    async def __anext__(self):
        await asyncio.sleep(5)


class FakeVoice(object):
    """모델을 호출하지 않는 가짜 bound voice."""

    stream_class = FakeStream

    def __init__(self, per_phrase):
        self.per_phrase = list(per_phrase)
        self.streams = []
        self.released = False

    def stream(self, phrase):
        chunks = self.per_phrase.pop(0) if self.per_phrase else []
        made = self.stream_class(chunks)
        self.streams.append(made)
        return made

    async def release(self):
        self.released = True


class BlockingVoice(FakeVoice):
    stream_class = BlockingStream


class WorkerSynthesizeTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.original_emit = worker.emit
        worker.emit = self.events.append

    def tearDown(self):
        worker.emit = self.original_emit

    def test_gap_is_passed_through_and_streams_close(self):
        module = phrase_module()
        request = {"text": "첫 문장입니다. 두 번째 문장입니다.", "mode": "phrase"}
        phrases = [p for p in module.PhraseBuffer().push(request["text"], final=True) if p.strip()]
        self.assertEqual(len(phrases), 2)
        self.assertTrue(module.ends_sentence(phrases[0]))
        gap_samples = 240
        gap = base64.b64encode(b"\x00" * (gap_samples * 2)).decode()  # 서버처럼 이미 base64 문자열
        voice = FakeVoice([[pcm_chunk(1, 120)], [pcm_chunk(2, 160)]])
        stats = asyncio.run(worker.synthesize(request, voice, module, gap, gap_samples))
        audio = [e for e in self.events if e["type"] == "audio"]
        self.assertEqual([e["samples"] for e in audio], [120, gap_samples, 160])
        self.assertEqual(audio[1]["pcm"], gap)  # 다시 인코딩하지 않고 그대로 보낸다
        self.assertEqual(stats.samples, 120 + gap_samples + 160)
        self.assertEqual(len(voice.streams), 2)
        self.assertTrue(all(item.closed for item in voice.streams))

    def test_phrase_without_audio_fails(self):
        module = SimpleNamespace(PhraseBuffer=StubPhraseBuffer, ends_sentence=stub_ends_sentence)
        voice = FakeVoice([[]])
        with self.assertRaises(worker.Failure) as ctx:
            asyncio.run(worker.synthesize({"text": "한 문장입니다.", "mode": "whole"},
                                          voice, module, "AAAA", 2))
        self.assertEqual(ctx.exception.code, "tts_failed")
        self.assertTrue(voice.streams[0].closed)

    def test_cancel_closes_stream(self):
        module = SimpleNamespace(PhraseBuffer=StubPhraseBuffer, ends_sentence=stub_ends_sentence)
        voice = BlockingVoice([])
        request = {"text": "한 문장입니다.", "mode": "whole"}

        async def scenario():
            task = asyncio.ensure_future(
                worker.synthesize(request, voice, module, "AAAA", 2))
            await asyncio.sleep(0.05)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        self.assertEqual(len(voice.streams), 1)
        self.assertTrue(voice.streams[0].closed)


class WorkerCleanupTest(unittest.TestCase):
    def test_failed_step_does_not_block_the_rest(self):
        calls = []

        class FakeHttp(object):
            async def delete(self, path):
                calls.append("delete")
                raise RuntimeError("삭제 실패")

            async def aclose(self):
                calls.append("aclose")

        class FakeTTS(object):
            async def close(self):
                calls.append("close")

        voice = FakeVoice([])
        with self.assertRaises(worker.Failure) as ctx:
            asyncio.run(worker.cleanup(FakeHttp(), FakeTTS(), voice, "ref-1"))
        self.assertEqual(ctx.exception.code, "cleanup_failed")
        self.assertTrue(voice.released)
        self.assertEqual(calls, ["delete", "close", "aclose"])


def server_wav_events(chunks):
    """모델 없이 워커 경로를 그대로 돌려 (완성 WAV, server_wav 이벤트, done) 을 만든다."""
    module = SimpleNamespace(PhraseBuffer=StubPhraseBuffer, ends_sentence=stub_ends_sentence)
    voice = FakeVoice([list(chunks)])
    original = worker.emit
    worker.emit = [].append
    try:
        stats = asyncio.run(worker.synthesize({"text": "한 문장입니다.", "mode": "whole"},
                                              voice, module, "AAAA", 2))
        data = worker.build_wav(bytes(stats.wav))
        events = []
        worker.emit = events.append
        count = worker.emit_server_wav(data)
    finally:
        worker.emit = original
    done = {"type": "done", "samples": stats.samples, "chunks": stats.chunks,
            "sha256": stats.digest.hexdigest(), "server_wav_bytes": len(data),
            "server_wav_chunks": count,
            "server_wav_sha256": hashlib.sha256(data).hexdigest()}
    return data, events, done


class ServerWavTest(unittest.TestCase):
    """A 경로 완성 WAV 가 B 경로와 같은 한 번의 합성인지 검증기로 확인한다."""

    def collect(self, events, done):
        collector = proxy.ServerWavCollector()
        for event in events:
            collector.add(event)
        return collector.finish(done)

    def test_same_synthesis_passes_and_keeps_pcm(self):
        chunks = [pcm_chunk(1, 4800), pcm_chunk(2, 4800)]
        data, events, done = server_wav_events(chunks)
        self.assertEqual(self.collect(events, done), data)
        pcm = b"".join(base64.b64decode(item[0]) for item in chunks)
        self.assertEqual(data[44:], pcm)  # 브라우저가 합칠 PCM 과 완전히 같다
        self.assertEqual(done["sha256"], hashlib.sha256(pcm).hexdigest())

    def test_last_chunk_may_be_short(self):
        data, events, done = server_wav_events([pcm_chunk(1, 4800), pcm_chunk(2, 200)])
        sizes = [len(base64.b64decode(event["data"])) for event in events]
        self.assertEqual(sizes, [9600, len(data) - 9600])
        self.assertLess(sizes[-1], 9600)
        self.assertEqual(self.collect(events, done), data)

    def test_corrupted_chunk_fails(self):
        _data, events, done = server_wav_events([pcm_chunk(1, 4800)])
        raw = bytearray(base64.b64decode(events[0]["data"]))
        raw[100] ^= 0xFF
        events[0] = dict(events[0], data=base64.b64encode(bytes(raw)).decode())
        with self.assertRaises(proxy.BadEvent) as ctx:
            self.collect(events, done)
        self.assertEqual(ctx.exception.code, "invalid_audio")

    def test_missing_chunk_is_incomplete(self):
        _data, events, done = server_wav_events([pcm_chunk(1, 4800)])
        self.assertGreater(len(events), 1)
        collector = proxy.ServerWavCollector()
        collector.add(events[0])
        with self.assertRaises(proxy.BadEvent) as ctx:
            collector.finish(done)
        self.assertEqual(ctx.exception.code, "incomplete")

    def test_duplicate_and_bad_seq_fail(self):
        _data, events, done = server_wav_events([pcm_chunk(1, 4800)])
        collector = proxy.ServerWavCollector()
        collector.add(events[0])
        with self.assertRaises(proxy.BadEvent):
            collector.add(events[0])  # 같은 seq 를 두 번 받지 않는다


class WavCacheTest(unittest.TestCase):
    def setUp(self):
        self.cache = proxy.WavCache()
        self.addCleanup(self.cache.clear)

    def test_url_token_is_32_hex(self):
        token = self.cache.store(b"RIFF")
        self.assertRegex(token, r"^[0-9a-f]{32}$")
        self.assertIsNotNone(proxy.AUDIO_PATH.match("/audio/%s.wav" % token))
        self.assertEqual(self.cache.get(token), b"RIFF")
        self.assertIsNone(self.cache.get("0" * 32))
        self.assertIsNone(self.cache.get(None))

    def test_new_cache_replaces_old_and_survives_old_timer(self):
        first = self.cache.store(b"first")
        second = self.cache.store(b"second")
        self.assertIsNone(self.cache.get(first))  # 다음 생성이 이전 캐시를 즉시 지운다
        self.cache.expire(first)  # 이전 타이머가 늦게 울려도 새 캐시는 남는다
        self.assertEqual(self.cache.get(second), b"second")

    def test_expire_releases_memory(self):
        token = self.cache.store(b"data")
        self.cache.expire(token)
        self.assertIsNone(self.cache.get(token))
        self.assertIsNone(self.cache.data)

    def test_server_wav_is_a_known_event(self):
        self.assertIn("server_wav", proxy.EVENT_TYPES)
        keep = {"type": "server_wav", "seq": 1, "data": "AAA="}
        self.assertEqual(proxy.sanitize(json.dumps(keep)), keep)


class BaselineWavTest(unittest.TestCase):
    """과거 파일은 재합성·재직렬화하지 않고 같은 바이트를 읽는다."""

    def load(self, data):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "baseline.wav")
            with open(path, "wb") as handle:
                handle.write(data)
            return proxy.load_baseline(path)

    def test_original_bytes_are_preserved(self):
        raw = worker.build_wav(bytes(range(128)) * 4)
        self.assertEqual(self.load(raw), raw)

    def test_unconfigured_has_no_baseline(self):
        self.assertIsNone(proxy.load_baseline(None))

    def test_missing_or_invalid_file_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                proxy.load_baseline(os.path.join(directory, "missing.wav"))
        for data in (b"not a wav", b"x" * (proxy.MAX_WAV_BYTES + 1), worker.build_wav(b"")):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                self.load(data)

    def test_wrong_format_or_truncated_audio_fails(self):
        raw = worker.build_wav(bytes(range(128)) * 4)
        wrong_rate = bytearray(raw)
        wrong_rate[24:28] = (16000).to_bytes(4, "little")
        for data in (raw[:-2], bytes(wrong_rate)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                self.load(data)


class StreamPublicationTest(unittest.TestCase):
    """완료 이벤트나 프로세스 종료가 잘못되면 청취 URL을 공개하지 않는다."""

    def relay(self, events, exit_code=0, disconnect=False):
        cache = proxy.WavCache()
        self.addCleanup(cache.clear)
        state = {"waited": False}

        def wait(_timeout):
            state["waited"] = True
            return exit_code

        proc = SimpleNamespace(
            stdin=io.BytesIO(),
            stdout=io.BytesIO("".join(json.dumps(e) + "\n" for e in events).encode()),
            wait=wait, poll=lambda: exit_code, terminate=lambda: None, kill=lambda: None)
        handler = proxy.Handler.__new__(proxy.Handler)
        handler.server = SimpleNamespace(ssh_alias="unused-test-alias")
        handler.send_response = lambda *_: None
        handler.send_header = lambda *_: None
        handler.end_headers = lambda: None
        sent = []

        def send(event):
            if event["type"] == "done":
                self.assertTrue(state["waited"])
                if disconnect:
                    raise BrokenPipeError()
            sent.append(event)

        handler._event = send
        with mock.patch.object(proxy, "CACHE", cache), \
                mock.patch.object(proxy.subprocess, "Popen", return_value=proc):
            handler._stream({"text": "검사용 문장", "mode": "whole"})
        return sent, cache

    def test_success_waits_for_exit_and_keeps_only_complete_wav(self):
        data, events, done = server_wav_events([pcm_chunk(1, 100)])
        sent, cache = self.relay(events + [done, {"type": "heartbeat"}])
        self.assertFalse(any(e["type"] == "server_wav" for e in sent))
        final = sent[-1]
        self.assertEqual(final["type"], "done")
        token = proxy.AUDIO_PATH.match(final["server_wav_url"]).group(1)
        self.assertEqual(cache.get(token), data)

    def test_failed_remote_exit_does_not_publish_wav(self):
        _data, events, done = server_wav_events([pcm_chunk(1, 100)])
        sent, cache = self.relay(events + [done], exit_code=1)
        self.assertFalse(any(e["type"] == "done" for e in sent))
        self.assertEqual(sent[-1], {"type": "error", "code": "ssh_failed"})
        self.assertIsNone(cache.data)

    def test_events_after_terminal_do_not_publish_wav(self):
        _data, events, done = server_wav_events([pcm_chunk(1, 100)])
        error = {"type": "error", "code": "tts_failed"}
        for sequence in (events + [done, done], events + [done, error], [error] + events + [done]):
            with self.subTest(sequence=[e["type"] for e in sequence]):
                sent, cache = self.relay(sequence)
                self.assertFalse(any(e["type"] == "done" for e in sent))
                self.assertIsNone(cache.data)

    def test_disconnected_final_write_discards_own_wav(self):
        _data, events, done = server_wav_events([pcm_chunk(1, 100)])
        _sent, cache = self.relay(events + [done], disconnect=True)
        self.assertIsNone(cache.data)


if __name__ == "__main__":
    unittest.main()
