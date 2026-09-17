"""잡음이 문자로 전사됐을 때만 조용히 거절하는지 검사한다.

지표 숫자는 메인이 2026-09-17 에 운영과 같은 Whisper large-v3 float16 옵션과 실제
Silero 로 잰 값이다(tools/_work/input_style_fix_20260917/asr-quality.json, 공개·합성
자료의 56 가지 입력 조건 ×3 회. 서로 다른 원본 56 개가 아니라 잡음·감쇠 변형을 포함한
조건 수다). 여기의 통과는 **그 표본에서 정상은 남고 잡음은 걸린다**는 뜻이지
임계값이 일반적으로 옳다는 뜻이 아니다. 실제 분포 검토는 운영 로그로 한다.
"""
import logging
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dialogue_diagnostics import LOGGER_NAME, NUMBERS, REASONS, ConnectionDiagnostics
from speech_gate import (WEAK_AVG_LOGPROB, WEAK_NO_SPEECH, quality_summary,
                         weak_transcript)


def segment(chars=6, **values):
    return dict(values, chars=chars)


# 기존 gate 를 통과한 잡음(-35 dBFS). white·clicks·rustle 의 실측값이다.
NOISE = ((-.486, .789), (-.466, .862), (-.577, .857), (-.474, .800), (-.473, .781))
# 정상 발화의 실측값. "으음" 은 avg_logprob 가 잡음보다도 낮아 지표 하나로는 못 가른다.
GOOD = ((-.835, .603), (-.292, .585), (-.100, .337), (-.504, .059), (-.210, .106))
# 실제 PCM 이 WebRTC→말끝→Silero 를 거쳐 **잘린 뒤** 전사된 같은 맞장구 "으음".
# 전체 클립(.603)과 값이 다르다. no_speech 기준이 .7 이던 때 3/3 거절된 회귀다.
SPLIT_ACK = (-0.791015625, 0.7021484375)


class WeakTranscriptTests(unittest.TestCase):
    def test_measured_normal_utterances_are_kept(self):
        for logprob, no_speech in GOOD:
            with self.subTest(avg_logprob=logprob, no_speech_prob=no_speech):
                quality = [segment(2, avg_logprob=logprob, no_speech_prob=no_speech)]
                self.assertEqual(weak_transcript("발화", quality), "")

    def test_measured_noise_that_passed_the_acoustic_gate_is_refused(self):
        for logprob, no_speech in NOISE:
            with self.subTest(avg_logprob=logprob, no_speech_prob=no_speech):
                quality = [segment(2, avg_logprob=logprob, no_speech_prob=no_speech)]
                self.assertEqual(weak_transcript("전사", quality), "weak_text")

    def test_split_candidate_acknowledgement_is_kept(self):
        # 후보가 잘린 실제 입력의 맞장구다. 이 수치를 거절하면 회귀다.
        quality = [segment(2, avg_logprob=SPLIT_ACK[0], no_speech_prob=SPLIT_ACK[1])]
        self.assertEqual(weak_transcript("으음", quality), "")

    def test_both_indicators_must_reach_their_thresholds(self):
        weakest_good = SPLIT_ACK[1]
        for logprob, no_speech, expected in (
                (WEAK_AVG_LOGPROB, WEAK_NO_SPEECH, "weak_text"),      # 두 기준을 모두 만족
                (WEAK_AVG_LOGPROB, weakest_good, ""),                 # no_speech 가 기준 아래
                (WEAK_AVG_LOGPROB + .01, WEAK_NO_SPEECH, ""),         # avg_logprob 가 기준 위
                (-.9, WEAK_NO_SPEECH - .0001, "")):                   # 경계 바로 아래
            with self.subTest(avg_logprob=logprob, no_speech_prob=no_speech):
                quality = [segment(2, avg_logprob=logprob, no_speech_prob=no_speech)]
                self.assertEqual(weak_transcript("전사", quality), expected)

    def test_missing_quality_keeps_the_previous_behaviour(self):
        # SenseVoice·모의 frontend·빈 오디오에는 지표가 없다. 그때는 거절하지 않는다.
        for quality in (None, [], (), [{}], [{"chars": 4}]):
            with self.subTest(quality=quality):
                self.assertEqual(weak_transcript("그.", quality), "")

    def test_text_shape_alone_never_refuses(self):
        # 마이크 점검·음절 연습도 정당한 발화다. 지표 없이 모양만으로 버리지 않는다.
        for text in ("아아아아아아아아", "ㅋㅋㅋㅋㅋㅋ", "네네네네네네", "테스트 테스트 테스트"):
            with self.subTest(text=text):
                self.assertEqual(weak_transcript(text, None), "")
                self.assertEqual(
                    weak_transcript(text, [segment(8, avg_logprob=-.21, no_speech_prob=.02)]), "")

    def test_one_good_segment_saves_the_whole_candidate(self):
        mixed = [segment(2, avg_logprob=NOISE[0][0], no_speech_prob=NOISE[0][1]),
                 segment(20, avg_logprob=GOOD[1][0], no_speech_prob=GOOD[1][1])]
        self.assertEqual(weak_transcript("네 그때 사진을 같이 봤어요", mixed), "")

    def test_every_text_segment_must_be_weak(self):
        both = [segment(2, avg_logprob=NOISE[1][0], no_speech_prob=NOISE[1][1]),
                segment(3, avg_logprob=NOISE[2][0], no_speech_prob=NOISE[2][1])]
        self.assertEqual(weak_transcript("전사", both), "weak_text")

    def test_blank_segments_are_not_evidence_on_either_side(self):
        blank_weak = [segment(0, avg_logprob=NOISE[0][0], no_speech_prob=NOISE[0][1]),
                      segment(20, avg_logprob=GOOD[2][0], no_speech_prob=GOOD[2][1])]
        self.assertEqual(weak_transcript("민수야", blank_weak), "")
        blank_good = [segment(0, avg_logprob=GOOD[2][0], no_speech_prob=GOOD[2][1]),
                      segment(2, avg_logprob=NOISE[3][0], no_speech_prob=NOISE[3][1])]
        self.assertEqual(weak_transcript("전사", blank_good), "weak_text")

    def test_unmeasured_or_out_of_range_segments_preserve_the_candidate(self):
        weak = {"avg_logprob": NOISE[0][0], "no_speech_prob": NOISE[0][1]}
        for broken in ([segment(2, **weak), segment(2, no_speech_prob=.9)],
                       [segment(2, **weak), segment(2, avg_logprob=-.9)],
                       [segment(2, avg_logprob=float("-inf"), no_speech_prob=float("nan"))],
                       [segment(2, avg_logprob=-.9, no_speech_prob=1.4)],
                       [segment(2, avg_logprob=.5, no_speech_prob=.9)],
                       [segment(2, avg_logprob="-0.9", no_speech_prob=True)],
                       [dict(weak)],                       # chars 없음
                       [segment(None, **weak)],            # chars 가 숫자가 아님
                       [segment(2, **weak), segment(-1, **weak)],    # chars 가 음수
                       [segment(2, **weak), segment(.5, **weak)],    # chars 가 정수가 아님
                       [segment(2, **weak), segment(True, **weak)],  # bool 은 개수가 아니다
                       [segment(2, **weak), segment("2", **weak)],   # chars 가 문자열
                       [segment(2, **weak), "세그먼트가 아님"]):
            with self.subTest(broken=broken):
                self.assertEqual(weak_transcript("전사", broken), "")
        self.assertEqual(weak_transcript(None, [segment(2, **weak)]), "")

    def test_malformed_quality_shapes_are_safe(self):
        # 목록이 아니거나 순회할 수 없는 자료로도 후보를 버리지 않는다.
        for shape in (5, 2.5, "세그먼트", b"x", {"chars": 2}, {"a": {"chars": 2}}):
            with self.subTest(shape=shape):
                self.assertEqual(weak_transcript("전사", shape), "")


class QualitySummaryTests(unittest.TestCase):
    def test_summary_reports_the_weakest_segment_and_omits_unmeasured_items(self):
        summary = quality_summary([segment(2, avg_logprob=-.90, no_speech_prob=.80),
                                   segment(20, avg_logprob=-.10, no_speech_prob=.05,
                                           compression_ratio=1.4),
                                   segment(0, avg_logprob=-2.0, no_speech_prob=.99)])
        self.assertEqual(summary["text_segments"], 2)
        self.assertEqual(summary["weak_segments"], 1)
        self.assertAlmostEqual(summary["no_speech_pct"], 80.0)
        self.assertAlmostEqual(summary["avg_logprob_x100"], -90.0)
        self.assertAlmostEqual(summary["compression_x100"], 140.0)
        bare = quality_summary([segment(2, no_speech_prob=.8)])
        self.assertNotIn("avg_logprob_x100", bare)
        self.assertNotIn("compression_x100", bare)
        self.assertEqual(quality_summary(None), {"text_segments": 0, "weak_segments": 0})

    def test_summary_keys_and_reason_are_whitelisted_for_diagnostics(self):
        self.assertIn("weak_text", REASONS)
        self.assertLessEqual(set(quality_summary([segment(2, avg_logprob=-.2,
                                                          no_speech_prob=.1,
                                                          compression_ratio=1.1)])), NUMBERS)

    def test_summary_is_safe_on_malformed_quality(self):
        empty = {"text_segments": 0, "weak_segments": 0}
        weak = {"avg_logprob": NOISE[0][0], "no_speech_prob": NOISE[0][1]}
        for shape in (5, 2.5, "세그먼트", {"chars": 2},
                      [segment(-1, **weak)], [segment(.5, **weak)],
                      [segment(True, **weak)], [segment("2", **weak)]):
            with self.subTest(shape=shape):
                self.assertEqual(quality_summary(shape), empty)

    def test_counts_are_an_aggregate_and_not_the_verdict(self):
        # 글자 수를 모르는 세그먼트는 집계에서 빠진다. 그래서 두 수가 같아도
        # 실제 판정은 보존일 수 있다. 판정은 weak_transcript 만 내린다.
        weak = {"avg_logprob": NOISE[0][0], "no_speech_prob": NOISE[0][1]}
        quality = [segment(2, **weak), segment(-1, **weak)]
        summary = quality_summary(quality)
        self.assertEqual(summary["weak_segments"], summary["text_segments"])
        self.assertEqual(weak_transcript("전사", quality), "")


class DiagnosticsLineTests(unittest.TestCase):
    """채택과 거절 양쪽에 숫자가 남고 원문은 어디에도 없는지 본다."""

    def setUp(self):
        self.lines = []
        handler = logging.Handler()
        handler.emit = lambda record: self.lines.append(record.getMessage())
        log = logging.getLogger(LOGGER_NAME)
        # 전역 singleton 로거다. 이 검사가 바꾼 수준도 끝나면 되돌린다.
        level = log.level
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        self.addCleanup(log.setLevel, level)
        self.addCleanup(log.removeHandler, handler)

    def test_accepted_and_refused_inputs_both_carry_numbers_only(self):
        diagnostics = ConnectionDiagnostics("0123456789ab")
        good = [{"chars": 4, "no_speech_prob": .059, "avg_logprob": -.504,
                 "compression_ratio": 1.1}]
        noise = [{"chars": 2, "no_speech_prob": NOISE[0][1], "avg_logprob": NOISE[0][0]}]
        diagnostics.event("input.verified", "closed", candidate=1, asr_ms=410.0,
                          **quality_summary(good))
        diagnostics.count("weak_text")
        diagnostics.event("input.rejected", "weak_text", candidate=2,
                          **quality_summary(noise))
        verified, rejected = self.lines[-2], self.lines[-1]
        self.assertIn("no_speech_pct=5.9", verified)
        self.assertIn("avg_logprob_x100=-50.4", verified)
        self.assertIn("weak_segments=0", verified)
        self.assertIn("reason=weak_text", rejected)
        self.assertIn("no_speech_pct=78.9", rejected)
        self.assertIn("avg_logprob_x100=-48.6", rejected)
        self.assertIn("weak_segments=1", rejected)
        self.assertIn("weak_text=1", rejected)          # 연결 누계 계수
        # 재지 못한 지표는 줄에서 빠진다. 0 으로 채우지 않는다.
        self.assertNotIn("compression_x100", rejected)


if __name__ == "__main__":
    unittest.main()
