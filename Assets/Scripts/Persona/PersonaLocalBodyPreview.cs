using System;
using System.IO;
using System.Security.Cryptography;
using UnityEngine;

/// <summary>등록 모델의 해시와 일치하는 준비된 몸체를 사용한다. 빌드는 StreamingAssets의 시연 자료를 읽는다.</summary>
public static class PersonaLocalBodyPreview
{
    public const string SettingsRelativePath = "UserSettings/PersonaLocalBodyPreview.json";
    [Serializable]
    public sealed class Settings
    {
        public bool enabled;
        public string sourceSha256;
        public string modelRelativePath;
        public string modelSha256;
        public float heightMeters = 1.7f;
        public float seatedHeightOffset;
    }

    public static string SettingsPath
    {
        get
        {
#if UNITY_EDITOR
            return Path.GetFullPath(Path.Combine(Application.dataPath, "..", SettingsRelativePath));
#else
            return Path.Combine(Application.streamingAssetsPath, "LocalDemoBody.json");
#endif
        }
    }
    public static Settings ReadSettings() => File.Exists(SettingsPath)
        ? JsonUtility.FromJson<Settings>(File.ReadAllText(SettingsPath)) : null;

    public static string Digest(byte[] bytes)
    {
        using (var sha = SHA256.Create())
            return BitConverter.ToString(sha.ComputeHash(bytes)).Replace("-", "").ToLowerInvariant();
    }

    public static bool TryResolve(byte[] serverModel, out byte[] preview, out float heightMeters, out float seatedHeightOffset)
    {
        preview = null;
        heightMeters = 0f;
        seatedHeightOffset = 0f;
        try
        {
            var settings = ReadSettings();
            if (settings == null || !settings.enabled || serverModel == null ||
                string.IsNullOrEmpty(settings.sourceSha256) ||
                !string.Equals(Digest(serverModel), settings.sourceSha256, StringComparison.OrdinalIgnoreCase)) return false;
#if UNITY_EDITOR
            string root = Path.GetFullPath(Path.Combine(Application.dataPath, "..", "tools/_work")) + Path.DirectorySeparatorChar;
            string path = Path.GetFullPath(Path.Combine(Application.dataPath, "..", settings.modelRelativePath ?? ""));
#else
            string root = Path.GetFullPath(Application.streamingAssetsPath) + Path.DirectorySeparatorChar;
            string path = Path.GetFullPath(Path.Combine(Application.streamingAssetsPath, settings.modelRelativePath ?? ""));
#endif
            if (!path.StartsWith(root, StringComparison.OrdinalIgnoreCase) || !File.Exists(path))
                throw new InvalidDataException();
            var bytes = File.ReadAllBytes(path);
            if (bytes.Length < 20 || BitConverter.ToUInt32(bytes, 0) != 0x46546C67 ||
                BitConverter.ToUInt32(bytes, 4) != 2 || BitConverter.ToUInt32(bytes, 8) != bytes.Length ||
                !string.Equals(Digest(bytes), settings.modelSha256, StringComparison.OrdinalIgnoreCase))
                throw new InvalidDataException();
            preview = bytes;
            heightMeters = !float.IsNaN(settings.heightMeters) && !float.IsInfinity(settings.heightMeters)
                ? Mathf.Clamp(settings.heightMeters, .8f, 2.3f) : 1.7f;
            seatedHeightOffset = !float.IsNaN(settings.seatedHeightOffset) && !float.IsInfinity(settings.seatedHeightOffset)
                ? Mathf.Clamp(settings.seatedHeightOffset, -.5f, .5f) : 0f;
            return true;
        }
        catch (Exception)
        {
            // 경로·해시·세션 등 개인 자료 식별자는 Console에 남기지 않는다.
            Debug.LogWarning("[PersonaSpawner] 준비된 몸체를 확인하지 못해 등록 모델을 사용합니다.");
            return false;
        }
    }
}
