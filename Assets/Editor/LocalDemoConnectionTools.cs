using System;
using System.Diagnostics;
using System.IO;
using UnityEditor;
using UnityEngine;
using Debug = UnityEngine.Debug;

/// <summary>씬·프리팹을 수정하지 않는 실행 환경 선택과 로컬 서비스 제어.</summary>
public static class LocalDemoConnectionTools
{
    const string RootMenu = "Tools/다시봄/대화·서버/실행 환경/";
    const string ServerMenu = RootMenu + "기존 GPU 서버 사용";
    const string LocalMenu = RootMenu + "로컬 API 사용";
    static Process launcher;
    static string runningAction;
    static string ProjectRoot => Path.GetFullPath(Path.Combine(Application.dataPath, ".."));

    [MenuItem(ServerMenu, priority = 0)]
    static void UseServer() => Select(false);
    [MenuItem(LocalMenu, priority = 1)]
    static void UseLocal() => Select(true);

    static void Select(bool local)
    {
        try
        {
            var path = DialogueConnectionProfile.EditorSettingsPath;
            var settings = new DialogueConnectionProfile.Settings();
            try { if (File.Exists(path)) settings = DialogueConnectionProfile.Read(path); }
            catch { if (local) throw; }
            settings.enabled = local;
            if (!DialogueConnectionProfile.Valid(settings))
                throw new InvalidOperationException("먼저 tools/local_demo.ps1 init과 use-local을 실행해 주세요.");
            Directory.CreateDirectory(Path.GetDirectoryName(path));
            File.WriteAllText(path, JsonUtility.ToJson(settings, true));
            Debug.Log("[Dialogue] " + (local ? "로컬 API" : "기존 GPU 서버") + " 선택 — 다음 Play부터 적용됩니다.");
        }
        catch (Exception)
        {
            Debug.LogError("로컬 실행 설정이 준비되지 않았습니다. tools/local_demo.ps1 init과 use-local을 먼저 실행하세요.");
        }
    }

    static bool IsLocal()
    {
        try { return File.Exists(DialogueConnectionProfile.EditorSettingsPath) &&
            DialogueConnectionProfile.Read(DialogueConnectionProfile.EditorSettingsPath).enabled; }
        catch { return false; }
    }

    [MenuItem(ServerMenu, true)]
    static bool ServerValid()
    {
        Menu.SetChecked(ServerMenu, !IsLocal());
        return !EditorApplication.isPlayingOrWillChangePlaymode;
    }
    [MenuItem(LocalMenu, true)]
    static bool LocalValid()
    {
        Menu.SetChecked(LocalMenu, IsLocal());
        return !EditorApplication.isPlayingOrWillChangePlaymode;
    }

    [MenuItem(RootMenu + "로컬 서비스 시작", priority = 20)]
    static void StartLocal() => Run("start");
    [MenuItem(RootMenu + "로컬 서비스 종료", priority = 21)]
    static void StopLocal() => Run("stop");
    [MenuItem(RootMenu + "로컬 상태 확인", priority = 22)]
    static void StatusLocal() => Run("status");
    [MenuItem(RootMenu + "로컬 등록 웹 시작 (GPU 사용 안 함)", priority = 25)]
    static void StartWeb() => Run("start-web");
    [MenuItem(RootMenu + "로컬 등록 웹 열기", priority = 26)]
    static void OpenWeb() => Run("open-web");
    [MenuItem(RootMenu + "사용법 열기", priority = 40)]
    static void OpenGuide() => EditorUtility.OpenWithDefaultApp(Path.Combine(ProjectRoot, "docs/로컬_API_시연.md"));

    static void Run(string action)
    {
        if (launcher != null || EditorApplication.isPlayingOrWillChangePlaymode)
        {
            Debug.LogWarning("Play와 이전 실행 명령이 끝난 뒤 다시 실행하세요.");
            return;
        }
        var python = Path.Combine(ProjectRoot, "tools/_work/local_demo/venv/Scripts/python.exe");
        if (!File.Exists(python))
        {
            Debug.LogError("먼저 tools/local_demo.ps1 setup을 실행해 주세요.");
            return;
        }
        try
        {
            var info = new ProcessStartInfo(python,
                "-X utf8 \"" + Path.Combine(ProjectRoot, "tools/local_demo.py") + "\" " + action)
            {
                WorkingDirectory = ProjectRoot, UseShellExecute = false, CreateNoWindow = true,
                RedirectStandardOutput = true, RedirectStandardError = true
            };
            // 긴 설치 이후에 Editor를 열었더라도 현재 사용자 환경의 키를 사용한다.
            // 키를 명령줄·씬·로그에 넣지 않는다.
            var key = Environment.GetEnvironmentVariable("OPENAI_API_KEY", EnvironmentVariableTarget.User);
            if (!string.IsNullOrEmpty(key)) info.EnvironmentVariables["OPENAI_API_KEY"] = key;
            runningAction = action;
            launcher = Process.Start(info);
            launcher.OutputDataReceived += (_, args) => { if (!string.IsNullOrEmpty(args.Data)) Debug.Log("[LocalDemo] " + args.Data); };
            launcher.ErrorDataReceived += (_, args) => { if (!string.IsNullOrEmpty(args.Data)) Debug.LogError("[LocalDemo] " + args.Data); };
            launcher.BeginOutputReadLine();
            launcher.BeginErrorReadLine();
            EditorApplication.update += Observe;
            Debug.Log("[LocalDemo] " + action + " 실행 중. 첫 음성 모델 로딩은 시간이 걸릴 수 있습니다.");
        }
        catch (Exception)
        {
            launcher?.Dispose();
            launcher = null;
            Debug.LogError("로컬 실행기를 시작하지 못했습니다. 사용법의 PowerShell 명령을 확인하세요.");
        }
    }

    static void Observe()
    {
        if (launcher == null || !launcher.HasExited) return;
        Debug.Log("[LocalDemo] " + runningAction + (launcher.ExitCode == 0 ? " 완료" : " 실패 — 위 오류를 확인하세요."));
        launcher.Dispose();
        launcher = null;
        EditorApplication.update -= Observe;
    }

    [MenuItem("Tools/다시봄/개발 도구/대화 검사/로컬 연결 설정 검사")]
    public static void ValidateProfile()
    {
        var valid = new DialogueConnectionProfile.Settings { enabled = true,
            serverUrl = "http://127.0.0.1:18000", dialogueServerUrl = "http://127.0.0.1:18002", token = new string('x', 64) };
        if (!DialogueConnectionProfile.Valid(valid)) throw new Exception("Local profile rejected");
        valid.serverUrl = "http://example.com:18000";
        if (DialogueConnectionProfile.Valid(valid)) throw new Exception("Remote profile accepted");
        valid.serverUrl = "http://127.0.0.1:18000/session?token=test";
        if (DialogueConnectionProfile.Valid(valid)) throw new Exception("Unexpected URL path accepted");
        valid.enabled = false;
        if (!DialogueConnectionProfile.Valid(valid)) throw new Exception("Server selection rejected");
        Debug.Log("[LocalDemo] 연결 설정 검사 통과 — loopback 제한·기존 서버 선택 확인.");
    }

    [MenuItem("Tools/다시봄/개발 도구/대화 검사/로컬 실행 상태 확인")]
    public static void InspectRuntime()
    {
        if (!EditorApplication.isPlaying)
        {
            Debug.LogWarning("[LocalDemo] Play 중에 실행하세요.");
            return;
        }
        var voice = UnityEngine.Object.FindObjectOfType<DialogueVoiceClient>();
        var persona = UnityEngine.Object.FindObjectOfType<PersonaSpawner>();
        Debug.Log("[LocalDemo] runtime " + JsonUtility.ToJson(new RuntimeReport {
            local = voice != null && voice.UsesLocalApi,
            serverReady = voice != null && voice.serverReady,
            hasSession = voice != null && voice.HasSession,
            sessionHasModel = voice != null && voice.SessionHasModel,
            modelReady = persona != null && persona.ModelReady
        }));
    }

    [Serializable]
    sealed class RuntimeReport
    {
        public bool local, serverReady, hasSession, sessionHasModel, modelReady;
    }
}
