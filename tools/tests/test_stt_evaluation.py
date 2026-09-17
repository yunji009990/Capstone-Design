"""STT 비교 실행기의 계산·자료 검증 검사. 실제 모델·GPU·오디오 장치를 쓰지 않는다.

가짜 백엔드와 합성 WAV만 사용한다. 이 검사의 통과는 모델 정확도가 아니라
CER 계산·manifest 검증·실패 집계·보고서 규칙이 의도대로 동작한다는 뜻이다.
"""
import ast
import io
import json
import os
import struct
import sys
import tempfile
import unittest
import wave
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import eval_stt_models as stt


def write_wav(path, seconds=0.5, rate=16000, channels=1, width=2, value=1000):
    frames = int(rate * seconds)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(struct.pack("<" + "h" * frames * channels, *([value] * frames * channels)))
    return path


def write_manifest(path, samples, notes=None):
    body = {"samples": samples}
    if notes: body["notes"] = notes
    path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    return path


class NormalizationTests(unittest.TestCase):
    def test_punctuation_and_spacing_are_dropped_but_letters_and_digits_stay(self):
        self.assertEqual(stt.normalize_text("안녕하세요, 저는 3층에 있어요!"), "안녕하세요저는3층에있어요")

    def test_width_and_case_are_folded(self):
        self.assertEqual(stt.normalize_text("ＡＢ１２"), stt.normalize_text("ab12"))

    def test_none_and_whitespace_become_empty(self):
        self.assertEqual(stt.normalize_text(None), "")
        self.assertEqual(stt.normalize_text("  …  "), "")

    def test_spoken_korean_numerals_are_not_converted_to_digits(self):
        # 기록한 한계: 숫자 발음과 숫자 표기를 자동으로 맞추지 않는다.
        self.assertNotEqual(stt.normalize_text("세 시"), stt.normalize_text("3시"))


class EditDistanceTests(unittest.TestCase):
    def test_identical_strings_have_no_edits(self):
        self.assertEqual(stt.edit_counts("가나다", "가나다"),
                         {"substitutions": 0, "deletions": 0, "insertions": 0, "distance": 0})

    def test_one_of_each_operation_is_counted_separately(self):
        counts = stt.edit_counts("가나다라", "가X다라마")
        self.assertEqual(counts["substitutions"], 1)
        self.assertEqual(counts["insertions"], 1)
        self.assertEqual(counts["deletions"], 0)
        self.assertEqual(counts["distance"], 2)

    def test_operation_counts_reconstruct_the_length_difference(self):
        for ref, hyp in (("안녕하세요", ""), ("", "환청입니다"), ("가나다", "가다라마")):
            counts = stt.edit_counts(ref, hyp)
            with self.subTest(ref=ref, hyp=hyp):
                self.assertEqual(counts["distance"],
                                 counts["substitutions"] + counts["deletions"] + counts["insertions"])
                self.assertEqual(len(hyp) - len(ref), counts["insertions"] - counts["deletions"])


class ScorePairTests(unittest.TestCase):
    def test_empty_reference_is_excluded_from_cer_and_flagged_as_hallucination(self):
        row = stt.score_pair("", "어 그러니까")
        self.assertIsNone(row["cer"])
        self.assertTrue(row["hallucinated"])
        self.assertTrue(row["reference_empty"])

    def test_empty_reference_with_empty_transcript_is_not_a_hallucination(self):
        row = stt.score_pair("", "")
        self.assertIsNone(row["cer"])
        self.assertFalse(row["hallucinated"])

    def test_empty_transcript_counts_as_deletions_not_as_a_skip(self):
        row = stt.score_pair("안녕하세요", "")
        self.assertEqual(row["cer"], 1.0)
        self.assertEqual(row["deletions"], 5)
        self.assertTrue(row["hypothesis_empty"])
        self.assertFalse(row["exact"])

    def test_punctuation_only_difference_is_an_exact_match(self):
        self.assertTrue(stt.score_pair("네, 알겠습니다.", "네 알겠습니다")["exact"])


class AggregateTests(unittest.TestCase):
    def rows(self):
        short = dict(stt.score_pair("가나", "가X"), failed=False)          # 2자 중 1자 오류
        long = dict(stt.score_pair("가" * 18, "가" * 18), failed=False)    # 18자 정답
        noise = dict(stt.score_pair("", "잡음"), failed=False)
        broken = dict(stt.score_pair("무엇이든", ""), failed=True)
        return [short, long, noise, broken]

    def test_micro_and_macro_weight_differently(self):
        summary = stt.aggregate_accuracy(self.rows())
        self.assertEqual(summary["micro_cer"], round(1/20, 4))
        self.assertEqual(summary["macro_cer"], round((0.5 + 0.0)/2, 4))

    def test_failures_are_reported_and_not_scored(self):
        summary = stt.aggregate_accuracy(self.rows())
        self.assertEqual(summary["failed_samples"], 1)
        self.assertEqual(summary["scored_samples"], 2)
        self.assertEqual(summary["empty_reference_samples"], 1)
        self.assertEqual(summary["hallucination_on_empty_reference"], 1)

    def test_sentence_exact_counts_only_scored_samples(self):
        summary = stt.aggregate_accuracy(self.rows())
        self.assertEqual(summary["sentence_exact"], 1)
        self.assertEqual(summary["sentence_exact_ratio"], 0.5)

    def test_all_failed_leaves_no_misleading_zero_cer(self):
        summary = stt.aggregate_accuracy([dict(stt.score_pair("가나", ""), failed=True)])
        self.assertIsNone(summary["micro_cer"])
        self.assertIsNone(summary["macro_cer"])


class ConditionGroupTests(unittest.TestCase):
    def rows(self):
        def row(condition, sample_id, round_index, reference, hypothesis, seconds):
            base = dict(stt.score_pair(reference, hypothesis), failed=False)
            base.update(condition=condition, id=sample_id, round=round_index,
                        seconds=seconds, duration_seconds=2.0)
            return base
        return [row("clean", "c1", 1, "가나다라", "가나다라", 1.0),
                row("clean", "c1", 2, "가나다라", "가나다라", 1.2),
                row("noisy", "n1", 1, "가나다라", "가X다라", 2.0),
                row("noisy", "n1", 2, "가나다라", "가X다라", 2.2)]

    def test_conditions_are_scored_and_timed_separately(self):
        groups = {group["condition"]: group for group in stt.group_by_condition(self.rows())}
        self.assertEqual(sorted(groups), ["clean", "noisy"])
        self.assertEqual(groups["clean"]["accuracy"]["micro_cer"], 0.0)
        self.assertEqual(groups["noisy"]["accuracy"]["micro_cer"], 0.25)
        self.assertEqual(groups["clean"]["input_files"], 1)
        # 정확도는 1회차 기준, 지연은 모든 회차를 쓴다.
        self.assertEqual(groups["noisy"]["latency"]["inferences"], 2)
        self.assertEqual(groups["clean"]["latency"]["average"], 1.1)


class LatencyTests(unittest.TestCase):
    def test_failed_calls_are_excluded_and_rtf_uses_audio_length(self):
        rows = [{"seconds": 1.0, "duration_seconds": 2.0, "failed": False},
                {"seconds": 3.0, "duration_seconds": 2.0, "failed": False},
                {"seconds": 9.0, "duration_seconds": 2.0, "failed": True}]
        stats = stt.latency_stats(rows)
        self.assertEqual(stats["inferences"], 2)
        self.assertEqual(stats["average"], 2.0)
        self.assertEqual(stats["rtf"], 1.0)
        self.assertEqual(stats["max"], 3.0)

    def test_empty_input_reports_null_instead_of_zero(self):
        stats = stt.latency_stats([])
        self.assertIsNone(stats["average"])
        self.assertIsNone(stats["rtf"])


class WavValidationTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_16k_mono_pcm16_is_accepted_with_duration_and_hash(self):
        audio = stt.read_wav(write_wav(self.dir / "ok.wav", seconds=0.5))
        self.assertEqual(audio["duration_seconds"], 0.5)
        self.assertEqual(len(audio["sha256"]), 64)
        self.assertEqual(len(audio["array"]), 8000)

    def test_other_rates_channels_and_widths_are_rejected(self):
        for name, kwargs in (("r8k", {"rate": 8000}), ("stereo", {"channels": 2}), ("w8", {"width": 1})):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    stt.read_wav(write_wav(self.dir / f"{name}.wav", **kwargs))

    def test_a_truncated_body_is_rejected_even_when_the_header_looks_valid(self):
        path = write_wav(self.dir / "cut.wav", seconds=1.0)
        data = path.read_bytes()
        path.write_bytes(data[:len(data) - 4000])      # 헤더는 그대로, 본문만 잘라낸다.
        with self.assertRaises(ValueError) as caught:
            stt.read_wav(path)
        self.assertIn("Truncated", str(caught.exception))

    def test_a_clip_over_the_limit_is_rejected_from_the_header_without_reading_it(self):
        path = write_wav(self.dir / "long.wav", seconds=stt.MAX_SECONDS + 0.1)
        with mock.patch.object(wave.Wave_read, "readframes",
                               side_effect=AssertionError("must not read")):
            with self.assertRaises(ValueError): stt.read_wav(path)

    def test_empty_and_too_long_clips_are_rejected(self):
        with self.assertRaises(ValueError):
            stt.read_wav(write_wav(self.dir / "empty.wav", seconds=0))
        with self.assertRaises(ValueError):
            stt.read_wav(write_wav(self.dir / "long.wav", seconds=stt.MAX_SECONDS + 0.1))


class ManifestTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "audio").mkdir()
        write_wav(self.dir / "audio" / "a.wav")
        write_wav(self.dir / "audio" / "b.wav")
        self.entries = [{"id": "s1", "audio": "audio/a.wav", "reference": "안녕하세요", "source": "가상 대본"},
                        {"id": "s2", "audio": "audio/b.wav", "reference": "반갑습니다"}]

    def load(self, entries, limit=None):
        return stt.load_manifest(write_manifest(self.dir / "m.json", entries), limit)

    def test_relative_audio_paths_resolve_against_the_manifest_folder(self):
        manifest = self.load(self.entries)
        self.assertEqual(manifest["selected"], 2)
        self.assertTrue(manifest["samples"][0]["path"].is_file())
        self.assertEqual(len(manifest["sha256"]), 64)

    def test_limit_keeps_the_declared_count_visible(self):
        manifest = self.load(self.entries, limit=1)
        self.assertEqual((manifest["declared"], manifest["selected"]), (2, 1))

    def test_duplicate_ids_are_rejected(self):
        entries = self.entries + [{"id": "s1", "audio": "audio/b.wav", "reference": "또"}]
        with self.assertRaises(ValueError): self.load(entries)

    def test_missing_audio_file_is_rejected(self):
        with self.assertRaises(FileNotFoundError):
            self.load([{"id": "s1", "audio": "audio/none.wav", "reference": "없음"}])

    def test_empty_reference_is_allowed_for_noise_only_samples(self):
        manifest = self.load([{"id": "n1", "audio": "audio/a.wav", "reference": "",
                               "condition": "negative"}])
        self.assertEqual(manifest["samples"][0]["reference"], "")
        self.assertEqual(manifest["samples"][0]["condition"], "negative")

    def test_a_missing_reference_is_rejected_instead_of_becoming_a_noise_sample(self):
        # 누락을 ''로 바꾸면 말이 있는 표본이 잡음 정답으로 집계돼 정확도가 조용히 틀어진다.
        with self.assertRaises(ValueError):
            self.load([{"id": "s1", "audio": "audio/a.wav"}])
        with self.assertRaises(ValueError):
            self.load([{"id": "s1", "audio": "audio/a.wav", "reference": None}])

    def test_condition_defaults_and_is_validated_as_a_short_label(self):
        self.assertEqual(self.load(self.entries)["samples"][0]["condition"], stt.DEFAULT_CONDITION)
        for condition in ("", "  ", "a" * (stt.MAX_CONDITION_CHARS + 1), "white snr20\nx", 3):
            with self.subTest(condition=condition), self.assertRaises(ValueError):
                self.load([{"id": "s1", "audio": "audio/a.wav", "reference": "가", "condition": condition}])

    def test_declared_source_recordings_is_optional_and_must_be_positive(self):
        path = self.dir / "m.json"
        path.write_text(json.dumps({"samples": self.entries, "source_recordings": 30}), encoding="utf-8")
        self.assertEqual(stt.load_manifest(path)["declared_source_recordings"], 30)
        self.assertIsNone(self.load(self.entries)["declared_source_recordings"])
        for bad in (0, -1, "30", True):
            path.write_text(json.dumps({"samples": self.entries, "source_recordings": bad}), encoding="utf-8")
            with self.subTest(bad=bad), self.assertRaises(ValueError): stt.load_manifest(path)

    def test_wrong_shapes_and_the_safety_cap_are_rejected(self):
        bad = [[], [{"audio": "audio/a.wav"}], [{"id": "s1"}],
               [{"id": "s1", "audio": "audio/a.wav", "reference": 3}], ["s1"]]
        for entries in bad:
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                self.load(entries)
        too_many = [{"id": f"s{i}", "audio": "audio/a.wav", "reference": "가"}
                    for i in range(stt.MAX_SAMPLES + 1)]
        with self.assertRaises(ValueError): self.load(too_many)

    def test_a_json_array_is_not_a_manifest(self):
        (self.dir / "m.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(ValueError): stt.load_manifest(self.dir / "m.json")


class WhisperGeneratorTests(unittest.TestCase):
    """faster-whisper는 generator를 돌려준다. 끝까지 소비해야 시간이 실제 추론을 포함한다."""

    class LazyModel:
        def __init__(self): self.consumed = 0

        def transcribe(self, audio, **kwargs):
            self.kwargs = kwargs

            def segments():
                for text in (" 안녕하세요", " 반갑습니다 "):
                    self.consumed += 1
                    yield type("Segment", (), {"text": text})()
            return segments(), {"language": "ko"}

    def test_transcribe_consumes_the_generator_and_joins_the_text(self):
        backend = object.__new__(stt.WhisperBackend)
        backend.model = self.LazyModel()
        text = backend.transcribe({"array": None})
        self.assertEqual(backend.model.consumed, 2)
        self.assertEqual(text, "안녕하세요 반갑습니다")

    def test_decoding_options_match_the_documented_comparison_settings(self):
        backend = object.__new__(stt.WhisperBackend)
        backend.model = self.LazyModel()
        backend.transcribe({"array": None})
        self.assertEqual(backend.model.kwargs,
                         {"language": "ko", "beam_size": 5, "temperature": 0,
                          "condition_on_previous_text": False, "vad_filter": False,
                          "without_timestamps": True})


class MemoryBudgetTests(unittest.TestCase):
    class FakeTorch:
        class cuda:
            fraction = None

            @staticmethod
            def get_device_properties(index):
                return type("Props", (), {"total_memory": 8 * 1024 * 1024 * 1024})()

            @classmethod
            def set_per_process_memory_fraction(cls, fraction, device):
                cls.fraction = (fraction, device)

    def test_budget_is_converted_to_a_fraction_of_the_device_total(self):
        torch = self.FakeTorch()
        applied = stt.apply_memory_budget(torch, 6500)
        self.assertEqual(self.FakeTorch.cuda.fraction, (6500/8192, 0))   # allocator에는 반올림 전 값
        self.assertEqual(applied["fraction"], round(6500/8192, 4))
        self.assertEqual(applied["requested_mib"], 6500)
        self.assertEqual(applied["device_total_mib"], 8192)
        self.assertIn("not a whole-process VRAM cap", applied["scope"])

    def test_a_budget_larger_than_the_device_is_clamped_to_one(self):
        stt.apply_memory_budget(self.FakeTorch(), 99999)
        self.assertEqual(self.FakeTorch.cuda.fraction[0], 1.0)

    def test_no_budget_leaves_the_allocator_untouched(self):
        self.FakeTorch.cuda.fraction = None
        applied = stt.apply_memory_budget(self.FakeTorch(), None)
        self.assertIsNone(self.FakeTorch.cuda.fraction)
        self.assertFalse(applied["applied"])

    def test_whisper_refuses_the_budget_instead_of_pretending_to_honour_it(self):
        backend = object.__new__(stt.WhisperBackend)
        with self.assertRaises(ValueError):
            stt.WhisperBackend.__init__(backend, "model", {"compute_type": "float16",
                                                           "gpu_memory_budget_mib": 6500})


class OptionalKwargTests(unittest.TestCase):
    def test_optional_kwargs_are_used_when_the_loader_accepts_them(self):
        def loader(dtype, local_files_only=None, trust_remote_code=None):
            return {"local": local_files_only, "trust": trust_remote_code}
        model, applied = stt.call_with_optional(loader, {"dtype": "bf16"},
                                                {"local_files_only": True, "trust_remote_code": False})
        self.assertEqual(model, {"local": True, "trust": False})
        self.assertEqual(applied, ["local_files_only", "trust_remote_code"])

    def test_an_older_loader_still_works_and_the_report_says_what_was_applied(self):
        def loader(dtype): return {"dtype": dtype}
        model, applied = stt.call_with_optional(loader, {"dtype": "bf16"}, {"local_files_only": True})
        self.assertEqual(model, {"dtype": "bf16"})
        self.assertEqual(applied, [])


class FakeBackend:
    """텍스트를 미리 정해 두는 가짜 백엔드. 모델도 GPU도 쓰지 않는다."""
    packages = ("numpy",)
    texts = {}
    closed = 0
    fail_load = False
    fail_close = False
    options = None

    def __init__(self, model_path, options):
        FakeBackend.options = options
        if FakeBackend.fail_load: raise RuntimeError("model path secret must not leak")
        self.settings = {"device": "fake"}

    def transcribe(self, audio):
        text = FakeBackend.texts.get(audio["sha256"], "")
        if text == "__raise__": raise RuntimeError("secret transcript must not leak")
        return text

    def close(self):
        FakeBackend.closed += 1
        if FakeBackend.fail_close: raise OSError("cleanup secret must not leak")


class RunTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        (self.dir / "audio").mkdir()
        self.a = write_wav(self.dir / "audio" / "a.wav", value=1000)
        self.b = write_wav(self.dir / "audio" / "b.wav", value=2000)
        self.warm = write_wav(self.dir / "audio" / "warm.wav", value=3000)
        (self.dir / "model").mkdir()
        (self.dir / "model" / "config.json").write_text("{}", encoding="utf-8")
        write_manifest(self.dir / "m.json",
                       [{"id": "s1", "audio": "audio/a.wav", "reference": "안녕하세요", "condition": "clean"},
                        {"id": "s2", "audio": "audio/b.wav", "reference": "반갑습니다", "condition": "noisy"}])
        FakeBackend.texts = {stt.sha256_file(self.a): "안녕하세요",
                             stt.sha256_file(self.b): "반가습니다",
                             stt.sha256_file(self.warm): "예열"}
        FakeBackend.closed, FakeBackend.fail_load, FakeBackend.fail_close = 0, False, False

    def args(self, **overrides):
        base = dict(backend="fake", model=self.dir / "model", manifest=self.dir / "m.json",
                    output=self.dir / "out" / "report.json", warmup_audio=self.warm,
                    repeats=2, limit=None, compute_type="float16", include_transcripts=False,
                    gpu_memory_budget_mib=None)
        base.update(overrides)
        return Namespace(**base)

    def run_tool(self, **overrides):
        args = self.args(**overrides)
        self.stdout = io.StringIO()
        with mock.patch.dict(stt.BACKENDS, {"fake": FakeBackend}), redirect_stdout(self.stdout):
            code = stt.run(args)
        return code, json.loads(args.output.read_text(encoding="utf-8"))

    def test_successful_run_reports_complete_and_counts_input_files_not_recordings(self):
        code, report = self.run_tool()
        self.assertEqual(code, 0)
        self.assertTrue(report["complete"])
        self.assertEqual(report["counts"]["unique_input_files"], 2)
        self.assertEqual(report["counts"]["repeat_inferences"], 4)
        self.assertEqual(report["counts"]["scored_input_files"], 2)
        self.assertEqual(report["counts"]["unscored_input_files"], 0)
        # 원본 녹음 수는 추정하지 않는다. manifest가 선언하지 않으면 null이다.
        self.assertIsNone(report["counts"]["declared_source_recordings"])
        self.assertNotIn("new_recordings", report["counts"])
        self.assertEqual(len(report["rounds"]), 2)
        self.assertEqual(report["accuracy"]["basis"], "round_1")
        self.assertEqual(report["accuracy"]["micro_cer"], round(1/10, 4))

    def test_condition_breakdown_separates_clean_from_noisy(self):
        _code, report = self.run_tool()
        groups = {group["condition"]: group for group in report["by_condition"]}
        self.assertEqual(sorted(groups), ["clean", "noisy"])
        self.assertEqual(groups["clean"]["accuracy"]["micro_cer"], 0.0)
        self.assertEqual(groups["noisy"]["accuracy"]["micro_cer"], 0.2)
        self.assertEqual(groups["noisy"]["input_files"], 1)
        self.assertEqual(report["samples"][0]["condition"], "clean")

    def test_declared_source_recordings_is_carried_through_without_being_invented(self):
        write_manifest(self.dir / "m2.json",
                       [{"id": "s1", "audio": "audio/a.wav", "reference": "안녕하세요"},
                        {"id": "s2", "audio": "audio/b.wav", "reference": "반갑습니다"}])
        body = json.loads((self.dir / "m2.json").read_text(encoding="utf-8"))
        body["source_recordings"] = 1
        (self.dir / "m2.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
        _code, report = self.run_tool(manifest=self.dir / "m2.json", output=self.dir / "o3" / "r.json")
        self.assertEqual(report["counts"]["declared_source_recordings"], 1)
        self.assertEqual(report["counts"]["unique_input_files"], 2)

    def test_report_records_reproducibility_and_the_no_preprocessing_policy(self):
        _code, report = self.run_tool()
        self.assertEqual(report["schema"], stt.SCHEMA)
        self.assertEqual(report["normalization"]["name"], stt.NORMALIZATION)
        self.assertEqual(report["audio_policy"]["vad_applied"], False)
        self.assertEqual(report["audio_policy"]["denoise_applied"], False)
        self.assertEqual(report["audio_policy"]["dialogue_prompt_applied"], False)
        self.assertEqual(report["model"]["file_count"], 1)
        self.assertEqual(len(report["manifest"]["sha256"]), 64)
        self.assertEqual(len(report["samples"][0]["audio_sha256"]), 64)
        self.assertIsNotNone(report["model_load_seconds"])
        self.assertIsNotNone(report["warmup"]["seconds"])
        self.assertFalse(report["warmup"]["counted_in_latency"])
        self.assertEqual(report["latency"]["overall"]["inferences"], 4)

    def test_progress_output_carries_only_ids_and_numbers(self):
        self.run_tool()
        printed = self.stdout.getvalue()
        self.assertIn("completed 2 / 2", printed)
        for secret in ("반가습니다", "안녕하세요", "예열", str(self.a)):
            self.assertNotIn(secret, printed)

    def test_transcripts_are_omitted_unless_requested(self):
        _code, report = self.run_tool()
        body = json.dumps(report, ensure_ascii=False)
        self.assertNotIn("반가습니다", body)
        self.assertNotIn("안녕하세요", body)
        self.assertFalse(report["transcripts_included"])
        _code, opened = self.run_tool(include_transcripts=True, output=self.dir / "out2" / "r.json")
        self.assertIn("반가습니다", json.dumps(opened, ensure_ascii=False))
        self.assertTrue(opened["transcripts_included"])

    def test_inference_failure_fails_the_run_and_hides_the_exception_message(self):
        FakeBackend.texts[stt.sha256_file(self.b)] = "__raise__"
        code, report = self.run_tool()
        self.assertEqual(code, 1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["failure_count"], 2)
        self.assertEqual(report["failures"][0]["code"], "inference_failed")
        self.assertEqual(report["failures"][0]["type"], "RuntimeError")
        self.assertNotIn("secret transcript", json.dumps(report, ensure_ascii=False))
        self.assertEqual(report["accuracy"]["failed_samples"], 1)

    def test_backend_is_closed_even_when_a_sample_fails(self):
        FakeBackend.texts[stt.sha256_file(self.b)] = "__raise__"
        self.run_tool()
        self.assertEqual(FakeBackend.closed, 1)

    def test_existing_output_is_never_overwritten(self):
        (self.dir / "out").mkdir()
        (self.dir / "out" / "report.json").write_text("keep", encoding="utf-8")
        with self.assertRaises(FileExistsError): self.run_tool()
        self.assertEqual((self.dir / "out" / "report.json").read_text(encoding="utf-8"), "keep")

    def test_invalid_repeats_and_limit_are_rejected_before_loading_a_model(self):
        for overrides in ({"repeats": 0}, {"repeats": stt.MAX_REPEATS + 1}, {"limit": 0},
                          {"gpu_memory_budget_mib": 0}):
            with self.subTest(**overrides), self.assertRaises(ValueError):
                self.run_tool(**overrides)

    def test_memory_budget_is_refused_for_backends_that_cannot_honour_it(self):
        with self.assertRaises(ValueError) as caught:
            self.run_tool(gpu_memory_budget_mib=6500)
        self.assertIn("qwen", str(caught.exception))
        self.assertFalse((self.dir / "out" / "report.json").exists())

    def test_model_load_failure_still_writes_an_incomplete_report(self):
        FakeBackend.fail_load = True
        code, report = self.run_tool()
        self.assertEqual(code, 1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["failed_stages"], ["model_load"])
        self.assertEqual(report["failures"][0]["code"], "model_load_failed")
        self.assertEqual(report["failures"][0]["type"], "RuntimeError")
        self.assertNotIn("secret", json.dumps(report, ensure_ascii=False))
        # 점수 없는 입력이 보고서에 드러나야 한다.
        self.assertEqual(report["counts"]["unscored_input_files"], 2)
        self.assertEqual(report["counts"]["repeat_inferences"], 0)
        self.assertIsNotNone(report["gpu"])
        # 결과가 없는 보고서가 만점처럼 읽히지 않아야 한다.
        self.assertIsNone(report["accuracy"]["micro_cer"])
        self.assertEqual(report["accuracy"]["scored_samples"], 0)

    def test_warmup_failure_is_reported_as_its_own_stage(self):
        FakeBackend.texts[stt.sha256_file(self.warm)] = "__raise__"
        code, report = self.run_tool()
        self.assertEqual(code, 1)
        self.assertEqual(report["failed_stages"], ["warmup"])
        self.assertEqual(report["failures"][0]["code"], "warmup_failed")
        self.assertIsNone(report["warmup"]["seconds"])

    def test_close_failure_is_recorded_without_hiding_the_earlier_failure(self):
        FakeBackend.texts[stt.sha256_file(self.b)] = "__raise__"
        FakeBackend.fail_close = True
        code, report = self.run_tool()
        self.assertEqual(code, 1)
        self.assertEqual(report["failed_stages"], ["inference", "close"])
        self.assertEqual({failure["code"] for failure in report["failures"]},
                         {"inference_failed", "close_failed"})
        self.assertNotIn("cleanup secret", json.dumps(report, ensure_ascii=False))

    def test_a_close_only_failure_also_fails_the_run(self):
        FakeBackend.fail_close = True
        code, report = self.run_tool()
        self.assertEqual(code, 1)
        self.assertFalse(report["complete"])
        self.assertEqual(report["failed_stages"], ["close"])

    def test_every_round_failing_leaves_the_sample_unscored_and_visible(self):
        for sha in (stt.sha256_file(self.a), stt.sha256_file(self.b)):
            FakeBackend.texts[sha] = "__raise__"
        code, report = self.run_tool()
        self.assertEqual(code, 1)
        self.assertEqual(report["counts"]["scored_input_files"], 0)
        self.assertEqual(report["counts"]["unscored_input_files"], 2)
        self.assertEqual(report["accuracy"]["failed_samples"], 2)
        self.assertIsNone(report["accuracy"]["micro_cer"])

    def test_warmup_audio_must_differ_from_every_evaluation_sample(self):
        with self.assertRaises(ValueError):
            self.run_tool(warmup_audio=self.a)

    def test_offline_environment_is_set_before_the_model_loads(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            for name in stt.OFFLINE_ENV: os.environ.pop(name, None)
            seen = {}
            original = FakeBackend.__init__

            def record(self, model_path, options):
                seen.update({name: os.environ.get(name) for name in stt.OFFLINE_ENV})
                original(self, model_path, options)
            with mock.patch.object(FakeBackend, "__init__", record):
                _code, report = self.run_tool()
        self.assertEqual(seen, dict(stt.OFFLINE_ENV))
        self.assertEqual(report["environment"]["offline_env"], dict(stt.OFFLINE_ENV))

    def test_backend_receives_the_selected_options(self):
        self.run_tool()
        self.assertEqual(FakeBackend.options,
                         {"compute_type": "float16", "gpu_memory_budget_mib": None})

    def test_unstable_output_across_rounds_is_visible(self):
        _code, report = self.run_tool()
        self.assertTrue(all(sample["stable_across_rounds"] for sample in report["samples"]))
        self.assertEqual(report["accuracy"]["unstable_samples"], 0)

    def test_missing_model_path_stops_the_run(self):
        with self.assertRaises(FileNotFoundError):
            self.run_tool(model=self.dir / "no-model")


class ModuleImportTests(unittest.TestCase):
    def test_torch_is_not_imported_at_module_level(self):
        tree = ast.parse(Path(stt.__file__).read_text(encoding="utf-8"))
        names = set()
        for node in tree.body:
            if isinstance(node, ast.Import): names.update(alias.name.split(".")[0] for alias in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        for heavy in ("torch", "faster_whisper", "qwen_asr", "sherpa_onnx", "transformers"):
            self.assertNotIn(heavy, names)
        self.assertIn("numpy", names)


if __name__ == "__main__":
    unittest.main()
