"""보고 도구가 합성 로그를 잘못 읽지 않는지 확인한다.

실제 체험 로그나 서버 파일은 읽지 않는다. 모든 자료는 이 파일에서 만든 합성 값이고
SSH 는 가짜 실행기로 대신한다. 구현을 그대로 따라 적기보다, 보고서를 읽는 사람이
오해할 만한 지점(기록 없음을 0 으로 읽기, 서버 응답 종료를 재생 완료로 읽기,
누적 카운터를 이번 턴 값으로 읽기)을 막는 데 초점을 둔다.
"""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import analyze_dialogue_trace as tool

TRACE = "0123456789ab"


def unity_line(event, ms, reason=None, version=2, **values):
    record = {"t": "2026-09-16T00:00:%06.3fZ" % (ms / 1000.0), "ms": ms,
              "trace": TRACE, "event": event}
    if reason:
        record["reason"] = reason
    if version:
        record["v"] = version
    record.update(values)
    return json.dumps(record, ensure_ascii=False)


def server_line(event, uptime, reason=None, version="dialogue_diag_v4", **values):
    fields = ["v=" + version, "trace=" + TRACE, "uptime_s=%s" % uptime]
    fields += ["%s=%s" % pair for pair in sorted(values.items())]
    fields.append("event=" + event)
    if reason:
        fields.append("reason=" + reason)
    return "2026-09-16 00:00:%06.3fZ INFO %s" % (uptime, " ".join(fields))


def normal_unity(turn=1):
    """실제 producer(unity-proposal DialogueTraceLog/Diagnostics)가 쓰는 사건·코드만 쓴다."""
    return [
        unity_line("experience.start", 0, "registered", tts=1, dsp_rate=48000),
        unity_line("input.started", 500, turn=0, input_turn=turn),
        unity_line("input.stopped", 2100, turn=0, input_turn=turn),
        unity_line("input.transcript", 2400, "final", turn=0, input_turn=turn, chars=14, final=1),
        unity_line("response.started", 2800, "normal", turn=turn, received=0, consumed=0),
        unity_line("audio.first_received", 3000, "answer", turn=turn, input_turn=turn,
                   response_seq=1, kind=0, received=4800),
        unity_line("playback.first_render", 3060, turn=turn, input_turn=turn,
                   response_seq=1, receive_to_render_ms=60, rendered=1024),
        unity_line("audio.boundary", 5000, "answer", turn=turn, input_turn=turn,
                   boundary=1, expected_samples=48000, received=48000, chars=14, kind=0),
        unity_line("response.done", 5600, turn=turn, received=96000, consumed=90000),
        unity_line("playback.completed", 6200, "playback_done", turn=turn, input_turn=turn,
                   response_seq=1, received=96000, consumed=96000, expected_samples=96000,
                   remaining=0, rendered=96000, underrun_frames=0, rebuffers=0, callbacks=180,
                   boundary=1, complete=1, ended=1, paused=0),
        unity_line("experience.end", 7000, "unspecified"),
    ]


def normal_server(turn=1, candidate=3):
    return [
        server_line("connection.open", 0.1, "registered", turn=0),
        server_line("input.candidate", 0.5, "started", turn=0, candidate=candidate),
        server_line("input.verified", 2.3, "closed", turn=0, candidate=candidate, asr_ms=410),
        server_line("input.candidate", 2.35, "accepted", turn=0, candidate=candidate),
        server_line("input.assigned", 2.36, "accepted", turn=turn, candidate=candidate),
        server_line("turn.started", 2.4, "normal", turn=turn),
        server_line("route.done", 2.5, "normal", turn=turn, route_ms=90),
        server_line("llm.first_text", 3.0, turn=turn, first_text_ms=520, llm_first_ms=310),
        server_line("tts.phrase_started", 3.1, "answer", turn=turn, phrase=1, chars=14),
        server_line("tts.phrase_first_audio", 3.3, "answer", turn=turn, phrase=1,
                    samples=2400, tts_first_ms=200),
        server_line("tts.phrase_done", 5.0, "answer", turn=turn, phrase=1, packets=40,
                    samples=96000, tts_first_ms=200, tts_total_ms=1700, tts_max_gap_ms=120),
        server_line("turn.timing", 5.1, turn=turn, first_text_sec=0.6, first_audio_sec=0.9,
                    first_reaction_audio_sec=0.3, first_any_audio_sec=0.3, total_sec=2.7),
        server_line("turn.done", 5.2, "normal", turn=turn, resp_samples=96000, resp_packets=40),
    ]


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write_unity(self, lines, newline=True, name=None):
        path = self.root / (name or ("dialogue-20260916T000000Z-%s.jsonl" % TRACE))
        path.write_text("\n".join(lines) + ("\n" if newline else ""), encoding="utf-8")
        return path

    def write_server(self, lines):
        path = self.root / "dialogue-diagnostics.log"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def run_tool(self, unity_lines, server_lines=None, output="out", **kwargs):
        unity = self.write_unity(unity_lines)
        server = self.write_server(server_lines) if server_lines is not None else None
        markdown, report = tool.analyze(unity, server, output=self.root / output, **kwargs)
        return markdown, report

    def turn(self, report, number=1):
        for row in report["turns"]:
            if row["turn"] == number:
                return row
        self.fail("turn %s missing from the report" % number)

    def codes(self, report):
        return {item["code"] for item in report["signals"]}


class NormalTurnTests(Base):
    def test_complete_turn_needs_both_playback_completed_and_server_turn_done(self):
        _, report = self.run_tool(normal_unity(), normal_server())
        row = self.turn(report)
        self.assertEqual(row["status"], "complete")
        self.assertTrue(report["completeness"]["server_checked"])
        self.assertTrue(report["completeness"]["experience_ended"])
        self.assertTrue(row["sample_match"]["checked"])
        self.assertTrue(row["sample_match"]["match"])
        self.assertNotIn("missing_completion", self.codes(report))

    def test_server_turn_done_alone_is_not_reported_as_playback_finished(self):
        unity = [line for line in normal_unity() if "playback.completed" not in line]
        _, report = self.run_tool(unity, normal_server())
        row = self.turn(report)
        self.assertEqual(row["status"], "server_done_playback_unconfirmed")
        self.assertFalse(row["sample_match"]["checked"])
        self.assertIsNone(row["sample_match"]["match"])
        self.assertEqual(row["sample_match"]["reason"], "insufficient_observation")

    def test_reported_seconds_stay_separate_from_unity_playback_numbers(self):
        _, report = self.run_tool(normal_unity(), normal_server())
        row = self.turn(report)
        self.assertEqual(row["server"]["total_sec"], 2.7)
        self.assertEqual(row["server"]["first_reaction_audio_sec"], 0.3)
        self.assertEqual(row["unity"]["receive_to_render_ms"], 60)
        self.assertEqual(row["unity"]["first_audio_kind"], "answer")
        self.assertEqual(report["completeness"]["clock_alignment"], "unverified")

    def test_markdown_and_json_are_written_together(self):
        markdown, report = self.run_tool(normal_unity(), normal_server())
        self.assertTrue(markdown.is_file())
        saved = json.loads((markdown.parent / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["trace"], TRACE)
        self.assertEqual(saved["schema_version"], report["schema_version"])
        self.assertIn("대화 진단 보고서", markdown.read_text(encoding="utf-8"))


class MissingDataTests(Base):
    def test_without_server_log_the_report_says_unchecked_instead_of_zero(self):
        _, report = self.run_tool(normal_unity())
        self.assertFalse(report["completeness"]["server_checked"])
        self.assertIn("server_unchecked", self.codes(report))
        row = self.turn(report)
        self.assertIsNone(row["server"]["sent_samples"])
        self.assertIsNone(row["server"]["total_sec"])
        self.assertIsNone(row["server"]["llm_first_ms"])
        self.assertFalse(row["sample_match"]["checked"])
        self.assertNotEqual(row["status"], "complete")

    def test_local_report_still_knows_the_mode_from_response_started(self):
        markdown, report = self.run_tool(normal_unity())
        row = self.turn(report)
        self.assertEqual(row["mode"], "normal")
        self.assertEqual(row["mode_source"], "unity_response_started")
        self.assertEqual(row["unity"]["stop_reason"], None)
        self.assertEqual([item["stage"] for item in row["unity"]["lifecycle"]],
                         ["started", "done"])
        self.assertIn("started → done", markdown.read_text(encoding="utf-8"))

    def test_stt_is_unconfirmed_rather_than_zero_when_no_mapping_exists(self):
        markdown, report = self.run_tool(normal_unity())
        row = self.turn(report)
        self.assertFalse(row["stt"]["mapped"])
        self.assertIsNone(row["stt"]["asr_max_ms"])
        self.assertIn("미확인", markdown.read_text(encoding="utf-8"))

    def test_missing_values_render_as_dash_not_zero(self):
        markdown, _ = self.run_tool(normal_unity())
        text = markdown.read_text(encoding="utf-8")
        self.assertIn("아니오(서버 미확인)", text)
        self.assertIn("—", text)

    def test_ongoing_experience_is_not_called_complete(self):
        unity = [line for line in normal_unity() if "experience.end" not in line]
        _, report = self.run_tool(unity, normal_server())
        self.assertFalse(report["completeness"]["experience_ended"])
        self.assertIn("experience_ongoing", self.codes(report))

    def test_turn_without_audio_or_completion_is_flagged_as_unobserved(self):
        unity = normal_unity()[:5] + [unity_line("experience.end", 7000, "unspecified")]
        _, report = self.run_tool(unity, normal_server()[:6])
        row = self.turn(report)
        self.assertEqual(row["status"], "incomplete_or_unobserved")
        self.assertIn("missing_completion", self.codes(report))
        self.assertIn("no_audio_received", self.codes(report))

    def test_empty_server_file_is_reported_as_empty_not_as_healthy(self):
        _, report = self.run_tool(normal_unity(), [])
        self.assertIn("server_empty", self.codes(report))
        self.assertEqual(report["sources"]["server"]["used"], 0)


class DamagedInputTests(Base):
    def test_truncated_and_invalid_lines_are_counted_and_flagged(self):
        lines = normal_unity()[:-1] + ['{"t":"2026-09-16T00:00:07Z","ms":7000,"trace":"']
        markdown, report = self.run_tool(lines, normal_server())
        unity = report["sources"]["unity"]
        self.assertEqual(unity["invalid_lines"], 1)
        self.assertIn("log_truncated", self.codes(report))
        self.assertIn("log_truncated", markdown.read_text(encoding="utf-8"))

    def test_lines_from_another_trace_are_excluded_and_counted(self):
        other = json.dumps({"t": "2026-09-16T00:00:01Z", "ms": 1000, "trace": "ffffffffffff",
                            "event": "playback.completed", "received": 999999})
        _, report = self.run_tool(normal_unity() + [other], normal_server())
        self.assertEqual(report["sources"]["unity"]["other_trace_lines"], 1)
        self.assertEqual(self.turn(report)["unity"]["received"], 96000)

    def test_non_finite_numbers_and_free_text_fields_never_reach_the_report(self):
        noisy = ('{"t":"2026-09-16T00:00:04Z","ms":4000,"trace":"%s","event":"playback.sample",'
                 '"received":NaN,"consumed":1e9999,"turn":1,'
                 '"transcript":"\\uc548\\ub155 \\ud558\\uc138\\uc694","session":"abcd-1234",'
                 '"token":"secret-value","path":"C:/Users/someone/logs"}' % TRACE)
        markdown, report = self.run_tool(normal_unity() + [noisy], normal_server())
        blob = json.dumps(report, ensure_ascii=False) + markdown.read_text(encoding="utf-8")
        for leaked in ("secret-value", "abcd-1234", "C:/Users/someone", "안녕 하세요"):
            self.assertNotIn(leaked, blob)
        self.assertNotIn("NaN", blob)
        self.assertNotIn("Infinity", blob)

    def test_unknown_event_and_reason_codes_are_folded_and_counted(self):
        odd = unity_line("playback.exploded", 4500, "because the model said so", turn=1)
        stop = unity_line("playback.stopped", 4600, "brand new reason", turn=1,
                          received=10, consumed=5)
        _, report = self.run_tool(normal_unity() + [odd, stop], normal_server())
        self.assertEqual(report["sources"]["unity"]["unknown_events"], 1)
        self.assertGreaterEqual(report["sources"]["unity"]["unknown_codes"], 1)
        self.assertEqual(self.turn(report)["unity"]["stop_reason"], "other")

    def test_server_free_text_line_is_not_parsed_as_an_event(self):
        lines = normal_server() + ["2026-09-16 00:00:06,000Z ERROR Traceback: user said 비밀"]
        _, report = self.run_tool(normal_unity(), lines)
        self.assertNotIn("비밀", json.dumps(report, ensure_ascii=False))
        self.assertEqual(report["sources"]["server"]["used"], len(normal_server()))


class LegacySchemaTests(Base):
    def test_legacy_unity_v1_and_server_v3_still_produce_a_report(self):
        unity = [unity_line("experience.start", 0, "registered", version=None),
                 unity_line("playback.sample", 4000, "periodic", version=None, turn=1,
                            received=96000, consumed=90000, underrun_frames=7,
                            rebuffers=2, callbacks=180),
                 unity_line("experience.end", 7000, "unspecified", version=None)]
        server = [server_line("turn.started", 2.4, "normal", version="dialogue_diag_v3", turn=1),
                  server_line("turn.done", 5.2, "normal", version="dialogue_diag_v3", turn=1,
                              resp_samples=96000)]
        _, report = self.run_tool(unity, server)
        self.assertIn("legacy_schema", self.codes(report))
        row = self.turn(report)
        self.assertIsNone(row["server"]["total_sec"])
        self.assertIsNone(row["unity"]["first_received_ms"])

    def test_cumulative_counters_are_marked_and_not_read_as_this_turn(self):
        unity = [unity_line("experience.start", 0, "registered", version=None),
                 unity_line("playback.sample", 4000, "periodic", version=None, turn=2,
                            received=200000, consumed=190000, underrun_frames=12),
                 unity_line("experience.end", 7000, "unspecified", version=None)]
        markdown, report = self.run_tool(unity)
        row = self.turn(report, 2)
        self.assertEqual(row["unity"]["underrun_scope"], "experience_cumulative")
        self.assertIn("cumulative_counters_only", self.codes(report))
        self.assertNotIn("underrun_observed", self.codes(report))
        self.assertIn("(체험누적)", markdown.read_text(encoding="utf-8"))

    def test_legacy_log_without_first_received_is_not_called_a_missing_pcm(self):
        """v1 에는 audio.first_received 사건 자체가 없다. 수신 0 이 아니다."""
        unity = [unity_line("experience.start", 0, "registered", version=None),
                 unity_line("response.started", 2800, "normal", version=None, turn=1),
                 unity_line("playback.sample", 4000, "periodic", version=None, turn=1,
                            received=96000, consumed=90000, underrun_frames=0),
                 unity_line("experience.end", 7000, "unspecified", version=None)]
        markdown, report = self.run_tool(unity)
        row = self.turn(report)
        self.assertIsNone(row["unity"]["first_received_ms"])
        self.assertEqual(row["unity"]["sample_received"], 96000)
        self.assertIn("first_audio_not_recorded", self.codes(report))
        self.assertNotIn("no_audio_received", self.codes(report))
        self.assertIn("PCM 이 없었다는 뜻이 아니다", markdown.read_text(encoding="utf-8"))


class TurnShapeTests(Base):
    def test_underrun_on_a_finished_response_is_reported_as_a_response_delta(self):
        unity = [line.replace('"underrun_frames": 0', '"underrun_frames": 9')
                 for line in normal_unity()]
        _, report = self.run_tool(unity, normal_server())
        row = self.turn(report)
        self.assertEqual(row["unity"]["underrun_frames"], 9)
        self.assertEqual(row["unity"]["underrun_scope"], "response")
        self.assertIn("underrun_observed", self.codes(report))
        detail = [item for item in report["signals"] if item["code"] == "underrun_observed"][0]
        self.assertEqual(detail["detail"]["scope"], "response")
        self.assertEqual(detail["confidence"], "observed")

    def test_cancelled_turn_is_not_reported_as_an_audio_loss(self):
        unity = normal_unity()[:6] + [
            unity_line("playback.stopped", 3500, "user_cancel", turn=1, input_turn=1,
                       received=20000, consumed=18000, remaining=2000,
                       underrun_frames=0, complete=0),
            unity_line("experience.end", 7000, "unspecified")]
        server = normal_server()[:5] + [
            server_line("turn.cancelled", 3.4, "user_speech", turn=1, resp_samples=20000)]
        _, report = self.run_tool(unity, server)
        row = self.turn(report)
        self.assertEqual(row["status"], "cancelled")
        self.assertFalse(row["sample_match"]["checked"])
        self.assertNotIn("sample_mismatch", self.codes(report))

    def test_sample_mismatch_is_only_claimed_when_both_sides_finished(self):
        unity = [line.replace('"received": 96000', '"received": 72000')
                 for line in normal_unity()]
        _, report = self.run_tool(unity, normal_server())
        row = self.turn(report)
        self.assertTrue(row["sample_match"]["checked"])
        self.assertFalse(row["sample_match"]["match"])
        self.assertIn("sample_mismatch", self.codes(report))

        partial = [line for line in unity if "playback.completed" not in line]
        _, second = self.run_tool(partial, normal_server(), output="out2")
        self.assertNotIn("sample_mismatch", self.codes(second))

    def test_held_and_resumed_turn_keeps_input_turn_separate_from_response_turn(self):
        unity = [unity_line("experience.start", 0, "registered"),
                 unity_line("input.started", 500, turn=1, input_turn=2),
                 unity_line("audio.first_received", 3000, "answer", turn=1, input_turn=2,
                            response_seq=1),
                 unity_line("playback.first_render", 3060, turn=1, input_turn=2,
                            receive_to_render_ms=60),
                 unity_line("playback.completed", 6000, "playback_done", turn=1, input_turn=2,
                            received=96000, consumed=96000, complete=1, underrun_frames=0),
                 unity_line("experience.end", 7000, "unspecified")]
        server = [server_line("turn.held", 2.0, "hold", turn=1),
                  server_line("turn.resumed", 2.8, "resume", turn=1),
                  server_line("turn.done", 5.2, "normal", turn=1, resp_samples=96000)]
        markdown, report = self.run_tool(unity, server)
        row = self.turn(report)
        self.assertEqual(row["input_turns"], [2])
        self.assertTrue(row["input_turn_mismatch"])
        self.assertIn("input_turn_mismatch", self.codes(report))
        text = markdown.read_text(encoding="utf-8")
        self.assertIn("보류 후 재개면 정상일 수 있다", text)

    def test_reaction_only_turn_is_separated_from_the_answer_audio(self):
        unity = [unity_line("experience.start", 0, "registered"),
                 unity_line("audio.first_received", 2500, "reaction", turn=1, input_turn=1,
                            response_seq=0, kind=1),
                 unity_line("playback.first_render", 2560, turn=1, input_turn=1,
                            receive_to_render_ms=60),
                 unity_line("experience.end", 7000, "unspecified")]
        _, report = self.run_tool(unity, normal_server()[:3])
        row = self.turn(report)
        self.assertIsNone(row["unity"]["first_received_ms"])
        self.assertEqual(row["unity"]["reaction_first_received_ms"], 2500)
        self.assertEqual(row["unity"]["first_audio_kind"], "reaction")
        self.assertIn("reaction_only", self.codes(report))

    def test_user_marker_is_listed_with_its_turn_and_time(self):
        unity = normal_unity()[:6] + [
            unity_line("user.marker", 4200, turn=1, input_turn=1, response_seq=1, marker=1),
            unity_line("playback.completed", 6200, "playback_done", turn=1, input_turn=1,
                       received=96000, consumed=96000, complete=1, underrun_frames=0),
            unity_line("experience.end", 7000, "unspecified")]
        markdown, report = self.run_tool(unity, normal_server())
        self.assertEqual(len(report["markers"]), 1)
        self.assertEqual(report["markers"][0]["turn"], 1)
        self.assertEqual(report["markers"][0]["marker"], 1)
        self.assertIn("user_marker", self.codes(report))
        self.assertIn("사용자 표시", markdown.read_text(encoding="utf-8"))

    def test_input_rejections_are_counted_per_reason_without_a_turn_claim(self):
        server = normal_server() + [
            server_line("input.rejected", 1.0, "no_speech", turn=0),
            server_line("input.rejected", 1.5, "no_speech", turn=0),
            server_line("input.endpoint", 1.8, "silence_tail", turn=0, tail_ms=320)]
        _, report = self.run_tool(normal_unity(), server)
        self.assertEqual(report["inputs"]["rejected"], {"no_speech": 2})
        self.assertEqual(report["inputs"]["endpoint"], {"silence_tail": 1})
        self.assertIn("연결 단위", report["inputs"]["note"])

    def test_weak_input_rejection_keeps_its_own_reason_and_quality_numbers(self):
        # 약한 전사 거절이 other 로 접히면 사용자가 잡음 거절을 구분할 수 없다.
        server = normal_server() + [
            server_line("input.rejected", 1.0, "weak_text", turn=0, candidate=8,
                        no_speech_pct=78.9, avg_logprob_x100=-48.6,
                        text_segments=1, weak_segments=1)]
        _, report = self.run_tool(normal_unity(), server)
        self.assertEqual(report["inputs"]["rejected"], {"weak_text": 1})
        self.assertEqual(report["sources"]["server"]["unknown_codes"], 0)


class ProducerContractTests(Base):
    """실제 producer 가 쓰는 코드가 other 로 접히거나 버려지지 않는지 본다.

    아래 목록은 unity-proposal/Assets/Scripts/Dialogue/DialogueTraceLog.cs 와
    server-proposal/Server/dialogue_diagnostics.py 의 화이트리스트에서 옮겼다.
    producer 가 늘어나면 여기도 함께 늘린다.
    """

    UNITY_EVENTS = (
        "experience.start", "experience.end", "dialogue.fail", "main_thread.stall",
        "audio.config_changed", "audio.first_received", "audio.boundary",
        "playback.sample", "playback.first_render", "playback.boundary",
        "playback.completed", "playback.stopped",
        "playback.paused", "playback.pause_forced",
        "response.started", "response.paused", "response.resumed", "response.done",
        "response.cancelled", "response.timing",
        "input.started", "input.stopped", "input.transcript", "input.sample",
        "output.state", "user.marker", "application.focus", "application.pause", "trace.limit",
    )
    UNITY_REASONS = (
        "test_profile", "registered", "unspecified", "on_disable", "app_pause",
        "session_changed", "begin_failed", "periodic", "frame_gap", "device", "size",
        "client_error", "connection_error", "microphone_stopped", "mic_pump_stall",
        "send_queue_overflow", "playback_overflow",
        "fail_client_error", "fail_connection_error", "fail_microphone_stopped",
        "fail_mic_pump_stall", "fail_send_queue_overflow", "fail_playback_overflow",
        "new_input", "new_response", "user_cancel", "server_cancel", "response_error",
        "reset", "experience_end", "playback_done",
        "normal", "reasoning", "fallback",
        "user_speech", "newer_utterance", "hold_timeout", "memory_forget",
        "turn_failed", "empty_transcript",
        "resume", "revise", "switch", "hold", "clarify",
        "answer", "reaction", "partial", "final",
        "ui_finish", "ui_finish_space", "ui_finish_enter", "ui_finish_spaceenter",
        "control_disabled",
        "phrase", "immediate", "boundary", "grace_expired", "no_audio", "already_paused",
    )
    UNITY_NAMES = (
        "tts", "dsp_rate", "dsp_buffer", "dsp_buffers", "source_rate", "preroll", "sec",
        "turn", "received", "consumed", "buffered", "underrun_frames", "rebuffers",
        "callbacks", "rendered", "ended", "audio_packets", "resp_packets", "resp_audio_span",
        "max_packet_gap", "paused_gap", "max_frame_stall", "cfg_changes", "paused", "listening",
        "input_turn", "response_seq", "kind", "chars", "final", "lines",
        "receive_to_render_ms", "boundary", "expected_samples", "remaining", "complete",
        "tail_rms", "tail_peak", "tail_silence_ms", "saw_answer", "saw_reaction",
        "first_text_sec", "first_audio_sec", "first_reaction_audio_sec",
        "first_any_audio_sec", "total_sec",
        "mic_chunks", "mic_samples", "mic_level", "waiting", "recording", "connected", "active",
        "source_mute", "source_volume", "source_pitch", "source_enabled", "source_playing",
        "listener_pause", "listener_volume", "focus", "marker", "unknown_names",
        "pause_id", "pause_grace_ms", "pause_expected_sample", "pause_stop_sample", "pause_wait_ms",
    )
    SERVER_EVENTS = (
        "connection.open", "connection.close", "input.candidate", "input.rejected",
        "input.verified", "input.endpoint", "turn.started", "turn.done", "turn.cancelled",
        "turn.held", "turn.resumed", "heartbeat",
        "input.assigned", "route.done", "llm.first_text", "tts.phrase_started",
        "tts.phrase_first_audio", "tts.phrase_done", "turn.timing", "playback.ack",
        "answer.fixed",
    )
    SERVER_NUMBERS = (
        "candidate", "turn", "samples", "asr_ms", "seconds", "code",
        "age_ms", "voiced_pct", "max_quiet_ms", "tail_ms",
        "no_speech_pct", "avg_logprob_x100", "compression_x100",
        "text_segments", "weak_segments",
        "route_ms", "first_text_ms", "llm_first_ms", "phrase", "chars", "packets",
        "tts_first_ms", "tts_total_ms", "tts_max_gap_ms",
        "first_text_sec", "first_audio_sec", "first_reaction_audio_sec",
        "first_any_audio_sec", "total_sec", "done",
        "pause_id", "pause_boundary", "pause_cut", "pause_stale", "pause_timeout",
    )

    def test_every_unity_event_the_logger_can_write_is_accepted(self):
        for name in self.UNITY_EVENTS:
            self.assertIn(name, tool.UNITY_EVENTS, name)

    def test_every_unity_reason_and_name_survives_the_whitelist(self):
        for reason in self.UNITY_REASONS:
            self.assertIn(reason, tool.UNITY_REASONS, reason)
        for name in self.UNITY_NAMES:
            self.assertIn(name, tool.UNITY_NUMBERS, name)

    def test_every_server_event_and_number_is_accepted(self):
        for name in self.SERVER_EVENTS:
            self.assertIn(name, tool.SERVER_EVENTS, name)
        for name in self.SERVER_NUMBERS:
            self.assertIn(name, tool.SERVER_NUMBERS, name)

    def test_legacy_response_lifecycle_lines_are_not_discarded(self):
        unity = [unity_line("experience.start", 0, "registered", version=None),
                 unity_line("response.started", 2800, "reasoning", version=None, turn=1),
                 unity_line("response.paused", 3200, version=None, turn=1),
                 unity_line("response.resumed", 3600, version=None, turn=1),
                 unity_line("response.cancelled", 4000, "user_speech", version=None, turn=1),
                 unity_line("experience.end", 7000, "unspecified", version=None)]
        _, report = self.run_tool(unity)
        self.assertEqual(report["sources"]["unity"]["unknown_events"], 0)
        self.assertEqual(report["sources"]["unity"]["unknown_codes"], 0)
        row = self.turn(report)
        self.assertEqual(row["mode"], "reasoning")
        self.assertEqual([item["stage"] for item in row["unity"]["lifecycle"]],
                         ["started", "paused", "resumed", "cancelled"])
        self.assertEqual(row["status"], "cancelled")

    def test_trace_limit_line_marks_the_report_as_partial(self):
        unity = normal_unity()[:5] + [
            unity_line("trace.limit", 6500, "size", lines=4000)]
        markdown, report = self.run_tool(unity)
        self.assertTrue(report["sources"]["unity"]["producer_truncated"])
        self.assertTrue(report["completeness"]["partial"])
        self.assertIn("producer_truncated", self.codes(report))
        self.assertIn("trace.limit", markdown.read_text(encoding="utf-8"))


    def test_ui_finish_and_control_reasons_are_not_folded(self):
        for reason in ("ui_finish", "ui_finish_space", "ui_finish_enter",
                       "ui_finish_spaceenter", "control_disabled"):
            with self.subTest(reason=reason):
                unity = normal_unity()[:-1] + [unity_line("experience.end", 7000, reason)]
                _, report = self.run_tool(unity, output="out-" + reason)
                self.assertEqual(report["sources"]["unity"]["unknown_codes"], 0)
                ends = [row for row in report["playback_stops"]
                        if row["event"] == "experience.end"]
                self.assertEqual(ends[0]["reason"], reason)

    def test_injected_minus_one_means_not_applicable_not_true(self):
        unity = normal_unity() + [unity_line("input.sample", 4000, "periodic", turn=1,
                                             input_turn=1, injected=-1)]
        _, report = self.run_tool(unity)
        self.assertIsNone(self.turn(report)["unity"]["injected"])
        self.assertNotIn("injected_audio", self.codes(report))

    def test_injected_one_marks_the_turn_and_zero_does_not(self):
        on = normal_unity() + [unity_line("input.sample", 4000, "periodic", turn=1, injected=1)]
        _, report = self.run_tool(on)
        self.assertTrue(self.turn(report)["unity"]["injected"])
        self.assertIn("injected_audio", self.codes(report))

        off = normal_unity() + [unity_line("input.sample", 4000, "periodic", turn=1, injected=0)]
        _, second = self.run_tool(off, output="out2")
        self.assertFalse(self.turn(second)["unity"]["injected"])
        self.assertNotIn("injected_audio", self.codes(second))


class PauseBoundaryTests(Base):
    """멈춤 경계 합의 로그가 unknown 으로 접히지 않고, 원문은 여전히 들어오지 않는지 본다.

    값은 producer 화이트리스트에서 옮긴 합성 로그다. 실제 체험 로그가 아니다.
    """

    def unity_pause(self):
        """경계에서 멈추고 보고한 뒤 재개한 회차. 입력 턴이 응답 턴과 다른 것이 정상이다."""
        return normal_unity()[:7] + [
            unity_line("response.paused", 3200, "phrase", turn=1, input_turn=2, response_seq=1,
                       pause_id=4, pause_grace_ms=1200, received=48000, consumed=24000, paused=0),
            unity_line("playback.paused", 3800, "boundary", turn=1, input_turn=2, response_seq=1,
                       pause_id=4, pause_expected_sample=24000, pause_stop_sample=23999,
                       pause_wait_ms=600, chars=7, received=48000, paused=1),
            unity_line("response.resumed", 4200, turn=1, input_turn=2),
        ] + normal_unity()[7:]

    def server_pause(self):
        server = [line for line in normal_server() if "event=turn.done" not in line]
        return server + [
            server_line("playback.ack", 4.6, "paused", turn=1, chars=7, done=0,
                        samples=48000, pause_id=4),
            server_line("turn.done", 5.2, "normal", turn=1, resp_samples=96000, resp_packets=40,
                        pause_boundary=1, pause_cut=0, pause_stale=0, pause_timeout=0),
        ]

    def test_pause_handshake_lines_are_not_folded_into_unknown(self):
        _, report = self.run_tool(self.unity_pause(), self.server_pause())
        unity, server = report["sources"]["unity"], report["sources"]["server"]
        self.assertEqual((unity["unknown_events"], unity["unknown_codes"]), (0, 0))
        self.assertEqual((server["unknown_events"], server["unknown_codes"]), (0, 0))
        row = self.turn(report)
        self.assertEqual([item["stage"] for item in row["unity"]["lifecycle"]],
                         ["started", "paused", "resumed", "done"])

    def test_pause_numbers_reach_the_report_from_both_sides(self):
        _, report = self.run_tool(self.unity_pause(), self.server_pause())
        found = {(event["source"], event["event"], event["reason"]): event["values"]
                 for event in report["events"]}
        request = found[("unity", "response.paused", "phrase")]
        self.assertEqual(request["pause_id"], 4)
        self.assertEqual(request["pause_grace_ms"], 1200)
        ack = found[("unity", "playback.paused", "boundary")]
        self.assertEqual(ack["pause_expected_sample"], 24000)
        self.assertEqual(ack["pause_stop_sample"], 23999)
        self.assertEqual(ack["pause_wait_ms"], 600)
        self.assertEqual(found[("server", "playback.ack", "paused")]["pause_id"], 4)
        done = found[("server", "turn.done", "normal")]
        self.assertEqual(done["pause_boundary"], 1)
        self.assertEqual(done["pause_timeout"], 0)
        # 멈춘 지점 보고가 이 턴의 서버 송신 샘플을 덮어쓰지 않는다.
        self.assertEqual(self.turn(report)["server"]["sent_samples"], 96000)

    def test_a_cut_stop_keeps_its_own_reason_and_no_expected_boundary(self):
        unity = normal_unity()[:7] + [
            unity_line("response.paused", 3200, "immediate", turn=1, input_turn=2, pause_id=5),
            unity_line("playback.pause_forced", 3400, "grace_expired", turn=1, input_turn=2,
                       pause_id=5, pause_stop_sample=31200, pause_wait_ms=200, received=48000),
            unity_line("playback.paused", 3500, "grace_expired", turn=1, input_turn=2, pause_id=5,
                       pause_expected_sample=-1, pause_stop_sample=31200, pause_wait_ms=300,
                       chars=0, received=48000, paused=1),
        ] + normal_unity()[7:]
        markdown, report = self.run_tool(unity)
        self.assertEqual(report["sources"]["unity"]["unknown_events"], 0)
        self.assertEqual(report["sources"]["unity"]["unknown_codes"], 0)
        forced = [row for row in report["playback_stops"]
                  if row["event"] == "playback.pause_forced"]
        self.assertEqual(len(forced), 1)
        self.assertEqual(forced[0]["reason"], "grace_expired")
        values = [event["values"] for event in report["events"]
                  if event["event"] == "playback.paused"][0]
        self.assertEqual(values["pause_expected_sample"], -1)   # 예약 없음이며 0 이 아니다
        self.assertIn("낱말 단위 정지를 뜻하지 않는다", markdown.read_text(encoding="utf-8"))

    def test_pause_lines_never_carry_spoken_text_session_or_urls(self):
        noisy = ('{"t":"2026-09-16T00:00:03.900Z","ms":3900,"trace":"%s","event":"playback.paused",'
                 '"reason":"boundary","turn":1,"pause_id":4,"pause_stop_sample":24000,'
                 '"text":"\\uc548\\ub155 \\ud558\\uc138\\uc694","session":"abcd-1234",'
                 '"url":"ws://192.0.2.8:8002/dialogue"}' % TRACE)
        server = self.server_pause() + [
            "2026-09-16 00:00:04,700Z INFO v=dialogue_diag_v4 trace=%s turn=1 "
            "event=playback.ack reason=paused pause_id=4 text=사용자원문 persona=jiwon_2024" % TRACE]
        markdown, report = self.run_tool(self.unity_pause() + [noisy], server)
        blob = json.dumps(report, ensure_ascii=False) + markdown.read_text(encoding="utf-8")
        for leaked in ("안녕 하세요", "abcd-1234", "192.0.2.8", "사용자원문", "jiwon_2024"):
            self.assertNotIn(leaked, blob)
        allowed = tool.UNITY_NUMBERS | tool.SERVER_NUMBERS
        for event in report["events"]:
            self.assertTrue(set(event["values"]) <= allowed, event["event"])


class EventArchiveTests(Base):
    def test_values_dropped_from_the_tables_are_still_in_the_json(self):
        unity = normal_unity() + [
            unity_line("audio.boundary", 5200, "answer", turn=1, input_turn=1, boundary=2,
                       expected_samples=96000, received=96000, chars=9, kind=0,
                       tail_rms=0.0021, tail_peak=0.013, tail_silence_ms=38),
            unity_line("output.state", 5300, "periodic", turn=1, input_turn=1,
                       source_volume=0.8, source_playing=1, listener_volume=1, focus=0),
            unity_line("input.sample", 5300, "periodic", turn=1, input_turn=1,
                       mic_chunks=120, mic_samples=153600, mic_level=0.04, listening=1)]
        _, report = self.run_tool(unity, normal_server())
        found = {event["event"]: event["values"] for event in report["events"]}
        self.assertEqual(found["audio.boundary"]["tail_silence_ms"], 38)
        self.assertEqual(found["audio.boundary"]["expected_samples"], 96000)
        self.assertEqual(found["output.state"]["source_volume"], 0.8)
        self.assertEqual(found["output.state"]["focus"], 0)
        self.assertEqual(found["input.sample"]["mic_chunks"], 120)
        self.assertTrue(found["input.sample"] and found["output.state"])

    def test_events_carry_both_sources_and_are_time_ordered(self):
        _, report = self.run_tool(normal_unity(), normal_server())
        sources = {event["source"] for event in report["events"]}
        self.assertEqual(sources, {"unity", "server"})
        walls = [event["wall"] for event in report["events"] if event["wall"]]
        self.assertEqual(walls, sorted(walls))
        self.assertTrue(any(event["periodic"] is False for event in report["events"]))

    def test_events_never_carry_raw_text_secrets_or_unknown_fields(self):
        noisy = ('{"t":"2026-09-16T00:00:04Z","ms":4000,"trace":"%s","event":"output.state",'
                 '"reason":"periodic","turn":1,"source_volume":0.5,'
                 '"device_name":"Realtek Speakers","token":"secret-value",'
                 '"transcript":"\\uc548\\ub155"}' % TRACE)
        server = normal_server() + [
            "2026-09-16 00:00:06,000Z INFO v=dialogue_diag_v4 trace=%s turn=1 "
            "event=turn.done reason=normal persona=jiwon_2024 note=hello" % TRACE]
        markdown, report = self.run_tool(normal_unity() + [noisy], server)
        blob = json.dumps(report, ensure_ascii=False) + markdown.read_text(encoding="utf-8")
        for leaked in ("Realtek", "secret-value", "jiwon_2024", "안녕", "device_name", "persona"):
            self.assertNotIn(leaked, blob)
        allowed = tool.UNITY_NUMBERS | tool.SERVER_NUMBERS
        for event in report["events"]:
            self.assertTrue(set(event["values"]) <= allowed, event["event"])
            self.assertTrue(set(event) == {"source", "wall", "clock_s", "event",
                                           "reason", "turn", "periodic", "values"})


class SttMappingTests(Base):
    def test_asr_is_attached_only_through_an_explicit_candidate_mapping(self):
        _, report = self.run_tool(normal_unity(), normal_server())
        row = self.turn(report)
        self.assertTrue(row["stt"]["mapped"])
        self.assertEqual(row["stt"]["asr_max_ms"], 410)
        self.assertEqual(row["candidates"], [3])
        self.assertEqual(report["inputs"]["asr_without_turn"]["calls"], 0)

    def test_rejected_noise_candidate_is_not_charged_to_the_next_turn(self):
        server = ([server_line("input.candidate", 0.2, "started", turn=0, candidate=1),
                   server_line("input.verified", 0.9, "closed", turn=0, candidate=1, asr_ms=2300),
                   server_line("input.rejected", 1.0, "no_text", turn=0, candidate=1)]
                  + normal_server())
        markdown, report = self.run_tool(normal_unity(), server)
        row = self.turn(report)
        self.assertEqual(row["stt"]["calls"], 1)
        self.assertEqual(row["stt"]["asr_max_ms"], 410)
        without = report["inputs"]["asr_without_turn"]
        self.assertEqual(without["calls"], 1)
        self.assertEqual(without["asr_max_ms"], 2300)
        self.assertNotIn("2300", markdown.read_text(encoding="utf-8").split("## 2.")[1].split("## 3.")[0])

    def test_held_turn_keeps_its_own_candidate_mapping(self):
        server = [server_line("input.verified", 1.0, "closed", turn=0, candidate=1, asr_ms=300),
                  server_line("input.assigned", 1.1, "accepted", turn=1, candidate=1),
                  server_line("turn.started", 1.2, "normal", turn=1),
                  server_line("turn.held", 2.0, "hold", turn=1),
                  server_line("input.verified", 3.0, "closed", turn=0, candidate=2, asr_ms=800),
                  server_line("input.assigned", 3.1, "accepted", turn=2, candidate=2),
                  server_line("turn.started", 3.2, "reasoning", turn=2),
                  server_line("turn.resumed", 4.0, "resume", turn=1)]
        _, report = self.run_tool(normal_unity(), server)
        self.assertEqual(self.turn(report, 1)["stt"]["asr_max_ms"], 300)
        self.assertEqual(self.turn(report, 2)["stt"]["asr_max_ms"], 800)
        self.assertEqual(self.turn(report, 2)["mode"], "reasoning")

    def test_v3_log_without_assignment_leaves_turn_stt_unconfirmed(self):
        server = [server_line("input.verified", 1.0, "closed", version="dialogue_diag_v3",
                              turn=0, candidate=1, asr_ms=450),
                  server_line("turn.started", 1.2, "normal", version="dialogue_diag_v3", turn=1),
                  server_line("turn.done", 5.0, "normal", version="dialogue_diag_v3", turn=1)]
        _, report = self.run_tool(normal_unity(), server)
        row = self.turn(report)
        self.assertFalse(row["stt"]["mapped"])
        self.assertIsNone(row["stt"]["asr_max_ms"])
        self.assertEqual(row["stt"]["reason"], "no_candidate_mapping")
        self.assertEqual(report["inputs"]["asr_without_turn"]["calls"], 1)
        self.assertIn("stt_unmapped", self.codes(report))


class TimingSeparationTests(Base):
    def test_respond_entry_and_llm_call_timings_stay_apart(self):
        _, report = self.run_tool(normal_unity(), normal_server())
        row = self.turn(report)
        self.assertEqual(row["server"]["first_text_ms"], 520)
        self.assertEqual(row["server"]["llm_first_ms"], 310)

    def test_unmeasured_minus_one_becomes_null_not_zero(self):
        server = [server_line("turn.started", 2.4, "normal", turn=1),
                  server_line("llm.first_text", 3.0, turn=1, first_text_ms=-1, llm_first_ms=-1),
                  server_line("turn.timing", 5.1, turn=1, first_text_sec=-1, first_audio_sec=-1,
                              first_reaction_audio_sec=-1, first_any_audio_sec=-1, total_sec=2.7)]
        _, report = self.run_tool(normal_unity(), server)
        row = self.turn(report)
        for key in ("first_text_ms", "llm_first_ms", "first_text_sec", "first_audio_sec",
                    "first_reaction_audio_sec"):
            self.assertIsNone(row["server"][key], key)
        self.assertEqual(row["server"]["total_sec"], 2.7)

    def test_reaction_and_answer_tts_are_never_merged_into_one_first_time(self):
        server = normal_server() + [
            server_line("tts.phrase_started", 2.6, "reaction", turn=1, phrase=1, chars=6),
            server_line("tts.phrase_first_audio", 2.7, "reaction", turn=1, phrase=1,
                        tts_first_ms=80),
            server_line("tts.phrase_done", 3.0, "reaction", turn=1, phrase=1, packets=5,
                        samples=12000, tts_first_ms=80, tts_max_gap_ms=20)]
        _, report = self.run_tool(normal_unity(), server)
        tts = self.turn(report)["tts"]
        self.assertEqual(tts["answer"]["first_ms"], 200)
        self.assertEqual(tts["reaction"]["first_ms"], 80)
        self.assertEqual(tts["answer"]["samples"], 96000)
        self.assertEqual(tts["reaction"]["samples"], 12000)


class ReaderLimitTests(Base):
    def test_oversize_file_is_marked_partial_instead_of_silently_cut(self):
        original = tool.MAX_LOG_BYTES
        tool.MAX_LOG_BYTES = 400
        self.addCleanup(setattr, tool, "MAX_LOG_BYTES", original)
        markdown, report = self.run_tool(normal_unity(), normal_server())
        self.assertTrue(report["sources"]["unity"]["size_limit_reached"])
        self.assertTrue(report["completeness"]["partial"])
        self.assertIn("reader_limit", self.codes(report))
        self.assertIn("부분 보고서", markdown.read_text(encoding="utf-8"))

    def test_a_single_huge_line_is_skipped_and_counted(self):
        huge = json.dumps({"t": "2026-09-16T00:00:04Z", "ms": 4000, "trace": TRACE,
                           "event": "playback.sample", "junk": "A" * 70000})
        _, report = self.run_tool(normal_unity() + [huge], normal_server())
        self.assertEqual(report["sources"]["unity"]["oversize_lines"], 1)
        self.assertIn("log_truncated", self.codes(report))
        self.assertTrue(report["completeness"]["partial"])

    def test_a_giant_line_does_not_stop_the_lines_after_it(self):
        """상한을 넘는 줄은 건너뛰고 그 다음 줄부터 다시 읽어야 한다."""
        giant = '{"junk":"' + "B" * 200000 + '"}'
        lines = normal_unity()[:5] + [giant] + normal_unity()[5:]
        _, report = self.run_tool(lines, normal_server())
        self.assertEqual(report["sources"]["unity"]["oversize_lines"], 1)
        self.assertEqual(report["sources"]["unity"]["used"], len(normal_unity()))
        self.assertEqual(self.turn(report)["status"], "complete")

    def test_a_long_but_valid_v4_line_keeps_its_trailing_timing_fields(self):
        """v4 줄은 600자를 넘을 수 있다. 뒤쪽 timing 을 잘라 버리면 안 된다."""
        padded = server_line("turn.timing", 5.1, turn=1, first_text_sec=0.6,
                             first_audio_sec=0.9, first_reaction_audio_sec=0.3,
                             first_any_audio_sec=0.3, total_sec=2.7,
                             audio_chunks=1, vad_start=1, vad_end=1, vad_too_long=0,
                             accepted=1, no_speech=0, no_text=0, verify_failed=0,
                             dropped=0, turns=1, audio_packets=40, held=0, resumed=0,
                             endpoint_checks=3, endpoint_end=1, endpoint_rearm=1,
                             endpoint_failed=0, endpoint_stale=0, asr_calls=1,
                             asr_avg_ms=410, asr_max_ms=410, max_input_gap_ms=95.5,
                             resp_packets=40, resp_samples=96000, resp_span_s=1.7,
                             resp_max_gap_ms=120, answer_audio_sec=4.0)
        self.assertGreater(len(padded), 600)
        server = [line for line in normal_server() if "turn.timing" not in line] + [padded]
        _, report = self.run_tool(normal_unity(), server)
        row = self.turn(report)
        self.assertEqual(row["server"]["total_sec"], 2.7)
        self.assertEqual(row["server"]["first_reaction_audio_sec"], 0.3)

    def test_remote_script_skips_oversize_lines_instead_of_truncating_them(self):
        self.assertNotIn("[:600]", tool.REMOTE_SCRIPT)
        self.assertIn("readline(line_cap + 1)", tool.REMOTE_SCRIPT)
        self.assertIn("#oversize line", tool.REMOTE_SCRIPT)
        self.assertIn("range(6)", tool.REMOTE_SCRIPT)

    def test_remote_oversize_marker_is_counted_without_claiming_a_cut_tail(self):
        def runner(command, **kwargs):
            return Fake(0, "#oversize line\n" + "\n".join(normal_server()) + "\n")

        unity = self.write_unity(normal_unity())
        _, report = tool.analyze(unity, None, "raon", output=self.root / "out", runner=runner)
        self.assertEqual(report["sources"]["server"]["oversize_lines"], 1)
        self.assertFalse(report["sources"]["server"]["size_limit_reached"])
        self.assertTrue(report["completeness"]["partial"])

    def test_missing_final_newline_is_a_note_not_a_defect(self):
        unity = self.root / ("dialogue-20260916T000000Z-%s.jsonl" % TRACE)
        unity.write_text("\n".join(normal_unity()), encoding="utf-8")   # 끝 개행 없음
        markdown, report = tool.analyze(unity, None, output=self.root / "out")
        self.assertFalse(report["sources"]["unity"]["last_line_complete"])
        self.assertEqual(report["sources"]["unity"]["invalid_lines"], 0)
        self.assertNotIn("log_truncated", self.codes(report))
        self.assertIn("그 자체로 결함은 아니다", markdown.read_text(encoding="utf-8"))


class VersionTests(Base):
    def test_unknown_version_values_are_counted_and_never_copied_out(self):
        secret = "dialogue_diag_v9_SUPERSECRET"
        server = normal_server() + [
            server_line("turn.done", 6.0, "normal", version=secret, turn=2)]
        unity = normal_unity() + [unity_line("playback.sample", 4100, "periodic",
                                             version=77, turn=1, received=1)]
        markdown, report = self.run_tool(unity, server)
        blob = json.dumps(report, ensure_ascii=False) + markdown.read_text(encoding="utf-8")
        self.assertNotIn(secret, blob)
        self.assertNotIn("77", str(report["sources"]["unity"]["versions"]))
        self.assertEqual(report["sources"]["server"]["unknown_version_lines"], 1)
        self.assertEqual(report["sources"]["unity"]["unknown_version_lines"], 1)
        self.assertIn("unknown_version", self.codes(report))
        self.assertEqual(report["sources"]["server"]["versions"], ["dialogue_diag_v4"])
        self.assertTrue(report["completeness"]["partial"])

    def test_damaged_lines_alone_make_the_report_partial(self):
        lines = normal_unity() + ['{"t":"2026-09-16T00:00:07Z", broken']
        _, report = self.run_tool(lines, normal_server())
        self.assertEqual(report["sources"]["unity"]["invalid_lines"], 1)
        self.assertTrue(report["completeness"]["partial"])
        self.assertEqual(self.turn(report)["status"], "complete")   # 턴 자체는 완료다

    def test_a_clean_log_is_not_marked_partial(self):
        _, report = self.run_tool(normal_unity(), normal_server())
        self.assertFalse(report["completeness"]["partial"])

    def test_source_metadata_carries_no_raw_values_from_the_log(self):
        allowed = set(tool.blank_source("unity")) | {"origin", "alias", "exit_code"}
        _, report = self.run_tool(normal_unity(), normal_server())
        for name in ("unity", "server"):
            self.assertTrue(set(report["sources"][name]) <= allowed, name)


class OutputTests(Base):
    def test_existing_output_directory_is_refused(self):
        (self.root / "out").mkdir()
        (self.root / "out" / "report.md").write_text("keep me", encoding="utf-8")
        with self.assertRaises(tool.ReportError) as caught:
            self.run_tool(normal_unity(), normal_server())
        self.assertEqual(caught.exception.code, "output_exists")
        self.assertEqual((self.root / "out" / "report.md").read_text(encoding="utf-8"), "keep me")

    def test_cli_prints_only_the_report_path_on_success(self):
        unity = self.write_unity(normal_unity())
        buffer, errors = [], []
        argv = ["--unity-log", str(unity), "--output", str(self.root / "cli")]
        code = run_main(argv, buffer, errors)
        self.assertEqual(code, 0)
        self.assertEqual(len(buffer), 1)
        self.assertTrue(Path(buffer[0]).is_file())
        self.assertEqual(Path(buffer[0]).name, "report.md")
        self.assertEqual(errors, [])

    def test_cli_failure_prints_a_fixed_code_without_any_log_content(self):
        buffer, errors = [], []
        argv = ["--unity-log", str(self.root / "missing.jsonl"),
                "--output", str(self.root / "cli")]
        code = run_main(argv, buffer, errors)
        self.assertEqual(code, 2)
        self.assertEqual(buffer, [])
        self.assertEqual(errors, ["no_unity_log"])
        self.assertIn(errors[0], tool.ERRORS)


class SshTests(Base):
    def test_alias_with_an_injected_option_is_refused_before_running_ssh(self):
        for alias in ("-oProxyCommand=curl evil", "--rsync-path=x", "raon host",
                      "raon;id", "", "user@host"):
            with self.subTest(alias=alias):
                with self.assertRaises(tool.ReportError) as caught:
                    tool.ssh_command(alias, TRACE)
                self.assertEqual(caught.exception.code, "ssh_alias_rejected")

    def test_ssh_command_is_a_fixed_non_interactive_argument_list(self):
        command = tool.ssh_command("raon", TRACE)
        self.assertIsInstance(command, list)
        self.assertEqual(command[0], "ssh")
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[-2], "raon")
        self.assertEqual(command[-1], "python3 - " + TRACE)
        self.assertIn("~/capstone-server/logs/dialogue-diagnostics.log", tool.REMOTE_SCRIPT)
        self.assertNotIn("rm ", tool.REMOTE_SCRIPT)

    def test_successful_ssh_collection_keeps_only_the_selected_trace(self):
        other = server_line("turn.done", 9.9, "normal", turn=4).replace(TRACE, "ffffffffffff")
        calls = []

        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return Fake(0, "\n".join(normal_server() + [other]) + "\n")

        unity = self.write_unity(normal_unity())
        _, report = tool.analyze(unity, None, "raon", output=self.root / "out", runner=runner)
        self.assertEqual(report["sources"]["server"]["origin"], "ssh")
        self.assertTrue(report["sources"]["server"]["available"])
        self.assertEqual(report["sources"]["server"]["other_trace_lines"], 1)
        self.assertEqual(calls[0][1]["input"], tool.REMOTE_SCRIPT)
        self.assertTrue(calls[0][1]["timeout"] > 0)

    def test_failed_ssh_is_shown_as_unchecked_and_never_echoes_remote_stderr(self):
        def runner(command, **kwargs):
            return Fake(255, "", "ssh: connect to host 220.69.208.201 port 22: timed out")

        unity = self.write_unity(normal_unity())
        markdown, report = tool.analyze(unity, None, "raon", output=self.root / "out",
                                        runner=runner)
        server = report["sources"]["server"]
        self.assertFalse(server["available"])
        self.assertEqual(server["note"], "ssh_failed")
        self.assertEqual(server["exit_code"], 255)
        blob = json.dumps(report, ensure_ascii=False) + markdown.read_text(encoding="utf-8")
        self.assertNotIn("220.69.208.201", blob)
        self.assertNotIn("connect to host", blob)
        self.assertIn("server_unchecked", self.codes(report))

    def test_ssh_timeout_is_reported_as_a_code(self):
        def runner(command, **kwargs):
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 1))

        unity = self.write_unity(normal_unity())
        _, report = tool.analyze(unity, None, "raon", output=self.root / "out", runner=runner)
        self.assertEqual(report["sources"]["server"]["note"], "ssh_timeout")
        self.assertFalse(report["sources"]["server"]["available"])


class TraceTests(Base):
    def test_trace_must_be_valid_hex(self):
        self.assertEqual(tool.valid_trace("0123456789AB"), TRACE)
        for bad in ("zzzz", "0123456789abc", "", None, "../../etc/passwd"):
            self.assertEqual(tool.valid_trace(bad), "")

    def test_trace_falls_back_to_the_first_record_when_the_name_has_none(self):
        path = self.write_unity(normal_unity(), name="capture.jsonl")
        self.assertEqual(tool.trace_of(path), TRACE)


class Fake:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def run_main(argv, out, err):
    """stdout/stderr 계약만 확인한다. 실제 파일은 임시 디렉터리에만 쓴다."""
    import io
    saved = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = io.StringIO(), io.StringIO()
    try:
        code = tool.main(argv)
    finally:
        out.extend(sys.stdout.getvalue().splitlines())
        err.extend(sys.stderr.getvalue().splitlines())
        sys.stdout, sys.stderr = saved
    return code


if __name__ == "__main__":
    unittest.main()
