using System.IO;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

[CustomEditor(typeof(PersonaBodyShapeProfile)), CanEditMultipleObjects]
public sealed class PersonaBodyShapeProfileEditor : Editor
{
    public const string DefaultAssetPath = "Assets/Resources/PersonaBodyShape.asset";
    internal static readonly string[] Fields =
    {
        "upperBodyWidth", "upperBodyDepth", "waistWidth", "bellyVolume", "armThickness", "legThickness",
    };
    static readonly string[] Labels = { "상체 폭", "상체 두께", "허리 폭", "배 볼륨", "팔 굵기", "다리 굵기" };
    internal static readonly string[] ProportionFields = { "bodyScale", "headScale", "headHeight", "headForward" };
    internal static readonly string[] ProportionLabels = { "전체 크기 (배율)", "머리 크기 (배율)", "머리 높이 (키 비율)", "머리 앞뒤 (키 비율)" };
    internal static readonly float[] ProportionMin = { .7f, .75f, -.03f, -.04f };
    internal static readonly float[] ProportionMax = { 1.3f, 1.35f, .05f, .04f };
    internal static readonly float[] ProportionDefaults = { 1f, 1f, 0f, 0f };

    [MenuItem("Tools/다시봄/캐릭터/체형 조절 열기", priority = 1)]
    public static void OpenControls()
    {
        var profile = AssetDatabase.LoadAssetAtPath<PersonaBodyShapeProfile>(DefaultAssetPath);
        if (profile == null)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(DefaultAssetPath));
            AssetDatabase.Refresh();
            profile = CreateInstance<PersonaBodyShapeProfile>();
            AssetDatabase.CreateAsset(profile, DefaultAssetPath);
            AssetDatabase.SaveAssetIfDirty(profile);
        }
        Selection.activeObject = profile;
        EditorGUIUtility.PingObject(profile);
    }

    public override VisualElement CreateInspectorGUI()
    {
        var root = new VisualElement();
        root.Add(new HelpBox("체형은 −1~+1, 전체·머리 크기는 1이 기준입니다. 머리 높이·앞뒤는 키 대비 비율이며 0.01은 1%입니다.\n" +
            "전용 체형 미리보기 씬 또는 Play에서 캐릭터를 표시하면 바로 조절됩니다. 기본 설정 자산은 다음에 불러오는 캐릭터에도 적용됩니다.",
            HelpBoxMessageType.Info));
        for (int i = 0; i < Fields.Length; i++)
        {
            var property = serializedObject.FindProperty(Fields[i]);
            var slider = new Slider(Labels[i], -1f, 1f) { showInputField = true, tooltip = property.tooltip };
            slider.AddToClassList("unity-base-field__aligned");
            slider.bindingPath = Fields[i];
            root.Add(slider);
        }
        root.Add(new Label("전체 크기 · 머리 결합") { style = { unityFontStyleAndWeight = FontStyle.Bold } });
        for (int i = 0; i < ProportionFields.Length; i++)
        {
            var property = serializedObject.FindProperty(ProportionFields[i]);
            var slider = new Slider(ProportionLabels[i], ProportionMin[i], ProportionMax[i])
                { showInputField = true, tooltip = property.tooltip, bindingPath = ProportionFields[i] };
            slider.AddToClassList("unity-base-field__aligned");
            root.Add(slider);
        }
        root.Add(new Button(() =>
        {
            serializedObject.Update();
            foreach (string field in Fields) serializedObject.FindProperty(field).floatValue = 0f;
            for (int i = 0; i < ProportionFields.Length; i++)
                serializedObject.FindProperty(ProportionFields[i]).floatValue = ProportionDefaults[i];
            serializedObject.ApplyModifiedProperties();
            ApplyToLoadedModels();
        }) { text = "기본 체형으로 되돌리기" });
        root.Add(new Button(() =>
        {
            serializedObject.ApplyModifiedProperties();
            foreach (var item in targets) AssetDatabase.SaveAssetIfDirty(item);
        }) { text = "체형 설정 저장" });
        root.Add(new HelpBox("설정 자산을 수정하므로 Play를 종료해도 값이 유지됩니다. ‘체형 설정 저장’으로 파일에 저장하세요. " +
            "Undo/Redo를 사용할 수 있습니다.", HelpBoxMessageType.None));
        var status = new HelpBox("캐릭터 연결을 확인하는 중입니다.", HelpBoxMessageType.Info);
        root.Add(status);
        root.schedule.Execute(() =>
        {
            var controllers = Object.FindObjectsOfType<PersonaBodyShapeController>(true);
            bool found = false;
            foreach (var controller in controllers)
            {
                if (controller.Profile != target) continue;
                found = true;
                status.text = controller.BoundShapeCount == PersonaBodyShapeProfile.ShapeCount
                    ? "현재 캐릭터에 체형 키 6개가 연결됐습니다."
                    : $"현재 모델의 체형 키 연결: {controller.BoundShapeCount}/6. 체형 키가 포함된 모델이 필요합니다.";
                status.messageType = controller.BoundShapeCount == PersonaBodyShapeProfile.ShapeCount
                    ? HelpBoxMessageType.Info : HelpBoxMessageType.Warning;
                break;
            }
            if (!found)
            {
                status.text = "연결된 캐릭터가 없습니다. 체형 미리보기 씬에서 모델을 열거나 Play에서 캐릭터를 표시하세요.";
                status.messageType = HelpBoxMessageType.Info;
            }
        }).Every(750);
        root.RegisterCallback<SerializedPropertyChangeEvent>(_ => ApplyToLoadedModels());
        return root;
    }

    static void ApplyToLoadedModels()
    {
        // 사용자 편집 시에만 검색한다. 런타임 갱신은 캐시한 렌더러와 변경 값만 확인한다.
        foreach (var controller in Object.FindObjectsOfType<PersonaBodyShapeController>(true))
            if (controller.isActiveAndEnabled) controller.ApplyNow();
        SceneView.RepaintAll();
    }
}
