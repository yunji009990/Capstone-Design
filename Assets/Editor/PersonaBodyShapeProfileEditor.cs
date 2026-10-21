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

    [MenuItem("Tools/다시봄/캐릭터/체형 조절 열기")]
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
        root.Add(new HelpBox("−1은 가늘게, 0은 기본 체형, +1은 굵게 조절합니다. 키·관절 위치·손·신발은 유지됩니다.\n" +
            "Play에서 캐릭터를 표시한 뒤 조절하면 바로 보입니다. 기본 설정은 다음에 불러오는 캐릭터에도 적용됩니다.",
            HelpBoxMessageType.Info));
        for (int i = 0; i < Fields.Length; i++)
        {
            var property = serializedObject.FindProperty(Fields[i]);
            var slider = new Slider(Labels[i], -1f, 1f) { showInputField = true, tooltip = property.tooltip };
            slider.AddToClassList("unity-base-field__aligned");
            slider.bindingPath = Fields[i];
            root.Add(slider);
        }
        root.Add(new Button(() =>
        {
            serializedObject.Update();
            foreach (string field in Fields) serializedObject.FindProperty(field).floatValue = 0f;
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
                status.text = "연결된 캐릭터가 없습니다. Play에서 캐릭터를 불러온 뒤 체험을 시작하세요.";
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
