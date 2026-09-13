import base64
import io
import json
import sys
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_server import Settings, create_app
from realtime_audio import Observation
from tts_server import QwenVoice, create_app as tts_app
from voice_reference import ReferenceStore, decode_reference, session_recording


def wav_bytes(seconds=4, rate=24000, channels=1, silent=False):
    audio = (np.sin(np.arange(int(seconds * rate)) * 2 * np.pi * 220 / rate) * 5000).astype("<i2")
    if silent: audio[:] = 0
    if channels == 2: audio = np.column_stack((audio, audio))
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(audio.tobytes())
    return out.getvalue()


class ReferenceTests(unittest.TestCase):
    def test_long_recording_is_contiguous_bounded_and_original_unchanged(self):
        original = wav_bytes(17, channels=2)
        before = bytes(original)
        reference = decode_reference(original, "test")
        self.assertLessEqual(reference.details()["duration_sec"], 12)
        self.assertTrue(reference.details()["cropped"])
        self.assertEqual(original, before)
        with wave.open(io.BytesIO(reference.wav())) as preview:
            self.assertEqual(preview.getnchannels(), 1)
            self.assertEqual(preview.readframes(preview.getnframes()), reference.pcm)
        mono = decode_reference(wav_bytes(12), "test")
        self.assertEqual(reference.pcm, mono.pcm)

    def test_bad_silent_short_and_truncated_audio_are_rejected(self):
        for raw in (b"bad", wav_bytes(2), wav_bytes(silent=True), wav_bytes()[:-200]):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                decode_reference(raw, "test")

    def test_memory_capacity_expiry_and_delete(self):
        now = [0.]
        store = ReferenceStore(ttl=5, capacity=1, clock=lambda: now[0])
        ref = store.put(decode_reference(wav_bytes(), "a"))
        with self.assertRaises(ValueError): store.put(decode_reference(wav_bytes(), "b"))
        now[0] = 6
        with self.assertRaises(ValueError): store.get(ref.reference_id)
        ref = store.put(decode_reference(wav_bytes(), "b"))
        store.delete(ref.reference_id)
        with self.assertRaises(ValueError): store.get(ref.reference_id)

    def test_session_paths_and_updated_recording(self):
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "person"
            folder.mkdir()
            (Path(root) / "current.json").write_text('{"session":"person"}')
            (folder / "voice.wav").write_bytes(wav_bytes(4))
            first, _ = session_recording(root)
            (folder / "voice.wav").write_bytes(wav_bytes(5))
            second, _ = session_recording(root, "person")
            self.assertNotEqual(first, second)
            for sid in ("..", "../person", "a/b", "a\\b", ".hidden", "missing"):
                with self.subTest(sid=sid), self.assertRaises(ValueError): session_recording(root, sid)

    def test_qwen_prompt_includes_audio_codes_and_transcript(self):
        class Model:
            def create_voice_clone_prompt(self, **kwargs):
                self.args = kwargs
                return [object()]
        voice = QwenVoice.__new__(QwenVoice)
        voice.model = Model()
        result = voice.create_prompt(bytes(24000 * 2 * 4), 24000, "정확한 참조 문장")
        self.assertIsInstance(result, list)
        self.assertFalse(voice.model.args["x_vector_only_mode"])
        self.assertEqual(voice.model.args["ref_text"], "정확한 참조 문장")
        self.assertEqual(voice.model.args["ref_audio"][1], 24000)


class ReferenceProtocolTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.headers = {"X-Token": "test-token"}
        self.observed = []
        self.bound = []
        self.released = []
        observed, bound, released = self.observed, self.bound, self.released
        class Frontend:
            async def transcribe(self, pcm, sample_rate=16000):
                observed.append((len(pcm), sample_rate))
                return Observation("자동 전사")
        class LLM:
            async def available(self, endpoint=None): return True
        class Voice:
            async def release(self): released.append(True)
        class TTS:
            async def available(self): return True
            async def bind(self, reference, text=None):
                bound.append((reference, text))
                return Voice()
        self.app = create_app(Settings(self.tmp.name, "unused", token="test-token", allow_test_mode=True),
                              frontend=Frontend(), llm=LLM(), tts=TTS())
        self.hello = {"type": "start", "protocol": 1, "sample_rate": 16000,
                      "channels": 1, "format": "pcm_s16le", "test_mode": True, "test_persona": "인물",
                      "interruption_policy": "semantic_v1"}

    def tearDown(self): self.tmp.cleanup()

    def test_upload_preview_corrected_transcript_and_prompt_release(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.post("/references").status_code, 403)
            response = client.post("/references", headers=self.headers,
                                   files={"voice": ("sample.wav", wav_bytes(16))})
            self.assertEqual(response.status_code, 200, response.text)
            info = response.json()
            key = info["reference_id"]
            self.assertEqual(client.get(f"/references/{key}/audio.wav").status_code, 403)
            preview = client.get(f"/references/{key}/audio.wav", headers=self.headers)
            self.assertEqual(preview.headers["cache-control"], "no-store")
            with wave.open(io.BytesIO(preview.content)) as wav:
                self.assertEqual(self.observed[-1], (wav.getnframes() * 2, wav.getframerate()))
            with client.websocket_connect("/dialogue", headers=self.headers) as ws:
                ws.send_json(dict(self.hello, reference_id=key, reference_text="수정한 전사"))
                self.assertEqual(ws.receive_json()["type"], "voice.preparing")
                ready = ws.receive_json()
                self.assertEqual(ready["voice_mode"], "reference_icl")
                self.assertEqual(ready["reference"]["text"], "수정한 전사")
                self.assertEqual(self.bound[0][1], "수정한 전사")
                self.assertEqual(len(self.observed), 1)  # no second ASR of a prepared clip
                ws.send_json({"type": "stop"})
                self.assertEqual(ws.receive_json()["type"], "stopped")
            client.delete(f"/references/{key}", headers=self.headers)
            self.assertEqual(client.get(f"/references/{key}/audio.wav", headers=self.headers).status_code, 404)
        self.assertEqual(self.released, [True])
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_registered_experience_uses_its_own_reference_and_rejects_override(self):
        for name, seconds in (("first", 4), ("second", 5)):
            folder = Path(self.tmp.name) / name
            folder.mkdir()
            (folder / "persona.md").write_text("인물", encoding="utf-8")
            (folder / "voice.wav").write_bytes(wav_bytes(seconds))
        (Path(self.tmp.name) / "current.json").write_text('{"session":"second"}')
        with TestClient(self.app) as client:
            with client.websocket_connect("/dialogue", headers=self.headers) as ws:
                ws.send_json(dict(self.hello, test_mode=False, session="first"))
                ws.receive_json()
                ready = ws.receive_json()
                self.assertEqual(ready["reference"]["duration_sec"], 4)
                self.assertIn("first", ready["reference"]["source"])
            with client.websocket_connect("/dialogue", headers=self.headers) as ws:
                ws.send_json(dict(self.hello, test_mode=False, session="first", reference_id="other"))
                self.assertEqual(ws.receive_json()["code"], "protocol_error")
        self.assertEqual(len(self.bound), 1)

    def test_unset_unity_reference_strings_use_current_registered_recording(self):
        folder = Path(self.tmp.name) / "person"
        folder.mkdir()
        (folder / "voice.wav").write_bytes(wav_bytes())
        (Path(self.tmp.name) / "current.json").write_text('{"session":"person"}')
        with TestClient(self.app) as client:
            with client.websocket_connect("/dialogue", headers=self.headers) as ws:
                ws.send_json(dict(self.hello, reference_id="", reference_text=""))
                self.assertEqual(ws.receive_json()["type"], "voice.preparing")
                ready = ws.receive_json()
                self.assertEqual(ready["type"], "ready")
                self.assertEqual(ready["reference"]["text"], "자동 전사")

    def test_missing_reference_reports_actionable_error_and_frees_connection(self):
        with TestClient(self.app) as client:
            for _ in range(2):
                with client.websocket_connect("/dialogue", headers=self.headers) as ws:
                    ws.send_json(self.hello)
                    ws.receive_json()
                    error = ws.receive_json()
                    self.assertEqual(error["code"], "protocol_error")
                    self.assertIn("WAV", error["message"])
            self.assertEqual(client.get("/health").json()["connections"], 0)

    def test_legacy_client_cannot_connect_without_playback_pause_support(self):
        hello = dict(self.hello)
        del hello["interruption_policy"]
        with TestClient(self.app) as client:
            with client.websocket_connect("/dialogue", headers=self.headers) as ws:
                ws.send_json(hello)
                error = ws.receive_json()
                self.assertEqual(error["code"], "protocol_error")
                self.assertIn("Unity", error["message"])
            self.assertEqual(client.get("/health").json()["connections"], 0)
        self.assertEqual(len(self.bound), 0)


class WorkerReferenceTests(unittest.TestCase):
    def test_prepare_once_reuse_for_phrases_release_and_auth(self):
        class Voice:
            model_id = "fake-base"
            def __init__(self): self.prepared, self.used = [], []
            def create_prompt(self, pcm, rate, text):
                prompt = [pcm[:10], rate, text]
                self.prepared.append(prompt)
                return prompt
            def synthesize(self, text, prompt, cancelled):
                self.used.append(prompt)
                return bytes(9600), 24000, .1
        voice = Voice()
        headers = {"X-Token": "worker-token"}
        request = {"pcm": base64.b64encode(bytes(4 * 16000 * 2)).decode(),
                   "sample_rate": 16000, "text": "참조 문장"}
        with TestClient(tts_app(voice, "worker-token")) as client:
            self.assertEqual(client.post("/voices", json=request).status_code, 403)
            bad = dict(request, pcm="broken")
            self.assertEqual(client.post("/voices", headers=headers, json=bad).status_code, 400)
            key = client.post("/voices", headers=headers, json=request).json()["voice_id"]
            for text in ("첫 답변", "다음 답변"):
                response = client.post("/synthesize", headers=headers, json={"text": text, "voice_id": key})
                packets = [json.loads(line) for line in response.text.splitlines()]
                self.assertEqual(packets[-1]["type"], "done")
                self.assertEqual(packets[1]["type"], "audio")
            self.assertEqual(len(voice.prepared), 1)
            self.assertTrue(all(p is voice.prepared[0] for p in voice.used))
            self.assertEqual(client.delete("/voices/" + key).status_code, 403)
            client.delete("/voices/" + key, headers=headers)
            self.assertEqual(client.get("/health").json()["references"], 0)
            self.assertEqual(client.post("/synthesize", headers=headers,
                json={"text": "답변", "voice_id": key}).status_code, 404)


if __name__ == "__main__": unittest.main()
