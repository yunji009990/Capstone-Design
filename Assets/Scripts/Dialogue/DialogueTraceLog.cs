// DialogueTraceLog.cs
// 체험 한 번을 JSONL 로 남기는 제한된 진단 로거.
//
// 남기는 것: 시각(UTC + 단조), 가명 trace_id, 사건 이름, 원인 코드, 숫자 계측.
// 남기지 않는 것: 사용자 발화·모델 답변 원문, 실제 session ID, 등록 인물 정보,
// 토큰·URL·환경변수, PCM, 장치 이름. 예외는 형식 이름만 남기고 메시지는 넣지 않는다.
//
// 저장에 실패해도 대화가 끊기면 안 된다. 한 번 실패하면 스스로 꺼지고 다시 시도하지 않는다.
//
// v=2 (2026-09-16): 줄마다 "v" 를 붙이고, event/reason/수치 이름을 화이트리스트로 제한한다.
// 예전에는 글자 종류만 걸렀다(Safe). 그러면 영어·숫자로만 된 서버 문자열이나 사용자 문구가
// 그대로 통과한다(예: response.started 의 reason 은 서버가 준 route 문자열이었다).
// 이제는 아는 코드가 아니면 "other" 로 바꾸고, 모르는 수치 이름은 버린 개수만 센다.
// 기존 v 없는 줄은 v1 로 읽는다. 기존 사건·필드 이름과 의미는 그대로 둔다.
//
// 2026-09-17: 멈춤 경계 보고가 늘었다. 사건 두 개(playback.paused, playback.pause_forced)와
// 고정 사유·회차 번호·정지 표본이 더해질 뿐 기존 사건·이름의 뜻은 바뀌지 않으므로 v=2 를 유지한다.

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using UnityEngine;

public sealed class DialogueTraceLog : IDisposable
{
    public const int Version = 2;

    const long MaxFileBytes = 2 * 1024 * 1024;   // 한 파일 상한
    const int MaxFiles = 8;                      // 보관 개수
    const int MaxValues = 24;                    // 한 줄에 담는 수치 개수 상한

    readonly string _traceId;
    readonly System.Diagnostics.Stopwatch _clock = System.Diagnostics.Stopwatch.StartNew();
    StreamWriter _writer;
    string _path = "";
    bool _disabled;
    long _written;
    long _lines, _dropped;
    bool _truncated, _failed;

    /// <summary>실제 세션 ID 가 아닌 이번 실행에만 쓰는 임의 식별자.</summary>
    public string TraceId => _traceId;
    public string Path => _path;
    public bool Active => !_disabled && _writer != null;
    /// <summary>실제로 쓴 줄 수와, 로거가 꺼진 뒤 버린 줄 수.</summary>
    public long Lines => _lines;
    public long Dropped => _dropped;
    /// <summary>파일 상한에 닿아 뒷부분이 없다. 기록 부재를 "사건 없음" 으로 읽으면 안 된다.</summary>
    public bool Truncated => _truncated;
    /// <summary>열기·쓰기가 실패해 기록이 끊겼다.</summary>
    public bool Failed => _failed;

    public DialogueTraceLog(string directory)
    {
        _traceId = Guid.NewGuid().ToString("N").Substring(0, 12);
        try
        {
            Directory.CreateDirectory(directory);
            Prune(directory);
            _path = System.IO.Path.Combine(directory,
                "dialogue-" + DateTime.UtcNow.ToString("yyyyMMdd'T'HHmmss'Z'", CultureInfo.InvariantCulture) +
                "-" + _traceId + ".jsonl");
            _writer = new StreamWriter(new FileStream(_path, FileMode.CreateNew, FileAccess.Write, FileShare.Read),
                new UTF8Encoding(false)) { AutoFlush = true };
        }
        catch (Exception)
        {
            // 저장 권한·경로 문제로 대화를 막지 않는다.
            _failed = true;
            Disable();
        }
    }

    /// <summary>플랫폼별 기본 폴더. 편집기는 Git 에서 제외한 작업 폴더를 쓴다.</summary>
    public static string DefaultDirectory()
    {
#if UNITY_EDITOR
        return System.IO.Path.GetFullPath(System.IO.Path.Combine(Application.dataPath, "..", ".dialogue-work", "logs"));
#else
        return System.IO.Path.Combine(Application.persistentDataPath, "dialogue-logs");
#endif
    }

    void Disable()
    {
        _disabled = true;
        try { _writer?.Dispose(); } catch (Exception) { }
        _writer = null;
    }

    static void Prune(string directory)
    {
        var files = new List<FileInfo>(new DirectoryInfo(directory).GetFiles("dialogue-*.jsonl"));
        if (files.Count < MaxFiles) return;
        files.Sort((a, b) => a.CreationTimeUtc.CompareTo(b.CreationTimeUtc));
        for (int i = 0; i <= files.Count - MaxFiles; i++)
        {
            try { files[i].Delete(); } catch (Exception) { }
        }
    }

    /// <summary>사건 한 줄. names/values 는 짝을 이루는 숫자 계측이다.</summary>
    public void Write(string evt, string reason = null, string[] names = null, double[] values = null)
    {
        if (_disabled || _writer == null) { _dropped++; return; }
        try
        {
            int unknown = 0;
            var line = new StringBuilder(256);
            line.Append("{\"t\":\"").Append(DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture))
                .Append("\",\"ms\":").Append(_clock.ElapsedMilliseconds)
                .Append(",\"v\":").Append(Version)
                .Append(",\"trace\":\"").Append(_traceId)
                .Append("\",\"event\":\"").Append(Code(evt, Events)).Append('"');
            if (!string.IsNullOrEmpty(reason)) line.Append(",\"reason\":\"").Append(Code(reason, Reasons)).Append('"');
            if (names != null && values != null)
            {
                int count = Math.Min(Math.Min(names.Length, values.Length), MaxValues);
                for (int i = 0; i < count; i++)
                {
                    // 모르는 이름은 값을 통째로 버린다. 새 계측을 넣으려면 아래 Names 에 먼저 적는다.
                    if (names[i] == null || !Names.Contains(names[i])) { unknown++; continue; }
                    double value = values[i];
                    if (double.IsNaN(value) || double.IsInfinity(value)) value = -1;
                    line.Append(",\"").Append(names[i]).Append("\":")
                        .Append(value.ToString("0.###", CultureInfo.InvariantCulture));
                }
            }
            if (unknown > 0) line.Append(",\"unknown_names\":").Append(unknown);
            line.Append('}');
            _writer.WriteLine(line.ToString());
            _lines++;
            _written += line.Length + 1;
            if (_written >= MaxFileBytes)
            {
                // 잘렸다는 사실 자체를 파일 안에 남기고 멈춘다.
                _truncated = true;
                _writer.WriteLine("{\"t\":\"" + DateTime.UtcNow.ToString("O", CultureInfo.InvariantCulture) +
                                  "\",\"ms\":" + _clock.ElapsedMilliseconds + ",\"v\":" + Version +
                                  ",\"trace\":\"" + _traceId + "\",\"event\":\"trace.limit\",\"reason\":\"size\"," +
                                  "\"lines\":" + _lines + "}");
                Disable();
            }
        }
        catch (Exception)
        {
            _failed = true;
            Disable();
        }
    }

    /// <summary>아는 코드만 통과시킨다. 서버 문자열·사용자 문구가 섞여 들어와도 여기서 "other" 가 된다.</summary>
    static string Code(string value, HashSet<string> allowed) =>
        !string.IsNullOrEmpty(value) && allowed.Contains(value) ? value : "other";

    // 아래 세 목록이 이 로거가 파일에 쓸 수 있는 전부다. 현재 호출 지점만 담는다.
    // 새 사건·원인·수치를 넣을 때 여기에 함께 적지 않으면 other 로 바뀌거나 버려진다.
    static readonly HashSet<string> Events = new HashSet<string>
    {
        "experience.start", "experience.end", "dialogue.fail", "main_thread.stall",
        "audio.config_changed", "audio.first_received", "audio.boundary",
        "playback.sample", "playback.first_render", "playback.boundary",
        "playback.completed", "playback.stopped",
        // 멈춤 경계: 실제로 멈춘 지점의 보고와, 출력이 멎어 감쇠가 끝나지 못한 강제 정지.
        "playback.paused", "playback.pause_forced",
        "response.started", "response.paused", "response.resumed", "response.done",
        "response.cancelled", "response.timing",
        "input.started", "input.stopped", "input.transcript", "input.sample",
        "output.state", "user.marker", "application.focus", "application.pause", "trace.limit",
    };

    static readonly HashSet<string> Reasons = new HashSet<string>
    {
        // 체험 수명
        "test_profile", "registered", "unspecified", "on_disable", "app_pause",
        "session_changed", "begin_failed", "periodic", "frame_gap", "device", "size",
        // ExperienceControl 이 실제로 쓰는 종료 이유. 자동 종료 원인을 가리는 값이라 반드시 남긴다.
        // ui_finish 에는 눌린 키가 붙는다(_space / _enter / _spaceenter).
        "ui_finish", "ui_finish_space", "ui_finish_enter", "ui_finish_spaceenter", "control_disabled",
        // 실패 코드와 EndExperience 가 붙이는 fail_ 접두 형태
        "client_error", "connection_error", "microphone_stopped", "mic_pump_stall",
        "send_queue_overflow", "playback_overflow",
        "fail_client_error", "fail_connection_error", "fail_microphone_stopped",
        "fail_mic_pump_stall", "fail_send_queue_overflow", "fail_playback_overflow",
        // 재생을 멈춘 원인
        "new_input", "new_response", "user_cancel", "server_cancel", "response_error",
        "reset", "experience_end", "playback_done",
        // 서버가 주는 경로·취소 사유·판정 행동
        "normal", "reasoning", "fallback",
        "user_speech", "newer_utterance", "hold_timeout", "memory_forget",
        "turn_failed", "empty_transcript",
        "resume", "revise", "switch", "hold", "clarify",
        // 멈춤 경계 계약. 요청 쪽 코드는 서버가 준 pause_mode 이고, 보고 쪽 코드는
        // 클라이언트가 실제로 멈춘 지점이다. 자유 문장은 여기에 없으므로 통과하지 못한다.
        // boundary 만 "그 구절을 끝까지 들려줬다"는 뜻이고 나머지는 중간 정지다.
        "phrase", "immediate", "boundary", "grace_expired", "no_audio", "already_paused",
        // 음성 종류와 전사 단계
        "answer", "reaction", "partial", "final",
    };

    static readonly HashSet<string> Names = new HashSet<string>
    {
        // 기존 v1 계측
        "tts", "dsp_rate", "dsp_buffer", "dsp_buffers", "source_rate", "preroll", "sec",
        "turn", "received", "consumed", "buffered", "underrun_frames", "rebuffers",
        "callbacks", "rendered", "ended", "audio_packets", "resp_packets", "resp_audio_span",
        "max_packet_gap", "paused_gap", "max_frame_stall", "cfg_changes", "paused", "listening",
        // v2 공통 축
        "input_turn", "response_seq", "kind", "chars", "final", "lines",
        // 수신·렌더·경계
        "receive_to_render_ms", "boundary", "expected_samples", "remaining", "complete",
        "tail_rms", "tail_peak", "tail_silence_ms",
        // 멈춤 경계 계측. pause_id 는 이 보고가 어느 요청에 대한 것인지 잇는 번호다(구형 서버는 0).
        // expected 는 예약한 구절 경계, stop 은 실제로 멈춘 표본이다. 예약이 없던 회차의
        // expected 는 -1(해당 없음)이며 0 이 아니다. wait_ms 는 요청부터 보고까지의 시간이다.
        "pause_id", "pause_grace_ms", "pause_expected_sample", "pause_stop_sample", "pause_wait_ms",
        // 서버가 보고한 시간(-1 은 미측정)과 그 회차에 실제로 받은 종류
        "first_text_sec", "first_audio_sec", "first_reaction_audio_sec",
        "first_any_audio_sec", "total_sec", "saw_answer", "saw_reaction",
        // 입력·출력 상태 표본
        "mic_chunks", "mic_samples", "mic_level", "waiting", "recording", "connected", "active",
        "injected",
        "source_mute", "source_volume", "source_pitch", "source_enabled", "source_playing",
        "playback_mode",   // 0=스트리밍, 1=응답 전체 AudioClip. 개인 정보가 아니다.
        "listener_pause", "listener_volume", "focus", "marker",
    };

    public void Dispose()
    {
        if (_writer == null) return;
        try { _writer.Dispose(); } catch (Exception) { }
        _writer = null;
    }
}
