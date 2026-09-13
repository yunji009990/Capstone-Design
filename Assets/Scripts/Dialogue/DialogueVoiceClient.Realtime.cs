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
    public string VoiceEmotion { get; private set; } = "unknown";
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

    public string DialogueServerUrl
    {
        get
        {
            if (!string.IsNullOrWhiteSpace(dialogueServerUrl)) return dialogueServerUrl.TrimEnd('/');
            var uri = new UriBuilder(serverUrl) { Port = 8002, Path = "", Query = "" };
            return uri.Uri.ToString().TrimEnd('/');
        }
    }

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
    readonly Queue<DialogueEvent> _boundaries = new Queue<DialogueEvent>();
    float _boundaryDue = -1;
    float _pausedBoundaryRemaining = -1;
    int _playedChars;
    bool _audioComplete;

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
        public string pcm, format;
        public int sample_rate, channels, samples, text_chars;
        public bool tts;
        public string voice_mode;
        public DialogueReferenceInfo reference;
        public AudioObservation audio;
        public ResponseTiming timing;
    }

    [Serializable]
    class AudioObservation
    {
        public string emotion, audio_event, language;
        public Prosody prosody;
    }
    [Serializable] class Prosody { public float duration_sec, rms_dbfs; }

    [Serializable]
    class ResponseTiming { public float first_text_sec, first_audio_sec, total_sec; }

    [Serializable] class PlaybackMessage
    {
        public string type, response_id;
        public int text_chars;
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
            _responseHeard = "";
            InterruptionAction = InterruptionReason = "";
            DecisionSeconds = -1;
            lastHeard = lastAnswer = "";
            ClearResponseMetadata();
            StopStreamPlayback();
            _dialogueReady = false;
            VoiceMode = "";
            ActiveReference = null;
            ConnectionStatus = "대화 서버에 연결 중입니다";
            _experienceActive = true;
            autoDetect = true;
            _dialogue = new DialogueTransport(uri.Uri, token,
                JsonUtility.ToJson(new DialogueStart { session = sessionId,
                    test_mode = useTestProfile, test_persona = useTestProfile ? testPersona : null,
                    reference_id = useTestProfile ? testReferenceId : null,
                    reference_text = useTestProfile && !string.IsNullOrEmpty(testReferenceId) ? testReferenceText : null }));
            _nextPing = Time.realtimeSinceStartup + 5f;
            OnAnswerUpdated?.Invoke("", "");
            return true;
        }
        catch (Exception e)
        {
            EndExperience();
            OnError?.Invoke(e.Message);
            return false;
        }
    }

    public void EndExperience()
    {
        _experienceActive = false;
        _dialogueReady = false;
        _dialogue?.Dispose();
        _dialogue = null;
        if (!string.IsNullOrEmpty(_responseId) || isWaiting)
            PublishInterrupted();
        _responseId = "";
        isWaiting = false;
        autoDetect = false;
        StopStreamPlayback();
        StopListening();
    }

    void UpdateRealtime()
    {
        var transport = _dialogue;
        if (!_experienceActive || transport == null) return;
        for (int i = 0; i < 128 && transport.TryReceive(out var packet); i++)
        {
            if (!string.IsNullOrEmpty(packet.Error))
            {
                FailDialogue(packet.Error);
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
        if (!useTestProfile || captureRealtimeMicrophone) PumpMicrophone();
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

    void PumpMicrophone()
    {
        if (!_listening || _recClip == null || !Microphone.IsRecording(_micDevice))
        {
            FailDialogue("마이크 연결이 끊겼습니다. 마이크를 확인하고 다시 시작해주세요.");
            return;
        }
        if (Time.realtimeSinceStartup - _lastMicPump > 2f)
        {
            FailDialogue("음성 전송이 지연되어 체험을 중단했습니다. 다시 시작해주세요.");
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
                FailDialogue("음성 전송이 밀렸습니다. 연결을 확인하고 다시 시작해주세요.");
                return;
            }
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
                if (!useTestProfile || captureRealtimeMicrophone)
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
                if (!suspended)
                {
                    _responseId = _responseHeard = "";
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
                DecisionPending = ResponsePaused;
                break;
            case "transcript.partial":
            case "transcript.final":
                if (message.turn_id != _dialogueTurn) break;
                lastHeard = message.text ?? "";
                if (message.audio != null)
                {
                    VoiceEmotion = message.audio.emotion ?? "unknown";
                    VoiceAudioEvent = message.audio.audio_event ?? "unknown";
                    VoiceLanguage = message.audio.language ?? "unknown";
                    if (message.audio.prosody != null)
                    {
                        VoiceDuration = message.audio.prosody.duration_sec;
                        VoiceRmsDb = message.audio.prosody.rms_dbfs;
                    }
                }
                TranscriptIsFinal = message.type == "transcript.final";
                OnTranscriptUpdated?.Invoke(showHeardSubtitle ? lastHeard : "");
                break;
            case "response.started":
                if (message.turn_id != _dialogueTurn) break;
                StopStreamPlayback();
                _responseId = message.response_id;
                _responseTurn = message.turn_id;
                _responseHeard = message.heard ?? lastHeard;
                ResponseRoute = message.route;
                lastAnswer = "";
                FirstTextSeconds = FirstAudioSeconds = TotalResponseSeconds = -1;
                isWaiting = true;
                OnAnswerUpdated?.Invoke(showHeardSubtitle ? _responseHeard : "", "");
                break;
            case "response.paused":
                if (!MatchesResponse(message)) break;
                if (!ResponsePaused)
                {
                    _pausedBoundaryRemaining = _boundaryDue < 0 ? -1 : Mathf.Max(0, _boundaryDue - Time.realtimeSinceStartup);
                    _boundaryDue = -1;
                    _playback.Pause();
                }
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
                _responseHeard = message.heard ?? _responseHeard;
                lastAnswer = message.text ?? lastAnswer;
                ResponseRoute = message.route;
                if (_pausedBoundaryRemaining >= 0)
                    _boundaryDue = Time.realtimeSinceStartup + _pausedBoundaryRemaining;
                _pausedBoundaryRemaining = -1;
                _playback.Resume();
                DecisionPending = false;
                isWaiting = !_playback.Playing && !_audioComplete;
                OnAnswerUpdated?.Invoke(showHeardSubtitle ? _responseHeard : "", lastAnswer);
                break;
            case "response.audio":
                if (!MatchesResponse(message)) break;
                if (message.sample_rate != 24000 || message.channels != 1 || message.format != "pcm_s16le")
                    throw new InvalidOperationException("지원하지 않는 답변 음성 형식입니다.");
                _playback.Enqueue(Convert.FromBase64String(message.pcm));
                isWaiting = false;
                break;
            case "audio.boundary":
                if (!MatchesResponse(message)) break;
                _boundaries.Enqueue(message);
                break;
            case "response.delta":
                if (!MatchesResponse(message)) break;
                lastAnswer += message.text;
                OnAnswerUpdated?.Invoke(showHeardSubtitle ? _responseHeard : "", lastAnswer);
                break;
            case "response.done":
                if (!MatchesResponse(message)) break;
                lastAnswer = message.text ?? lastAnswer;
                if (message.timing != null)
                {
                    FirstTextSeconds = message.timing.first_text_sec;
                    FirstAudioSeconds = TtsEnabled ? message.timing.first_audio_sec : -1;
                    TotalResponseSeconds = message.timing.total_sec;
                }
                isWaiting = false;
                _audioComplete = true;
                if (!TtsEnabled)
                {
                    _responseId = "";
                    PlaybackFinished = true;
                    OnAnswer?.Invoke(showHeardSubtitle ? _responseHeard : "", lastAnswer);
                }
                break;
            case "response.cancelled":
                if (!MatchesResponse(message)) break;
                PublishInterrupted();
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
                StopStreamPlayback();
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

    void FailDialogue(string message)
    {
        EndExperience();
        OnError?.Invoke(message);
    }

    void ClearResponseMetadata(bool preserveAnswer = false)
    {
        VoiceEmotion = VoiceAudioEvent = VoiceLanguage = "unknown";
        TranscriptIsFinal = false;
        VoiceDuration = VoiceRmsDb = 0;
        if (preserveAnswer) return;
        ResponseRoute = "";
        FirstTextSeconds = FirstAudioSeconds = TotalResponseSeconds = -1;
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
        _playback?.Stop();
        _boundaries.Clear();
        _boundaryDue = -1;
        _pausedBoundaryRemaining = -1;
        _playedChars = 0;
        _audioComplete = PlaybackFinished = false;
        DecisionPending = false;
    }

    void UpdatePlayback()
    {
        if (string.IsNullOrEmpty(_responseId) || _playback == null || ResponsePaused) return;
        if (_boundaries.Count > 0 && _playback.Consumed >= _boundaries.Peek().samples)
        {
            if (_boundaryDue < 0) _boundaryDue = Time.realtimeSinceStartup + _playback.TailSeconds;
            if (Time.realtimeSinceStartup >= _boundaryDue)
            {
                _playedChars = _boundaries.Dequeue().text_chars;
                _boundaryDue = -1;
                SendPlayback("playback.progress");
            }
        }
        if (_audioComplete && _boundaries.Count == 0 && _playedChars > 0)
        {
            SendPlayback("playback.done");
            _playback.Stop();
            _responseId = "";
            PlaybackFinished = true;
            OnAnswer?.Invoke(showHeardSubtitle ? _responseHeard : "", lastAnswer);
        }
    }

    void SendPlayback(string type) => SendControl(JsonUtility.ToJson(
        new PlaybackMessage { type = type, response_id = _responseId, text_chars = _playedChars }));

    public void CancelDialogueResponse()
    {
        SendControl("{\"type\":\"cancel\"}");
        PublishInterrupted();
        _responseId = "";
        isWaiting = false;
        StopStreamPlayback();
    }
    public void ResetDialogueHistory()
    {
        SendControl("{\"type\":\"reset\"}");
        _responseId = "";
        StopStreamPlayback();
    }

#if UNITY_EDITOR
    // Automated checks inject a public WAV through the exact microphone PCM path.
    // No typed question input is exposed by the test scene or player build.
    public bool SendTestAudio(byte[] pcm) => useTestProfile && _dialogueReady && _dialogue.SendAudio(pcm);
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
