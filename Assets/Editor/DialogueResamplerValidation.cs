// DialogueResamplerValidation.cs
// 생산 코드인 DialogueAudioRenderer 의 리샘플 코어를 결정적으로 검증한다.
//
// AudioSource 가 없는 GameObject 에 렌더러만 올리면 Unity 가 콜백을 자동으로 부르지
// 않는다. private OnAudioFilterRead 를 reflection 으로 직접 불러 블록 단위로 돌리므로
// 스케줄러·장치와 무관하게 같은 결과가 나온다. 스텁이 아니라 실제 생산 코어다.
//
// 기대 파형은 이 파일에서 독립적으로 다시 계산한다(렌더러 코드를 재사용하지 않는다).
// 씬·서버·마이크·등록 인물을 건드리지 않고 자기 GameObject 만 만들었다 지운다.
//
// 굶은 구간을 건너뛸 때 진폭으로 무음을 지우면 파형 안의 정상 0 샘플까지 지워져
// 거짓 통과가 난다(0 을 피하는 합성 파형으로 바꾸는 것도 같은 회피다). 여기서는
// 콜백 앞뒤의 renderedFrames 차이로 "이번 블록에서 실제로 렌더된 앞부분 프레임"만
// 위치까지 따져 모은다. 정상 0 은 남고 굶은·멈춘 무음만 빠진다.

using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEditor;
using UnityEngine;

public static class DialogueResamplerValidation
{
    const string FallbackOutput = "tools/_work/audio_incident_20260916/resampler-core.json";
    const int Rate = DialogueAudioRenderer.SourceRate;
    const float Tolerance = 1e-4f;
    const float Silence = 1e-6f;

    // 생산 DialogueAudioPlayer 와 같은 선버퍼 설정. 이 조건에서만 드러나는 상태 버그가 있다.
    const int RealPreroll = Rate * 160 / 1000;    // 3840 샘플
    const int RealRebuffer = Rate * 80 / 1000;    // 1920 샘플

    [Serializable]
    class CaseResult
    {
        public string name = "";
        public bool passed;
        public int outputRate, channels, inputSamples;
        public int blockFrames, prerollSamples, rebufferSamples;
        public long expectedFrames, renderedFrames, consumed, received;
        public float maxDeviation;
        public int firstBadFrame = -1;
        public long trailingNonZeroFrames;    // 기대 길이 뒤에 남은 신호. DC tail 검출
        public long afterEndNonZeroFrames;    // 마지막 렌더 프레임 뒤에 남은 신호
        public bool ended;
        public long underruns, rebuffers, preOutputConsumed;
        public List<string> notes = new List<string>();
    }

    [Serializable]
    class Report
    {
        public bool passed;
        public string time = "", unityVersion = "", error = "";
        public string scope =
            "AudioSource 없이 생산 렌더러의 OnAudioFilterRead 를 직접 불러 잰 결정적 검사다. " +
            "합성 파형이며 실제 TTS PCM·사람의 청취 판정이 아니다.";
        public int cases, failed;
        public List<CaseResult> results = new List<CaseResult>();
        public List<string> limits = new List<string>();
        public string outputPath = "";
    }

    [MenuItem("Tools/Dialogue/Run resampler core check", priority = 22)]
    public static void Run()
    {
        var report = new Report { time = DateTime.UtcNow.ToString("O"), unityVersion = Application.unityVersion };
        GameObject host = null;
        try
        {
            // AudioSource 를 붙이지 않는다. 그래야 자동 콜백이 끼어들지 않는다.
            host = new GameObject("Dialogue Resampler Check") { hideFlags = HideFlags.DontSave };

            foreach (int rate in new[] { 48000, 44100 })
            {
                foreach (int samples in new[] { 0, 1, 2, 1920, 4800 })
                    report.results.Add(Straight(host, rate, samples, 2));
                report.results.Add(Straight(host, rate, 1920, 1));
                report.results.Add(Straight(host, rate, 1920, 6));
                // 블록 크기를 바꿔도 같은 결과여야 한다. 경계에서 프레임을 먹거나 겹치지 않는지 본다.
                report.results.Add(Straight(host, rate, 1920, 2, block: 17));
                report.results.Add(Straight(host, rate, 1920, 2, block: 511));
                report.results.Add(MidPacket(host, rate));
                report.results.Add(PausedMidStream(host, rate));
                report.results.Add(StarveThenResume(host, rate));
                report.results.Add(StopThenOther(host, rate));

                // 실제 선버퍼(160ms/80ms)에서의 완료 경로. 코어 상태 버그는 여기서만 드러난다.
                report.results.Add(EmptyCompletion(host, rate));
                report.results.Add(ExhaustedThenComplete(host, rate));
                report.results.Add(RealPrerollStraight(host, rate, 1920, "short_final_80ms"));
                report.results.Add(RealPrerollStraight(host, rate, 1, "one_sample"));
                report.results.Add(RealPrerollStraight(host, rate, 2, "two_samples"));
                report.results.Add(PausedThenComplete(host, rate));
            }

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
            if (host != null) UnityEngine.Object.DestroyImmediate(host);
            report.limits.Add("합성 파형 검사다. 실제 TTS PCM 과 사람의 청취 판정이 아니다.");
            report.limits.Add("선형 보간의 고주파 왜곡은 여기서 판정하지 않는다. 수치 일치만 본다.");
            report.limits.Add("실제 오디오 장치·DSP 버퍼·스레드 경합은 이 검사 범위 밖이다.");
            report.limits.Add("콜백을 직접 부르므로 오디오 스레드 경합과 잠금 대기는 재현하지 않는다.");
            Write(report);
        }
    }

    // --- 시나리오 ---

    /// <summary>N 샘플을 한 번에 넣고 완료. 정확한 길이·파형·꼬리 무음을 본다.</summary>
    static CaseResult Straight(GameObject host, int rate, int samples, int channels, int block = 1024)
    {
        string name = "straight_" + rate + "_" + samples + "s_" + channels + "ch" +
                      (block == 1024 ? "" : "_block" + block);
        var result = Start(rate, samples, channels, name);
        var renderer = Attach(host, rate, 0, 0, result);
        try
        {
            float[] source = Tone(samples, 0);
            if (samples > 0) renderer.Write(Encode(source), samples * 2);
            renderer.Begin();
            renderer.MarkComplete();
            var capture = Drive(renderer, channels, Expected(samples, rate) + 4096, block, result);
            Compare(result, renderer, source, capture, rate);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    /// <summary>여러 조각으로 나눠 넣어도 경계에서 샘플이 잘리거나 겹치지 않아야 한다.</summary>
    static CaseResult MidPacket(GameObject host, int rate)
    {
        int total = 1920 + 480 + 37;
        var result = Start(rate, total, 2, "mid_packet_" + rate);
        var renderer = Attach(host, rate, 0, 0, result);
        try
        {
            float[] source = Tone(total, 0);
            int at = 0;
            foreach (int chunk in new[] { 1920, 480, 37 })
            {
                var slice = new float[chunk];
                Array.Copy(source, at, slice, 0, chunk);
                renderer.Write(Encode(slice), chunk * 2);
                at += chunk;
            }
            renderer.Begin();
            renderer.MarkComplete();
            var capture = Drive(renderer, 2, Expected(total, rate) + 4096, 1024, result);
            Compare(result, renderer, source, capture, rate);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    /// <summary>일시정지 동안 소비가 멈추고 무음이 나가며, 재개 뒤 이어져야 한다.</summary>
    static CaseResult PausedMidStream(GameObject host, int rate)
    {
        int total = 4800;
        var result = Start(rate, total, 2, "paused_" + rate);
        var renderer = Attach(host, rate, 0, 0, result);
        try
        {
            float[] source = Tone(total, 0);
            renderer.Write(Encode(source), total * 2);
            renderer.Begin();
            renderer.MarkComplete();
            var capture = new Capture();
            Drive(renderer, 2, 2048, 1024, result, capture);
            long consumedBefore = renderer.Consumed;

            renderer.SetPaused(true);
            var during = Drive(renderer, 2, 4096, 1024, result);
            CheckPausedWindow(result, renderer, during, consumedBefore);

            renderer.SetPaused(false);
            Drive(renderer, 2, Expected(total, rate) + 4096, 1024, result, capture);
            Compare(result, renderer, source, capture, rate, useRendered: true);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    /// <summary>공급이 끊겼다 다시 들어와도 외삽·중복·손실이 없어야 한다.</summary>
    static CaseResult StarveThenResume(GameObject host, int rate)
    {
        int first = 1000, second = 920;
        var result = Start(rate, first + second, 2, "starve_resume_" + rate);
        var renderer = Attach(host, rate, 240, 240, result);   // 짧은 선버퍼로 바로 시작하게 한다
        try
        {
            float[] source = Tone(first + second, 0);
            var head = new float[first];
            Array.Copy(source, 0, head, 0, first);
            renderer.Write(Encode(head), first * 2);
            renderer.Begin();
            var capture = new Capture();
            // 공급보다 길게 돌려 굶긴다. 완료 표시는 아직 하지 않는다.
            Drive(renderer, 2, Expected(first, rate) + 4096, 1024, result, capture);
            var starved = renderer.Snapshot();
            if (starved.rebuffers == 0) result.notes.Add("실패: 공급이 끊겼는데 재버퍼가 잡히지 않았다");
            if (starved.consumed != first)
                result.notes.Add("실패: 굶은 뒤 소비 " + starved.consumed + " != " + first);
            if (starved.ended) result.notes.Add("실패: 완료 표시 전인데 ended 가 섰다");

            var tail = new float[second];
            Array.Copy(source, first, tail, 0, second);
            renderer.Write(Encode(tail), second * 2);
            renderer.MarkComplete();
            Drive(renderer, 2, Expected(first + second, rate) + 8192, 1024, result, capture);

            // 굶은 구간의 무음만 빠진 실제 렌더 프레임을 이어 붙여 기대 파형과 맞춘다.
            Compare(result, renderer, source, capture, rate, useRendered: true);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    /// <summary>Stop 뒤 다른 PCM 을 넣으면 앞의 잔여가 섞이지 않아야 한다.</summary>
    static CaseResult StopThenOther(GameObject host, int rate)
    {
        int total = 960;
        var result = Start(rate, total, 2, "stop_then_other_" + rate);
        var renderer = Attach(host, rate, 0, 0, result);
        try
        {
            renderer.Write(Encode(Tone(4800, 0)), 4800 * 2);
            renderer.Begin();
            Drive(renderer, 2, 2048, 1024, result);
            renderer.Reset();               // Stop 과 같은 경로
            // Reset 은 버퍼·소비를 되돌리지만 누적 진단(renderedFrames 등)은 일부러 남긴다.
            // 생산의 누적 의미는 그대로 두고, Stop 뒤 구간만 보려면 검사에서 진단을 함께 되돌린다.
            renderer.ResetDiagnostics();
            renderer.ForceOutputRate(rate);

            float[] source = Constant(total, .5f);   // 앞의 톤과 뚜렷이 다른 값
            renderer.Write(Encode(source), total * 2);
            renderer.Begin();
            renderer.MarkComplete();
            var capture = Drive(renderer, 2, Expected(total, rate) + 4096, 1024, result);
            Compare(result, renderer, source, capture, rate);
            if (renderer.Received != total)
                result.notes.Add("실패: Stop 뒤 received 가 " + renderer.Received + " 로 남았다");
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    /// <summary>실제 선버퍼에서 한 샘플도 없이 완료되면 대기하지 않고 바로 끝나야 한다.</summary>
    static CaseResult EmptyCompletion(GameObject host, int rate)
    {
        var result = Start(rate, 0, 2, "empty_completion_" + rate);
        var renderer = Attach(host, rate, RealPreroll, RealRebuffer, result);
        try
        {
            renderer.Begin();
            renderer.MarkComplete();
            var capture = Drive(renderer, 2, 4096, 1024, result);
            if (!renderer.Ended)
                result.notes.Add("실패: 빈 완료인데 ended 가 서지 않았다(선버퍼 대기에 갇혔다)");
            if (capture.rendered.Count != 0)
                result.notes.Add("실패: 낼 것이 없는데 " + capture.rendered.Count + "프레임을 렌더했다");
            Compare(result, renderer, new float[0], capture, rate);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    /// <summary>공급이 먼저 끊겨 버퍼가 빈 뒤에 완료 표시가 오는 실제 응답 끝 상황.
    /// 재버퍼 대기에 갇히면 마지막 종료가 영원히 나지 않는다.</summary>
    static CaseResult ExhaustedThenComplete(GameObject host, int rate)
    {
        int total = 4800;   // 선버퍼(3840)보다 길어 바로 시작한다
        var result = Start(rate, total, 2, "exhausted_then_complete_" + rate);
        var renderer = Attach(host, rate, RealPreroll, RealRebuffer, result);
        try
        {
            float[] source = Tone(total, 0);
            renderer.Write(Encode(source), total * 2);
            renderer.Begin();
            var capture = new Capture();
            Drive(renderer, 2, Expected(total, rate) + 4096, 1024, result, capture);

            var starved = renderer.Snapshot();
            if (starved.consumed != total)
                result.notes.Add("실패: 완료 전 소비 " + starved.consumed + " != " + total);
            if (starved.buffered != 0)
                result.notes.Add("실패: 완료 전 버퍼가 비지 않았다 " + starved.buffered);
            if (starved.ended) result.notes.Add("실패: 완료 표시 전인데 ended 가 섰다");

            renderer.MarkComplete();   // 버퍼가 빈 상태에서 도착한 response.done
            Drive(renderer, 2, 8192, 1024, result, capture);
            if (!renderer.Ended)
                result.notes.Add("실패: 버퍼가 빈 뒤 완료 표시가 왔는데 종료되지 않았다(재버퍼 대기에 갇혔다)");
            Compare(result, renderer, source, capture, rate, useRendered: true);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    /// <summary>실제 선버퍼보다 짧은 조각·한두 샘플도 완료 표시만으로 끝까지 나가야 한다.</summary>
    static CaseResult RealPrerollStraight(GameObject host, int rate, int samples, string label)
    {
        var result = Start(rate, samples, 2, label + "_" + rate);
        var renderer = Attach(host, rate, RealPreroll, RealRebuffer, result);
        try
        {
            float[] source = Tone(samples, 0);
            renderer.Write(Encode(source), samples * 2);
            renderer.Begin();
            renderer.MarkComplete();
            var capture = Drive(renderer, 2, Expected(samples, rate) + 4096, 1024, result);
            if (!renderer.Ended)
                result.notes.Add("실패: 선버퍼보다 짧은 조각이 끝나지 않았다");
            Compare(result, renderer, source, capture, rate);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    /// <summary>일시정지 중에 완료 표시가 와도 그때는 나가지 않고, 재개한 뒤 끝까지 나가야 한다.</summary>
    static CaseResult PausedThenComplete(GameObject host, int rate)
    {
        int total = 4800;
        var result = Start(rate, total, 2, "paused_then_complete_" + rate);
        var renderer = Attach(host, rate, RealPreroll, RealRebuffer, result);
        try
        {
            float[] source = Tone(total, 0);
            renderer.Write(Encode(source), total * 2);
            renderer.Begin();
            var capture = new Capture();
            Drive(renderer, 2, 1024, 1024, result, capture);
            long consumedBefore = renderer.Consumed;
            if (consumedBefore == 0) result.notes.Add("실패: 일시정지 전에 아무것도 소비하지 않았다");

            renderer.SetPaused(true);
            renderer.MarkComplete();       // 멈춰 있는 동안 도착한 response.done
            var during = Drive(renderer, 2, 4096, 1024, result);
            CheckPausedWindow(result, renderer, during, consumedBefore);
            if (renderer.Ended) result.notes.Add("실패: 일시정지 중에 종료로 처리됐다");

            renderer.SetPaused(false);
            Drive(renderer, 2, Expected(total, rate) + 4096, 1024, result, capture);
            if (!renderer.Ended) result.notes.Add("실패: 재개 뒤에도 종료되지 않았다");
            Compare(result, renderer, source, capture, rate, useRendered: true);
        }
        finally { UnityEngine.Object.DestroyImmediate(renderer); }
        return result;
    }

    // --- 도구 ---

    static CaseResult Start(int rate, int samples, int channels, string name) =>
        new CaseResult { name = name, outputRate = rate, inputSamples = samples, channels = channels };

    static DialogueAudioRenderer Attach(GameObject host, int rate, int preroll, int rebuffer, CaseResult result)
    {
        var renderer = host.AddComponent<DialogueAudioRenderer>();
        renderer.ForceOutputRate(rate);
        renderer.Configure(preroll, rebuffer);   // 0,0 이면 선버퍼 대기를 빼고 코어만 본다
        renderer.ResetDiagnostics();
        result.prerollSamples = preroll;
        result.rebufferSamples = rebuffer;
        return renderer;
    }

    static long Expected(int samples, int rate)
    {
        if (samples <= 0) return 0;
        double step = (double)Rate / rate;
        return (long)Math.Ceiling(samples / step - 1e-9);
    }

    static float[] Tone(int samples, int phase)
    {
        var value = new float[samples];
        for (int i = 0; i < samples; i++)
            value[i] = Mathf.Sin(2f * Mathf.PI * 300f * (phase + i) / Rate) * .6f;
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
    static float[] Quantise(float[] source)
    {
        var value = new float[source.Length];
        for (int i = 0; i < source.Length; i++)
            value[i] = (short)(Mathf.Clamp(source[i], -1f, 1f) * 32767f) / 32768f;
        return value;
    }

    /// <summary>블록별 출력. raw 는 콜백이 채운 전부, rendered 는 실제로 렌더된 앞부분만이다.</summary>
    class Capture
    {
        public List<float> raw = new List<float>();
        public List<float> rendered = new List<float>();
        public long lastRenderedIndex = -1;   // raw 기준 위치
    }

    static MethodInfo _callback;

    /// <summary>블록 단위로 생산 콜백을 직접 부른다. 모노 값이므로 첫 채널만 모은다.
    /// 콜백 앞뒤 renderedFrames 차이가 이번 블록에서 실제 렌더된 앞부분 프레임 수다.
    /// 렌더러는 멈춘 지점부터 블록 끝까지 0 으로 채우므로 앞부분만 실제 출력이다.</summary>
    static Capture Drive(DialogueAudioRenderer renderer, int channels, long frames, int block,
                         CaseResult result, Capture into = null)
    {
        if (_callback == null)
            _callback = typeof(DialogueAudioRenderer).GetMethod(
                "OnAudioFilterRead", BindingFlags.Instance | BindingFlags.NonPublic);
        if (_callback == null) throw new Exception("OnAudioFilterRead 를 찾지 못했습니다.");
        if (block <= 0) throw new Exception("블록 크기가 0 이하입니다.");
        result.blockFrames = block;

        var capture = into ?? new Capture();
        var buffer = new float[block * channels];
        var arguments = new object[] { buffer, channels };
        long done = 0;
        while (done < frames)
        {
            Array.Clear(buffer, 0, buffer.Length);
            long before = renderer.Snapshot().renderedFrames;
            _callback.Invoke(renderer, arguments);
            long produced = renderer.Snapshot().renderedFrames - before;
            if (produced < 0 || produced > block)
                throw new Exception("블록당 렌더 프레임이 " + produced + " 입니다(0~" + block + " 이어야 한다).");

            for (int frame = 0; frame < block; frame++)
            {
                float first = buffer[frame * channels];
                for (int channel = 1; channel < channels; channel++)
                    if (Mathf.Abs(buffer[frame * channels + channel] - first) > 1e-6f)
                        throw new Exception("채널 값이 서로 다릅니다.");
                capture.raw.Add(first);
                if (frame < produced)
                {
                    capture.rendered.Add(first);
                    capture.lastRenderedIndex = capture.raw.Count - 1;
                }
            }
            done += block;
        }
        return capture;
    }

    /// <summary>일시정지 구간: 소비가 멈추고 렌더 없이 무음만 나가야 한다.</summary>
    static void CheckPausedWindow(CaseResult result, DialogueAudioRenderer renderer,
                                  Capture during, long consumedBefore)
    {
        if (renderer.Consumed != consumedBefore)
            result.notes.Add("실패: 일시정지 중 소비가 " + (renderer.Consumed - consumedBefore) + "샘플 늘었다");
        if (during.rendered.Count != 0)
            result.notes.Add("실패: 일시정지 중 " + during.rendered.Count + "프레임을 렌더했다");
        foreach (float value in during.raw)
            if (Mathf.Abs(value) > Silence) { result.notes.Add("실패: 일시정지 중 무음이 아니다"); break; }
    }

    /// <summary>독립적으로 계산한 기대 파형과 맞춘다. 길이·꼬리 무음·소비도 함께 본다.
    /// useRendered 면 굶은·멈춘 구간의 무음을 뺀 실제 렌더 프레임만 이어 비교한다.</summary>
    static void Compare(CaseResult result, DialogueAudioRenderer renderer, float[] source,
                        Capture capture, int rate, bool useRendered = false)
    {
        var snapshot = renderer.Snapshot();
        result.renderedFrames = snapshot.renderedFrames;
        result.consumed = snapshot.consumed;
        result.received = snapshot.received;
        result.ended = snapshot.ended;
        result.underruns = snapshot.underruns;
        result.rebuffers = snapshot.rebuffers;
        result.preOutputConsumed = snapshot.preOutputConsumed;
        result.expectedFrames = Expected(source.Length, rate);

        if (snapshot.consumed != source.Length)
            result.notes.Add("실패: 소비 " + snapshot.consumed + " != 입력 " + source.Length);
        if (snapshot.renderedFrames != result.expectedFrames)
            result.notes.Add("실패: 렌더 " + snapshot.renderedFrames + " != 기대 " + result.expectedFrames);
        if (source.Length > 0 && !snapshot.ended)
            result.notes.Add("실패: 끝났는데 ended 표시가 없다");
        // 보간 시드로 첫 출력 전에 최대 2샘플을 읽는다. 그보다 많으면 선읽기다.
        if (snapshot.preOutputConsumed > 2)
            result.notes.Add("실패: 첫 출력 전 소비가 " + snapshot.preOutputConsumed + "샘플이다(시드는 2 이하)");

        var stream = useRendered ? capture.rendered : capture.raw;

        // 기대 파형: s[i], s[i+1] 선형 보간. 범위를 넘으면 0 으로 내려간다.
        float[] quantised = Quantise(source);
        double step = (double)Rate / rate;
        float worst = 0f;
        int limit = (int)Math.Min(result.expectedFrames, stream.Count);
        for (int k = 0; k < limit; k++)
        {
            double t = k * step;
            long index = (long)Math.Floor(t);
            float fraction = (float)(t - index);
            float a = index < quantised.Length ? quantised[index] : 0f;
            float b = index + 1 < quantised.Length ? quantised[index + 1] : 0f;
            float expected = a + (b - a) * fraction;
            float deviation = Mathf.Abs(stream[k] - expected);
            if (deviation > worst) { worst = deviation; }
            if (deviation > Tolerance && result.firstBadFrame < 0) result.firstBadFrame = k;
        }
        result.maxDeviation = worst;
        if (result.firstBadFrame >= 0)
            result.notes.Add("실패: " + result.firstBadFrame + "번째 프레임부터 기대 파형과 다르다");
        if (stream.Count < result.expectedFrames)
            result.notes.Add("실패: 출력이 기대 길이보다 짧다 " + stream.Count);

        if (useRendered)
        {
            // 렌더 프레임만 모았으므로 길이가 기대와 정확히 같아야 한다.
            if (stream.Count != result.expectedFrames)
                result.notes.Add("실패: 렌더 프레임 " + stream.Count + " != 기대 " + result.expectedFrames);
        }
        else
        {
            // 기대 길이 뒤에는 신호가 남으면 안 된다. 무한 DC tail 검출.
            long tail = 0;
            for (int k = (int)result.expectedFrames; k < stream.Count; k++)
                if (Mathf.Abs(stream[k]) > Silence) tail++;
            result.trailingNonZeroFrames = tail;
            if (tail > 0) result.notes.Add("실패: 기대 길이 뒤에 신호가 " + tail + "프레임 남았다");
        }

        // 마지막 렌더 프레임 뒤는 위치까지 따져 전부 0 이어야 한다.
        long after = 0;
        for (int k = (int)(capture.lastRenderedIndex + 1); k < capture.raw.Count; k++)
            if (Mathf.Abs(capture.raw[k]) > Silence) after++;
        result.afterEndNonZeroFrames = after;
        if (after > 0) result.notes.Add("실패: 마지막 렌더 뒤에 신호가 " + after + "프레임 남았다");

        result.passed = result.notes.Count == 0;
        if (result.passed) result.notes.Add("통과");
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
            Debug.LogError("[ResamplerCheck] 결과 파일을 쓰지 못했습니다: " + path + "\n" + e);
        }
        string summary = "[ResamplerCheck] passed=" + report.passed + " cases=" + report.cases +
                         " failed=" + report.failed + " out=" + report.outputPath + "\n" + report.error;
        if (report.passed) Debug.Log(summary);
        else Debug.LogError(summary);
    }
}
