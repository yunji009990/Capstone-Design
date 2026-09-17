"""녹음 도구의 대본·인수·캡처 흐름·manifest를 가짜 WinMM으로 검사한다. 실제 장치는 열지 않는다."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import unittest.mock
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import record_stt_samples as recorder


class FakeWinMM:
    """WinMM 래퍼와 같은 메서드만 흉내 낸다. winmm.dll을 로드하지 않는다."""

    def __init__(self, open_result=0, reset_result=0, finish=True):
        self.open_result = open_result
        self.reset_result = reset_result
        self.finish = finish  # False면 드라이버가 버퍼를 끝내지 못한 상황이다.
        self.calls = []
        self.headers = []

    def open(self, device_id, fmt):
        self.calls.append("open")
        self.fmt = fmt
        self.device_id = device_id
        return self.open_result, object()

    def prepare(self, handle, header):
        self.calls.append("prepare")
        return 0

    def add_buffer(self, handle, header):
        self.calls.append("add_buffer")
        self.headers.append(header)
        return 0

    def start(self, handle):
        self.calls.append("start")
        if self.finish:
            self._mark_done()
        return 0

    def _mark_done(self):
        for header in self.headers:
            header.dwBytesRecorded = header.dwBufferLength
            header.dwFlags |= recorder.WHDR_DONE

    def stop(self, handle):
        self.calls.append("stop")
        return 0

    def reset(self, handle):
        self.calls.append("reset")
        self._mark_done()  # waveInReset은 모든 버퍼를 done으로 돌려준다.
        return self.reset_result

    def unprepare(self, handle, header):
        self.calls.append("unprepare")
        return 0

    def close(self, handle):
        self.calls.append("close")
        return 0

    def error_text(self, code):
        return "MMRESULT=%d" % code


class FakeClock:
    """폴링 대기를 실제로 기다리지 않는다."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def fake_capture(marker):
    def capture(device_id, seconds):
        capture.calls.append((device_id, seconds))
        return bytes([marker, 0]) * (recorder.SAMPLE_RATE * seconds)
    capture.calls = []
    return capture


class ScriptTests(unittest.TestCase):
    def test_scripts_cover_comparison_cases(self):
        self.assertGreaterEqual(len(recorder.SCRIPTS), 6)
        self.assertEqual(len(set(recorder.SCRIPTS)), len(recorder.SCRIPTS))
        self.assertTrue(all(text.strip() for text in recorder.SCRIPTS))


class ArgumentTests(unittest.TestCase):
    def test_seconds_range_and_device_default(self):
        args = recorder.parse_args(["--output", "out"])
        self.assertEqual(args.seconds, 6)
        self.assertEqual(args.device_id, recorder.WAVE_MAPPER)
        self.assertEqual(recorder.parse_args(["--output", "out", "--device", "2"]).device_id, 2)
        for bad in (["--output", "out", "--seconds", "1"],
                    ["--output", "out", "--seconds", "16"],
                    ["--output", "out", "--device", "default"],
                    []):
            with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                recorder.parse_args(bad)

    def test_list_devices_needs_no_output(self):
        args = recorder.parse_args(["--list-devices"])
        self.assertTrue(args.list_devices)
        self.assertIsNone(args.output)


class ValidationTests(unittest.TestCase):
    def test_only_empty_odd_or_short_is_rejected(self):
        silence = b"\x00" * recorder.expected_bytes(2)
        self.assertEqual(recorder.validate_pcm(silence, 2), silence)  # 무음도 잡음 검증에 쓴다.
        for bad in (b"", silence[:-1], silence[:100]):
            with self.assertRaises(recorder.CaptureError):
                recorder.validate_pcm(bad, 2)


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        patcher = unittest.mock.patch.object(recorder, "time", self.clock)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.retained_before = len(recorder.RETAINED_RESOURCES)

    def test_seconds_range_checked_before_touching_device(self):
        api = FakeWinMM()
        for bad in (1, 16, 0):
            with self.assertRaises(recorder.CaptureError):
                recorder.capture_pcm(recorder.WAVE_MAPPER, bad, winmm=api)
        self.assertEqual(api.calls, [])

    def test_capture_returns_full_buffer_and_cleans_up(self):
        api = FakeWinMM()
        pcm = recorder.capture_pcm(3, 2, winmm=api)
        self.assertEqual(len(pcm), recorder.expected_bytes(2))
        self.assertEqual(api.device_id, 3)
        self.assertEqual((api.fmt.nSamplesPerSec, api.fmt.nChannels, api.fmt.wBitsPerSample),
                         (16000, 1, 16))
        self.assertEqual(api.calls[-11:], ["stop", "reset"] + ["unprepare"] * 8 + ["close"])
        self.assertEqual(len(recorder.RETAINED_RESOURCES), self.retained_before)

    def test_unfinished_buffers_fail_instead_of_short_wav(self):
        api = FakeWinMM(finish=False)
        with self.assertRaises(recorder.CaptureError) as caught:
            recorder.capture_pcm(recorder.WAVE_MAPPER, 2, winmm=api)
        self.assertIn("끝나지 않았다", str(caught.exception))
        self.assertGreaterEqual(self.clock.now, 2 + recorder.TIMEOUT_MARGIN)
        self.assertIn("close", api.calls)
        self.assertEqual(len(recorder.RETAINED_RESOURCES), self.retained_before)

    def test_bad_format_is_not_replaced_by_another_format(self):
        api = FakeWinMM(open_result=recorder.WAVERR_BADFORMAT)
        with self.assertRaises(recorder.CaptureError) as caught:
            recorder.capture_pcm(recorder.WAVE_MAPPER, 2, winmm=api)
        self.assertIn("16kHz mono PCM16", str(caught.exception))
        self.assertEqual(api.calls, ["open"])

    def test_cleanup_failure_retains_resources_and_fails(self):
        api = FakeWinMM(reset_result=8)
        with self.assertRaises(recorder.CaptureError) as caught:
            recorder.capture_pcm(recorder.WAVE_MAPPER, 2, winmm=api)
        self.assertIn("정리하지 못했다", str(caught.exception))
        self.assertEqual(len(recorder.RETAINED_RESOURCES), self.retained_before + 1)
        retained = recorder.RETAINED_RESOURCES[-1]
        self.assertEqual(len(retained["buffers"]), 8)  # 2초 × 0.25초 버퍼

    def test_first_error_survives_cleanup_error(self):
        api = FakeWinMM(reset_result=8, finish=False)
        with self.assertRaises(recorder.CaptureError) as caught:
            recorder.capture_pcm(recorder.WAVE_MAPPER, 2, winmm=api)
        self.assertIn("끝나지 않았다", str(caught.exception))
        self.assertIn("정리하지 못했다", str(caught.exception.__cause__))
        self.assertEqual(len(recorder.RETAINED_RESOURCES), self.retained_before + 1)


class SessionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.output = Path(temp.name) / "stt-mic"
        self.output.mkdir()

    def manifest(self):
        return json.loads((self.output / "manifest.json").read_text(encoding="utf-8"))

    def test_existing_folder_is_not_overwritten(self):
        (self.output / "manifest.json").write_text("{}", encoding="utf-8")
        self.assertEqual(recorder.main(["--output", str(self.output)]), 2)
        self.assertEqual((self.output / "manifest.json").read_text(encoding="utf-8"), "{}")

    def test_full_session_writes_wav_and_manifest(self):
        capture = fake_capture(1)
        answers = iter([""] * (2 * len(recorder.SCRIPTS)))
        manifest, code = recorder.run_session(self.output, 6, recorder.WAVE_MAPPER,
                                              capture=capture, prompt=lambda _: next(answers))
        self.assertEqual(code, 0)
        self.assertTrue(manifest["completed"])
        self.assertEqual(manifest["recorded_count"], len(recorder.SCRIPTS))
        capture_meta = manifest["capture"]
        self.assertEqual(capture_meta["device_id"], "WAVE_MAPPER")
        self.assertEqual((capture_meta["sample_rate"], capture_meta["channels"],
                          capture_meta["sample_width"], capture_meta["duration_seconds"]),
                         (16000, 1, 2, 6))
        sample = manifest["samples"][0]
        self.assertEqual(sample["id"], "mic_001")
        self.assertEqual(sample["audio"], "mic_001.wav")
        self.assertEqual(sample["reference"], recorder.SCRIPTS[0])
        self.assertEqual((sample["source"], sample["condition"]),
                         ("user_microphone", "actual_mic"))
        self.assertEqual((sample["bytes"], sample["duration_seconds"]), (16000 * 6 * 2, 6.0))
        self.assertEqual(len(sample["sha256"]), 64)
        self.assertTrue(sample["recorded_at"].endswith("Z"))
        self.assertEqual(self.manifest(), manifest)
        with wave.open(str(self.output / "mic_001.wav")) as handle:
            self.assertEqual((handle.getnchannels(), handle.getsampwidth(),
                              handle.getframerate(), handle.getnframes()),
                             (1, 2, 16000, 16000 * 6))
        self.assertEqual(capture.calls, [(recorder.WAVE_MAPPER, 6)] * len(recorder.SCRIPTS))

    def test_only_enter_or_q_starts_recording(self):
        capture = fake_capture(1)
        # 임의 입력 두 번은 재질문만 하고, 빈 Enter에서만 녹음이 시작된다.
        answers = iter(["y", "시작", "", "저장", "x", "", "q"])
        manifest, _ = recorder.run_session(self.output, 2, 0, capture=capture,
                                           prompt=lambda _: next(answers))
        self.assertEqual(len(capture.calls), 1)
        self.assertEqual(manifest["recorded_count"], 1)

    def test_retry_discards_buffer_before_saving(self):
        capture = fake_capture(1)
        # 시작 / 다시 녹음 / 시작 / 저장 / 다음 문장에서 끝내기
        answers = iter(["", "r", "", "", "q"])
        manifest, code = recorder.run_session(self.output, 2, 3,
                                              capture=capture, prompt=lambda _: next(answers))
        self.assertEqual(len(capture.calls), 2)
        self.assertEqual(manifest["recorded_count"], 1)
        self.assertFalse(manifest["completed"])
        self.assertEqual(code, 1)
        self.assertFalse((self.output / "mic_002.wav").exists())
        with wave.open(str(self.output / "mic_001.wav")) as handle:
            self.assertEqual(handle.getnframes(), 16000 * 2)

    def test_quit_leaves_partial_manifest(self):
        answers = iter(["", "", "q"])
        manifest, code = recorder.run_session(self.output, 6, 0,
                                              capture=fake_capture(2),
                                              prompt=lambda _: next(answers))
        self.assertEqual(code, 1)
        self.assertFalse(manifest["completed"])
        self.assertEqual(manifest["recorded_count"], 1)
        self.assertEqual(manifest["capture"]["device_id"], 0)
        self.assertEqual(self.manifest()["samples"], manifest["samples"])

    def test_interrupt_and_eof_leave_partial_manifest(self):
        for error in (KeyboardInterrupt, EOFError):
            with self.subTest(error=error):
                (self.output / "manifest.json").unlink(missing_ok=True)

                def prompt(_, error=error):
                    if prompt.count >= 2:
                        raise error
                    prompt.count += 1
                    return ""
                prompt.count = 0
                manifest, code = recorder.run_session(self.output, 6, recorder.WAVE_MAPPER,
                                                      capture=fake_capture(3), prompt=prompt)
                self.assertEqual(code, 1)
                self.assertFalse(manifest["completed"])
                self.assertEqual(self.manifest()["recorded_count"], 1)

    def test_capture_failure_on_first_clip_still_writes_manifest(self):
        def failing_capture(device_id, seconds):
            raise recorder.CaptureError("장치 실패")
        with self.assertRaises(recorder.CaptureError):
            recorder.run_session(self.output, 6, recorder.WAVE_MAPPER,
                                 capture=failing_capture, prompt=lambda _: "")
        manifest = self.manifest()
        self.assertFalse(manifest["completed"])
        self.assertEqual(manifest["recorded_count"], 0)


if __name__ == "__main__":
    unittest.main()
