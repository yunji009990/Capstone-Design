// DialogueReplayCache.cs
// 개발용. 마지막으로 "끝까지 재생된" 답변의 원본 PCM 을 메모리에만 잠시 들고 있는다.
// 같은 문장을 다시 합성하면 확률적으로 다른 음성이 나오므로, 말끝 증상을 다시 들으려면
// 그때 실제로 받은 바이트 그대로가 필요하다. 재생 메뉴는 Assets/Editor/DialogueReplayMenu.cs 다.
//
// #if UNITY_EDITOR 로만 컴파일된다. 플레이어 빌드에는 이 클래스도 호출부도 남지 않는다.
//
// 지키는 것:
// - 파일·로그에 음성·텍스트·response_id·session 을 남기지 않는다. 밖으로 내는 것은 turn 번호와 길이뿐이다.
// - 받은 byte[] 를 변형 없이 그대로 모은다. 리샘플·정규화·잘라내기를 하지 않는다.
// - 미완성·취소·초기화·넘침 상태의 PCM 은 완료본으로 절대 노출하지 않는다.
// - 새 응답이 시작되면 이전 완료본을 그 자리에서 버린다. 취소·넘침 뒤에 예전 답변이
//   "마지막 것"으로 둔갑해 재생되는 일이 없어야 한다.
// - 받는 중(pending)에는 조회를 거절한다. 조회는 완료본이 확정된 뒤에만 된다.
// - 참조가 아니라 복사본을 준다. 받아 간 쪽이 만져도 캐시는 그대로다.
// - 다 쓴 raw 바이트 구간은 Array.Clear 로 지우고 참조를 놓는다. 10분 TTL 은 조회할 때가
//   아니라 Expire() 로 실제로 지운다. Expire 는 DialogueReplayMenu 의 [InitializeOnLoad] 가
//   EditorApplication.update 에 걸어 두고 부른다. 그래서 Play 를 일시정지해 둬도, Play 를
//   끝낸 뒤에도 10분이 지나면 지워진다.
// - 시계는 Time.realtimeSinceStartup 이 아니라 EditorApplication.timeSinceStartup 이다.
//   Play 일시정지 중에도 흐르고, 에디터가 켜져 있는 동안 계속 흐른다.
// - 여기서 난 예외를 대화 쪽으로 올리지 않는다. 실패하면 스스로 꺼진다.

#if UNITY_EDITOR
using System;
using UnityEditor;

public static class DialogueReplayCache
{
    public const int SourceRate = 24000;              // PCM16 mono 24 kHz
    public const int MaxSeconds = 60;
    const int MaxBytes = SourceRate * 2 * MaxSeconds; // 약 2.88 MB
    const double TtlSeconds = 600.0;                  // 완료 후 10분

    static byte[] _pending;          // 수신 중인 응답. Begin 에서 잡고 끝나면 지우고 놓는다
    static int _pendingBytes, _pendingTurn = -1;
    static bool _open;               // 지금 모으는 중인가
    static byte[] _ready;            // 완료된 응답 사본. 실제 길이만큼만 잡는다
    static int _readyTurn = -1, _readyGeneration;
    // 에디터 시계다(Play 일시정지 중에도 흐른다). 메인의 리플렉션 검사는 double 로 읽고 쓴다.
    static double _readyAt;
    static bool _disabled;           // 한 번이라도 실패하면 조용히 꺼진다

    /// <summary>받는 중이면 false 다. 완료본이 확정된 뒤에만 true 가 된다.</summary>
    public static bool HasReady { get { Expire(); return !_disabled && !_open && _ready != null; } }
    public static int ReadyTurn => HasReady ? _readyTurn : -1;
    public static int ReadySamples => HasReady ? _ready.Length / 2 : 0;
    public static float ReadySeconds => ReadySamples / (float)SourceRate;
    /// <summary>완료본이 바뀔 때마다 1 씩 는다. 두 메뉴가 같은 사본을 본 것인지 대조할 때 쓴다.</summary>
    public static int ReadyGeneration => _readyGeneration;

    /// <summary>새 응답이 시작됐다. 이전 완료본과 미완성분을 여기서 모두 버린다.</summary>
    public static void Begin(int turn)
    {
        if (_disabled) return;
        try
        {
            DropReady();      // 취소·넘침으로 이번 응답이 완료되지 못해도 예전 것이 남지 않는다
            WipePending();
            _pending = new byte[MaxBytes];
            _pendingTurn = turn;
            _open = true;
        }
        catch (Exception) { Disable(); }
    }

    /// <summary>재생기에 넣는 데 성공한 바이트만, 온 그대로 이어 붙인다.</summary>
    public static void Append(byte[] pcm)
    {
        if (_disabled || !_open || pcm == null || pcm.Length == 0) return;
        try
        {
            if (_pending == null || _pendingBytes + pcm.Length > MaxBytes)
            {
                // 60초를 넘겼다. 앞부분만 남은 토막을 완료본으로 둔갑시키지 않는다.
                _open = false;
                WipePending();
                return;
            }
            Buffer.BlockCopy(pcm, 0, _pending, _pendingBytes, pcm.Length);
            _pendingBytes += pcm.Length;
        }
        catch (Exception) { Disable(); }
    }

    /// <summary>끝까지 재생된 응답이다. 이 시점의 것만 완료본이 된다.</summary>
    public static void Complete(int turn)
    {
        if (_disabled) return;
        try
        {
            if (!_open || _pending == null || _pendingBytes <= 0 || turn != _pendingTurn)
            { DiscardIncomplete(); return; }
            var ready = new byte[_pendingBytes];
            Buffer.BlockCopy(_pending, 0, ready, 0, _pendingBytes);
            DropReady();
            _ready = ready;
            _readyTurn = turn;
            _readyAt = EditorApplication.timeSinceStartup;
            _readyGeneration++;
            _open = false;
            WipePending();     // 복사한 뒤 원본 구간을 지우고 놓는다
        }
        catch (Exception) { Disable(); }
    }

    /// <summary>모으던 것을 버린다. 완료본은 그대로 둔다(체험을 끝낸 뒤에도 들어야 한다).</summary>
    public static void DiscardIncomplete()
    {
        if (_disabled) return;
        _open = false;
        WipePending();
        _pendingTurn = -1;
    }

    /// <summary>완료본까지 모두 버린다. 체험 시작·초기화·비활성·파기에서 부른다.</summary>
    public static void Clear()
    {
        _open = false;
        _pendingTurn = -1;
        WipePending();
        DropReady();
    }

    /// <summary>10분이 지난 완료본을 실제로 메모리에서 지운다. 조회가 없어도 지워져야 한다.
    /// DialogueReplayMenu 가 EditorApplication.update 에 걸어 두고 매 틱 부른다.
    /// 완료본이 없으면 바로 돌아가므로 그 경우 비용은 참조 비교 한 번뿐이다.</summary>
    public static void Expire()
    {
        if (_disabled || _ready == null) return;
        if (EditorApplication.timeSinceStartup - _readyAt > TtlSeconds) DropReady();
    }

    /// <summary>완료본 복사본을 준다. 받는 중이거나 만료됐으면 거절한다.</summary>
    public static bool TryGetLast(out byte[] pcm, out int turn, out string reason)
    {
        pcm = null;
        turn = -1;
        Expire();
        if (_disabled) { reason = "진단 캐시가 꺼져 있습니다."; return false; }
        if (_open) { reason = "답변을 받는 중입니다. 끝난 뒤에 실행하세요."; return false; }
        if (_ready == null) { reason = "끝까지 재생된 답변이 없습니다(취소·초기화·60초 초과·10분 경과 포함)."; return false; }
        try
        {
            var copy = new byte[_ready.Length];
            Buffer.BlockCopy(_ready, 0, copy, 0, _ready.Length);
            pcm = copy;
            turn = _readyTurn;
            reason = "";
            return true;
        }
        catch (Exception)
        {
            Disable();
            reason = "진단 캐시 복사에 실패했습니다.";
            return false;
        }
    }

    static void WipePending()
    {
        if (_pending != null) Array.Clear(_pending, 0, _pending.Length);
        _pending = null;
        _pendingBytes = 0;
    }

    static void DropReady()
    {
        if (_ready != null) Array.Clear(_ready, 0, _ready.Length);
        _ready = null;
        _readyTurn = -1;
        _readyAt = 0.0;
    }

    static void Disable()
    {
        _disabled = true;
        Clear();
    }
}
#endif
