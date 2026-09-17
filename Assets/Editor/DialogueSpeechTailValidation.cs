// DialogueSpeechTailValidation.cs
// 실제 TTS WAV 의 마지막 샘플까지 Unity 오디오 출력 콜백에 도달했는지 조용히 확인한다.
//
// 기존 Assets/Editor/DialoguePlaybackRunner.cs 는 합성 sine/ramp 로 버퍼 계약을 잰다.
// 여기서는 같은 계약을 실제 음성 PCM 으로 다시 재되, 카운터가 아니라 출력 직전 파형
// 자체를 기대 파형과 샘플 단위로 대조한다. 말끝 음절이 잘리면 렌더러 카운터가 맞아도
// 마지막 수십 ms 의 파형이 어긋나므로, 카운터만으로는 배제할 수 없는 구간이 남는다.
//
// 기대 파형은 렌더러 코드를 재사용하지 않고 여기서 독립적으로 다시 계산한다.
// 출력 프레임 k 는 원본 시간 k*24000/outputRate 에 대응하고 이웃한 두 원본 샘플을
// 선형 보간한다. 마지막 원본 샘플 뒤는 0 이다. 렌더러의 상태 기계를 복제하면 같은
// 버그를 같이 복제해 거짓 통과가 나므로 수식만 쓴다.
//
// 이 메뉴는 대화 서버·마이크·등록 인물·씬 오브젝트·전역 음량을 전혀 건드리지 않는다.
// 자기 GameObject 와 AudioSource 만 만들어 쓰고, 끝나거나 실패해도 자기 것만 지운다.
// 진행 중인 체험·연결·녹음이 있으면 실행을 거부한다.
//
// 기본값(audible=false)에서는 탭이 원본 PCM 을 센 뒤 출력 버퍼를 0 으로 지운다.
// 측정값은 전부 지우기 전의 PCM 이고, 실제로 스피커로 나간 것은 무음이다.

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;

public static class DialogueSpeechTailValidation
{
    const string ConfigPath = ".dialogue-work/speech-tail-config.json";
    const string FallbackOutput = "tools/_work/sentence_tail_20260916/speech-tail.json";
    const int Rate = DialogueAudioRenderer.SourceRate;   // 24000
    const int MaxPacketBytes = 9600;                     // Enqueue 상한
    const int MaxSourceSeconds = 30;                     // WAV 길이 상한
    const int CaptureSlackSeconds = 3;                   // 선버퍼·꼬리까지 담을 여유
    const float Silence = 1e-6f;                         // 첫 신호 판정 문턱

    static bool _running;

    [Serializable]
    class TailConfig
    {
        public string caseName, outputPath;
        // 프로젝트 안의 WAV 경로 목록. PCM16 모노 24 kHz, 30초 이하만 받는다.
        public string[] wavPaths;
        // 기본은 소리를 내지 않는다. 이 검사는 파형을 대조하는 것이라 청취와 무관하다.
        public bool audible;
        public int packetMillis = 200;        // 한 번에 보낼 길이. 9600바이트 상한 안에서만
        // 실제 수신은 재생보다 조금 앞선다. 이 여유가 없으면 공급이 재생을 겨우 따라가
        // 검사 쪽 사정으로 언더런이 난다. 검사는 언더런 0 을 요구하므로 여유를 둔다.
        public int leadMillis = 400;
        public int tailWindowMillis = 200;    // 말끝을 따로 볼 구간
        public float maxAbsError = 1e-5f;     // 기대 파형과의 최대 허용 오차
    }

    [Serializable]
    class CaseResult
    {
        public string name = "", wavPath = "", error = "";
        public bool passed;
        public int sourceSamples, outputRate, packets, packetSamples;
        public float sourceSeconds, tailSeconds, drainSec, observedSec;
        public long expectedFrames, capturedFrames, comparedFrames;
        public long expectedFirstSignalFrame = -1, actualFirstSignalFrame = -1, alignOffsetFrames;
        public float maxAbsError, tailMaxAbsError;
        public long firstBadFrame = -1, missingFrames, trailingNonZeroFrames;
        public long tailWindowFrames;
        public DialogueAudioRenderer.Diagnostics player;
        public DialoguePlaybackTap.Output output;
        public List<string> quality = new List<string>();
    }

    [Serializable]
    class Report
    {
        public bool passed;            // 실행이 끝까지 진행됐는가
        public bool qualityPassed;     // 모든 회차가 파형 대조를 통과했는가
        public string error = "", time = "", caseName = "", unityVersion = "";
        public string scope =
            "이미 만들어 둔 TTS WAV 를 독립 소유 AudioSource 로 재생하고, 출력 콜백에 도달한 " +
            "파형을 독립 계산한 기대 파형과 샘플 단위로 대조한 계측이다. 대화 서버·마이크·" +
            "등록 인물·씬 오브젝트를 쓰지 않았고 새 음성을 합성하지도 않았다. " +
            "기본값(audible=false)에서는 탭이 원본 PCM 을 센 뒤 출력 버퍼를 0 으로 지운다. " +
            "여기 수치는 전부 지우기 전의 PCM 이고, 실제로 뒤로 나간 것은 무음이다.";
        public bool audible;
        public int startOutputSampleRate, endOutputSampleRate;
        public int cases, failed;
        public List<CaseResult> results = new List<CaseResult>();
        public List<string> limits = new List<string>();
        public string outputPath = "";
    }

    [MenuItem("Tools/Dialogue/Run speech tail output check", priority = 23)]
    public static async void Run()
    {
        if (_running) { Debug.LogWarning("[SpeechTail] 이미 실행 중입니다."); return; }
        if (!EditorApplication.isPlaying) { Debug.LogWarning("[SpeechTail] Play Mode 에서 실행하세요."); return; }

        var report = new Report { time = DateTime.UtcNow.ToString("O"), unityVersion = Application.unityVersion };
        string configuredOutput = null;

        _running = true;
        try
        {
            string root = ProjectRoot();
            var config = LoadConfig(Path.Combine(root, ConfigPath));
            report.caseName = config.caseName ?? "";
            report.audible = config.audible;
            configuredOutput = string.IsNullOrWhiteSpace(config.outputPath)
                ? Path.Combine(root, FallbackOutput)
                : ResolveInsideProject(root, config.outputPath, "outputPath");
            report.startOutputSampleRate = OutputRate();

            foreach (string relative in config.wavPaths)
            {
                RequireIdle();
                string full = ResolveInsideProject(root, relative, "wavPaths");
                var result = new CaseResult { name = Path.GetFileName(full), wavPath = relative };
                report.results.Add(result);
                try
                {
                    await RunOne(full, config, result);
                }
                catch (Exception e)
                {
                    result.passed = false;
                    result.error = e.ToString();
                    result.quality.Add("실패: 회차가 예외로 끝났다(출력 미검증)");
                }
                if (!result.passed) report.failed++;
            }

            report.endOutputSampleRate = OutputRate();
            if (report.endOutputSampleRate != report.startOutputSampleRate)
            {
                // 표본율이 바뀌면 기대 파형의 기준이 바뀐다. 앞선 대조를 통과로 남기지 않는다.
                report.failed++;
                report.results.Add(new CaseResult
                {
                    name = "output-sample-rate",
                    quality = { "실패: 검사 중 출력 표본율이 " + report.startOutputSampleRate +
                                " → " + report.endOutputSampleRate + " 로 바뀌었다(대조 무효)" },
                });
            }
            report.cases = report.results.Count;
            if (report.cases == 0) throw new Exception("검사한 WAV 가 없습니다.");
            report.passed = true;
            report.qualityPassed = report.failed == 0;
        }
        catch (Exception e)
        {
            report.passed = false;
            report.qualityPassed = false;
            report.error = e.ToString();
        }
        finally
        {
            report.limits.Add("원음 자체가 제대로 발음된 음성인지, 사람이 이어폰으로 들었을 때 " +
                              "말끝이 들리는지는 이 검사로 알 수 없다. 서버가 만든 WAV 를 그대로 " +
                              "기준으로 삼아 Unity 출력이 그 WAV 와 같은지만 본다. " +
                              "WAV 자체에서 이미 잘린 말끝은 여기서 통과로 나온다.");
            report.limits.Add("기대 파형은 x[k*24000/outputRate] 선형 보간으로 여기서 따로 계산한다. " +
                              "렌더러와 같은 리샘플 모델을 가정하므로, 모델 자체가 틀렸다면 함께 틀린다. " +
                              "모델의 결정적 검사는 Run resampler core check 가 맡는다.");
            report.limits.Add("독립 AudioSource 로 쟀다. Scene_2 의 실제 AudioSource·믹서·장치 설정과 다를 수 있다.");
            report.limits.Add("모든 수치는 탭이 지우기 전의 PCM 이다. 스피커에서 난 소리를 잰 것이 아니다.");
            report.limits.Add("무음 처리는 이 검사가 만든 AudioSource 에 붙인 탭에서만 한다. 전역 리스너·시스템·" +
                              "에디터 음량, 오디오 믹서, 실제 대화용 AudioSource 는 건드리지 않았다.");
            report.limits.Add("WAV 와 녹음된 출력 파형은 파일로 저장하지 않는다. JSON 수치만 남긴다.");
            report.limits.Add("실제 서버에서 오는 도착 간격이 아니라 일정한 간격으로 공급한다. " +
                              "네트워크 지연·지터로만 생기는 결함은 이 검사로 재현되지 않는다.");
            WriteReport(report, configuredOutput);
            _running = false;
        }
    }

    /// <summary>WAV 하나를 실제 재생 경로로 흘려보내고 출력 파형을 대조한다.
    /// 자기 GameObject 만 만들고 끝나거나 실패해도 자기 것만 지운다.</summary>
    static async Task RunOne(string wavPath, TailConfig config, CaseResult result)
    {
        float[] source = LoadWav(wavPath, out byte[] pcm);
        result.sourceSamples = source.Length;
        result.sourceSeconds = source.Length / (float)Rate;

        GameObject host = null;
        DialogueAudioPlayer player = null;
        DialoguePlaybackTap tap = null;
        try
        {
            RequireIdle();
            int outputRate = OutputRate();
            result.outputRate = outputRate;

            host = new GameObject("Dialogue Speech Tail Check") { hideFlags = HideFlags.DontSave };
            var audioSource = host.AddComponent<AudioSource>();
            // 탭이 붙어 막아 줄 때까지는 어떤 소리도 나가지 않게 한다. 그 뒤에는 mute 를 푼다.
            // mute 로 막으면 렌더러의 PCM 이 탭에 닿기 전에 0 이 되어 파형을 볼 수 없다(실측).
            audioSource.mute = true;
            player = new DialogueAudioPlayer(audioSource);     // 렌더러가 먼저 붙는다
            tap = host.AddComponent<DialoguePlaybackTap>();    // 탭은 렌더러 뒤에 온다
            if (tap == null) throw new Exception("출력 탭을 붙이지 못했습니다. 소리를 막을 수 없어 중단합니다.");
            // 기록 배열은 재생 전에 메인 스레드에서 배정한다. 오디오 스레드는 채우기만 한다.
            tap.BeginCapture(new float[(MaxSourceSeconds + CaptureSlackSeconds) * outputRate]);
            tap.SuppressOutput = !config.audible;
            audioSource.mute = false;
            player.ResetDiagnostics();
            result.tailSeconds = player.TailSeconds;

            int packetSamples = Mathf.Clamp(Rate * config.packetMillis / 1000, 1, MaxPacketBytes / 2);
            result.packetSamples = packetSamples;
            int lead = Mathf.Max(0, config.leadMillis);
            float start = Time.realtimeSinceStartup;
            for (int offset = 0; offset < pcm.Length; offset += packetSamples * 2)
            {
                int bytes = Math.Min(packetSamples * 2, pcm.Length - offset);
                float due = start + Math.Max(0f, offset / 2f / Rate - lead / 1000f);
                while (Time.realtimeSinceStartup < due) { RequirePlaying(); await Task.Delay(2); }
                RequirePlaying();
                var packet = new byte[bytes];
                Buffer.BlockCopy(pcm, offset, packet, 0, bytes);
                player.Enqueue(packet);
                result.packets++;
            }
            player.MarkComplete();

            // 기존 클라이언트와 같은 종료 판정이다. 남은 것을 다 소비한 뒤 TailSeconds 를
            // 더 기다리고, 그 시점의 상태를 찍은 다음에야 Stop 한다.
            float deadline = start + result.sourceSeconds + player.TailSeconds + 15f;
            while (player.Consumed < player.Received)
            {
                RequirePlaying();
                if (Time.realtimeSinceStartup > deadline)
                    throw new Exception("소비가 끝나지 않았습니다. consumed=" + player.Consumed +
                                        " received=" + player.Received);
                await Task.Delay(5);
            }
            result.drainSec = Time.realtimeSinceStartup - start;
            await Hold(player.TailSeconds);

            // Stop 직전 상태를 그대로 남긴다.
            result.player = player.Snapshot();
            result.output = tap.Snapshot();
            var captured = new float[Math.Max(1, result.output.captureFrames)];
            result.capturedFrames = tap.CopyCapture(captured);
            result.observedSec = Time.realtimeSinceStartup - start;
            player.Stop();

            Compare(source, captured, config, result);
            result.passed = Grade(config, result);
        }
        finally
        {
            // 만든 것만 지운다. 다른 오브젝트·설정은 건드리지 않는다.
            try
            {
                if (player != null) player.Dispose();
                if (host != null) UnityEngine.Object.DestroyImmediate(host);
            }
            catch (Exception e)
            {
                result.passed = false;
                result.error += (string.IsNullOrEmpty(result.error) ? "" : "\n") + "정리 중 오류: " + e;
            }
        }
    }

    /// <summary>기대 파형을 독립적으로 계산해 실제 출력과 대조한다.
    /// 시작 무음만큼 민 뒤 전체와 마지막 구간의 최대 오차를 각각 남긴다.</summary>
    static void Compare(float[] source, float[] captured, TailConfig config, CaseResult result)
    {
        int outputRate = result.outputRate;
        long n = source.Length;
        // 원본 N 샘플은 정확히 ceil(N*outputRate/24000) 프레임을 만든다.
        long expectedFrames = n <= 0 ? 0
            : (long)Math.Ceiling(n * (double)outputRate / Rate - 1e-9);
        result.expectedFrames = expectedFrames;

        var expected = new float[expectedFrames];
        double step = (double)Rate / outputRate;
        for (long k = 0; k < expectedFrames; k++)
        {
            double t = k * step;
            long i = (long)Math.Floor(t);
            float frac = (float)(t - i);
            float a = i < n ? source[i] : 0f;
            float b = i + 1 < n ? source[i + 1] : 0f;   // 마지막 샘플 뒤는 0 이다
            expected[k] = a + (b - a) * frac;
        }

        for (long k = 0; k < expectedFrames; k++)
            if (Math.Abs(expected[k]) > Silence) { result.expectedFirstSignalFrame = k; break; }
        for (long k = 0; k < result.capturedFrames; k++)
            if (Math.Abs(captured[k]) > Silence) { result.actualFirstSignalFrame = k; break; }
        if (result.expectedFirstSignalFrame < 0 || result.actualFirstSignalFrame < 0)
        {
            result.quality.Add("실패: 기대 또는 실제 출력에서 신호를 찾지 못해 정렬할 수 없다 " +
                               "(기대 첫 신호=" + result.expectedFirstSignalFrame +
                               ", 실제 첫 신호=" + result.actualFirstSignalFrame + ")");
            // 무한대를 쓰면 JSON 이 파서에 따라 깨진다. 임계값을 확실히 넘는 유한값으로 남긴다.
            result.maxAbsError = result.tailMaxAbsError = float.MaxValue;
            return;
        }
        // 선버퍼 때문에 출력은 기대보다 뒤에서 시작한다. 그 차이만큼만 민다.
        result.alignOffsetFrames = result.actualFirstSignalFrame - result.expectedFirstSignalFrame;

        long tailWindow = Math.Max(1, (long)outputRate * Math.Max(1, config.tailWindowMillis) / 1000);
        result.tailWindowFrames = Math.Min(tailWindow, expectedFrames);
        long tailFrom = expectedFrames - result.tailWindowFrames;

        // 실제 gain 은 1 이므로 값을 그대로 뺀다. 마지막 구간뿐 아니라 전체를 본다.
        for (long k = 0; k < expectedFrames; k++)
        {
            long at = k + result.alignOffsetFrames;
            if (at < 0 || at >= result.capturedFrames) { result.missingFrames++; continue; }
            result.comparedFrames++;
            float error = Math.Abs(captured[at] - expected[k]);
            if (error > result.maxAbsError)
            {
                result.maxAbsError = error;
                if (result.firstBadFrame < 0 && error > config.maxAbsError) result.firstBadFrame = k;
            }
            if (k >= tailFrom && error > result.tailMaxAbsError) result.tailMaxAbsError = error;
        }
        // 기대 길이를 넘긴 뒤에도 신호가 남아 있으면 DC tail 등 과잉 출력이다.
        for (long at = expectedFrames + result.alignOffsetFrames; at < result.capturedFrames; at++)
            if (Math.Abs(captured[at]) > Silence) result.trailingNonZeroFrames++;
    }

    /// <summary>임계값 검사. 통과와 실패 이유를 모두 남긴다.
    /// 출력을 보지 못한 회차는 통과가 아니라 미검증(실패)으로 본다.</summary>
    static bool Grade(TailConfig config, CaseResult result)
    {
        bool ok = true;
        void Check(bool condition, string note)
        {
            result.quality.Add((condition ? "통과: " : "실패: ") + note);
            ok = ok && condition;
        }

        Check(result.output.captureEnabled, "파형 기록 사용 여부 " + result.output.captureEnabled);
        Check(!result.output.captureOverflow,
            "기록 배열 넘침 " + result.output.captureOverflow + " (넘치면 뒤쪽 파형을 잃어 미검증이다)");
        Check(result.output.channels > 0, "출력 채널 " + result.output.channels);
        Check(result.player.callbacks > 0, "오디오 콜백 " + result.player.callbacks + "회 (0이면 필터가 돌지 않았다)");
        Check(result.player.outputRate == result.outputRate && result.player.configChanges == 0,
            "출력 표본율 " + result.player.outputRate + " (시작 " + result.outputRate +
            ", 설정 변경 " + result.player.configChanges + "회, 바뀌면 대조가 무효다)");
        Check(result.player.received == result.sourceSamples,
            "입력 " + result.player.received + "/" + result.sourceSamples + "샘플");
        Check(result.player.consumed == result.player.received,
            "소비 " + result.player.consumed + "/" + result.player.received + "샘플");
        Check(result.player.ended, "완료 표시 ended=" + result.player.ended + " (Stop 직전 값이다)");
        Check(result.player.underruns == 0,
            "공급 부족 " + result.player.underruns + "회 " + result.player.underrunFrames + "프레임");
        Check(result.player.renderedFrames == result.expectedFrames,
            "렌더 프레임 " + result.player.renderedFrames + " (기대 " + result.expectedFrames + ")");

        // 여기부터가 이 검사의 본론이다. 카운터가 아니라 출력 직전 파형을 본다.
        Check(result.capturedFrames >= result.expectedFrames + result.alignOffsetFrames,
            "기록한 출력 프레임 " + result.capturedFrames + " (기대 " +
            (result.expectedFrames + result.alignOffsetFrames) + " 이상)");
        Check(result.missingFrames == 0,
            "대조하지 못한 프레임 " + result.missingFrames + " (0이 아니면 끝이 잘린 것이다)");
        Check(result.maxAbsError <= config.maxAbsError,
            "전체 최대 오차 " + result.maxAbsError.ToString("E3") + " (허용 " +
            config.maxAbsError.ToString("E3") + ", 첫 어긋난 프레임 " + result.firstBadFrame + ")");
        Check(result.tailMaxAbsError <= config.maxAbsError,
            "마지막 " + config.tailWindowMillis + "ms 최대 오차 " + result.tailMaxAbsError.ToString("E3") +
            " (허용 " + config.maxAbsError.ToString("E3") + ")");
        Check(result.trailingNonZeroFrames == 0,
            "기대 길이 뒤에 남은 신호 " + result.trailingNonZeroFrames + "프레임 (DC tail)");
        Check(result.output.peak > 0.001f, "지우기 전 출력 최대 진폭 " + result.output.peak.ToString("F4"));

        if (!config.audible)
        {
            Check(result.output.suppressOutput, "탭이 출력을 지움 " + result.output.suppressOutput);
            Check(result.output.postPeak == 0f,
                "지운 뒤 최대 진폭 " + result.output.postPeak.ToString("F4") + " (0이어야 무음이다)");
            Check(result.output.suppressedCallbacks == result.output.callbacks,
                "출력을 지운 콜백 " + result.output.suppressedCallbacks + "/" + result.output.callbacks +
                " (하나라도 빠지면 그 블록은 소리가 났다)");
        }
        else
        {
            result.quality.Add("참고: audible=true 회차다. 소리가 나갔고 기본 설정이 아니다.");
            Check(result.output.suppressedCallbacks == 0,
                "audible=true 인데 지운 콜백 " + result.output.suppressedCallbacks + " (0이어야 한다)");
        }
        return ok;
    }

    /// <summary>PCM16 모노 24 kHz, 30초 이하만 받는다. 어긋나면 고치지 않고 거절한다.</summary>
    static float[] LoadWav(string path, out byte[] pcm)
    {
        if (!File.Exists(path)) throw new Exception("WAV 파일이 없습니다: " + path);
        byte[] bytes = File.ReadAllBytes(path);
        if (bytes.Length < 44) throw new Exception("WAV 가 너무 짧습니다: " + path);
        if (Encoding.ASCII.GetString(bytes, 0, 4) != "RIFF" || Encoding.ASCII.GetString(bytes, 8, 4) != "WAVE")
            throw new Exception("RIFF/WAVE 헤더가 아닙니다: " + path);

        int dataAt = -1, dataBytes = 0;
        bool fmtSeen = false;
        for (int at = 12; at + 8 <= bytes.Length;)
        {
            string id = Encoding.ASCII.GetString(bytes, at, 4);
            int size = BitConverter.ToInt32(bytes, at + 4);
            int body = at + 8;
            if (size < 0 || body + size > bytes.Length) throw new Exception("WAV 청크 길이가 잘못됐습니다: " + path);
            if (id == "fmt ")
            {
                if (size < 16) throw new Exception("fmt 청크가 너무 짧습니다: " + path);
                int format = BitConverter.ToUInt16(bytes, body);
                int channels = BitConverter.ToUInt16(bytes, body + 2);
                int sampleRate = BitConverter.ToInt32(bytes, body + 4);
                int bits = BitConverter.ToUInt16(bytes, body + 14);
                if (format != 1) throw new Exception("PCM(format 1) 이 아닙니다(format=" + format + "): " + path);
                if (channels != 1) throw new Exception("모노가 아닙니다(channels=" + channels + "): " + path);
                if (sampleRate != Rate) throw new Exception(Rate + "Hz 가 아닙니다(" + sampleRate + "): " + path);
                if (bits != 16) throw new Exception("16비트가 아닙니다(" + bits + "): " + path);
                fmtSeen = true;
            }
            else if (id == "data") { dataAt = body; dataBytes = size; }
            at = body + size + (size % 2);   // 청크는 짝수 경계로 맞춰져 있다
        }
        if (!fmtSeen) throw new Exception("fmt 청크가 없습니다: " + path);
        if (dataAt < 0 || dataBytes <= 0) throw new Exception("data 청크가 없습니다: " + path);
        if (dataBytes % 2 != 0) throw new Exception("data 길이가 홀수입니다: " + path);
        int samples = dataBytes / 2;
        if (samples > MaxSourceSeconds * Rate)
            throw new Exception(MaxSourceSeconds + "초를 넘습니다(" + samples / (float)Rate + "초): " + path);

        pcm = new byte[dataBytes];
        Buffer.BlockCopy(bytes, dataAt, pcm, 0, dataBytes);
        var source = new float[samples];
        // 렌더러와 같은 변환이다. 여기서 값을 다르게 만들면 대조가 성립하지 않는다.
        for (int i = 0; i < samples; i++)
            source[i] = (short)(pcm[i * 2] | (pcm[i * 2 + 1] << 8)) / 32768f;
        return source;
    }

    static TailConfig LoadConfig(string path)
    {
        if (!File.Exists(path)) throw new Exception("설정 파일이 없습니다: " + ConfigPath);
        var config = JsonUtility.FromJson<TailConfig>(File.ReadAllText(path));
        if (config == null) throw new Exception("설정 파일을 읽지 못했습니다: " + ConfigPath);
        if (string.IsNullOrWhiteSpace(config.caseName)) throw new Exception("caseName 이 비어 있습니다.");
        if (config.wavPaths == null || config.wavPaths.Length == 0) throw new Exception("wavPaths 가 비어 있습니다.");
        if (config.wavPaths.Length > 20) throw new Exception("wavPaths 가 너무 많습니다(최대 20).");
        if (config.packetMillis <= 0 || config.packetMillis > MaxPacketBytes * 1000 / 2 / Rate)
            throw new Exception("packetMillis 는 1~" + (MaxPacketBytes * 1000 / 2 / Rate) + " 여야 합니다.");
        if (config.tailWindowMillis <= 0) throw new Exception("tailWindowMillis 가 0 이하입니다.");
        if (!(config.maxAbsError > 0f)) throw new Exception("maxAbsError 가 0 이하입니다.");
        return config;
    }

    /// <summary>체험·연결·녹음 중이면 거절한다. 진행 중인 것을 끊고 검사하지 않는다.</summary>
    static void RequireIdle()
    {
        RequirePlaying();
        var voice = UnityEngine.Object.FindObjectOfType<DialogueVoiceClient>();
        if (voice != null && (voice.ExperienceActive || voice.DialogueConnected || voice.IsListening))
            throw new Exception("체험이 진행 중입니다. 체험을 끝낸 뒤 실행하세요.");
        foreach (string device in Microphone.devices)
            if (Microphone.IsRecording(device))
                throw new Exception("마이크 녹음이 진행 중입니다: " + device);
    }

    static void RequirePlaying()
    {
        if (!EditorApplication.isPlaying) throw new Exception("검사 도중 Play Mode 가 끝났습니다.");
    }

    static int OutputRate() => AudioSettings.outputSampleRate > 0 ? AudioSettings.outputSampleRate : 48000;

    static async Task Hold(float seconds)
    {
        float until = Time.realtimeSinceStartup + seconds;
        while (Time.realtimeSinceStartup < until) { RequirePlaying(); await Task.Delay(5); }
    }

    static string ProjectRoot() => Path.GetFullPath(Path.Combine(Application.dataPath, ".."));

    static string ResolveInsideProject(string root, string relative, string field)
    {
        if (string.IsNullOrWhiteSpace(relative)) throw new Exception(field + " 이 비어 있습니다.");
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
            Debug.LogError("[SpeechTail] 결과 파일을 쓰지 못했습니다(검사는 실패로 처리): " + path + "\n" + e);
        }

        string summary = "[SpeechTail] passed=" + report.passed + " quality=" + report.qualityPassed +
            " case=" + report.caseName + " cases=" + report.cases + " failed=" + report.failed +
            " audible=" + report.audible +
            " outputRate=" + report.startOutputSampleRate + "→" + report.endOutputSampleRate +
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
