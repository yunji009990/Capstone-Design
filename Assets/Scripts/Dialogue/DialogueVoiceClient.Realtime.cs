using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.SceneManagement;

public partial class DialogueVoiceClient
{
    [Header("실시간 음성 대화")]
    [Tooltip("비우면 기존 서버와 같은 호스트의 8002 포트를 사용합니다.")]
    public string dialogueServerUrl = "";

    [Header("AI 테스트 씬 전용")]
    public bool useTestProfile;
    [TextArea(3, 8)] public string testPersona = "너는 한국어로 대화하는 AI 테스트 도우미다. " +
        "친절하고 자연스럽게 짧게 답한다. 제공되지 않은 인물 정보나 기억은 지어내지 않는다. " +
        "사용자가 계산이나 비교를 요청하면 필요한 근거를 간결하게 설명한다.";
    [HideInInspector] public bool captureRealtimeMicrophone = true;
    [HideInInspector] public string testReferenceId = "";
    [HideInInspector] public string testReferenceText = "";
    public string VoiceMode { get; private set; } = "";
    public string ConnectionStatus { get; private set; } = "";
    public DialogueReferenceInfo ActiveReference { get; private set; }

    public event Action<string> OnTranscriptUpdated;
    public event Action<string, string> OnAnswerUpdated;
    public event Action<string, string> OnAnswerInterrupted;
    public event Action<string, string> OnInterruptionDecision;
    public event Action OnDialogueReset;
    public bool ExperienceActive => _experienceActive;
    public bool DialogueConnected => _dialogueReady;
    public string ResponseRoute { get; private set; } = "";
    public string VoiceAudioEvent { get; private set; } = "unknown";
    public string VoiceLanguage { get; private set; } = "unknown";
    public float VoiceDuration { get; private set; }
    public float VoiceRmsDb { get; private set; }
    public bool TranscriptIsFinal { get; private set; }
    public float FirstTextSeconds { get; private set; } = -1;
    public float FirstAudioSeconds { get; private set; } = -1;
    public float TotalResponseSeconds { get; private set; } = -1;
    public long ReceivedAudioSamples => _playback?.Received ?? 0;
    public bool PlaybackFinished { get; private set; }
    public bool ResponsePaused => _playback != null && _playback.Paused;
    public bool DecisionPending { get; private set; }
    public string InterruptionAction { get; private set; } = "";
    public string InterruptionReason { get; private set; } = "";
    public float DecisionSeconds { get; private set; } = -1;
    public string CurrentResponseId => _responseId;
    public long ConsumedAudioSamples => _playback?.Consumed ?? 0;
    /// <summary>마지막으로 서버에 보고한 정지 지점. 보고를 보냈다는 사실 자체의 관측이다.</summary>
    public string PauseAckReason { get; private set; } = "";
    public int PauseAckId { get; private set; }
    public int PauseAckCount { get; private set; }
    /// <summary>멈추라는 요청을 받고 아직 멈추지 않은 상태인가.</summary>
    public bool PausePending => _pausePending;

    public string DialogueServerUrl
    {
        get
        {
            if (!string.IsNullOrWhiteSpace(dialogueServerUrl)) return dialogueServerUrl.TrimEnd('/');
            var uri = new UriBuilder(serverUrl) { Port = 8002, Path = "", Query = "" };
            return uri.Uri.ToString().TrimEnd('/');
        }
    }

    /// <summary>마지막 오류 문구. 표시할 곳이 없어도 남아 있어야 종료 원인을 볼 수 있다.</summary>
    public string LastError { get; private set; } = "";
    public string LastEndReason { get; private set; } = "";
    /// <summary>마지막 진단 로그의 가명 ID 와 경로. 체험이 끝난 뒤에도 남는다.</summary>
    public string TraceId { get; private set; } = "";
    public string TraceLogPath { get; private set; } = "";
    /// <summary>로그 파일을 실제로 열었는가. false 면 저장이 막혀 기록이 없다.</summary>
    public bool TraceLogAvailable { get; private set; }

    DialogueTraceLog _trace;
    float _nextTraceSample, _lastTick, _lastAudioAt;
    float _maxAudioGap, _maxFrameStall, _pausedAudioGap;
    // 기록하는 turn 은 _responseTurn 하나로 통일한다. 예전에는 표본만 따로 _responseTurnTraced 를
    // 썼는데, 그 값이 체험을 새로 시작해도 남아 있어 첫 응답 전 표본이 지난 체험의 턴을 달았다.
    int _audioPackets, _responseAudioPackets;
    int _dspBufferSize, _dspBufferCount;
    long _tracedConfigChanges;
    float _responseFirstAudio, _responseLastAudio;

    DialogueTransport _dialogue;
    bool _experienceActive;
    bool _dialogueReady;
    string _responseId = "";
    string _responseHeard = "";
    int _responseTurn;
    int _dialogueTurn;
    int _sendCursor;
    float _lastMicPump;
    float _connectStarted;
    float _nextContext;
    float _nextPing;
    Camera _contextCamera;
    const int DialogueSamples = 1280; // 80 ms at 16 kHz
    // 구절 경계를 찾지 못했을 때만 쓰는 감쇠 길이. 클릭음만 없앤다.
    const int PauseFadeMillis = 60;
    const float PauseMaxGrace = 1.5f;
    // 출력이 전혀 진행되지 않아 감쇠가 끝나지 못할 때의 마지막 상한.
    const float PauseStallFallback = .15f; // 감쇠·출력 배출·왕복이 서버의 600 ms 여유 안에 들게 한다
    readonly Queue<DialogueEvent> _boundaries = new Queue<DialogueEvent>();
    float _boundaryDue = -1;
    float _pausedBoundaryRemaining = -1;
    int _playedChars;
    bool _audioComplete;

    // 정지 예약 상태. _pauseId 가 0 이면 이번 응답에 예약도 보고 대기도 없다.
    int _pauseId;
    bool _pausePending, _pauseFading, _pauseArmed, _pauseAckSent;
    float _pauseDeadline, _pauseForceAt, _pauseConfirmedAt = -1, _pauseDrain;
    long _pauseArmedSample = -1;
    string _pauseReason = "";

#if UNITY_EDITOR
    /// <summary>에디터 검사에서만 시각을 고정한다. 플레이어 빌드에는 없다.</summary>
    public static Func<float> EditorClock;
#endif

    /// <summary>정지 예약·경계 대기가 쓰는 시각. 검사에서만 고정할 수 있다.</summary>
    float Now
    {
        get
        {
#if UNITY_EDITOR
            if (EditorClock != null) return EditorClock();
#endif
            return Time.realtimeSinceStartup;
        }
    }

    [Serializable]
    class DialogueStart
    {
        public string type = "start";
        public int protocol = 1;
        public string session;
        public int sample_rate = 16000;
        public int channels = 1;
        public string format = "pcm_s16le";
        public bool test_mode;
        public string test_persona;
        public string reference_id, reference_text;
        public string interruption_policy = "semantic_v1";
        // 멈춘 지점을 보고할 수 있다는 선언이다. 서버는 이 보고를 받은 뒤에 답변을 바꾼다.
        public bool pause_ack = true;
        public string trace_id;   // 가명 진단 ID. 실제 session ID 가 아니다.
    }

    [Serializable]
    class DialogueEvent
    {
        public string type;
        public int turn_id;
        public string response_id;
        public string text;
        public string message;
        public string code;
        public string route;
        public string heard, suspended_response_id, action, reason;
        public float decision_sec;
        // 서버가 예전부터 보내던 answer|reaction 구분. 대기 리액션을 본답변과 섞어 재지 않으려고 읽는다.
        public string kind;
        public string pcm, format;
        public int sample_rate, channels, samples, text_chars;
        // 멈춤 회차와 경계를 기다릴 상한, 그리고 즉시 정지 여부.
        public int pause_id, grace_ms;
        public string pause_mode;
        public bool tts;
        public string voice_mode;
        public DialogueReferenceInfo reference;
        public AudioObservation audio;
        public ResponseTiming timing;
    }

    [Serializable]
    class AudioObservation
    {
        public string audio_event, language;
        public Prosody prosody;
    }
    [Serializable] class Prosody { public float duration_sec, rms_dbfs; }

    [Serializable]
    class ResponseTiming
    {
        public float first_text_sec, first_audio_sec, total_sec;
        // 서버가 이미 보내던 값이다. 첫 대기 리액션과 첫 본답변을 구별해 읽는다.
        public float first_reaction_audio_sec, first_any_audio_sec;
    }

    [Serializable] class PlaybackMessage
    {
        public string type, response_id, reason;
        public int text_chars;
        public int pause_id;
    }

    [Serializable]
    class ContextMessage
    {
        public string type = "context";
        public string kind;
        public string text;
    }

    /// <summary>The experience button owns capture and connection lifetime.</summary>
    public bool BeginExperience()
    {
        if (_experienceActive) return true;
        if ((!useTestProfile && !HasSession) || !serverReady) return false;
        try
        {
            StopListening();
            sampleRate = 16000;
            var uri = new UriBuilder(DialogueServerUrl);
            uri.Scheme = uri.Scheme == "https" ? "wss" : "ws";
            uri.Path = uri.Path.TrimEnd('/') + "/dialogue";
            _sendCursor = 0;
            _lastMicPump = _connectStarted = Time.realtimeSinceStartup;
            _dialogueTurn = 0;
            _responseId = "";
            // 지난 체험의 턴 번호가 이번 체험 첫 표본에 남지 않게 여기서 지운다.
            _responseTurn = 0;
            _responseHeard = "";
            InterruptionAction = InterruptionReason = "";
            DecisionSeconds = -1;
            lastHeard = lastAnswer = "";
            ClearResponseMetadata();
            StopStreamPlayback();
#if UNITY_EDITOR
            // 진단용. 새 체험을 시작하면 지난 회차의 완료본까지 버린다.
            DialogueReplayCache.Clear();
#endif
            _dialogueReady = false;
            VoiceMode = "";
            ActiveReference = null;
            ConnectionStatus = "대화 서버에 연결 중입니다";
            _experienceActive = true;
            autoDetect = true;
            // 서버와 같은 가명 ID 로 맞추려면 연결 전에 만들어 start 에 실어야 한다.
            _trace = new DialogueTraceLog(DialogueTraceLog.DefaultDirectory());
            TraceId = _trace.TraceId;
            TraceLogPath = _trace.Path;
            TraceLogAvailable = _trace.Active;
            LastError = LastEndReason = "";
            _lastTick = Time.realtimeSinceStartup;
            PauseAckReason = "";
            PauseAckId = PauseAckCount = 0;
            _nextTraceSample = _lastTick + 2f;
            ResetAudioGap();
            _maxFrameStall = 0;
            _audioPackets = 0;
            DiagnosticsBeginExperience();
            _dialogue = new DialogueTransport(uri.Uri, token,
                JsonUtility.ToJson(new DialogueStart { session = sessionId,
                    test_mode = useTestProfile, test_persona = useTestProfile ? testPersona : null,
                    reference_id = useTestProfile ? testReferenceId : null,
                    reference_text = useTestProfile && !string.IsNullOrEmpty(testReferenceId) ? testReferenceText : null,
                    trace_id = TraceId }));
            _nextPing = Time.realtimeSinceStartup + 5f;
            // DSP 버퍼 크기·개수는 메인 스레드 API 다. 여기서 한 번 읽어 두고 표본마다 재사용한다.
            AudioSettings.GetDSPBufferSize(out _dspBufferSize, out _dspBufferCount);
            _tracedConfigChanges = 0;
            var dsp = _playback != null ? _playback.Snapshot() : default;
            _trace.Write("experience.start", useTestProfile ? "test_profile" : "registered",
                new[] { "tts", "dsp_rate", "dsp_buffer", "dsp_buffers", "source_rate", "preroll" },
                new double[] { TtsEnabled ? 1 : 0, dsp.outputRate, _dspBufferSize, _dspBufferCount,
                               dsp.sourceRate, dsp.prerollSamples });
            OnAnswerUpdated?.Invoke("", "");
            return true;
        }
        catch (Exception e)
        {
            LastError = e.Message;
            EndExperience("begin_failed");
            OnError?.Invoke(e.Message);
            return false;
        }
    }

    public void EndExperience() => EndExperience("unspecified");

    /// <summary>종료 원인을 남기고 끝낸다. reason 은 코드 이름이며 사용자 문구가 아니다.</summary>
    public void EndExperience(string reason)
    {
        if (_experienceActive || _trace != null)
        {
            LastEndReason = reason ?? "unspecified";
            TraceSample("experience.end", LastEndReason);
        }
        _experienceActive = false;
        _dialogueReady = false;
        _dialogue?.Dispose();
        _dialogue = null;
        if (!string.IsNullOrEmpty(_responseId) || isWaiting)
            PublishInterrupted();
        _responseId = "";
        isWaiting = false;
        autoDetect = false;
        NoteStopReason("experience_end");
        StopStreamPlayback();
        StopListening();
        if (_trace != null)
        {
            DiagnosticsEndExperience();
            TraceLogAvailable = _trace.Active;
            _trace.Dispose();
            _trace = null;
            Debug.Log("[Dialogue] 체험 종료 reason=" + LastEndReason +
                      " trace=" + TraceId + " log=" + (TraceLogAvailable ? TraceLogPath : "저장 안 됨"));
        }
#if UNITY_EDITOR
        // 체험이 끝나면 검사 상태도 반드시 풀린다. OnDisable·예외·FailDialogue 모두 여기를 지난다.
        _editorAudioInjection = false;
        // 진단용. 등록 인물이 바뀌는 종료는 완료본까지 버린다. 보통의 체험 종료는 그대로 둔다.
        if (reason == "session_changed") DialogueReplayCache.Clear();
#endif
    }

    void UpdateRealtime()
    {
        // 참조 음성 준비처럼 ready 이전 구간도 프레임 간격을 이어서 잰다.
        // 여기서 갱신하지 않으면 준비 시간이 통째로 메인 스레드 지연으로 잡힌다.
        float tick = Time.realtimeSinceStartup;
        if (_experienceActive && _lastTick > 0)
        {
            float delta = tick - _lastTick;
            if (delta > _maxFrameStall) _maxFrameStall = delta;
            if (delta >= 2f && _trace != null)
                _trace.Write("main_thread.stall", "frame_gap", new[] { "sec" }, new double[] { delta });
        }
        _lastTick = tick;

        var transport = _dialogue;
        if (!_experienceActive || transport == null) return;
        for (int i = 0; i < 128 && transport.TryReceive(out var packet); i++)
        {
            if (!string.IsNullOrEmpty(packet.Error))
            {
                FailDialogue("connection_error", packet.Error);
                return;
            }
            try { HandleDialogueEvent(JsonUtility.FromJson<DialogueEvent>(packet.Json)); }
            catch (Exception e) { FailDialogue("대화 응답을 처리하지 못했습니다: " + e.Message); return; }
            if (_dialogue != transport) return;
        }
        if (!_dialogueReady)
        {
            if (Time.realtimeSinceStartup - _connectStarted > 90f)
                FailDialogue("참조 음성 준비 시간이 초과되었습니다. 서버 상태를 확인하고 다시 시작해주세요.");
            return;
        }
        if (tick >= _nextTraceSample)
        {
            _nextTraceSample = tick + 2f;
            TraceSample("playback.sample", "periodic");
            DiagnosticsPeriodic();
        }

        if (ShouldCaptureMicrophone) PumpMicrophone();
        if (!_experienceActive) return;
        if (Time.realtimeSinceStartup >= _nextPing)
        {
            _nextPing = Time.realtimeSinceStartup + 5f;
            SendControl("{\"type\":\"ping\"}");
        }
        if (Time.realtimeSinceStartup >= _nextContext)
        {
            _nextContext = Time.realtimeSinceStartup + 0.5f;
            ReportViewContext();
        }
    }

    /// <summary>마이크를 직접 잡을지 여부. 에디터 검사에서 파일 PCM을 넣는 동안에만 꺼진다.</summary>
    bool ShouldCaptureMicrophone
    {
        get
        {
#if UNITY_EDITOR
            if (_editorAudioInjection) return false;
#endif
            return !useTestProfile || captureRealtimeMicrophone;
        }
    }

    void PumpMicrophone()
    {
        if (!_listening || _recClip == null || !Microphone.IsRecording(_micDevice))
        {
            FailDialogue("microphone_stopped", "마이크 연결이 끊겼습니다. 마이크를 확인하고 다시 시작해주세요.");
            return;
        }
        if (Time.realtimeSinceStartup - _lastMicPump > 2f)
        {
            FailDialogue("mic_pump_stall", "음성 전송이 지연되어 체험을 중단했습니다. 다시 시작해주세요.");
            return;
        }
        _lastMicPump = Time.realtimeSinceStartup;
        int pos = Microphone.GetPosition(_micDevice);
        if (pos < 0) return;
        int available = (pos - _sendCursor + _recClip.samples) % _recClip.samples;
        while (available >= DialogueSamples)
        {
            int channels = _recClip.channels;
            var samples = new float[DialogueSamples * channels];
            // Unity GetData wraps reads at the end of an AudioClip ring buffer.
            if (!_recClip.GetData(samples, _sendCursor))
            {
                FailDialogue("마이크 음성을 읽지 못했습니다.");
                return;
            }
            var pcm = new byte[DialogueSamples * 2];
            for (int i = 0; i < DialogueSamples; i++)
            {
                float value = 0;
                for (int ch = 0; ch < channels; ch++) value += samples[i * channels + ch];
                short encoded = (short)(Mathf.Clamp(value / channels, -1f, 1f) * 32767f);
                pcm[i * 2] = (byte)encoded;
                pcm[i * 2 + 1] = (byte)(encoded >> 8);
            }
            if (!_dialogue.SendAudio(pcm))
            {
                FailDialogue("send_queue_overflow", "음성 전송이 밀렸습니다. 연결을 확인하고 다시 시작해주세요.");
                return;
            }
            DiagnosticsMicSent();
            _sendCursor = (_sendCursor + DialogueSamples) % _recClip.samples;
            available -= DialogueSamples;
        }
    }

    void HandleDialogueEvent(DialogueEvent message)
    {
        if (message == null) throw new InvalidOperationException("빈 응답");
        switch (message.type)
        {
            case "voice.preparing":
                ConnectionStatus = message.message;
                break;
            case "ready":
                TtsEnabled = message.tts;
                // Start capture after prompt preparation so preparation time is
                // not replayed as a stale microphone backlog on connection.
                if (ShouldCaptureMicrophone)
                {
                    StartListening();
                    if (!_listening || _recClip.frequency != 16000)
                        throw new InvalidOperationException("16000 Hz 마이크를 열 수 없습니다. 장치와 권한을 확인해 주세요.");
                }
                _sendCursor = 0;
                _lastMicPump = Time.realtimeSinceStartup;
                VoiceMode = message.voice_mode ?? "";
                ActiveReference = message.reference;
                ConnectionStatus = TtsEnabled ? "참조 음성 준비 완료" : "텍스트 대화 준비 완료";
                _dialogueReady = true;
                _nextContext = 0;
                ReportUnityAction("사용자가 체험을 시작했다.");
                break;
            case "speech.started":
                _dialogueTurn = message.turn_id;
                isRecording = true;
                isWaiting = false;
                bool suspended = !string.IsNullOrEmpty(_responseId) && message.suspended_response_id == _responseId;
                DiagnosticsInputStarted(message.turn_id);
                if (!suspended)
                {
                    _responseId = _responseHeard = "";
                    NoteStopReason("new_input");
                    StopStreamPlayback();
                    lastAnswer = "";
                    OnAnswerUpdated?.Invoke("", "");
                }
                lastHeard = "";
                DecisionPending = false;
                ClearResponseMetadata(suspended);
                break;
            case "speech.stopped":
                if (message.turn_id != _dialogueTurn) break;
                isRecording = false;
                isWaiting = true;
                // 예약만 된 상태도 판정 대기다. 아직 소리가 나는 중이어도 답변은 멈추는 쪽이다.
                DecisionPending = ResponsePaused || _pausePending;
                DiagnosticsInputStopped();
                break;
            case "transcript.partial":
            case "transcript.final":
                if (message.turn_id != _dialogueTurn) break;
                lastHeard = message.text ?? "";
                if (message.audio != null)
                {
                    VoiceAudioEvent = message.audio.audio_event ?? "unknown";
                    VoiceLanguage = message.audio.language ?? "unknown";
                    if (message.audio.prosody != null)
                    {
                        VoiceDuration = message.audio.prosody.duration_sec;
                        VoiceRmsDb = message.audio.prosody.rms_dbfs;
                    }
                }
                TranscriptIsFinal = message.type == "transcript.final";
                DiagnosticsTranscript(lastHeard, TranscriptIsFinal);
                OnTranscriptUpdated?.Invoke(showHeardSubtitle ? lastHeard : "");
                break;
            case "response.started":
                if (message.turn_id != _dialogueTurn) break;
                // 이전 답변과 사용자의 생각 시간이 이번 답변의 수신 지연으로 잡히면 안 된다.
                ResetAudioGap();
                NoteStopReason("new_response");
                StopStreamPlayback();
#if UNITY_EDITOR
                // 진단용. 새 응답이 시작되면 이전 완료본도 여기서 버려진다.
                DialogueReplayCache.Begin(message.turn_id);
#endif
                _responseId = message.response_id;
                _responseTurn = message.turn_id;
                DiagnosticsResponseStarted();
                // Stop 뒤로 옮겼다. 앞에서 찍으면 이전 응답의 재생 수치가 새 턴 번호를 달고 남는다.
                // 이전 응답의 최종 수치는 위 StopStreamPlayback 이 이미 남겼다.
                TraceSample("response.started", message.route ?? "");
                _responseHeard = message.heard ?? lastHeard;
                ResponseRoute = message.route;
                lastAnswer = "";
                FirstTextSeconds = FirstAudioSeconds = TotalResponseSeconds = -1;
                isWaiting = true;
                OnAnswerUpdated?.Invoke(showHeardSubtitle ? _responseHeard : "", "");
                break;
            case "response.paused":
                if (!MatchesResponse(message)) break;
                DiagnosticsPauseRequested(message.pause_id, message.grace_ms, message.pause_mode ?? "",
                                          _pausePending || ResponsePaused);
                RequestPause(message);
                isWaiting = false;
                break;
            case "interruption.decision":
                if (message.turn_id != _dialogueTurn || message.response_id != _responseId) break;
                InterruptionAction = message.action ?? "";
                InterruptionReason = message.reason ?? "";
                DecisionSeconds = message.decision_sec;
                DecisionPending = false;
                isWaiting = message.action != "hold" && message.action != "resume";
                OnInterruptionDecision?.Invoke(InterruptionAction, message.text ?? "");
                break;
            case "response.resumed":
                if (!MatchesResponse(message)) break;
                TraceSample("response.resumed", "");
                _responseHeard = message.heard ?? _responseHeard;
                lastAnswer = message.text ?? lastAnswer;
                ResponseRoute = message.route;
                // 예약만 되어 있었다면 한 번도 멈추지 않았다. 틈 없이 그대로 이어 말한다.
                bool wasPaused = ResponsePaused;
                ClearPauseSchedule();
                _playback.Resume();
                if (wasPaused && _pausedBoundaryRemaining >= 0)
                    _boundaryDue = Now + _pausedBoundaryRemaining;
                _pausedBoundaryRemaining = -1;
                DecisionPending = false;
                isWaiting = !_playback.Playing && !_audioComplete;
                OnAnswerUpdated?.Invoke(showHeardSubtitle ? _responseHeard : "", lastAnswer);
                break;
            case "response.audio":
                if (!MatchesResponse(message)) break;
                if (message.sample_rate != 24000 || message.channels != 1 || message.format != "pcm_s16le")
                    throw new InvalidOperationException("지원하지 않는 답변 음성 형식입니다.");
                float at = Time.realtimeSinceStartup;
                if (_lastAudioAt >= 0)
                {
                    float gap = at - _lastAudioAt;
                    // 보류 중의 공백은 네트워크 지연이 아니다. 따로 센다.
                    if (ResponsePaused) { if (gap > _pausedAudioGap) _pausedAudioGap = gap; }
                    else if (gap > _maxAudioGap) _maxAudioGap = gap;
                }
                _lastAudioAt = at;
                _audioPackets++;
                _responseAudioPackets++;
                if (_responseFirstAudio < 0) _responseFirstAudio = at;
                _responseLastAudio = at;
                byte[] replayPcm = Convert.FromBase64String(message.pcm);
                try { _playback.Enqueue(replayPcm); }
                catch (InvalidOperationException)
                {
                    FailDialogue("playback_overflow", "음성 재생 버퍼가 가득 찼습니다. 다시 시작해주세요.");
                    return;
                }
#if UNITY_EDITOR
                // 진단용. 재생에 성공한 바이트만 변형 없이 모은다.
                DialogueReplayCache.Append(replayPcm);
#endif
                DiagnosticsAudioReceived(message.kind, replayPcm);
                isWaiting = false;
                break;
            case "audio.boundary":
                if (!MatchesResponse(message)) break;
                DiagnosticsAudioBoundary(message);
                _boundaries.Enqueue(message);
                break;
            case "response.delta":
                if (!MatchesResponse(message)) break;
                lastAnswer += message.text;
                OnAnswerUpdated?.Invoke(showHeardSubtitle ? _responseHeard : "", lastAnswer);
                break;
            case "response.done":
                if (!MatchesResponse(message)) break;
                TraceSample("response.done", "");
                lastAnswer = message.text ?? lastAnswer;
                if (message.timing != null)
                {
                    FirstTextSeconds = message.timing.first_text_sec;
                    FirstAudioSeconds = TtsEnabled ? message.timing.first_audio_sec : -1;
                    TotalResponseSeconds = message.timing.total_sec;
                }
                DiagnosticsResponseTiming(message.timing);
                isWaiting = false;
                _audioComplete = true;
                // 이 응답의 PCM 은 더 오지 않는다. 선버퍼보다 짧은 마지막 조각도 끝까지 내보낸다.
                // 보류 중이면 재생하지 않고 재개 시점에 이어서 나간다.
                if (_playback != null) _playback.MarkComplete();
                if (!TtsEnabled)
                {
                    _responseId = "";
                    PlaybackFinished = true;
                    OnAnswer?.Invoke(showHeardSubtitle ? _responseHeard : "", lastAnswer);
                }
                break;
            case "response.cancelled":
                if (!MatchesResponse(message)) break;
                TraceSample("response.cancelled", message.reason ?? "");
                PublishInterrupted();
                NoteStopReason("server_cancel");
                StopStreamPlayback();
                _responseId = "";
                isWaiting = false;
                if (message.reason == "hold_timeout")
                {
                    InterruptionAction = "expired";
                    InterruptionReason = "보류 시간이 지나 이전 답변을 해제했습니다. 새로 말씀해 주세요.";
                    DecisionSeconds = -1;
                }
                break;
            case "response.skipped":
                if (message.turn_id == _dialogueTurn) isWaiting = false;
                break;
            case "routing.fallback":
                if (message.turn_id == _dialogueTurn) ResponseRoute = "normal (분류 실패)";
                break;
            case "reset.done":
                NoteStopReason("reset");
                StopStreamPlayback();
#if UNITY_EDITOR
                // 진단용. 기록을 지우는 회차다. 완료본도 남기지 않는다.
                DialogueReplayCache.Clear();
#endif
                _dialogueTurn = message.turn_id;
                _responseId = _responseHeard = lastHeard = lastAnswer = "";
                InterruptionAction = InterruptionReason = "";
                DecisionSeconds = -1;
                isWaiting = isRecording = false;
                ClearResponseMetadata();
                OnDialogueReset?.Invoke();
                OnAnswerUpdated?.Invoke("", "");
                break;
            case "error":
                if (message.turn_id != 0 && message.turn_id != _dialogueTurn && !MatchesResponse(message)) break;
                if (message.code == "protocol_error" || message.code == "server_busy")
                { FailDialogue(message.message); break; }
                PublishInterrupted();
                NoteStopReason("response_error");
                StopStreamPlayback();
                _responseId = "";
                isWaiting = false;
                OnError?.Invoke(message.message);
                break;
        }
    }

    void PublishInterrupted()
    {
        if (!string.IsNullOrEmpty(lastHeard) || !string.IsNullOrEmpty(lastAnswer))
            OnAnswerInterrupted?.Invoke(showHeardSubtitle ?
                (string.IsNullOrEmpty(_responseId) ? lastHeard : _responseHeard) : "", lastAnswer);
    }

    bool MatchesResponse(DialogueEvent message) => !string.IsNullOrEmpty(_responseId) &&
        message.response_id == _responseId && message.turn_id == _responseTurn;

    void FailDialogue(string message) => FailDialogue("client_error", message);

    /// <summary>원인 코드와 사용자 문구를 나눠 남긴다. 원문 메시지는 로그에 넣지 않는다.</summary>
    void FailDialogue(string reason, string message)
    {
        LastError = message ?? "";
        if (_trace != null) _trace.Write("dialogue.fail", reason);
        EndExperience("fail_" + reason);
        OnError?.Invoke(message);
    }

    void ClearResponseMetadata(bool preserveAnswer = false)
    {
        VoiceAudioEvent = VoiceLanguage = "unknown";
        TranscriptIsFinal = false;
        VoiceDuration = VoiceRmsDb = 0;
        if (preserveAnswer) return;
        ResponseRoute = "";
        FirstTextSeconds = FirstAudioSeconds = TotalResponseSeconds = -1;
    }

    /// <summary>응답이 바뀔 때마다 수신 간격 계측을 다시 시작한다.</summary>
    void ResetAudioGap()
    {
        _lastAudioAt = -1;
        _maxAudioGap = _pausedAudioGap = 0;
        _responseAudioPackets = 0;
        _responseFirstAudio = _responseLastAudio = -1;
    }

    /// <summary>재생·수신 계측을 한 줄로 남긴다. 주기 표본도 항상 남겨 underrun·장치·마이크
    /// 상태 변화를 놓치지 않는다. 2초 주기라 양이 제한된다.</summary>
    void TraceSample(string evt, string reason)
    {
        if (_trace == null) return;
        var snapshot = _playback != null ? _playback.Snapshot() : default;
        float span = _responseFirstAudio >= 0 && _responseLastAudio >= _responseFirstAudio
            ? _responseLastAudio - _responseFirstAudio : 0f;
        // 비교 모드는 native AudioClip 출력이라 DSP 프레임·콜백을 재지 않는다. 추정하지 않고 -1 로 남긴다.
        double rendered = WholeResponsePlayback ? -1 : snapshot.renderedFrames;
        double callbacks = WholeResponsePlayback ? -1 : snapshot.callbacks;
        _trace.Write(evt, reason,
            new[] { "turn", "received", "consumed", "buffered", "underrun_frames", "rebuffers", "callbacks",
                    "rendered", "ended", "audio_packets", "resp_packets", "resp_audio_span",
                    "max_packet_gap", "paused_gap", "max_frame_stall", "cfg_changes", "paused", "listening",
                    "dsp_rate", "dsp_buffer", "dsp_buffers" },
            new double[] { _responseTurn, snapshot.received, snapshot.consumed, snapshot.buffered,
                           snapshot.underrunFrames, snapshot.rebuffers, callbacks,
                           rendered, snapshot.ended ? 1 : 0, _audioPackets,
                           _responseAudioPackets, span, _maxAudioGap, _pausedAudioGap, _maxFrameStall,
                           snapshot.configChanges, snapshot.paused ? 1 : 0, _listening ? 1 : 0,
                           snapshot.outputRate, _dspBufferSize, _dspBufferCount });

        // 출력 설정이 바뀌면 그 사실만 따로 한 줄 남긴다. 장치 이름은 다루지 않는다.
        if (snapshot.configChanges != _tracedConfigChanges)
        {
            _tracedConfigChanges = snapshot.configChanges;
            AudioSettings.GetDSPBufferSize(out _dspBufferSize, out _dspBufferCount);
            _trace.Write("audio.config_changed", "device",
                new[] { "cfg_changes", "dsp_rate", "dsp_buffer", "dsp_buffers" },
                new double[] { snapshot.configChanges, snapshot.outputRate,
                               _dspBufferSize, _dspBufferCount });
        }
    }

    bool SendControl(string json)
    {
        if (!_dialogueReady || _dialogue == null) return false;
        if (_dialogue.SendText(json)) return true;
        FailDialogue("대화 제어 메시지를 전송하지 못했습니다.");
        return false;
    }

    void StopStreamPlayback()
    {
        // 리셋이 received/consumed/ended 를 지우기 전에 이 응답의 마지막 상태를 남긴다.
        DiagnosticsStopping();
#if UNITY_EDITOR
        // 진단용. 모으던 미완성 PCM 만 버린다. 완료본은 체험을 끝낸 뒤에도 들어야 해서 남긴다.
        DialogueReplayCache.DiscardIncomplete();
#endif
        // 예약·보고 대기를 먼저 지운다. 늦은 보고나 남은 PCM 이 다음 응답으로 새지 않는다.
        ClearPauseSchedule();
        _playback?.Stop();
        _boundaries.Clear();
        _boundaryDue = -1;
        _pausedBoundaryRemaining = -1;
        _playedChars = 0;
        _audioComplete = PlaybackFinished = false;
        DecisionPending = false;
    }

    /// <summary>서버가 답변을 멈추라고 했다. 어디서 멈출지는 여기서 정한다.
    ///
    /// 아는 정지 지점은 서버가 보낸 구절 경계(audio.boundary.samples)뿐이다. 낱말
    /// 타임스탬프는 없고 글자 수로 시각을 추정하지 않는다. 경계가 상한 안에 없으면
    /// 짧게 감쇠해 멈추고 그 정지를 경계라고 부르지 않는다.
    ///
    /// pause_id 가 없는 서버에서는 예전 그대로 즉시 멈추고, 서버가 모르는 보고도 보내지 않는다.</summary>
    void RequestPause(DialogueEvent message)
    {
        if (_playback == null) return;
        if (message.pause_id <= 0)
        {
            if (!ResponsePaused) HardPause();
            return;   // 구형 서버다. 보고 계약이 없다
        }
        _pauseId = message.pause_id;
        _pauseAckSent = false;
        if (ResponsePaused)
        {
            // 콜백이 경계에서 멈춘 직후, 첫 보고 펌프보다 새 요청이 먼저 올 수 있다.
            // 이미 끝까지 재생한 구절의 전달 위치를 잃지 않는다.
            if (_pauseArmed && _playback.PauseState.cause == DialogueAudioRenderer.PauseBoundary)
                TakeArmedBoundary();
            // 이미 멈춰 있다. 남은 잔향 시간을 앞당기지 않고 회차 번호만 새로 보고한다.
            _pausePending = _pauseFading = _pauseArmed = false;
            _pauseArmedSample = -1;
            _pauseReason = "already_paused";
            if (_pauseConfirmedAt < 0) { _pauseConfirmedAt = Now; _pauseDrain = _playback.TailSeconds; }
            return;
        }
        if (message.pause_mode == "immediate")
        {
            // 명시적 보류다. 예약을 버리고 지금 멈춘다.
            HardPause();
            _pauseReason = "immediate";
            _pauseConfirmedAt = Now;
            _pauseDrain = _playback.TailSeconds;
            return;
        }
        if (_playback.Received <= 0)
        {
            // 아직 한 표본도 들려주지 않았다. 기다릴 경계도 뺄 잔향도 없다.
            HardPause();
            _pauseReason = "no_audio";
            _pauseConfirmedAt = Now;
            _pauseDrain = 0;
            return;
        }
        // 새 계약은 grace_ms를 항상 보낸다. 명시한 0은 기본값 대체가 아니라 즉시 감쇠다.
        float grace = Mathf.Clamp(message.grace_ms / 1000f, 0f, PauseMaxGrace);
        float deadline = Now + grace;
        if (_pausePending)
        {
            // 같은 응답에 두 번째 요청이다. 더 늦추지 않고 회차 번호만 최신으로 바꾼다.
            if (deadline < _pauseDeadline) _pauseDeadline = deadline;
            return;
        }
        _pausePending = true;
        _pauseFading = _pauseArmed = false;
        _pauseArmedSample = -1;
        _pauseDeadline = deadline;
        _pauseConfirmedAt = -1;
        _pauseDrain = _playback.TailSeconds;
        _pauseReason = "";
    }

    /// <summary>지금 그 자리에서 멈춘다. 렌더러는 위상과 남은 PCM 을 그대로 두므로 재개하면 이어진다.</summary>
    void HardPause()
    {
        _pausedBoundaryRemaining = _boundaryDue < 0 ? -1 : Mathf.Max(0, _boundaryDue - Now);
        _boundaryDue = -1;
        _pausePending = _pauseFading = _pauseArmed = false;
        _pauseArmedSample = -1;
        _playback.Pause();
    }

    /// <summary>예약·보고 대기를 전부 지운다. 늦은 보고가 다음 회차로 새지 않는다.</summary>
    void ClearPauseSchedule()
    {
        DiagnosticsPauseCleared();
        _pauseId = 0;
        _pausePending = _pauseFading = _pauseArmed = _pauseAckSent = false;
        _pauseArmedSample = -1;
        _pauseConfirmedAt = -1;
        _pauseDrain = 0;
        _pauseDeadline = _pauseForceAt = 0;
        _pauseReason = "";
        _playback?.CancelScheduledPause();
    }

    /// <summary>예약된 정지를 진행한다. 실제 정지는 오디오 콜백이 표본 단위로 만들고,
    /// 여기서는 어디에 걸지와 상한만 정한다.</summary>
    void AdvancePauseSchedule()
    {
        if (!_pausePending) return;
        float now = Now;
        if (!_pauseArmed && !_pauseFading && _boundaries.Count > 0)
        {
            long target = _boundaries.Peek().samples;
            // 이미 지나간 경계에는 걸지 않는다. 늦은 정지를 경계라고 부르지 않기 위해서다.
            if (_playback.PauseAtSample(target))
            {
                _pauseArmed = true;
                _pauseArmedSample = target;
                DiagnosticsPauseArmed(target);
            }
        }
        if (!_pauseFading && now >= _pauseDeadline)
        {
            // 상한 안에 경계가 없었다. 클릭음만 없애는 짧은 감쇠로 멈춘다.
            _pauseFading = true;
            _pauseArmed = false;
            _pauseArmedSample = -1;
            _pauseReason = "grace_expired";
            _pauseForceAt = now + PauseFadeMillis / 1000f + PauseStallFallback;
            _playback.PauseWithFade(PauseFadeMillis);
        }
        if (_pauseFading && now >= _pauseForceAt && !_playback.PauseState.applied)
        {
            // 출력이 전혀 진행되지 않아 감쇠가 끝나지 못했다. 벽시계로 멈춘다.
            // 남은 PCM 과 위상은 그대로 두므로 재개하면 이어지고, 경계라고 부르지 않는다.
            HardPause();
            DiagnosticsPauseForced(_pauseId);
            _pauseReason = "grace_expired";
            _pauseConfirmedAt = Now;
            _pauseDrain = _playback.TailSeconds;
        }
    }

    /// <summary>실제로 멈췄는지 확인하고, 출력에 이미 넘긴 소리가 다 빠진 뒤에 보고한다.
    /// 잔향이 남은 채로 보고하면 서버가 그 자리에서 답변을 바꿔 구절 꼬리가 잘린다.
    /// 멈춘 뒤에도 이 함수는 계속 돌아야 한다.</summary>
    void PumpPauseAck()
    {
        if (_pauseId == 0 || _pauseAckSent || _playback == null) return;
        if (_pauseConfirmedAt < 0)
        {
            var pause = _playback.PauseState;
            if (!pause.applied) return;   // 아직 한 표본도 멈추지 않았다
            _pauseConfirmedAt = Now;
            _pauseDrain = _playback.TailSeconds;
            _pausePending = _pauseFading = false;
            if (pause.cause == DialogueAudioRenderer.PauseBoundary) TakeArmedBoundary();
            else if (string.IsNullOrEmpty(_pauseReason)) _pauseReason = "grace_expired";
        }
        if (Now < _pauseConfirmedAt + _pauseDrain) return;
        SendPauseAck(_pauseReason);
        _pauseAckSent = true;
    }

    /// <summary>예약한 경계에서 멈췄다. 그 구절은 끝까지 들려줬으므로 전달된 글자 수에 넣는다.
    /// 감쇠·강제 정지에서는 부르지 않는다. 잘린 구절을 전달됐다고 세지 않는다.</summary>
    void TakeArmedBoundary()
    {
        _pauseReason = "boundary";
        _pauseArmed = false;
        if (_boundaries.Count == 0 || _boundaries.Peek().samples != _pauseArmedSample) return;
        var boundary = _boundaries.Dequeue();
        _playedChars = boundary.text_chars;
        _boundaryDue = -1;
        _pauseArmedSample = -1;
        DiagnosticsPlaybackBoundary(boundary);
    }

    void UpdatePlayback()
    {
        // 첫 출력 프레임은 보류·응답 종료와 상관없이 잰다. 이미 남겼으면 바로 돌아온다.
        DiagnosticsUpdate();
        if (string.IsNullOrEmpty(_responseId) || _playback == null) return;
        // 멈춘 뒤에도 실제 정지 확인과 잔향 대기는 계속 돌아야 보고가 늦지 않는다.
        PumpPauseAck();
        if (ResponsePaused) return;
        // 경계에 정지를 걸어 둔 동안에는 그 경계를 진행 보고로 소비하지 않는다.
        // 실제로 멈춘 뒤 보고 경로가 같은 경계를 쓴다.
        if (_boundaries.Count > 0 && _playback.Consumed >= _boundaries.Peek().samples &&
            !(_pauseArmed && _boundaries.Peek().samples == _pauseArmedSample))
        {
            if (_boundaryDue < 0) _boundaryDue = Now + _playback.TailSeconds;
            if (Now >= _boundaryDue)
            {
                var boundary = _boundaries.Dequeue();
                _playedChars = boundary.text_chars;
                _boundaryDue = -1;
                DiagnosticsPlaybackBoundary(boundary);
                SendPlayback("playback.progress");
            }
        }
        AdvancePauseSchedule();
        if (ResponsePaused) return;
        if (_audioComplete && _boundaries.Count == 0 && _playedChars > 0)
        {
#if UNITY_EDITOR
            // 진단용. 끝까지 재생된 이 시점의 것만 완료본이 된다.
            DialogueReplayCache.Complete(_responseTurn);
#endif
            // SendPlayback 보다 먼저 남긴다. 전송이 실패하면 FailDialogue → EndExperience 로 빠져
            // 로거가 그 자리에서 닫히므로, 뒤에 두면 정상 완료가 통째로 사라진다.
            DiagnosticsPlaybackCompleted();
            SendPlayback("playback.done");
            _playback.Stop();
            _responseId = "";
            PlaybackFinished = true;
            OnAnswer?.Invoke(showHeardSubtitle ? _responseHeard : "", lastAnswer);
        }
    }

    void SendPlayback(string type) => SendControl(JsonUtility.ToJson(
        new PlaybackMessage { type = type, response_id = _responseId, text_chars = _playedChars }));

    /// <summary>실제로 멈춘 지점을 서버에 알린다. 서버는 이 보고를 받은 뒤에 답변을 바꾼다.
    /// 전송이 막혀 있어도 무엇을 보고하기로 했는지는 남긴다.</summary>
    void SendPauseAck(string reason)
    {
        PauseAckReason = string.IsNullOrEmpty(reason) ? "grace_expired" : reason;
        PauseAckId = _pauseId;
        PauseAckCount++;
        DiagnosticsPauseAck(_pauseId, PauseAckReason);
        SendControl(JsonUtility.ToJson(new PlaybackMessage
        {
            type = "playback.paused", response_id = _responseId, text_chars = _playedChars,
            pause_id = _pauseId, reason = PauseAckReason,
        }));
    }

    public void CancelDialogueResponse()
    {
        SendControl("{\"type\":\"cancel\"}");
        PublishInterrupted();
        _responseId = "";
        isWaiting = false;
        NoteStopReason("user_cancel");
        StopStreamPlayback();
    }
    public void ResetDialogueHistory()
    {
        SendControl("{\"type\":\"reset\"}");
        _responseId = "";
        NoteStopReason("reset");
        StopStreamPlayback();
#if UNITY_EDITOR
        // 진단용. 기록 초기화는 완료본까지 버린다.
        DialogueReplayCache.Clear();
#endif
    }

#if UNITY_EDITOR
    // Automated checks inject a public WAV through the exact microphone PCM path.
    // No typed question input is exposed by the test scene or player build.
    public bool SendTestAudio(byte[] pcm) => useTestProfile && _dialogueReady && _dialogue.SendAudio(pcm);

    // 등록 인물(useTestProfile=false) 그대로 검사할 때 쓰는 파일 입력 상태.
    // 켜져 있는 동안 마이크는 열리지 않으므로 실제 마이크 PCM이 섞이지 않는다.
    bool _editorAudioInjection;
    public bool EditorAudioInjection => _editorAudioInjection;

    /// <summary>체험을 시작하기 전에만 켠다. 프로토콜과 test_mode 계약은 바뀌지 않는다.</summary>
    public void BeginEditorAudioInjection()
    {
        if (_experienceActive) throw new InvalidOperationException("체험이 시작되기 전에 켜야 합니다.");
        _editorAudioInjection = true;
        StopListening();
    }

    public void EndEditorAudioInjection() => _editorAudioInjection = false;

    /// <summary>마이크 전송과 같은 PCM 경로로 파일 조각을 보낸다.</summary>
    public bool SendInjectedAudio(byte[] pcm) =>
        _editorAudioInjection && _dialogueReady && _dialogue != null && _dialogue.SendAudio(pcm);
    [Serializable] class TestTextMessage { public string type = "text", text; }
    public bool SendTestText(string text) => useTestProfile && _dialogueReady &&
        _dialogue.SendText(JsonUtility.ToJson(new TestTextMessage { text = text }));
#endif

    /// <summary>Can also be connected to existing UnityEvent interaction callbacks.</summary>
    public void ReportUnityAction(string description) => SendUnityContext("action", description);
    public void ReportUnityState(string description) => SendUnityContext("state", description);

    void SendUnityContext(string kind, string text)
    {
        if (!_dialogueReady || _dialogue == null || string.IsNullOrWhiteSpace(text)) return;
        if (text.Length > 1500) text = text.Substring(0, 1500);
        if (!_dialogue.SendText(JsonUtility.ToJson(new ContextMessage { kind = kind, text = text })))
            FailDialogue("상황 정보를 전송하지 못했습니다. 연결을 확인해주세요.");
    }

    void ReportViewContext()
    {
        if (_contextCamera == null) _contextCamera = Camera.main;
        string text = "현재 장면: " + SceneManager.GetActiveScene().name + ".";
        if (_contextCamera != null && Physics.Raycast(_contextCamera.transform.position,
            _contextCamera.transform.forward, out var hit, 10f, Physics.DefaultRaycastLayers,
            QueryTriggerInteraction.Ignore))
        {
            var label = hit.collider.GetComponentInParent<DialogueContextObject>();
            // Only author-labelled objects have a reliable meaning for the LLM.
            if (label != null)
                text += $" 사용자 머리 정면에 있는 대상: {label.DisplayName}. 거리: {hit.distance:F1}m.";
        }
        ReportUnityState(text);
    }
}
