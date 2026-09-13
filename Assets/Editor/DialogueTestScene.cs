using System;
using System.IO;
using System.Threading.Tasks;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static partial class DialogueTestScene
{
    public const string ScenePath = "Assets/Scenes/AI_Response_Test.unity";
    static bool _checking;

    [Serializable]
    class CheckResult
    {
        public bool passed;
        public bool ttsEnabled, typedInput;
        public string answer, transcript, emotion, audioEvent, route, error;
        public string voiceMode, referenceSource;
        public float referenceSeconds;
        public int deltas;
        public long audioSamples;
        public float outputPeak;
        public float firstTextSeconds, firstAudioSeconds, totalSeconds;
    }

    [MenuItem("Tools/Dialogue/Run AI test scene check", priority = 10)]
    public static void CheckRunningScene() => RunCheck();

    [MenuItem("Tools/Dialogue/Run voice interruption check", priority = 11)]
    public static void CheckInterruption() => RunSemanticCheck();

    [MenuItem("Tools/Dialogue/Run reference upload voice check", priority = 12)]
    public static void CheckReferenceUpload() => RunCheck(true);

    [MenuItem("Tools/Dialogue/Run text response check", priority = 14)]
    public static void CheckTextResponse() => RunCheck(false, true);

    [MenuItem("Tools/Dialogue/Run reference preview check", priority = 13)]
    public static async void CheckReferencePreview()
    {
        if (_checking) return;
        var panel = UnityEngine.Object.FindObjectOfType<DialogueTestPanel>();
        if (!EditorApplication.isPlaying || panel == null || panel.Voice.ExperienceActive) return;
        _checking = true;
        string work = Path.GetFullPath(Path.Combine(Application.dataPath, "../.dialogue-work"));
        var result = new CheckResult();
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(60);
            while (panel.CheckingServer && DateTime.UtcNow < deadline) await Task.Delay(100);
            panel.StartCoroutine(panel.LoadReferenceFile(Path.Combine(work, "models/sensevoice/test_wavs/ko.wav")));
            while (panel.CheckingReference && DateTime.UtcNow < deadline) await Task.Delay(100);
            if (string.IsNullOrEmpty(panel.SelectedReferenceId)) throw new Exception("Reference upload failed.");
            panel.PreviewReferenceForCheck();
            while (!panel.ReferencePreviewPlaying && DateTime.UtcNow < deadline) await Task.Delay(50);
            if (!panel.ReferencePreviewPlaying) throw new Exception("Reference preview did not play.");
            ScreenCapture.CaptureScreenshot(Path.Combine(work, "ai-reference-settings.png"));
            await Task.Delay(600);
            result.passed = true;
        }
        catch (Exception e) { result.error = e.ToString(); }
        finally
        {
            if (panel != null) panel.CloseReferenceForCheck();
            File.WriteAllText(Path.Combine(work, "ai-reference-preview.json"), JsonUtility.ToJson(result, true));
            Debug.Log("[ReferencePreviewCheck] " + JsonUtility.ToJson(result));
            _checking = false;
        }
    }

    static async void RunCheck(bool uploadReference = false, bool typedInput = false)
    {
        if (_checking) return;
        var panel = UnityEngine.Object.FindObjectOfType<DialogueTestPanel>();
        if (!EditorApplication.isPlaying || panel == null)
        { Debug.LogWarning("Open the AI test scene and enter Play Mode first."); return; }
        _checking = true;
        var result = new CheckResult();
        Action<string, string> onDelta = (heard, answer) => { if (!string.IsNullOrEmpty(answer)) result.deltas++; };
        string work = Path.GetFullPath(Path.Combine(Application.dataPath, "../.dialogue-work"));
        try
        {
            var deadline = DateTime.UtcNow.AddSeconds(20);
            // Wait for the panel's initial health request, then make a single connection attempt.
            while (panel != null && (panel.CheckingServer || !panel.Voice.serverReady) && DateTime.UtcNow < deadline)
                await Task.Delay(100);
            if (panel == null) throw new Exception("Test scene was closed.");
            if (uploadReference)
            {
                panel.StartCoroutine(panel.LoadReferenceFile(Path.Combine(work, "models/sensevoice/test_wavs/ko.wav")));
                deadline = DateTime.UtcNow.AddSeconds(65);
                while (panel != null && panel.CheckingReference && DateTime.UtcNow < deadline) await Task.Delay(100);
                if (panel == null || string.IsNullOrEmpty(panel.SelectedReferenceId)) throw new Exception("Reference upload failed.");
            }
            if (!panel.BeginAudioFileCheck()) throw new Exception("Audio check did not start.");
            deadline = DateTime.UtcNow.AddSeconds(90);
            while (panel != null && !panel.Voice.DialogueConnected && DateTime.UtcNow < deadline)
            {
                await Task.Delay(100);
            }
            if (panel == null || !panel.Voice.DialogueConnected) throw new Exception("Test scene did not connect.");
            var voice = panel.Voice;
            result.ttsEnabled = voice.TtsEnabled;
            result.typedInput = typedInput;
            result.voiceMode = voice.VoiceMode;
            result.referenceSource = voice.ActiveReference?.source;
            result.referenceSeconds = voice.ActiveReference?.duration_sec ?? 0;
            if (voice.TtsEnabled && (result.voiceMode != "reference_icl" || result.referenceSeconds < 3 ||
                (uploadReference && voice.ActiveReference.reference_id != panel.SelectedReferenceId)))
                throw new Exception("Reference conditioning was not enabled.");
            voice.OnAnswerUpdated += onDelta;
            voice.ReportUnityAction("사용자가 가족 사진을 집어 들었다.");
            if (typedInput)
            {
                if (!voice.SendTestText("내가 지금 들고 있는 게 뭐야?")) throw new Exception("Text send failed.");
            }
            else
            {
                await SendSemanticPcm(voice, Path.Combine(work, "models/sensevoice/test_wavs/ko.wav"));
            }
            deadline = DateTime.UtcNow.AddSeconds(90);
            while (panel != null && !voice.PlaybackFinished && DateTime.UtcNow < deadline)
            {
                result.audioSamples = Math.Max(result.audioSamples, voice.ReceivedAudioSamples);
                await Task.Delay(100);
            }
            if (panel == null || !voice.PlaybackFinished) throw new Exception("Answer did not finish.");
            result.answer = voice.lastAnswer;
            result.transcript = voice.lastHeard;
            result.emotion = voice.VoiceEmotion;
            result.audioEvent = voice.VoiceAudioEvent;
            result.firstAudioSeconds = voice.FirstAudioSeconds;
            result.route = voice.ResponseRoute;
            result.firstTextSeconds = voice.FirstTextSeconds;
            result.totalSeconds = voice.TotalResponseSeconds;
            result.outputPeak = voice.OutputPeak;
            if (result.deltas == 0 || string.IsNullOrEmpty(result.answer) ||
                string.IsNullOrEmpty(result.transcript) || !panel.ConversationText.Contains(result.answer) ||
                (!typedInput && voice.VoiceEmotion == "unknown") ||
                (voice.TtsEnabled && (result.audioSamples == 0 || result.outputPeak <= 0.001f)) ||
                (!voice.TtsEnabled && (result.audioSamples != 0 || voice.IsSpeaking)) ||
                (typedInput && (!result.answer.Contains("사진") || result.audioEvent != "text")))
                throw new Exception("Response, context, or output mode check failed.");
            result.passed = true;
            Directory.CreateDirectory(work);
            ScreenCapture.CaptureScreenshot(Path.Combine(work, typedInput ? "ai-text-scene-tested.png" : "ai-voice-scene-tested.png"));
        }
        catch (Exception e) { result.error = e.ToString(); }
        finally
        {
            if (panel != null)
            {
                panel.Voice.OnAnswerUpdated -= onDelta;
                panel.Voice.EndExperience();
            }
            Directory.CreateDirectory(work);
            File.WriteAllText(Path.Combine(work, typedInput ? "ai-text-scene-smoke.json" : uploadReference ? "ai-reference-upload.json" :
                "ai-voice-scene-smoke.json"), JsonUtility.ToJson(result, true));
            Debug.Log("[DialogueTestCheck] " + JsonUtility.ToJson(result));
            _checking = false;
        }
    }

    [MenuItem("Tools/Dialogue/Open AI test scene", priority = 0)]
    public static void Open()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode)
        { Debug.LogWarning("Stop Play Mode before opening the AI test scene."); return; }
        if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
        CreateAssetIfMissing();
        EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
    }

    [MenuItem("Tools/Dialogue/Create AI test scene asset", priority = 1)]
    public static void CreateAssetIfMissing()
    {
        if (File.Exists(ScenePath)) return;
        if (EditorApplication.isPlayingOrWillChangePlaymode) return;
        var previous = SceneManager.GetActiveScene();
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Additive);
        try
        {
            SceneManager.SetActiveScene(scene);
            var camera = new GameObject("AI test camera", typeof(Camera), typeof(AudioListener));
            camera.tag = "MainCamera";
            var cam = camera.GetComponent<Camera>();
            cam.clearFlags = CameraClearFlags.SolidColor;
            cam.backgroundColor = new Color(.035f, .055f, .095f);
            cam.stereoTargetEye = StereoTargetEyeMask.None;
            var light = new GameObject("Directional Light", typeof(Light)).GetComponent<Light>();
            light.type = LightType.Directional;
            light.transform.rotation = Quaternion.Euler(45, -30, 0);
            var root = new GameObject("AI response test", typeof(DialogueVoiceClient));
            var voice = root.GetComponent<DialogueVoiceClient>();
            // The AI test panel owns connection preferences; scene creation never changes them.
            voice.token = "";
            voice.useTestProfile = true;
            voice.followServerSession = false;
            voice.dialogueServerUrl = "http://220.69.208.201:8002";
            var panel = root.AddComponent<DialogueTestPanel>();
            panel.koreanFont = AssetDatabase.LoadAssetAtPath<Font>("Assets/Scripts/Raon/Fonts/malgun.ttf");
            if (panel.koreanFont == null) throw new InvalidOperationException("Korean test UI font was not found.");
            if (!EditorSceneManager.SaveScene(scene, ScenePath)) throw new IOException("Could not save AI test scene.");
            Debug.Log("[DialogueTest] Created " + ScenePath);
        }
        finally
        {
            if (previous.IsValid() && previous.isLoaded) SceneManager.SetActiveScene(previous);
            EditorSceneManager.CloseScene(scene, true);
        }
    }

}
