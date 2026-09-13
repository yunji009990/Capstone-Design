using System;
using System.Collections.Concurrent;
using System.IO;
using System.Net.WebSockets;
using System.Text;
using System.Threading;
using System.Threading.Tasks;

/// <summary>
/// One connection per experience. One sender and one receiver run concurrently;
/// Unity consumes their events on its main thread. Queues have explicit limits.
/// </summary>
public sealed class DialogueTransport : IDisposable
{
    public struct Incoming
    {
        public string Json;
        public string Error;
    }

    struct Packet
    {
        public byte[] Bytes;
        public WebSocketMessageType Type;
    }

    const int MaxQueuedBytes = 64000; // at most 2 seconds of microphone audio
    const int MaxIncomingEvents = 256;
    const int MaxMessageBytes = 65536;
    readonly ClientWebSocket _socket = new ClientWebSocket();
    readonly CancellationTokenSource _cancel = new CancellationTokenSource();
    readonly SemaphoreSlim _signal = new SemaphoreSlim(0);
    readonly ConcurrentQueue<Packet> _outgoing = new ConcurrentQueue<Packet>();
    readonly ConcurrentQueue<Incoming> _incoming = new ConcurrentQueue<Incoming>();
    readonly object _gate = new object();
    int _queuedBytes;
    int _incomingCount;
    bool _disposed;
    volatile bool _open;

    public bool IsOpen => _open;
    public Task Completion { get; }

    public DialogueTransport(Uri uri, string token, string startJson)
    {
        if (!string.IsNullOrEmpty(token)) _socket.Options.SetRequestHeader("X-Token", token);
        _socket.Options.KeepAliveInterval = TimeSpan.FromSeconds(5);
        Completion = RunAsync(uri, startJson);
    }

    public bool SendAudio(byte[] pcm) => Enqueue(pcm, WebSocketMessageType.Binary);
    public bool SendText(string json) => Enqueue(Encoding.UTF8.GetBytes(json), WebSocketMessageType.Text);

    bool Enqueue(byte[] data, WebSocketMessageType type)
    {
        lock (_gate)
        {
            if (_disposed || !_open) return false;
            if (Interlocked.Add(ref _queuedBytes, data.Length) > MaxQueuedBytes)
            {
                Interlocked.Add(ref _queuedBytes, -data.Length);
                return false;
            }
            _outgoing.Enqueue(new Packet { Bytes = data, Type = type });
            _signal.Release();
            return true;
        }
    }

    public bool TryReceive(out Incoming message)
    {
        if (!_incoming.TryDequeue(out message)) return false;
        Interlocked.Decrement(ref _incomingCount);
        return true;
    }

    void Receive(Incoming message)
    {
        if (Interlocked.Increment(ref _incomingCount) > MaxIncomingEvents)
            throw new IOException("Unity가 대화 결과를 처리하지 못해 연결을 중단했습니다.");
        _incoming.Enqueue(message);
    }

    async Task RunAsync(Uri uri, string startJson)
    {
        Task sender = null;
        Task receiver = null;
        try
        {
            using (var connectTimeout = CancellationTokenSource.CreateLinkedTokenSource(_cancel.Token))
            {
                connectTimeout.CancelAfter(TimeSpan.FromSeconds(10));
                await _socket.ConnectAsync(uri, connectTimeout.Token).ConfigureAwait(false);
            }
            var hello = Encoding.UTF8.GetBytes(startJson);
            await _socket.SendAsync(new ArraySegment<byte>(hello), WebSocketMessageType.Text,
                true, _cancel.Token).ConfigureAwait(false);
            _open = true;
            sender = SendLoopAsync();
            receiver = ReceiveLoopAsync();
            var finished = await Task.WhenAny(sender, receiver).ConfigureAwait(false);
            await finished.ConfigureAwait(false);
        }
        catch (Exception e)
        {
            if (!_cancel.IsCancellationRequested)
            {
                // Keep the error observable even when the incoming queue is full.
                _incoming.Enqueue(new Incoming { Error = "대화 연결이 끊겼습니다: " + e.Message });
                Interlocked.Increment(ref _incomingCount);
            }
        }
        finally
        {
            lock (_gate)
            {
                _open = false;
                _disposed = true;
                _cancel.Cancel();
                _socket.Abort();
            }
            if (sender != null) { try { await sender.ConfigureAwait(false); } catch { } }
            if (receiver != null) { try { await receiver.ConfigureAwait(false); } catch { } }
            lock (_gate)
            {
                _socket.Dispose();
                _cancel.Dispose();
                _signal.Dispose();
                while (_outgoing.TryDequeue(out _)) { }
            }
        }
    }

    async Task SendLoopAsync()
    {
        while (true)
        {
            await _signal.WaitAsync(_cancel.Token).ConfigureAwait(false);
            if (!_outgoing.TryDequeue(out var packet)) continue;
            await _socket.SendAsync(new ArraySegment<byte>(packet.Bytes), packet.Type,
                true, _cancel.Token).ConfigureAwait(false);
            Interlocked.Add(ref _queuedBytes, -packet.Bytes.Length);
        }
    }

    async Task ReceiveLoopAsync()
    {
        var buffer = new byte[8192];
        while (true)
        {
            using (var message = new MemoryStream())
            {
                WebSocketReceiveResult result;
                do
                {
                    result = await _socket.ReceiveAsync(new ArraySegment<byte>(buffer),
                        _cancel.Token).ConfigureAwait(false);
                    if (result.MessageType == WebSocketMessageType.Close)
                        throw new IOException("서버가 연결을 종료했습니다.");
                    if (result.MessageType != WebSocketMessageType.Text)
                        throw new IOException("서버의 대화 응답 형식이 올바르지 않습니다.");
                    if (message.Length + result.Count > MaxMessageBytes)
                        throw new IOException("서버의 대화 응답이 너무 큽니다.");
                    message.Write(buffer, 0, result.Count);
                } while (!result.EndOfMessage);
                Receive(new Incoming { Json = Encoding.UTF8.GetString(message.ToArray()) });
            }
        }
    }

    public void Dispose()
    {
        lock (_gate)
        {
            if (_disposed) return;
            _disposed = true;
            _open = false;
            _cancel.Cancel();
            _socket.Abort();
        }
    }
}
