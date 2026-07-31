using System;
using UnityEngine;
using UnityEngine.Networking;

/// <summary>
/// /talk_stream이 흘려보내는 16bit PCM을 받아 링 버퍼에 쌓습니다.
/// ReceiveData는 메인 스레드에서, Read는 오디오 스레드에서 호출되므로 락으로 보호합니다.
/// </summary>
public class RaonPcmStream : DownloadHandlerScript
{
    readonly object _lock = new object();
    readonly float[] _ring;
    int _read;
    int _write;
    int _count;

    byte _oddByte;
    bool _hasOdd;

    /// <summary>서버가 전송을 끝냈는지. 버퍼가 비어도 이게 false면 아직 더 올 수 있습니다.</summary>
    public volatile bool Complete;

    /// <summary>지금까지 받은 총 샘플 수.</summary>
    public long TotalSamples { get; private set; }

    /// <summary>재생 중 데이터가 모자라 무음으로 때운 횟수와 샘플 수.</summary>
    public int Underruns { get; private set; }
    public long UnderrunSamples { get; private set; }

    public int Available { get { lock (_lock) { return _count; } } }

    // 수신 버퍼가 작으면 큰 청크를 잘게 나눠 받으면서 프레임 경계마다 지연이 쌓인다.
    // 문장 하나가 100KB를 넘으므로 넉넉하게 잡는다.
    const int ReceiveBufferBytes = 256 * 1024;

    public RaonPcmStream(int capacitySamples) : base(new byte[ReceiveBufferBytes])
    {
        _ring = new float[capacitySamples];
    }

    protected override bool ReceiveData(byte[] data, int dataLength)
    {
        if (data == null || dataLength <= 0) return true;

        lock (_lock)
        {
            int i = 0;
            if (_hasOdd)
            {
                Write((short)(_oddByte | (data[0] << 8)));
                _hasOdd = false;
                i = 1;
            }
            for (; i + 1 < dataLength; i += 2)
                Write((short)(data[i] | (data[i + 1] << 8)));

            if (i < dataLength)
            {
                _oddByte = data[i];
                _hasOdd = true;
            }
        }
        return true;
    }

    protected override void CompleteContent() => Complete = true;

    // 락을 잡은 상태에서만 호출할 것
    void Write(short sample)
    {
        _ring[_write] = sample / 32768f;
        _write = (_write + 1) % _ring.Length;
        if (_count < _ring.Length) _count++;
        else _read = (_read + 1) % _ring.Length;   // 넘치면 가장 오래된 것을 버린다
        TotalSamples++;
    }

    /// <summary>오디오 스레드에서 호출됩니다. 채운 샘플 수를 반환합니다.</summary>
    public int Read(float[] dest, int count)
    {
        lock (_lock)
        {
            int n = count < _count ? count : _count;
            for (int i = 0; i < n; i++)
            {
                dest[i] = _ring[_read];
                _read = (_read + 1) % _ring.Length;
            }
            _count -= n;

            // 전송이 끝나기 전에 모자란 것만 언더런으로 센다(끝난 뒤의 무음은 정상)
            if (n < count && !Complete)
            {
                Underruns++;
                UnderrunSamples += count - n;
            }
            return n;
        }
    }
}
