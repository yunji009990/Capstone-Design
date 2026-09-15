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
    /// <summary>지금 재생 중인 답변 음성의 크기(선형 RMS, 0~1). 인물의 입 벙긋이 읽는다.</summary>
    public float SpeechLevel => _playback != null ? _playback.Level : 0f;
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
    volatile float _outputPeak;
    public float OutputPeak => _outputPeak;
    void OnAudioFilterRead(float[] data, int channels)
    {
        float peak = OutputPeak;
        foreach (float sample in data) peak = Math.Max(peak, Math.Abs(sample));
        _outputPeak = peak;
    }
#endif

    void Awake()
    {
        _playback = new DialogueAudioPlayer(GetComponent<AudioSource>());
        if (Microphone.devices.Length > 0) _micDevice = Microphone.devices[0];
    }

    void Start()
    {
        if (!followServerSession || useTestProfile)
            HasSession = useTestProfile || !string.IsNullOrWhiteSpace(sessionId);
        if (checkHealthOnStart) StartCoroutine(CheckHealth());
        if (followServerSession && !useTestProfile) StartCoroutine(PollSession());
    }

    void OnDisable() => EndExperience();
    void OnDestroy() => _playback?.Dispose();
    void OnApplicationPause(bool paused) { if (paused) EndExperience(); }
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
                            if (changed) EndExperience();
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
