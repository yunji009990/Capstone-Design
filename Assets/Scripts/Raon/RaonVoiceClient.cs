using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// A.X K2 Raon-Speech 음성 서버 클라이언트.
/// 마이크를 계속 열어두고(링 버퍼) 목소리가 감지되면 자동으로 녹음·전송한 뒤 답변 음성을 재생합니다.
///
/// 사용법:
///   1. 빈 GameObject를 만들고 이 스크립트를 붙입니다.
///   2. AudioSource 컴포넌트를 같은 오브젝트에 추가합니다(자동 생성됨).
///   3. serverUrl을 서버 주소로 맞춥니다.
/// </summary>
[RequireComponent(typeof(AudioSource))]
public class RaonVoiceClient : MonoBehaviour
{
    [Header("서버 설정")]
    public string serverUrl = "http://220.69.208.201:8000";
    [Tooltip("서버에 RAON_TOKEN을 설정했다면 같은 값을 입력")]
    public string token = "";
    [Tooltip("대화 맥락을 구분하는 키. 서버에서 세션을 받아오면 이 값이 덮어써집니다")]
    public string sessionId = "player1";
    [Tooltip("서버의 현재 세션을 주기적으로 조회해 sessionId에 반영합니다. "
           + "웹에서 인물을 등록하면 자동으로 그 인물로 바뀝니다")]
    public bool followServerSession = true;
    [Tooltip("세션 조회 간격(초)")]
    public float sessionPollSec = 5f;
    [Tooltip("시작할 때 /health로 서버 가동 여부를 확인. 자동 감지는 서버가 준비된 뒤에만 동작합니다")]
    public bool checkHealthOnStart = true;
    [Tooltip("내가 한 말(X-Heard) 자막을 받아옵니다. 서버가 음성 인식을 먼저 돌려야 해서 응답이 약 0.55초 느려집니다. "
           + "끄면 서버가 응답 후 뒤늦게 인식하므로 빨라지지만 '나: …' 자막이 비어 있게 됩니다")]
    public bool showHeardSubtitle = true;

    [Tooltip("/talk_stream을 써서 답변 음성을 도착하는 대로 재생합니다. 답변이 두 문장 이상일 때 첫 소리가 빨라집니다")]
    public bool useStreaming = true;
    [Tooltip("재생을 시작하기 전에 모아둘 분량(초). 크면 첫 소리가 늦지만 중간에 끊길 위험이 줄어듭니다")]
    public float streamPrebufferSec = 1.0f;
    [Tooltip("버퍼가 빈 뒤 재생 종료까지 기다리는 시간(초). 짧으면 답변 끝부분이 잘립니다")]
    public float streamTailSec = 0.6f;

    [Header("자동 감지 (VAD)")]
    [Tooltip("목소리가 감지되면 자동으로 녹음·전송합니다. 끄면 스페이스바/버튼 수동 조작만 동작합니다")]
    public bool autoDetect = true;
    [Tooltip("주변 소음의 몇 배 이상이면 목소리로 볼지. 오작동이 잦으면 올리세요")]
    public float noiseMultiplier = 2.5f;
    [Tooltip("절대 하한. 아무리 조용한 환경이라도 이보다 작은 소리는 무시합니다")]
    public float minLevel = 0.02f;
    [Tooltip("이만큼 조용하면 말이 끝난 것으로 보고 전송합니다(초). 그대로 체감 지연에 더해지므로 짧을수록 좋지만, "
           + "0.5 아래로 내리면 말 중간에 뜸 들일 때 끊깁니다")]
    public float silenceSec = 0.5f;
    [Tooltip("감지 시점보다 이만큼 앞의 소리도 함께 보냅니다. 첫 음절이 잘리는 것을 막습니다(초)")]
    public float prerollSec = 0.25f;

    [Header("녹음 설정")]
    public int sampleRate = 16000;
    public int maxRecordSeconds = 15;
    public KeyCode pushToTalkKey = KeyCode.Space;

    [Header("상태 (읽기 전용)")]
    public string lastHeard = "";
    public string lastAnswer = "";
    public bool isRecording = false;
    public bool isWaiting = false;
    public bool serverReady = false;

    /// <summary>인식된 사용자 발화, 캐릭터 답변 순서로 전달됩니다. 자막 UI에 연결하세요.</summary>
    public event Action<string, string> OnAnswer;
    public event Action<string> OnError;
    /// <summary>서버 상태 확인 결과. (준비됨, 안내 메시지)</summary>
    public event Action<bool, string> OnHealth;
    /// <summary>서버의 현재 세션이 바뀌었을 때. (세션ID, 모델 준비됨)</summary>
    public event Action<string, bool> OnSessionChanged;

    /// <summary>서버가 알려준 현재 세션에 3D 모델이 준비되어 있는지.</summary>
    public bool SessionHasModel { get; private set; }

    /// <summary>서버에 등록된 인물이 있는지. 없으면 대화를 시작할 수 없습니다.</summary>
    public bool HasSession { get; private set; }

    /// <summary>답변 음성을 재생 중인지. 상태 표시나 립싱크에 사용하세요.</summary>
    public bool IsSpeaking => _audio != null && _audio.isPlaying;

    /// <summary>사용 가능한 마이크 목록.</summary>
    public string[] MicDevices => Microphone.devices;

    /// <summary>현재 선택된 마이크. 없으면 빈 문자열.</summary>
    public string CurrentMic => _micDevice ?? "";

    /// <summary>마이크를 열어 대기 중인지.</summary>
    public bool IsListening => _listening;

    /// <summary>현재 마이크 입력 크기(RMS, 0~1). 레벨 미터에 사용하세요.</summary>
    public float MicLevel { get; private set; }

    /// <summary>목소리로 판정하는 현재 기준값. 레벨 미터의 눈금 표시에 사용하세요.</summary>
    public float VadThreshold => Mathf.Max(minLevel, _noiseFloor * noiseMultiplier);

    // 링 버퍼 길이. maxRecordSeconds + preroll보다 넉넉해야 한다.
    const int RingSeconds = 30;
    const int AnalysisWindow = 1024;
    const float StartHoldSec = 0.15f;      // 이만큼 연속으로 커야 발화 시작으로 인정
    const float MinUtteranceSec = 0.4f;    // 기침·문 닫는 소리 등을 걸러낸다
    const float ResumeCooldownSec = 0.35f; // 답변 재생 직후 잔향을 다시 잡지 않도록

    AudioSource _audio;
    AudioClip _recClip;
    string _micDevice;
    bool _listening;

    readonly float[] _analysis = new float[AnalysisWindow];
    float _noiseFloor = 0.01f;
    float _aboveTime;
    float _belowTime;
    float _cooldownUntil;
    int _captureStart = -1;
    float _recordStartTime;

    void Awake()
    {
        _audio = GetComponent<AudioSource>();
        if (Microphone.devices.Length == 0)
        {
            Debug.LogError("[Raon] 마이크를 찾을 수 없습니다.");
            return;
        }
        _micDevice = Microphone.devices[0];
        Debug.Log($"[Raon] 마이크: {_micDevice}");
    }

    void Start()
    {
        StartListening();
        if (checkHealthOnStart) StartCoroutine(CheckHealth());
        if (followServerSession) StartCoroutine(PollSession());
    }

    void OnDisable() => StopListening();

    void Update()
    {
        // 수동 조작 (자동 감지를 꺼두었거나 강제로 보내고 싶을 때)
        if (Input.GetKeyDown(pushToTalkKey) && !isRecording && !isWaiting && HasSession) StartRecording();
        else if (Input.GetKeyUp(pushToTalkKey) && isRecording) StopAndSend();

        UpdateLevel();
        if (autoDetect) UpdateVad();

        if (isRecording && Time.time - _recordStartTime >= maxRecordSeconds)
        {
            Debug.Log("[Raon] 최대 길이 도달, 전송합니다.");
            StopAndSend();
        }
    }

    // ─────────── 마이크 상시 청취 ───────────
    void StartListening()
    {
        if (string.IsNullOrEmpty(_micDevice) || _listening) return;

        _recClip = Microphone.Start(_micDevice, true, RingSeconds, sampleRate);
        if (_recClip == null)
        {
            OnError?.Invoke($"마이크를 열 수 없습니다: {_micDevice}");
            return;
        }
        _listening = true;
        _noiseFloor = minLevel;
        Debug.Log($"[Raon] 청취 시작: {_micDevice}");
    }

    void StopListening()
    {
        if (!string.IsNullOrEmpty(_micDevice)) Microphone.End(_micDevice);
        _listening = false;
        isRecording = false;
        MicLevel = 0f;
        _recClip = null;
    }

    /// <summary>마이크를 바꿉니다. 녹음 중에는 무시됩니다.</summary>
    public void SelectMic(string device)
    {
        if (isRecording)
        {
            Debug.LogWarning("[Raon] 녹음 중에는 마이크를 바꿀 수 없습니다.");
            return;
        }
        StopListening();
        _micDevice = device;
        StartListening();
        Debug.Log($"[Raon] 마이크 변경: {_micDevice}");
    }

    void UpdateLevel()
    {
        if (!_listening || _recClip == null) { MicLevel = 0f; return; }

        int pos = Microphone.GetPosition(_micDevice);
        int start = Mathf.Max(0, pos - AnalysisWindow);
        _recClip.GetData(_analysis, start);

        float sum = 0f;
        for (int i = 0; i < _analysis.Length; i++) sum += _analysis[i] * _analysis[i];
        MicLevel = Mathf.Sqrt(sum / _analysis.Length);
    }

    void UpdateVad()
    {
        // 전송 중·재생 중에는 판단하지 않는다. 스피커 소리를 마이크가 되잡으면 무한 루프가 된다.
        if (isWaiting || IsSpeaking)
        {
            _aboveTime = _belowTime = 0f;
            _cooldownUntil = Time.time + ResumeCooldownSec;
            return;
        }
        if (!_listening || Time.time < _cooldownUntil) return;
        if (!serverReady) return;   // 서버가 죽어 있으면 헛되이 보내지 않는다
        if (!HasSession) return;    // 인물이 등록되지 않았으면 보낼 곳이 없다

        float threshold = VadThreshold;
        bool loud = MicLevel > threshold;

        if (!isRecording)
        {
            // 조용할 때만 소음 기준선을 따라간다
            if (!loud) _noiseFloor = Mathf.Lerp(_noiseFloor, MicLevel, Time.deltaTime * 0.8f);

            _aboveTime = loud ? _aboveTime + Time.deltaTime : 0f;
            if (_aboveTime >= StartHoldSec)
            {
                _aboveTime = 0f;
                StartRecording();
            }
        }
        else
        {
            _belowTime = loud ? 0f : _belowTime + Time.deltaTime;
            if (_belowTime >= silenceSec) StopAndSend();
        }
    }

    // ─────────── 녹음 구간 확정 ───────────
    public void StartRecording()
    {
        if (isRecording) return;
        if (!_listening || _recClip == null)
        {
            OnError?.Invoke("마이크가 준비되지 않았습니다.");
            return;
        }

        int pos = Microphone.GetPosition(_micDevice);
        _captureStart = pos - Mathf.RoundToInt(prerollSec * sampleRate);
        _recordStartTime = Time.time;
        isRecording = true;
        Debug.Log("[Raon] 발화 감지 — 녹음 시작");
    }

    public void StopAndSend()
    {
        if (!isRecording) return;
        isRecording = false;
        _aboveTime = _belowTime = 0f;
        _cooldownUntil = Time.time + ResumeCooldownSec;

        int total = _recClip.samples;
        int end = Microphone.GetPosition(_micDevice);
        int start = Wrap(_captureStart, total);
        int len = end - start;
        if (len < 0) len += total;

        float seconds = len / (float)sampleRate;
        if (seconds < MinUtteranceSec)
        {
            Debug.LogWarning($"[Raon] 발화가 너무 짧습니다({seconds:F2}초). 무시합니다.");
            return;
        }

        // 상한을 넘으면 뒤쪽(최근) 구간만 남긴다
        int maxLen = maxRecordSeconds * sampleRate;
        if (len > maxLen)
        {
            start = Wrap(end - maxLen, total);
            len = maxLen;
        }

        var samples = new float[len];
        ReadRing(start, len, samples);

        byte[] wav = EncodeWav(samples, _recClip.channels, _recClip.frequency);
        Debug.Log($"[Raon] 전송 {wav.Length / 1024}KB ({len / (float)sampleRate:F1}초)");
        StartCoroutine(useStreaming ? SendTalkStream(wav) : SendTalk(wav));
    }

    static int Wrap(int index, int total) => ((index % total) + total) % total;

    /// <summary>링 버퍼에서 start부터 count개를 읽는다. 경계를 넘으면 나눠 읽는다.</summary>
    void ReadRing(int start, int count, float[] dest)
    {
        int total = _recClip.samples;
        start = Wrap(start, total);

        if (start + count <= total)
        {
            _recClip.GetData(dest, start);
            return;
        }

        int first = total - start;
        var head = new float[first];
        _recClip.GetData(head, start);
        Array.Copy(head, 0, dest, 0, first);

        var tail = new float[count - first];
        _recClip.GetData(tail, 0);
        Array.Copy(tail, 0, dest, first, count - first);
    }

    // ─────────── 서버 통신 ───────────
    IEnumerator SendTalk(byte[] wav)
    {
        isWaiting = true;
        float t0 = Time.realtimeSinceStartup;

        var form = new List<IMultipartFormSection>
        {
            new MultipartFormFileSection("file", wav, "input.wav", "audio/wav"),
            new MultipartFormDataSection("session", sessionId),
            new MultipartFormDataSection("show_heard", showHeardSubtitle ? "1" : "0")
        };

        using (var req = UnityWebRequest.Post($"{serverUrl}/talk", form))
        {
            if (!string.IsNullOrEmpty(token)) req.SetRequestHeader("X-Token", token);
            req.timeout = 60;
            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
            {
                string msg = req.responseCode == 503
                    ? "서버가 아직 모델을 로딩 중입니다. 20초 뒤 다시 시도하세요."
                    : $"요청 실패: {req.error}";
                Debug.LogError($"[Raon] {msg}");
                OnError?.Invoke(msg);
                isWaiting = false;
                yield break;
            }

            lastHeard = UnityWebRequest.UnEscapeURL(req.GetResponseHeader("X-Heard") ?? "");
            lastAnswer = UnityWebRequest.UnEscapeURL(req.GetResponseHeader("X-Answer") ?? "");

            AudioClip clip = DecodeWav(req.downloadHandler.data, "reply");
            if (clip == null)
            {
                OnError?.Invoke("음성 디코딩 실패");
                isWaiting = false;
                yield break;
            }

            float elapsed = Time.realtimeSinceStartup - t0;
            Debug.Log($"[Raon] {FormatTiming(req, elapsed)} | 들은 말: {lastHeard} | 답변: {lastAnswer}");

            _audio.clip = clip;
            _audio.Play();
            OnAnswer?.Invoke(lastHeard, lastAnswer);
        }
        isWaiting = false;
    }

    // ─────────── 스트리밍 재생 ───────────
    RaonPcmStream _stream;
    AudioClip _streamClip;

    IEnumerator SendTalkStream(byte[] wav)
    {
        isWaiting = true;
        float t0 = Time.realtimeSinceStartup;

        var form = new List<IMultipartFormSection>
        {
            new MultipartFormFileSection("file", wav, "input.wav", "audio/wav"),
            new MultipartFormDataSection("session", sessionId),
            new MultipartFormDataSection("show_heard", showHeardSubtitle ? "1" : "0")
        };

        var stream = new RaonPcmStream(60 * 24000);
        using (var req = UnityWebRequest.Post($"{serverUrl}/talk_stream", form))
        {
            req.downloadHandler = stream;
            if (!string.IsNullOrEmpty(token)) req.SetRequestHeader("X-Token", token);
            req.timeout = 60;

            var op = req.SendWebRequest();
            bool playing = false;
            int rate = 24000;

            // 서버가 프레임 단위로 계속 흘려보내므로(실시간의 약 1.5배) 단순 임계값이면 된다.
            // 한번 시작하면 도착 속도가 재생 속도보다 빨라 뒤처지지 않는다.
            while (!op.isDone && !playing)
            {
                int avail = stream.Available;
                if (avail > 0)
                {
                    rate = ParseInt(req.GetResponseHeader("X-Sample-Rate"), 24000);
                    if (avail >= Mathf.RoundToInt(streamPrebufferSec * rate))
                        playing = StartStreamPlayback(req, stream, rate, t0);
                }
                yield return null;
            }

            // 프리버퍼가 차기 전에 전송이 끝난 짧은 답변
            if (!playing && stream.Available > 0)
            {
                rate = ParseInt(req.GetResponseHeader("X-Sample-Rate"), 24000);
                playing = StartStreamPlayback(req, stream, rate, t0);
            }

            while (!op.isDone) yield return null;

            if (req.result != UnityWebRequest.Result.Success)
            {
                string msg = req.responseCode == 503
                    ? "서버가 아직 모델을 로딩 중입니다. 20초 뒤 다시 시도하세요."
                    : $"요청 실패: {req.error}";
                Debug.LogError($"[Raon] {msg}");
                OnError?.Invoke(msg);
                StopStreamPlayback();
                isWaiting = false;
                yield break;
            }

            if (!playing)
            {
                OnError?.Invoke("서버가 음성을 보내지 않았습니다.");
                isWaiting = false;
                yield break;
            }

            // 남은 버퍼를 다 소진할 때까지
            while (stream.Available > 0) yield return null;

            // Unity는 재생보다 앞서 PCM을 미리 가져간다. 버퍼가 비었다고 바로 끊으면
            // 아직 재생되지 않은 뒷부분이 잘린다.
            yield return new WaitForSeconds(streamTailSec);

            Debug.Log($"[Raon] 재생 완료 {Time.realtimeSinceStartup - t0:F1}초 "
                    + $"({stream.TotalSamples / (float)rate:F1}초 분량, "
                    + $"끊김 {stream.Underruns}회 / {stream.UnderrunSamples / (float)rate:F2}초)");
            StopStreamPlayback();
        }
        isWaiting = false;
    }

    bool StartStreamPlayback(UnityWebRequest req, RaonPcmStream stream, int rate, float t0)
    {
        lastHeard = UnityWebRequest.UnEscapeURL(req.GetResponseHeader("X-Heard") ?? "");
        lastAnswer = UnityWebRequest.UnEscapeURL(req.GetResponseHeader("X-Answer") ?? "");

        _stream = stream;
        if (_streamClip) Destroy(_streamClip);
        _streamClip = AudioClip.Create("reply_stream", rate * 60, 1, rate, true, OnPcmRead);
        _audio.loop = false;
        _audio.clip = _streamClip;
        _audio.Play();
        isWaiting = false;

        string heardPart = string.IsNullOrEmpty(lastHeard) ? "" : $"들은 말: {lastHeard} | ";
        Debug.Log($"[Raon] 첫 소리까지 {Time.realtimeSinceStartup - t0:F1}초 | {heardPart}답변: {lastAnswer}");
        OnAnswer?.Invoke(lastHeard, lastAnswer);
        return true;
    }

    void StopStreamPlayback()
    {
        _stream = null;
        if (_audio) _audio.Stop();
    }

    /// <summary>오디오 스레드에서 호출됩니다. 모자라면 무음으로 채웁니다.</summary>
    void OnPcmRead(float[] data)
    {
        var s = _stream;
        int got = s != null ? s.Read(data, data.Length) : 0;
        for (int i = got; i < data.Length; i++) data[i] = 0f;
    }

    static int ParseInt(string s, int fallback)
        => int.TryParse(s, out int v) && v > 0 ? v : fallback;

    /// <summary>
    /// 왕복 시간을 서버 연산과 네트워크로 쪼개 표시한다.
    /// X-Elapsed는 서버가 STT→LLM→TTS에 쓴 시간(초)이므로, 나머지가 전송·네트워크 몫이다.
    /// </summary>
    static string FormatTiming(UnityWebRequest req, float elapsed)
    {
        string raw = req.GetResponseHeader("X-Elapsed");
        if (string.IsNullOrEmpty(raw))
            return $"왕복 {elapsed:F1}초 (X-Elapsed 없음)";

        if (!float.TryParse(raw, NumberStyles.Float, CultureInfo.InvariantCulture, out float serverSec))
            return $"왕복 {elapsed:F1}초 (X-Elapsed 해석 실패: '{raw}')";

        float network = elapsed - serverSec;
        return $"왕복 {elapsed:F1}초 = 서버 {serverSec:F1}초 + 네트워크 {network:F1}초";
    }

    // ─────────── 세션 추적 ───────────
    [Serializable]
    class SessionResponse
    {
        public string session;
        public bool has_model;
    }

    /// <summary>
    /// 서버의 현재 세션을 따라간다. 웹에서 인물을 등록하면 그 세션으로 갈아탄다.
    /// 등록된 세션이 없으면 HasSession 이 false 가 되어 대화가 막힌다 — 서버에
    /// 기본 인물이 없으므로, 막지 않으면 매번 409 를 받게 된다.
    /// </summary>
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
                    catch (Exception e) { Debug.LogWarning($"[Raon] 세션 응답 파싱 실패: {e.Message}"); }

                    if (s != null)
                    {
                        // 서버에 기본 인물이 없다. 등록된 세션이 없으면 대화 자체를 막는다.
                        string cur = s.session ?? "";
                        bool has = !string.IsNullOrEmpty(cur);
                        if (cur != sessionId || has != HasSession || s.has_model != SessionHasModel)
                        {
                            bool changed = cur != sessionId;
                            sessionId = cur;
                            HasSession = has;
                            SessionHasModel = s.has_model;
                            if (changed)
                                Debug.Log(has
                                    ? $"[Raon] 세션 전환: {sessionId} (모델 {(s.has_model ? "있음" : "없음")})"
                                    : "[Raon] 등록된 인물이 없습니다 — 웹에서 등록해야 대화할 수 있습니다.");
                            OnSessionChanged?.Invoke(sessionId, s.has_model);
                        }
                    }
                }
                else if (req.responseCode == 401)
                {
                    Debug.LogError("[Raon] 토큰이 틀렸습니다. 인스펙터의 Token 값을 확인하세요.");
                    yield break;   // 토큰이 틀리면 계속 두드려봐야 소용없다
                }
            }
            yield return wait;
        }
    }

    [Serializable]
    class HealthResponse
    {
        public string status;
        public float vram_gb;
        public int uptime_sec;
        public string voice;
        public int sessions;
    }

    /// <summary>서버 가동 여부를 확인합니다. 결과는 serverReady와 OnHealth로 전달됩니다.</summary>
    public IEnumerator CheckHealth()
    {
        using (var req = UnityWebRequest.Get($"{serverUrl}/health"))
        {
            if (!string.IsNullOrEmpty(token)) req.SetRequestHeader("X-Token", token);
            req.timeout = 10;
            yield return req.SendWebRequest();

            if (req.result != UnityWebRequest.Result.Success)
            {
                serverReady = false;
                string msg = $"서버에 연결할 수 없습니다 ({serverUrl}). 서버가 꺼져 있거나 교내망이 아닙니다.";
                Debug.LogError($"[Raon] {msg} — {req.error}");
                OnHealth?.Invoke(false, msg);
                yield break;
            }

            HealthResponse h = null;
            try { h = JsonUtility.FromJson<HealthResponse>(req.downloadHandler.text); }
            catch (Exception e) { Debug.LogWarning($"[Raon] /health 응답 파싱 실패: {e.Message}"); }

            serverReady = h != null && h.status == "ready";
            string info = serverReady
                ? $"서버 준비 완료 (VRAM {h.vram_gb:F1}GB, 세션 {h.sessions}개)"
                : $"서버가 아직 준비되지 않았습니다 (status={h?.status ?? "?"}). 약 20초 걸립니다.";
            Debug.Log($"[Raon] {info}");
            OnHealth?.Invoke(serverReady, info);
        }
    }

    /// <summary>대화 맥락을 초기화합니다.</summary>
    public void ResetSession()
    {
        StartCoroutine(DoReset());
    }

    IEnumerator DoReset()
    {
        var form = new List<IMultipartFormSection>
        {
            new MultipartFormDataSection("session", sessionId)
        };
        using (var req = UnityWebRequest.Post($"{serverUrl}/reset", form))
        {
            if (!string.IsNullOrEmpty(token)) req.SetRequestHeader("X-Token", token);
            yield return req.SendWebRequest();
            Debug.Log("[Raon] 대화 맥락 초기화");
        }
    }

    // ─────────── WAV 인코딩 (float 샘플 → 16bit PCM) ───────────
    static byte[] EncodeWav(float[] samples, int channels, int frequency)
    {
        int dataBytes = samples.Length * 2;
        var buf = new byte[44 + dataBytes];
        int p = 0;

        void S(string s) { foreach (char c in s) buf[p++] = (byte)c; }
        void I(int v) { buf[p++] = (byte)v; buf[p++] = (byte)(v >> 8); buf[p++] = (byte)(v >> 16); buf[p++] = (byte)(v >> 24); }
        void H(short v) { buf[p++] = (byte)v; buf[p++] = (byte)(v >> 8); }

        S("RIFF"); I(36 + dataBytes); S("WAVE");
        S("fmt "); I(16); H(1); H((short)channels);
        I(frequency); I(frequency * channels * 2);
        H((short)(channels * 2)); H(16);
        S("data"); I(dataBytes);

        foreach (float f in samples)
        {
            short v = (short)(Mathf.Clamp(f, -1f, 1f) * 32767f);
            buf[p++] = (byte)v; buf[p++] = (byte)(v >> 8);
        }
        return buf;
    }

    // ─────────── WAV 디코딩 (16bit PCM → AudioClip) ───────────
    static AudioClip DecodeWav(byte[] data, string name)
    {
        if (data == null || data.Length < 44) return null;
        if (data[0] != 'R' || data[1] != 'I' || data[2] != 'F' || data[3] != 'F')
        {
            Debug.LogError("[Raon] RIFF 헤더가 아닙니다.");
            return null;
        }

        int channels = 1, rate = 24000, bits = 16, dataPos = -1, dataLen = 0;
        int pos = 12;
        while (pos + 8 <= data.Length)
        {
            string id = "" + (char)data[pos] + (char)data[pos + 1] + (char)data[pos + 2] + (char)data[pos + 3];
            int size = BitConverter.ToInt32(data, pos + 4);
            int body = pos + 8;
            if (id == "fmt ")
            {
                channels = BitConverter.ToInt16(data, body + 2);
                rate = BitConverter.ToInt32(data, body + 4);
                bits = BitConverter.ToInt16(data, body + 14);
            }
            else if (id == "data") { dataPos = body; dataLen = size; break; }
            pos = body + size + (size % 2);
        }
        if (dataPos < 0 || bits != 16) { Debug.LogError($"[Raon] 지원하지 않는 형식 (bits={bits})"); return null; }

        dataLen = Mathf.Min(dataLen, data.Length - dataPos);
        int n = dataLen / 2;
        var samples = new float[n];
        for (int i = 0; i < n; i++)
            samples[i] = BitConverter.ToInt16(data, dataPos + i * 2) / 32768f;

        var clip = AudioClip.Create(name, n / channels, channels, rate, false);
        clip.SetData(samples, 0);
        return clip;
    }
}
