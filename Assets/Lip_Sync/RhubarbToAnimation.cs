using UnityEngine;
using UnityEditor;
using System.IO;
using System.Diagnostics;
using System.Collections.Generic;

public class RhubarbBatchProcessor : EditorWindow
{
    [MenuItem("Tools/Rhubarb LipSync/Batch Processor")]
    public static void ShowWindow() => GetWindow<RhubarbBatchProcessor>("Rhubarb Batch");

    private string rhubarbExePath = "";
    private List<AudioClip> audioClips = new List<AudioClip>();
    private float intensity = 0.5f;
    private Vector2 scrollPos;

    // --- 신규 설정: 전환 시간 (0.05 ~ 0.1초 추천) ---
    // 이 시간 동안 입 모양이 부드럽게 변합니다. 너무 길면 흐물거리고 짧으면 딱딱해집니다.
    private float transitionTime = 0.2f;

    void OnEnable()
    {
        rhubarbExePath = EditorPrefs.GetString("RhubarbExePath", "");
    }

    void OnGUI()
    {
        GUILayout.Label("Rhubarb Batch LipSync Processor (Smooth Mode)", EditorStyles.boldLabel);

        EditorGUILayout.BeginHorizontal();
        rhubarbExePath = EditorGUILayout.TextField("Rhubarb.exe Path", rhubarbExePath);
        if (GUILayout.Button("Browse", GUILayout.Width(60)))
        {
            rhubarbExePath = EditorUtility.OpenFilePanel("Select rhubarb.exe", "", "exe");
            if (!string.IsNullOrEmpty(rhubarbExePath))
                EditorPrefs.SetString("RhubarbExePath", rhubarbExePath);
        }
        EditorGUILayout.EndHorizontal();

        intensity = EditorGUILayout.Slider("Intensity (0~1)", intensity, 0.1f, 1.0f);

        // 전환 시간 슬라이더 추가
        transitionTime = EditorGUILayout.Slider("Smoothness (Transition)", transitionTime, 0.01f, 0.2f);

        EditorGUILayout.Space();

        GUILayout.Label("Target Audio Clips (WAV only)", EditorStyles.boldLabel);
        EditorGUILayout.BeginHorizontal();
        if (GUILayout.Button("Add Selected Clips"))
        {
            foreach (var obj in Selection.objects)
            {
                if (obj is AudioClip clip && !audioClips.Contains(clip))
                {
                    string path = AssetDatabase.GetAssetPath(clip).ToLower();
                    if (path.EndsWith(".wav") || path.EndsWith(".ogg"))
                        audioClips.Add(clip);
                }
            }
        }
        if (GUILayout.Button("Clear All", GUILayout.Width(80))) audioClips.Clear();
        EditorGUILayout.EndHorizontal();

        scrollPos = EditorGUILayout.BeginScrollView(scrollPos, GUILayout.Height(200));
        for (int i = 0; i < audioClips.Count; i++)
        {
            EditorGUILayout.BeginHorizontal();
            audioClips[i] = (AudioClip)EditorGUILayout.ObjectField(audioClips[i], typeof(AudioClip), false);
            if (GUILayout.Button("X", GUILayout.Width(25)))
            {
                audioClips.RemoveAt(i);
                break;
            }
            EditorGUILayout.EndHorizontal();
        }
        EditorGUILayout.EndScrollView();

        EditorGUILayout.Space();

        GUI.enabled = audioClips.Count > 0 && File.Exists(rhubarbExePath);
        if (GUILayout.Button($"Process {audioClips.Count} Clips (Smooth)", GUILayout.Height(40)))
        {
            ProcessBatch();
        }
        GUI.enabled = true;
    }

    void ProcessBatch()
    {
        int successCount = 0;
        try
        {
            for (int i = 0; i < audioClips.Count; i++)
            {
                AudioClip clip = audioClips[i];
                if (clip == null) continue;

                float progress = (float)i / audioClips.Count;
                if (EditorUtility.DisplayCancelableProgressBar("Rhubarb Batch", $"Processing {clip.name}...", progress))
                {
                    UnityEngine.Debug.LogWarning("Batch processing cancelled by user.");
                    break;
                }

                if (RunSingleRhubarb(clip)) successCount++;
            }
        }
        finally
        {
            EditorUtility.ClearProgressBar();
            AssetDatabase.Refresh();
            EditorUtility.DisplayDialog("완료", $"{successCount}개의 애니메이션 클립 생성 완료", "확인");
        }
    }

    bool RunSingleRhubarb(AudioClip clip)
    {
        string audioPath = Path.GetFullPath(AssetDatabase.GetAssetPath(clip));
        string jsonPath = Path.Combine(Application.temporaryCachePath, clip.name + "_temp.json");

        ProcessStartInfo startInfo = new ProcessStartInfo
        {
            FileName = rhubarbExePath,
            Arguments = $"-f json -o \"{jsonPath}\" \"{audioPath}\"",
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true
        };

        using (Process process = new Process())
        {
            process.StartInfo = startInfo;
            process.Start();

            string output = process.StandardOutput.ReadToEnd();
            string error = process.StandardError.ReadToEnd();

            process.WaitForExit();

            if (process.ExitCode == 0 && File.Exists(jsonPath))
            {
                BakeFromPath(jsonPath, clip.name);
                return true;
            }
            else
            {
                UnityEngine.Debug.LogError($"{clip.name} 분석 실패: {error}");
                return false;
            }
        }
    }

    void BakeFromPath(string path, string clipName)
    {
        string json = File.ReadAllText(path);
        var data = JsonUtility.FromJson<RhubarbOutput>(json);
        if (data == null || data.mouthCues == null || data.mouthCues.Count == 0) return;

        MonoScript script = MonoScript.FromScriptableObject(this);
        string scriptPath = AssetDatabase.GetAssetPath(script);
        string scriptDirectory = Path.GetDirectoryName(scriptPath);
        string folderName = "GeneratedAnimations_Smooth"; // 폴더 이름 변경
        string folderPath = scriptDirectory + "/" + folderName;

        if (!AssetDatabase.IsValidFolder(folderPath))
            AssetDatabase.CreateFolder(scriptDirectory, folderName);

        string savePath = folderPath + "/" + clipName + "_SmoothLipSync.anim";

        AnimationClip clip = new AnimationClip();
        var curves = new AnimationCurve[6] {
            new AnimationCurve(), new AnimationCurve(), new AnimationCurve(),
            new AnimationCurve(), new AnimationCurve(), new AnimationCurve()
        };

        foreach (var mouthCue in data.mouthCues)
        {
            // --- 핵심 수정: 부드러운 전환 로직 ---

            // 1. 해당 모음의 '순수 유지 구간'을 계산합니다.
            // 전체 데이터 구간(start~end)에서 앞뒤로 transitionTime의 절반씩을 전환용으로 뺍니다.
            float duration = mouthCue.end - mouthCue.start;
            float actualTransition = Mathf.Min(transitionTime, duration * 0.5f); // 구간이 너무 짧으면 transition도 줄임

            float holdStart = mouthCue.start + actualTransition * 0.5f;
            float holdEnd = mouthCue.end - actualTransition * 0.5f;

            // 2. 유지 구간의 시작점에 키를 박습니다. (전환이 끝나고 목표값에 도달하는 지점)
            ApplyMapping(mouthCue.value, holdStart, curves);

            // 3. 유지 구간의 끝점에 키를 박습니다. (목표값을 유지하다가 다음 전환이 시작되는 지점)
            // 이전 에러 방지를 위해 미세한 간격(-0.001f)은 유지합니다.
            if (holdEnd > holdStart)
            {
                ApplyMapping(mouthCue.value, holdEnd - 0.001f, curves);
            }
        }

        float endTime = data.mouthCues[data.mouthCues.Count - 1].end;
        ApplyMapping("X", endTime, curves);

        string[] shapeNames = { "A", "E", "I", "O", "U", "Tongue_out" };
        for (int j = 0; j < 6; j++)
        {
            clip.SetCurve("", typeof(SkinnedMeshRenderer), "blendShape." + shapeNames[j], curves[j]);
        }

        AssetDatabase.CreateAsset(clip, savePath);
    }

    void ApplyMapping(string shape, float time, AnimationCurve[] curves)
    {
        float a = 0, e = 0, i = 0, o = 0, u = 0, tOut = 0;

        switch (shape)
        {
            case "B": i = 60f; e = 20f; break;
            case "C": a = 50f; e = 40f; break;
            case "D": a = 100f; break;
            case "E": e = 100f; break;

            // --- O 가중치 수정 구간 ---
            // 기존: o = 80f; u = 60f; 
            // 수정: 입술을 덜 오므리도록 수치를 낮춥니다.
            case "F": o = 40f; u = 30f; break;

            case "G": e = 30f; i = 20f; break;
            case "H": a = 30f; tOut = 50f; break;
            case "X": break;
        }

        for (int k = 0; k < 6; k++)
        {
            float val = (k == 0 ? a : k == 1 ? e : k == 2 ? i : k == 3 ? o : k == 4 ? u : tOut) * intensity;
            AddSmoothKey(curves[k], time, val);
        }
    }

    // --- 함수 이름 및 로직 변경: Linear -> Smooth (ClampedAuto) ---
    void AddSmoothKey(AnimationCurve curve, float time, float value)
    {
        int index = curve.AddKey(new Keyframe(time, value));

        if (index != -1)
        {
            // ClampedAuto는 유니티가 부드러운 Cubic 곡선을 그리되,
            // 인접한 키와 값이 같으면 평평하게(Flat) 만들어 오버슛(음수값 등)을 방지하는 모드입니다.
            AnimationUtility.SetKeyLeftTangentMode(curve, index, AnimationUtility.TangentMode.ClampedAuto);
            AnimationUtility.SetKeyRightTangentMode(curve, index, AnimationUtility.TangentMode.ClampedAuto);
        }
    }

    [System.Serializable] public class RhubarbOutput { public List<MouthCue> mouthCues; }
    [System.Serializable] public class MouthCue { public float start; public float end; public string value; }
}