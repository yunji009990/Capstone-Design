// DialoguePauseBoundaryValidation.cs
// 끼어들기 정지가 "다음 구절의 표본을 한 개도 내보내지 않는 지점"에서 일어나는지,
// 그리고 보고(playback.paused)가 "실제로 멈춘 뒤 + 출력 잔향이 빠진 뒤"에만 나가는지
// 결정적으로 검증한다.
//
// 스텁이 아니라 생산 코드를 그대로 돌린다.
// - 렌더러: AudioSource 없는 GameObject 에 올리고 private OnAudioFilterRead 를 리플렉션으로
//   직접 부른다. Unity 가 콜백을 자동으로 부르지 않으므로 소리가 나지 않고 결과가 같다.
// - 클라이언트: 실제 DialogueVoiceClient 의 HandleDialogueEvent/UpdatePlayback 을 리플렉션으로
//   부르고, 실제 DialogueAudioPlayer(에디터용 AudioSource 없는 생성자)를 붙인다. 시각은
//   DialogueVoiceClient.EditorClock 으로 고정한다. 대화 서버·마이크·씬·등록 인물은 건드리지 않는다.
//
// 기대 파형·기대 프레임 수는 렌더러 코드를 재사용하지 않고 여기서 다시 계산한다.
// 상태 기계를 복제하면 같은 버그를 복제해 거짓 통과가 난다.
//
// 이 검사는 Tools/Dialogue/Run resampler core check 를 대신하지 않는다. 일반 재생 파형은
// 그쪽이 본다. 여기서는 정지·보고 계약만 본다. 실제 TTS PCM 과 사람의 청취 판정도 아니다.

using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class DialoguePauseBoundaryValidation
{
    const string FallbackOutput = "tools/_work/interruption_flow_20260917/pause-boundary.json";
    const int Rate = DialogueAudioRenderer.SourceRate;
    const float Tolerance = 1e-4f;
    const float Silence = 1e-6f;
    const int Phrase = 2400;        // 0.1초짜리 구절 두 개로 경계를 만든다
    const float Drain = .2f;        // 검사용 출력 잔향. 실제 값은 DSP 버퍼에서 온다
    const int FadeMillis = 60;      // 클라이언트가 쓰는 감쇠 길이와 같다

    [Serializable]
    class CaseResult
    {
        public string name = "";
        public bool passed;
        public int outputRate;
        public List<string> notes = new List<string>();
    }

    [Serializable]
    class Report
    {
        public bool passed;
        public string time = "", unityVersion = "", error = "";
        public string scope =
            "생산 렌더러 콜백과 생산 클라이언트 처리기를 직접 불러 잰 결정적 검사다. " +
            "합성 PCM 이며 실제 TTS 음성·사람의 청취 판정이 아니다.";
        public int cases, failed;
        public List<CaseResult> results = new List<CaseResult>();
        public List<string> limits = new List<string>();
        public string outputPath = "";
    }

    static MethodInfo _callback;

    [MenuItem("Tools/Dialogue/Run pause boundary check", priority = 23)]
    public static void Run()
    {
        var report = new Report { time = DateTime.UtcNow.ToString("O"), unityVersion = Application.unityVersion };
        GameObject host = null;
#if UNITY_EDITOR
        var savedClock = DialogueVoiceClient.EditorClock;
#endif
        try
        {
            // AudioSource 를 붙이지 않는다. 그래야 자동 콜백이 끼어들지 않고 소리도 나지 않는다.
            host = new GameObject("Dialogue Pause Boundary Check") { hideFlags = HideFlags.DontSave };
            foreach (int rate in new[] { 48000, 44100, 96000 })
            {
                report.results.Add(BoundaryStop(host, rate));
                report.results.Add(FadeCompletes(host, rate));
                report.results.Add(StarvedFadeNeverApplies(host, rate));
                report.results.Add(PassedBoundaryRefused(host, rate));
                report.results.Add(ClientBoundaryAck(rate));
                report.results.Add(ClientGraceExpired(rate));
            }
            // 아래는 리샘플과 무관한 상태 계약이라 48 kHz 에서 한 번만 본다.
            report.results.Add(ClientStallForced());
            report.results.Add(ClientResumeCancels());
            report.results.Add(ClientDuplicateRequest());
            report.results.Add(ClientAlreadyPausedKeepsDrain());
            report.results.Add(ClientDuplicateAtBoundaryKeepsDeliveredText());
            report.results.Add(ClientImmediateOverride());
            report.results.Add(ClientNoAudio());
            report.results.Add(ClientZeroGraceFadesImmediately());
            report.results.Add(ClientLegacyServer());
            report.results.Add(ClientResetClears());

            report.cases = report.results.Count;
            foreach (var result in report.results) if (!result.passed) report.failed++;
            report.passed = report.failed == 0;
        }
        catch (Exception e)
        {
            report.passed = false;
            report.error = e.ToString();
        }
        finally
        {
#if UNITY_EDITOR
            DialogueVoiceClient.EditorClock = savedClock;
#endif
            if (host != null) UnityEngine.Object.DestroyImmediate(host);
            report.limits.Add("합성 PCM 검사다. 실제 TTS 음성과 사람의 청취 판정이 아니다.");
            report.limits.Add("정지 지점은 서버가 보낸 구절 경계뿐이다. 낱말 경계를 찾지 않는다.");
            report.limits.Add("실제 오디오 장치·DSP 잔향·스레드 경합은 이 검사 범위 밖이다.");
            report.limits.Add("일반 재생 파형 회귀는 Run resampler core check 가 본다.");
            Write(report);
        }
    }

    // --- 렌더러 ---

    /// <summary>예약한 경계 직전에서 멈추고, 다음 구절의 표본이 한 개도 나가지 않아야 한다.
    /// 재개하면 두 구절이 이어지고 잃거나 겹치는 표본이 없어야 한다.</summary>
    static CaseResult BoundaryStop(GameObject host, int rate)
    {
        var result = new CaseResult { name = "boundary_stops_before_next_phrase_" + rate, outputRate = rate };
        var renderer = Attach(host, rate);
        try
        {
            float[] source = TwoPhrases();
            renderer.Write(Encode(source), source.Length * 2);
            renderer.Begin();
            var capture = new Capture();
            DriveBlock(renderer, 256, capture);
            if (!renderer.ScheduleSourcePause(Phrase))
                result.notes.Add("실패: 아직 지나지 않은 경계인데 예약이 거부됐다");
            for (int i = 0; i < 80; i++) DriveBlock(renderer, 512, capture);

            var pause = renderer.PauseState;
            if (!pause.applied) result.notes.Add("실패: 경계에 닿았는데 멈추지 않았다");
            if (pause.cause != DialogueAudioRenderer.PauseBoundary)
                result.notes.Add("실패: 정지 원인이 경계가 아니다 cause=" + pause.cause);
            if (pause.position >= Phrase)
                result.notes.Add("실패: 정지 위치가 경계를 넘었다 " + pause.position);

            long expected = FramesBefore(Phrase, rate);
            if (capture.rendered.Count != expected)
                result.notes.Add("실패: 경계까지 렌더 " + capture.rendered.Count + " != 기대 " + expected);
            float first = Quantise(.5f);
            for (int k = 0; k < capture.rendered.Count; k++)
                if (Mathf.Abs(capture.rendered[k] - first) > Tolerance)
                {
                    result.notes.Add("실패: " + k + "번째 프레임에 다음 구절 소리가 섞였다 " + capture.rendered[k]);
                    break;
                }
            if (renderer.Consumed > Phrase + 1)
                result.notes.Add("실패: 경계 너머로 " + (renderer.Consumed - Phrase) + "샘플을 더 읽었다");

            renderer.SetPaused(false);
            renderer.MarkComplete();
            for (int i = 0; i < 120; i++) DriveBlock(renderer, 512, capture);
            if (!renderer.Ended) result.notes.Add("실패: 재개 뒤에도 끝나지 않았다");
            if (renderer.Consumed != source.Length)
                result.notes.Add("실패: 총 소비 " + renderer.Consumed + " != " + source.Length);
            CompareWaveform(result, source, capture.rendered, rate);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return Finish(result);
    }

    /// <summary>감쇠는 요청만으로 멈춘 것이 아니다. 램프가 실제로 끝나야 멈춘 상태가 된다.</summary>
    static CaseResult FadeCompletes(GameObject host, int rate)
    {
        var result = new CaseResult { name = "fade_completes_before_pause_" + rate, outputRate = rate };
        var renderer = Attach(host, rate);
        try
        {
            float[] source = Constant(4800, .5f);
            renderer.Write(Encode(source), source.Length * 2);
            renderer.Begin();
            DriveBlock(renderer, 512, new Capture());

            int length = Mathf.Max(1, rate * FadeMillis / 1000);
            renderer.FadeToPause(FadeMillis);
            if (renderer.PauseState.applied)
                result.notes.Add("실패: 감쇠를 요청하자마자 멈춘 것으로 보고했다");

            var ramp = new Capture();
            for (int i = 0; i < 400 && !renderer.PauseState.applied; i++) DriveBlock(renderer, 128, ramp);
            var pause = renderer.PauseState;
            if (!pause.applied) result.notes.Add("실패: 감쇠가 끝나지 않았다");
            if (pause.cause != DialogueAudioRenderer.PauseFade)
                result.notes.Add("실패: 정지 원인이 감쇠가 아니다 cause=" + pause.cause);
            if (ramp.rendered.Count != length)
                result.notes.Add("실패: 감쇠 프레임 " + ramp.rendered.Count + " != 기대 " + length);
            float level = Quantise(.5f);
            for (int i = 0; i < ramp.rendered.Count; i++)
            {
                float want = level * (length - i) / length;
                if (Mathf.Abs(ramp.rendered[i] - want) > Tolerance)
                { result.notes.Add("실패: " + i + "번째 감쇠 값이 기대 램프와 다르다"); break; }
                if (i > 0 && ramp.rendered[i] > ramp.rendered[i - 1] + Tolerance)
                { result.notes.Add("실패: 감쇠가 다시 커졌다"); break; }
            }

            long frozen = renderer.Consumed;
            var after = new Capture();
            for (int i = 0; i < 8; i++) DriveBlock(renderer, 512, after);
            if (after.rendered.Count != 0) result.notes.Add("실패: 멈춘 뒤에도 렌더했다");
            foreach (float value in after.raw)
                if (Mathf.Abs(value) > Silence) { result.notes.Add("실패: 멈춘 뒤 무음이 아니다"); break; }
            if (renderer.Consumed != frozen) result.notes.Add("실패: 멈춘 뒤 소비가 늘었다");
            if (renderer.Ended) result.notes.Add("실패: 감쇠 정지를 종료로 처리했다");
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return Finish(result);
    }

    /// <summary>공급이 끊기면 램프가 끝나지 못한다. 그래서 클라이언트에 벽시계 상한이 필요하다.</summary>
    static CaseResult StarvedFadeNeverApplies(GameObject host, int rate)
    {
        var result = new CaseResult { name = "starved_fade_never_applies_" + rate, outputRate = rate };
        var renderer = Attach(host, rate);
        try
        {
            float[] source = Constant(1200, .5f);
            renderer.Write(Encode(source), source.Length * 2);
            renderer.Begin();
            var capture = new Capture();
            for (int i = 0; i < 40; i++) DriveBlock(renderer, 512, capture);
            if (renderer.Consumed != source.Length)
                result.notes.Add("실패: 굶기 전 소비 " + renderer.Consumed + " != " + source.Length);

            renderer.FadeToPause(FadeMillis);
            for (int i = 0; i < 80; i++) DriveBlock(renderer, 512, capture);
            if (renderer.PauseState.applied)
                result.notes.Add("실패: 출력이 없는데 감쇠가 끝난 것으로 보고했다");
            if (renderer.Consumed != source.Length) result.notes.Add("실패: 굶은 뒤 소비가 늘었다");
            if (renderer.Ended) result.notes.Add("실패: 완료 표시 전인데 종료됐다");
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return Finish(result);
    }

    /// <summary>이미 지나간 경계에는 예약하지 않는다. 늦은 정지를 경계라고 부르지 않기 위해서다.</summary>
    static CaseResult PassedBoundaryRefused(GameObject host, int rate)
    {
        var result = new CaseResult { name = "passed_boundary_refused_" + rate, outputRate = rate };
        var renderer = Attach(host, rate);
        try
        {
            float[] source = TwoPhrases();
            renderer.Write(Encode(source), source.Length * 2);
            renderer.Begin();
            var capture = new Capture();
            long need = FramesBefore(Phrase, rate) + 1024;
            for (long done = 0; done < need; done += 512) DriveBlock(renderer, 512, capture);
            if (renderer.PauseState.position < Phrase)
                result.notes.Add("실패: 검사 준비가 경계를 지나지 못했다 " + renderer.PauseState.position);
            if (renderer.ScheduleSourcePause(Phrase))
                result.notes.Add("실패: 이미 지나간 경계를 예약했다");
            for (int i = 0; i < 8; i++) DriveBlock(renderer, 512, capture);
            if (renderer.PauseState.applied) result.notes.Add("실패: 거부된 예약으로 멈췄다");
            if (!renderer.ScheduleSourcePause(renderer.Consumed + Phrase))
                result.notes.Add("실패: 아직 남은 경계 예약이 거부됐다");
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return Finish(result);
    }

    // --- 클라이언트 ---

    /// <summary>경계에서 멈추고, 실제 정지와 잔향이 끝난 뒤에만 boundary 로 보고한다.</summary>
    static CaseResult ClientBoundaryAck(int rate)
    {
        var result = new CaseResult { name = "client_acks_boundary_after_drain_" + rate, outputRate = rate };
        using (var rig = new ClientRig(rate))
        {
            rig.Audio(Constant(Phrase, .5f));
            rig.Audio(Constant(Phrase, -.5f));
            rig.Boundary(Phrase, 7);
            for (int i = 0; i < 2; i++) rig.Step();
            rig.Paused(1, 1200, "phrase");

            float appliedAt = -1, ackAt = -1;
            for (int i = 0; i < 400 && ackAt < 0; i++)
            {
                float before = rig.clock;
                rig.Step();
                if (appliedAt < 0 && rig.renderer.PauseState.applied) appliedAt = before;
                if (ackAt < 0 && rig.client.PauseAckCount > 0) ackAt = before;
                if (ackAt >= 0 && appliedAt < 0) result.notes.Add("실패: 멈추기 전에 보고했다");
            }
            if (appliedAt < 0) result.notes.Add("실패: 경계에서 멈추지 않았다");
            if (ackAt < 0) result.notes.Add("실패: 보고가 나가지 않았다");
            if (rig.client.PauseAckCount != 1) result.notes.Add("실패: 보고 횟수 " + rig.client.PauseAckCount);
            if (rig.client.PauseAckReason != "boundary")
                result.notes.Add("실패: 보고 이유가 " + rig.client.PauseAckReason + " 다");
            if (rig.client.PauseAckId != 1) result.notes.Add("실패: 보고 회차 " + rig.client.PauseAckId);
            if (appliedAt >= 0 && ackAt >= 0 && ackAt + 1e-4f < appliedAt + Drain)
                result.notes.Add("실패: 잔향이 빠지기 전에 보고했다 applied=" + appliedAt + " ack=" + ackAt);
            if (appliedAt >= 0 && ackAt > appliedAt + Drain + .05f)
                result.notes.Add("실패: 보고가 잔향보다 많이 늦었다 " + (ackAt - appliedAt));
            if (!rig.client.ResponsePaused) result.notes.Add("실패: 보고했는데 멈춘 상태가 아니다");
            if ((int)rig.Get("_playedChars") != 7)
                result.notes.Add("실패: 전달된 글자 수가 " + rig.Get("_playedChars") + " 다");

            float level = Quantise(.5f);
            foreach (float value in rig.capture.rendered)
                if (Mathf.Abs(value - level) > Tolerance)
                { result.notes.Add("실패: 다음 구절 소리가 이미 나갔다 " + value); break; }
        }
        return Finish(result);
    }

    /// <summary>경계가 상한 안에 없으면 감쇠로 멈춘다. 잘린 구절은 전달로 세지 않는다.</summary>
    static CaseResult ClientGraceExpired(int rate)
    {
        var result = new CaseResult { name = "client_acks_grace_expired_" + rate, outputRate = rate };
        using (var rig = new ClientRig(rate))
        {
            // 200 ms 대기 + 60 ms 감쇠보다 긴 공급. 굶김은 별도 검사로 다룬다.
            rig.Audio(Constant(Rate, .5f));
            rig.Paused(1, 200, "phrase");

            float appliedAt = -1, ackAt = -1;
            for (int i = 0; i < 400 && ackAt < 0; i++)
            {
                float before = rig.clock;
                rig.Step();
                if (appliedAt < 0 && rig.renderer.PauseState.applied) appliedAt = before;
                if (ackAt < 0 && rig.client.PauseAckCount > 0) ackAt = before;
                if (appliedAt < 0 && before > .2f + FadeMillis / 1000f + .05f)
                { result.notes.Add("실패: 상한이 지나도 멈추지 않았다"); break; }
                if (appliedAt < 0 && before < .2f - 1e-4f && rig.renderer.PauseState.applied)
                    result.notes.Add("실패: 상한 전에 멈췄다");
            }
            if (ackAt < 0) result.notes.Add("실패: 보고가 나가지 않았다");
            if (rig.client.PauseAckReason != "grace_expired")
                result.notes.Add("실패: 보고 이유가 " + rig.client.PauseAckReason + " 다");
            if (rig.renderer.PauseState.cause != DialogueAudioRenderer.PauseFade)
                result.notes.Add("실패: 감쇠로 멈추지 않았다 cause=" + rig.renderer.PauseState.cause);
            if (appliedAt >= 0 && ackAt >= 0 && ackAt + 1e-4f < appliedAt + Drain)
                result.notes.Add("실패: 잔향이 빠지기 전에 보고했다");
            if ((int)rig.Get("_playedChars") != 0)
                result.notes.Add("실패: 잘린 구절을 전달된 글자로 셌다");

            int count = rig.capture.rendered.Count;
            if (count > 2 && rig.capture.rendered[count - 1] > rig.capture.rendered[count - 2] + Tolerance)
                result.notes.Add("실패: 마지막 출력이 줄어들며 끝나지 않았다");
            if (count > 0 && rig.capture.rendered[count - 1] > Quantise(.5f) * .2f)
                result.notes.Add("실패: 감쇠 없이 큰 값에서 끊겼다");
        }
        return Finish(result);
    }

    /// <summary>출력이 전혀 진행되지 않아도 상한 안에 멈춘다. 상태는 보존되고 경계라고 부르지 않는다.</summary>
    static CaseResult ClientStallForced()
    {
        var result = new CaseResult { name = "client_forces_pause_when_output_stalls", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            float[] source = Constant(4800, .5f);
            rig.Audio(Constant(2400, .5f));
            rig.Audio(Constant(2400, .5f));
            for (int i = 0; i < 2; i++) rig.Step();
            long frozen = rig.renderer.Consumed;
            rig.Paused(1, 200, "phrase");

            // 오디오 콜백을 전혀 부르지 않는다. 감쇠가 끝날 수 없는 상황이다.
            for (int i = 0; i < 200 && rig.client.PauseAckCount == 0; i++) rig.Step(512, false);
            if (rig.client.PauseAckCount != 1) result.notes.Add("실패: 보고가 나가지 않았다");
            if (rig.client.PauseAckReason != "grace_expired")
                result.notes.Add("실패: 보고 이유가 " + rig.client.PauseAckReason + " 다");
            if (rig.renderer.PauseState.cause == DialogueAudioRenderer.PauseBoundary)
                result.notes.Add("실패: 늦은 정지를 경계로 보고했다");
            if (!rig.client.ResponsePaused) result.notes.Add("실패: 멈춘 상태가 아니다");
            if (rig.renderer.Consumed != frozen) result.notes.Add("실패: 멈추는 사이 소비가 늘었다");
            if (rig.clock > .2f + FadeMillis / 1000f + .15f + Drain + .05f)
                result.notes.Add("실패: 상한을 넘겨 멈췄다 " + rig.clock);

            // 재개하면 남은 것이 하나도 없이 끝까지 나가야 한다.
            rig.Resumed();
            rig.player.MarkComplete();
            for (int i = 0; i < 60; i++) rig.Step();
            if (!rig.renderer.Ended) result.notes.Add("실패: 재개 뒤에도 끝나지 않았다");
            CompareWaveform(result, source, rig.capture.rendered, 48000);
        }
        return Finish(result);
    }

    /// <summary>판정이 재개면 한 번도 멈추지 않는다. 늦은 보고도 나가지 않는다.</summary>
    static CaseResult ClientResumeCancels()
    {
        var result = new CaseResult { name = "client_resume_before_boundary_never_pauses", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            float[] source = TwoPhrases();
            rig.Audio(Constant(Phrase, .5f));
            rig.Audio(Constant(Phrase, -.5f));
            rig.Boundary(Phrase, 7);
            rig.Step();
            rig.Paused(1, 1200, "phrase");
            rig.Step();
            rig.Resumed();

            rig.player.MarkComplete();
            for (int i = 0; i < 120; i++)
            {
                rig.Step();
                if (rig.renderer.PauseState.applied)
                { result.notes.Add("실패: 재개했는데 멈췄다"); break; }
            }
            if (rig.client.PauseAckCount != 0)
                result.notes.Add("실패: 재개 뒤에 보고가 나갔다 " + rig.client.PauseAckReason);
            if (rig.client.ResponsePaused) result.notes.Add("실패: 멈춘 상태로 남았다");
            if ((int)rig.Get("_pauseId") != 0) result.notes.Add("실패: 정지 예약이 남았다");
            CompareWaveform(result, source, rig.capture.rendered, 48000);
        }
        return Finish(result);
    }

    /// <summary>두 번째 요청은 상한을 늦추지 않고 회차 번호만 바꾼다.</summary>
    static CaseResult ClientDuplicateRequest()
    {
        var result = new CaseResult { name = "client_duplicate_keeps_earliest_deadline", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            rig.Audio(Constant(Rate, .5f));
            rig.Paused(1, 400, "phrase");
            while (rig.clock < .2f) rig.Step();
            rig.Paused(2, 1200, "phrase");   // 새 기한이 더 길어도 기존 상한이 밀리면 안 된다

            float appliedAt = -1;
            for (int i = 0; i < 400 && rig.client.PauseAckCount == 0; i++)
            {
                float before = rig.clock;
                rig.Step();
                if (appliedAt < 0 && rig.renderer.PauseState.applied) appliedAt = before;
            }
            if (appliedAt < 0) result.notes.Add("실패: 멈추지 않았다");
            else if (appliedAt > .4f + FadeMillis / 1000f + .05f)
                result.notes.Add("실패: 두 번째 요청이 상한을 늦췄다 " + appliedAt);
            if (rig.client.PauseAckId != 2)
                result.notes.Add("실패: 보고 회차가 " + rig.client.PauseAckId + " 다");
            if (rig.client.PauseAckCount != 1)
                result.notes.Add("실패: 보고 횟수 " + rig.client.PauseAckCount);
        }
        return Finish(result);
    }

    /// <summary>이미 멈춘 상태의 새 요청은 남은 잔향 시간을 앞당기지 못한다.</summary>
    static CaseResult ClientAlreadyPausedKeepsDrain()
    {
        var result = new CaseResult { name = "client_already_paused_keeps_outstanding_drain", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            rig.Audio(Constant(Rate, .5f));
            rig.Paused(1, 200, "phrase");

            float appliedAt = -1;
            for (int i = 0; i < 200 && appliedAt < 0; i++)
            {
                float before = rig.clock;
                rig.Step();
                if (rig.renderer.PauseState.applied) appliedAt = before;
            }
            if (appliedAt < 0) { result.notes.Add("실패: 멈추지 않았다"); return Finish(result); }
            if (rig.client.PauseAckCount != 0)
                result.notes.Add("실패: 잔향이 남았는데 벌써 보고했다");

            rig.Paused(3, 0, "phrase");   // 잔향이 끝나기 전에 도착한 새 회차
            float ackAt = -1;
            for (int i = 0; i < 200 && ackAt < 0; i++)
            {
                float before = rig.clock;
                rig.Step();
                if (rig.client.PauseAckCount > 0) ackAt = before;
            }
            if (ackAt < 0) result.notes.Add("실패: 보고가 나가지 않았다");
            else if (ackAt + 1e-4f < appliedAt + Drain)
                result.notes.Add("실패: 새 회차 보고가 잔향을 건너뛰었다 " + ackAt);
            if (rig.client.PauseAckId != 3) result.notes.Add("실패: 보고 회차 " + rig.client.PauseAckId);
            if (rig.client.PauseAckReason != "already_paused")
                result.notes.Add("실패: 보고 이유가 " + rig.client.PauseAckReason + " 다");
        }
        return Finish(result);
    }

    /// <summary>정지 직후 다음 회차가 와도 이미 전달한 구절의 글자 수를 보존한다.</summary>
    static CaseResult ClientDuplicateAtBoundaryKeepsDeliveredText()
    {
        var result = new CaseResult { name = "client_duplicate_before_ack_keeps_delivered_phrase", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            rig.Audio(TwoPhrases());
            rig.Boundary(Phrase, 7);
            rig.Paused(1, 1200, "phrase");
            rig.Frame();
            // 콜백만 진행한다. 메인 스레드의 정지 확인보다 새 요청이 먼저 도착한다.
            for (int i = 0; i < 30 && !rig.renderer.PauseState.applied; i++)
                DriveBlock(rig.renderer, 512, rig.capture);
            if (!rig.renderer.PauseState.applied) result.notes.Add("실패: 경계 정지 준비가 되지 않았다");
            rig.Paused(2, 1200, "phrase");
            for (int i = 0; i < 100 && rig.client.PauseAckCount == 0; i++) rig.Step();
            if (rig.client.PauseAckId != 2) result.notes.Add("실패: 최신 정지 회차가 아니다");
            if ((int)rig.Get("_playedChars") != 7) result.notes.Add("실패: 이미 들려준 구절을 잃었다");
            if (rig.Boundaries() != 0) result.notes.Add("실패: 이미 전달된 경계가 큐에 남았다");
        }
        return Finish(result);
    }

    /// <summary>명시적 보류는 같은 회차로 즉시 정지를 덮어쓴다.</summary>
    static CaseResult ClientImmediateOverride()
    {
        var result = new CaseResult { name = "client_immediate_overrides_schedule", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            rig.Audio(Constant(2400, .5f));
            rig.Audio(Constant(2400, .5f));
            rig.Boundary(4800, 9);
            rig.Paused(1, 1200, "phrase");
            rig.Step();
            if (rig.client.ResponsePaused) result.notes.Add("실패: 예약만 했는데 벌써 멈췄다");

            rig.Paused(1, 1200, "immediate");
            if (!rig.client.ResponsePaused) result.notes.Add("실패: 즉시 정지가 적용되지 않았다");
            long frozen = rig.renderer.Consumed;

            float ackAt = -1, at = rig.clock;
            for (int i = 0; i < 200 && ackAt < 0; i++)
            {
                float before = rig.clock;
                rig.Step();
                if (rig.client.PauseAckCount > 0) ackAt = before;
            }
            if (ackAt < 0) result.notes.Add("실패: 보고가 나가지 않았다");
            else if (ackAt + 1e-4f < at + Drain) result.notes.Add("실패: 잔향 전에 보고했다");
            if (rig.client.PauseAckReason != "immediate")
                result.notes.Add("실패: 보고 이유가 " + rig.client.PauseAckReason + " 다");
            if (rig.client.PauseAckId != 1) result.notes.Add("실패: 보고 회차 " + rig.client.PauseAckId);
            if (rig.renderer.Consumed != frozen) result.notes.Add("실패: 즉시 정지 뒤 소비가 늘었다");
            if ((int)rig.Get("_playedChars") != 0)
                result.notes.Add("실패: 잘린 구절을 전달된 글자로 셌다");
        }
        return Finish(result);
    }

    /// <summary>아직 한 표본도 들려주지 않았으면 기다릴 것이 없다.</summary>
    static CaseResult ClientNoAudio()
    {
        var result = new CaseResult { name = "client_no_audio_acks_at_once", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            rig.Paused(1, 1200, "phrase");
            if (!rig.client.ResponsePaused) result.notes.Add("실패: 즉시 멈추지 않았다");
            float at = rig.clock;
            rig.Step();
            if (rig.client.PauseAckCount != 1) result.notes.Add("실패: 보고 횟수 " + rig.client.PauseAckCount);
            if (rig.client.PauseAckReason != "no_audio")
                result.notes.Add("실패: 보고 이유가 " + rig.client.PauseAckReason + " 다");
            if (at > 1e-4f) result.notes.Add("실패: 검사 준비 시각이 0 이 아니다");
        }
        return Finish(result);
    }

    /// <summary>pause_id 가 없는 서버에서는 예전처럼 즉시 멈추고 보고도 보내지 않는다.</summary>
    static CaseResult ClientZeroGraceFadesImmediately()
    {
        var result = new CaseResult { name = "client_explicit_zero_grace_fades_immediately", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            rig.Audio(Constant(Rate, .5f));
            rig.Paused(1, 0, "phrase");
            for (int i = 0; i < 150 && rig.client.PauseAckCount == 0; i++) rig.Step();
            if (rig.client.PauseAckCount != 1) result.notes.Add("실패: 0 ms 기한의 보고가 없다");
            if (rig.clock > FadeMillis / 1000f + Drain + .05f)
                result.notes.Add("실패: 명시한 0 ms를 기본 대기시간으로 바꾸었다");
            if (rig.renderer.PauseState.cause != DialogueAudioRenderer.PauseFade)
                result.notes.Add("실패: 0 ms에서도 짧은 감쇠로 멈춰야 한다");
        }
        return Finish(result);
    }

    /// <summary>pause_id 가 없는 서버에서는 예전처럼 즉시 멈추고 보고도 보내지 않는다.</summary>
    static CaseResult ClientLegacyServer()
    {
        var result = new CaseResult { name = "client_legacy_server_keeps_old_behaviour", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            float[] source = Constant(4800, .5f);
            rig.Audio(Constant(2400, .5f));
            rig.Audio(Constant(2400, .5f));
            rig.Boundary(2400, 7);
            rig.Step();
            rig.Send("{\"type\":\"response.paused\",\"turn_id\":1,\"response_id\":\"r1\"}");
            if (!rig.client.ResponsePaused) result.notes.Add("실패: 구형 서버에서 즉시 멈추지 않았다");
            for (int i = 0; i < 60; i++) rig.Step();
            if (rig.client.PauseAckCount != 0)
                result.notes.Add("실패: 서버가 모르는 보고를 보냈다 " + rig.client.PauseAckReason);

            rig.Resumed();
            rig.player.MarkComplete();
            for (int i = 0; i < 60; i++) rig.Step();
            if (rig.client.ResponsePaused) result.notes.Add("실패: 재개되지 않았다");
            if (!rig.renderer.Ended) result.notes.Add("실패: 재개 뒤 끝까지 나가지 않았다");
            CompareWaveform(result, source, rig.capture.rendered, 48000);
        }
        return Finish(result);
    }

    /// <summary>초기화는 예약·경계·PCM 을 모두 지우고, 다음 응답으로 새지 않는다.</summary>
    static CaseResult ClientResetClears()
    {
        var result = new CaseResult { name = "client_reset_clears_schedule", outputRate = 48000 };
        using (var rig = new ClientRig(48000))
        {
            rig.Audio(Constant(Phrase, .5f));
            rig.Audio(Constant(Phrase, -.5f));
            rig.Boundary(Phrase, 7);
            rig.Step();
            rig.Paused(1, 1200, "phrase");
            rig.Send("{\"type\":\"reset.done\",\"turn_id\":1}");

            if ((int)rig.Get("_pauseId") != 0) result.notes.Add("실패: 정지 예약이 남았다");
            if (rig.Boundaries() != 0) result.notes.Add("실패: 경계 큐가 남았다");
            if (rig.renderer.Received != 0) result.notes.Add("실패: 이전 응답의 PCM 이 남았다");
            for (int i = 0; i < 60; i++) rig.Step();
            if (rig.client.PauseAckCount != 0)
                result.notes.Add("실패: 초기화 뒤 늦은 보고가 나갔다 " + rig.client.PauseAckReason);

            // 새 응답에서는 같은 계약이 처음부터 다시 성립해야 한다.
            rig.NewResponse("r2");
            rig.Audio(Constant(Phrase, .5f));
            rig.Audio(Constant(Phrase, -.5f));
            rig.Boundary(Phrase, 5);
            rig.Step();
            rig.Paused(1, 1200, "phrase");
            for (int i = 0; i < 400 && rig.client.PauseAckCount == 0; i++) rig.Step();
            if (rig.client.PauseAckCount != 1) result.notes.Add("실패: 새 응답에서 보고하지 않았다");
            if (rig.client.PauseAckReason != "boundary")
                result.notes.Add("실패: 새 응답의 보고 이유가 " + rig.client.PauseAckReason + " 다");
            if ((int)rig.Get("_playedChars") != 5)
                result.notes.Add("실패: 새 응답의 전달 글자 수가 " + rig.Get("_playedChars") + " 다");
        }
        return Finish(result);
    }

    // --- 도구 ---

    class Capture
    {
        public List<float> raw = new List<float>();
        public List<float> rendered = new List<float>();
    }

    /// <summary>실제 DialogueVoiceClient·DialogueAudioPlayer·DialogueAudioRenderer 를 그대로 쓴다.
    ///
    /// 렌더러는 AudioSource 가 없는 GameObject 에 둔다. Unity 가 콜백을 자동으로 부르지 않으므로
    /// 소리가 나지 않고 검사가 콜백을 직접 부른다. 클라이언트는 RequireComponent 때문에 자기
    /// AudioSource 를 갖는 별도 GameObject 에 두고, 그 AudioSource 는 꺼 둔다.</summary>
    sealed class ClientRig : IDisposable
    {
        static readonly Type EventType =
            typeof(DialogueVoiceClient).GetNestedType("DialogueEvent", BindingFlags.NonPublic);
        static readonly MethodInfo Handle = typeof(DialogueVoiceClient).GetMethod(
            "HandleDialogueEvent", BindingFlags.Instance | BindingFlags.NonPublic);
        static readonly MethodInfo Pump = typeof(DialogueVoiceClient).GetMethod(
            "UpdatePlayback", BindingFlags.Instance | BindingFlags.NonPublic);

        public readonly GameObject audioHost, clientHost;
        public readonly DialogueVoiceClient client;
        public readonly DialogueAudioRenderer renderer;
        public readonly DialogueAudioPlayer player;
        public readonly Capture capture = new Capture();
        public float clock;
        readonly int _rate;
        string _response = "r1";

        public ClientRig(int rate)
        {
            if (EventType == null || Handle == null || Pump == null)
                throw new Exception("클라이언트 내부 구조를 찾지 못했습니다.");
            _rate = rate;
            audioHost = new GameObject("Dialogue Pause Renderer") { hideFlags = HideFlags.DontSave };
            renderer = audioHost.AddComponent<DialogueAudioRenderer>();
            renderer.Configure(0, 0);
            player = new DialogueAudioPlayer(renderer, rate, Drain);
            renderer.Configure(0, 0);   // 생성자가 실제 선버퍼로 되돌리므로 다시 0 으로 둔다
            renderer.ForceOutputRate(rate);

            clientHost = new GameObject("Dialogue Pause Client") { hideFlags = HideFlags.DontSave };
            client = clientHost.AddComponent<DialogueVoiceClient>();
            var source = clientHost.GetComponent<AudioSource>();
            if (source != null) { source.playOnAwake = false; source.mute = true; source.enabled = false; }
            Set("_playback", player);
            Set("_responseId", _response);
            Set("_responseTurn", 1);
            Set("_dialogueTurn", 1);
            DialogueVoiceClient.EditorClock = () => clock;
        }

        static FieldInfo Field(string name) =>
            typeof(DialogueVoiceClient).GetField(name, BindingFlags.Instance | BindingFlags.NonPublic);
        void Set(string name, object value) => Field(name).SetValue(client, value);
        public object Get(string name) => Field(name).GetValue(client);
        public int Boundaries() =>
            ((System.Collections.ICollection)Field("_boundaries").GetValue(client)).Count;

        public void Send(string json) =>
            Handle.Invoke(client, new object[] { JsonUtility.FromJson(json, EventType) });
        public void Frame() => Pump.Invoke(client, null);

        public void NewResponse(string id)
        {
            _response = id;
            Set("_responseId", id);
            Set("_responseTurn", 1);
            Set("_dialogueTurn", 1);
        }

        public void Audio(float[] samples)
        {
            // 실제 서버처럼 PCM 패킷당 200 ms 이하로 보낸다.
            var pcm = Encode(samples);
            for (int offset = 0; offset < pcm.Length; offset += 9600)
            {
                int size = Math.Min(9600, pcm.Length - offset);
                Send("{\"type\":\"response.audio\",\"turn_id\":1,\"response_id\":\"" + _response +
                    "\",\"format\":\"pcm_s16le\",\"sample_rate\":24000,\"channels\":1,\"pcm\":\"" +
                    Convert.ToBase64String(pcm, offset, size) + "\"}");
            }
        }

        public void Boundary(long samples, int chars) => Send(
            "{\"type\":\"audio.boundary\",\"turn_id\":1,\"response_id\":\"" + _response +
            "\",\"samples\":" + samples + ",\"text_chars\":" + chars + "}");

        public void Paused(int id, int graceMs, string mode) => Send(
            "{\"type\":\"response.paused\",\"turn_id\":1,\"response_id\":\"" + _response +
            "\",\"pause_id\":" + id + ",\"grace_ms\":" + graceMs + ",\"pause_mode\":\"" + mode + "\"}");

        public void Resumed() => Send(
            "{\"type\":\"response.resumed\",\"turn_id\":1,\"response_id\":\"" + _response + "\"}");

        /// <summary>한 프레임: 갱신 → 오디오 콜백 한 블록 → 시계 전진.</summary>
        public void Step(int block = 512, bool render = true)
        {
            Frame();
            if (render) DriveBlock(renderer, block, capture);
            clock += (float)block / _rate;
        }

        public void Dispose()
        {
            DialogueVoiceClient.EditorClock = null;
            player?.Dispose();
            if (clientHost != null) UnityEngine.Object.DestroyImmediate(clientHost);
            if (audioHost != null) UnityEngine.Object.DestroyImmediate(audioHost);
        }
    }

    static DialogueAudioRenderer Attach(GameObject host, int rate)
    {
        var renderer = host.AddComponent<DialogueAudioRenderer>();
        renderer.ForceOutputRate(rate);
        renderer.Configure(0, 0);   // 선버퍼 대기를 빼고 정지 계약만 본다
        renderer.ResetDiagnostics();
        return renderer;
    }

    /// <summary>생산 콜백을 한 블록 직접 부른다. 앞부분 produced 프레임만 실제 출력이다.</summary>
    static void DriveBlock(DialogueAudioRenderer renderer, int block, Capture capture)
    {
        if (_callback == null)
            _callback = typeof(DialogueAudioRenderer).GetMethod(
                "OnAudioFilterRead", BindingFlags.Instance | BindingFlags.NonPublic);
        if (_callback == null) throw new Exception("OnAudioFilterRead 를 찾지 못했습니다.");
        var buffer = new float[block * 2];
        long before = renderer.Snapshot().renderedFrames;
        _callback.Invoke(renderer, new object[] { buffer, 2 });
        long produced = renderer.Snapshot().renderedFrames - before;
        if (produced < 0 || produced > block)
            throw new Exception("블록당 렌더 프레임이 " + produced + " 입니다.");
        for (int frame = 0; frame < block; frame++)
        {
            capture.raw.Add(buffer[frame * 2]);
            if (frame < produced) capture.rendered.Add(buffer[frame * 2]);
        }
    }

    /// <summary>경계 앞에서 나갈 수 있는 출력 프레임 수. 프레임 k 는 원본 시간 k*24000/rate 의
    /// 두 이웃 표본을 읽으므로, 뒤쪽 표본이 경계에 닿으면 그 프레임은 나가지 못한다.</summary>
    static long FramesBefore(long target, int rate)
    {
        double step = (double)Rate / rate;
        long k = 0;
        while ((long)Math.Floor(k * step) + 1 < target) k++;
        return k;
    }

    static float[] TwoPhrases()
    {
        var value = new float[Phrase * 2];
        for (int i = 0; i < Phrase; i++) { value[i] = .5f; value[Phrase + i] = -.5f; }
        return value;
    }

    static float[] Constant(int samples, float level)
    {
        var value = new float[samples];
        for (int i = 0; i < samples; i++) value[i] = level;
        return value;
    }

    static byte[] Encode(float[] source)
    {
        var pcm = new byte[source.Length * 2];
        for (int i = 0; i < source.Length; i++)
        {
            short encoded = (short)(Mathf.Clamp(source[i], -1f, 1f) * 32767f);
            pcm[i * 2] = (byte)encoded;
            pcm[i * 2 + 1] = (byte)(encoded >> 8);
        }
        return pcm;
    }

    /// <summary>PCM16 왕복을 거친 뒤 렌더러가 실제로 보게 되는 값.</summary>
    static float Quantise(float value) => (short)(Mathf.Clamp(value, -1f, 1f) * 32767f) / 32768f;

    /// <summary>렌더된 프레임을 기대 파형과 맞춘다. 기대값은 렌더러 코드를 재사용하지 않고
    /// 여기서 다시 계산한다. 정지·재개로 표본을 잃거나 겹쳤는지 이 비교가 잡는다.</summary>
    static void CompareWaveform(CaseResult result, float[] source, List<float> rendered, int rate)
    {
        double step = (double)Rate / rate;
        long expected = (long)Math.Ceiling(source.Length / step - 1e-9);
        if (rendered.Count != expected)
        {
            result.notes.Add("실패: 렌더 " + rendered.Count + " != 기대 " + expected);
            return;
        }
        var quantised = new float[source.Length];
        for (int i = 0; i < source.Length; i++) quantised[i] = Quantise(source[i]);
        for (int k = 0; k < expected; k++)
        {
            double t = k * step;
            long index = (long)Math.Floor(t);
            float a = index < quantised.Length ? quantised[index] : 0f;
            float b = index + 1 < quantised.Length ? quantised[index + 1] : 0f;
            float want = a + (b - a) * (float)(t - index);
            if (Mathf.Abs(rendered[k] - want) > Tolerance)
            {
                result.notes.Add("실패: " + k + "번째 프레임이 기대 파형과 다르다");
                return;
            }
        }
    }

    static CaseResult Finish(CaseResult result)
    {
        result.passed = result.notes.Count == 0;
        if (result.passed) result.notes.Add("통과");
        return result;
    }

    static void Write(Report report)
    {
        string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", FallbackOutput));
        try
        {
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            for (int i = 2; File.Exists(path) && i < 1000; i++)
                path = Path.GetFullPath(Path.Combine(Application.dataPath, "..",
                    FallbackOutput.Replace(".json", "-" + i + ".json")));
            report.outputPath = path;
            File.WriteAllText(path, JsonUtility.ToJson(report, true), new UTF8Encoding(false));
        }
        catch (Exception e)
        {
            report.passed = false;
            Debug.LogError("[PauseBoundaryCheck] 결과 파일을 쓰지 못했습니다: " + path + "\n" + e);
        }
        string summary = "[PauseBoundaryCheck] passed=" + report.passed + " cases=" + report.cases +
                         " failed=" + report.failed + " out=" + report.outputPath + "\n" + report.error;
        if (report.passed) Debug.Log(summary);
        else Debug.LogError(summary);
    }
}
