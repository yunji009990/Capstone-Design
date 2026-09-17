"""Whisper 운영 STT 전환에서 실제로 깨질 수 있는 계약만 검사한다.

faster-whisper 모델은 가짜 모듈로 대신한다. GPU·실제 모델 없이 돌아가며, 여기의
통과는 **전사 품질이 아니라 배선·직렬화·메타데이터·설정 계약**만 보장한다.
실제 인식 정확도는 [STT 모델 비교](../../docs/STT_모델_비교.md)와 실제 마이크 검사가 다룬다.
"""
import asyncio
import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import types
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dialogue_server
import realtime_audio
from dialogue_server import Settings, create_app
from realtime_audio import Observation, WhisperFrontend, create_frontend, frontend_name


def pcm(seconds=1.0, rate=16000):
    """일정한 파형의 PCM16. 내용이 아니라 길이·레이트 계약을 보려고 쓴다."""
    values = (np.sin(np.arange(int(rate * seconds)) * .05) * 8000).astype("<i2")
    return values.tobytes()


class FakeSegment:
    def __init__(self, text, **quality):
        self.text = text
        # 실제 세그먼트처럼 있는 속성만 붙인다. 없는 지표는 아예 속성이 없다.
        for name, value in quality.items():
            setattr(self, name, value)


class FakeModel:
    """호출 인자·스레드·동시 실행을 기록하는 가짜 CTranslate2 모델."""

    def __init__(self):
        self.init = {}
        self.texts = ["안녕하세요. ", "오늘은 어땠어요?"]
        self.calls, self.events, self.threads = [], [], []
        self.hold = None
        self.fail = None
        self.active, self.max_active = 0, 0
        self.lock = threading.Lock()

    def transcribe(self, audio, **kwargs):
        index = len(self.calls)
        self.calls.append({"audio": np.array(audio), "kwargs": kwargs})
        self.threads.append(threading.current_thread().name)
        if self.fail is not None:
            raise self.fail
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        self.events.append(("start", index))

        def segments():
            try:
                if self.hold is not None and index == 0:
                    self.hold.wait(5)
                for text in self.texts:
                    yield text if isinstance(text, FakeSegment) else FakeSegment(text)
            finally:
                with self.lock:
                    self.active -= 1
                self.threads.append(threading.current_thread().name)
                self.events.append(("end", index))

        return segments(), object()


def fake_whisper(model, decode=None):
    """faster_whisper 와 faster_whisper.audio 를 sys.modules 에 끼워 넣는다."""
    def construct(path, **kwargs):
        model.init = dict(kwargs, path=path)
        return model

    def refuse(*args, **kwargs):
        raise AssertionError("16 kHz 입력에는 디코더를 부르면 안 된다")

    module = types.ModuleType("faster_whisper")
    module.WhisperModel = construct
    audio = types.ModuleType("faster_whisper.audio")
    audio.decode_audio = decode or refuse
    module.audio = audio
    return mock.patch.dict(sys.modules, {"faster_whisper": module,
                                         "faster_whisper.audio": audio})


class Fixture(unittest.TestCase):
    """임시 모델 폴더와 가짜 모델. 실제 가중치는 쓰지 않는다."""

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = self.model_dir()
        self.model = FakeModel()

    def model_dir(self, files=("model.bin", "config.json")):
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in files:
            (root / name).write_bytes(b"not a real model")
        return root


class LoadingTests(Fixture):
    def test_local_model_directory_is_required(self):
        incomplete = self.model_dir(files=("config.json",))
        with fake_whisper(self.model):
            with self.assertRaises(FileNotFoundError):
                WhisperFrontend(incomplete)
            with self.assertRaises(FileNotFoundError):
                WhisperFrontend(self.root / "없는폴더")
        self.assertEqual(self.model.init, {})

    def test_local_files_only_and_pinned_decoding_settings(self):
        with fake_whisper(self.model):
            frontend = WhisperFrontend(self.root)
        self.addCleanup(frontend.close)
        self.assertEqual(self.model.init["path"], str(self.root))
        self.assertEqual(self.model.init["device"], "cuda")
        self.assertEqual(self.model.init["compute_type"], "float16")
        self.assertIs(self.model.init["local_files_only"], True)
        options = self.model.calls[0]["kwargs"]
        self.assertEqual(options["language"], "ko")
        self.assertEqual(options["beam_size"], 5)
        self.assertEqual(options["temperature"], 0)
        self.assertIs(options["condition_on_previous_text"], False)
        self.assertIs(options["vad_filter"], False)
        self.assertIs(options["without_timestamps"], True)

    def test_invalid_device_or_compute_type_is_rejected_without_loading(self):
        with fake_whisper(self.model):
            with self.assertRaises(ValueError):
                WhisperFrontend(self.root, device="gpu")
            with self.assertRaises(ValueError):
                WhisperFrontend(self.root, compute_type="fp16")
        self.assertEqual(self.model.init, {})

    def test_warmup_runs_on_the_worker_thread_and_is_never_exposed(self):
        with fake_whisper(self.model):
            frontend = WhisperFrontend(self.root)
        self.addCleanup(frontend.close)
        # 예열은 무음 0.5초이고, 그 결과 텍스트는 어디에도 남지 않는다.
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(len(self.model.calls[0]["audio"]), 8000)
        self.assertTrue(np.all(self.model.calls[0]["audio"] == 0))
        self.assertTrue(self.model.threads[0].startswith("speech"))
        observation = asyncio.run(frontend.transcribe(pcm()))
        self.assertEqual(observation.text, "안녕하세요. 오늘은 어땠어요?")

    def test_warmup_failure_is_raised_instead_of_a_half_ready_frontend(self):
        self.model.fail = RuntimeError("CUDA driver version is insufficient")
        with fake_whisper(self.model):
            with self.assertRaises(RuntimeError):
                WhisperFrontend(self.root)


class DecodeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ("model.bin", "config.json"):
            (self.root / name).write_bytes(b"not a real model")
        self.model = FakeModel()
        self.decoded = []

    def frontend(self, decode=None):
        # 리샘플 경로는 전사할 때 디코더를 가져오므로 검사 내내 가짜 모듈을 둔다.
        self.stack.enter_context(fake_whisper(self.model, decode))
        frontend = WhisperFrontend(self.root, warmup=False)
        self.addCleanup(frontend.close)
        return frontend

    def resampler(self):
        """PyAV 대신 길이만 16 kHz 로 맞춰 준다. 넘어온 WAV 를 그대로 확인한다."""
        def decode_audio(buffer, sampling_rate=16000, **kwargs):
            with wave.open(io.BytesIO(buffer.getvalue()), "rb") as wav:
                self.decoded.append({"rate": wav.getframerate(), "frames": wav.getnframes(),
                                     "channels": wav.getnchannels(), "width": wav.getsampwidth(),
                                     "pcm": wav.readframes(wav.getnframes()),
                                     "target": sampling_rate})
            entry = self.decoded[-1]
            return np.linspace(-.5, .5, int(entry["frames"] * sampling_rate / entry["rate"]),
                               dtype=np.float32)
        return decode_audio

    async def test_16k_input_is_passed_straight_through(self):
        frontend = self.frontend()
        observation = await frontend.transcribe(pcm(seconds=2), 16000)
        audio = self.model.calls[0]["audio"]
        self.assertEqual(audio.dtype, np.float32)
        self.assertEqual(len(audio), 32000)
        self.assertLessEqual(float(np.abs(audio).max()), 1.0)
        self.assertEqual(observation.prosody["duration_sec"], 2.0)

    async def test_non_16k_reference_is_really_resampled(self):
        frontend = self.frontend(self.resampler())
        raw = pcm(seconds=4, rate=48000)
        observation = await frontend.transcribe(raw, 48000)
        # 원본 레이트 그대로 디코더에 넘기고, 목표만 16 kHz 로 지정한다.
        self.assertEqual(self.decoded[0]["rate"], 48000)
        self.assertEqual(self.decoded[0]["channels"], 1)
        self.assertEqual(self.decoded[0]["width"], 2)
        self.assertEqual(self.decoded[0]["frames"], 192000)
        self.assertEqual(self.decoded[0]["pcm"], raw)
        self.assertEqual(self.decoded[0]["target"], 16000)
        # 모델 입력은 16 kHz 길이로 줄고, 길이 지표는 원 PCM 기준으로 남는다.
        self.assertEqual(len(self.model.calls[0]["audio"]), 64000)
        self.assertEqual(observation.prosody["duration_sec"], 4.0)

    async def test_reference_rates_are_resampled_and_measured_on_the_original(self):
        frontend = self.frontend(self.resampler())
        for rate in (24000, 44100, 48000, 96000):
            with self.subTest(rate=rate):
                self.decoded.clear()
                del self.model.calls[:]
                observation = await frontend.transcribe(pcm(seconds=3, rate=rate), rate)
                self.assertEqual(self.decoded[0]["rate"], rate)
                self.assertEqual(len(self.model.calls[0]["audio"]), 48000)
                self.assertEqual(observation.prosody["duration_sec"], 3.0)

    async def test_sample_rate_must_be_an_integer(self):
        frontend = self.frontend(self.resampler())
        for rate in ("16000", 16000.0, True, None):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                await frontend.transcribe(pcm(), rate)
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.decoded, [])

    async def test_voice_metadata_is_only_event_language_and_prosody(self):
        frontend = self.frontend()
        observation = await frontend.transcribe(pcm(), 16000)
        self.assertIsInstance(observation, Observation)
        self.assertEqual(observation.audio_event, "unknown")
        # 요청에서 한국어를 강제했다. 모델의 언어 판정 결과가 아니다.
        self.assertEqual(observation.language, "ko")
        self.assertEqual(set(observation.metadata()), {"audio_event", "language", "prosody"})
        self.assertFalse(hasattr(observation, "emotion"))

    async def test_segment_quality_is_kept_internally_and_out_of_metadata(self):
        frontend = self.frontend()
        self.model.texts = [FakeSegment(" 네, ", avg_logprob=-.21, no_speech_prob=.03,
                                        compression_ratio=1.1),
                            FakeSegment("그때 사진을 봤어요.", avg_logprob=-.35,
                                        no_speech_prob=.07, compression_ratio=1.4)]
        observation = await frontend.transcribe(pcm(), 16000)
        self.assertEqual(observation.text, "네, 그때 사진을 봤어요.")
        self.assertEqual([entry["chars"] for entry in observation.quality], [2, 11])
        self.assertAlmostEqual(observation.quality[0]["no_speech_prob"], .03)
        self.assertAlmostEqual(observation.quality[1]["avg_logprob"], -.35)
        # 판정용 숫자는 관측 메타데이터로 나가지 않는다.
        self.assertEqual(set(observation.metadata()), {"audio_event", "language", "prosody"})
        self.assertNotIn("logprob", json.dumps(observation.metadata(), ensure_ascii=False))
        # 지표를 모으려고 생성기를 두 번 돌지 않는다. 디코드는 한 번뿐이다.
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(self.model.events, [("start", 0), ("end", 0)])

    async def test_missing_or_broken_quality_values_are_dropped(self):
        frontend = self.frontend()
        self.model.texts = [FakeSegment("네", avg_logprob=float("nan"), no_speech_prob=None),
                            FakeSegment("그래", compression_ratio="1.1", avg_logprob=True),
                            FakeSegment("알겠어")]
        observation = await frontend.transcribe(pcm(), 16000)
        # 재지 못한 항목은 담지 않는다. 0 으로 채우지 않는다.
        self.assertEqual([set(entry) for entry in observation.quality],
                         [{"chars"}, {"chars"}, {"chars"}])

    async def test_korean_segments_are_joined_and_stripped(self):
        frontend = self.frontend()
        self.model.texts = [" 네, ", "그때 ", "사진을 봤어요. ", " "]
        observation = await frontend.transcribe(pcm(), 16000)
        self.assertEqual(observation.text, "네, 그때 사진을 봤어요.")

    async def test_empty_odd_and_out_of_range_audio_do_not_reach_the_model(self):
        frontend = self.frontend()
        empty = await frontend.transcribe(b"", 16000)
        self.assertEqual(empty.text, "")
        self.assertEqual(empty.prosody["duration_sec"], 0.0)
        with self.assertRaises(ValueError):
            await frontend.transcribe(b"\x00\x01\x02", 16000)
        for rate in (0, 4000, 192000):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                await frontend.transcribe(pcm(), rate)
        self.assertEqual(self.model.calls, [])

    async def test_segment_generator_is_consumed_before_the_call_returns(self):
        frontend = self.frontend()
        await frontend.transcribe(pcm(), 16000)
        # 추론과 생성기 소비가 같은 단일 워커에서 끝났다는 뜻이다.
        self.assertEqual(self.model.events, [("start", 0), ("end", 0)])
        self.assertEqual(self.model.active, 0)
        self.assertEqual(len(set(self.model.threads)), 1)
        self.assertTrue(self.model.threads[0].startswith("speech"))

    async def test_cancelled_call_never_overlaps_the_next_inference(self):
        frontend = self.frontend()
        self.model.hold = threading.Event()
        first = asyncio.ensure_future(frontend.transcribe(pcm(), 16000))
        while not self.model.calls:
            await asyncio.sleep(.01)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        second = asyncio.ensure_future(frontend.transcribe(pcm(), 16000))
        await asyncio.sleep(.05)
        self.model.hold.set()
        observation = await asyncio.wait_for(second, timeout=5)
        self.assertEqual(observation.text, "안녕하세요. 오늘은 어땠어요?")
        # 취소는 이미 올라간 GPU 작업을 멈추지 않는다. 겹치지만 않으면 된다.
        self.assertEqual(self.model.max_active, 1)
        self.assertEqual(self.model.events,
                         [("start", 0), ("end", 0), ("start", 1), ("end", 1)])


class SenseVoiceRollbackTests(unittest.IsolatedAsyncioTestCase):
    """되돌린 CPU 모델이 라벨을 내도 관측에는 전사·이벤트·언어만 남는지 본다."""

    async def test_speaker_labels_from_the_model_are_not_published(self):
        class Result:
            text = " 안녕하세요. "
            emotion = "<|SAD|>"
            event = "<|Speech|>"
            lang = "<|ko|>"

        class Stream:
            result = Result()

            def accept_waveform(self, rate, samples):
                pass

        class Recognizer:
            def create_stream(self):
                return Stream()

            def decode_stream(self, stream):
                pass

        sherpa = types.ModuleType("sherpa_onnx")
        sherpa.OfflineRecognizer = types.SimpleNamespace(
            from_sense_voice=lambda **kwargs: Recognizer())
        with contextlib.ExitStack() as stack:
            root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
            for name in ("model.int8.onnx", "tokens.txt"):
                (root / name).write_bytes(b"not a real model")
            stack.enter_context(mock.patch.dict(sys.modules, {"sherpa_onnx": sherpa}))
            frontend = realtime_audio.SenseVoiceFrontend(root)
            self.addCleanup(frontend.close)
            observation = await frontend.transcribe(pcm(), 16000)
        self.assertEqual(observation.text, "안녕하세요.")
        self.assertEqual(observation.audio_event, "speech")
        self.assertEqual(observation.language, "ko")
        self.assertEqual(set(observation.metadata()), {"audio_event", "language", "prosody"})
        self.assertFalse(hasattr(observation, "emotion"))
        self.assertNotIn("sad", json.dumps(observation.metadata(), ensure_ascii=False).lower())


class FactoryTests(Fixture):
    def test_whisper_is_the_default_production_frontend(self):
        with fake_whisper(self.model):
            frontend = create_frontend("whisper", self.root)
        self.addCleanup(frontend.close)
        self.assertIsInstance(frontend, WhisperFrontend)
        self.assertIn("faster-whisper", frontend_name(frontend))

    def test_sensevoice_stays_available_as_an_explicit_rollback(self):
        made = {}

        class Stub:
            def __init__(self, model_dir, *args, **kwargs):
                made["model_dir"] = model_dir

        with mock.patch.object(realtime_audio, "SenseVoiceFrontend", Stub):
            frontend = create_frontend("sensevoice", self.root)
        self.assertIsInstance(frontend, Stub)
        self.assertEqual(made["model_dir"], self.root)

    def test_unknown_backend_is_refused_instead_of_falling_back(self):
        for backend in ("", "qwen", "Whisper ", None):
            with self.subTest(backend=backend), self.assertRaises(ValueError):
                create_frontend(backend, self.root)


STT_KEYS = ("DIALOGUE_STT_BACKEND", "DIALOGUE_STT_DEVICE",
            "DIALOGUE_STT_COMPUTE_TYPE", "DIALOGUE_MODEL_DIR")


@contextlib.contextmanager
def environment(**values):
    """STT 관련 변수만 지정한 값으로 두고 나머지 환경은 건드리지 않는다."""
    with mock.patch.dict("os.environ", values):
        for key in STT_KEYS:
            if key not in values:
                os.environ.pop(key, None)
        yield


class SettingsTests(unittest.TestCase):
    def test_defaults_select_whisper_on_cuda(self):
        with environment():
            settings = Settings.from_env()
        self.assertEqual(settings.stt_backend, "whisper")
        self.assertEqual(settings.stt_device, "cuda")
        self.assertEqual(settings.stt_compute_type, "float16")
        self.assertTrue(settings.model_dir.endswith("whisper-large-v3"))

    def test_explicit_sensevoice_restores_the_old_model_directory(self):
        with environment(DIALOGUE_STT_BACKEND="sensevoice"):
            settings = Settings.from_env()
        self.assertEqual(settings.stt_backend, "sensevoice")
        self.assertTrue(settings.model_dir.endswith("sensevoice"))

    def test_explicit_model_dir_and_cpu_are_honoured(self):
        with environment(DIALOGUE_STT_DEVICE="CPU", DIALOGUE_STT_COMPUTE_TYPE="int8",
                         DIALOGUE_MODEL_DIR="/srv/models/whisper"):
            settings = Settings.from_env()
        self.assertEqual(settings.stt_device, "cpu")
        self.assertEqual(settings.stt_compute_type, "int8")
        self.assertEqual(settings.model_dir, "/srv/models/whisper")

    def test_existing_positional_construction_still_works(self):
        settings = Settings("/tmp/sessions", "/tmp/model", "token")
        self.assertEqual(settings.stt_backend, "whisper")
        self.assertEqual(settings.stt_device, "cuda")


class LLM:
    async def available(self, endpoint=None):
        return True


class Gate:
    def settings(self):
        return {"version": "verified_input_v1", "acoustic": "stub"}

    def close(self):
        pass


class HealthTests(Fixture):
    def setUp(self):
        super().setUp()
        self.sessions = self.stack.enter_context(tempfile.TemporaryDirectory())

    def settings(self, model_dir=None, **values):
        return Settings(self.sessions, str(model_dir or self.root),
                        reactions_enabled=False, **values)

    def test_health_names_the_frontend_the_server_actually_loaded(self):
        app = create_app(self.settings(stt_compute_type="int8_float16"),
                         llm=LLM(), speech_gate=Gate())
        with fake_whisper(self.model), TestClient(app) as client:
            body = client.get("/health").json()
        self.assertEqual(body["stt"], "faster-whisper {} cuda/int8_float16".format(self.root.name))
        self.assertEqual(self.model.init["compute_type"], "int8_float16")
        # 예열은 health 가 ready 를 말하기 전에 끝난다.
        self.assertEqual(len(self.model.calls), 1)

    def test_injected_test_frontend_is_not_labelled_as_a_real_model(self):
        class Frontend:
            async def transcribe(self, pcm, sample_rate=16000):
                return Observation("테스트")

        app = create_app(self.settings(), frontend=Frontend(), llm=LLM(), speech_gate=Gate())
        with TestClient(app) as client:
            self.assertEqual(client.get("/health").json()["stt"], "injected")

    def test_startup_fails_when_the_model_cannot_warm_up(self):
        self.model.fail = RuntimeError("CUDA out of memory")
        app = create_app(self.settings(), llm=LLM(), speech_gate=Gate())
        with fake_whisper(self.model), self.assertRaises(RuntimeError):
            with TestClient(app):
                pass

    def test_loaded_model_is_released_when_a_later_startup_step_fails(self):
        made = []
        build = dialogue_server.create_frontend

        def spy(*args, **kwargs):
            made.append(build(*args, **kwargs))
            return made[-1]

        def broken_gate(*args, **kwargs):
            raise FileNotFoundError("Silero VAD model missing")

        app = create_app(self.settings(), llm=LLM())
        with fake_whisper(self.model),                 mock.patch.object(dialogue_server, "create_frontend", spy),                 mock.patch.object(dialogue_server, "SileroSpeechGate", broken_gate),                 self.assertRaises(FileNotFoundError):
            with TestClient(app):
                pass
        # 이미 올라온 GPU 모델과 실행기를 시작 실패 때도 놓는다.
        self.assertIsNone(made[0].model)
        with self.assertRaises(RuntimeError):
            made[0].executor.submit(int)

    def test_startup_fails_when_the_model_directory_is_missing(self):
        app = create_app(self.settings(model_dir=self.root / "없음"), llm=LLM(), speech_gate=Gate())
        with fake_whisper(self.model), self.assertRaises(FileNotFoundError):
            with TestClient(app):
                pass


if __name__ == "__main__":
    unittest.main()
