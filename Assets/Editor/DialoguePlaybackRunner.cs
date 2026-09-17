// DialoguePlaybackRunner.cs
// 실제 Unity 오디오 콜백에서 DialogueAudioPlayer / DialogueAudioRenderer 를 계측한다.
//
// 이 메뉴는 대화 서버·마이크·등록 인물·씬 오브젝트를 전혀 건드리지 않는다.
// 자기 GameObject 와 AudioSource 만 만들어 쓰고 끝나면 자기 것만 지운다.
// 넣는 소리는 전부 합성 sine/ramp 다. 보낼 시각만 설정 파일의 일정에서 읽는다.
//
// 진행 중인 체험이 있으면 실행을 거부한다. 체험을 끊고 검사하지 않는다.

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;

public static class DialoguePlaybackValidation
{
    const string ConfigPath = ".dialogue-work/playback-diagnostic-config.json";
    const string FallbackOutput = "tools/_work/audio_incident_20260916/playback-diagnostic.json";
    const int Rate = DialogueAudioRenderer.SourceRate;
    const int MaxPacketMillis = 200;         // 9600바이트 = Enqueue 상한
    const int MaxSamples = 3000;             // 시계열 기록 상한
    const int SeedSamples = 2;               // 보간 시드로 첫 출력 전에 읽는 최대 샘플 수

    static bool _running;

    [Serializable]
    class DiagnosticConfig
    {
        public string caseName, outputPath;
        // uniform | schedule | short_final | pause_resume | gap | stop_restart
        public string scenario = "uniform";
        public int packetMillis = 200;
        public int packetCount = 25;
        public int[] sendAtMillis;           // 있으면 이 일정(시작 기준 ms)을 그대로 쓴다
        public float toneHz = 440f;
        public float amplitude = .25f;
        public string waveform = "sine";     // sine | ramp
        // 기본은 들리지 않게 돌린다. 이 검사는 합성 sine/ramp 를 반복 재생하므로 소리가 나면
        // 사람에게 삐 소리로 들린다. 측정은 출력 버퍼의 PCM 을 보는 것이라 음소거와 무관하다.
        // 직접 들어 봐야 할 때만 true 로 둔다.
        public bool audible;
        public int settleMillis = 2000;
        public int actionAtPacket = 8;       // pause/gap/stop 을 넣을 지점
        public int actionMillis = 600;       // 멈춰 있거나 비어 있는 시간
        public int maxInnerSilenceMillis = 30;   // 신호 사이 연속 무음 허용치
        public float minSignalRatio = .99f;      // 기대 대비 실제 신호 프레임 비율 하한
        public float maxSignalRatio = 1.01f;     // 상한. 넘으면 DC tail 등 과잉 출력이다
    }

    [Serializable]
    class Sample
    {
        public float t;
        public long received, consumed, callbacks, underrunFrames;
        public int buffered;
        public bool buffering, paused;
    }

    [Serializable]
    class Report
    {
        public bool passed;                  // 실행이 끝까지 진행됐는가
        public bool qualityPassed;           // 출력 품질 임계값을 통과했는가
        public string error = "", time = "", caseName = "", scenario = "", unityVersion = "";
        public string scope =
            "합성 sine/ramp 를 독립 소유 AudioSource 로 재생한 계측이다. 대화 서버·마이크·등록 인물·" +
            "씬 오브젝트를 쓰지 않았고 실제 사용자 음성도 아니다. 사람의 청취 판정이 아니다. " +
            "기본값(audible=false)에서는 진단용 탭이 원본 PCM 을 센 뒤 출력 버퍼를 0 으로 지운다. " +
            "여기 수치는 전부 지우기 전의 PCM 이고, 실제로 뒤로 나간 것은 무음이다.";
        public int packets, packetSamples, packetMillis;
        public bool scheduleFromConfig;
        public float toneHz, amplitude;
        public string waveform = "";
        public float firstEnqueueAtSec = -1, lastEnqueueAtSec = -1, observedSec;
        public float maxSendGapSec, maxSendLateSec;
        public long pausedConsumedDelta;   // 일시정지 동안 늘어난 소비. 0이어야 한다
        public bool tapAttached;
        // audible 은 설정값이다. sourceMuted 는 진단용 AudioSource 에서 되읽은 실제 mute 값으로,
        // 이제는 평소 false 다(mute 를 쓰면 탭이 PCM 을 못 본다). 소리는 탭이 지워서 막는다.
        // outputMuted 는 둘 중 하나라도 걸려 실제로 무음이 되는가, 즉 효과적 무음 여부다.
        public bool audible, sourceMuted, tapSuppressesOutput, outputMuted;
        public DialogueAudioRenderer.Diagnostics player;
        public DialoguePlaybackTap.Output output;
        // 파생값
        public long enqueuedSamples, missingSamples, expectedSignalFrames;
        public float signalRatio, startLatencySec;
        public float preOutputConsumedSec, underrunSec, innerSilenceSec, trailingSilenceSec;
        // 설정이 일부러 만든 무음(일시정지·공급 중단)과 그것으로 설명되지 않는 무음을 나눈다.
        public float knownSilenceSec, unexplainedInnerSilenceSec;
        public long expectedRenderedFrames;
        public bool tapRequired = true;   // 탭이 없으면 품질은 미검증이다
        public string outputPath = "";
        public List<Sample> timeline = new List<Sample>();
        public List<string> steps = new List<string>();
        public List<string> quality = new List<string>();
        public List<string> limits = new List<string>();
    }

    [MenuItem("Tools/Dialogue/Run playback buffer diagnostic", priority = 21)]
    public static async void Run()
    {
        if (_running) { Debug.LogWarning("[PlaybackDiagnostic] 이미 실행 중입니다."); return; }
        if (!EditorApplication.isPlaying) { Debug.LogWarning("[PlaybackDiagnostic] Play Mode 에서 실행하세요."); return; }

        var report = new Report { time = DateTime.UtcNow.ToString("O"), unityVersion = Application.unityVersion };
        GameObject host = null;
        AudioSource diagnosticSource = null;   // 이 검사가 만든 것. 음소거 상태를 되읽어 기록한다
        DialogueAudioPlayer player = null;
        DialoguePlaybackTap tap = null;
        EditorApplication.CallbackFunction sampler = null;
        string configuredOutput = null;

        _running = true;
        try
        {
            var voice = UnityEngine.Object.FindObjectOfType<DialogueVoiceClient>();
            if (voice != null && (voice.ExperienceActive || voice.DialogueConnected || voice.IsListening))
                throw new Exception("체험이 진행 중입니다. 체험을 끝낸 뒤 실행하세요.");

            string root = ProjectRoot();
            var config = LoadConfig(Path.Combine(root, ConfigPath));
            report.caseName = config.caseName ?? "";
            report.scenario = (config.scenario ?? "uniform").Trim().ToLowerInvariant();
            configuredOutput = string.IsNullOrWhiteSpace(config.outputPath)
                ? Path.Combine(root, FallbackOutput)
                : ResolveInsideProject(root, config.outputPath, "outputPath");

            int[] schedule = BuildSchedule(config, out int packetMillis, report.scenario);
            report.scheduleFromConfig = config.sendAtMillis != null && config.sendAtMillis.Length > 0;
            report.packetMillis = packetMillis;
            report.packets = schedule.Length;
            report.toneHz = config.toneHz;
            report.amplitude = config.amplitude;
            report.waveform = (config.waveform ?? "sine").Trim().ToLowerInvariant();
            int packetSamples = Mathf.Clamp(Rate * packetMillis / 1000, 1, Rate * MaxPacketMillis / 1000);
            report.packetSamples = packetSamples;
            report.steps.Add("설정 확인 · " + report.scenario + " · 패킷 " + schedule.Length + "개 · " +
                             packetMillis + "ms · " + report.waveform);

            host = new GameObject("Dialogue Playback Diagnostic") { hideFlags = HideFlags.DontSave };
            var source = host.AddComponent<AudioSource>();
            // 소리를 죽이는 일은 탭이 한다. AudioSource.mute 로 죽였더니 렌더러의 PCM 이 탭에
            // 닿기 전에 0 이 되어 탭이 무음만 봤다(quiet-fast.json 실측). 여기서는 탭이 원본을
            // 센 뒤 출력 버퍼를 지운다. 건드리는 것은 이 검사가 만든 AudioSource 하나뿐이고
            // 전역 리스너·시스템·에디터 음량과 실제 대화용 AudioSource 는 그대로 둔다.
            report.audible = config.audible;
            diagnosticSource = source;
            source.mute = true;      // 탭이 붙어 막아 줄 때까지는 어떤 소리도 나가지 않게 한다
            player = new DialogueAudioPlayer(source);   // 렌더러가 먼저 붙는다
            tap = host.AddComponent<DialoguePlaybackTap>();   // 탭은 렌더러 뒤에 온다
            // 탭이 없으면 소리를 막을 수단이 없다. PCM 을 보내기 전에 실패로 끝낸다.
            if (tap == null) throw new Exception("출력 탭을 붙이지 못했습니다. 소리를 막을 수 없어 중단합니다.");
            tap.SuppressOutput = !config.audible;
            report.tapAttached = true;
            report.tapSuppressesOutput = tap.SuppressOutput;
            // 탭이 막아 주므로 이 진단용 AudioSource 만 음소거를 푼다. 다른 소스는 건드리지 않는다.
            source.mute = false;
            player.ResetDiagnostics();
            report.steps.Add("독립 AudioSource 준비 · 출력 탭 부착 · " +
                             (report.tapSuppressesOutput ? "탭이 출력을 지움(무음)" : "소리 남(audible=true)"));

            float start = Time.realtimeSinceStartup;
            sampler = () =>
            {
                if (player == null || report.timeline.Count >= MaxSamples) return;
                var now = player.Snapshot();
                report.timeline.Add(new Sample
                {
                    t = Time.realtimeSinceStartup - start,
                    received = now.received, consumed = now.consumed,
                    callbacks = now.callbacks, underrunFrames = now.underrunFrames,
                    buffered = now.buffered, buffering = now.buffering, paused = now.paused,
                });
            };
            EditorApplication.update += sampler;

            int phase = 0;
            float previousSend = -1, drift = 0;
            for (int i = 0; i < schedule.Length; i++)
            {
                float due = start + drift + schedule[i] / 1000f;
                while (Time.realtimeSinceStartup < due) { RequirePlaying(); await Task.Delay(2); }
                RequirePlaying();
                player.Enqueue(BuildPacket(report.waveform, packetSamples, config.toneHz, config.amplitude, ref phase));
                float sentAt = Time.realtimeSinceStartup;
                report.enqueuedSamples += packetSamples;
                if (report.firstEnqueueAtSec < 0) report.firstEnqueueAtSec = sentAt - start;
                report.lastEnqueueAtSec = sentAt - start;
                report.maxSendLateSec = Mathf.Max(report.maxSendLateSec, sentAt - due);
                if (previousSend >= 0) report.maxSendGapSec = Mathf.Max(report.maxSendGapSec, sentAt - previousSend);
                previousSend = sentAt;

                if (i + 1 == config.actionAtPacket)
                {
                    float hold = Mathf.Max(0, config.actionMillis) / 1000f;
                    if (report.scenario == "pause_resume")
                    {
                        player.Pause();
                        long consumedAtPause = player.Snapshot().consumed;
                        report.steps.Add((i + 1) + "번째 패킷 뒤 일시정지 " + config.actionMillis + "ms");
                        await Hold(hold);
                        report.pausedConsumedDelta = player.Snapshot().consumed - consumedAtPause;
                        player.Resume();
                        drift += hold;   // 남은 일정도 그만큼 뒤로 민다
                    }
                    else if (report.scenario == "gap")
                    {
                        report.steps.Add((i + 1) + "번째 패킷 뒤 공급 중단 " + config.actionMillis + "ms");
                        await Hold(hold);
                        drift += hold;
                    }
                    else if (report.scenario == "stop_restart")
                    {
                        player.Stop();
                        report.steps.Add((i + 1) + "번째 패킷 뒤 Stop · 잔여 폐기");
                        report.enqueuedSamples = 0;   // Stop 이후 보낸 것만 기대값으로 센다
                        // 계측도 함께 되돌려야 이후 기대 프레임/비율 비교가 성립한다.
                        player.ResetDiagnostics();
                        // 탭을 다시 만든다. 새 PCM 이 가기 전에 같은 억제 설정을 반드시 다시 건다.
                        // 그 사이에는 재생이 멈춰 있고, 실패하면 보내기 전에 중단한다.
                        UnityEngine.Object.DestroyImmediate(tap);
                        tap = host.AddComponent<DialoguePlaybackTap>();
                        if (tap == null) throw new Exception("Stop 뒤 출력 탭을 다시 붙이지 못했습니다. 중단합니다.");
                        tap.SuppressOutput = !config.audible;
                        report.tapSuppressesOutput = tap.SuppressOutput;
                        await Hold(hold);
                        drift += hold;
                    }
                }
            }
            player.MarkComplete();
            report.steps.Add("전송 완료 · 합성 " + report.enqueuedSamples + " 샘플 · MarkComplete");

            await Hold(Mathf.Max(0, config.settleMillis) / 1000f);
            report.observedSec = Time.realtimeSinceStartup - start;

            report.player = player.Snapshot();
            if (tap != null) report.output = tap.Snapshot();
            RecordSilenceMode(report, source, tap);
            report.missingSamples = report.enqueuedSamples - report.player.consumed;
            report.preOutputConsumedSec = report.player.preOutputConsumed / (float)Rate;
            int outputRate = report.player.outputRate > 0 ? report.player.outputRate : 48000;
            report.underrunSec = report.player.underrunFrames / (float)outputRate;
            report.expectedSignalFrames = report.enqueuedSamples * outputRate / Rate;
            report.expectedRenderedFrames = report.enqueuedSamples <= 0 ? 0
                : (long)System.Math.Ceiling(report.enqueuedSamples * (double)outputRate / Rate - 1e-9);
            // 이 시나리오들은 검사가 직접 재생을 멈추거나 공급을 끊는다. 그 길이는 알려진 무음이다.
            report.knownSilenceSec = (report.scenario == "pause_resume" || report.scenario == "gap")
                ? Mathf.Max(0, config.actionMillis) / 1000f : 0f;
            if (report.tapAttached)
            {
                report.innerSilenceSec = report.output.longestInnerSilenceFrames / (float)outputRate;
                report.unexplainedInnerSilenceSec = Mathf.Max(0f, report.innerSilenceSec - report.knownSilenceSec);
                report.startLatencySec = report.output.firstSignalFrame < 0
                    ? -1 : report.output.firstSignalFrame / (float)outputRate;
                report.trailingSilenceSec = report.output.lastSignalFrame < 0
                    ? -1 : (report.output.frames - report.output.lastSignalFrame) / (float)outputRate;
                if (report.expectedSignalFrames > 0)
                    report.signalRatio = report.output.signalFrames / (float)report.expectedSignalFrames;
            }
            report.steps.Add("관측 종료");
            report.passed = true;
            report.qualityPassed = Grade(report, config);
        }
        catch (Exception e)
        {
            report.passed = false;
            report.qualityPassed = false;
            report.error = e.ToString();
        }
        finally
        {
            try
            {
                // 정리 전에 실제 상태를 되읽는다. 설정값이 아니라 AudioSource·탭이 가진 값이다.
                RecordSilenceMode(report, diagnosticSource, tap);
                if (sampler != null) EditorApplication.update -= sampler;
                if (player != null) player.Dispose();
                if (host != null) UnityEngine.Object.DestroyImmediate(host);
            }
            catch (Exception e)
            {
                report.passed = report.qualityPassed = false;
                report.error += (string.IsNullOrEmpty(report.error) ? "" : "\n") + "정리 중 오류: " + e;
            }

            report.limits.Add("합성 sine/ramp 계측이다. 실제 TTS PCM·네트워크 도착 간격·사람의 청취 판정이 아니다.");
            report.limits.Add("독립 AudioSource 로 쟀다. Scene_2 의 실제 AudioSource·믹서·장치 설정과 다를 수 있다.");
            report.limits.Add("innerSilenceSec 은 첫 신호와 마지막 신호 사이의 연속 무음이다. 시작 대기와 끝난 뒤 무음은 뺐다.");
            report.limits.Add("pause_resume·gap 은 설정한 actionMillis 만큼을 알려진 무음으로 빼고 판정한다. " +
                              "그 구간 안에서 그보다 짧은 결함은 이 검사로 가려지지 않는다.");
            report.limits.Add("preOutputConsumed 는 보간 시드 " + SeedSamples + "샘플까지 정상으로 본다. " +
                              "예전 AudioClip 선읽기(preArm, 4800샘플 규모)와 같은 수치가 아니다.");
            report.limits.Add("모든 수치는 탭이 지우기 전의 PCM 이다. 스피커에서 난 소리를 잰 것이 아니므로 " +
                              "무음 처리와 측정값은 무관하다. 어느 쪽이든 사람이 들은 결과는 아니다.");
            report.limits.Add("무음 처리는 이 검사가 만든 AudioSource 에 붙인 탭에서만 한다. 전역 리스너·시스템·" +
                              "에디터 음량, 오디오 믹서, 실제 대화용 AudioSource 는 건드리지 않았다.");
            report.limits.Add("AudioSource.mute 는 쓰지 않는다. 렌더러의 PCM 이 탭에 닿기 전에 0 이 되어 " +
                              "탭이 무음만 보게 된다(quiet-fast.json 실측). 그 회차의 실패 기록은 그대로 둔다.");
            report.limits.Add("탭보다 뒤에 붙는 필터가 있으면 이 무음 처리가 보장되지 않는다. 이 검사는 자기 " +
                              "GameObject 만 쓰므로 뒤에 붙는 것이 없다. 장치 단 가청 여부는 확인하지 않았다.");
            report.limits.Add("qualityPassed 는 여기 적은 임계값 검사일 뿐이며 사람이 들어 확인한 결과가 아니다.");
            report.limits.Add("timeline 은 에디터 틱 표본이며 최대 " + MaxSamples + "개에서 멈춘다. 오디오 콜백 해상도가 아니다.");
            WriteReport(report, configuredOutput);
            _running = false;
        }
    }

    /// <summary>출력 품질 임계값 검사. 통과와 실패 이유를 모두 남긴다.
    /// 탭이 없으면 파형을 볼 수 없으므로 통과가 아니라 미검증(실패)으로 본다.</summary>
    static bool Grade(Report report, DiagnosticConfig config)
    {
        bool ok = true;
        void Check(bool condition, string note)
        {
            report.quality.Add((condition ? "통과: " : "실패: ") + note);
            ok = ok && condition;
        }

        // 판정 기준은 무음 처리와 무관하다. 탭이 지우기 전의 원본 PCM 을 세기 때문이다.
        // 무음 처리 때문에 콜백이나 탭 기록이 사라진다면 아래 콜백·진폭·신호 비율 판정이
        // 그대로 실패한다. 예외를 두지 않는다(mute 방식이 실패했던 것도 이렇게 드러났다).
        report.quality.Add("참고: 효과적 무음=" + report.outputMuted + " (audible=" + report.audible +
                           ", source.mute=" + report.sourceMuted +
                           ", 탭 출력 지움=" + report.tapSuppressesOutput + ")");
        Check(report.player.callbacks > 0, "오디오 콜백 " + report.player.callbacks + "회 (0이면 필터가 돌지 않았다)");
        // 보간 시드로 첫 출력 전에 1~2샘플을 읽는 것은 정상이다(Prime). 예전 AudioClip 선읽기가
        // 시작 전에 4,800샘플을 당겨 가던 것(preArm)과는 의미가 다르다. 수치를 0으로 숨기지 않고
        // 시드 한도만 허용한다. 한도를 넘으면 다시 선읽기가 생긴 것이다.
        Check(report.player.preOutputConsumed <= SeedSamples,
            "첫 출력 전 소비 " + report.player.preOutputConsumed + "샘플 (보간 시드 " + SeedSamples +
            "샘플까지 정상, 그 이상은 선읽기)");
        Check(report.missingSamples == 0, "미소비 " + report.missingSamples + "샘플");
        // 입력 N 샘플은 정확히 ceil(N*출력율/24000) 프레임이어야 한다. 넘치면 DC tail 이다.
        Check(report.player.renderedFrames == report.expectedRenderedFrames,
            "렌더 프레임 " + report.player.renderedFrames + " (기대 " + report.expectedRenderedFrames + ")");
        Check(report.player.ended || report.enqueuedSamples == 0,
            "완료 표시 ended=" + report.player.ended);

        if (report.scenario == "pause_resume")
        {
            Check(report.pausedConsumedDelta == 0,
                "일시정지 중 소비 증가 " + report.pausedConsumedDelta + "샘플 (0이어야 한다)");
            Check(report.player.consumed == report.enqueuedSamples,
                "재개 뒤 전량 소비 " + report.player.consumed + "/" + report.enqueuedSamples);
        }
        if (report.scenario == "gap")
        {
            Check(report.player.rebuffers > 0, "재버퍼 " + report.player.rebuffers + "회 (공급을 끊었으므로 1회 이상)");
            Check(report.player.underruns > 0, "공급 부족 " + report.player.underruns + "회 (끊었으므로 1회 이상)");
            // 끊긴 구간이 있어도 샘플은 하나도 잃지 않아야 한다.
            Check(report.player.consumed == report.enqueuedSamples,
                "끊긴 뒤에도 전량 보존 " + report.player.consumed + "/" + report.enqueuedSamples);
        }
        if (report.scenario == "stop_restart")
        {
            // Stop 뒤 계측을 되돌렸으므로 이후 구간의 입력만 남아 있어야 한다.
            Check(report.player.received == report.enqueuedSamples,
                "Stop 뒤 입력 " + report.player.received + "/" + report.enqueuedSamples + " (앞 구간 잔여 없음)");
            Check(report.player.consumed == report.enqueuedSamples,
                "Stop 뒤 소비 " + report.player.consumed + "/" + report.enqueuedSamples);
        }
        if (report.scenario == "short_final")
            Check(report.player.consumed == report.enqueuedSamples,
                "짧은 마지막 조각까지 소비 " + report.player.consumed + "/" + report.enqueuedSamples);

        if (!report.tapAttached)
        {
            report.quality.Add("실패: 출력 탭이 없어 파형을 검사하지 못했다(미검증)");
            return false;
        }

        // peak 는 탭이 지우기 전에 잰 원본 PCM 이다. 무음으로 돌려도 이 값은 그대로여야 한다.
        Check(report.output.peak > 0.001f, "지우기 전 출력 최대 진폭 " + report.output.peak.ToString("F4"));
        if (report.tapSuppressesOutput)
        {
            // 지운 뒤를 다시 재서 뒤로 아무것도 안 나갔음을 증명한다.
            Check(report.output.postPeak == 0f,
                "지운 뒤 최대 진폭 " + report.output.postPeak.ToString("F4") + " (0이어야 무음이다)");
            Check(report.output.suppressedCallbacks == report.output.callbacks,
                "출력을 지운 콜백 " + report.output.suppressedCallbacks + "/" + report.output.callbacks +
                " (하나라도 빠지면 그 블록은 소리가 났다)");
            Check(report.output.suppressedFrames == report.output.frames,
                "출력을 지운 프레임 " + report.output.suppressedFrames + "/" + report.output.frames);
        }
        else
        {
            Check(report.output.suppressedCallbacks == 0,
                "audible=true 인데 지운 콜백 " + report.output.suppressedCallbacks + " (0이어야 한다)");
        }
        Check(report.signalRatio >= config.minSignalRatio,
            "신호 프레임 비율 " + report.signalRatio.ToString("F4") + " (하한 " + config.minSignalRatio.ToString("F2") + ")");
        // 상한이 없으면 무한 DC tail 이 비율만 키우고 통과해 버린다.
        Check(report.signalRatio <= config.maxSignalRatio,
            "신호 프레임 비율 상한 " + report.signalRatio.ToString("F4") + " (상한 " + config.maxSignalRatio.ToString("F2") + ")");
        Check(report.trailingSilenceSec >= 0.02f,
            "마지막 신호 뒤 무음 " + (report.trailingSilenceSec * 1000f).ToString("F1") + "ms (끝에 무음이 없으면 DC tail 의심)");
        // 설정이 일부러 만든 일시정지·공급 중단은 알려진 무음이다. 그만큼을 빼고 남은 무음만
        // 결함으로 본다. 알려진 무음을 통째로 허용치에 더하면 그 구간의 결함을 놓친다.
        float allowed = Mathf.Max(0, config.maxInnerSilenceMillis) / 1000f;
        Check(report.unexplainedInnerSilenceSec <= allowed,
            "설명되지 않는 최장 무음 " + (report.unexplainedInnerSilenceSec * 1000f).ToString("F1") +
            "ms (최장 " + (report.innerSilenceSec * 1000f).ToString("F1") + "ms 중 알려진 " +
            (report.knownSilenceSec * 1000f).ToString("F1") + "ms 제외, 허용 " +
            config.maxInnerSilenceMillis + "ms)");
        return ok;
    }

    static async Task Hold(float seconds)
    {
        float until = Time.realtimeSinceStartup + seconds;
        while (Time.realtimeSinceStartup < until) { RequirePlaying(); await Task.Delay(5); }
    }

    static void RequirePlaying()
    {
        if (!EditorApplication.isPlaying) throw new Exception("검사 도중 Play Mode 가 끝났습니다.");
    }

    static int[] BuildSchedule(DiagnosticConfig config, out int packetMillis, string scenario)
    {
        packetMillis = Mathf.Clamp(config.packetMillis <= 0 ? 200 : config.packetMillis, 10, MaxPacketMillis);
        if (scenario == "short_final")
        {
            packetMillis = Mathf.Clamp(packetMillis, 10, 80);
            return new int[] { 0 };
        }
        if (config.sendAtMillis != null && config.sendAtMillis.Length > 0)
        {
            var schedule = (int[])config.sendAtMillis.Clone();
            Array.Sort(schedule);
            if (schedule[0] < 0) throw new Exception("sendAtMillis 에 음수가 있습니다.");
            if (schedule.Length > 4000) throw new Exception("sendAtMillis 가 너무 많습니다(최대 4000).");
            return schedule;
        }
        int count = Mathf.Clamp(config.packetCount <= 0 ? 25 : config.packetCount, 1, 4000);
        var uniform = new int[count];
        for (int i = 0; i < count; i++) uniform[i] = i * packetMillis;
        return uniform;
    }

    /// <summary>합성 PCM16 모노 24 kHz. 무음과 신호를 출력에서 구분할 수 있게 만든다.</summary>
    static byte[] BuildPacket(string waveform, int samples, float toneHz, float amplitude, ref int phase)
    {
        float gain = Mathf.Clamp(amplitude, .01f, .9f);
        var pcm = new byte[samples * 2];
        for (int i = 0; i < samples; i++)
        {
            int n = phase + i;
            float value;
            if (waveform == "ramp")
            {
                float period = Mathf.Max(1f, Rate / Mathf.Max(1f, toneHz));
                value = (n % period) / period * 2f - 1f;
            }
            else value = Mathf.Sin(2f * Mathf.PI * toneHz * n / Rate);
            short encoded = (short)(Mathf.Clamp(value * gain, -1f, 1f) * 32767f);
            if (encoded == 0) encoded = 1;   // 출력의 연속 무음을 셀 때 입력의 0과 섞이지 않게 한다
            pcm[i * 2] = (byte)encoded;
            pcm[i * 2 + 1] = (byte)(encoded >> 8);
        }
        phase += samples;
        return pcm;
    }

    static DiagnosticConfig LoadConfig(string path)
    {
        if (!File.Exists(path)) throw new Exception("설정 파일이 없습니다: " + ConfigPath);
        var config = JsonUtility.FromJson<DiagnosticConfig>(File.ReadAllText(path));
        if (config == null) throw new Exception("설정 파일을 읽지 못했습니다: " + ConfigPath);
        if (string.IsNullOrWhiteSpace(config.caseName)) throw new Exception("caseName 이 비어 있습니다.");
        return config;
    }

    /// <summary>무음 방식을 설정값이 아니라 실제 객체에서 되읽어 기록한다.
    /// 소리를 막는 것은 탭이고, source.mute 는 탭이 붙기 전 잠깐만 쓴다.</summary>
    static void RecordSilenceMode(Report report, AudioSource source, DialoguePlaybackTap tap)
    {
        if (source != null) report.sourceMuted = source.mute;
        if (tap != null) report.tapSuppressesOutput = tap.SuppressOutput;
        report.outputMuted = report.sourceMuted || report.tapSuppressesOutput;
    }

    static string ProjectRoot() => Path.GetFullPath(Path.Combine(Application.dataPath, ".."));

    static string ResolveInsideProject(string root, string relative, string field)
    {
        string full = Path.GetFullPath(Path.Combine(root, relative));
        string prefix = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            throw new Exception(field + " 은 프로젝트 안의 경로여야 합니다.");
        return full;
    }

    static void WriteReport(Report report, string configuredOutput)
    {
        string path = configuredOutput;
        bool written = false;
        try
        {
            if (string.IsNullOrEmpty(path)) path = Path.Combine(ProjectRoot(), FallbackOutput);
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            path = UniquePath(path);
            report.outputPath = path;
            File.WriteAllText(path, JsonUtility.ToJson(report, true), new UTF8Encoding(false));
            written = true;
        }
        catch (Exception e)
        {
            report.passed = false;
            Debug.LogError("[PlaybackDiagnostic] 결과 파일을 쓰지 못했습니다(검사는 실패로 처리): " + path + "\n" + e);
        }

        string summary = "[PlaybackDiagnostic] passed=" + report.passed + " quality=" + report.qualityPassed +
            " case=" + report.caseName + " scenario=" + report.scenario +
            " silent=" + report.outputMuted + " tapSuppress=" + report.tapSuppressesOutput +
            " sourceMute=" + report.sourceMuted +
            " preOutputConsumed=" + report.player.preOutputConsumed +
            " rebuffers=" + report.player.rebuffers +
            " innerSilenceMs=" + (report.innerSilenceSec * 1000f).ToString("F1") +
            " unexplainedSilenceMs=" + (report.unexplainedInnerSilenceSec * 1000f).ToString("F1") +
            " out=" + (written ? path : "저장 실패") + "\n" + report.error;
        if (report.passed && report.qualityPassed) Debug.Log(summary);
        else Debug.LogError(summary);
    }

    static string UniquePath(string path)
    {
        if (!File.Exists(path)) return path;
        string dir = Path.GetDirectoryName(path);
        string name = Path.GetFileNameWithoutExtension(path);
        string ext = Path.GetExtension(path);
        for (int i = 2; i < 1000; i++)
        {
            string candidate = Path.Combine(dir, name + "-" + i + ext);
            if (!File.Exists(candidate)) return candidate;
        }
        throw new Exception("결과 파일 이름이 너무 많이 중복됩니다: " + path);
    }
}
