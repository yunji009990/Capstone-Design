using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading.Tasks;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
using TMPro;
using Object = UnityEngine.Object;

/// <summary>격리한 로컬 연결에서 준비·착석 입력 차단·페이드 종료를 검사한다.</summary>
[InitializeOnLoad]
public static class ExperiencePreparationValidation
{
    const string Folder = "tools/_work/experience_preparation";
    const string Running = "ExperiencePreparationValidation.Running";
    const string ScenePath = "Assets/Scenes/Scene_2.unity";
    [Serializable] sealed class Config { public string url; }
    [Serializable] sealed class Stats { public int audio_packets, starts, downloads; }
    [Serializable] sealed class Report { public bool success; public string error, voiceError, endReason, preparation; public int completedCycles, completedFades; public List<string> passed = new List<string>(); }

    static ExperiencePreparationValidation()
    {
        EditorApplication.playModeStateChanged += state =>
        {
            if (state == PlayModeStateChange.EnteredPlayMode && SessionState.GetBool(Running, false)) _ = RunPlay();
            if (state == PlayModeStateChange.EnteredEditMode && SessionState.GetBool(Running, false))
            {
                EditorApplication.delayCall += Restore;
            }
        };
        EditorApplication.delayCall += () =>
        {
            bool testScene = SceneManager.GetActiveScene().GetRootGameObjects().Any(root => root.name == "__ExperiencePreparationValidation");
            if ((SessionState.GetBool(Running, false) || testScene) && !EditorApplication.isPlayingOrWillChangePlaymode) Restore();
        };
    }

    static void Restore()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode || EditorApplication.isCompiling || EditorApplication.isUpdating)
        {
            EditorApplication.delayCall += Restore;
            return;
        }
        if (SceneManager.GetActiveScene().path != ScenePath) EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Single);
        SessionState.SetBool(Running, false);
    }

    [MenuItem("Tools/다시봄/개발 도구/대화 검사/로컬 음성 검사/체험 준비·착석 입력 제어", priority = 25)]
    public static async void Run()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode) throw new InvalidOperationException("Edit Mode에서 실행하세요.");
        if (!File.Exists(Folder + "/mock.json")) throw new InvalidOperationException("로컬 준비 검사 서버를 먼저 실행하세요. 문서의 검사 방법을 따르세요.");
        var config = JsonUtility.FromJson<Config>(File.ReadAllText(Folder + "/mock.json"));
        var uri = new Uri(config.url);
        if (!uri.IsLoopback) throw new InvalidOperationException("로컬 검사 서버만 사용할 수 있습니다.");
        var scene = SceneManager.GetActiveScene();
        if (scene.path != ScenePath) throw new InvalidOperationException("Scene_2에서 실행하세요.");
        await Configure(config.url, "{\"model_status\":404,\"model_delay\":0,\"ready_delay\":0}");
        var source = scene.GetRootGameObjects().SelectMany(root => root.GetComponentsInChildren<PersonaSpawner>(true)).FirstOrDefault();
        if (source == null || source.arrival == null) throw new InvalidOperationException("Scene_2에서 실행하세요.");
        if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;
        var ui = Object.FindObjectOfType<DialogueVoiceUI>();
        var serializedUI = ui != null ? new SerializedObject(ui) : null;
        string[] fields = { "client", "statusLabel", "messageLabel", "micDropdown" };
        var wiring = fields.Select(field => new Wiring { field = field,
            assigned = serializedUI != null && serializedUI.FindProperty(field).objectReferenceValue != null }).ToArray();
        Directory.CreateDirectory(Folder);
        File.WriteAllText(Folder + "/scene_wiring.json", JsonUtility.ToJson(new WiringReport { fields = wiring }, true));
        Require(wiring.All(field => field.assigned), "준비 안내 UI 연결 누락");
        Require(serializedUI.FindProperty("healthButton").objectReferenceValue == null, "별도 준비 확인 버튼이 남아 있습니다.");
        Require(ui.transform.root.GetComponentsInChildren<Transform>(true).All(item => item.name != "PreparationRetryButton"), "별도 준비 확인 오브젝트가 남아 있습니다.");
        CaptureUI(ui);
        var original = source.arrival;
        var idle = original.idleClip; var walk = original.walkClip; var seated = original.seatedClip;
        var reference = original.referencePose;
        EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);
        new GameObject("__ExperiencePreparationValidation");
        var voice = new GameObject("검사 음성", typeof(AudioSource)).AddComponent<DialogueVoiceClient>();
        voice.serverUrl = config.url; voice.dialogueServerUrl = config.url; voice.token = "";
        voice.sessionId = "preparation-validation"; voice.followServerSession = false;
        voice.useTestProfile = true; voice.captureRealtimeMicrophone = false;
        var spawner = new GameObject("검사 캐릭터").AddComponent<PersonaSpawner>();
        spawner.voiceClient = voice; spawner.sessionIdOverride = "preparation-validation";
        spawner.serverUrl = config.url; spawner.token = ""; spawner.retryIntervalSec = 60f;
        spawner.verboseLog = false; spawner.waitForExperienceStart = true;
        spawner.targetHeightMeters = 0f; spawner.scaleMultiplier = 1.2f;
        var seat = new GameObject("검사 의자").transform;
        spawner.spawnPoint = seat;
        var arrival = spawner.gameObject.AddComponent<PersonaArrival>();
        spawner.arrival = arrival; arrival.seat = seat;
        arrival.referencePose = reference; arrival.idleClip = idle; arrival.walkClip = walk; arrival.seatedClip = seated;
        arrival.loopSeated = true; arrival.initialIdleSeconds = .1f; arrival.walkSpeed = 1f; arrival.sitTransitionSeconds = .2f;
        var path = new GameObject("검사 경로").AddComponent<PersonaGroundPath>();
        path.SetWorldPoints(new[] { new Vector3(0, 0, -1f), Vector3.zero });
        arrival.groundPath = path;
        var control = new GameObject("검사 시작 제어").AddComponent<ExperienceControl>();
        var camera = new GameObject("검사 카메라", typeof(Camera)); camera.tag = "MainCamera";
        new GameObject("검사 조명").AddComponent<Light>().type = LightType.Directional;
        new GameObject("검사 오디오 리스너").AddComponent<AudioListener>();
        control.voice = voice; control.spawner = spawner;
        control.startButton = new GameObject("검사 시작 버튼", typeof(RectTransform)).AddComponent<Button>();
        var cues = new GameObject("검사 페이드").AddComponent<ExperienceCues>();
        cues.experience = control; cues.voice = voice; cues.spawner = spawner;
        cues.finishDarkenSec = .8f; cues.beginBrightenSec = .2f;
        SessionState.SetBool(Running, true);
        EditorApplication.EnterPlaymode();
    }

    [Serializable] sealed class Wiring { public string field; public bool assigned; }
    [Serializable] sealed class WiringReport { public Wiring[] fields; }

    static void CaptureUI(DialogueVoiceUI ui)
    {
        var source = ui.GetComponentInParent<Canvas>();
        if (source == null) return;
        var clone = Object.Instantiate(source.gameObject);
        var cameraObject = new GameObject("Preparation UI Capture");
        var target = new RenderTexture(1280, 720, 24);
        var image = new Texture2D(1280, 720, TextureFormat.RGB24, false);
        var previous = RenderTexture.active;
        try
        {
            foreach (var transform in clone.GetComponentsInChildren<Transform>(true)) transform.gameObject.hideFlags = HideFlags.DontSaveInEditor;
            foreach (var behavior in clone.GetComponentsInChildren<MonoBehaviour>(true))
                if (!(behavior is TMPro.TMP_Text) && !(behavior is UnityEngine.EventSystems.UIBehaviour)) behavior.enabled = false;
            var canvas = clone.GetComponent<Canvas>();
            var camera = cameraObject.AddComponent<Camera>(); cameraObject.hideFlags = HideFlags.DontSaveInEditor;
            camera.clearFlags = CameraClearFlags.SolidColor; camera.backgroundColor = Color.black;
            camera.targetTexture = target; camera.cullingMask = 1 << source.gameObject.layer;
            camera.transform.position = new Vector3(0, 10000, 0);
            camera.stereoTargetEye = StereoTargetEyeMask.None;
            canvas.renderMode = RenderMode.ScreenSpaceCamera; canvas.worldCamera = camera; canvas.planeDistance = 1f;
            clone.transform.Find("Backdrop/StatusCard/InfoText").GetComponent<TMP_Text>().text = "체험 준비 검사\n실제 인물 정보 없음";
            var message = clone.transform.Find("Backdrop/SettingsCard/PreparationMessage").GetComponent<TMP_Text>();
            message.text = "대화 서버 연결 필요\n마이크 연결·선택 필요\n캐릭터 모델 준비 중";
            clone.transform.Find("Backdrop/StatusCard/StateRow/StatusLabel").GetComponent<TMP_Text>().text = "캐릭터 모델 확인 필요";
            clone.transform.Find("Backdrop/SettingsCard/StartButton").GetComponentInChildren<TMP_Text>().text = "체험 시작";
            Canvas.ForceUpdateCanvases();
            camera.Render(); RenderTexture.active = target;
            image.ReadPixels(new Rect(0, 0, 1280, 720), 0, 0); image.Apply();
            File.WriteAllBytes(Folder + "/preparation_ui.png", image.EncodeToPNG());
        }
        finally
        {
            RenderTexture.active = previous; target.Release();
            Object.DestroyImmediate(clone); Object.DestroyImmediate(cameraObject);
            Object.DestroyImmediate(target); Object.DestroyImmediate(image);
        }
    }


    static async Task RunPlay()
    {
        var report = new Report();
        ExperienceControl control = null;
        try
        {
            control = Object.FindObjectOfType<ExperienceControl>();
            var voice = control.voice;
            var spawner = control.spawner;
            string url = voice.DialogueServerUrl;
            await Wait(() => voice.serverReady && spawner.Preparation == PersonaSpawner.PreparationPhase.모델대기);
            Require(!control.CanStart && control.startButton.interactable && string.IsNullOrEmpty(control.StartWarning), "시작 전 안내·버튼 상태 오류");
            control.startButton.onClick.Invoke();
            Require(!control.Started && !voice.ExperienceActive && control.StartWarning.Contains("캐릭터 모델 준비 중"), "시작 클릭 시 모델 미완료 안내 실패");
            await Wait(() => !control.CheckingPreparation);
            Require(!control.Started && control.startButton.interactable, "미완료 확인 후 재시도 버튼 복구 실패");
            report.passed.Add("시작 전 안내 숨김·미준비 클릭 허용·미완료 표시·실제 시작 차단");
            await Configure(url, "{\"model_status\":503}");
            spawner.RetryPreparation();
            await Wait(() => spawner.Preparation == PersonaSpawner.PreparationPhase.실패);
            await Task.Delay(50);
            Require(!control.CanStart && control.StartWarning.Contains("캐릭터 불러오기 실패"), "모델 다운로드 실패의 안내·시작 차단 실패");
            report.passed.Add("다운로드 실패 안내");
            await Configure(url, "{\"model_status\":200,\"model_delay\":1}");
            spawner.RetryPreparation();
            await Task.Delay(100);
            control.Begin();
            Require(!control.Started && !voice.ExperienceActive, "다운로드 중 시작 허용");
            await Wait(() => control.CanStart, 35f);
            Require(string.IsNullOrEmpty(control.StartWarning), "모델 복구 후 안내 제거 실패");
            report.passed.Add("지연 다운로드·수동 재시도·모델 및 리깅 준비");

            voice.captureRealtimeMicrophone = true;
            Set(voice, "_micDevices", Array.Empty<string>()); Set(voice, "_micDevice", null);
            Set(voice, "_nextMicRefresh", float.MaxValue);
            await Task.Delay(100);
            Require(control.Preparation == ExperienceControl.PreparationState.마이크필요 && !control.CanStart, "마이크 없음 안내 실패");
            control.Begin(); Require(!control.Started, "마이크 없는 시작 허용");
            Require(control.StartWarning.Contains("마이크 연결·선택 필요"), "마이크 미완료 항목 누락");
            voice.captureRealtimeMicrophone = false; voice.RefreshMicrophones();
            await Wait(() => control.CanStart);
            report.passed.Add("마이크 없음·준비 복구");

            control.Begin();
            Require(string.IsNullOrEmpty(control.StartWarning), "정상 시작 뒤 미완료 안내가 남음");
            await Wait(() => voice.DialogueConnected);
            Require(!voice.ConversationInputAllowed && !voice.SendTestAudio(new byte[2560]) && !voice.SendTestText("blocked"), "입장 중 입력 허용");
            await FinishAndCheck(control, report);
            report.passed.Add("입장 중 입력 차단·취소");

            for (int cycle = 0; cycle < 3; cycle++)
            {
                await Configure(url, cycle == 1 ? "{\"ready_delay\":5}" : "{\"ready_delay\":0}");
                await Wait(() => control.CanStart);
                control.startButton.onClick.Invoke();
                Require(control.Started, "실제 시작 버튼 연결 실패");
                await CheckReveal();
                if (cycle == 1)
                {
                    await Wait(() => spawner.ReadyForConversation && !voice.DialogueConnected);
                    await Task.Delay(50);
                    Require(control.Preparation == ExperienceControl.PreparationState.음성연결중 && !voice.SendTestAudio(new byte[2560]), "착석 후 음성 준비 대기 실패");
                }
                else
                {
                    await Wait(() => voice.DialogueConnected);
                    Require(!spawner.ReadyForConversation && !voice.ConversationInputAllowed && !voice.SendTestAudio(new byte[2560]), "음성 선연결 시 입장 중 입력 허용");
                }
                var before = await Get<Stats>(url + "/stats");
                await Wait(() => spawner.ReadyForConversation && voice.DialogueConnected && voice.ConversationInputAllowed);
                Require(voice.SendTestAudio(new byte[2560]), "착석 후 입력 열기 실패");
                await Task.Delay(150);
                var after = await Get<Stats>(url + "/stats");
                Require(after.audio_packets == before.audio_packets + 1, "실제 WebSocket PCM 전달 실패");
                await FinishAndCheck(control, report, button: true, paused: cycle == 2);
                report.completedCycles++;
            }
            report.passed.Add("음성 선연결·착석 선완료·시작 종료 3회·실제 PCM 전달");
            report.passed.Add("페이드 완료 뒤 인물 숨김·입력 즉시 차단·연속 클릭 차단·검은 화면 유지·재시작 밝아짐·timeScale 0 종료");
            await Configure(url, "{\"ready_delay\":0}");
            control.voiceOnlyExperience = true;
            Set(spawner, "_modelReady", false);
            await Wait(() => control.CanStart);
            control.Begin();
            await Wait(() => voice.DialogueConnected);
            Require(voice.ConversationInputAllowed && !spawner.PresentationStarted && voice.SendTestAudio(new byte[2560]), "명시적 음성 전용 체험 실패");
            await FinishAndCheck(control, report);
            control.voiceOnlyExperience = false; Set(spawner, "_modelReady", true);
            report.passed.Add("모델 없는 명시적 음성 전용 체험");
            await Wait(() => control.CanStart);
            control.Begin(); await Wait(() => voice.DialogueConnected);
            control.Finish();
            Require(control.IsFinishing, "연출 비활성화 검사 시작 실패");
            var cues = Object.FindObjectOfType<ExperienceCues>();
            cues.enabled = false;
            await Wait(() => !control.IsFinishing);
            Require(!spawner.PresentationStarted && !voice.ExperienceActive, "페이드 비활성화 시 정리 실패");
            cues.enabled = true;
            report.passed.Add("페이드 도중 연출 비활성화 시 종료 복구");
            await Wait(() => control.CanStart);
            control.Begin(); await Wait(() => voice.DialogueConnected);
            voice.EndExperience("validation_disconnect");
            await Wait(() => !control.Started);
            Require(!spawner.PresentationStarted && !voice.IsListening, "연결 종료 시 입장·마이크 정리 실패");
            report.passed.Add("연결 종료 시 입장 취소");
            report.success = true;
        }
        catch (Exception error)
        {
            report.error = error.GetType().Name + ": " + error.Message;
            if (control != null && control.voice != null)
            {
                report.voiceError = control.voice.LastError;
                report.endReason = control.voice.LastEndReason;
                report.preparation = control.Preparation.ToString();
            }
        }
        finally
        {
            if (control != null) control.Finish();
            Directory.CreateDirectory(Folder);
            File.WriteAllText(Folder + "/validation.json", JsonUtility.ToJson(report, true));
            EditorApplication.ExitPlaymode();
        }
    }

    static async Task FinishAndCheck(ExperienceControl control, Report report, bool button = false, bool paused = false)
    {
        var cues = Object.FindObjectOfType<ExperienceCues>();
        bool presented = control.spawner.PresentationStarted;
        float previous = cues.fadeGroup.alpha;
        float timeScale = Time.timeScale;
        try
        {
            if (paused) Time.timeScale = 0f;
            if (button) control.startButton.onClick.Invoke(); else control.Finish();
            Require(control.IsFinishing && !control.Started && !control.CanStart && !control.startButton.interactable, "페이드 중 시작 차단 실패");
            Require(!control.voice.ExperienceActive && !control.voice.IsListening && !control.voice.SendTestAudio(new byte[2560]), "종료 요청 직후 입력·음성 정리 실패");
            Require(control.spawner.PresentationStarted == presented, "화면을 덮기 전에 인물을 숨김");
            control.Finish(); control.Begin();
            Require(control.IsFinishing && !control.Started, "중복 종료·재시작 차단 실패");
            double until = EditorApplication.timeSinceStartup + 4f;
            while (control.IsFinishing)
            {
                Require(cues.fadeGroup.alpha + .001f >= previous, "페이드아웃 도중 화면이 밝아짐");
                previous = cues.fadeGroup.alpha;
                if (previous < .999f) Require(control.spawner.PresentationStarted == presented, "페이드 완료 전에 인물을 숨김");
                Require(EditorApplication.timeSinceStartup < until, "페이드 종료 시간 초과");
                await Task.Delay(15);
            }
            Require(cues.fadeGroup.alpha >= .999f && !control.spawner.PresentationStarted, "완전히 어두워진 뒤 종료 실패");
            await Task.Delay(100);
            control.Finish();
            Require(cues.fadeGroup.alpha >= .999f, "종료 후 검은 화면 유지 실패");
            report.completedFades++;
        }
        finally { Time.timeScale = timeScale; }
    }

    static async Task CheckReveal()
    {
        var cues = Object.FindObjectOfType<ExperienceCues>();
        float previous = cues.fadeGroup.alpha;
        double until = EditorApplication.timeSinceStartup + 4f;
        while (cues.fadeGroup.alpha > .001f)
        {
            await Task.Delay(15);
            Require(cues.fadeGroup.alpha <= previous + .001f, "재시작 페이드 도중 화면이 다시 어두워짐");
            previous = cues.fadeGroup.alpha;
            Require(EditorApplication.timeSinceStartup < until, "재시작 밝아짐 시간 초과");
        }
    }

    static void Set(object target, string name, object value) => target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(target, value);
    static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
    static async Task Wait(Func<bool> condition, float timeout = 20f)
    {
        double until = EditorApplication.timeSinceStartup + timeout;
        while (!condition())
        {
            if (!EditorApplication.isPlaying || EditorApplication.timeSinceStartup > until) throw new TimeoutException("준비·착석 상태 전환 대기 시간 초과");
            await Task.Delay(25);
        }
    }
    static async Task<T> Get<T>(string url)
    {
        using (var request = UnityWebRequest.Get(url))
        {
            request.timeout = 3; var operation = request.SendWebRequest();
            while (!operation.isDone) await Task.Delay(15);
            Require(request.result == UnityWebRequest.Result.Success, "로컬 검사 서버 연결 실패");
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
            var operation = request.SendWebRequest(); while (!operation.isDone) await Task.Delay(15);
            Require(request.result == UnityWebRequest.Result.Success, "로컬 검사 조건 변경 실패");
        }
    }
}
