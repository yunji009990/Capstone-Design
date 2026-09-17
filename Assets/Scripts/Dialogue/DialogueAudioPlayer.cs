using System;
using UnityEngine;

/// <summary>Bounded PCM16 playback. Audio callbacks never access the websocket.
///
/// 실제 출력은 <see cref="DialogueAudioRenderer"/> 가 OnAudioFilterRead 에서 만든다.
/// 이 클래스는 기존 호출 계약(Enqueue/Pause/Resume/Stop/Consumed/Received/TailSeconds)을
/// 그대로 두고 수명과 메인 스레드 설정만 맡는다.</summary>
public sealed class DialogueAudioPlayer : IDisposable
{
    const int Rate = DialogueAudioRenderer.SourceRate;
    const int MaxPacketBytes = 9600;
    const int PrerollMillis = 160;     // 시작 선버퍼. 대기시간을 크게 늘리지 않는 범위
    const int RebufferMillis = 80;     // 공급이 끊긴 뒤 다시 모을 양

    readonly AudioSource _source;
    readonly DialogueAudioRenderer _renderer;
    readonly bool _ownsRenderer;
    AudioSettings.AudioConfigurationChangeHandler _configChanged;
    bool _started, _disposed;
    bool _sourcePaused;   // AudioSource 를 실제로 멈췄는가. 예약·감쇠 정지에서는 멈추지 않는다

#if UNITY_EDITOR
    /// <summary>재생 비교 모드의 협력 객체. 기본 streaming 에서는 null 이다.</summary>
    readonly DialogueBufferedClipPlayer _buffered;

    /// <summary>감쇠 정지는 오디오 스레드가 아니라 메인 스레드에서만 진행한다.
    /// 상태를 읽을 때마다 한 칸 나아가게 해서 Update 주기에 감쇠가 끝난다.</summary>
    DialogueBufferedClipPlayer Buffered
    {
        get
        {
            if (_buffered != null) _buffered.Tick();
            return _buffered;
        }
    }
#endif

    /// <summary>응답 전체를 받은 뒤 재생하는 비교 모드인가. 플레이어 빌드에서는 항상 false 다.</summary>
    public bool ReceiveWholeResponse
    {
        get
        {
#if UNITY_EDITOR
            return _buffered != null;
#else
            return false;
#endif
        }
    }

    /// <summary>실제로 멈춰 있는가. 예약만 된 상태는 아직 false 다. 그래야 경계 보고와
    /// 완료 처리가 멈추기 전까지 정상으로 돌아간다.</summary>
    public bool Paused
    {
        get
        {
#if UNITY_EDITOR
            if (_buffered != null) return Buffered.Paused;
#endif
            return _renderer != null && _renderer.PauseApplied;
        }
    }
    public bool Playing
    {
        get
        {
#if UNITY_EDITOR
            if (_buffered != null) return Buffered.Playing;
#endif
            return _started && !Paused;
        }
    }
    public long Consumed
    {
        get
        {
#if UNITY_EDITOR
            if (_buffered != null) return Buffered.Consumed;
#endif
            return _renderer != null ? _renderer.Consumed : 0;
        }
    }
    public long Received
    {
        get
        {
#if UNITY_EDITOR
            if (_buffered != null) return Buffered.Received;
#endif
            return _renderer != null ? _renderer.Received : 0;
        }
    }
    public float TailSeconds { get; }
    /// <summary>실제로 출력 버퍼에 쓴 PCM 의 최대 진폭. 무음 여부 확인용이다.</summary>
    public float OutputPeak
    {
        get
        {
#if UNITY_EDITOR
            if (_buffered != null) return Buffered.OutputPeak;
#endif
            return _renderer != null ? _renderer.Peak : 0f;
        }
    }
    /// <summary>정지 예약·실제 정지 상태. 보고 시점을 정할 때 쓴다.</summary>
    public DialogueAudioRenderer.PauseStatus PauseState
    {
        get
        {
#if UNITY_EDITOR
            if (_buffered != null) return Buffered.PauseState;
#endif
            return _renderer != null ? _renderer.PauseState : default;
        }
    }

    /// <summary>receiveWholeResponse 는 에디터 비교 모드 전용이다. 기본은 기존 streaming 이고
    /// 플레이어 빌드에서는 값과 상관없이 streaming 으로 동작한다.</summary>
    public DialogueAudioPlayer(AudioSource source, bool receiveWholeResponse = false)
    {
        _source = source;
        _source.playOnAwake = false;
        _source.spatialBlend = 0;
        _source.loop = false;
        // 클립을 쓰지 않는다. 필터가 직접 출력을 만든다.
        _source.clip = null;

#if UNITY_EDITOR
        if (receiveWholeResponse)
        {
            // 비교 모드다. 출력은 일반 AudioClip 이 만든다. 진행 렌더러를 붙이지 않는다.
            _buffered = new DialogueBufferedClipPlayer(source);
            AudioSettings.GetDSPBufferSize(out int fullSize, out int fullBuffers);
            int fullRate = AudioSettings.outputSampleRate > 0 ? AudioSettings.outputSampleRate : 48000;
            TailSeconds = Mathf.Max(.3f, (float)(fullSize * fullBuffers) / fullRate + .1f);
            return;
        }
#endif
        // 같은 GameObject 의 마지막 필터로 붙는다. 뒤에 붙는 진단 탭은 이 출력을 본다.
        _renderer = source.GetComponent<DialogueAudioRenderer>();
        if (_renderer == null)
        {
            _renderer = source.gameObject.AddComponent<DialogueAudioRenderer>();
            _ownsRenderer = true;
        }
        _renderer.Configure(Rate * PrerollMillis / 1000, Rate * RebufferMillis / 1000);
        _renderer.Reset();

        AudioSettings.GetDSPBufferSize(out int size, out int buffers);
        int outputRate = AudioSettings.outputSampleRate > 0 ? AudioSettings.outputSampleRate : 48000;
        _renderer.UpdateOutputRate(outputRate, false);
        TailSeconds = Mathf.Max(.3f, (float)(size * buffers) / outputRate + .1f);

        _configChanged = changed =>
        {
            // 메인 스레드 콜백이다. 장치 이름 같은 원문은 다루지 않고 표본율만 반영한다.
            int rate = AudioSettings.outputSampleRate > 0 ? AudioSettings.outputSampleRate : 48000;
            if (_renderer != null) _renderer.UpdateOutputRate(rate, changed);
        };
        AudioSettings.OnAudioConfigurationChanged += _configChanged;
    }

#if UNITY_EDITOR
    /// <summary>에디터 검사 전용. AudioSource 없이 렌더러만 쓴다. 그 GameObject 에는
    /// AudioSource 가 없으므로 Unity 가 오디오 콜백을 자동으로 부르지 않는다. 검사가
    /// 콜백을 직접 부르므로 결과가 결정적이고 소리도 나지 않는다. 호출 계약은 같다.</summary>
    public DialogueAudioPlayer(DialogueAudioRenderer renderer, int outputRate, float drainSeconds)
    {
        _renderer = renderer;
        _renderer.Configure(Rate * PrerollMillis / 1000, Rate * RebufferMillis / 1000);
        _renderer.Reset();
        _renderer.ForceOutputRate(outputRate);
        TailSeconds = Mathf.Max(0f, drainSeconds);
    }
#endif

    public void Enqueue(byte[] pcm)
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.Enqueue(pcm); return; }
#endif
        if (pcm == null || pcm.Length == 0 || pcm.Length % 2 != 0 || pcm.Length > MaxPacketBytes)
            throw new ArgumentException("잘못된 PCM 음성 패킷입니다.");
        if (!_renderer.Write(pcm, pcm.Length))
            throw new InvalidOperationException("음성 재생 버퍼가 가득 찼습니다.");
        if (!_started && !Paused) StartPlayback();
    }

    void StartPlayback()
    {
        if (_started) return;
        _renderer.Begin();
        if (_source != null) _source.Play();
        _started = true;
    }

    /// <summary>이 응답의 PCM 이 더 오지 않는다. 남은 짧은 조각까지 내보낸다.</summary>
    public void MarkComplete()
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.MarkComplete(); return; }
#endif
        if (_renderer != null) _renderer.MarkComplete();
    }

    public DialogueAudioRenderer.Diagnostics Snapshot()
    {
#if UNITY_EDITOR
        if (_buffered != null) return Buffered.Snapshot();
#endif
        return _renderer != null ? _renderer.Snapshot() : default;
    }

    public void ResetDiagnostics()
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.ResetDiagnostics(); return; }
#endif
        if (_renderer != null) _renderer.ResetDiagnostics();
    }

    public void ResetPeak()
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.ResetPeak(); return; }
#endif
        if (_renderer != null) _renderer.ResetPeak();
    }

    public void Stop()
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.Stop(); return; }
#endif
        _started = _sourcePaused = false;
        if (_source != null) _source.Stop();
        // 남은 PCM 과 보간 위상까지 모두 버린다. 다음 응답에 섞이지 않는다.
        if (_renderer != null) _renderer.Reset();
    }

    /// <summary>지금 그 자리에서 멈춘다. 기존 즉시 정지 경로다.</summary>
    public void Pause()
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.Pause(); return; }
#endif
        if (Paused || _renderer == null) return;
        _renderer.SetPaused(true);
        if (_started && _source != null) _source.Pause();
        _sourcePaused = _started && _source != null;
    }

    /// <summary>이 원본 샘플 직전에서 멈추도록 렌더러에 예약한다. 이미 지나간 경계면 false 다.
    /// 정지는 오디오 콜백이 만들므로 AudioSource 는 계속 돌려 둔다.</summary>
    public bool PauseAtSample(long sample)
    {
#if UNITY_EDITOR
        // 비교 모드는 샘플 단위 정지를 지원하지 않는다. 호출한 쪽은 감쇠 경로로 넘어간다.
        if (_buffered != null) return _buffered.PauseAtSample(sample);
#endif
        return _renderer != null && _renderer.ScheduleSourcePause(sample);
    }

    /// <summary>경계를 찾지 못했을 때만 쓰는 짧은 감쇠 정지다. 낱말 경계가 아니다.</summary>
    public void PauseWithFade(int millis)
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.PauseWithFade(millis); return; }
#endif
        if (_renderer != null) _renderer.FadeToPause(millis);
    }

    /// <summary>예약과 감쇠만 지운다. 이미 멈춘 상태는 그대로 둔다.</summary>
    public void CancelScheduledPause()
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.CancelScheduledPause(); return; }
#endif
        if (_renderer != null) _renderer.CancelScheduledPause();
    }

    public void Resume()
    {
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.Resume(); return; }
#endif
        if (_renderer == null) return;
        bool wasPaused = Paused;
        // 예약만 되어 있었다면 한 번도 멈추지 않았다. 예약을 지우면 틈 없이 이어진다.
        _renderer.CancelScheduledPause();
        if (!wasPaused) return;
        _renderer.SetPaused(false);
        if (_started)
        {
            if (_sourcePaused && _source != null) _source.UnPause();
            _sourcePaused = false;
        }
        else if (_renderer.Received > _renderer.Consumed)
        {
            // 보류 중에 도착한 PCM 이 있으면 재개 시점에 이어서 내보낸다.
            StartPlayback();
        }
    }

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
#if UNITY_EDITOR
        if (_buffered != null) { _buffered.Dispose(); return; }
#endif
        if (_configChanged != null)
        {
            AudioSettings.OnAudioConfigurationChanged -= _configChanged;
            _configChanged = null;
        }
        Stop();
        if (_ownsRenderer && _renderer != null) UnityEngine.Object.Destroy(_renderer);
    }
}
