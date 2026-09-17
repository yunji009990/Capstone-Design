// DialogueBufferedPlaybackValidation.cs
// 전체 수신 재생(ReceiveWholeResponse=true) 계약을 Play 모드에서 결정적으로 검증한다.
//
// - 생산 코드를 그대로 돌린다. 재생은 Unity 네이티브 AudioSource 가 만든다.
// - 검사용 GameObject/AudioSource 는 HideFlags.DontSave 이고 mute=true 라 항상 무음이다.
//   씬의 DialogueVoiceClient, 마이크, 다른 오디오 소스, 대화 서버는 건드리지 않는다.
// - 기대값은 원본 PCM 에서 여기서 다시 계산한다. 생산 Snapshot 으로 기대값을 만들지 않는다.
// - 전체 수신 모드에서 Snapshot().renderedFrames/callbacks 는 -1(미측정)이다.
//   이 값은 "출력이 정상이었다"는 근거가 아니며 실제 청취 판정도 아니다.

using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class DialogueBufferedPlaybackValidation
{
    const string OutputPath = "tools/_work/unity_tail_20260917_latest/buffered-playback-validation.json";
    const int Rate = 24000;
    const int Packet = 4800;            // 9600 바이트. 서버 패킷 상한과 같다
    const int MaxPacketBytes = 9600;
    const long CapSamples = 45L * Rate; // 45초 상한
    const float Tolerance = 1e-4f;
    const BindingFlags Flags = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;

    [Serializable]
    class CaseResult
    {
        public string name = "";
        public bool passed;
        public List<string> notes = new List<string>();   // 실패 사유만 남긴다
        public List<string> actual = new List<string>();  // 실제 측정 숫자
    }

    [Serializable]
    class Report
    {
        public bool passed, aborted;
        public string time = "", unityVersion = "", error = "";
        public string scope =
            "Play 모드에서 생산 DialogueAudioPlayer(전체 수신 모드)와 실제 DialogueVoiceClient 처리기를 " +
            "직접 돌린 검사다. 네이티브 출력은 mute=true 로 항상 무음이며 청취 품질 검사가 아니다. " +
            "실제 TTS 음성·대화 서버·마이크는 쓰지 않는다.";
        public int cases, failed;
        public List<CaseResult> results = new List<CaseResult>();
        public List<string> limits = new List<string>();
        public string outputPath = "";
    }

    static IEnumerator _routine;
    static Report _report;
    static bool _running;

    [MenuItem("Tools/Dialogue/Run buffered playback check", priority = 24)]
    public static void Run()
    {
        if (_running) { Debug.LogWarning("[BufferedPlaybackCheck] 이미 실행 중입니다."); return; }
        if (!EditorApplication.isPlaying)
        {
            Debug.LogError("[BufferedPlaybackCheck] Play 모드에서만 실행합니다. 네이티브 재생이 필요합니다.");
            return;
        }
        _report = new Report { time = DateTime.UtcNow.ToString("O"), unityVersion = Application.unityVersion };
        _routine = Cases();
        _running = true;
        EditorApplication.update += Step;
        AssemblyReloadEvents.beforeAssemblyReload += AbortOnReload;
    }

    static void AbortOnReload() => Finish("컴파일로 중단했습니다.");

    static void Step()
    {
        if (!EditorApplication.isPlaying) { Finish("Play 모드가 끝나 중단했습니다."); return; }
        bool more;
        try { more = _routine.MoveNext(); }
        catch (Exception e) { _report.error = e.ToString(); Finish("예외로 중단했습니다."); return; }
        if (!more) Finish(null);
    }

    /// <summary>중단·정상 종료 모두 여기로 모인다. 반복자 Dispose 가 각 검사의 finally 를 돌려
    /// 임시 물체와 player 를 정리한다.</summary>
    static void Finish(string abortReason)
    {
        if (!_running) return;
        _running = false;
        EditorApplication.update -= Step;
        AssemblyReloadEvents.beforeAssemblyReload -= AbortOnReload;
        try { (_routine as IDisposable)?.Dispose(); }
        catch (Exception e) { _report.error += "\n정리 중 예외: " + e; }
        _routine = null;

        _report.aborted = abortReason != null;
        if (abortReason != null) _report.error = abortReason + "\n" + _report.error;
        foreach (var result in _report.results)
        {
            if (!result.passed && result.notes.Count == 0) result.notes.Add("실패: 검사가 끝나기 전에 중단됐다");
            if (result.passed && result.notes.Count == 0) result.notes.Add("통과");
        }
        _report.cases = _report.results.Count;
        foreach (var result in _report.results) if (!result.passed) _report.failed++;
        _report.passed = _report.failed == 0 && !_report.aborted && string.IsNullOrEmpty(_report.error);
        _report.limits.Add("네이티브 출력은 mute 되어 있다. 소리의 품질·지지직 여부는 여기서 판정하지 않는다.");
        _report.limits.Add("합성 PCM 이다. 실제 TTS 음성과 사람의 청취 판정이 아니다.");
        _report.limits.Add("전체 수신 모드의 renderedFrames/callbacks 는 -1(미측정)이다. 정상 출력 근거가 아니다.");
        _report.limits.Add("씬에 AudioListener 가 없으면 timeSamples 가 진행하지 않아 자연 종료 검사가 실패한다.");
        Write(_report);
    }

    // --- 검사 목록 ---

    static IEnumerator Cases()
    {
        foreach (var x in Case("receiving_does_not_play", ReceivingDoesNotPlay)) yield return x;
        foreach (var x in Case("clip_matches_source_samples", ClipMatchesSource)) yield return x;
        foreach (var x in Case("mark_complete_is_idempotent", MarkCompleteIdempotent)) yield return x;
        foreach (var x in Case("natural_end_reports_consumed_and_ended", NaturalEnd)) yield return x;
        foreach (var x in Case("pause_while_receiving_then_resume", PauseWhileReceiving)) yield return x;
        foreach (var x in Case("pause_during_playback_preserves_position", PauseDuringPlayback)) yield return x;
        foreach (var x in Case("cancel_while_receiving_then_next_response", CancelWhileReceiving)) yield return x;
        foreach (var x in Case("stop_during_playback_then_next_response", StopDuringPlayback)) yield return x;
        foreach (var x in Case("rejects_bad_pcm_and_45s_cap", BadPcmAndCap)) yield return x;
        foreach (var x in Case("client_completes_after_playback_end_plus_tail", ClientIntegration)) yield return x;
    }

    static IEnumerable Case(string name, Func<CaseResult, IEnumerable> body)
    {
        var result = new CaseResult { name = name };
        _report.results.Add(result);
        foreach (var x in body(result)) yield return x;
        result.passed = result.notes.Count == 0;
    }

    /// <summary>수신 중에는 재생하지 않는다. clip 도 아직 없다.</summary>
    static IEnumerable ReceivingDoesNotPlay(CaseResult r)
    {
        using (var rig = new Rig())
        {
            var data = Samples(Packet * 2, 11);
            Feed(rig.player, data);
            foreach (var x in Frames(3)) yield return x;
            if (rig.source.isPlaying) r.notes.Add("실패: 수신 중에 재생이 시작됐다");
            if (rig.source.clip != null) r.notes.Add("실패: 완료 전에 clip 이 생겼다 samples=" + rig.source.clip.samples);
            if (rig.player.Consumed != 0) r.notes.Add("실패: 수신 중 Consumed=" + rig.player.Consumed);
            if (!rig.player.ReceiveWholeResponse) r.notes.Add("실패: ReceiveWholeResponse 가 false 다");
            if (rig.player.Received != data.Length)
                r.notes.Add("실패: Received=" + rig.player.Received + " != " + data.Length);
            var snapshot = rig.player.Snapshot();
            if (snapshot.renderedFrames != -1 || snapshot.callbacks != -1)
                r.notes.Add("실패: renderedFrames=" + snapshot.renderedFrames + " callbacks=" +
                            snapshot.callbacks + " (전체 수신 모드는 -1 미측정이어야 한다)");
            r.actual.Add("received=" + rig.player.Received + " consumed=" + rig.player.Consumed +
                         " isPlaying=" + rig.source.isPlaying + " clip=" + (rig.source.clip == null ? "없음" : "있음") +
                         " renderedFrames=" + snapshot.renderedFrames + " callbacks=" + snapshot.callbacks + "(미측정)");
        }
    }

    /// <summary>완료 시 만든 stereo clip 의 좌우가 원본 mono 표본과 정확히 같아야 한다.
    /// 마지막 17표본 자투리 패킷도 빠짐없이 들어간다.</summary>
    static IEnumerable ClipMatchesSource(CaseResult r)
    {
        using (var rig = new Rig())
        {
            var data = Samples(Packet * 2 + 17, 23);
            Feed(rig.player, data);
            rig.player.MarkComplete();
            yield return null;
            var clip = rig.source.clip;
            if (clip == null) { r.notes.Add("실패: 완료 뒤에도 clip 이 없다"); yield break; }
            if (clip.samples != data.Length)
                r.notes.Add("실패: clip.samples=" + clip.samples + " != " + data.Length);
            if (clip.channels != 2) r.notes.Add("실패: clip.channels=" + clip.channels);
            if (clip.frequency != Rate) r.notes.Add("실패: clip.frequency=" + clip.frequency);
            r.actual.Add("samples=" + clip.samples + " channels=" + clip.channels +
                         " frequency=" + clip.frequency + " 마지막표본=" + data[data.Length - 1] +
                         " 자투리패킷=" + (data.Length % Packet) + "표본");
            if (clip.samples != data.Length || clip.channels != 2) yield break;

            var buffer = new float[clip.samples * clip.channels];
            if (!clip.GetData(buffer, 0)) { r.notes.Add("실패: GetData 가 false 를 돌려줬다"); yield break; }
            int valueMismatch = -1, channelMismatch = -1;
            float worst = 0;
            for (int i = 0; i < data.Length; i++)
            {
                float left = buffer[i * 2], right = buffer[i * 2 + 1];
                if (left != right && channelMismatch < 0) channelMismatch = i;
                float diff = Mathf.Abs(left - data[i] / 32768f);
                if (diff > worst) worst = diff;
                if (diff > Tolerance && valueMismatch < 0) valueMismatch = i;
            }
            if (channelMismatch >= 0)
                r.notes.Add("실패: " + channelMismatch + "번째에서 좌우가 다르다 L=" +
                            buffer[channelMismatch * 2] + " R=" + buffer[channelMismatch * 2 + 1]);
            if (valueMismatch >= 0)
                r.notes.Add("실패: " + valueMismatch + "번째가 원본과 다르다 clip=" +
                            buffer[valueMismatch * 2] + " 원본=" + data[valueMismatch] / 32768f);
            r.actual.Add("최대 표본 오차=" + worst.ToString("G6") +
                         " 마지막 clip 값 L=" + buffer[(data.Length - 1) * 2] +
                         " R=" + buffer[(data.Length - 1) * 2 + 1]);
        }
    }

    /// <summary>MarkComplete 를 두 번 불러도 clip 이 새로 만들어지거나 재생이 되감기지 않는다.</summary>
    static IEnumerable MarkCompleteIdempotent(CaseResult r)
    {
        using (var rig = new Rig())
        {
            var data = Samples(Rate, 31);
            Feed(rig.player, data);
            rig.player.MarkComplete();
            foreach (var x in Until(() => rig.player.Consumed > 0, 2f)) yield return x;
            var first = rig.source.clip;
            long before = rig.player.Consumed;
            rig.player.MarkComplete();
            foreach (var x in Wait(.15f)) yield return x;
            long after = rig.player.Consumed;
            if (!ReferenceEquals(first, rig.source.clip)) r.notes.Add("실패: 두 번째 완료가 clip 을 새로 만들었다");
            if (after < before) r.notes.Add("실패: 재생이 되감겼다 " + before + " → " + after);
            if (!rig.source.isPlaying && after < data.Length)
                r.notes.Add("실패: 두 번째 완료 뒤 재생이 멈췄다 consumed=" + after);
            r.actual.Add("clip 동일=" + ReferenceEquals(first, rig.source.clip) +
                         " consumed " + before + " → " + after + " / " + data.Length);
        }
    }

    /// <summary>완료 뒤 자연히 끝날 때 Consumed=N, Snapshot().ended=true 가 된다.</summary>
    static IEnumerable NaturalEnd(CaseResult r)
    {
        using (var rig = new Rig())
        {
            var data = Samples(Rate, 37);
            Feed(rig.player, data);
            rig.player.MarkComplete();
            foreach (var x in Until(() => rig.player.Consumed > 0, 2f)) yield return x;
            if (rig.player.Consumed <= 0) { r.notes.Add("실패: 완료 뒤에도 재생이 시작되지 않았다"); yield break; }
            foreach (var x in Wait(.1f)) yield return x;
            long mid = rig.player.Consumed;
            if (mid <= 0 || mid >= data.Length)
                r.notes.Add("실패: 재생 0.1초 시점 consumed=" + mid + " (0 < consumed < " + data.Length + " 여야 한다)");

            foreach (var x in Until(() => rig.player.Consumed >= data.Length && !rig.source.isPlaying, 5f))
                yield return x;
            var snapshot = rig.player.Snapshot();
            if (rig.player.Consumed != data.Length)
                r.notes.Add("실패: 종료 뒤 Consumed=" + rig.player.Consumed + " != " + data.Length);
            if (!snapshot.ended) r.notes.Add("실패: Snapshot().ended 가 false 다");
            if (rig.source.isPlaying) r.notes.Add("실패: 5초 안에 네이티브 재생이 끝나지 않았다");
            r.actual.Add("0.1초 consumed=" + mid + " 종료 consumed=" + rig.player.Consumed +
                         "/" + data.Length + " ended=" + snapshot.ended + " isPlaying=" + rig.source.isPlaying);
        }
    }

    /// <summary>수신 중에 멈추면 완료해도 재생하지 않는다. 재개해야 처음부터 나간다.</summary>
    static IEnumerable PauseWhileReceiving(CaseResult r)
    {
        using (var rig = new Rig())
        {
            var data = Samples(Rate, 41);
            Feed(rig.player, data, 0, Packet * 2);
            rig.player.Pause();
            Feed(rig.player, data, Packet * 2, data.Length);
            rig.player.MarkComplete();
            foreach (var x in Wait(.3f)) yield return x;
            if (rig.source.isPlaying) r.notes.Add("실패: 보류 중인데 재생했다");
            if (rig.player.Consumed != 0) r.notes.Add("실패: 보류 중 Consumed=" + rig.player.Consumed);
            if (!rig.player.Paused) r.notes.Add("실패: Paused 가 false 다");
            r.actual.Add("보류 중 consumed=" + rig.player.Consumed + " isPlaying=" + rig.source.isPlaying);

            rig.player.Resume();
            foreach (var x in Until(() => rig.player.Consumed >= data.Length && !rig.source.isPlaying, 5f))
                yield return x;
            if (rig.player.Consumed != data.Length)
                r.notes.Add("실패: 재개 뒤 Consumed=" + rig.player.Consumed + " != " + data.Length);
            if (!rig.player.Snapshot().ended) r.notes.Add("실패: 재개 뒤에도 ended 가 false 다");
            r.actual.Add("재개 뒤 consumed=" + rig.player.Consumed + "/" + data.Length);
        }
    }

    /// <summary>재생 중 PauseWithFade 는 네이티브 Pause 다. 멈춘 동안 진행이 보존되고 재개하면 끝까지 간다.</summary>
    static IEnumerable PauseDuringPlayback(CaseResult r)
    {
        using (var rig = new Rig())
        {
            var data = Samples(Rate * 2, 43);
            Feed(rig.player, data);
            rig.player.MarkComplete();
            foreach (var x in Until(() => rig.player.Consumed > 0, 2f)) yield return x;
            foreach (var x in Wait(.15f)) yield return x;

            if (rig.player.PauseAtSample(Packet))
                r.notes.Add("실패: 전체 수신 모드에서 PauseAtSample 이 true 를 돌려줬다");
            rig.player.PauseWithFade(60);
            foreach (var x in Wait(.15f)) yield return x;
            long frozen = rig.player.Consumed;
            bool paused = rig.player.Paused;
            if (!paused) r.notes.Add("실패: PauseWithFade 뒤에도 Paused 가 false 다");
            if (rig.source.isPlaying) r.notes.Add("실패: 네이티브 재생이 멈추지 않았다 consumed=" + frozen);
            foreach (var x in Wait(.2f)) yield return x;
            long held = rig.player.Consumed;
            if (held != frozen) r.notes.Add("실패: 멈춘 0.2초 동안 consumed 가 " + frozen + " → " + held + " 로 변했다");
            r.actual.Add("정지 consumed=" + frozen + " 0.2초 뒤=" + held +
                         " snapshot.consumed=" + rig.player.Snapshot().consumed);

            rig.player.Resume();
            foreach (var x in Until(() => rig.player.Consumed >= data.Length && !rig.source.isPlaying, 6f))
                yield return x;
            if (rig.player.Consumed != data.Length)
                r.notes.Add("실패: 재개 뒤 Consumed=" + rig.player.Consumed + " != " + data.Length);
            if (!rig.player.Snapshot().ended) r.notes.Add("실패: 재개 뒤에도 ended 가 false 다");
            r.actual.Add("재개 뒤 consumed=" + rig.player.Consumed + "/" + data.Length);
        }
    }

    /// <summary>수신 중 취소하면 상태가 0 으로 돌아가고 다음 응답이 정상으로 나간다.</summary>
    static IEnumerable CancelWhileReceiving(CaseResult r)
    {
        using (var rig = new Rig())
        {
            var first = Samples(Rate, 47);
            Feed(rig.player, first);
            rig.player.Stop();
            yield return null;
            if (rig.source.clip != null) r.notes.Add("실패: 취소 뒤 clip 이 남았다");
            if (rig.player.Consumed != 0 || rig.player.Received != 0)
                r.notes.Add("실패: 취소 뒤 consumed=" + rig.player.Consumed + " received=" + rig.player.Received);
            if (rig.source.isPlaying) r.notes.Add("실패: 취소 뒤에도 재생 중이다");
            r.actual.Add("취소 뒤 received=" + rig.player.Received + " consumed=" + rig.player.Consumed);

            var second = Samples(Packet * 3, 53);
            Feed(rig.player, second);
            rig.player.MarkComplete();
            foreach (var x in Until(() => rig.player.Consumed >= second.Length && !rig.source.isPlaying, 5f))
                yield return x;
            if (rig.source.clip == null || rig.source.clip.samples != second.Length)
                r.notes.Add("실패: 다음 응답 clip.samples=" +
                            (rig.source.clip == null ? "없음" : rig.source.clip.samples.ToString()));
            if (rig.player.Consumed != second.Length)
                r.notes.Add("실패: 다음 응답 Consumed=" + rig.player.Consumed + " != " + second.Length);
            r.actual.Add("다음 응답 consumed=" + rig.player.Consumed + "/" + second.Length);
        }
    }

    /// <summary>재생 중 정지도 같은 계약이다. 남은 clip 과 계수가 다음 응답으로 새지 않는다.</summary>
    static IEnumerable StopDuringPlayback(CaseResult r)
    {
        using (var rig = new Rig())
        {
            var first = Samples(Rate * 2, 59);
            Feed(rig.player, first);
            rig.player.MarkComplete();
            foreach (var x in Until(() => rig.player.Consumed > 0, 2f)) yield return x;
            long at = rig.player.Consumed;
            rig.player.Stop();
            yield return null;
            if (rig.source.clip != null) r.notes.Add("실패: 정지 뒤 clip 이 남았다");
            if (rig.player.Consumed != 0 || rig.player.Received != 0)
                r.notes.Add("실패: 정지 뒤 consumed=" + rig.player.Consumed + " received=" + rig.player.Received);
            if (rig.source.isPlaying) r.notes.Add("실패: 정지 뒤에도 재생 중이다");
            r.actual.Add("정지 시점 consumed=" + at + " 정지 뒤 consumed=" + rig.player.Consumed +
                         " received=" + rig.player.Received);

            var second = Samples(Packet * 3, 61);
            Feed(rig.player, second);
            rig.player.MarkComplete();
            foreach (var x in Until(() => rig.player.Consumed >= second.Length && !rig.source.isPlaying, 5f))
                yield return x;
            if (rig.player.Consumed != second.Length)
                r.notes.Add("실패: 다음 응답 Consumed=" + rig.player.Consumed + " != " + second.Length);
            if (!rig.player.Snapshot().ended) r.notes.Add("실패: 다음 응답이 끝나지 않았다");
            r.actual.Add("다음 응답 consumed=" + rig.player.Consumed + "/" + second.Length);
        }
    }

    /// <summary>불량 PCM 은 ArgumentException, 45초 상한 초과는 InvalidOperationException 이다.</summary>
    static IEnumerable BadPcmAndCap(CaseResult r)
    {
        using (var rig = new Rig())
        {
            Expect<ArgumentException>(r, "null", () => rig.player.Enqueue(null));
            Expect<ArgumentException>(r, "길이 0", () => rig.player.Enqueue(new byte[0]));
            Expect<ArgumentException>(r, "홀수 길이", () => rig.player.Enqueue(new byte[9599]));
            Expect<ArgumentException>(r, "패킷 상한 초과", () => rig.player.Enqueue(new byte[MaxPacketBytes + 2]));
            if (rig.player.Received != 0)
                r.notes.Add("실패: 거부된 패킷이 Received=" + rig.player.Received + " 로 남았다");

            var block = Samples(Packet, 67);
            long accepted = 0;
            string thrown = null;
            for (int i = 0; i < 250 && thrown == null; i++)
            {
                try { rig.player.Enqueue(Encode(block, 0, block.Length)); accepted += Packet; }
                catch (InvalidOperationException) { thrown = "InvalidOperationException"; }
                catch (Exception e) { thrown = e.GetType().Name; }
                if (i % 32 == 31) yield return null;
            }
            if (thrown == null) r.notes.Add("실패: " + accepted + "표본을 넣어도 상한 예외가 없었다");
            else if (thrown != "InvalidOperationException")
                r.notes.Add("실패: 상한 초과 예외가 " + thrown + " 다");
            if (accepted < CapSamples || accepted > CapSamples + Packet)
                r.notes.Add("실패: 허용된 표본이 " + accepted + " 로 45초(" + CapSamples + ") 상한과 맞지 않는다");
            r.actual.Add("허용 표본=" + accepted + " (" + (accepted / (float)Rate).ToString("F2") + "초) 예외=" + thrown +
                         " received=" + rig.player.Received);
            rig.player.Stop();
        }
    }

    /// <summary>실제 DialogueVoiceClient 의 HandleDialogueEvent/UpdatePlayback 을 그대로 돌린다.
    /// 재생 전에는 완료하지 않고, 재생이 끝난 뒤 TailSeconds 가 지나야 완료한다.
    /// SendControl 은 _dialogue 가 null 이면 false 로 즉시 돌아오므로 서버 연결이 없다.</summary>
    static IEnumerable ClientIntegration(CaseResult r)
    {
        var type = typeof(DialogueVoiceClient);
        var eventType = type.GetNestedType("DialogueEvent", BindingFlags.NonPublic);
        var handle = type.GetMethod("HandleDialogueEvent", Flags);
        var pump = type.GetMethod("UpdatePlayback", Flags);
        var playbackField = type.GetField("_playback", Flags);
        var turnField = type.GetField("_dialogueTurn", Flags);
        var responseField = type.GetField("_responseId", Flags);
        if (eventType == null || handle == null || pump == null ||
            playbackField == null || turnField == null || responseField == null)
        { r.notes.Add("실패: 클라이언트 내부 구조를 찾지 못했다"); yield break; }

        using (var rig = new Rig())
        {
            GameObject clientHost = null;
            try
            {
                // 비활성 GameObject 에 붙인다. Awake 가 실행되지 않아 마이크·연결이 열리지 않는다.
                clientHost = new GameObject("Dialogue Buffered Client") { hideFlags = HideFlags.DontSave };
                clientHost.SetActive(false);
                var client = clientHost.AddComponent<DialogueVoiceClient>();
                client.enabled = false;
                var own = clientHost.GetComponent<AudioSource>();
                if (own != null) { own.playOnAwake = false; own.mute = true; own.enabled = false; }

                playbackField.SetValue(client, rig.player);
                turnField.SetValue(client, 1);
                SetTts(client, true);
                Action<string> send = json =>
                    handle.Invoke(client, new object[] { JsonUtility.FromJson(json, eventType) });

                var data = Samples(Packet * 5, 71);
                send("{\"type\":\"response.started\",\"turn_id\":1,\"response_id\":\"r1\",\"route\":\"normal\"}");
                if ((string)responseField.GetValue(client) != "r1")
                    r.notes.Add("실패: response.started 뒤 _responseId=" + responseField.GetValue(client));
                for (int i = 0; i < data.Length; i += Packet)
                    send("{\"type\":\"response.audio\",\"turn_id\":1,\"response_id\":\"r1\"," +
                         "\"format\":\"pcm_s16le\",\"sample_rate\":24000,\"channels\":1,\"pcm\":\"" +
                         Convert.ToBase64String(Encode(data, i, Math.Min(Packet, data.Length - i))) + "\"}");
                send("{\"type\":\"audio.boundary\",\"turn_id\":1,\"response_id\":\"r1\",\"samples\":" +
                     data.Length + ",\"text_chars\":5}");
                pump.Invoke(client, null);
                yield return null;
                if (rig.source.isPlaying) r.notes.Add("실패: response.done 전에 재생했다");
                if (client.PlaybackFinished) r.notes.Add("실패: response.done 전에 완료 처리됐다");

                send("{\"type\":\"response.done\",\"turn_id\":1,\"response_id\":\"r1\",\"text\":\"ok\"," +
                     "\"timing\":{\"first_text_sec\":0.1,\"first_audio_sec\":0.2,\"total_sec\":0.5}}");

                float tail = rig.player.TailSeconds;
                float startedAt = -1, endedAt = -1, finishedAt = -1;
                float deadline = Time.realtimeSinceStartup + 10f;
                string idWhilePlaying = "";
                while (Time.realtimeSinceStartup < deadline && finishedAt < 0)
                {
                    pump.Invoke(client, null);
                    float now = Time.realtimeSinceStartup;
                    if (startedAt < 0 && rig.player.Consumed > 0)
                    { startedAt = now; idWhilePlaying = client.CurrentResponseId; }
                    if (endedAt < 0 && rig.player.Consumed >= data.Length && !rig.source.isPlaying) endedAt = now;
                    if (client.PlaybackFinished)
                    {
                        finishedAt = now;
                        if (endedAt < 0) r.notes.Add("실패: 재생이 끝나기 전에 완료 처리됐다 consumed=" + rig.player.Consumed);
                    }
                    yield return null;
                }

                if (startedAt < 0) r.notes.Add("실패: 재생이 시작되지 않았다");
                if (endedAt < 0) r.notes.Add("실패: 재생이 자연히 끝나지 않았다 consumed=" + rig.player.Consumed);
                if (finishedAt < 0) r.notes.Add("실패: PlaybackFinished 가 되지 않았다");
                if (idWhilePlaying != "r1") r.notes.Add("실패: 재생 중 CurrentResponseId=" + idWhilePlaying);
                if (endedAt >= 0 && finishedAt >= 0)
                {
                    float delay = finishedAt - endedAt;
                    if (delay + .05f < tail) r.notes.Add("실패: 잔향 " + tail + "초 전에 완료했다 지연=" + delay);
                    if (delay > tail + .5f) r.notes.Add("실패: 완료가 너무 늦었다 지연=" + delay + " 잔향=" + tail);
                    r.actual.Add("재생 " + (endedAt - startedAt).ToString("F3") + "초, 종료→완료 " +
                                 delay.ToString("F3") + "초, TailSeconds=" + tail.ToString("F3"));
                }
                if (finishedAt >= 0 && client.CurrentResponseId != "")
                    r.notes.Add("실패: 완료 뒤 CurrentResponseId=" + client.CurrentResponseId);
                r.actual.Add("consumed=" + rig.player.Consumed + "/" + data.Length +
                             " ended=" + rig.player.Snapshot().ended +
                             " PlaybackFinished=" + client.PlaybackFinished +
                             " (SendControl 은 _dialogue=null 이라 전송 없이 false 반환)");
            }
            finally { if (clientHost != null) UnityEngine.Object.DestroyImmediate(clientHost); }
        }
    }

    // --- 도구 ---

    /// <summary>검사마다 새로 만들고 반드시 버린다. mute=true 라 항상 무음이다.</summary>
    sealed class Rig : IDisposable
    {
        public readonly GameObject host;
        public readonly AudioSource source;
        public readonly DialogueAudioPlayer player;

        public Rig()
        {
            host = new GameObject("Dialogue Buffered Playback Check") { hideFlags = HideFlags.DontSave };
            source = host.AddComponent<AudioSource>();
            source.playOnAwake = false;
            source.mute = true;             // 검사 내내 소리를 내지 않는다
            source.spatialBlend = 0;
            source.loop = false;
            source.volume = 0;
            player = new DialogueAudioPlayer(source, true);
        }

        public void Dispose()
        {
            try { player?.Dispose(); }
            finally { if (host != null) UnityEngine.Object.DestroyImmediate(host); }
        }
    }

    static void SetTts(DialogueVoiceClient client, bool value)
    {
        var type = typeof(DialogueVoiceClient);
        var field = type.GetField("TtsEnabled", Flags) ?? type.GetField("<TtsEnabled>k__BackingField", Flags);
        if (field != null) { field.SetValue(client, value); return; }
        var setter = type.GetProperty("TtsEnabled", Flags)?.GetSetMethod(true);
        if (setter == null) throw new Exception("TtsEnabled 를 설정할 수 없습니다.");
        setter.Invoke(client, new object[] { value });
    }

    static void Expect<T>(CaseResult r, string label, Action action) where T : Exception
    {
        try { action(); r.notes.Add("실패: " + label + " 에서 예외가 없었다"); }
        catch (T) { r.actual.Add(label + " → " + typeof(T).Name); }
        catch (Exception e) { r.notes.Add("실패: " + label + " 예외가 " + e.GetType().Name + " 다"); }
    }

    static short[] Samples(int count, int seed)
    {
        var random = new System.Random(seed);
        var data = new short[count];
        for (int i = 0; i < count; i++) data[i] = (short)random.Next(-20000, 20001);
        data[count - 1] = 12345;   // 마지막 자투리까지 비교되는지 보려고 비영 값을 둔다
        return data;
    }

    static byte[] Encode(short[] data, int offset, int count)
    {
        var pcm = new byte[count * 2];
        for (int i = 0; i < count; i++)
        {
            short value = data[offset + i];
            pcm[i * 2] = (byte)value;
            pcm[i * 2 + 1] = (byte)(value >> 8);
        }
        return pcm;
    }

    static void Feed(DialogueAudioPlayer player, short[] data) => Feed(player, data, 0, data.Length);

    static void Feed(DialogueAudioPlayer player, short[] data, int from, int to)
    {
        for (int i = from; i < to; i += Packet)
            player.Enqueue(Encode(data, i, Math.Min(Packet, to - i)));
    }

    static IEnumerable Frames(int count) { for (int i = 0; i < count; i++) yield return null; }

    static IEnumerable Wait(float seconds)
    {
        float until = Time.realtimeSinceStartup + seconds;
        while (Time.realtimeSinceStartup < until) yield return null;
    }

    static IEnumerable Until(Func<bool> condition, float seconds)
    {
        float deadline = Time.realtimeSinceStartup + seconds;
        while (!condition() && Time.realtimeSinceStartup < deadline) yield return null;
    }

    static void Write(Report report)
    {
        string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", OutputPath));
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            report.outputPath = path;
            File.WriteAllText(path, JsonUtility.ToJson(report, true), new UTF8Encoding(false));
        }
        catch (Exception e)
        {
            report.passed = false;
            Debug.LogError("[BufferedPlaybackCheck] 결과 파일을 쓰지 못했습니다: " + path + "\n" + e);
        }
        string summary = "[BufferedPlaybackCheck] passed=" + report.passed + " cases=" + report.cases +
                         " failed=" + report.failed + " aborted=" + report.aborted +
                         " out=" + report.outputPath + "\n" + report.error;
        if (report.passed) Debug.Log(summary);
        else Debug.LogError(summary);
    }
}
