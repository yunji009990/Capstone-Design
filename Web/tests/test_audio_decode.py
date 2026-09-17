"""업로드 오디오 디코딩 검사. 실제 ffmpeg 로 만든 M4A·WAV 를 그대로 통과시킨다.

무거운 음성 라이브러리(librosa·soundfile)를 설치하지 않고도 돌도록, `Web/app.py` 에서
디코딩 함수만 그대로 꺼내 쓴다. libsndfile 경로는 가짜 모듈로 대신하고, ffmpeg 경로는
**진짜 ffmpeg** 로 검사한다. ffmpeg 가 없으면 건너뛰며, 건너뛴 것을 통과로 보고하지 않는다.
"""
import ast
import contextlib
import io
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import types
import unittest
import wave

import numpy as np
from fastapi import HTTPException

ROOT = Path(__file__).resolve().parents[2]


def find_ffmpeg():
    """PATH → 설치된 imageio-ffmpeg 번들 순서로 찾는다.

    운영 Web 환경에는 PATH 의 ffmpeg 가 없고 번들 바이너리만 있다. 번들을 못 보면
    실제 디코딩 검사가 통째로 건너뛰어져 아무것도 확인하지 못한다."""
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        path = imageio_ffmpeg.get_ffmpeg_exe()
        return path if path and os.path.exists(path) else None
    except Exception:
        return None


FFMPEG = find_ffmpeg()
NEED_FFMPEG = "실제 디코딩 검사에는 PATH 의 ffmpeg 나 설치된 imageio-ffmpeg 번들이 필요합니다."
SR = 24000
DECODERS = ("_ffmpeg_path", "_temp_upload", "_decode_libsndfile", "_decode_ffmpeg", "_decode_media")
# 상수도 원본에서 함께 가져온다. 여기에 값을 다시 적으면 운영 설정과 어긋나도 모른다.
CONSTANTS = ("TARGET_SR", "FFMPEG_TIMEOUT_SEC")


def tone_with_pause(seconds=1.0, pause=1.0, rate=16000):
    """소리 → 쉼 → 소리. 쉼이 그대로 남는지 보려고 가운데를 완전히 비운다."""
    samples = []
    for index in range(int(rate * seconds)):
        samples.append(int(12000 * math.sin(2 * math.pi * 440 * index / rate)))
    samples += [0] * int(rate * pause)
    for index in range(int(rate * seconds)):
        samples.append(int(12000 * math.sin(2 * math.pi * 330 * index / rate)))
    stream = io.BytesIO()
    with wave.open(stream, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return stream.getvalue()


def encode(raw, suffix, *args):
    """진짜 ffmpeg 로 다른 형식을 만든다. 검사 입력을 흉내 내지 않기 위해서다."""
    with tempfile.TemporaryDirectory() as folder:
        source = Path(folder) / "source.wav"
        target = Path(folder) / ("converted" + suffix)
        source.write_bytes(raw)
        done = subprocess.run([FFMPEG, "-nostdin", "-hide_banner", "-loglevel", "error",
                               "-y", "-i", str(source), *args, str(target)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=120)
        if done.returncode != 0:
            raise AssertionError(done.stderr.decode("utf-8", "replace")[-400:])
        return target.read_bytes()


class DecodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.calls = []
        # 찾은 실제 바이너리를 PATH 조회 결과로 준다. 번들만 있는 환경에서도 같은
        # 디코딩 경로를 실제로 실행하기 위해서다. 번들 우선순위 자체는 별도 검사가 본다.
        self.scope = dict(os=os, re=__import__("re"), subprocess=subprocess,
                          shutil=types.SimpleNamespace(
                              which=lambda name: FFMPEG if name == "ffmpeg" else None),
                          tempfile=tempfile, np=np, HTTPException=HTTPException,
                          WORK=str(self.work), imageio_ffmpeg=None, librosa=None)
        tree = ast.parse((ROOT / "Web/app.py").read_text(encoding="utf-8"))
        selected = [node for node in tree.body
                    if (isinstance(node, ast.FunctionDef) and node.name in DECODERS)
                    or (isinstance(node, ast.Assign) and any(
                        isinstance(t, ast.Name) and t.id in CONSTANTS for t in node.targets))]
        self.assertEqual(len(selected), len(DECODERS) + len(CONSTANTS))
        exec(compile(ast.Module(body=selected, type_ignores=[]), "Web/app.py", "exec"), self.scope)

    def decode(self, raw, name):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.scope["_decode_media"](raw, name)

    def assertClean(self):
        """어느 경로로 끝나든 임시 파일을 남기지 않는다."""
        self.assertEqual(list(self.work.iterdir()), [])

    @unittest.skipUnless(FFMPEG, NEED_FFMPEG)
    def test_real_m4a_and_wav_decode_to_24k_mono_keeping_length_and_pause(self):
        source = tone_with_pause()
        for name, raw in (("recording.m4a", encode(source, ".m4a", "-c:a", "aac", "-b:a", "96k")),
                          ("recording.wav", source)):
            with self.subTest(name=name):
                y = self.decode(raw, name)
                self.assertEqual(y.ndim, 1)
                self.assertEqual(y.dtype, np.float32)
                # 3초 녹음이다. 인코더가 앞뒤로 조금 덧붙일 수 있어 0.2초까지 허용한다.
                self.assertAlmostEqual(len(y) / SR, 3.0, delta=0.2)
                loud_head = float(np.abs(y[:int(SR * 0.8)]).mean())
                quiet_middle = float(np.abs(y[int(SR * 1.3):int(SR * 1.7)]).mean())
                loud_tail = float(np.abs(y[int(SR * 2.2):int(SR * 2.8)]).mean())
                self.assertGreater(loud_head, 0.05)
                self.assertGreater(loud_tail, 0.05)
                self.assertLess(quiet_middle, loud_head / 10)   # 쉼이 살아 있다
                self.assertClean()

    @unittest.skipUnless(FFMPEG, NEED_FFMPEG)
    def test_video_container_uses_its_first_audio_track(self):
        movie = encode(tone_with_pause(), ".mp4", "-f", "lavfi", "-i", "color=c=black:s=64x64:r=5",
                       "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac")
        y = self.decode(movie, "memory.mp4")
        self.assertAlmostEqual(len(y) / SR, 3.0, delta=0.3)
        self.assertGreater(float(np.abs(y).max()), 0.05)
        self.assertClean()

    def test_readable_formats_keep_the_existing_libsndfile_path(self):
        """WAV 는 예전처럼 librosa 가 읽는다. ffmpeg 를 부르지 않는다."""
        expected = np.linspace(-0.5, 0.5, SR, dtype=np.float32)
        self.scope["librosa"] = types.SimpleNamespace(
            load=lambda path, sr, mono: (self.calls.append((path, sr, mono)) or (expected, sr)))
        self.scope["subprocess"] = types.SimpleNamespace(
            run=lambda *a, **k: self.fail("libsndfile 로 읽은 파일에 ffmpeg 를 부르면 안 됩니다"),
            TimeoutExpired=subprocess.TimeoutExpired)
        y = self.decode(tone_with_pause(), "clean.wav")
        np.testing.assert_array_equal(y, expected)
        self.assertEqual(self.calls[0][1:], (SR, True))
        self.assertTrue(self.calls[0][0].endswith(".wav"))
        self.assertClean()

    @unittest.skipUnless(FFMPEG, NEED_FFMPEG)
    def test_corrupt_or_soundless_media_is_a_client_error_without_server_details(self):
        cases = [("broken.m4a", b"\x00\x01\x02\x03" * 512),
                 ("notes.txt", "이건 오디오가 아닙니다".encode("utf-8") * 40)]
        for name, raw in cases:
            with self.subTest(name=name):
                with self.assertRaises(HTTPException) as caught:
                    self.decode(raw, name)
                self.assertEqual(caught.exception.status_code, 400)
                detail = caught.exception.detail
                self.assertIn("다른 파일", detail)
                for leak in (str(self.work), "ffmpeg", "Error", "Invalid", "moov"):
                    self.assertNotIn(leak, detail)
                self.assertClean()

    def test_empty_upload_is_rejected_before_writing_anything(self):
        with self.assertRaises(HTTPException) as caught:
            self.decode(b"", "empty.m4a")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("비어 있습니다", caught.exception.detail)
        self.assertClean()

    def test_missing_decoder_is_reported_as_unavailable_not_as_a_broken_file(self):
        self.scope["shutil"] = types.SimpleNamespace(which=lambda name: None)
        with self.assertRaises(HTTPException) as caught:
            self.decode(tone_with_pause(), "recording.m4a")
        self.assertEqual(caught.exception.status_code, 503)
        self.assertIn("준비되지 않았습니다", caught.exception.detail)
        self.assertClean()

    def test_bundled_binary_is_preferred_over_the_system_one(self):
        bundled = self.work / "ffmpeg-bundled"
        bundled.write_bytes(b"")
        self.scope["imageio_ffmpeg"] = types.SimpleNamespace(get_ffmpeg_exe=lambda: str(bundled))
        self.assertEqual(self.scope["_ffmpeg_path"](), str(bundled))
        # 번들이 깨졌으면 PATH 로 물러선다. 그 경우에도 조용히 실패하지 않는다.
        self.scope["imageio_ffmpeg"] = types.SimpleNamespace(
            get_ffmpeg_exe=lambda: (_ for _ in ()).throw(RuntimeError("no binary")))
        self.scope["shutil"] = types.SimpleNamespace(which=lambda name: "/usr/bin/ffmpeg")
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(self.scope["_ffmpeg_path"](), "/usr/bin/ffmpeg")
        bundled.unlink()

    def test_unrunnable_decoder_is_unavailable_not_a_broken_file(self):
        """경로는 찾았는데 실행이 안 되는 경우. 파일 잘못이 아니므로 400 이 아니다."""
        for error in (FileNotFoundError(2, "No such file or directory"),
                      PermissionError(13, "Permission denied"),
                      OSError(8, "Exec format error")):
            with self.subTest(error=type(error).__name__):
                def explode(*args, **kwargs):
                    raise error
                self.scope["subprocess"] = types.SimpleNamespace(
                    run=explode, TimeoutExpired=subprocess.TimeoutExpired, PIPE=subprocess.PIPE)
                with self.assertRaises(HTTPException) as caught:
                    self.decode(tone_with_pause(), "recording.m4a")
                self.assertEqual(caught.exception.status_code, 503)
                self.assertIn("준비되지 않았습니다", caught.exception.detail)
                for leak in ("Permission denied", "Exec format", "No such file", str(self.work)):
                    self.assertNotIn(leak, caught.exception.detail)
                self.assertClean()

    def test_slow_conversion_times_out_and_cleans_up(self):
        def slow(*args, **kwargs):
            self.assertIn("timeout", kwargs)
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
        self.scope["subprocess"] = types.SimpleNamespace(
            run=slow, TimeoutExpired=subprocess.TimeoutExpired, PIPE=subprocess.PIPE)
        self.scope["shutil"] = types.SimpleNamespace(which=lambda name: "/usr/bin/ffmpeg")
        with self.assertRaises(HTTPException) as caught:
            self.decode(tone_with_pause(), "long.m4a")
        self.assertEqual(caught.exception.status_code, 504)
        self.assertIn("짧은 녹음", caught.exception.detail)
        self.assertClean()

    def test_each_request_gets_its_own_temporary_file_inside_the_work_folder(self):
        first = self.scope["_temp_upload"](b"one", "a.m4a")
        second = self.scope["_temp_upload"](b"two", "a.m4a")
        self.assertNotEqual(first, second)           # 공용 _upload 이름을 쓰지 않는다
        for path in (first, second):
            self.assertEqual(Path(path).parent, self.work)
            self.assertTrue(Path(path).name.startswith("upload-"))
        self.assertTrue(first.endswith(".m4a"))
        for name in ("../../escape.wav", "voice.exe; rm -rf /", "voice.verylongextension", ""):
            with self.subTest(name=name):
                path = Path(self.scope["_temp_upload"](b"x", name))
                self.assertEqual(path.parent, self.work)
                self.assertIn(path.suffix, (".wav", ".bin"))
        for leftover in self.work.iterdir():
            leftover.unlink()


if __name__ == "__main__":
    unittest.main()
