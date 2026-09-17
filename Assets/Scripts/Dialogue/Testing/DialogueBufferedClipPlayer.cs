#if UNITY_EDITOR
using System;
using UnityEngine;

/// <summary>재생 비교 모드 전용. 응답 PCM 을 전부 받은 뒤 일반 AudioClip 으로 한 번에 재생한다.
///
/// 웹에서 WAV 를 받아 듣는 것과 같은 조건을 Unity 안에서 만들어 보려는 검사용 경로다.
/// 이 경로가 말끝 문제를 고친다는 뜻이 아니다. 스트리밍 경로와 무엇이 다른지를 비교하기 위한
/// 관측 장치이고, 기본 재생은 그대로 <see cref="DialogueAudioRenderer"/> 가 맡는다.
///
/// 출력 진폭을 기존 스트리밍과 맞추려고 mono 수신 샘플을 두 채널에 같은 값으로 복사한
/// stereo 클립을 쓴다. clip.samples 는 원본 샘플 수 N 이고 float 배열 길이는 2N 이다.</summary>
internal sealed class DialogueBufferedClipPlayer : IDisposable
{
    public const int Rate = DialogueAudioRenderer.SourceRate;   // 24000
    const int MaxSeconds = 45;
    const int MaxSamples = Rate * MaxSeconds;
    const int MaxPacketBytes = 9600;
    const int InitialSamples = Rate * 4;

    readonly AudioSource _source;

    float[] _mono = new float[InitialSamples];
    int _received;              // 받은 원본 mono 샘플 수 N
    int _consumed;              // 관측한 source.timeSamples 의 최대값
    AudioClip _clip;

    bool _complete;             // MarkComplete 를 이미 처리했다
    bool _requested;            // Play 를 실제로 불렀다
    bool _confirmed;            // 재생 시작을 한 번이라도 확인했다
    bool _ended;                // 자연 종료
    bool _paused;               // 요청으로 멈춘 상태
    bool _nativePaused;         // AudioSource.Pause 를 실제로 불렀다
    bool _disposed;

    // 감쇠 정지. 오디오 스레드를 쓰지 않고 Tick 에서만 진행한다.
    bool _fading;
    float _fadeStart, _fadeSeconds, _fadeFrom;

    bool _pauseApplied;
    int _pauseCause;
    long _pauseSample = -1;

    public DialogueBufferedClipPlayer(AudioSource source)
    {
        if (source == null) throw new ArgumentNullException(nameof(source));
        // 진행 렌더러가 붙어 있으면 OnAudioFilterRead 가 클립 출력을 덮어쓴다. 같이 쓸 수 없다.
        if (source.GetComponent<DialogueAudioRenderer>() != null)
            throw new InvalidOperationException(
                "비교 모드에서는 AudioSource 에 DialogueAudioRenderer 가 없어야 합니다.");
        _source = source;
    }

    /// <summary>실제로 재생 중인가. 아직 다 받지 못한 동안은 false 다. 모으는 중을 재생 중이라고 하지 않는다.</summary>
    public bool Playing => _requested && !_paused && !_ended;
    public bool Paused => _paused;
    public long Received => _received;
    /// <summary>재생한 위치다. 다 받았다는 사실을 재생 완료로 세지 않는다.</summary>
    public long Consumed => _consumed;
    /// <summary>이 경로는 출력 진폭을 재지 않는다. 0 을 재생 정상의 근거로 쓰지 않는다.</summary>
    public float OutputPeak => 0f;

    /// <summary>정지 상태 보고. 적용 전에도 감쇠·예약과 현재 위치를 실제 값으로 채운다.
    /// 아직 멈추지 않았다는 사실을 전부 false/0 인 default 로 보고하지 않는다.</summary>
    public DialogueAudioRenderer.PauseStatus PauseState => new DialogueAudioRenderer.PauseStatus
    {
        applied = _pauseApplied,
        cause = _pauseApplied ? _pauseCause : DialogueAudioRenderer.PauseNone,
        appliedSample = _pauseApplied ? _pauseSample : -1,
        // 멈춘 뒤에는 정지 표본, 그 전에는 Tick 이 관측한 현재 재생 위치다.
        position = _pauseApplied ? _pauseSample : _consumed,
        // 감쇠는 Tick 에서만 진행한다. 적용 전이면 예약만 걸린 상태다.
        fading = _fading,
        scheduled = _fading && !_pauseApplied,
    };

    public void Enqueue(byte[] pcm)
    {
        if (_disposed) throw new InvalidOperationException("이미 정리된 재생기입니다.");
        if (pcm == null || pcm.Length == 0 || pcm.Length % 2 != 0 || pcm.Length > MaxPacketBytes)
            throw new ArgumentException("잘못된 PCM 음성 패킷입니다.");
        if (_complete)
            throw new InvalidOperationException("완료된 응답에는 PCM 을 더 넣을 수 없습니다.");
        int count = pcm.Length / 2;
        if (_received + count > MaxSamples)
            throw new InvalidOperationException("음성 재생 버퍼가 가득 찼습니다.");
        EnsureCapacity(_received + count);
        for (int i = 0; i < count; i++)
        {
            int at = i * 2;
            short value = (short)(pcm[at] | (pcm[at + 1] << 8));
            _mono[_received + i] = value / 32768f;
        }
        _received += count;
    }

    /// <summary>이 응답의 PCM 이 더 오지 않는다. 여기서만 클립을 만들고 재생을 시작한다.
    /// 보류 중이면 재생하지 않고 Resume 에서 시작한다. 두 번 불러도 다시 시작하지 않는다.</summary>
    public void MarkComplete()
    {
        if (_disposed || _complete) return;
        _complete = true;
        if (_received <= 0) return;
        CreateClip();
        if (!_paused) StartPlayback();
    }

    /// <summary>메인 스레드에서 상태를 읽을 때마다 한 칸 나아간다. 감쇠와 재생 위치를 여기서만 본다.</summary>
    public void Tick()
    {
        if (_disposed || _source == null) return;
        AdvanceFade();
        if (_ended || _paused || !_requested) return;
        // 리스너 정지·비활성 소스·다른 클립은 "재생이 끝났다"는 근거가 아니다.
        // 여기서 자연 종료로 세면 consumed=N 으로 거짓 완료가 된다.
        if (AudioListener.pause && !_source.ignoreListenerPause) return;
        if (!_source.enabled || !_source.gameObject.activeInHierarchy) return;
        if (_clip == null || _source.clip != _clip) return;
        if (_source.isPlaying)
        {
            _confirmed = true;
            int at = _source.timeSamples;
            if (at > _consumed) _consumed = at;
            return;
        }
        // 시작을 한 번도 확인하지 못했다면 아직 끝난 것이 아니다.
        if (!_confirmed) return;
        _ended = true;
        _consumed = _received;
    }

    public DialogueAudioRenderer.Diagnostics Snapshot() => new DialogueAudioRenderer.Diagnostics
    {
        running = Playing,
        paused = _paused,
        complete = _complete,
        ended = _ended,
        sourceRate = Rate,
        outputRate = AudioSettings.outputSampleRate > 0 ? AudioSettings.outputSampleRate : 48000,
        received = _received,
        consumed = _consumed,
        buffered = Math.Max(0, _received - _consumed),
        // 아직 다 받지 못한 동안만 모으는 중이다. 완료 뒤 재생 대기는 buffering 이 아니다.
        buffering = !_complete && _received > 0,
        // 응답 전체를 들고 있으므로 최대 적재량은 지금까지 받은 총 표본 수다.
        maxBuffered = _received,
        // native 출력이라 DSP 프레임·콜백을 재지 않는다. 추정해서 적지 않고 미측정(-1)로 남긴다.
        renderedFrames = -1,
        callbacks = -1,
        // 응답 전체를 모은 뒤 재생하므로 공급 부족이 있을 수 없다.
        underrunFrames = 0,
        rebuffers = 0,
        prerollSamples = 0,
        configChanges = 0,
    };

    public void ResetDiagnostics() { }
    public void ResetPeak() { }

    /// <summary>샘플 단위 정지는 지원하지 않는다. 지원한다고 말하지 않기 위해 항상 false 다.
    /// 호출한 쪽은 기존 grace 뒤 감쇠 경로로 넘어간다.</summary>
    public bool PauseAtSample(long sample) => false;

    /// <summary>지금 그 자리에서 멈춘다.</summary>
    public void Pause() => ApplyPause(DialogueAudioRenderer.PauseImmediate);

    /// <summary>재생 중이면 짧게 감쇠한 뒤 멈춘다. 아직 모으는 중이면 그대로 멈춘다.
    /// 이 정지는 구절 경계가 아니다.</summary>
    public void PauseWithFade(int millis)
    {
        if (_disposed || _paused || _fading) return;
        if (!_requested || _source == null || !_source.isPlaying)
        {
            ApplyPause(DialogueAudioRenderer.PauseImmediate);
            return;
        }
        _fading = true;
        _fadeFrom = _source.volume;
        _fadeStart = Time.realtimeSinceStartup;
        _fadeSeconds = Mathf.Max(0f, millis / 1000f);
        AdvanceFade();
    }

    /// <summary>아직 적용되지 않은 감쇠만 취소하고 음량을 되돌린다. 이미 멈춘 상태는 그대로 둔다.</summary>
    public void CancelScheduledPause()
    {
        if (!_fading) return;
        _fading = false;
        if (_source != null) _source.volume = _fadeFrom;
    }

    public void Resume()
    {
        CancelScheduledPause();
        if (!_paused) return;
        _paused = _pauseApplied = false;
        _pauseCause = 0;
        _pauseSample = -1;
        if (_nativePaused)
        {
            _nativePaused = false;
            if (_source != null) _source.UnPause();
            return;
        }
        // 보류 중에 수신이 끝났다면 재개 시점에 처음부터 내보낸다.
        if (_complete && _clip != null && !_requested) StartPlayback();
    }

    /// <summary>모아 둔 PCM 과 클립을 모두 버린다. 미완성 취소본은 재생하지 않는다.</summary>
    public void Stop()
    {
        CancelScheduledPause();
        if (_source != null)
        {
            _source.Stop();
            _source.clip = null;
        }
        Reset();
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        Stop();
    }

    void StartPlayback()
    {
        if (_requested || _clip == null || _source == null) return;
        _source.clip = _clip;
        _source.timeSamples = 0;
        _source.Play();
        _requested = true;
        _nativePaused = false;
        _confirmed = _source.isPlaying;
        if (!_confirmed)
            Debug.LogWarning("[Dialogue] 비교 모드: Play 직후 AudioSource 가 재생 상태가 아닙니다.");
    }

    void CreateClip()
    {
        var interleaved = new float[_received * 2];
        for (int i = 0; i < _received; i++)
        {
            float value = _mono[i];
            interleaved[i * 2] = value;
            interleaved[i * 2 + 1] = value;
        }
        // mono 클립은 같은 PCM 이라도 기존 streaming 출력보다 낮게(약 .707 배) 들린다.
        // 두 채널에 같은 값을 넣어 기존 진폭을 유지한다.
        var clip = AudioClip.Create("DialogueWholeResponse", _received, 2, Rate, false);
        if (clip == null || !clip.SetData(interleaved, 0))
        {
            if (clip != null) UnityEngine.Object.Destroy(clip);
            Reset();
            throw new InvalidOperationException("응답 음성 클립을 만들지 못했습니다.");
        }
        _clip = clip;
        // 만든 즉시 붙인다. 검사 코드가 재생 전에도 clip.GetData 로 내용을 확인할 수 있다.
        // 실제 재생 시작은 기존대로 완료·비보류일 때 StartPlayback 이 맡는다.
        if (_source != null) _source.clip = _clip;
    }

    void AdvanceFade()
    {
        if (!_fading) return;
        float elapsed = Time.realtimeSinceStartup - _fadeStart;
        if (_fadeSeconds > 0f && elapsed < _fadeSeconds)
        {
            if (_source != null) _source.volume = _fadeFrom * (1f - elapsed / _fadeSeconds);
            return;
        }
        _fading = false;
        if (_source != null) _source.volume = _fadeFrom;
        ApplyPause(DialogueAudioRenderer.PauseFade);
    }

    void ApplyPause(int cause)
    {
        if (_disposed || _paused) return;
        bool live = _requested && _source != null && _source.isPlaying;
        // 멈추기 전에 현재 위치부터 확정한다. 정지 표본이 실제보다 앞서면 안 된다.
        if (live)
        {
            int at = _source.timeSamples;
            if (at > _consumed) _consumed = at;
        }
        // 감쇠 중 즉시 정지가 들어오면 남은 감쇠를 먼저 취소해 음량을 원상복원한다.
        // 그러지 않으면 _fading 이 남아 정지 뒤에도 volume 을 계속 낮추고 정지 원인을 덮어쓴다.
        // AdvanceFade 는 이미 _fading 을 내리고 음량을 되돌린 뒤 부르므로 여기서는 무동작이다.
        CancelScheduledPause();
        _paused = _pauseApplied = true;
        _pauseCause = cause;
        if (live)
        {
            _source.Pause();
            _nativePaused = true;
        }
        _pauseSample = _consumed;
    }

    void EnsureCapacity(int samples)
    {
        if (samples <= _mono.Length) return;
        int size = _mono.Length;
        while (size < samples) size *= 2;
        if (size > MaxSamples) size = MaxSamples;
        Array.Resize(ref _mono, size);
    }

    void Reset()
    {
        _received = _consumed = 0;
        _complete = _requested = _confirmed = _ended = false;
        _paused = _nativePaused = _fading = _pauseApplied = false;
        _pauseCause = 0;
        _pauseSample = -1;
        if (_clip != null)
        {
            UnityEngine.Object.Destroy(_clip);
            _clip = null;
        }
        if (_mono.Length > InitialSamples) _mono = new float[InitialSamples];
        else Array.Clear(_mono, 0, _mono.Length);
    }
}

/// <summary>재생 비교 모드 선택값. 프로젝트별 EditorPrefs 에만 저장하며 서버·씬·등록 세션은 건드리지 않는다.
/// 선택은 다음 Play 부터 적용된다. 메뉴는 <c>Assets/Editor/DialoguePlaybackComparisonMenu.cs</c> 에 있다.</summary>
public static class DialoguePlaybackComparison
{
    public const string WholeResponseMenu = "Tools/Dialogue/Playback mode/Receive whole response (AudioClip)";
    public const string StreamingMenu = "Tools/Dialogue/Playback mode/Stream as received";

    // 같은 PC 의 다른 clone 이 서로의 선택을 덮어쓰지 않게 프로젝트 경로를 키에 넣는다.
    static string Key => "Dialogue.Playback.WholeResponse." + Application.dataPath;

    /// <summary>값이 없으면 false 다. 기본은 기존 streaming 이다.</summary>
    public static bool WholeResponseEnabled
    {
        get { return UnityEditor.EditorPrefs.GetBool(Key, false); }
        set { UnityEditor.EditorPrefs.SetBool(Key, value); }
    }
}
#endif
