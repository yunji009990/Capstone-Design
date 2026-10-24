using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using TMPro;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

/// <summary>실제 시작·카페 씬을 사용하되 통신과 음성 입력은 loopback 검사로 격리한다.</summary>
[InitializeOnLoad]
public static class StartSceneMergeValidation
{
    const string Folder = "tools/_work/merge_hj_20261024";
    const string Running = "StartSceneMergeValidation.Running";
    const string RestorePending = "StartSceneMergeValidation.RestorePending";
    const string LocalUrl = "StartSceneMergeValidation.LocalUrl";
    const string StartPath = "Assets/Scenes/StartScene.unity";
    const string CafePath = "Assets/Scenes/Scene_2.unity";
    [Serializable] sealed class Config { public string url; public uint[] union; }
    [Serializable] sealed class Stats { public int starts; }
    [Serializable] sealed class Report
    {
        public bool success;
        public string stage, error;
        public int backgrounds, fontCharacters;
        public List<string> passed = new List<string>();
    }

    static StartSceneMergeValidation()
    {
        SceneManager.sceneLoaded -= ConfigureCafe;
        SceneManager.sceneLoaded += ConfigureCafe;
        EditorApplication.playModeStateChanged += state =>
        {
            if (!SessionState.GetBool(Running, false)) return;
            if (state == PlayModeStateChange.EnteredPlayMode) _ = RunPlay();
        };
        // 숨겨진 Editor 창에서도 실행되는 update에서 종료 후 복귀를 처리한다.
        EditorApplication.update += () =>
        {
            if (SessionState.GetBool(RestorePending, false) && !EditorApplication.isPlayingOrWillChangePlaymode) Restore();
        };
    }

    [MenuItem("Tools/다시봄/개발 도구/대화 검사/로컬 음성 검사/시작 화면·배경 전환", priority = 26)]
    public static void Run()
    {
        Require(!EditorApplication.isPlayingOrWillChangePlaymode, "Edit Mode에서 실행하세요.");
        Require(!SceneManager.GetActiveScene().isDirty, "현재 씬을 저장한 뒤 실행하세요.");
        var config = JsonUtility.FromJson<Config>(File.ReadAllText("tools/_work/experience_preparation/mock.json"));
        Require(new Uri(config.url).IsLoopback, "loopback 검사 서버만 사용할 수 있습니다.");
        var scenes = EditorBuildSettings.scenes;
        Require(scenes.Length > 0 && scenes[0].path == StartPath && scenes[0].enabled, "시작 씬 빌드 순서 오류");
        Require(scenes.Any(s => s.path == CafePath && s.enabled), "카페 씬이 빌드 목록에 없습니다.");

        // 병합 때만 존재하는 목록으로 양쪽 동적 글리프를 보존한다. 다른 PC에서는 현재 목록을 사용한다.
        var font = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>("Assets/Scripts/Raon/Fonts/Malgun SDF.asset");
        string charsPath = Folder + "/font_characters.json";
        if (File.Exists(charsPath))
        {
            var chars = JsonUtility.FromJson<Config>(File.ReadAllText(charsPath));
            var toAdd = chars.union.Where(c => !font.characterLookupTable.ContainsKey(c)).ToArray();
            if (toAdd.Length > 0)
                Require(font.TryAddCharacters(toAdd, out uint[] missing) || missing == null || missing.Length == 0,
                    "병합한 한글 글리프를 추가하지 못했습니다.");
            Require(chars.union.All(c => font.characterLookupTable.ContainsKey(c)), "기존 한글 글리프 유실");
            EditorUtility.SetDirty(font);
            AssetDatabase.SaveAssetIfDirty(font);
        }
        SessionState.SetString(LocalUrl, config.url);
        SessionState.SetBool(RestorePending, false);
        EditorSceneManager.OpenScene(StartPath, OpenSceneMode.Single);
        SessionState.SetBool(Running, true);
        EditorApplication.EnterPlaymode();
    }

    static void ConfigureCafe(Scene scene, LoadSceneMode mode)
    {
        if (!EditorApplication.isPlaying || !SessionState.GetBool(Running, false) || scene.path != CafePath) return;
        string url = SessionState.GetString(LocalUrl, "");
        Require(new Uri(url).IsLoopback, "로컬 검사 주소가 유실됐습니다.");
        // sceneLoaded는 Start보다 먼저 호출된다. 운영 인물 조회·음성 연결을 시작하기 전에 격리한다.
        foreach (var root in scene.GetRootGameObjects())
        {
            foreach (var voice in root.GetComponentsInChildren<DialogueVoiceClient>(true))
            {
                voice.serverUrl = url; voice.dialogueServerUrl = url; voice.token = "";
                voice.sessionId = "preparation-validation"; voice.followServerSession = false;
                voice.useTestProfile = true; voice.captureRealtimeMicrophone = false;
                voice.autoDetect = false; voice.checkHealthOnStart = true;
            }
            foreach (var spawner in root.GetComponentsInChildren<PersonaSpawner>(true))
            {
                spawner.serverUrl = url; spawner.token = "";
                spawner.sessionIdOverride = "preparation-validation";
                spawner.verboseLog = false; spawner.retryIntervalSec = 60f;
            }
        }
    }

    static async Task RunPlay()
    {
        var report = new Report();
        ExperienceControl control = null;
        string url = SessionState.GetString(LocalUrl, "");
        try
        {
            var font = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>("Assets/Scripts/Raon/Fonts/Malgun SDF.asset");
            report.fontCharacters = font.characterTable.Count;
            report.passed.Add("시작 씬 빌드 등록·양쪽 한글 글리프 보존");
            foreach (var background in new[] { StartSelection.Background.Pastel, StartSelection.Background.Moonlit, StartSelection.Background.Garden })
            {
                await Wait(() => SceneManager.GetActiveScene().path == StartPath);
                await Task.Delay(600);
                var screen = Object.FindObjectOfType<StartScreen>();
                Require(screen != null && screen.backgroundButtons.Length == 3 && screen.startButton != null, "시작 UI 연결 오류");
                Require(Object.FindObjectsOfType<UnityEngine.EventSystems.EventSystem>().Length == 1, "시작 씬 EventSystem 중복·누락");
                Require(Object.FindObjectOfType<VRUIPointer>() != null, "시작 씬 VR 포인터 누락");
                if (report.backgrounds == 0)
                {
                    screen.experienceScene = "__missing_merge_validation_scene__";
                    screen.startButton.onClick.Invoke();
                    await Task.Delay(100);
                    Require(screen.startButton.interactable && !StartSelection.startRequested &&
                        screen.backgroundButtons.All(b => b.button.interactable) && !string.IsNullOrEmpty(screen.statusText.text),
                        "씬 열기 실패 뒤 시작 UI 복구 실패");
                    screen.experienceScene = "Scene_2";
                    report.passed.Add("잘못된 다음 씬 안내·버튼 복구");
                    screen.statusText.text = "";
                    CaptureStartScreen();
                }
                await Configure(url, "{\"model_status\":404,\"model_delay\":1,\"ready_delay\":0}");
                int startsBefore = (await Get<Stats>(url + "/stats")).starts;
                screen.backgroundButtons.First(b => b.background == background).button.onClick.Invoke();
                report.stage = background + " 카페 전환"; Save(report);
                screen.startButton.onClick.Invoke();
                await Wait(() => SceneManager.GetActiveScene().path == CafePath);
                control = Object.FindObjectOfType<ExperienceControl>();
                Require(control != null && control.spawner != null, "카페 체험 제어 연결 누락");
                var voice = control.voice;
                Require(new Uri(voice.DialogueServerUrl).IsLoopback, "검사 중 운영 서버 연결 금지");
                await Wait(() => voice.serverReady && control.spawner.Preparation == PersonaSpawner.PreparationPhase.모델대기);
                Require(!control.Started && !control.CanStart && StartSelection.startRequested, "모델 대기 중 자동 시작됨");
                Require(!voice.ConversationInputAllowed || !voice.ExperienceActive, "준비 중 대화 시작됨");

                var state = Object.FindObjectOfType<CafeSkyThemeState>(true);
                var set = Resources.Load<CafeBackgroundSet>(CafeBackgroundSet.ResourceName);
                Require(state != null && set != null, "카페 배경 상태·재질 누락");
                bool sky = background != StartSelection.Background.Garden;
                Require(state.skyActive == sky && state.gameObject.activeSelf == sky, "선택한 배경 활성 상태 오류");
                if (sky)
                {
                    bool pastel = background == StartSelection.Background.Pastel;
                    Require(RenderSettings.skybox == (pastel ? set.pastelSky : set.moonlitSky), "선택한 하늘 재질 오류");
                    Require(state.appearance == (pastel ? CafeSkyThemeState.Appearance.Pastel : CafeSkyThemeState.Appearance.Moonlit), "선택한 구름 색감 오류");
                }
                else Require(RenderSettings.skybox == state.originalSky, "정원 하늘 복원 오류");
                var hud = Object.FindObjectOfType<OperatorHUD>(true);
                Require(hud != null && hud.transform.Find("Backdrop/StatusCard/_Face") != null, "씬 전환 뒤 운영자 테마 미적용");
                Require(hud.transform.Find("Backdrop/SettingsCard/PreparationMessage") != null, "준비 안내 UI 유실");

                // 자동 시작 요청이 있어도 마이크·모델 조건을 함께 통과해야 한다.
                voice.captureRealtimeMicrophone = true;
                Set(voice, "_micDevices", Array.Empty<string>()); Set(voice, "_micDevice", null);
                Set(voice, "_nextMicRefresh", float.MaxValue);
                await Configure(url, "{\"model_status\":200}");
                control.spawner.RetryPreparation();
                await Wait(() => control.spawner.ModelReady, 50f);
                await Task.Delay(100);
                Require(!control.Started && !control.CanStart && StartSelection.startRequested, "마이크 없는 자동 시작 허용");
                voice.captureRealtimeMicrophone = false;
                voice.RefreshMicrophones();
                await Wait(() => control.Started && voice.DialogueConnected);
                Require(!StartSelection.startRequested, "자동 시작 요청 미소비");
                Require(!voice.ConversationInputAllowed && !voice.SendTestAudio(new byte[2560]), "입장 중 새 입력 허용");
                report.stage = background + " 착석 대기"; Save(report);
                await Wait(() => control.spawner.ReadyForConversation && voice.ConversationInputAllowed, 50f);
                Require(voice.SendTestAudio(new byte[2560]), "착석 후 검사 PCM 입력 거부");
                var stats = await Get<Stats>(url + "/stats");
                Require(stats.starts == startsBefore + 1, "한 번의 자동 시작에서 음성 연결이 중복됨");
                int starts = stats.starts;
                control.Finish();
                Require(!voice.ExperienceActive && !control.Started, "종료 직후 음성·입력 중지 실패");
                await Wait(() => !control.IsFinishing);
                await Task.Delay(300);
                Require(!control.Started && !control.spawner.PresentationStarted, "종료 후 자동 재시작됨");
                Require((await Get<Stats>(url + "/stats")).starts == starts, "종료 후 음성 연결 자동 재시도");
                report.backgrounds++;
                report.passed.Add(background + " 선택·실제 씬 전환·준비 차단·자동 시작·착석 입력·페이드 종료");
                Save(report);
                control = null;
                var load = SceneManager.LoadSceneAsync("StartScene");
                await Wait(() => load.isDone);
            }
            report.success = true;
            report.stage = "완료";
        }
        catch (Exception error) { report.error = error.GetType().Name + ": " + error.Message; }
        finally
        {
            if (control != null) control.Finish();
            StartSelection.startRequested = false;
            StartSelection.background = StartSelection.Background.Keep;
            Save(report);
            SessionState.SetBool(RestorePending, true);
            EditorApplication.ExitPlaymode();
        }
    }

    static void Restore()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
            return;
        if (SceneManager.GetActiveScene().path != CafePath) EditorSceneManager.OpenScene(CafePath, OpenSceneMode.Single);
        SessionState.SetBool(Running, false);
        SessionState.SetBool(RestorePending, false);
    }

    static void CaptureStartScreen()
    {
        // Game View의 편집용 Gizmo 표시와 분리해 실제 카메라의 시작 UI만 촬영한다.
        var camera = Camera.main;
        Require(camera != null, "시작 화면 카메라 누락");
        var target = RenderTexture.GetTemporary(1920, 1080, 24);
        var image = new Texture2D(1920, 1080, TextureFormat.RGB24, false);
        var previous = camera.targetTexture;
        var previousActive = RenderTexture.active;
        var stereo = camera.stereoTargetEye;
        try
        {
            Canvas.ForceUpdateCanvases();
            camera.stereoTargetEye = StereoTargetEyeMask.None;
            camera.targetTexture = target;
            camera.Render(); RenderTexture.active = target;
            image.ReadPixels(new Rect(0, 0, target.width, target.height), 0, 0); image.Apply();
            Directory.CreateDirectory(Folder);
            File.WriteAllBytes(Folder + "/start_screen.png", image.EncodeToPNG());
        }
        finally
        {
            camera.targetTexture = previous; camera.stereoTargetEye = stereo;
            RenderTexture.active = previousActive;
            RenderTexture.ReleaseTemporary(target);
            Object.DestroyImmediate(image);
        }
    }

    static void Save(Report report)
    {
        Directory.CreateDirectory(Folder);
        File.WriteAllText(Folder + "/validation.json", JsonUtility.ToJson(report, true));
    }
    static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    static void Set(object target, string field, object value) => target.GetType().GetField(field, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(target, value);
    static async Task Wait(Func<bool> condition, float timeout = 20f)
    {
        double until = EditorApplication.timeSinceStartup + timeout;
        while (!condition())
        {
            Require(EditorApplication.isPlaying, "검사 중 Play가 종료됐습니다.");
            if (EditorApplication.timeSinceStartup > until) throw new TimeoutException("시작 씬·체험 상태 전환 시간 초과");
            await Task.Delay(25);
        }
    }
    static async Task<T> Get<T>(string url)
    {
        using (var request = UnityWebRequest.Get(url))
        {
            request.timeout = 3;
            var op = request.SendWebRequest(); while (!op.isDone) await Task.Delay(15);
            Require(request.result == UnityWebRequest.Result.Success, "로컬 검사 서버 조회 실패");
            return JsonUtility.FromJson<T>(request.downloadHandler.text);
        }
    }
    static async Task Configure(string url, string body)
    {
        using (var request = new UnityWebRequest(url + "/control", "POST"))
        {
            request.uploadHandler = new UploadHandlerRaw(System.Text.Encoding.UTF8.GetBytes(body));
            request.downloadHandler = new DownloadHandlerBuffer(); request.timeout = 3;
            request.SetRequestHeader("Content-Type", "application/json");
            var op = request.SendWebRequest(); while (!op.isDone) await Task.Delay(15);
            Require(request.result == UnityWebRequest.Result.Success, "로컬 검사 조건 변경 실패");
        }
    }
}
