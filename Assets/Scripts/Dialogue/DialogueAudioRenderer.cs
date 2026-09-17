using System;
using UnityEngine;

/// <summary>24 kHz PCM 링 버퍼를 출력 표본율로 이어서 내보내는 절차적 렌더러.
///
/// AudioClip.Create(stream:true) 의 읽기 콜백은 생성·시작 시점에 큰 덩어리를 미리 당겨
/// 읽는다. 실측(2026-09-16 warm 회차)에서 시작 전 콜백 5회가 19,200샘플을 요청해
/// 4,800샘플만 채우고 14,400샘플(0.6초)을 0으로 메웠고, 그 무음이 먼저 재생됐다.
/// 여기서는 클립 없이 필터가 직접 출력을 만들어 그 선읽기 자체를 없앤다.
///
/// 리샘플 모델: 출력 프레임 k 는 원본 시간 k*step(step = 24000/출력표본율)에 대응하고
/// 이웃한 두 원본 샘플을 선형 보간한다. 원본 N 샘플은 정확히 ceil(N/step) 프레임을
/// 만들고 그 뒤로는 무음이다. 마지막 샘플 뒤에는 0 을 하나 이어 붙여 0 으로 내려가며
/// 끝난다. 마지막 값을 계속 내보내 DC 가 남던 결함(draft-short-final.json)을 막는다.
///
/// 공급이 끊기면 _prev/_next/_phase 를 건드리지 않고 그대로 멈춘다. 다시 공급되면
/// 같은 상태에서 이어가므로 외삽·중복·손실이 없다.
///
/// OnAudioFilterRead 는 오디오 스레드에서 불린다. 이 안에서는 Unity API·로그·파일·
/// 할당을 하지 않고 잠금과 카운터만 쓴다.</summary>
public sealed class DialogueAudioRenderer : MonoBehaviour
{
    public const int SourceRate = 24000;

    const int Advanced = 1;      // 다음 두 샘플을 확보했다
    const int Starved = 0;       // 공급이 끊겼다. 상태를 그대로 두고 멈춘다
    const int Finished = -1;     // 이 응답은 끝났다

    // 멈춘 원인. 오디오 스레드에서 문자열을 만들지 않으려고 정수로 둔다.
    public const int PauseNone = 0;
    public const int PauseBoundary = 1;    // 예약한 원본 샘플 직전에서 멈췄다
    public const int PauseFade = 2;        // 감쇠 램프가 끝나 멈췄다
    public const int PauseImmediate = 3;   // 그 자리에서 바로 멈췄다

    readonly object _gate = new object();
    readonly float[] _ring = new float[SourceRate * 45];
    int _read, _write, _count;
    long _received, _consumed;

    bool _running, _paused, _complete;
    bool _buffering = true;      // 시작·재버퍼 대기 중
    bool _primed;                // 보간용 두 샘플을 확보했는가
    bool _terminated;            // 마지막 샘플 뒤의 0 을 이미 넣었는가
    bool _ended;                 // 낼 것이 더 없다. 이후로는 무음
    bool _everRendered;
    int _prerollSamples = SourceRate / 5;
    int _rebufferSamples = SourceRate / 10;
    double _phase;               // 다음 출력이 _prev~_next 사이 어디인지 [0,1)
    float _prev, _next;
    int _outputRate = 48000;
    // 지금 _prev 가 원본 스트림의 몇 번째 샘플인가. 실제로 내보내는 위치는 _sourceBase + _phase 다.
    // _consumed 는 보간 시드로 한두 샘플 앞서 읽으므로 정지 지점의 근거로 쓸 수 없다.
    long _sourceBase;
    // 이 원본 샘플을 건드리기 전에 멈춘다. -1 이면 예약이 없다.
    long _pauseTarget = -1;
    long _pauseAppliedSample = -1;
    int _pauseCause;
    bool _pauseApplied;
    // 경계를 찾지 못한 정지의 클릭음만 없애는 짧은 선형 감쇠다. 낱말 경계를 만드는 기능이 아니다.
    int _fadeRemaining, _fadeLength;

    // --- 진단 계측 ---
    long _callbacks, _renderedFrames, _silentFrames;
    long _preOutputCallbacks, _preOutputConsumed;
    long _underruns, _underrunFrames, _rebuffers, _configChanges;
    int _maxBuffered, _minBufferedAtCallback = int.MaxValue;
    int _callbackMin, _callbackMax;
    float _peak;

    [Serializable]
    public struct Diagnostics
    {
        public long received, consumed;
        public int buffered, maxBuffered, minBufferedAtCallback;
        public bool running, paused, complete, buffering, ended;
        public long callbacks, renderedFrames, silentFrames;
        // 첫 출력 프레임보다 먼저 일어난 콜백/소비다.
        // 예전 AudioClip 시절의 preArmConsumed(선읽기 소비)와는 의미가 다르다.
        public long preOutputCallbacks, preOutputConsumed;
        public long underruns, underrunFrames, rebuffers, configChanges;
        public int callbackMin, callbackMax;
        public int sourceRate, outputRate, prerollSamples, rebufferSamples;
        public float peak;
    }

    /// <summary>정지 예약과 실제 정지 상태. 메인 스레드가 읽는다.</summary>
    public struct PauseStatus
    {
        public bool scheduled, fading, applied;
        public int cause;
        public long targetSample, appliedSample;
        public double position;   // 지금 내보내는 소리의 원본 샘플 위치
    }

    void OnEnable()
    {
        _outputRate = AudioSettings.outputSampleRate > 0 ? AudioSettings.outputSampleRate : 48000;
    }

    /// <summary>메인 스레드에서 출력 설정 변화를 반영한다. 장치 이름은 다루지 않는다.</summary>
    public void UpdateOutputRate(int rate, bool countChange)
    {
        lock (_gate)
        {
            if (rate > 0) _outputRate = rate;
            if (countChange) _configChanges++;
        }
    }

    /// <summary>검사에서 출력 표본율을 고정할 때 쓴다. 장치 설정을 바꾸지 않는다.</summary>
    public void ForceOutputRate(int rate)
    {
        lock (_gate) if (rate > 0) _outputRate = rate;
    }

    public void Configure(int prerollSamples, int rebufferSamples)
    {
        lock (_gate)
        {
            _prerollSamples = Mathf.Clamp(prerollSamples, 0, _ring.Length / 4);
            _rebufferSamples = Mathf.Clamp(rebufferSamples, 0, _prerollSamples);
        }
    }

    public long Received { get { lock (_gate) return _received; } }
    public long Consumed { get { lock (_gate) return _consumed; } }
    public float Peak { get { lock (_gate) return _peak; } }
    public bool Ended { get { lock (_gate) return _ended; } }
    public bool PauseApplied { get { lock (_gate) return _pauseApplied; } }
    public void ResetPeak() { lock (_gate) _peak = 0f; }

    public PauseStatus PauseState
    {
        get
        {
            lock (_gate)
                return new PauseStatus
                {
                    scheduled = _pauseTarget >= 0, fading = _fadeLength > 0, applied = _pauseApplied,
                    cause = _pauseCause, targetSample = _pauseTarget,
                    appliedSample = _pauseAppliedSample, position = Position(),
                };
        }
    }

    /// <summary>실제로 내보내는 소리의 원본 샘플 위치. 보간 선읽기를 포함하지 않는다.</summary>
    double Position() => _primed ? _sourceBase + _phase : _consumed;

    public bool Write(byte[] pcm, int bytes)
    {
        lock (_gate)
        {
            int samples = bytes / 2;
            if (_count + samples > _ring.Length) return false;
            for (int i = 0; i < bytes; i += 2)
            {
                _ring[_write] = (short)(pcm[i] | (pcm[i + 1] << 8)) / 32768f;
                _write = (_write + 1) % _ring.Length;
                _count++;
            }
            _received += samples;
            if (_count > _maxBuffered) _maxBuffered = _count;
            if (_ended)
            {
                // 끝난 뒤에 새 PCM 이 오면 버리지 않고 다시 시작한다. 이어 붙이지는 않는다.
                _ended = _terminated = _primed = false;
                _buffering = true;
                _phase = 0;
                _prev = _next = 0f;
            }
            return true;
        }
    }

    public void Begin() { lock (_gate) _running = true; }

    /// <summary>즉시 정지·재개. 남은 PCM 과 보간 위상은 그대로 두므로 재개하면 이어진다.</summary>
    public void SetPaused(bool paused)
    {
        lock (_gate)
        {
            _pauseTarget = -1;
            _fadeLength = _fadeRemaining = 0;
            if (paused) { ApplyPause(PauseImmediate); return; }
            _paused = _pauseApplied = false;
            _pauseCause = PauseNone;
            _pauseAppliedSample = -1;
        }
    }

    /// <summary>이 원본 샘플부터는 한 표본도 내보내지 않고 그 직전에서 멈춘다.
    ///
    /// 서버가 알려 준 구절 경계(audio.boundary.samples)를 그대로 받는다. 이미 지나간
    /// 경계면 아무것도 예약하지 않고 false 를 돌려준다. 늦은 정지를 경계라고 부르지
    /// 않기 위해서다. 정지는 오디오 콜백이 표본 단위로 만든다.</summary>
    public bool ScheduleSourcePause(long sample)
    {
        lock (_gate)
        {
            if (_paused || _pauseApplied || sample <= 0) return false;
            if (_primed ? _sourceBase + 1 >= sample : _consumed >= sample) return false;
            _pauseTarget = sample;
            return true;
        }
    }

    /// <summary>millis 동안 선형으로 줄인 뒤 그 자리에서 멈춘다. 예약한 경계보다 우선한다.
    /// 감쇠는 오디오 콜백이 만드므로 호출자는 AudioSource 를 멈추지 않아야 한다.
    /// 공급이 끊기면 램프가 끝나지 않는다. 그 경우의 상한은 호출자가 가진다.</summary>
    public void FadeToPause(int millis)
    {
        lock (_gate)
        {
            _pauseTarget = -1;
            if (_paused || _pauseApplied || _fadeLength > 0) return;
            if (millis <= 0) { ApplyPause(PauseImmediate); return; }
            _fadeLength = _fadeRemaining = Mathf.Max(1, _outputRate * millis / 1000);
        }
    }

    /// <summary>예약과 감쇠만 지운다. 이미 멈춘 상태는 건드리지 않는다.</summary>
    public void CancelScheduledPause()
    {
        lock (_gate)
        {
            _pauseTarget = -1;
            _fadeLength = _fadeRemaining = 0;
        }
    }

    /// <summary>멈춘 상태로 들어간다. 원인은 처음 멈춘 것만 남긴다.</summary>
    void ApplyPause(int cause)
    {
        _paused = true;
        if (_pauseApplied) return;
        _pauseApplied = true;
        _pauseCause = cause;
        _pauseAppliedSample = (long)Math.Floor(Position());
    }

    /// <summary>남은 PCM 이 선버퍼보다 짧아도 끝까지 내보내게 한다. response.done 에서 부른다.</summary>
    public void MarkComplete() { lock (_gate) _complete = true; }

    public void Reset()
    {
        lock (_gate)
        {
            _read = _write = _count = 0;
            _received = _consumed = 0;
            _running = _paused = _complete = false;
            _buffering = true;
            _primed = _terminated = _ended = _everRendered = false;
            _phase = 0;
            _prev = _next = 0f;
            _sourceBase = 0;
            _pauseTarget = _pauseAppliedSample = -1;
            _pauseCause = PauseNone;
            _pauseApplied = false;
            _fadeLength = _fadeRemaining = 0;
        }
    }

    public void ResetDiagnostics()
    {
        lock (_gate)
        {
            _callbacks = _renderedFrames = _silentFrames = 0;
            _preOutputCallbacks = _preOutputConsumed = 0;
            _underruns = _underrunFrames = _rebuffers = _configChanges = 0;
            _maxBuffered = 0;
            _minBufferedAtCallback = int.MaxValue;
            _callbackMin = _callbackMax = 0;
            _peak = 0f;
        }
    }

    public Diagnostics Snapshot()
    {
        lock (_gate)
        {
            return new Diagnostics
            {
                received = _received, consumed = _consumed,
                buffered = _count, maxBuffered = _maxBuffered,
                minBufferedAtCallback = _minBufferedAtCallback == int.MaxValue ? -1 : _minBufferedAtCallback,
                running = _running, paused = _paused, complete = _complete,
                buffering = _buffering, ended = _ended,
                callbacks = _callbacks, renderedFrames = _renderedFrames, silentFrames = _silentFrames,
                preOutputCallbacks = _preOutputCallbacks, preOutputConsumed = _preOutputConsumed,
                underruns = _underruns, underrunFrames = _underrunFrames,
                rebuffers = _rebuffers, configChanges = _configChanges,
                callbackMin = _callbackMin, callbackMax = _callbackMax,
                sourceRate = SourceRate, outputRate = _outputRate,
                prerollSamples = _prerollSamples, rebufferSamples = _rebufferSamples,
                peak = _peak,
            };
        }
    }

    float Pop()
    {
        float value = _ring[_read];
        _read = (_read + 1) % _ring.Length;
        _count--;
        _consumed++;
        if (!_everRendered) _preOutputConsumed++;
        return value;
    }

    /// <summary>다음 두 샘플로 넘어간다. 실패하면 상태를 전혀 바꾸지 않는다.</summary>
    int Advance()
    {
        if (_count > 0)
        {
            _prev = _next;
            _next = Pop();
            _phase -= 1.0;
            _sourceBase++;
            return Advanced;
        }
        if (_complete && !_terminated)
        {
            // 마지막 원본 샘플 뒤에 0 을 하나 붙여 0 으로 내려가며 끝낸다.
            _prev = _next;
            _next = 0f;
            _terminated = true;
            _phase -= 1.0;
            _sourceBase++;
            return Advanced;
        }
        if (_complete) return Finished;
        return Starved;
    }

    int Prime()
    {
        if (_count >= 2)
        {
            _prev = Pop();
            _next = Pop();
            _sourceBase = _consumed - 2;
        }
        else if (_complete && _count == 1)
        {
            _prev = Pop();
            _next = 0f;
            _terminated = true;
            _sourceBase = _consumed - 1;
        }
        else if (_complete)
        {
            return Finished;
        }
        else
        {
            return Starved;
        }
        _phase = 0;
        _primed = true;
        return Advanced;
    }

    void OnAudioFilterRead(float[] data, int channels)
    {
        if (channels <= 0) return;
        int frames = data.Length / channels;
        lock (_gate)
        {
            _callbacks++;
            if (_callbackMin == 0 || frames < _callbackMin) _callbackMin = frames;
            if (frames > _callbackMax) _callbackMax = frames;
            if (_count < _minBufferedAtCallback) _minBufferedAtCallback = _count;
            if (!_everRendered) _preOutputCallbacks++;

            if (!_running || _paused || _ended)
            {
                Array.Clear(data, 0, data.Length);
                _silentFrames += frames;
                return;
            }

            if (_buffering)
            {
                int need = _primed ? _rebufferSamples : _prerollSamples;
                // 완료 표시가 오면 선버퍼를 기다리지 않는다. 마지막 짧은 조각을 그대로 내보내고,
                // 남은 것이 없으면 Prime/Advance 의 Finished 경로로 끝낸다. 예전에는 _count > 0 을
                // 함께 요구해서, 공급이 끊겨 _count == 0 이 된 뒤 MarkComplete 가 오면 영원히
                // 재버퍼를 기다리며 종료(_ended)가 나지 않았다.
                if (_count >= need || _complete) _buffering = false;
                else
                {
                    Array.Clear(data, 0, data.Length);
                    _silentFrames += frames;
                    return;
                }
            }

            double step = (double)SourceRate / (_outputRate > 0 ? _outputRate : 48000);
            int rendered = 0;
            for (int frame = 0; frame < frames; frame++)
            {
                if (!_primed)
                {
                    int primed = Prime();
                    if (primed != Advanced) { Settle(data, frame, frames, channels, rendered, primed); return; }
                    _everRendered = true;
                }
                // 보간 구간을 벗어났으면 먼저 다음 샘플로 넘어간다. 실패하면 상태를 두고 멈춘다.
                bool blocked = false;
                while (_phase >= 1.0)
                {
                    int moved = Advance();
                    if (moved != Advanced)
                    {
                        Settle(data, frame, frames, channels, rendered, moved);
                        blocked = true;
                        break;
                    }
                }
                if (blocked) return;

                // 예약한 경계다. 다음 구절의 첫 표본이 _next 로 올라왔으므로 한 표본도
                // 내보내지 않고 여기서 멈춘다. 위상과 남은 PCM 은 그대로 둔다.
                if (_pauseTarget >= 0 && _sourceBase + 1 >= _pauseTarget)
                {
                    _pauseTarget = -1;
                    _fadeLength = _fadeRemaining = 0;
                    ApplyPause(PauseBoundary);
                    Hush(data, frame, frames, channels, rendered);
                    return;
                }

                float value = _prev + (_next - _prev) * (float)_phase;
                if (_fadeLength > 0) value *= (float)_fadeRemaining / _fadeLength;
                int at = frame * channels;
                for (int channel = 0; channel < channels; channel++) data[at + channel] = value;
                float magnitude = value < 0f ? -value : value;
                if (magnitude > _peak) _peak = magnitude;
                _everRendered = true;
                rendered++;
                _phase += step;
                if (_fadeLength > 0 && --_fadeRemaining <= 0)
                {
                    // 램프가 끝났다. 이 프레임까지 내보내고 멈춘 상태로 들어간다.
                    _fadeLength = _fadeRemaining = 0;
                    ApplyPause(PauseFade);
                    Hush(data, frame + 1, frames, channels, rendered);
                    return;
                }
            }
            _renderedFrames += rendered;
        }
    }

    /// <summary>멈춘 지점부터 블록 끝까지 0 으로 채운다. 의도한 정지이므로 언더런으로 세지 않고
    /// 재버퍼로 넘기지도 않는다. 재개하면 같은 상태에서 이어간다.</summary>
    void Hush(float[] data, int fromFrame, int frames, int channels, int rendered)
    {
        _renderedFrames += rendered;
        int at = fromFrame * channels;
        Array.Clear(data, at, data.Length - at);
        _silentFrames += frames - fromFrame;
    }

    /// <summary>남은 출력 구간을 0으로 채운다. 끝난 것과 공급이 끊긴 것을 구분해 센다.</summary>
    void Settle(float[] data, int fromFrame, int frames, int channels, int rendered, int outcome)
    {
        _renderedFrames += rendered;
        int at = fromFrame * channels;
        Array.Clear(data, at, data.Length - at);
        int missing = frames - fromFrame;
        _silentFrames += missing;
        if (outcome == Finished)
        {
            _ended = true;
            return;   // 정상 종료다. 공급 부족으로 세지 않는다.
        }
        _underruns++;
        _underrunFrames += missing;
        if (!_buffering) { _buffering = true; _rebuffers++; }
    }
}
