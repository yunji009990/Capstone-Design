using System;
using System.Collections;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>Continuous microphone, CPU session API, and independent dialogue service.</summary>
[RequireComponent(typeof(AudioSource))]
public partial class DialogueVoiceClient : MonoBehaviour
{
    [Header("등록 서버")]
    public string serverUrl = "http://220.69.208.201:8000";
    public string token = "";
    public string sessionId = "player1";
    public bool followServerSession = true;
    public float sessionPollSec = 5f;
    public bool checkHealthOnStart = true;
    public bool showHeardSubtitle = true;
    [Header("마이크 입력 표시")]
    public float minLevel = .02f;
    public int sampleRate = 16000;
    [HideInInspector] public bool autoDetect;
    [HideInInspector] public bool isRecording, isWaiting, serverReady;
    [HideInInspector] public string lastHeard = "", lastAnswer = "";

    public event Action<string, string> OnAnswer;
    public event Action<string> OnError;
    public event Action<bool, string> OnHealth;
    public event Action<string, bool> OnSessionChanged;
    public bool SessionHasModel { get; private set; }
    public bool HasSession { get; private set; }
    public bool IsSpeaking => _playback != null && _playback.Playing;
    public bool TtsEnabled { get; private set; }
    public string[] MicDevices => Microphone.devices;
    public string CurrentMic => _micDevice ?? "";
    public bool IsListening => _listening;
    public float MicLevel { get; private set; }
    public float VadThreshold => minLevel; // meter guide; actual VAD runs on server
    public bool SawSignal { get; private set; }
    const int RingSeconds = 30;
    AudioClip _recClip;
    string _micDevice;
    bool _listening;
    readonly float[] _analysis = new float[1024];
    DialogueAudioPlayer _playback;

#if UNITY_EDITOR
    /// <summary>실제로 출력 버퍼에 쓴 PCM 의 최대 진폭이다. 누적값이므로 검사는 회차마다 0으로 되돌린다.
    /// 예전에는 AudioSource 뒤의 필터에서 쟀지만, 이제 재생 렌더러가 쓰는 값을 그대로 읽는다.
    /// 무음 여부 확인이라는 의미는 같고 믹서 이후의 음량은 반영하지 않는다.</summary>
    public float OutputPeak => _playback != null ? _playback.OutputPeak : 0f;
    public void ResetOutputPeak() { if (_playback != null) _playback.ResetPeak(); }
#endif

    void Awake()
    {
#if UNITY_EDITOR
        // 재생 비교 모드는 에디터 선택값으로만 정하고 Play 중에는 바뀌지 않는다.
        bool wholeResponse = DialoguePlaybackComparison.WholeResponseEnabled;
        _playback = new DialogueAudioPlayer(GetComponent<AudioSource>(), wholeResponse);
        Debug.Log(wholeResponse
            ? "[Dialogue] 재생 모드: 응답 전체 수신 후 AudioClip 재생 (비교 모드)"
            : "[Dialogue] 재생 모드: 수신 즉시 스트리밍");
#else
        _playback = new DialogueAudioPlayer(GetComponent<AudioSource>());
#endif
        if (Microphone.devices.Length > 0) _micDevice = Microphone.devices[0];
    }

    void Start()
    {
        if (!followServerSession || useTestProfile)
            HasSession = useTestProfile || !string.IsNullOrWhiteSpace(sessionId);
        if (checkHealthOnStart) StartCoroutine(CheckHealth());
        if (followServerSession && !useTestProfile) StartCoroutine(PollSession());
    }

    void OnDisable()
    {
        EndExperience("on_disable");
#if UNITY_EDITOR
        // 진단용. 비활성·Play 종료에서는 완료본까지 버린다.
        DialogueReplayCache.Clear();
#endif
    }

    void OnDestroy()
    {
        _playback?.Dispose();
#if UNITY_EDITOR
        DialogueReplayCache.Clear();
#endif
    }
    void OnApplicationPause(bool paused)
    {
        // 기록이 먼저다. EndExperience 가 로거를 닫으면 남길 곳이 없다.
        DiagnosticsPause(paused);
        if (paused) EndExperience("app_pause");
    }

    // 소리가 멈춘 구간이 창 포커스와 겹치는지 보려면 상태 변화가 필요하다. 대화 동작은 바뀌지 않는다.
    void OnApplicationFocus(bool focused) => DiagnosticsFocus(focused);
    void Update()
    {
        UpdateLevel();
        UpdateRealtime();
        UpdatePlayback();
    }

    void StartListening()
    {
        if (string.IsNullOrEmpty(_micDevice) || _listening) return;
        _recClip = Microphone.Start(_micDevice, true, RingSeconds, sampleRate);
        _listening = _recClip != null;
        SawSignal = false;
    }

    void StopListening()
    {
        if (_listening && !string.IsNullOrEmpty(_micDevice)) Microphone.End(_micDevice);
        _listening = isRecording = false;
        MicLevel = 0;
        if (_recClip != null) Destroy(_recClip);
        _recClip = null;
    }

    public void SelectMic(string device)
    {
        if (ExperienceActive) return;
        _micDevice = device;
    }

    void UpdateLevel()
    {
        if (!_listening || _recClip == null) { MicLevel = 0; return; }
        int pos = Microphone.GetPosition(_micDevice);
        if (pos < 0) return;
        if (!_recClip.GetData(_analysis, Mathf.Max(0, pos - _analysis.Length))) return;
        float sum = 0;
        foreach (float value in _analysis) sum += value * value;
        MicLevel = Mathf.Sqrt(sum / _analysis.Length);
        if (MicLevel > 0) SawSignal = true;
    }

    // Existing UnityEvent bindings continue to use the experience lifecycle.
    public void StartRecording() => BeginExperience();
    public void StopAndSend() { } // server VAD decides the end of each utterance
    public void ResetSession() => ResetDialogueHistory();

    [Serializable] class SessionResponse { public string session; public bool has_model; }
    IEnumerator PollSession()
    {
        var wait = new WaitForSeconds(Mathf.Max(1f, sessionPollSec));
        while (true)
        {
            using (var req = UnityWebRequest.Get($"{serverUrl}/session/current"))
            {
                if (!string.IsNullOrEmpty(token)) req.SetRequestHeader("X-Token", token);
                req.timeout = 10;
                yield return req.SendWebRequest();

                if (req.result == UnityWebRequest.Result.Success)
                {
                    SessionResponse s = null;
                    try { s = JsonUtility.FromJson<SessionResponse>(req.downloadHandler.text); }
                    catch (Exception e) { Debug.LogWarning($"[Dialogue] 세션 응답 파싱 실패: {e.Message}"); }

                    if (s != null)
                    {
                        // 서버에 기본 인물이 없다. 등록된 세션이 없으면 대화 자체를 막는다.
                        string cur = s.session ?? "";
                        bool has = !string.IsNullOrEmpty(cur);
                        if (cur != sessionId || has != HasSession || s.has_model != SessionHasModel)
                        {
                            bool changed = cur != sessionId;
                            if (changed) EndExperience("session_changed");
                            sessionId = cur;
                            HasSession = has;
                            SessionHasModel = s.has_model;
                            if (changed)
                                Debug.Log(has
                                    ? $"[Dialogue] 세션 전환: {sessionId} (모델 {(s.has_model ? "있음" : "없음")})"
                                    : "[Dialogue] 등록된 인물이 없습니다 — 웹에서 등록해야 대화할 수 있습니다.");
                            OnSessionChanged?.Invoke(sessionId, s.has_model);
                        }
                    }
                }
                else if ((req.responseCode == 401 || req.responseCode == 403))
                {
                    Debug.LogError("[Dialogue] 토큰이 틀렸습니다. 인스펙터의 Token 값을 확인하세요.");
                    yield break;   // 토큰이 틀리면 계속 두드려봐야 소용없다
                }
            }
            if (!serverReady && checkHealthOnStart) yield return CheckHealth();
            yield return wait;
        }
    }


    [Serializable] class HealthResponse
    {
        public string status, mode;
        public bool test_mode_available, tts, tts_ready;
    }

    public IEnumerator CheckHealth()
    {
        using (var req = UnityWebRequest.Get(DialogueServerUrl + "/health"))
        {
            req.timeout = 10;
            yield return req.SendWebRequest();
            HealthResponse health = null;
            if (req.result == UnityWebRequest.Result.Success)
            {
                try { health = JsonUtility.FromJson<HealthResponse>(req.downloadHandler.text); }
                catch (Exception) { }
            }
            TtsEnabled = health != null && health.tts;
            serverReady = health != null && health.status == "ready" &&
                (health.mode == "streaming_voice" || health.mode == "streaming_text") &&
                (!health.tts || health.tts_ready) && (!useTestProfile || health.test_mode_available);
            OnHealth?.Invoke(serverReady, serverReady ? (TtsEnabled ? "음성 대화 서버 준비 완료 · Qwen3-TTS" :
                "대화 서버 준비 완료 · 텍스트 답변 · TTS 꺼짐") : "대화 서버 연결을 확인해 주세요.");
        }
    }
}
