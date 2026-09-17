// DialogueReplayMenu.cs
// 개발용. 마지막으로 끝까지 재생된 답변의 원본 PCM 을 두 경로로 다시 들어 본다.
//
//   Tools/Dialogue/Compare last reply/Stream renderer  : 실제 대화와 같은 경로
//     (DialogueAudioPlayer 에 24 kHz PCM 을 9600바이트 이하로 흘려 넣고 MarkComplete →
//      전량 소비 → TailSeconds 뒤 정지)
//   Tools/Dialogue/Compare last reply/AudioClip        : 비교용 경로
//     (stream:false 인 AudioClip 에 전체 PCM 을 SetData 하고 한 번에 재생)
//
// 두 메뉴는 같은 캐시 사본을 쓴다. 둘 다 잘리면 원본 PCM·공유 출력 경로·장치를 더 조사하고,
// 한쪽만 다르면 해당 재생 경로 또는 그 시점의 조건을 더 조사한다.
// 어느 쪽도 원인을 확정하지는 못한다.
//
// 소리는 사람이 메뉴를 고른 그때만 난다. 자동 재생은 없다.
// 씬의 AudioSource·마이크·인물·전역 볼륨을 건드리지 않고, 자기 GameObject 만 만들었다 지운다.
// 체험·연결·마이크·다른 비교 재생이 돌고 있으면 실행을 거절한다. 돌고 있는 것을 멈추지 않는다.

using System;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class DialogueReplayMenu
{
    const int Rate = DialogueReplayCache.SourceRate;   // 24000
    const int MaxPacketBytes = 9600;                   // Enqueue 상한
    const int PacketMillis = 200;
    const int LeadMillis = 400;                        // 실제 수신처럼 재생보다 조금 앞서 공급한다

    static bool _running, _stopRequested;
    static GameObject _host;
    // Guard 에서 한 번 잡아 둔다. 재생 루프에서 매번 찾지 않고 이 참조의 상태만 가볍게 본다.
    static DialogueVoiceClient _voice;

    /// <summary>완료본 10분 TTL 을 에디터 시계로 실제로 청소한다. Play 를 일시정지해 두거나
    /// 끝낸 뒤에도 돈다. 완료본이 없으면 Expire 는 즉시 돌아간다.</summary>
    static DialogueReplayMenu()
    {
        EditorApplication.update += DialogueReplayCache.Expire;
    }

    [MenuItem("Tools/Dialogue/Compare last reply/Stream renderer", priority = 24)]
    public static void ReplayStream() => Replay(true);

    [MenuItem("Tools/Dialogue/Compare last reply/AudioClip", priority = 25)]
    public static void ReplayClip() => Replay(false);

    [MenuItem("Tools/Dialogue/Compare last reply/Stop replay", priority = 26)]
    public static void StopReplay()
    {
        if (!_running) { Debug.LogWarning("[Replay] 재생 중이 아닙니다."); return; }
        _stopRequested = true;
    }

    static async void Replay(bool stream)
    {
        if (!Guard(out DialogueVoiceClient voice)) return;
        if (!DialogueReplayCache.TryGetLast(out byte[] pcm, out int turn, out string reason))
        { Debug.LogWarning("[Replay] " + reason); return; }
        _voice = voice;   // 재생하는 동안만 잡아 둔다. finally 에서 놓는다.

        int samples = pcm.Length / 2;
        float expectedSeconds = samples / (float)Rate;
        // 남기는 것은 turn 번호와 길이, 사본 세대, 경로 종류뿐이다.
        // 대사·response_id·session 은 쓰지 않는다. gen 이 같으면 두 경로가 같은 사본을 들은 것이다.
        Debug.Log("[Replay] turn=" + turn + " gen=" + DialogueReplayCache.ReadyGeneration +
                  " samples=" + samples + " seconds=" + expectedSeconds.ToString("F2") +
                  " path=" + (stream ? "stream-renderer" : "audioclip"));

        _running = true;
        _stopRequested = false;
        DialogueAudioPlayer player = null;
        AudioClip clip = null;
        float[] data = null;
        try
        {
            _host = new GameObject(stream ? "Dialogue Replay (stream)" : "Dialogue Replay (clip)")
            { hideFlags = HideFlags.DontSave };
            var source = _host.AddComponent<AudioSource>();
            source.playOnAwake = false;
            source.spatialBlend = 0f;

            if (stream)
            {
                player = new DialogueAudioPlayer(source);
                await FeedStream(player, pcm);
            }
            else
            {
                clip = AudioClip.Create("dialogue-replay", samples, 1, Rate, false);
                if (clip == null) throw new Exception("AudioClip 을 만들지 못했습니다.");
                data = new float[samples];
                for (int i = 0; i < samples; i++)
                    data[i] = (short)(pcm[i * 2] | (pcm[i * 2 + 1] << 8)) / 32768f;
                // 실패를 정상 종료로 적지 않는다. 넣기·시작이 안 되면 그 자리에서 실패다.
                if (!clip.SetData(data, 0)) throw new Exception("AudioClip 에 PCM 을 넣지 못했습니다(SetData 실패).");
                source.clip = clip;
                source.Play();
                float startedAt = Time.realtimeSinceStartup;
                while (!source.isPlaying && Time.realtimeSinceStartup - startedAt < .2f)
                { RequireRunning(); await Task.Delay(10); }
                if (!source.isPlaying) throw new Exception("AudioClip 재생이 시작되지 않았습니다.");

                float until = startedAt + expectedSeconds + .5f;
                while (Time.realtimeSinceStartup < until && source.isPlaying)
                {
                    RequireRunning();
                    await Task.Delay(20);
                }
                float played = Time.realtimeSinceStartup - startedAt;
                // 기한이 지났는데도 아직 재생 중이면 끝난 것이 아니다. 성공으로 적지 않는다.
                if (source.isPlaying)
                {
                    source.Stop();
                    throw new Exception("예상 시간(" + expectedSeconds.ToString("F2") +
                                        "초)이 지나도 재생이 끝나지 않았습니다.");
                }
                // 자연 종료 뒤에도 DSP 버퍼에 아직 남아 있다. 기존 재생기와 같은 여유를 두고
                // 다 나간 뒤에 자기 오브젝트를 지운다(정리는 finally 가 한다).
                await Drain();
                // 예상 길이에 한참 못 미치면 완료로 단정하지 않는다. 그 자체가 관찰 결과다.
                if (played < expectedSeconds - .25f)
                {
                    Debug.LogWarning("[Replay] 재생이 예상보다 일찍 끝났습니다(완료 아님) turn=" + turn +
                                     " played=" + played.ToString("F2") +
                                     " expected=" + expectedSeconds.ToString("F2") + " path=audioclip");
                    return;
                }
            }
            Debug.Log("[Replay] 재생 종료 turn=" + turn + " path=" + (stream ? "stream-renderer" : "audioclip"));
        }
        catch (Exception e)
        {
            Debug.LogWarning("[Replay] 재생을 끝내지 못했습니다: " + e.Message);
        }
        finally
        {
            // 자기 것만 지운다. 실패하든 중단되든 여기를 반드시 지난다.
            try
            {
                if (player != null) player.Dispose();
                if (clip != null) UnityEngine.Object.DestroyImmediate(clip);
                if (_host != null) UnityEngine.Object.DestroyImmediate(_host);
            }
            catch (Exception e) { Debug.LogWarning("[Replay] 정리 중 오류: " + e.Message); }
            // 완료·중단·실패 어느 쪽이든 손에 쥔 음성 사본을 지우고 놓는다.
            if (pcm != null) Array.Clear(pcm, 0, pcm.Length);
            if (data != null) Array.Clear(data, 0, data.Length);
            _host = null;
            _voice = null;
            _running = false;
            _stopRequested = false;
        }
    }

    /// <summary>실제 대화와 같은 방식으로 흘려 넣는다. 바이트는 그대로 두고 나누기만 한다.</summary>
    static async Task FeedStream(DialogueAudioPlayer player, byte[] pcm)
    {
        int packetBytes = Mathf.Clamp(Rate * PacketMillis / 1000 * 2, 2, MaxPacketBytes);
        float start = Time.realtimeSinceStartup;
        for (int offset = 0; offset < pcm.Length; offset += packetBytes)
        {
            int bytes = Math.Min(packetBytes, pcm.Length - offset);
            float due = start + Math.Max(0f, offset / 2f / Rate - LeadMillis / 1000f);
            while (Time.realtimeSinceStartup < due) { RequireRunning(); await Task.Delay(2); }
            RequireRunning();
            var packet = new byte[bytes];
            Buffer.BlockCopy(pcm, offset, packet, 0, bytes);
            player.Enqueue(packet);
        }
        player.MarkComplete();

        // 대화 클라이언트와 같은 종료 판정이다. 전량 소비한 뒤 TailSeconds 를 더 기다린다.
        float deadline = start + pcm.Length / 2f / Rate + player.TailSeconds + 15f;
        while (player.Consumed < player.Received)
        {
            RequireRunning();
            if (Time.realtimeSinceStartup > deadline) throw new Exception("소비가 끝나지 않았습니다.");
            await Task.Delay(5);
        }
        float until = Time.realtimeSinceStartup + player.TailSeconds;
        while (Time.realtimeSinceStartup < until) { RequireRunning(); await Task.Delay(5); }
        player.Stop();
    }

    /// <summary>DSP 버퍼에 남은 것이 다 나갈 때까지 기다린다. 기존 DialogueAudioPlayer 의
    /// TailSeconds 와 같은 계산이다(최소 .3초, 또는 DSP 전체 버퍼 + .1초).
    /// 중단 요청과 새 체험은 다른 재생 루프와 같은 방식으로 처리한다.</summary>
    static async Task Drain()
    {
        AudioSettings.GetDSPBufferSize(out int size, out int buffers);
        int rate = AudioSettings.outputSampleRate > 0 ? AudioSettings.outputSampleRate : 48000;
        float seconds = Mathf.Max(.3f, (float)(size * buffers) / rate + .1f);
        float until = Time.realtimeSinceStartup + seconds;
        while (Time.realtimeSinceStartup < until)
        {
            RequireRunning();
            await Task.Delay(10);
        }
    }

    /// <summary>체험·연결·마이크·답변 재생·보류·다른 비교 재생 중이면 거절한다.
    /// 진행 중인 것은 절대 멈추지 않는다. 찾은 클라이언트는 재생 중 감시용으로 잡아 둔다.</summary>
    static bool Guard(out DialogueVoiceClient voice)
    {
        voice = null;
        if (!EditorApplication.isPlaying)
        { Debug.LogWarning("[Replay] Play Mode 에서 실행하세요."); return false; }
        if (_running)
        { Debug.LogWarning("[Replay] 이미 비교 재생 중입니다. 끝나거나 Stop replay 뒤에 다시 하세요."); return false; }

        voice = UnityEngine.Object.FindObjectOfType<DialogueVoiceClient>();
        if (voice == null)
        {
            // 감시할 대상이 없으면 재생 중 체험이 시작돼도 멈출 수 없다. 그 상태로는 켜지 않는다.
            Debug.LogWarning("[Replay] 씬에서 DialogueVoiceClient 를 찾지 못했습니다. " +
                             "Scene_2 를 Play 한 상태에서 실행하세요.");
            return false;
        }
        if (Busy(voice))
        { Debug.LogWarning("[Replay] 체험·답변이 진행 중입니다. 끝난 뒤 실행하세요."); return false; }
        foreach (string device in Microphone.devices)
            if (Microphone.IsRecording(device))
            { Debug.LogWarning("[Replay] 마이크 녹음이 진행 중입니다."); return false; }
        return true;
    }

    static bool Busy(DialogueVoiceClient voice) => voice != null &&
        (voice.ExperienceActive || voice.DialogueConnected || voice.IsListening ||
         voice.IsSpeaking || voice.ResponsePaused);

    /// <summary>Play 가 끝났거나, 사용자가 멈췄거나, 체험이 시작됐으면 즉시 빠져나온다.
    /// 잡아 둔 참조의 상태만 읽는다. 정리는 finally 가 자기 것만 한다.</summary>
    static void RequireRunning()
    {
        if (!EditorApplication.isPlaying) throw new Exception("Play Mode 가 끝났습니다.");
        if (_stopRequested) throw new Exception("사용자가 재생을 멈췄습니다.");
        // 클라이언트가 사라졌으면(씬 전환·파기) 진단 재생만 끝낸다.
        if (_voice == null) throw new Exception("대화 클라이언트가 사라져 진단 재생을 멈췄습니다.");
        // 체험이 시작되면 진단용 소리만 즉시 멈춘다. 사용자의 체험에는 손대지 않는다.
        if (Busy(_voice)) throw new Exception("체험이 시작돼 진단 재생을 멈췄습니다.");
    }
}
