using System;
using System.IO;
using UnityEngine;

/// <summary>씬의 서버 설정을 저장하지 않고, 선택한 로컬 시연 연결을 Play에만 적용한다.</summary>
public static class DialogueConnectionProfile
{
    public const string RelativePath = "UserSettings/LocalDemoConnection.json";
    public const string Argument = "--local-demo-config";

    [Serializable]
    public sealed class Settings
    {
        public bool enabled;
        public string serverUrl;
        public string dialogueServerUrl;
        public string token;
    }

    public static string EditorSettingsPath => Path.GetFullPath(Path.Combine(Application.dataPath, "..", RelativePath));

    public static bool Valid(Settings settings)
    {
        return settings != null && (!settings.enabled ||
            (LocalUrl(settings.serverUrl) && LocalUrl(settings.dialogueServerUrl) &&
             !string.IsNullOrEmpty(settings.token) && settings.token.Length >= 32));
    }

    static bool LocalUrl(string value)
    {
        return Uri.TryCreate(value, UriKind.Absolute, out var uri) && uri.Scheme == "http" &&
            uri.Host == "127.0.0.1" && uri.Port >= 1024 && uri.Port <= 65535 &&
            uri.AbsolutePath == "/" && string.IsNullOrEmpty(uri.Query) &&
            string.IsNullOrEmpty(uri.Fragment) && string.IsNullOrEmpty(uri.UserInfo);
    }

    public static Settings Read(string path)
    {
        if (new FileInfo(path).Length > 8192) throw new InvalidDataException();
        var settings = JsonUtility.FromJson<Settings>(File.ReadAllText(path));
        if (!Valid(settings)) throw new InvalidDataException();
        return settings;
    }

    public static void Apply(DialogueVoiceClient target)
    {
        target.UsesLocalApi = false;
        try
        {
            string path = null;
            var args = Environment.GetCommandLineArgs();
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] != Argument) continue;
                if (i + 1 >= args.Length || !File.Exists(args[i + 1])) throw new InvalidDataException();
                path = args[i + 1];
                break;
            }
#if UNITY_EDITOR
            if (path == null) path = EditorSettingsPath;
#else
            if (path == null) path = Path.Combine(Application.persistentDataPath, "LocalDemoConnection.json");
#endif
            if (!File.Exists(path))
            {
#if !UNITY_EDITOR
                if (File.Exists(Path.Combine(Application.streamingAssetsPath, "LocalDemoRequired.txt")))
                    throw new InvalidDataException();
#endif
                return;
            }
            var settings = Read(path);
            if (!settings.enabled) return;
            target.UsesLocalApi = true;
            target.serverUrl = settings.serverUrl.TrimEnd('/');
            target.dialogueServerUrl = settings.dialogueServerUrl.TrimEnd('/');
            target.token = settings.token;
        }
        catch (Exception)
        {
            target.UsesLocalApi = true;
            // 잘못된 로컬 설정으로 기존 GPU 서버에 접속하지 않도록 실패를 명확히 한다.
            target.serverUrl = target.dialogueServerUrl = "http://127.0.0.1:1";
            target.token = "";
            Debug.LogError("[Dialogue] 로컬 API 연결 설정을 읽지 못했습니다. 로컬 시연 시작 프로그램으로 실행하세요.");
        }
    }
}
