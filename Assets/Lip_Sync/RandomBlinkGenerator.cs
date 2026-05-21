using UnityEngine;
using UnityEditor;
using System.IO;

public class RandomBlinkGenerator : EditorWindow
{
    [MenuItem("Tools/Random Blink Generator")]
    public static void ShowWindow() => GetWindow<RandomBlinkGenerator>("Blink Generator");

    private string blendShapeName = "blink"; // 모델에 맞춰 수정 가능 (예: Blink, EyeClose 등)
    private float clipDuration = 60f;        // 애니메이션 총 길이 (초)
    private float minInterval = 2.0f;        // 최소 깜빡임 간격
    private float maxInterval = 6.0f;        // 최대 깜빡임 간격
    private float blinkSpeed = 0.15f;        // 한 번 깜빡이는 데 걸리는 시간
    [Range(0, 100)]
    private float doubleBlinkChance = 20f;   // 두 번 연속 깜빡일 확률 (%)

    void OnGUI()
    {
        GUILayout.Label("Random Blink Animation Generator", EditorStyles.boldLabel);
        EditorGUILayout.Space();

        GUILayout.Label("BlendShape Settings", EditorStyles.boldLabel);
        blendShapeName = EditorGUILayout.TextField("BlendShape Name", blendShapeName);
        EditorGUILayout.HelpBox("SkinnedMeshRenderer에 있는 눈 깜빡임 블랜드쉐이프의 정확한 이름을 적어주세요. (대소문자 구분)", MessageType.Info);

        EditorGUILayout.Space();
        GUILayout.Label("Timing Settings", EditorStyles.boldLabel);
        clipDuration = EditorGUILayout.FloatField("Clip Duration (sec)", clipDuration);

        EditorGUILayout.BeginHorizontal();
        minInterval = EditorGUILayout.FloatField("Min Interval", minInterval);
        maxInterval = EditorGUILayout.FloatField("Max Interval", maxInterval);
        EditorGUILayout.EndHorizontal();

        blinkSpeed = EditorGUILayout.Slider("Blink Speed", blinkSpeed, 0.05f, 0.3f);
        doubleBlinkChance = EditorGUILayout.Slider("Double Blink Chance (%)", doubleBlinkChance, 0f, 100f);

        EditorGUILayout.Space();
        if (GUILayout.Button("Generate Blink Animation", GUILayout.Height(40)))
        {
            GenerateBlinkAnimation();
        }
    }

    void GenerateBlinkAnimation()
    {
        AnimationClip clip = new AnimationClip();
        AnimationCurve curve = new AnimationCurve();

        float currentTime = 0f;

        // 1. 시작 프레임: 눈 뜬 상태 (0)
        AddKey(curve, 0f, 0f);

        // 2. 랜덤 간격으로 깜빡임 생성
        while (currentTime < clipDuration - maxInterval)
        {
            // 다음 깜빡임까지 대기하는 시간
            float interval = Random.Range(minInterval, maxInterval);
            currentTime += interval;

            // 한 번 깜빡임
            CreateBlink(curve, currentTime);

            // 확률에 따라 연속 두 번 깜빡임 (Double Blink)
            if (Random.Range(0f, 100f) < doubleBlinkChance)
            {
                currentTime += blinkSpeed * 1.5f; // 살짝 텀을 두고
                CreateBlink(curve, currentTime);
            }
        }

        // 3. 종료 프레임: 눈 뜬 상태 (0) 유지하여 자연스러운 루프 만들기
        AddKey(curve, clipDuration, 0f);

        // 생성된 커브를 블랜드쉐이프에 적용
        clip.SetCurve("", typeof(SkinnedMeshRenderer), "blendShape." + blendShapeName, curve);

        // 무한 루프(Loop Time) 설정 켜기
        AnimationClipSettings settings = AnimationUtility.GetAnimationClipSettings(clip);
        settings.loopTime = true;
        AnimationUtility.SetAnimationClipSettings(clip, settings);

        // 저장 경로 자동 설정 (이 스크립트 옆 폴더)
        MonoScript script = MonoScript.FromScriptableObject(this);
        string folderPath = Path.GetDirectoryName(AssetDatabase.GetAssetPath(script)) + "/GeneratedAnimations_Final";
        if (!AssetDatabase.IsValidFolder(folderPath))
            AssetDatabase.CreateFolder(Path.GetDirectoryName(AssetDatabase.GetAssetPath(script)), "GeneratedAnimations_Final");

        string savePath = folderPath + "/RandomBlink_" + clipDuration + "s.anim";
        AssetDatabase.CreateAsset(clip, savePath);
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();

        EditorUtility.DisplayDialog("성공", "눈 깜빡임 애니메이션이 성공적으로 생성되었습니다!\nAnimator에 넣고 사용하세요.", "확인");
    }

    void CreateBlink(AnimationCurve curve, float time)
    {
        float halfSpeed = blinkSpeed / 2f;

        // 눈 감기 시작 (0) -> 눈 완전히 감음 (100) -> 눈 다시 뜸 (0)
        AddKey(curve, time - halfSpeed, 0f);
        AddKey(curve, time, 100f);
        AddKey(curve, time + halfSpeed, 0f);
    }

    void AddKey(AnimationCurve curve, float time, float value)
    {
        int index = curve.AddKey(new Keyframe(time, value));
        if (index != -1)
        {
            // 오버슛(음수값 등) 방지를 위해 Linear 사용
            // 눈 깜빡임은 빠르게 팍 감았다가 뜨는 것이 자연스러우므로 직선 보간이 좋습니다.
            AnimationUtility.SetKeyLeftTangentMode(curve, index, AnimationUtility.TangentMode.Linear);
            AnimationUtility.SetKeyRightTangentMode(curve, index, AnimationUtility.TangentMode.Linear);
        }
    }
}