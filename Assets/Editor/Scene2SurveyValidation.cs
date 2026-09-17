// Scene2SurveyValidation.cs
// Scene_2에서 실제 등록 인물로 한 턴을 끝까지 검사한다.
//
// 재생 중인 Scene_2 자체를 검사한다. 씬을 새로 만들거나 열거나 저장하지 않는다.
// 등록·대화 서버는 loopback QA 서버로만 바꿔서, 검사 도중 운영 현재 인물을 건드리지 않는다.
// useTestProfile 은 끝까지 false 다. 마이크만 에디터 전용 파일 입력으로 대신한다.
//
// 검사가 직접 시작한 체험만 검사가 끝낸다. 이미 진행 중인 체험은 중단시키지 않고 실행을 거부한다.

using System;
using System.Collections.Generic;
using System.IO;
using System.Text;
using System.Threading.Tasks;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class Scene2SurveyValidation
{
    const string SceneName = "Scene_2";
    const string ConfigPath = ".dialogue-work/scene2-survey-check-config.json";
    const int ChunkBytes = 2560;      // 80 ms · 1280 samples · 16 kHz mono PCM16
    const int ChunkMillis = 80;
    const int PaddingSilenceChunks = 14;   // 1.12초 패딩 끝무음으로 서버 VAD 종료를 끌어낸다
    const float MinReferenceSeconds = 3f;

    static bool _running;

    [Serializable]
    class CheckConfig
    {
        public string registrationUrl, dialogueUrl, expectedSession, caseName, wavPath, outputPath;
    }

    [Serializable]
    class CheckReport
    {
        public bool passed;
        public string error = "", time = "", caseName = "", scene = "";
        public bool protocolTestMode;          // 항상 false 여야 한다
        public bool followServerSession, micCaptureOpen;
        public string sessionId = "", expectedSession = "";
        public bool sessionMatched, serverReady, ttsEnabled, dialogueConnected;
        public string voiceMode = "", referenceSource = "";
        public float referenceSeconds;
        public bool startButtonPresent, startButtonInteractable, startButtonInvoked, experienceStarted;
        public int inputChunks, paddingChunks, deltas;
        public float wavSeconds, paddingSilenceSec;
        public string responseId = "", heard = "", answer = "", route = "", audioEvent = "", language = "";
        public long receivedAudioSamples, consumedAudioSamples;
        public float outputPeak;
        public bool playbackFinished;

        // 시간 기준: WAV 마지막 PCM 조각을 전송한 시각(뒤에 붙인 패딩 끝무음 제외).
        public string timingBasis =
            "WAV 마지막 PCM 조각 전송 시각 기준의 에디터 관측 시간. 패딩 끝무음은 기준에서 제외했고 " +
            "WAV 자체에 들어 있는 끝무음은 포함된다. 실제 사용자의 발화 끝이나 스피커에서 소리가 난 시각이 아니다.";
        // 아래 두 값은 대기 리액션과 본답변을 구분하지 않는 '첫 관측'이다. 현재 공개 속성으로는 나눌 수 없다.
        public bool firstAnyAudioObserved, firstAnyAudioBeforeWavEnd;
        public float firstAnyAudioAfterWavSec;   // ReceivedAudioSamples 가 처음 0을 넘은 관측 시각
        public bool firstAnyTextObserved, firstAnyTextBeforeWavEnd;
        public float firstAnyTextAfterWavSec;    // lastAnswer 가 처음 채워진 관측 시각
        public float playbackDoneAfterWavSec;
        // 서버가 response.done 에 담아 보낸 본답변 기준 값.
        public float serverFirstTextSec = -1, serverFirstAudioSec = -1, serverTotalSec = -1;

        public bool conversationLogPassed;
        public string conversationLogCheck = "미실행";
        public string outputPath = "";
        public List<string> steps = new List<string>();
        public List<string> limits = new List<string>();
    }

    [MenuItem("Tools/Dialogue/Run Scene 2 registered survey check", priority = 20)]
    public static async void Run()
    {
        if (_running) { Debug.LogWarning("[Scene2SurveyCheck] 이미 실행 중입니다."); return; }

        var report = new CheckReport { time = DateTime.UtcNow.ToString("O"), scene = SceneManager.GetActiveScene().name };
        DialogueVoiceClient voice = null;
        ExperienceControl control = null;
        Action<string, string> onDelta = null;
        Action<string> onError = null;
        EditorApplication.CallbackFunction sampler = null;
        string savedServerUrl = null, savedDialogueUrl = null;
        // 검사가 직접 바꾼 것만 되돌린다.
        bool urlsChanged = false, injecting = false, startedByCheck = false;
        string configuredOutput = null;

        if (!EditorApplication.isPlaying) { Debug.LogWarning("[Scene2SurveyCheck] Play Mode 에서 실행하세요."); return; }
        if (report.scene != SceneName) { Debug.LogWarning("[Scene2SurveyCheck] 활성 씬이 Scene_2 가 아닙니다."); return; }

        _running = true;
        try
        {
            // --- 여기부터 사전 검증. 씬 상태를 바꾸지 않는다. ---
            string root = ProjectRoot();
            var config = LoadConfig(Path.Combine(root, ConfigPath));
            report.caseName = config.caseName ?? "";
            report.expectedSession = config.expectedSession ?? "";
            configuredOutput = ResolveInsideProject(root, config.outputPath, "outputPath");
            string wavPath = ResolveInsideProject(root, config.wavPath, "wavPath");
            if (!File.Exists(wavPath)) throw new Exception("입력 WAV 가 없습니다: " + config.wavPath);
            string registrationUrl = RequireLoopback(config.registrationUrl, "registrationUrl");
            string dialogueUrl = RequireLoopback(config.dialogueUrl, "dialogueUrl");
            if (string.IsNullOrWhiteSpace(config.expectedSession)) throw new Exception("expectedSession 이 비어 있습니다.");

            byte[] pcm = ReadPcm16Mono16k(wavPath);
            report.wavSeconds = pcm.Length / 32000f;
            report.paddingSilenceSec = PaddingSilenceChunks * ChunkMillis / 1000f;
            report.steps.Add($"설정·WAV 확인 ({report.wavSeconds:F2}초)");

            voice = UnityEngine.Object.FindObjectOfType<DialogueVoiceClient>();
            control = UnityEngine.Object.FindObjectOfType<ExperienceControl>();
            if (voice == null || control == null) throw new Exception("Scene_2 에서 DialogueVoiceClient / ExperienceControl 을 찾지 못했습니다.");
            if (voice.ExperienceActive || control.Started)
                throw new Exception("체험이 진행 중입니다. 진행 중인 체험을 건드리지 않고 실행을 거부합니다.");
            if (voice.useTestProfile) throw new Exception("useTestProfile 이 켜져 있습니다. 등록 인물 검사가 아닙니다.");
            if (!voice.followServerSession) throw new Exception("followServerSession 이 꺼져 있습니다. 등록 세션을 따라가지 않습니다.");
            report.startButtonPresent = control.startButton != null;
            if (!report.startButtonPresent)
                throw new Exception("ExperienceControl.startButton 참조가 없습니다. 사용자가 누르는 경로를 검사할 수 없습니다.");
            if (control.log == null || control.log.body == null)
                throw new Exception("ConversationLog / body 참조가 없습니다. 자막 표시를 검사할 수 없습니다.");
            report.protocolTestMode = voice.useTestProfile;
            report.followServerSession = voice.followServerSession;

            // --- 여기부터 상태를 바꾼다. 바꾼 것은 finally 에서 되돌린다. ---
            savedServerUrl = voice.serverUrl;
            savedDialogueUrl = voice.dialogueServerUrl;
            voice.serverUrl = registrationUrl;
            voice.dialogueServerUrl = dialogueUrl;
            urlsChanged = true;
            report.steps.Add("등록·대화 주소를 loopback QA 서버로 전환");

            voice.serverReady = false;
            voice.StartCoroutine(voice.CheckHealth());
            await Until(() => { RequireAlive(voice, control); return voice.serverReady; },
                40, "QA 대화 서버 health 가 준비되지 않았습니다.");
            report.serverReady = voice.serverReady;
            report.ttsEnabled = voice.TtsEnabled;
            report.steps.Add("QA 대화 서버 health 준비 완료");

            // sessionPollSec 은 건드리지 않는다. 이미 도는 폴링 주기를 그대로 기다린다.
            await Until(() => { RequireAlive(voice, control); return voice.HasSession && voice.sessionId == config.expectedSession; },
                60, "QA 등록 서버의 current 세션이 expectedSession 이 되지 않았습니다.");
            report.sessionId = voice.sessionId;
            report.sessionMatched = true;
            report.steps.Add("QA 등록 세션 일치 확인 (기존 폴링 주기 대기)");

            // 세션이 막 확인된 프레임에는 ExperienceControl.Refresh 가 아직 단추를 켜지 않았을 수 있다.
            // 짧게만 기다리고, 그래도 아니면 실패한다. UI 오류를 우회 경로로 숨기지 않는다.
            await Until(() =>
            {
                RequireAlive(voice, control);
                report.startButtonInteractable = control.startButton.gameObject.activeInHierarchy &&
                    control.startButton.IsInteractable();
                return report.startButtonInteractable;
            }, 5, "시작 단추를 누를 수 없는 상태입니다. 사용자가 누르는 경로를 검사할 수 없습니다.");

            // 마이크를 열지 않고 파일 PCM 만 같은 전송 경로로 넣는다.
            voice.BeginEditorAudioInjection();
            injecting = true;

            string failure = null;
            onDelta = (heard, answer) => { if (!string.IsNullOrEmpty(answer)) report.deltas++; };
            onError = message => failure = message;
            voice.OnAnswerUpdated += onDelta;
            voice.OnError += onError;

            // 회차마다 누적값을 0으로 되돌린다. Received/Consumed 는 BeginExperience 의 Stop() 이 되돌린다.
            voice.ResetOutputPeak();

            startedByCheck = true;
            control.startButton.onClick.Invoke();
            report.startButtonInvoked = true;

            await Until(() => { RequireAlive(voice, control); return (control.Started && voice.ExperienceActive) || failure != null; },
                10, "체험이 시작되지 않았습니다.");
            if (failure != null) throw new Exception(failure);
            report.experienceStarted = true;
            report.steps.Add("시작 단추 onClick 으로 체험 시작");

            await Until(() => { RequireAlive(voice, control); return voice.DialogueConnected || failure != null; },
                90, "대화 서버에 연결되지 않았습니다.");
            if (failure != null) throw new Exception(failure);
            report.dialogueConnected = true;
            report.ttsEnabled = voice.TtsEnabled;
            report.voiceMode = voice.VoiceMode ?? "";
            report.referenceSource = voice.ActiveReference?.source ?? "";
            report.referenceSeconds = voice.ActiveReference?.duration_sec ?? 0;
            report.micCaptureOpen = voice.IsListening;
            if (report.micCaptureOpen) throw new Exception("검사 중 실제 마이크가 열렸습니다.");
            if (voice.useTestProfile) throw new Exception("연결 중 useTestProfile 이 바뀌었습니다.");
            // 이 메뉴는 음성 왕복 검사다. 텍스트 대화로는 통과시키지 않는다.
            if (!report.ttsEnabled) throw new Exception("서버 TTS 가 꺼져 있습니다. 음성 왕복 검사를 할 수 없습니다.");
            if (report.voiceMode != "reference_icl")
                throw new Exception("참조 음성 조건이 적용되지 않았습니다: " + report.voiceMode);
            if (report.referenceSeconds < MinReferenceSeconds)
                throw new Exception($"참조 음성이 {MinReferenceSeconds}초 미만입니다: {report.referenceSeconds:F2}초");
            report.steps.Add($"연결 완료 · voice_mode={report.voiceMode} · 참조 {report.referenceSeconds:F2}초");

            // 입력을 보내기 전부터 매 에디터 틱마다 관측한다. 전송 대기 중에 도착한 응답도 실제 시각으로 찍힌다.
            float firstAnyAudioAt = -1, firstAnyTextAt = -1;
            sampler = () =>
            {
                if (voice == null) return;
                float now = Time.realtimeSinceStartup;
                if (firstAnyAudioAt < 0 && voice.ReceivedAudioSamples > 0) firstAnyAudioAt = now;
                if (firstAnyTextAt < 0 && !string.IsNullOrEmpty(voice.lastAnswer)) firstAnyTextAt = now;
                if (string.IsNullOrEmpty(report.responseId) && !string.IsNullOrEmpty(voice.CurrentResponseId))
                    report.responseId = voice.CurrentResponseId;
                report.receivedAudioSamples = Math.Max(report.receivedAudioSamples, voice.ReceivedAudioSamples);
                report.consumedAudioSamples = Math.Max(report.consumedAudioSamples, voice.ConsumedAudioSamples);
                report.outputPeak = Math.Max(report.outputPeak, voice.OutputPeak);
                if (!string.IsNullOrEmpty(voice.lastHeard)) report.heard = voice.lastHeard;
            };
            EditorApplication.update += sampler;

            // 시간 기준: 마지막 WAV PCM 조각을 보낸 바로 그 시각. 뒤의 Task.Delay 나 패딩 끝무음은 포함하지 않는다.
            float wavEnd = -1;
            for (int offset = 0; offset < pcm.Length; offset += ChunkBytes)
            {
                RequireRunning(voice, control, failure);
                var packet = new byte[ChunkBytes];
                Buffer.BlockCopy(pcm, offset, packet, 0, Math.Min(ChunkBytes, pcm.Length - offset));
                if (!voice.SendInjectedAudio(packet)) throw new Exception("PCM 조각을 보내지 못했습니다.");
                wavEnd = Time.realtimeSinceStartup;
                report.inputChunks++;
                await Task.Delay(ChunkMillis);
            }
            RequireRunning(voice, control, failure);
            if (wavEnd < 0) throw new Exception("보낼 WAV PCM 이 없습니다.");

            for (int i = 0; i < PaddingSilenceChunks; i++)
            {
                RequireRunning(voice, control, failure);
                if (!voice.SendInjectedAudio(new byte[ChunkBytes])) throw new Exception("패딩 끝무음 조각을 보내지 못했습니다.");
                report.paddingChunks++;
                await Task.Delay(ChunkMillis);
            }
            report.steps.Add($"입력 전송 완료 (WAV {report.inputChunks} + 패딩 무음 {report.paddingChunks} 조각)");

            await Until(() => { RequireRunning(voice, control, failure); return voice.PlaybackFinished; }, 120,
                "답변이 끝까지 재생되지 않았습니다.");
            if (failure != null) throw new Exception(failure);
            sampler();
            report.playbackDoneAfterWavSec = Time.realtimeSinceStartup - wavEnd;
            report.firstAnyAudioObserved = firstAnyAudioAt >= 0;
            if (report.firstAnyAudioObserved)
            {
                report.firstAnyAudioAfterWavSec = firstAnyAudioAt - wavEnd;      // 음수면 마지막 입력 전에 도착한 것
                report.firstAnyAudioBeforeWavEnd = report.firstAnyAudioAfterWavSec < 0;
            }
            report.firstAnyTextObserved = firstAnyTextAt >= 0;
            if (report.firstAnyTextObserved)
            {
                report.firstAnyTextAfterWavSec = firstAnyTextAt - wavEnd;
                report.firstAnyTextBeforeWavEnd = report.firstAnyTextAfterWavSec < 0;
            }
            report.playbackFinished = voice.PlaybackFinished;
            report.answer = voice.lastAnswer ?? "";
            report.route = voice.ResponseRoute ?? "";
            report.audioEvent = voice.VoiceAudioEvent;
            report.language = voice.VoiceLanguage;
            report.serverFirstTextSec = voice.FirstTextSeconds;
            report.serverFirstAudioSec = voice.FirstAudioSeconds;
            report.serverTotalSec = voice.TotalResponseSeconds;
            report.micCaptureOpen = voice.IsListening;

            report.conversationLogPassed = InspectConversationLog(control, report.answer, out string logNote);
            report.conversationLogCheck = logNote;

            if (string.IsNullOrEmpty(report.heard)) throw new Exception("전사 결과가 비어 있습니다.");
            if (string.IsNullOrEmpty(report.answer) || report.deltas == 0) throw new Exception("본답변 델타가 도착하지 않았습니다.");
            if (string.IsNullOrEmpty(report.responseId)) throw new Exception("response_id 를 관찰하지 못했습니다.");
            if (report.micCaptureOpen) throw new Exception("검사 중 실제 마이크가 열렸습니다.");
            if (voice.useTestProfile) throw new Exception("검사 중 useTestProfile 이 바뀌었습니다.");
            if (report.receivedAudioSamples <= 0 || report.consumedAudioSamples <= 0)
                throw new Exception("답변 음성 샘플이 재생되지 않았습니다.");
            if (report.outputPeak <= 0.001f) throw new Exception("오디오 출력이 무음입니다.");
            if (report.audioEvent == "text") throw new Exception("음성 입력인데 audio_event 가 text 입니다.");
            if (!report.conversationLogPassed) throw new Exception("대화 기록 표시 검사 실패: " + report.conversationLogCheck);

            report.passed = true;
        }
        catch (Exception e)
        {
            report.passed = false;
            report.error = e.ToString();
        }
        finally
        {
            try
            {
                if (sampler != null) EditorApplication.update -= sampler;
                if (voice != null)
                {
                    if (onDelta != null) voice.OnAnswerUpdated -= onDelta;
                    if (onError != null) voice.OnError -= onError;
                }
                // 검사가 시작한 체험만 끝낸다.
                if (startedByCheck)
                {
                    if (control != null) control.Finish();
                    if (voice != null) voice.EndExperience();
                }
                if (injecting && voice != null) voice.EndEditorAudioInjection();
                if (urlsChanged && voice != null)
                {
                    voice.serverUrl = savedServerUrl;
                    voice.dialogueServerUrl = savedDialogueUrl;
                    voice.serverReady = false;   // 원래 서버로 다시 확인하게 둔다
                    report.steps.Add("등록·대화 주소 복구");
                }
            }
            catch (Exception e)
            {
                report.passed = false;
                report.error += (string.IsNullOrEmpty(report.error) ? "" : "\n") + "정리 중 오류: " + e;
                Debug.LogError("[Scene2SurveyCheck] 정리 중 오류가 났습니다. 씬 상태를 직접 확인하세요.\n" + e);
            }

            report.limits.Add("시간 값은 파일 입력의 마지막 WAV 조각 전송 → 에디터 관측 기준이다. 사용자의 실제 발화 끝이나 스피커 출력 시각이 아니다.");
            report.limits.Add("firstAnyAudioAfterWavSec / firstAnyTextAfterWavSec 은 대기 리액션과 본답변을 구분하지 못하는 '첫 관측'이다. 본답변 기준은 serverFirstAudioSec / serverFirstTextSec 을 본다.");
            WriteReport(report, configuredOutput);
            _running = false;
        }
    }

    /// <summary>Play 모드와 씬 객체가 살아 있는지 본다. 사라졌으면 기다리지 않고 바로 끝낸다.</summary>
    static void RequireAlive(DialogueVoiceClient voice, ExperienceControl control)
    {
        if (!EditorApplication.isPlaying) throw new Exception("검사 도중 Play Mode 가 끝났습니다.");
        if (voice == null || control == null) throw new Exception("검사 도중 씬 객체가 사라졌습니다.");
    }

    static void RequireRunning(DialogueVoiceClient voice, ExperienceControl control, string failure)
    {
        if (failure != null) throw new Exception(failure);
        RequireAlive(voice, control);
        if (!voice.ExperienceActive) throw new Exception("검사 도중 체험이 끊겼습니다.");
    }

    /// <summary>ConversationLog 본문에 실제 답변이 들어갔는지 본다.</summary>
    static bool InspectConversationLog(ExperienceControl control, string answer, out string note)
    {
        if (control == null || control.log == null || control.log.body == null)
        { note = "실패: ConversationLog / body 참조가 사라졌습니다"; return false; }
        if (string.IsNullOrEmpty(answer)) { note = "실패: 비교할 답변이 없습니다"; return false; }
        // ConversationLog 는 < > 만 전각으로 바꿔 넣는다.
        string shown = answer.Replace("<", "＜").Replace(">", "＞");
        string body = control.log.body.text ?? "";
        if (body.Contains(shown)) { note = "통과: 본문에 답변 포함"; return true; }
        note = "실패: 본문에 완성 답변이 없습니다";
        return false;
    }

    static void WriteReport(CheckReport report, string configuredOutput)
    {
        string path = configuredOutput;
        bool written = false;
        try
        {
            if (string.IsNullOrEmpty(path))
                path = Path.Combine(ProjectRoot(), "tools/_work/scene2-survey-check-fallback.json");
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            path = UniquePath(path);
            report.outputPath = path;
            File.WriteAllText(path, JsonUtility.ToJson(report, true), new UTF8Encoding(false));
            written = true;
        }
        catch (Exception e)
        {
            report.passed = false;
            Debug.LogError($"[Scene2SurveyCheck] 결과 파일을 쓰지 못했습니다(검사는 실패로 처리): {path}\n{e}");
        }

        string summary = $"[Scene2SurveyCheck] passed={report.passed} case={report.caseName} " +
            $"out={(written ? path : "저장 실패")}\n{report.error}";
        if (report.passed) Debug.Log(summary);
        else Debug.LogError(summary);
    }

    /// <summary>이미 있는 결과를 덮어쓰지 않는다.</summary>
    static string UniquePath(string path)
    {
        if (!File.Exists(path)) return path;
        string dir = Path.GetDirectoryName(path);
        string name = Path.GetFileNameWithoutExtension(path);
        string ext = Path.GetExtension(path);
        for (int i = 2; i < 1000; i++)
        {
            string candidate = Path.Combine(dir, $"{name}-{i}{ext}");
            if (!File.Exists(candidate)) return candidate;
        }
        throw new Exception("결과 파일 이름이 너무 많이 중복됩니다: " + path);
    }

    static CheckConfig LoadConfig(string path)
    {
        if (!File.Exists(path)) throw new Exception("설정 파일이 없습니다: " + ConfigPath);
        var config = JsonUtility.FromJson<CheckConfig>(File.ReadAllText(path));
        if (config == null) throw new Exception("설정 파일을 읽지 못했습니다: " + ConfigPath);
        if (string.IsNullOrWhiteSpace(config.caseName)) throw new Exception("caseName 이 비어 있습니다.");
        return config;
    }

    static string ProjectRoot() => Path.GetFullPath(Path.Combine(Application.dataPath, ".."));

    /// <summary>운영 서버를 건드리지 않도록 loopback 만 허용한다.</summary>
    static string RequireLoopback(string url, string field)
    {
        if (!Uri.TryCreate((url ?? "").Trim(), UriKind.Absolute, out var uri) || uri.Scheme != Uri.UriSchemeHttp)
            throw new Exception($"{field} 은 http://호스트:포트 형식이어야 합니다.");
        string host = uri.Host;
        if (host != "127.0.0.1" && host != "localhost" && host != "::1" && host != "[::1]")
            throw new Exception($"{field} 은 loopback 주소만 허용합니다: {host}");
        return uri.GetLeftPart(UriPartial.Authority);
    }

    static string ResolveInsideProject(string root, string relative, string field)
    {
        if (string.IsNullOrWhiteSpace(relative)) throw new Exception($"{field} 이 비어 있습니다.");
        string full = Path.GetFullPath(Path.Combine(root, relative));
        string prefix = root.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(prefix, StringComparison.OrdinalIgnoreCase))
            throw new Exception($"{field} 은 프로젝트 안의 경로여야 합니다.");
        return full;
    }

    /// <summary>RIFF chunk 를 따라 읽는다. 헤더 44바이트를 가정하지 않는다.</summary>
    static byte[] ReadPcm16Mono16k(string path)
    {
        byte[] wav = File.ReadAllBytes(path);
        if (wav.Length < 12 || Encoding.ASCII.GetString(wav, 0, 4) != "RIFF" ||
            Encoding.ASCII.GetString(wav, 8, 4) != "WAVE")
            throw new Exception("WAV 파일이 아닙니다: " + Path.GetFileName(path));
        bool format = false;
        for (int offset = 12; offset + 8 <= wav.Length;)
        {
            string kind = Encoding.ASCII.GetString(wav, offset, 4);
            int size = BitConverter.ToInt32(wav, offset + 4);
            offset += 8;
            if (size < 0 || size > wav.Length - offset) break;
            if (kind == "fmt " && size >= 16)
                format = BitConverter.ToInt16(wav, offset) == 1 && BitConverter.ToInt16(wav, offset + 2) == 1 &&
                    BitConverter.ToInt32(wav, offset + 4) == 16000 && BitConverter.ToInt16(wav, offset + 14) == 16;
            if (kind == "data" && format && size > 0 && size % 2 == 0)
            {
                var pcm = new byte[size];
                Buffer.BlockCopy(wav, offset, pcm, 0, size);
                return pcm;
            }
            offset += size + (size & 1);
        }
        throw new Exception("모노 PCM16 / 16 kHz WAV 가 필요합니다: " + Path.GetFileName(path));
    }

    static async Task Until(Func<bool> predicate, int seconds, string error)
    {
        DateTime deadline = DateTime.UtcNow.AddSeconds(seconds);
        while (!predicate())
        {
            if (!EditorApplication.isPlaying) throw new Exception("검사 도중 Play Mode 가 끝났습니다.");
            if (DateTime.UtcNow >= deadline) throw new Exception(error);
            await Task.Delay(20);
        }
    }
}
