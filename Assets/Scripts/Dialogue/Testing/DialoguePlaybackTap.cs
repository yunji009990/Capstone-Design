// DialoguePlaybackTap.cs
// 진단 전용 출력 탭. 렌더러 뒤에 붙어 실제로 출력 버퍼에 나간 파형을 센다.
// 실행 메뉴는 Assets/Editor/DialoguePlaybackRunner.cs 에 있다.
//
// Editor 폴더에 두면 AddComponent 가 "it is an editor script" 로 거부된다(실측).
// 그래서 런타임 폴더에 두고 #if UNITY_EDITOR 로 플레이어 빌드에서는 제외한다.
//
// 소리를 죽이는 일도 여기서 한다. AudioSource.mute 로 죽였더니 렌더러의 PCM 이 탭에
// 닿기 전에 0 이 되어 탭이 무음만 봤다(quiet-fast.json 실측: 렌더러는 received=consumed=120000,
// renderedFrames=240000, ended=true, 언더런 0 이었는데 탭은 0 만 봤다).
// 그래서 mute 를 쓰지 않고, 탭이 먼저 원본 PCM 을 센 다음 출력 버퍼를 통째로 0 으로 지운다.
// 세는 값은 지우기 전의 PCM 이고, 뒤로 나가는 것은 무음이다.
//
// 오디오 스레드에서 불린다. 카운터만 만지고 로그·파일·Unity API·할당을 쓰지 않는다.

#if UNITY_EDITOR
using System;
using UnityEngine;

public sealed class DialoguePlaybackTap : MonoBehaviour
{
    readonly object _gate = new object();
    long _callbacks, _frames, _silentFrames, _signalFrames;
    long _longestSilentRun, _currentSilentRun, _longestSignalRun, _currentSignalRun;
    long _longestInnerSilence, _firstSignalFrame = -1, _lastSignalFrame = -1;
    long _suppressedCallbacks, _suppressedFrames;
    float _peak, _postPeak;
    int _channels;

    // 선택적 bounded capture. 기본은 꺼져 있고, 켜 두면 지우기 전의 출력 파형을
    // 프레임 단위로 그대로 남긴다(DialogueSpeechTailValidation 이 파형을 대조할 때 쓴다).
    // 배열은 메인 스레드가 미리 배정해 넘긴다. 오디오 스레드는 채우기만 하고 할당하지 않으며,
    // 배열이 모자라면 덮어쓰지 않고 넘침만 표시한다(검사는 그 회차를 실패로 본다).
    float[] _capture;
    long _captureFrames;
    bool _captureOverflow;

    // 오디오 스레드가 읽고 메인 스레드가 쓴다. 기본은 소리를 내지 않는 쪽이다.
    // 잠금 없이 읽어도 최신 값을 보도록 volatile 로 둔다.
    volatile bool _suppress = true;

    /// <summary>true 면 센 뒤 출력 버퍼를 0 으로 지운다. 뒤로는 무음만 나간다.</summary>
    public bool SuppressOutput
    {
        get { return _suppress; }
        set { _suppress = value; }
    }

    /// <summary>메인 스레드에서 미리 배정한 배열로 파형 기록을 켠다. 재생을 시작하기 전에 부른다.
    /// null 을 주면 기록을 끈다. 기록하는 값은 출력 버퍼를 지우기 전의 채널 0 PCM 이다.</summary>
    public void BeginCapture(float[] buffer)
    {
        lock (_gate)
        {
            _capture = buffer;
            _captureFrames = 0;
            _captureOverflow = false;
        }
    }

    /// <summary>메인 스레드에서 잠그고 복사한다. 복사한 프레임 수를 돌려준다.</summary>
    public long CopyCapture(float[] destination)
    {
        if (destination == null) return 0;
        lock (_gate)
        {
            if (_capture == null) return 0;
            long count = Math.Min(_captureFrames, destination.Length);
            if (count > 0) Array.Copy(_capture, 0, destination, 0, (int)count);
            return count;
        }
    }

    [Serializable]
    public struct Output
    {
        public long callbacks, frames, silentFrames, signalFrames;
        public long longestSilentRunFrames, longestSignalRunFrames;
        // 시작 대기와 끝난 뒤의 무음을 뺀, 신호와 신호 사이의 연속 무음 최장 구간이다.
        public long longestInnerSilenceFrames;
        public long firstSignalFrame, lastSignalFrame;
        // peak 는 지우기 전 원본 PCM 의 최대 진폭이다. 아래 값들은 지운 뒤를 증명한다.
        public float peak, postPeak;
        public bool suppressOutput;
        public long suppressedCallbacks, suppressedFrames;
        public int channels;
        // 파형 기록을 켠 회차에서만 의미가 있다. overflow 면 뒤쪽 파형을 잃은 것이다.
        public bool captureEnabled, captureOverflow;
        public long captureFrames, captureCapacityFrames;
    }

    void OnAudioFilterRead(float[] data, int channels)
    {
        bool suppress = _suppress;
        if (channels <= 0)
        {
            // 셀 수는 없지만 소리는 반드시 막는다.
            if (suppress && data != null) Array.Clear(data, 0, data.Length);
            return;
        }
        lock (_gate)
        {
            _channels = channels;
            _callbacks++;
            int frames = data.Length / channels;
            for (int frame = 0; frame < frames; frame++)
            {
                float peak = 0f;
                int at = frame * channels;
                // 지우기 전의 채널 0 을 그대로 남긴다. 무음 콜백도 빠짐없이 기록해야
                // 프레임 번호가 실제 출력 시각과 어긋나지 않는다.
                if (_capture != null)
                {
                    if (_captureFrames < _capture.Length) _capture[_captureFrames++] = data[at];
                    else _captureOverflow = true;
                }
                for (int channel = 0; channel < channels; channel++)
                {
                    float value = data[at + channel];
                    if (value < 0f) value = -value;
                    if (value > peak) peak = value;
                }
                if (peak > _peak) _peak = peak;
                long position = _frames + frame;
                if (peak <= 1e-6f)
                {
                    _silentFrames++;
                    _currentSilentRun++;
                    if (_currentSilentRun > _longestSilentRun) _longestSilentRun = _currentSilentRun;
                    _currentSignalRun = 0;
                }
                else
                {
                    _signalFrames++;
                    // 첫 신호 전의 대기 무음은 내부 끊김이 아니다. 신호를 다시 만났을 때만 센다.
                    if (_firstSignalFrame >= 0 && _currentSilentRun > _longestInnerSilence)
                        _longestInnerSilence = _currentSilentRun;
                    if (_firstSignalFrame < 0) _firstSignalFrame = position;
                    _lastSignalFrame = position;
                    _currentSignalRun++;
                    if (_currentSignalRun > _longestSignalRun) _longestSignalRun = _currentSignalRun;
                    _currentSilentRun = 0;
                }
            }
            _frames += frames;

            // 다 센 뒤에 지운다. 이 뒤로는 어떤 필터도 스피커도 원본을 보지 못한다.
            if (suppress)
            {
                Array.Clear(data, 0, data.Length);
                _suppressedCallbacks++;
                _suppressedFrames += frames;
            }
            // 지운 결과를 그대로 다시 재서 남긴다. 억제 중이라면 0 이어야 한다.
            for (int i = 0; i < data.Length; i++)
            {
                float value = data[i];
                if (value < 0f) value = -value;
                if (value > _postPeak) _postPeak = value;
            }
        }
    }

    public Output Snapshot()
    {
        lock (_gate)
        {
            return new Output
            {
                callbacks = _callbacks,
                frames = _frames,
                silentFrames = _silentFrames,
                signalFrames = _signalFrames,
                longestSilentRunFrames = _longestSilentRun,
                longestSignalRunFrames = _longestSignalRun,
                longestInnerSilenceFrames = _longestInnerSilence,
                firstSignalFrame = _firstSignalFrame,
                lastSignalFrame = _lastSignalFrame,
                peak = _peak,
                postPeak = _postPeak,
                suppressOutput = _suppress,
                suppressedCallbacks = _suppressedCallbacks,
                suppressedFrames = _suppressedFrames,
                channels = _channels,
                captureEnabled = _capture != null,
                captureOverflow = _captureOverflow,
                captureFrames = _captureFrames,
                captureCapacityFrames = _capture != null ? _capture.Length : 0,
            };
        }
    }
}
#endif
