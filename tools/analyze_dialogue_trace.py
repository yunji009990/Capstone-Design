"""한 번의 체험(trace)을 Unity JSONL 과 서버 진단 로그로 대조해 보고서를 만든다.

사용자가 Scene_2 테스트를 마친 뒤 "어디서 멈췄는지"를 가리기 위한 읽기 전용 도구다.
원인을 자동으로 확정하지 않는다. 관측된 숫자와 확인해 볼 영역만 적는다.

    python tools/analyze_dialogue_trace.py                    # 최신 Unity 로그
    python tools/analyze_dialogue_trace.py --server-log <경로>
    python tools/analyze_dialogue_trace.py --ssh raon         # 명시할 때만 SSH

입력은 모두 비신뢰로 다룬다. 미리 정한 사건·원인 코드와 유한한 숫자만 다시 만들어
쓰고, 그 밖의 값은 버리거나 other 로 접는다. 사용자 발화·모델 답변·세션 ID·경로·키는
애초에 로그에 없어야 하지만, 섞여 들어와도 이 도구가 보고서로 옮기지 않는다.

성공하면 stdout 마지막 줄은 report.md 절대 경로 한 줄이다(Unity 메뉴가 읽는다).
실패하면 stderr 에 고정 오류 코드만 남긴다. 로그 원문은 어느 쪽에도 내보내지 않는다.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPORT_SCHEMA = 1
UNITY_LOG_DIR = ".dialogue-work/logs"
REPORT_DIR = ".dialogue-work/reports"
REMOTE_LOG = "~/capstone-server/logs/dialogue-diagnostics.log"

TRACE_RE = re.compile(r"\A[0-9a-f]{12}\Z|\A[0-9a-f]{32}\Z")
ALIAS_RE = re.compile(r"\A[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}\Z")
CODE_RE = re.compile(r"\A[A-Za-z0-9_.:-]{1,48}\Z")
PAIR_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]{0,31})=(\S{1,64})")
SERVER_STAMP_RE = re.compile(r"\A(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})[,.](\d{1,6})Z?\b")

# --- 허용 목록 -------------------------------------------------------------
# 여기 없는 사건은 버리고, 여기 없는 원인 코드는 other 로 접는다. 접은 횟수는 센다.

UNITY_EVENTS = frozenset({
    # v1 부터 있던 사건
    "experience.start", "experience.end", "dialogue.fail", "main_thread.stall",
    "audio.config_changed", "playback.sample",
    "response.started", "response.paused", "response.resumed", "response.done",
    "response.cancelled",
    # v2 에서 늘어난 사건
    "audio.first_received", "audio.boundary", "playback.first_render",
    "playback.boundary", "playback.completed", "playback.stopped", "response.timing",
    # 멈춤 경계(2026-09-17). 요청은 기존 response.paused 에 숫자가 붙는 형태다.
    "playback.paused", "playback.pause_forced",
    "input.started", "input.stopped", "input.transcript", "input.sample",
    "output.state", "user.marker", "application.focus", "application.pause",
    "trace.limit",
})
UNITY_REASONS = frozenset({
    # 체험 수명
    "test_profile", "registered", "unspecified", "on_disable", "app_pause",
    "session_changed", "begin_failed", "periodic", "frame_gap", "device", "size",
    # 사용자가 UI 로 끝낸 경우와 제어가 꺼진 경우
    "ui_finish", "ui_finish_space", "ui_finish_enter", "ui_finish_spaceenter",
    "control_disabled",
    # 실패 코드와 EndExperience 의 fail_ 접두 형태
    "client_error", "connection_error", "microphone_stopped", "mic_pump_stall",
    "send_queue_overflow", "playback_overflow",
    "fail_client_error", "fail_connection_error", "fail_microphone_stopped",
    "fail_mic_pump_stall", "fail_send_queue_overflow", "fail_playback_overflow",
    # 재생을 멈춘 원인
    "new_input", "new_response", "user_cancel", "server_cancel", "response_error",
    "reset", "experience_end", "playback_done",
    # 서버가 주는 경로·취소 사유·판정 행동
    "normal", "reasoning", "fallback",
    "user_speech", "newer_utterance", "hold_timeout", "memory_forget",
    "turn_failed", "empty_transcript",
    "resume", "revise", "switch", "hold", "clarify",
    # 멈춤 경계. phrase/immediate 는 서버가 요청한 방식이고, 나머지는 클라이언트가
    # 실제로 멈춘 지점이다. boundary 만 구절을 끝까지 들려준 정지다.
    "phrase", "immediate", "boundary", "grace_expired", "no_audio", "already_paused",
    # 음성 종류와 전사 단계
    "answer", "reaction", "partial", "final",
    "other",
})
UNITY_NUMBERS = frozenset({
    # v1 계측
    "tts", "dsp_rate", "dsp_buffer", "dsp_buffers", "source_rate", "preroll", "sec",
    "turn", "received", "consumed", "buffered", "underrun_frames", "rebuffers",
    "callbacks", "rendered", "ended", "audio_packets", "resp_packets", "resp_audio_span",
    "max_packet_gap", "paused_gap", "max_frame_stall", "cfg_changes", "paused", "listening",
    # v2 공통 축
    "input_turn", "response_seq", "kind", "chars", "final", "lines",
    # 수신·렌더·경계
    "receive_to_render_ms", "boundary", "expected_samples", "remaining", "complete",
    "tail_rms", "tail_peak", "tail_silence_ms", "saw_answer", "saw_reaction",
    # 멈춤 경계 계측. expected 가 -1 이면 예약한 경계가 없었다는 뜻이고 0 이 아니다.
    "pause_id", "pause_grace_ms", "pause_expected_sample", "pause_stop_sample", "pause_wait_ms",
    # 서버가 보고한 시간
    "first_text_sec", "first_audio_sec", "first_reaction_audio_sec",
    "first_any_audio_sec", "total_sec",
    # 입력·출력 상태 표본
    "mic_chunks", "mic_samples", "mic_level", "waiting", "recording", "connected", "active",
    "source_mute", "source_volume", "source_pitch", "source_enabled", "source_playing",
    "listener_pause", "listener_volume", "focus", "marker",
    # 로거가 직접 붙이는 항목과, 파일 주입 재생을 표시하는 선택 항목
    "v", "unknown_names", "injected",
})
# 응답 수명 사건은 turn 이 주기 표본과 같은 이름이라 따로 모아 둔다.
UNITY_RESPONSE_EVENTS = frozenset({
    "response.started", "response.paused", "response.resumed",
    "response.done", "response.cancelled",
})
UNITY_VERSIONS = frozenset({1, 2})

SERVER_EVENTS = frozenset({
    # v3
    "connection.open", "connection.close", "input.candidate", "input.rejected",
    "input.verified", "input.endpoint", "turn.started", "turn.done",
    "turn.cancelled", "turn.held", "turn.resumed", "heartbeat",
    # v4
    "input.assigned", "route.done", "llm.first_text", "tts.phrase_started",
    "tts.phrase_first_audio", "tts.phrase_done", "turn.timing", "playback.ack",
    # 미리 준비한 안내 문장을 읽은 턴
    "answer.fixed",
})
SERVER_REASONS = frozenset({
    "registered", "test", "client_stop", "client_cancel", "disconnect",
    "protocol_error", "voice_error", "server_error", "shutdown", "close",
    "accepted", "no_speech", "no_text", "verify_failed", "dropped", "too_long",
    # 표기 문자는 있으나 Whisper 세그먼트 지표가 비음성을 가리킨 입력
    "weak_text",
    "started", "closed", "queue_full",
    "silence_tail", "rearmed", "speech", "failed", "stale",
    "normal", "reasoning", "resume", "revise", "switch", "hold", "clarify",
    "turn_failed", "context_too_long", "newer_utterance", "hold_timeout",
    "memory_forget", "utterance_too_long", "reset", "experience_end", "user_speech",
    # answer.fixed 의 출처. 기억 삭제 성공과 미수행을 구별한다.
    "memory_forgotten", "memory_clarify",
    "receive_timeout", "server_shutdown", "input",
    "answer", "reaction",
    # playback.ack 가 멈춘 지점의 보고라는 뜻. 진행·완료 보고와 구별한다.
    "paused",
    "other",
})
SERVER_NUMBERS = frozenset({
    "turn", "uptime_s", "max_input_gap_ms", "asr_calls", "asr_avg_ms", "asr_max_ms",
    "resp_packets", "resp_samples", "resp_span_s", "resp_max_gap_ms",
    "answer_audio_sec", "audio_chunks", "vad_start", "vad_end", "vad_too_long",
    "accepted", "no_speech", "no_text", "verify_failed", "dropped", "weak_text", "turns",
    "audio_packets", "held", "resumed", "endpoint_checks", "endpoint_end",
    "endpoint_rearm", "endpoint_failed", "endpoint_stale",
    # 멈춤 경계. pause_id 는 회차 번호이고 나머지 넷은 연결 누계 계수다.
    # 경계 정지 / 그 밖의 정지 / 신원이 맞지 않는 늦은 보고 / 기한 초과다.
    "pause_id", "pause_boundary", "pause_cut", "pause_stale", "pause_timeout",
    "candidate", "samples", "asr_ms", "seconds", "code",
    "age_ms", "voiced_pct", "max_quiet_ms", "tail_ms",
    # 입력 품질 집계. 채택(input.verified)과 거절(input.rejected)에 같이 붙는다.
    # x100 은 서버가 반올림 해상도를 지키려고 100 을 곱한 값이다. 재지 못한 항목은
    # 줄에 아예 없다. 없는 것을 0 으로 읽지 않는다.
    "no_speech_pct", "avg_logprob_x100", "compression_x100",
    "text_segments", "weak_segments",
    # first_text_ms 는 respond 진입 기준, llm_first_ms 는 생성 호출 기준이다. 서로 다르다.
    "route_ms", "first_text_ms", "llm_first_ms", "phrase", "chars", "packets",
    "tts_first_ms", "tts_total_ms", "tts_max_gap_ms", "done",
    "first_text_sec", "first_audio_sec", "first_reaction_audio_sec",
    "first_any_audio_sec", "total_sec",
})
SERVER_VERSIONS = frozenset({"dialogue_diag_v3", "dialogue_diag_v4"})
# 서버가 재지 못한 값은 -1 로 적는다. 0 과 구별한다.
MISSING = -1

# 주기 표본은 시간 순 흐름표에서 빼고 턴 집계에만 쓴다.
PERIODIC = frozenset({"playback.sample", "input.sample", "output.state", "heartbeat"})

ERRORS = frozenset({
    "bad_arguments", "invalid_trace", "no_unity_log", "unity_log_unreadable",
    "output_exists", "output_unwritable", "ssh_alias_rejected",
})


class ReportError(Exception):
    """stderr 로 나갈 고정 코드. 원문 메시지는 담지 않는다."""

    def __init__(self, code):
        super().__init__(code if code in ERRORS else "bad_arguments")
        self.code = code if code in ERRORS else "bad_arguments"


# --- 값 정제 ---------------------------------------------------------------

def valid_trace(value):
    text = str(value or "").strip().lower()
    return text if TRACE_RE.match(text) else ""


def code(value, allowed, counter):
    """허용 코드만 통과시킨다. 접은 값은 세어 두고 보고서에 건수만 적는다."""
    text = str(value or "")
    if not text:
        return ""
    if CODE_RE.match(text) and text in allowed:
        return text
    counter[0] += 1
    return "other"


def number(value):
    """유한한 숫자만 받는다. bool·NaN·무한대·문자열은 버린다."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) else None


def parse_number(text):
    try:
        value = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else value


# --- 파일 읽기 -------------------------------------------------------------
# 진행 중인 체험의 로그를 읽을 수도 있다. 언제나 "읽은 시점의 스냅샷"이며 상한이 있다.

MAX_LOG_BYTES = 16 * 1024 * 1024
MAX_LINE_CHARS = 64 * 1024


def blank_source(kind):
    return {"kind": kind, "available": False, "path": None, "versions": [],
            "unknown_version_lines": 0, "lines": 0, "used": 0, "invalid_lines": 0,
            "other_trace_lines": 0, "unknown_events": 0, "unknown_codes": 0,
            "oversize_lines": 0, "size_limit_reached": False,
            "last_line_complete": None, "producer_truncated": False,
            "legacy_only": False, "note": None}


def skip_rest_of_line(handle):
    """상한을 넘은 줄의 나머지를 조각으로 읽어 버린다. 통째로 메모리에 담지 않는다."""
    dropped = 0
    while True:
        chunk = handle.readline(MAX_LINE_CHARS + 1)
        dropped += len(chunk)
        if not chunk or chunk.endswith("\n"):
            return dropped


def iter_lines(handle, info):
    """줄 단위로 읽는다. 한 줄과 파일 전체에 상한을 두고, 큰 줄은 건너뛴다.

    한 줄이 통째로 16MB 여도 readline(상한+1) 로 조각만 잡는다. 마지막 줄에 개행이
    없는 것과, 상한을 넘겨 잘린 것을 구별한다.
    """
    total = 0
    while True:
        chunk = handle.readline(MAX_LINE_CHARS + 1)
        if not chunk:
            return
        total += len(chunk)
        if total > MAX_LOG_BYTES:
            info["size_limit_reached"] = True
            return
        if len(chunk) > MAX_LINE_CHARS:
            info["oversize_lines"] += 1
            total += skip_rest_of_line(handle)
            if total > MAX_LOG_BYTES:
                info["size_limit_reached"] = True
                return
            continue
        info["last_line_complete"] = chunk.endswith("\n")
        yield chunk


def open_log(path, info):
    try:
        handle = open(str(path), "r", encoding="utf-8", errors="replace")
    except OSError:
        info["note"] = "unreadable"
        return None
    info["available"] = True
    info["path"] = str(Path(path).resolve())
    return handle


# --- Unity JSONL -----------------------------------------------------------

def read_unity(path, trace):
    """Unity 진단 JSONL 을 읽는다. 손상·절단·다른 trace 는 세어서 보고한다."""
    info = blank_source("unity")
    events = []
    handle = open_log(path, info)
    if handle is None:
        return events, info
    folded = [0]
    versions = set()
    with handle:
        for line in iter_lines(handle, info):
            read_unity_line(line, trace, events, info, folded, versions)
    info["unknown_codes"] = folded[0]
    info["versions"] = sorted(versions)
    info["legacy_only"] = bool(versions) and versions == {1}
    info["producer_truncated"] = any(event["event"] == "trace.limit" for event in events)
    return events, info


def read_unity_line(line, trace, events, info, folded, versions):
    line = line.strip()
    if not line:
        return
    info["lines"] += 1
    try:
        record = json.loads(line)
    except ValueError:
        info["invalid_lines"] += 1
        return
    if not isinstance(record, dict):
        info["invalid_lines"] += 1
        return
    if valid_trace(record.get("trace")) != trace:
        info["other_trace_lines"] += 1
        return
    name = str(record.get("event") or "")
    if name not in UNITY_EVENTS:
        info["unknown_events"] += 1
        return
    values = {}
    for key, value in record.items():
        if key in UNITY_NUMBERS:
            clean = number(value)
            if clean is not None:
                values[key] = clean
    # v 가 없으면 v1 이다. 아는 번호가 아니면 보고서에 그 값을 옮기지 않고 unknown 으로 센다.
    raw_version = values.pop("v", 1)
    if raw_version in UNITY_VERSIONS:
        versions.add(int(raw_version))
    else:
        info["unknown_version_lines"] += 1
    millis = number(record.get("ms"))
    events.append({"source": "unity", "event": name,
                   "reason": code(record.get("reason"), UNITY_REASONS, folded),
                   "clock_s": round(millis / 1000.0, 3) if millis is not None else None,
                   "wall": wall_time(record.get("t")), "values": values})
    info["used"] += 1


def wall_time(value):
    """ISO 문자열을 정규화한다. 기기 시계가 달라 비교용이 아니라 정렬용이다."""
    text = str(value or "").strip()
    if not text or len(text) > 40:
        return None
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc).isoformat()


# --- 서버 key=value 로그 ----------------------------------------------------

def parse_server_lines(lines, trace, info=None):
    """key=value 한 줄 형식을 읽는다. 자유 문장·미지 필드는 버린다."""
    if info is None:
        info = blank_source("server")
        info["origin"] = "none"
    events = []
    folded = [0]
    versions = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            # 원격 수집기가 붙이는 상한 표시. 빠진 것이 있다는 뜻이다.
            if "oversize" in line:
                info["oversize_lines"] += 1
            else:
                info["size_limit_reached"] = True
            continue
        info["lines"] += 1
        pairs = {key: value for key, value in PAIR_RE.findall(line)}
        if not pairs:
            info["invalid_lines"] += 1
            continue
        if valid_trace(pairs.get("trace")) != trace:
            info["other_trace_lines"] += 1
            continue
        name = pairs.get("event", "")
        if name not in SERVER_EVENTS:
            info["unknown_events"] += 1
            continue
        # v 값은 아는 것만 옮긴다. 모르는 문자열을 보고서에 그대로 복사하지 않는다.
        version = pairs.get("v", "")
        if version in SERVER_VERSIONS:
            versions.add(version)
        else:
            info["unknown_version_lines"] += 1
        values = {}
        for key, text in pairs.items():
            if key in SERVER_NUMBERS:
                clean = parse_number(text)
                if clean is not None:
                    values[key] = clean
        stamp = SERVER_STAMP_RE.match(line)
        events.append({"source": "server", "event": name,
                       "reason": code(pairs.get("reason"), SERVER_REASONS, folded),
                       "clock_s": values.get("uptime_s"),
                       "wall": wall_time("%sT%s.%s+00:00" % stamp.groups()[:3]) if stamp else None,
                       "values": values})
        info["used"] += 1
    info["unknown_codes"] = folded[0]
    info["versions"] = sorted(versions)
    info["legacy_only"] = bool(versions) and "dialogue_diag_v4" not in versions
    return events, info


def empty_server(trace, **fields):
    events, info = parse_server_lines([], trace)
    info.update(fields)
    return events, info


def read_server_file(path, trace):
    info = blank_source("server")
    info["origin"] = "file"
    handle = open_log(path, info)
    if handle is None:
        return [], info
    with handle:
        return parse_server_lines(iter_lines(handle, info), trace, info)


# --- SSH 수집 --------------------------------------------------------------
# 원격에서 고정 스크립트를 stdin 으로 넣어 실행한다. 선택한 trace 줄만 되돌려 받고
# 다른 세션이나 원문 로그는 가져오지 않는다. 키·비밀번호는 조회하지 않는다.

REMOTE_SCRIPT = '''
import io, os, sys
trace = sys.argv[1] if len(sys.argv) > 1 else ""
if len(trace) not in (12, 32) or set(trace) - set("0123456789abcdef"):
    sys.exit(3)
base = os.path.expanduser("%s")
needle = "trace=" + trace
line_cap = 64 * 1024
budget = 4 * 1024 * 1024
written = 0
for index in range(6):
    path = base if index == 0 else "%%s.%%d" %% (base, index)
    try:
        size = os.path.getsize(path)
    except OSError:
        continue
    if size > 32 * 1024 * 1024:
        sys.stdout.write("#oversize file\\n")
        continue
    try:
        handle = io.open(path, "r", encoding="utf-8", errors="replace")
    except OSError:
        continue
    with handle:
        while True:
            line = handle.readline(line_cap + 1)
            if not line:
                break
            if len(line) > line_cap:
                # 한 줄이 상한을 넘었다. 자르지 않고 통째로 건너뛰며 그 사실만 알린다.
                while True:
                    rest = handle.readline(line_cap + 1)
                    if not rest or rest.endswith("\\n"):
                        break
                sys.stdout.write("#oversize line\\n")
                continue
            if needle not in line:
                continue
            # 줄을 자르지 않는다. v4 줄은 600자를 넘을 수 있고 뒤쪽에 timing 이 붙는다.
            line = line.rstrip("\\n")
            if written + len(line) + 1 > budget:
                sys.stdout.write("#budget\\n")
                sys.exit(0)
            written += len(line) + 1
            sys.stdout.write(line + "\\n")
''' % REMOTE_LOG

SSH_TIMEOUT = 60
SSH_MAX_BYTES = 4 * 1024 * 1024


def ssh_command(alias, trace):
    """별칭에 옵션을 주입할 수 없게 형식을 좁힌다. 어긋나면 실행하지 않는다."""
    if not ALIAS_RE.match(str(alias or "")):
        raise ReportError("ssh_alias_rejected")
    if not valid_trace(trace):
        raise ReportError("invalid_trace")
    return ["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
            "-o", "NumberOfPasswordPrompts=0", alias, "python3 - " + trace]


def collect_via_ssh(alias, trace, runner=subprocess.run):
    """원격 진단 로그에서 선택한 trace 만 받아 온다. 실패는 코드로만 보고한다."""
    command = ssh_command(alias, trace)
    try:
        done = runner(command, input=REMOTE_SCRIPT, capture_output=True, text=True,
                      encoding="utf-8", errors="replace", timeout=SSH_TIMEOUT)
    except subprocess.TimeoutExpired:
        return empty_server(trace, origin="ssh", note="ssh_timeout", alias=alias)
    except OSError:
        return empty_server(trace, origin="ssh", note="ssh_launch_failed", alias=alias)
    if done.returncode != 0:
        # 원격 stderr 본문은 옮기지 않는다. 종료 코드만 남긴다.
        return empty_server(trace, origin="ssh", note="ssh_failed", alias=alias,
                            exit_code=int(done.returncode))
    text = (done.stdout or "")[:SSH_MAX_BYTES]
    info = blank_source("server")
    info.update(origin="ssh", available=True, alias=alias, exit_code=0)
    events, info = parse_server_lines(text.splitlines(), trace, info)
    if len(done.stdout or "") > SSH_MAX_BYTES:
        info["size_limit_reached"] = True
    return events, info


# --- 집계 ------------------------------------------------------------------

def blank_phrases():
    return {"started": 0, "done": 0, "packets": None, "samples": None,
            "first_ms": None, "max_gap_ms": None}


def blank_turn(turn):
    return {
        "turn": turn, "mode": None, "mode_source": None, "route_ms": None,
        "input_turns": [], "input_turn_mismatch": False, "candidates": [],
        # STT 는 채택된 후보와 turn 을 잇는 줄이 있을 때만 이 턴의 값이다.
        "stt": {"calls": 0, "asr_max_ms": None, "asr_avg_ms": None,
                "mapped": False, "reason": "no_candidate_mapping"},
        "server": {"first_text_ms": None, "llm_first_ms": None, "first_text_sec": None,
                   "first_audio_sec": None, "first_reaction_audio_sec": None,
                   "first_any_audio_sec": None, "total_sec": None,
                   "sent_samples": None, "sent_packets": None,
                   "status": None, "status_reason": None},
        # 대기 리액션과 본답변의 TTS 는 섞지 않는다.
        "tts": {"answer": blank_phrases(), "reaction": blank_phrases()},
        "unity": {"first_audio_kind": None, "first_received_ms": None,
                  "reaction_first_received_ms": None, "first_render_ms": None,
                  "receive_to_render_ms": None, "received": None, "consumed": None,
                  "expected_samples": None, "remaining": None, "rendered": None,
                  "underrun_frames": None, "underrun_scope": None, "rebuffers": None,
                  "callbacks": None, "boundaries": 0, "complete": None,
                  "status": None, "stop_reason": None, "lifecycle": [], "cancelled": False,
                  "sample_received": None, "injected": None},
        "sample_match": {"checked": False, "match": None, "reason": "insufficient_observation",
                         "server_samples": None, "unity_received": None, "unity_consumed": None},
        "markers": [], "status": "unknown",
    }


def keep_min_in(bucket, key, value):
    if value is not None and (bucket[key] is None or value < bucket[key]):
        bucket[key] = value


def keep_max_in(bucket, key, value):
    if value is not None and (bucket[key] is None or value > bucket[key]):
        bucket[key] = value


def keep_min(row, path, key, value):
    keep_min_in(row[path], key, value)


def turn_of(values):
    turn = values.get("turn")
    return int(turn) if isinstance(turn, (int, float)) and 0 < turn < 10 ** 6 else None


def in_time_order(events):
    """벽시계, 없으면 각자의 단조시간으로 정렬한다. 두 출처를 섞어 빼지 않는다."""
    return sorted(events, key=lambda event: (event["wall"] or "", event["clock_s"] or 0))


def candidate_of(values):
    value = values.get("candidate")
    return int(value) if isinstance(value, (int, float)) and value >= 0 else None


def measured(value):
    """서버는 재지 못한 값을 -1 로 적는다. 0 과 구별해 미측정은 None 으로 둔다."""
    return None if value is None or value <= MISSING else value


def map_candidates(server_events):
    """input.assigned 로 채택 후보와 응답 turn 을 잇는다. 없으면 잇지 않는다.

    input.candidate/verified/rejected 줄에 찍힌 turn 은 그 시점의 **이전 응답** 값이다.
    거절된 잡음 후보와 채택 후보가 섞이므로 "다음 turn.started 에 다 붙이기" 는 틀린다.
    명시적 매핑이 없는 v3 로그에서는 턴별 STT 를 미확인으로 남긴다.
    """
    mapping = {}
    for event in server_events:
        if event["event"] != "input.assigned":
            continue
        candidate = candidate_of(event["values"])
        turn = turn_of(event["values"])
        if candidate is not None and turn is not None:
            mapping[candidate] = turn
    return mapping


def collect_turns(unity_events, server_events):
    rows = {}
    server_events = in_time_order(server_events)
    assigned = map_candidates(server_events)
    verified = {}
    unmapped_asr = []

    def row_for(turn):
        if turn not in rows:
            rows[turn] = blank_turn(turn)
        return rows[turn]

    for event in server_events:
        values = event["values"]
        name = event["event"]
        turn = turn_of(values)
        if name == "input.verified":
            asr = values.get("asr_ms")
            candidate = candidate_of(values)
            if asr is None:
                continue
            if candidate is not None and candidate in assigned:
                verified.setdefault(assigned[candidate], []).append(asr)
            else:
                unmapped_asr.append(asr)
            continue
        if turn is None or name in ("heartbeat", "connection.open", "connection.close",
                                    "input.candidate", "input.rejected", "input.endpoint"):
            continue
        row = row_for(turn)
        if name == "input.assigned":
            candidate = candidate_of(values)
            if candidate is not None and candidate not in row["candidates"]:
                row["candidates"].append(candidate)
        elif name == "turn.started":
            row["mode"] = event["reason"] or None
            row["mode_source"] = "server_turn_started"
            row["server"]["status"] = "started"
        elif name == "route.done":
            row["mode"] = event["reason"] or row["mode"]
            row["mode_source"] = "server_route_done"
            row["route_ms"] = values.get("route_ms")
        elif name == "llm.first_text":
            # respond 진입 기준과 생성 호출 기준은 다른 값이다. 둘을 합치지 않는다.
            row["server"]["first_text_ms"] = measured(values.get("first_text_ms"))
            row["server"]["llm_first_ms"] = measured(values.get("llm_first_ms"))
        elif name in ("tts.phrase_started", "tts.phrase_first_audio", "tts.phrase_done"):
            kind = "reaction" if event["reason"] == "reaction" else "answer"
            phrases = row["tts"][kind]
            if name == "tts.phrase_started":
                phrases["started"] += 1
            elif name == "tts.phrase_first_audio":
                keep_min_in(phrases, "first_ms", measured(values.get("tts_first_ms")))
            else:
                phrases["done"] += 1
                add(phrases, "packets", values.get("packets"))
                add(phrases, "samples", values.get("samples"))
                keep_max_in(phrases, "max_gap_ms", values.get("tts_max_gap_ms"))
                keep_min_in(phrases, "first_ms", measured(values.get("tts_first_ms")))
        elif name == "turn.timing":
            for key in ("first_text_sec", "first_audio_sec", "first_reaction_audio_sec",
                        "first_any_audio_sec", "total_sec"):
                row["server"][key] = measured(values.get(key))
        elif name == "playback.ack":
            row["server"]["sent_samples"] = values.get("samples", row["server"]["sent_samples"])
        elif name in ("turn.done", "turn.cancelled", "turn.held", "turn.resumed"):
            row["server"]["status"] = name.split(".", 1)[1]
            row["server"]["status_reason"] = event["reason"] or None
            if values.get("resp_samples") is not None:
                row["server"]["sent_samples"] = values["resp_samples"]
            if values.get("resp_packets") is not None:
                row["server"]["sent_packets"] = values["resp_packets"]

    for event in in_time_order(unity_events):
        values = event["values"]
        name = event["event"]
        turn = turn_of(values)
        if turn is None:
            continue
        row = row_for(turn)
        input_turn = values.get("input_turn")
        if input_turn is not None and int(input_turn) not in row["input_turns"]:
            row["input_turns"].append(int(input_turn))
        unity = row["unity"]
        if name == "audio.first_received":
            if event["reason"] == "reaction":
                keep_min(row, "unity", "reaction_first_received_ms", event["clock_s"] and
                         round(event["clock_s"] * 1000.0, 1))
            else:
                keep_min(row, "unity", "first_received_ms", event["clock_s"] and
                         round(event["clock_s"] * 1000.0, 1))
            if unity["first_audio_kind"] is None:
                unity["first_audio_kind"] = event["reason"] or None
        elif name == "playback.first_render":
            keep_min(row, "unity", "first_render_ms", event["clock_s"] and
                     round(event["clock_s"] * 1000.0, 1))
            keep_min(row, "unity", "receive_to_render_ms", values.get("receive_to_render_ms"))
        elif name in ("audio.boundary", "playback.boundary"):
            unity["boundaries"] += 1
        elif name == "response.timing":
            for key in ("first_text_sec", "first_audio_sec", "first_reaction_audio_sec",
                        "first_any_audio_sec", "total_sec"):
                value = values.get(key)
                if value is not None and value >= 0 and row["server"][key] is None:
                    # 서버가 보고한 값을 Unity 가 중계한 것이다. 들린 시간이 아니다.
                    row["server"][key] = value
        elif name in ("playback.completed", "playback.stopped"):
            for key in ("received", "consumed", "expected_samples", "remaining", "rendered",
                        "underrun_frames", "rebuffers", "callbacks"):
                if values.get(key) is not None:
                    unity[key] = values[key]
            unity["underrun_scope"] = "response"
            unity["complete"] = values.get("complete")
            unity["status"] = "completed" if name == "playback.completed" else "stopped"
            if name == "playback.stopped":
                unity["stop_reason"] = event["reason"] or None
        elif name in UNITY_RESPONSE_EVENTS:
            # response.started 의 reason 은 서버가 준 경로다. 서버 로그가 없어도 모드를 안다.
            stage = name.split(".", 1)[1]
            unity["lifecycle"].append({"stage": stage, "reason": event["reason"] or None,
                                       "clock_s": event["clock_s"]})
            if name == "response.started" and row["mode"] is None and event["reason"]:
                row["mode"] = event["reason"]
                row["mode_source"] = "unity_response_started"
            if name == "response.cancelled":
                unity["cancelled"] = True
                if unity["stop_reason"] is None:
                    unity["stop_reason"] = event["reason"] or None
            keep_max_in(unity, "sample_received", values.get("received"))
        elif name == "playback.sample":
            # received/consumed 는 렌더러가 응답마다 0 으로 되돌리므로 이 응답의 값이다.
            # rendered/callbacks/underrun_frames/rebuffers 는 체험 내내 쌓이는 누적값이다.
            keep_max_in(unity, "sample_received", values.get("received"))
            if unity["status"] is None:
                for key in ("received", "consumed", "underrun_frames", "rebuffers",
                            "callbacks", "rendered"):
                    if values.get(key) is not None:
                        unity[key] = values[key]
                unity["underrun_scope"] = "experience_cumulative"
        elif name in ("input.sample", "output.state"):
            # 1 일 때만 주입이다. -1 은 "해당 없음" 이라 True 로 만들지 않는다.
            if values.get("injected") == 1:
                unity["injected"] = True
            elif values.get("injected") == 0 and unity["injected"] is None:
                unity["injected"] = False
        elif name == "user.marker":
            row["markers"].append({"marker": values.get("marker"),
                                   "clock_s": event["clock_s"],
                                   "input_turn": values.get("input_turn")})

    for turn, values in verified.items():
        row = row_for(turn)
        row["stt"] = {"calls": len(values), "asr_max_ms": max(values),
                      "asr_avg_ms": round(sum(values) / len(values), 1),
                      "mapped": True, "reason": "input_assigned"}
    for row in rows.values():
        row["input_turns"].sort()
        row["input_turn_mismatch"] = any(value != row["turn"] for value in row["input_turns"])
        finish_row(row)
    return [rows[key] for key in sorted(rows)], unmapped_asr


def add(bucket, key, value):
    if value is None:
        return
    bucket[key] = value if bucket[key] is None else bucket[key] + value


def finish_row(row):
    unity, server = row["unity"], row["server"]
    played = unity["status"] == "completed" and unity["complete"] not in (0,)
    done = server["status"] == "done"
    if (server["status"] == "cancelled" or unity["cancelled"]
            or unity["stop_reason"] in ("user_cancel", "server_cancel")):
        row["status"] = "cancelled"
    elif played and done:
        row["status"] = "complete"
    elif unity["status"] == "stopped":
        row["status"] = "playback_stopped"
    elif played:
        row["status"] = "unity_complete_server_unconfirmed"
    elif done:
        row["status"] = "server_done_playback_unconfirmed"
    else:
        row["status"] = "incomplete_or_unobserved"

    match = row["sample_match"]
    match.update(server_samples=server["sent_samples"], unity_received=unity["received"],
                 unity_consumed=unity["consumed"])
    if played and done and server["sent_samples"] is not None and unity["received"] is not None:
        match["checked"] = True
        match["match"] = int(server["sent_samples"]) == int(unity["received"])
        match["reason"] = "compared" if match["match"] else "sent_received_differ"


def collect_inputs(server_events, unmapped_asr):
    """입력 후보 판정과 말끝 판정을 사유별로 센다. 응답 턴에 귀속하지 않는다."""
    rejected, endpoint, candidate = {}, {}, {}
    assigned = 0
    for event in server_events:
        reason = event["reason"] or "other"
        if event["event"] == "input.rejected":
            rejected[reason] = rejected.get(reason, 0) + 1
        elif event["event"] == "input.endpoint":
            endpoint[reason] = endpoint.get(reason, 0) + 1
        elif event["event"] == "input.candidate":
            candidate[reason] = candidate.get(reason, 0) + 1
        elif event["event"] == "input.assigned":
            assigned += 1
    values = sorted(unmapped_asr)
    return {"candidate": candidate, "rejected": rejected, "endpoint": endpoint,
            "assigned": assigned,
            "asr_without_turn": {"calls": len(values),
                                 "asr_max_ms": values[-1] if values else None,
                                 "asr_avg_ms": round(sum(values) / len(values), 1) if values else None},
            "note": "입력 후보는 응답 턴이 정해지기 전의 사건이라 연결 단위로 센다. "
                    "STT 시간은 input.assigned 로 후보와 턴이 이어진 경우에만 그 턴에 붙인다. "
                    "거절된 후보와 매핑 없는 후보의 시간은 여기에 남고 어떤 턴의 값도 아니다."}


def collect_events(events):
    """정제된 사건을 그대로 싣는다. 요약이 버린 수치까지 나중에 다시 볼 수 있어야 한다.

    말끝(tail_rms/tail_silence_ms), 장치(output.state 의 볼륨·포커스), 입력(input.sample 의
    마이크 계수) 같은 값은 표에는 없지만 여기 남는다. 담기는 것은 파서를 통과한
    허용 코드와 유한한 숫자뿐이고, 원문이나 미지 필드는 이미 버려진 뒤다.

    Unity ms 와 서버 uptime_s 는 서로 다른 기기의 단조시간이다. 벽시계로 늘어놓기만
    하고 두 값을 빼지 않는다. 순서 자체도 시계가 맞다는 보장이 없어 표시만 한다.
    """
    rows = [{"source": event["source"], "wall": event["wall"], "clock_s": event["clock_s"],
             "event": event["event"], "reason": event["reason"] or None,
             "turn": turn_of(event["values"]), "periodic": event["event"] in PERIODIC,
             "values": event["values"]}
            for event in events]
    rows.sort(key=lambda row: (row["wall"] or "", row["source"], row["clock_s"] or 0))
    return rows


def collect_playback_stops(unity_events):
    rows = []
    for event in unity_events:
        if event["event"] not in ("playback.stopped", "playback.pause_forced",
                                  "experience.end", "dialogue.fail",
                                  "application.focus", "application.pause",
                                  "audio.config_changed", "main_thread.stall"):
            continue
        rows.append({"event": event["event"], "reason": event["reason"] or None,
                     "clock_s": event["clock_s"], "turn": turn_of(event["values"]),
                     "received": event["values"].get("received"),
                     "consumed": event["values"].get("consumed"),
                     "sec": event["values"].get("sec")})
    return rows


# --- 신호 ------------------------------------------------------------------
# 원인을 정하지 않는다. 관측된 사실과, 사람이 확인해 볼 영역만 적는다.

SIGNAL_TEXT = {
    "server_unchecked": ("서버 로그를 확인하지 않았다. 서버 쪽 값은 없음(—)이며 0 이 아니다.",
                         ["--server-log 또는 --ssh 로 다시 수집"]),
    "server_empty": ("서버 로그를 열었으나 이 trace 의 줄이 없다. 회전으로 밀렸거나 진단이 꺼져 있었을 수 있다.",
                     ["서버 진단 설정", "로그 회전 시점"]),
    "experience_ongoing": ("체험 종료 기록이 없다. 진행 중이거나 로그가 끊겼다. 완료로 읽지 않는다.",
                           ["체험을 끝내고 다시 보고서 생성"]),
    "log_truncated": ("로그 일부가 해석되지 않았다. 빠진 사건이 있을 수 있다.",
                      ["로그 파일 상한", "디스크·권한"]),
    "producer_truncated": ("로거가 파일 상한에 닿아 뒷부분을 남기지 못했다(trace.limit). "
                           "그 뒤 구간은 기록 자체가 없다.",
                           ["체험 길이", "진단 파일 상한"]),
    "reader_limit": ("이 도구의 읽기 상한에 닿아 뒷부분을 읽지 않았다. 부분 보고서다.",
                     ["로그 크기", "--unity-log 로 대상 좁히기"]),
    "unknown_version": ("아는 진단 버전이 아닌 줄이 있다. 그 줄의 버전 값은 보고서에 옮기지 않았다.",
                        ["Unity·서버 진단 버전", "로그 출처"]),
    "legacy_schema": ("구버전 스키마다. 새 항목은 기록 자체가 없어서 비어 있다. "
                      "필드가 없는 것과 값이 0 인 것은 다르다.",
                      ["Unity·서버 진단 버전"]),
    "underrun_observed": ("응답 재생 중 언더런이 관측됐다.",
                          ["수신 간격", "재생 버퍼·프리롤", "메인 스레드 멈춤"]),
    "cumulative_counters_only": ("응답별 정리 기록이 없어 체험 누적 카운터만 보인다. "
                                 "언더런·렌더·콜백을 이 턴의 값으로 읽지 않는다.",
                                 ["Unity 진단 v2 적용 여부"]),
    "no_first_render": ("PCM 은 받았는데 첫 렌더 기록이 없다.",
                        ["AudioSource 상태", "출력 장치", "재생 시작 경로"]),
    "no_audio_received": ("이 턴에 받은 PCM 기록이 없다.",
                          ["서버 생성", "전송", "취소 여부"]),
    "first_audio_not_recorded": ("이 구버전 로그에는 첫 수신·첫 렌더 사건이 없다. "
                                 "수신 샘플은 0 이 아니므로 PCM 이 없었다는 뜻이 아니다.",
                                 ["Unity 진단 v2 적용 여부"]),
    "stt_unmapped": ("이 턴에 이어지는 input.assigned 가 없어 STT 시간을 붙이지 못했다. "
                     "STT 가 느렸다는 뜻도 빨랐다는 뜻도 아니다.",
                     ["서버 진단 v4 적용 여부", "입력 후보 통계"]),
    "injected_audio": ("파일로 주입한 오디오가 섞인 구간이다. 실제 마이크·실제 재생 경로와 다르다.",
                       ["검사 방식"]),
    "playback_stopped_early": ("완료가 아니라 중단으로 끝났다.",
                               ["중단 사유 코드", "새 응답·취소 시점"]),
    "sample_mismatch": ("서버 송신 샘플과 Unity 수신 샘플이 다르다.",
                        ["전송 손실", "경계 누계 계산"]),
    "reaction_only": ("대기 리액션 음성만 관측되고 본답변 첫 PCM 기록이 없다.",
                      ["본답변 생성", "리액션 전환 시점"]),
    "input_turn_mismatch": ("입력 턴과 응답 턴이 다르다. 보류 후 재개면 정상일 수 있다.",
                            ["보류·재개 경로"]),
    "user_marker": ("사용자가 문제 지점을 표시했다.",
                    ["표시 시각 주변 사건"]),
    "missing_completion": ("완료 기록이 없다. 오류 확정이 아니라 미완료 또는 관측 부족이다.",
                           ["해당 턴 이후 사건"]),
}


def signal(code_name, turn=None, detail=None):
    text, areas = SIGNAL_TEXT[code_name]
    return {"code": code_name, "turn": turn, "observation": text,
            "check_areas": areas, "detail": detail, "confidence": "observed"}


def collect_signals(report, unity_info, server_info):
    rows = []
    if not server_info["available"]:
        rows.append(signal("server_unchecked"))
    elif server_info["used"] == 0:
        rows.append(signal("server_empty"))
    if not report["completeness"]["experience_ended"]:
        rows.append(signal("experience_ongoing"))
    if unity_info["invalid_lines"] or server_info["invalid_lines"] or unity_info["oversize_lines"]:
        rows.append(signal("log_truncated",
                           detail={"unity_invalid": unity_info["invalid_lines"],
                                   "server_invalid": server_info["invalid_lines"]}))
    if unity_info["producer_truncated"]:
        rows.append(signal("producer_truncated"))
    if unity_info["size_limit_reached"] or server_info["size_limit_reached"]:
        rows.append(signal("reader_limit"))
    if unity_info["unknown_version_lines"] or server_info["unknown_version_lines"]:
        rows.append(signal("unknown_version",
                           detail={"unity_lines": unity_info["unknown_version_lines"],
                                   "server_lines": server_info["unknown_version_lines"]}))
    if unity_info["legacy_only"] or server_info["legacy_only"]:
        rows.append(signal("legacy_schema",
                           detail={"unity": unity_info["versions"],
                                   "server": server_info["versions"]}))
    for row in report["turns"]:
        turn, unity = row["turn"], row["unity"]
        legacy_shape = unity["underrun_scope"] == "experience_cumulative"
        if row["input_turn_mismatch"]:
            rows.append(signal("input_turn_mismatch", turn, {"input_turns": row["input_turns"]}))
        if legacy_shape:
            rows.append(signal("cumulative_counters_only", turn))
        elif unity["underrun_frames"]:
            rows.append(signal("underrun_observed", turn,
                               {"underrun_frames": unity["underrun_frames"],
                                "rebuffers": unity["rebuffers"], "scope": "response"}))
        if unity["injected"]:
            rows.append(signal("injected_audio", turn))
        if server_info["available"] and not row["stt"]["mapped"]:
            rows.append(signal("stt_unmapped", turn))
        if unity["first_received_ms"] is None and unity["reaction_first_received_ms"] is not None:
            rows.append(signal("reaction_only", turn))
        elif unity["first_received_ms"] is None and (unity["sample_received"] or 0) > 0:
            # 구버전에는 audio.first_received 자체가 없다. 수신이 없었다고 말하면 안 된다.
            rows.append(signal("first_audio_not_recorded", turn,
                               {"sample_received": unity["sample_received"]}))
        elif unity["first_received_ms"] is None and row["status"] != "cancelled":
            rows.append(signal("no_audio_received", turn))
        elif unity["first_render_ms"] is None and unity["receive_to_render_ms"] is None:
            rows.append(signal("no_first_render", turn))
        if unity["status"] == "stopped":
            rows.append(signal("playback_stopped_early", turn, {"reason": unity["stop_reason"]}))
        if row["sample_match"]["checked"] and row["sample_match"]["match"] is False:
            rows.append(signal("sample_mismatch", turn,
                               {"server_samples": row["sample_match"]["server_samples"],
                                "unity_received": row["sample_match"]["unity_received"]}))
        if row["status"] == "incomplete_or_unobserved":
            rows.append(signal("missing_completion", turn))
        for marker in row["markers"]:
            rows.append(signal("user_marker", turn, {"marker": marker["marker"],
                                                     "clock_s": marker["clock_s"]}))
    return rows


# --- 보고서 ----------------------------------------------------------------

def build_report(trace, unity_events, unity_info, server_events, server_info):
    ended = any(event["event"] == "experience.end" for event in unity_events)
    turns, unmapped_asr = collect_turns(unity_events, server_events)
    report = {
        "schema_version": REPORT_SCHEMA, "kind": "dialogue_trace_report",
        "generated_at": datetime.now(timezone.utc).isoformat(), "trace": trace,
        "sources": {"unity": unity_info, "server": server_info},
        "completeness": {
            "server_checked": bool(server_info["available"]),
            "experience_ended": ended,
            "unity_events": unity_info["used"], "server_events": server_info["used"],
            "clock_alignment": "unverified",
            # 손상 줄·상한·로거 잘림·미지 버전 중 하나라도 있으면 부분 기록이다.
            # 빠진 기록을 정상 완료로 포장하지 않는다.
            "partial": any(info[key] for info in (unity_info, server_info)
                           for key in ("invalid_lines", "oversize_lines", "size_limit_reached",
                                       "producer_truncated", "unknown_version_lines")),
            "note": ("Unity ms 는 체험 시작 후, 서버 uptime_s 는 연결 시작 후 단조시간이다. "
                     "서로 다른 기기의 값이라 빼서 네트워크 지연으로 읽지 않는다. "
                     "로그는 읽은 시점의 스냅샷이라 체험이 진행 중이면 뒤에 더 있을 수 있다."),
        },
        "turns": turns,
        "inputs": collect_inputs(server_events, unmapped_asr),
        "events": collect_events(unity_events + server_events),
        "playback_stops": collect_playback_stops(unity_events),
        "markers": [],
        "limits": [
            "기록이 없는 값은 — 또는 null 이며 0 이나 정상으로 읽지 않는다.",
            "서버가 보고한 초 단위 값은 생성 시각이며 실제로 들린 시간이 아니다.",
            "turn.done 은 서버 응답 종료이고 재생 완료가 아니다. 재생 완료는 playback.completed 로만 본다.",
            "일반 마이크 경로는 후보 검증 STT 가 respond 전에 끝나므로 STT 를 따로 잰다. "
            "first_text_ms 는 respond 시작 기준이며, 예외적으로 respond 안에서 전사할 때는 "
            "그 시간이 포함될 수 있다. llm_first_ms 는 LLM 호출 기준이다.",
            "tts_total_ms 와 tts_max_gap_ms 에는 보류 대기와 전송 대기가 포함될 수 있다. "
            "합성만의 시간이 아니다.",
            "대기 리액션 TTS 와 본답변 TTS 는 따로 센다. 둘 중 이른 값을 본답변 값으로 쓰지 않는다.",
            "input.started 는 Unity 가 서버 speech.started 를 받은 시점이다. "
            "마이크 raw VAD 가 말을 감지한 시점이 아니다.",
            "events 배열은 파서를 통과한 사건 전부다. 표에 없는 수치도 여기서 찾는다.",
            "playback.paused 의 reason 은 클라이언트가 멈춘 지점의 고정 코드다. 아는 정지 지점은 "
            "서버가 보낸 구절 경계뿐이며 낱말 단위 정지를 뜻하지 않는다. "
            "pause_expected_sample 이 -1 이면 예약한 경계가 없었다는 뜻이고 0 이 아니다.",
            "구버전 로그에 없는 사건은 '그 종류의 사건을 관측하지 못함'이며 '일어나지 않음'이 아니다.",
            "신호는 관측과 확인 영역이며 근본 원인 확정이 아니다.",
        ],
    }
    report["markers"] = [dict(marker, turn=row["turn"])
                         for row in report["turns"] for marker in row["markers"]]
    report["signals"] = collect_signals(report, unity_info, server_info)
    return report


def cell(value, digits=1):
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, float):
        return ("%.*f" % (digits, value)).rstrip("0").rstrip(".") or "0"
    return str(value)


STATUS_TEXT = {
    "complete": "완료",
    "cancelled": "취소",
    "playback_stopped": "재생 중단",
    "unity_complete_server_unconfirmed": "재생완료·서버미확인",
    "server_done_playback_unconfirmed": "서버완료·재생미확인",
    "incomplete_or_unobserved": "미완료 또는 관측 부족",
    "unknown": "알 수 없음",
}


def render_markdown(report):
    unity, server = report["sources"]["unity"], report["sources"]["server"]
    out = ["# 대화 진단 보고서", "",
           "- trace: `%s`" % report["trace"],
           "- 생성: %s" % report["generated_at"],
           "- 서버 로그 확인: %s" % ("예" if report["completeness"]["server_checked"] else "아니오(서버 미확인)"),
           "- 체험 종료 기록: %s" % ("예" if report["completeness"]["experience_ended"] else "아니오(진행 중이거나 로그 끊김)"),
           "- 부분 기록: %s" % ("예(뒷부분 없음)" if report["completeness"]["partial"] else "아니오"),
           "", "## 1. 자료", "",
           "| 출처 | 확보 | 스키마 | 읽은 줄 | 사용 | 해석 실패 | 다른 trace | 미지 사건 | 미지 코드 | 미지 버전 | 상한 |",
           "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, info in (("Unity", unity), ("서버", server)):
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            name, "예" if info["available"] else "아니오",
            "/".join(str(value) for value in info["versions"]) or "—",
            info["lines"], info["used"], info["invalid_lines"], info["other_trace_lines"],
            info["unknown_events"], info["unknown_codes"], info["unknown_version_lines"],
            "예" if info["size_limit_reached"] else "아니오"))
    if unity["producer_truncated"]:
        out.append("")
        out.append("Unity 로거가 파일 상한에 닿았다(`trace.limit`). 그 뒤 구간은 기록이 없다.")
    if unity["last_line_complete"] is False:
        out.append("")
        out.append("Unity 로그 마지막 줄에 개행이 없다. 쓰는 중이었을 수 있다. 그 자체로 결함은 아니다.")
    if server.get("note"):
        out.append("")
        out.append("서버 수집 결과 코드: `%s`" % server["note"])

    out += ["", "## 2. 턴별 진행", "",
            "| 턴 | 모드 | 입력턴 | STT최대ms | 서버첫문장s | 서버첫음성s | 첫리액션s | 서버총s | "
            "첫PCM수신ms | 첫렌더ms | 서버송신샘플 | 수신 | 소비 | 언더런 | 상태 |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for row in report["turns"]:
        u, s = row["unity"], row["server"]
        underrun = cell(u["underrun_frames"])
        if u["underrun_scope"] == "experience_cumulative" and u["underrun_frames"] is not None:
            underrun += "(체험누적)"
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["turn"], cell(row["mode"]),
            ",".join(str(value) for value in row["input_turns"]) or "—",
            cell(row["stt"]["asr_max_ms"]) if row["stt"]["mapped"] else "미확인",
            cell(s["first_text_sec"], 2),
            cell(s["first_audio_sec"], 2), cell(s["first_reaction_audio_sec"], 2),
            cell(s["total_sec"], 2), cell(u["first_received_ms"]), cell(u["first_render_ms"]),
            cell(s["sent_samples"]), cell(u["received"]), cell(u["consumed"]),
            underrun, STATUS_TEXT.get(row["status"], row["status"])))
    if not report["turns"]:
        out.append("| — | — | — | — | — | — | — | — | — | — | — | — | — | — | 턴 기록 없음 |")

    out += ["", "### 생성 단계와 응답 수명", "",
            "| 턴 | 모드 출처 | 판정ms | 첫텍스트ms(respond) | LLM호출ms | 응답 수명 | 중단 사유 | 주입 |",
            "|---|---|---|---|---|---|---|---|"]
    for row in report["turns"]:
        u, s = row["unity"], row["server"]
        life = " → ".join(item["stage"] for item in u["lifecycle"]) or "—"
        out.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            row["turn"], cell(row["mode_source"]), cell(row["route_ms"]),
            cell(s["first_text_ms"]), cell(s["llm_first_ms"]), life,
            cell(u["stop_reason"]), cell(u["injected"])))

    out += ["", "### 본답변과 대기 리액션", "",
            "| 턴 | 첫 음성 종류 | 본답변 첫 PCM ms | 리액션 첫 PCM ms | 첫렌더까지 ms | 경계 수 | "
            "본답변 TTS(시작/완료, 첫ms) | 리액션 TTS(시작/완료, 첫ms) | 샘플 대조 |",
            "|---|---|---|---|---|---|---|---|---|"]
    for row in report["turns"]:
        u, match = row["unity"], row["sample_match"]
        if match["checked"]:
            compare = "일치" if match["match"] else "불일치"
        else:
            compare = "대조 불가(관측 부족)"
        answer, reaction = row["tts"]["answer"], row["tts"]["reaction"]
        out.append("| %s | %s | %s | %s | %s | %s | %s/%s, %s | %s/%s, %s | %s |" % (
            row["turn"], cell(u["first_audio_kind"]), cell(u["first_received_ms"]),
            cell(u["reaction_first_received_ms"]), cell(u["receive_to_render_ms"]),
            u["boundaries"],
            answer["started"], answer["done"], cell(answer["first_ms"]),
            reaction["started"], reaction["done"], cell(reaction["first_ms"]), compare))

    out += ["", "## 3. 입력 판정", ""]
    inputs = report["inputs"]
    for title, bucket in (("후보", inputs["candidate"]), ("기각", inputs["rejected"]),
                          ("말끝", inputs["endpoint"])):
        listed = ", ".join("%s=%d" % pair for pair in sorted(bucket.items())) or "—"
        out.append("- %s: %s" % (title, listed))
    out.append("- 턴에 이어진 후보(input.assigned): %d건" % inputs["assigned"])
    without = inputs["asr_without_turn"]
    out.append("- 턴에 붙이지 못한 STT: %d건, 최대 %sms, 평균 %sms" % (
        without["calls"], cell(without["asr_max_ms"]), cell(without["asr_avg_ms"])))
    out.append("- %s" % inputs["note"])

    out += ["", "## 4. 재생 중단·전환·표시", "",
            "| 시각(체험 후 s) | 사건 | 사유 | 턴 | 수신 | 소비 |", "|---|---|---|---|---|---|"]
    for row in report["playback_stops"]:
        out.append("| %s | %s | %s | %s | %s | %s |" % (
            cell(row["clock_s"], 3), row["event"], cell(row["reason"]), cell(row["turn"]),
            cell(row["received"]), cell(row["consumed"])))
    if not report["playback_stops"]:
        out.append("| — | — | — | — | — | 기록 없음 |")
    if report["markers"]:
        out += ["", "사용자 표시:"] + [
            "- %s번 표시 · 턴 %s · 체험 후 %ss" % (cell(m["marker"]), cell(m["turn"]), cell(m["clock_s"], 3))
            for m in report["markers"]]

    out += ["", "## 5. 관측 신호", ""]
    if report["signals"]:
        for item in report["signals"]:
            head = "턴 %s · " % item["turn"] if item["turn"] is not None else ""
            out.append("- **%s** — %s%s 확인할 영역: %s" % (
                item["code"], head, item["observation"], ", ".join(item["check_areas"])))
    else:
        out.append("- 표시할 신호 없음")

    out += ["", "## 6. 한계", ""] + ["- " + line for line in report["limits"]]
    out.append("")
    return "\n".join(out)


# --- 실행 ------------------------------------------------------------------

def latest_unity_log(root=ROOT):
    directory = Path(root) / UNITY_LOG_DIR
    try:
        files = [path for path in directory.glob("dialogue-*.jsonl") if path.is_file()]
    except OSError:
        raise ReportError("no_unity_log")
    if not files:
        raise ReportError("no_unity_log")
    return max(files, key=lambda path: path.stat().st_mtime)


def trace_of(path):
    """파일 이름의 꼬리, 없으면 첫 줄의 trace 를 쓴다. 둘 다 hex 검증을 거친다."""
    trace = valid_trace(Path(path).stem.rsplit("-", 1)[-1])
    if trace:
        return trace
    scratch = blank_source("unity")
    try:
        with Path(path).open("r", encoding="utf-8", errors="replace") as handle:
            # 큰 줄을 통째로 담지 않도록 본문과 같은 상한 읽기를 쓴다.
            for line in iter_lines(handle, scratch):
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                if isinstance(record, dict):
                    trace = valid_trace(record.get("trace"))
                    if trace:
                        return trace
    except OSError:
        raise ReportError("unity_log_unreadable")
    raise ReportError("invalid_trace")


def write_report(output, report):
    try:
        output.mkdir(parents=True, exist_ok=False)   # 기존 결과를 덮어쓰지 않는다
    except FileExistsError:
        raise ReportError("output_exists")
    except OSError:
        raise ReportError("output_unwritable")
    try:
        (output / "report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        markdown = output / "report.md"
        markdown.write_text(render_markdown(report), encoding="utf-8")
    except OSError:
        raise ReportError("output_unwritable")
    return markdown


def analyze(unity_log, server_log=None, ssh=None, output=None, root=ROOT, runner=subprocess.run):
    unity_log = Path(unity_log)
    if not unity_log.is_file():
        raise ReportError("no_unity_log")
    trace = trace_of(unity_log)
    unity_events, unity_info = read_unity(unity_log, trace)
    if not unity_info["available"]:
        raise ReportError("unity_log_unreadable")
    if server_log:
        server_events, server_info = read_server_file(server_log, trace)
    elif ssh:
        server_events, server_info = collect_via_ssh(ssh, trace, runner=runner)
    else:
        server_events, server_info = empty_server(trace, note="not_requested")
    report = build_report(trace, unity_events, unity_info, server_events, server_info)
    if output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = Path(root) / REPORT_DIR / ("%s-%s" % (stamp, trace))
    return write_report(Path(output), report), report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--latest", action="store_true",
                        help="가장 최근 Unity 진단 로그를 쓴다(기본값)")
    parser.add_argument("--unity-log", type=Path, help="Unity 진단 JSONL 경로")
    parser.add_argument("--server-log", type=Path, help="내려받아 둔 서버 진단 로그 경로")
    parser.add_argument("--ssh", help="서버에서 이 trace 만 수집할 SSH 별칭(명시할 때만 접속)")
    parser.add_argument("--output", type=Path, help="새 결과 디렉터리(기존 디렉터리는 거부)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.unity_log and args.latest:
            raise ReportError("bad_arguments")
        if args.server_log and args.ssh:
            raise ReportError("bad_arguments")
        if args.ssh is not None and not ALIAS_RE.match(args.ssh):
            raise ReportError("ssh_alias_rejected")
        unity_log = args.unity_log or latest_unity_log()
        output = args.output
        if output is not None and not output.is_absolute():
            output = ROOT / output
        markdown, _ = analyze(unity_log, args.server_log, args.ssh, output)
    except ReportError as error:
        print(error.code, file=sys.stderr)
        return 2
    print(str(markdown.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
