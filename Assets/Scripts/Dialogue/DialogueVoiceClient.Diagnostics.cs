// DialogueVoiceClient.Diagnostics.cs
// 2026-09-16 진단 보강(v2). 새 관측만 담는 partial 이다.
//
// 이 파일은 상태를 읽어 기록만 한다. 재생·전송·취소·보류 계약과 700 ms 경계 대기,
// 씬·UI 는 건드리지 않는다. 기록이 실패해도 대화는 그대로 이어져야 한다.
//
// 2026-09-17 멈춤 경계 관측을 더했다. 아래 Diagnostics* 함수는 인수로 받은 값만 쓰고
// 정지 상태 기계의 필드를 읽지 않는다. 호출 지점은 Realtime 쪽에 한 줄씩 들어간다.
//
// 기존 파일에는 호출 한 줄씩만 들어간다. 계측의 의미는 전부 여기 주석에 모아 둔다.
//
// 읽는 법 세 가지.
// 1) received/consumed 는 렌더러가 응답마다 0 으로 되돌리므로 그 자체가 응답별 값이다.
//    rendered/callbacks/underrun_frames/rebuffers 는 체험 동안 계속 쌓이므로
//    응답 시작 때의 값을 빼서 이 응답의 delta 로 남긴다.
// 2) 재생이 끝나면 _playback.Stop() → 렌더러 Reset 이 received/consumed/ended 를 0 으로 지운다.
//    그래서 completed/stopped 는 반드시 Stop 을 부르기 전에 찍는다.
// 3) turn 은 서버 응답 턴이고 input_turn 은 지금 사용자 턴이다. 보류한 답변을 재개하면 둘이 다르다.

using System;
using UnityEngine;

public partial class DialogueVoiceClient
{
    // 24 kHz PCM16 기준 꼬리 관측 창. 마지막 50 ms.
    const int TailSamples = 1200;
    const float TailSilenceLevel = 2.5e-4f;   // 16 bit 로 약 8 LSB. 이 아래는 무음으로 본다

    int _responseSeq;
    long _baseRendered, _baseCallbacks, _baseUnderrunFrames, _baseRebuffers;

    float _firstAudioAt = -1;           // 이 응답에서 PCM 을 처음 받은 시각
    bool _firstRenderLogged, _firstAnswerAudio, _firstReactionAudio;
    int _serverBoundaries, _playedBoundaries;
    long _expectedSamples;              // 서버가 알려 준 이 응답 누계 샘플
    bool _completedLogged, _sawResponseAudio;
    string _stopReason = "unspecified";

    float _tailRms, _tailPeak, _tailSilenceMs;
    bool _tailValid;

    // --- 멈춤 경계 관측 ---
    // 아는 정지 지점은 서버가 보낸 구절 경계(audio.boundary.samples)뿐이다. 낱말 타임스탬프가
    // 없으므로 "낱말에서 정확히 멈췄다" 는 주장을 하지 않는다. 남기는 것은 회차 번호와
    // 예약한 경계·실제로 멈춘 표본·요청에서 보고까지의 시간뿐이고, 답변 원문·자막·판정 문장은 없다.
    float _pauseRequestedAt = -1;      // 이번 회차의 response.paused 를 받은 시각
    long _pauseExpectedSample = -1;    // 예약한 구절 경계. 예약이 없었으면 -1 이다

    int _micChunks;
    long _micSamples;
    int _markerSeq;
    AudioSource _diagSource;
    bool _diagFocus = true;

    /// <summary>진단 로그가 상한에 닿아 잘렸는가. 기록 부재를 "사건 없음" 으로 읽지 않기 위한 표시다.</summary>
    public bool TraceLogTruncated { get; private set; }
    /// <summary>쓰기가 실패해 기록이 끊겼는가.</summary>
    public bool TraceLogFailed { get; private set; }

    /// <summary>재생 비교 모드(응답 전체 수신 후 AudioClip)인가. 플레이어 빌드에서는 항상 false 다.
    /// 이 모드에서는 DSP 프레임·콜백을 재지 않으므로 그 값들을 -1(미측정)로 남긴다.</summary>
    bool WholeResponsePlayback => _playback != null && _playback.ReceiveWholeResponse;

    void DiagnosticsBeginExperience()
    {
        _responseSeq = 0;
        _micChunks = 0;
        _micSamples = 0;
        _markerSeq = 0;
        _stopReason = "unspecified";
        TraceLogTruncated = TraceLogFailed = false;
        _diagSource = GetComponent<AudioSource>();
        _diagFocus = Application.isFocused;
        DiagnosticsResetResponse();
    }

    /// <summary>응답 하나 분량의 관측만 지운다. 체험 전체 계측은 남는다.</summary>
    void DiagnosticsResetResponse()
    {
        var snapshot = _playback != null ? _playback.Snapshot() : default;
        _baseRendered = snapshot.renderedFrames;
        _baseCallbacks = snapshot.callbacks;
        _baseUnderrunFrames = snapshot.underrunFrames;
        _baseRebuffers = snapshot.rebuffers;
        _firstAudioAt = -1;
        _firstRenderLogged = _firstAnswerAudio = _firstReactionAudio = false;
        _serverBoundaries = _playedBoundaries = 0;
        _expectedSamples = 0;
        _completedLogged = _sawResponseAudio = false;
        _tailValid = false;
        _tailRms = _tailPeak = _tailSilenceMs = 0;
        _pauseRequestedAt = -1;
        _pauseExpectedSample = -1;
    }

    /// <summary>로거가 잘렸는지·실패했는지를 체험이 끝나기 전에 읽어 둔다.</summary>
    void DiagnosticsEndExperience()
    {
        if (_trace == null) return;
        TraceLogTruncated = _trace.Truncated;
        TraceLogFailed = _trace.Failed;
    }

    void DiagnosticsInputStarted(int turn)
    {
        _trace?.Write("input.started", null,
            new[] { "input_turn", "turn" },
            new double[] { turn, _responseTurn });
    }

    void DiagnosticsInputStopped()
    {
        _trace?.Write("input.stopped", null,
            new[] { "input_turn", "turn" },
            new double[] { _dialogueTurn, _responseTurn });
    }

    /// <summary>전사는 길이만 센다. 원문은 로거까지 가지 않는다.</summary>
    void DiagnosticsTranscript(string text, bool final)
    {
        _trace?.Write("input.transcript", final ? "final" : "partial",
            new[] { "input_turn", "turn", "chars", "final" },
            new double[] { _dialogueTurn, _responseTurn, text == null ? 0 : text.Length, final ? 1 : 0 });
    }

    /// <summary>response.started 에서 _responseTurn 을 채운 뒤에 부른다.
    /// 그래야 앞선 StopStreamPlayback 이 이전 응답의 관측을 먼저 남길 수 있다.</summary>
    void DiagnosticsResponseStarted()
    {
        _responseSeq++;
        DiagnosticsResetResponse();
    }

    void DiagnosticsMicSent()
    {
        _micChunks++;
        _micSamples += DialogueSamples;
    }

    /// <summary>재생 큐에 실제로 들어간 PCM 만 센다. kind 는 서버가 준 answer/reaction 이다.</summary>
    void DiagnosticsAudioReceived(string kind, byte[] pcm)
    {
        bool reaction = kind == "reaction";
        _sawResponseAudio = true;
        if (_firstAudioAt < 0) _firstAudioAt = Time.realtimeSinceStartup;
        MeasureTail(pcm);

        // 대기 리액션과 본답변의 첫 소리는 따로 본다. 첫 리액션 시각을 첫 본답변 시각으로 읽으면 안 된다.
        if (reaction ? _firstReactionAudio : _firstAnswerAudio) return;
        if (reaction) _firstReactionAudio = true; else _firstAnswerAudio = true;
        var snapshot = _playback != null ? _playback.Snapshot() : default;
        _trace?.Write("audio.first_received", reaction ? "reaction" : "answer",
            new[] { "turn", "input_turn", "response_seq", "kind", "received" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq, reaction ? 1 : 0, snapshot.received });
    }

    /// <summary>서버가 알려 준 문장 경계. samples 는 이 응답의 누계다.</summary>
    void DiagnosticsAudioBoundary(DialogueEvent message)
    {
        _serverBoundaries++;
        _expectedSamples = message.samples;
        bool reaction = message.kind == "reaction";
        var snapshot = _playback != null ? _playback.Snapshot() : default;
        if (_tailValid)
            _trace?.Write("audio.boundary", reaction ? "reaction" : "answer",
                new[] { "turn", "input_turn", "response_seq", "boundary", "expected_samples",
                        "received", "chars", "kind", "tail_rms", "tail_peak", "tail_silence_ms" },
                new double[] { _responseTurn, _dialogueTurn, _responseSeq, _serverBoundaries,
                               message.samples, snapshot.received, message.text_chars, reaction ? 1 : 0,
                               _tailRms, _tailPeak, _tailSilenceMs });
        else
            _trace?.Write("audio.boundary", reaction ? "reaction" : "answer",
                new[] { "turn", "input_turn", "response_seq", "boundary", "expected_samples",
                        "received", "chars", "kind" },
                new double[] { _responseTurn, _dialogueTurn, _responseSeq, _serverBoundaries,
                               message.samples, snapshot.received, message.text_chars, reaction ? 1 : 0 });
    }

    /// <summary>실제 재생이 그 경계를 지난 시점. 서버 경계와 시간이 벌어지면 재생이 밀린 것이다.</summary>
    void DiagnosticsPlaybackBoundary(DialogueEvent message)
    {
        _playedBoundaries++;
        bool reaction = message != null && message.kind == "reaction";
        var snapshot = _playback != null ? _playback.Snapshot() : default;
        _trace?.Write("playback.boundary", reaction ? "reaction" : "answer",
            new[] { "turn", "input_turn", "response_seq", "boundary", "expected_samples",
                    "received", "consumed", "chars", "kind" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq, _playedBoundaries,
                           message == null ? 0 : message.samples, snapshot.received, snapshot.consumed,
                           message == null ? 0 : message.text_chars, reaction ? 1 : 0 });
    }

    /// <summary>매 프레임. 첫 출력 프레임이 나온 순간만 한 줄 남기고 그 뒤로는 아무 일도 하지 않는다.</summary>
    void DiagnosticsUpdate()
    {
        // 비교 모드는 출력 프레임을 세지 않는다. 재지 않은 것을 실제 render 로 보고하지 않는다.
        if (WholeResponsePlayback) return;
        if (_firstRenderLogged || _firstAudioAt < 0 || _trace == null || _playback == null) return;
        var snapshot = _playback.Snapshot();
        long rendered = snapshot.renderedFrames - _baseRendered;
        if (rendered <= 0) return;
        _firstRenderLogged = true;
        _trace.Write("playback.first_render", null,
            new[] { "turn", "input_turn", "response_seq", "receive_to_render_ms", "rendered" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq,
                           (Time.realtimeSinceStartup - _firstAudioAt) * 1000f, rendered });
    }

    /// <summary>정상 완료. UpdatePlayback 이 playback.done 을 보내기 전에 찍는다.
    /// 이미 남겼거나 로거가 닫혔으면 조용히 넘어간다. 기록이 없다고 실패로 세지 않는다.</summary>
    void DiagnosticsPlaybackCompleted()
    {
        if (_completedLogged || _trace == null) return;
        _completedLogged = true;
        WritePlaybackState("playback.completed", "playback_done");
    }

    /// <summary>다음 StopStreamPlayback 의 원인을 예약한다. 코드 이름만 쓴다.</summary>
    void NoteStopReason(string reason) => _stopReason = reason;

    /// <summary>StopStreamPlayback 의 첫 줄. 리셋으로 값이 지워지기 전에 남긴다.
    /// 정상 완료 직후의 Stop 은 completed 로 이미 남겼으므로 중복해서 쓰지 않는다.</summary>
    void DiagnosticsStopping()
    {
        if (_trace != null && _sawResponseAudio && !_completedLogged)
            WritePlaybackState("playback.stopped", _stopReason);
        _stopReason = "unspecified";
        DiagnosticsResetResponse();
    }

    void WritePlaybackState(string evt, string reason)
    {
        if (_trace == null) return;
        var s = _playback != null ? _playback.Snapshot() : default;
        // 받아 놓고 아직 내보내지 못한 양이다. expected_samples 는 문장 경계에서만 갱신돼서
        // 문장 중간에 끊긴 회차에서는 남은 양을 나타내지 못한다(0 이나 음수처럼 보인다).
        // expected_samples 는 서버가 알린 누계 그대로 따로 싣는다.
        double remaining = Math.Max(0, s.received - s.consumed);
        // 비교 모드는 native 출력이라 DSP 프레임·콜백을 재지 않는다. 추정하지 않고 -1 로 남긴다.
        double rendered = WholeResponsePlayback ? -1 : s.renderedFrames - _baseRendered;
        double callbacks = WholeResponsePlayback ? -1 : s.callbacks - _baseCallbacks;
        _trace.Write(evt, reason,
            new[] { "turn", "input_turn", "response_seq", "received", "consumed", "expected_samples",
                    "remaining", "rendered", "callbacks", "underrun_frames", "rebuffers",
                    "boundary", "complete", "ended", "paused" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq, s.received, s.consumed,
                           _expectedSamples, remaining,
                           rendered, callbacks,
                           s.underrunFrames - _baseUnderrunFrames, s.rebuffers - _baseRebuffers,
                           _playedBoundaries, s.complete ? 1 : 0, s.ended ? 1 : 0, s.paused ? 1 : 0 });
    }

    /// <summary>서버가 보고한 시간이다. 사용자가 실제로 들은 시각이 아니다.
    ///
    /// 서버는 재지 못한 값을 JSON null 로 보내는데 JsonUtility 는 그것을 0 으로 채운다.
    /// 그대로 쓰면 "재지 못함" 이 "0 초" 로 읽힌다. 리액션이 없던 회차의
    /// first_reaction_audio_sec 가 대표적이다. 그래서 0 이하는 -1(미측정)로 남긴다.
    /// 전부 양수여야 하는 값들이라 이 규칙으로 잃는 정보가 없다.
    ///
    /// 함께 싣는 saw_answer/saw_reaction 은 이 클라이언트가 실제로 그 종류의 PCM 을 받았는지다.
    /// 서버 보고와 어긋나면(예: sec 는 있는데 saw 가 0) 전송 구간을 의심할 근거가 된다.
    /// 화면에 쓰는 FirstAudioSeconds 계약은 건드리지 않는다.</summary>
    void DiagnosticsResponseTiming(ResponseTiming timing)
    {
        if (timing == null) return;
        _trace?.Write("response.timing", null,
            new[] { "turn", "input_turn", "response_seq", "first_text_sec", "first_audio_sec",
                    "first_reaction_audio_sec", "first_any_audio_sec", "total_sec",
                    "saw_answer", "saw_reaction" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq,
                           Measured(timing.first_text_sec), Measured(timing.first_audio_sec),
                           Measured(timing.first_reaction_audio_sec),
                           Measured(timing.first_any_audio_sec), Measured(timing.total_sec),
                           _firstAnswerAudio ? 1 : 0, _firstReactionAudio ? 1 : 0 });
    }

    /// <summary>JSON null·필드 누락이 0 으로 들어온 것을 미측정(-1)로 되돌린다.</summary>
    static double Measured(float value) => value > 0 ? value : -1;

    /// <summary>기존 2 초 표본과 같은 시점에 입력·출력 상태를 한 줄씩 더 남긴다.</summary>
    void DiagnosticsPeriodic()
    {
        if (_trace == null) return;
        // injected=1 이면 에디터 검사가 파일 PCM 을 넣는 중이라 마이크를 열지 않는다.
        // 그때의 mic_chunks=0·listening=0 은 고장이 아니다. 플레이어 빌드에서는 -1(해당 없음)이다.
#if UNITY_EDITOR
        double injected = EditorAudioInjection ? 1 : 0;
#else
        double injected = -1;
#endif
        _trace.Write("input.sample", "periodic",
            new[] { "input_turn", "turn", "mic_chunks", "mic_samples", "mic_level",
                    "listening", "waiting", "recording", "connected", "active", "injected" },
            new double[] { _dialogueTurn, _responseTurn, _micChunks, _micSamples, MicLevel,
                           _listening ? 1 : 0, isWaiting ? 1 : 0, isRecording ? 1 : 0,
                           _dialogueReady ? 1 : 0, _experienceActive ? 1 : 0, injected });

        if (_diagSource == null) _diagSource = GetComponent<AudioSource>();
        bool has = _diagSource != null;
        // playback_mode 0=수신 즉시 스트리밍, 1=응답 전체 수신 후 AudioClip(비교 모드).
        _trace.Write("output.state", "periodic",
            new[] { "turn", "input_turn", "source_mute", "source_volume", "source_pitch",
                    "source_enabled", "source_playing", "listener_pause", "listener_volume", "focus",
                    "playback_mode" },
            new double[] { _responseTurn, _dialogueTurn,
                           has && _diagSource.mute ? 1 : 0, has ? _diagSource.volume : -1,
                           has ? _diagSource.pitch : -1, has && _diagSource.enabled ? 1 : 0,
                           has && _diagSource.isPlaying ? 1 : 0,
                           AudioListener.pause ? 1 : 0, AudioListener.volume, _diagFocus ? 1 : 0,
                           WholeResponsePlayback ? 1 : 0 });
    }

    void DiagnosticsFocus(bool focused)
    {
        if (focused == _diagFocus) return;   // 상태가 바뀔 때만 남긴다
        _diagFocus = focused;
        _trace?.Write("application.focus", null,
            new[] { "turn", "input_turn", "focus" },
            new double[] { _responseTurn, _dialogueTurn, focused ? 1 : 0 });
    }

    void DiagnosticsPause(bool paused)
    {
        _trace?.Write("application.pause", null,
            new[] { "turn", "input_turn", "paused" },
            new double[] { _responseTurn, _dialogueTurn, paused ? 1 : 0 });
    }

    /// <summary>서버가 답변을 멈추라고 한 시점. mode 는 서버가 준 phrase/immediate 이며
    /// 아는 코드가 아니면 로거가 other 로 접는다. pauseId 가 0 이면 보고 계약이 없는 구형 서버다.
    /// 구형 서버 요청은 grace를 -1(해당 없음)로 남기고 즉시 정지의 0 ms는 그대로 남긴다.
    ///
    /// 시간은 클라이언트 실시간이다. 에디터 검사가 시계를 고정해도 여기는 따라가지 않으므로
    /// 검사 회차의 pause_wait_ms 는 계약 판정 근거가 아니다.</summary>
    void DiagnosticsPauseRequested(int pauseId, int graceMs, string mode, bool continuing)
    {
        if (!continuing || _pauseRequestedAt < 0) _pauseRequestedAt = Time.realtimeSinceStartup;
        if (!continuing) _pauseExpectedSample = -1;
        var s = _playback != null ? _playback.Snapshot() : default;
        _trace?.Write("response.paused", mode,
            new[] { "turn", "input_turn", "response_seq", "pause_id", "pause_grace_ms",
                    "received", "consumed", "paused" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq, pauseId,
                           pauseId > 0 ? graceMs : -1,
                           s.received, s.consumed, s.paused ? 1 : 0 });
    }

    /// <summary>정지를 걸어 둔 구절 경계를 기억한다. 줄을 따로 쓰지 않는다.
    /// 실제로 멈춘 뒤에는 이 값이 지워질 수 있어서 예약하는 순간에 받아 둔다.</summary>
    void DiagnosticsPauseArmed(long sample) => _pauseExpectedSample = sample;

    void DiagnosticsPauseCleared()
    {
        _pauseRequestedAt = -1;
        _pauseExpectedSample = -1;
    }

    /// <summary>실제로 멈춘 지점을 서버에 보고한 순간. 예약한 경계와 실제 정지 표본을 함께 남겨
    /// "경계에서 멈췄다" 를 숫자로 확인할 수 있게 한다. expected 는 다음 구절의 첫 표본이고
    /// stop 은 보간 중인 원본 표본 위치여서 경계 정지에서는 보통 expected-1이다.
    /// 원인은 reason으로 구별한다. chars 는 전달된 것으로 센 글자 수이며 잘린 구절은 세지 않는다.</summary>
    void DiagnosticsPauseAck(int pauseId, string reason)
    {
        var s = _playback != null ? _playback.Snapshot() : default;
        _trace?.Write("playback.paused", reason,
            new[] { "turn", "input_turn", "response_seq", "pause_id", "pause_expected_sample",
                    "pause_stop_sample", "pause_wait_ms", "chars", "received", "paused" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq, pauseId,
                           _pauseExpectedSample, _playback != null ? _playback.PauseState.appliedSample : -1,
                           PauseWaitMs(), _playedChars,
                           s.received, s.paused ? 1 : 0 });
        _pauseRequestedAt = -1;
    }

    /// <summary>출력이 진행되지 않아 감쇠가 끝나지 못해 벽시계 상한으로 멈춘 회차.
    /// 이 정지는 구절 경계가 아니다. 보고 자체는 뒤이어 playback.paused 로 따로 남는다.</summary>
    void DiagnosticsPauseForced(int pauseId)
    {
        var s = _playback != null ? _playback.Snapshot() : default;
        _trace?.Write("playback.pause_forced", "grace_expired",
            new[] { "turn", "input_turn", "response_seq", "pause_id", "pause_stop_sample",
                    "pause_wait_ms", "received" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq, pauseId,
                           _playback != null ? _playback.PauseState.appliedSample : -1,
                           PauseWaitMs(), s.received });
    }

    /// <summary>멈춤 요청부터 지금까지의 시간. 요청 기록이 없으면 미측정(-1)이다.</summary>
    double PauseWaitMs() =>
        _pauseRequestedAt < 0 ? -1 : (Time.realtimeSinceStartup - _pauseRequestedAt) * 1000f;

    /// <summary>사람이 "지금 이상하다" 고 표시한 지점. 관측만 남기고 응답·마이크는 그대로 둔다.
    /// 진행 중인 체험이 없으면 남길 로그도 없으므로 false 를 돌려준다.</summary>
    public bool MarkIssue()
    {
        if (_trace == null || !_trace.Active) return false;
        _markerSeq++;
        _trace.Write("user.marker", null,
            new[] { "turn", "input_turn", "response_seq", "marker" },
            new double[] { _responseTurn, _dialogueTurn, _responseSeq, _markerSeq });
        TraceSample("playback.sample", "periodic");
        DiagnosticsPeriodic();
        return true;
    }

    /// <summary>받은 PCM 의 마지막 50 ms 를 잰다. 패킷이 그보다 짧으면 있는 만큼만 본다.
    /// 문장 끝 음절 문제를 "수신 단계에서 이미 그랬는가" 로 좁히기 위한 값이고,
    /// 음질·청취 판정은 아니다. silence_ms 는 관측 창인 50 ms 를 넘지 않는다.</summary>
    void MeasureTail(byte[] pcm)
    {
        _tailValid = false;
        if (pcm == null || pcm.Length < 2) return;
        int total = pcm.Length / 2;
        int count = Math.Min(total, TailSamples);
        int start = total - count;
        double sum = 0;
        float peak = 0;
        int trailing = 0;
        bool broken = false;
        for (int i = count - 1; i >= 0; i--)
        {
            int at = (start + i) * 2;
            float value = (short)(pcm[at] | (pcm[at + 1] << 8)) / 32768f;
            float magnitude = value < 0 ? -value : value;
            sum += (double)value * value;
            if (magnitude > peak) peak = magnitude;
            if (!broken)
            {
                if (magnitude <= TailSilenceLevel) trailing++;
                else broken = true;
            }
        }
        _tailRms = (float)Math.Sqrt(sum / count);
        _tailPeak = peak;
        _tailSilenceMs = trailing * 1000f / DialogueAudioRenderer.SourceRate;
        _tailValid = true;
    }
}
