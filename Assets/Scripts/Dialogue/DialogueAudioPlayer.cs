using System;
using UnityEngine;

/// <summary>Bounded PCM16 playback. Audio callbacks never access the websocket.</summary>
public sealed class DialogueAudioPlayer : IDisposable
{
    const int Rate = 24000;
    readonly object _gate = new object();
    readonly float[] _ring = new float[Rate * 45];
    readonly AudioSource _source;
    AudioClip _clip;
    int _read, _write, _count;
    long _consumed, _received;
    bool _started;
    public bool Paused { get; private set; }
    public bool Playing => _started && !Paused;
    public long Consumed { get { lock (_gate) return _consumed; } }
    public long Received { get { lock (_gate) return _received; } }
    public float TailSeconds { get; }

    public DialogueAudioPlayer(AudioSource source)
    {
        _source = source;
        _source.playOnAwake = false;
        _source.spatialBlend = 0;
        AudioSettings.GetDSPBufferSize(out int size, out int buffers);
        TailSeconds = Mathf.Max(.3f, (float)(size * buffers) / AudioSettings.outputSampleRate + .1f);
    }

    public void Enqueue(byte[] pcm)
    {
        if (pcm == null || pcm.Length == 0 || pcm.Length % 2 != 0 || pcm.Length > 9600)
            throw new ArgumentException("잘못된 PCM 음성 패킷입니다.");
        lock (_gate)
        {
            if (_count + pcm.Length / 2 > _ring.Length)
                throw new InvalidOperationException("음성 재생 버퍼가 가득 찼습니다.");
            for (int i = 0; i < pcm.Length; i += 2)
            {
                _ring[_write] = (short)(pcm[i] | (pcm[i + 1] << 8)) / 32768f;
                _write = (_write + 1) % _ring.Length;
                _count++;
            }
            _received += pcm.Length / 2;
        }
        if (!_started && !Paused)
            StartPlayback();
    }

    void StartPlayback()
    {
        if (!_started)
        {
            if (_clip == null) _clip = AudioClip.Create("Dialogue PCM", Rate, 1, Rate, true, Read);
            _source.clip = _clip;
            _source.loop = true;
            _source.Play();
            _started = true;
        }
    }

    void Read(float[] data)
    {
        lock (_gate)
        {
            int take = Math.Min(data.Length, _count);
            for (int i = 0; i < take; i++)
            {
                data[i] = _ring[_read];
                _read = (_read + 1) % _ring.Length;
            }
            Array.Clear(data, take, data.Length - take);
            _count -= take;
            _consumed += take;
        }
    }

    public void Stop()
    {
        _source.Stop();
        _started = Paused = false;
        lock (_gate)
        {
            _read = _write = _count = 0;
            _consumed = _received = 0;
        }
    }

    public void Pause()
    {
        if (Paused) return;
        Paused = true;
        if (_started) _source.Pause();
    }

    public void Resume()
    {
        if (!Paused) return;
        Paused = false;
        if (_started) _source.UnPause();
        else
        {
            bool queued;
            lock (_gate) queued = _count > 0;
            if (queued) StartPlayback();
        }
    }

    public void Dispose()
    {
        Stop();
        if (_clip != null) UnityEngine.Object.Destroy(_clip);
    }
}
