using System;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.UIElements;

public sealed class PersonaBodyPreviewWindow : EditorWindow
{
    static readonly string[] Labels = { "상체 폭", "상체 두께", "허리 폭", "배 볼륨", "팔 굵기", "다리 굵기" };
    HelpBox status;
    VisualElement controls;
    SerializedObject settings;
    EnumField poseField;
    HelpBox dimensions;
    Button cafeButton, studioButton, playerViewButton;
    FloatField eyeHeight;
    public static void Open()
    {
        var window = GetWindow<PersonaBodyPreviewWindow>("얼굴·몸체 비율 조절");
        window.minSize = new Vector2(410, 600);
        window.Show();
    }
    void OnEnable() => PersonaBodyPreviewScene.Changed += UpdateStatus;
    void OnDisable() => PersonaBodyPreviewScene.Changed -= UpdateStatus;
    public void CreateGUI()
    {
        var root = rootVisualElement;
        root.Clear();
        var scroll = new ScrollView();
        root.Add(scroll);
        scroll.Add(new HelpBox("얼굴이 붙은 상태에서 몸체 Blend Shape를 조절합니다. Play 없이 바로 확인할 수 있습니다.\n" +
            "몸체 6개 키, 전체 크기, 머리 크기·높이·앞뒤를 조절합니다. 목 연결부는 옷깃에 맞춰 유지합니다.", HelpBoxMessageType.Info));
        cafeButton = new Button(() => _ = PersonaBodyPreviewScene.SetEnvironment(true)) { text = "카페에서 보기 · 실제 의자에 앉히기" };
        studioButton = new Button(() => _ = PersonaBodyPreviewScene.SetEnvironment(false)) { text = "단독 미리보기로 돌아가기" };
        playerViewButton = new Button(PersonaBodyPreviewCafe.PlayerView) { text = "체험자 자리에서 보기" };
        scroll.Add(cafeButton);
        scroll.Add(studioButton);
        scroll.Add(playerViewButton);
        eyeHeight = new FloatField("미리보기 눈높이 (m)") { value = PersonaBodyPreviewCafe.EyeHeight, isDelayed = true,
            tooltip = "카페 바닥 기준 눈높이. 기본 1.2m는 앉은 시점입니다. 실제 HMD의 추적 높이는 착용 자세에 따라 달라집니다." };
        eyeHeight.RegisterValueChangedCallback(evt =>
        {
            PersonaBodyPreviewCafe.EyeHeight = evt.newValue;
            eyeHeight.SetValueWithoutNotify(PersonaBodyPreviewCafe.EyeHeight);
        });
        scroll.Add(eyeHeight);
        dimensions = new HelpBox("", HelpBoxMessageType.Info);
        scroll.Add(dimensions);
        scroll.Add(new Button(() => _ = PersonaBodyPreviewScene.LoadRegisteredModel()) { text = "현재 등록 모델 다시 가져오기" });
        scroll.Add(new Button(() =>
        {
            string path = EditorUtility.OpenFilePanel("얼굴·몸체가 결합된 GLB", "", "glb");
            if (!string.IsNullOrEmpty(path)) _ = PersonaBodyPreviewScene.LoadFile(path);
        }) { text = "로컬 결합 GLB 열기" });
        status = new HelpBox(PersonaBodyPreviewScene.Status, HelpBoxMessageType.Info);
        scroll.Add(status);
        poseField = new EnumField("확인 자세", PersonaBodyPreviewScene.CurrentPose);
        poseField.RegisterValueChangedCallback(evt => PersonaBodyPreviewScene.SetPose((PersonaBodyPreviewScene.Pose)evt.newValue));
        scroll.Add(poseField);
        scroll.Add(new Button(() => PersonaBodyPreviewScene.Frame(0)) { text = "정면 · 전체 보기" });
        scroll.Add(new Button(() => PersonaBodyPreviewScene.Frame(90)) { text = "측면 보기" });
        scroll.Add(new Button(() => PersonaBodyPreviewScene.Frame(180)) { text = "뒤에서 보기" });
        scroll.Add(new Button(() => PersonaBodyPreviewScene.Frame(0, true)) { text = "얼굴·어깨 가까이 보기" });
        scroll.Add(new Button(() => PersonaBodyPreviewScene.Frame(90, true)) { text = "목 연결부 · 측면 가까이 보기" });
        controls = new VisualElement();
        scroll.Add(controls);
        var profile = PersonaBodyPreviewScene.Profile;
        if (profile != null)
        {
            settings = new SerializedObject(profile);
            for (int i = 0; i < Labels.Length; i++)
            {
                var slider = new Slider(Labels[i], -1, 1) { showInputField = true, bindingPath = PersonaBodyShapeProfileEditor.Fields[i] };
                slider.AddToClassList("unity-base-field__aligned");
                controls.Add(slider);
            }
            controls.Add(new Label("전체 크기 · 머리 결합") { style = { unityFontStyleAndWeight = FontStyle.Bold } });
            for (int i = 0; i < PersonaBodyShapeProfileEditor.ProportionFields.Length; i++)
            {
                var field = PersonaBodyShapeProfileEditor.ProportionFields[i];
                var slider = new Slider(PersonaBodyShapeProfileEditor.ProportionLabels[i], PersonaBodyShapeProfileEditor.ProportionMin[i], PersonaBodyShapeProfileEditor.ProportionMax[i])
                    { showInputField = true, bindingPath = field, tooltip = settings.FindProperty(field).tooltip };
                slider.AddToClassList("unity-base-field__aligned");
                controls.Add(slider);
            }
            controls.Bind(settings);
            controls.RegisterCallback<SerializedPropertyChangeEvent>(_ => PersonaBodyPreviewScene.ApplyShapes());
        }
        scroll.Add(new Button(() => { if (profile != null) Selection.activeObject = profile; }) { text = "체형 설정을 Inspector에서도 열기" });
        scroll.Add(new HelpBox("카페와 단독 미리보기는 같은 체형 값을 사용합니다. ‘기본 체형에 적용’을 누르면 실제 체험의 다음 인물 로드에도 적용됩니다.\n" +
            "슬라이더는 Ctrl+Z로 되돌릴 수 있습니다. 결합 모델은 씬 파일에 저장하지 않습니다.", HelpBoxMessageType.Info));
        scroll.Add(new Button(() =>
        {
            if (profile == null) return;
            settings.ApplyModifiedProperties();
            AssetDatabase.SaveAssetIfDirty(profile);
            PersonaBodyPreviewScene.SetStatus("미리보기 체형 설정을 저장했습니다.");
        }) { text = "미리보기 체형 저장" });
        scroll.Add(new Button(() => CopyValues(PersonaBodyShapeProfile.LoadDefault(), profile)) { text = "현재 기본 체형으로 되돌리기" });
        scroll.Add(new Button(() =>
        {
            settings?.ApplyModifiedProperties();
            CopyValues(profile, PersonaBodyShapeProfile.LoadDefault());
            PersonaBodyPreviewScene.SetStatus("현재 값을 기본 체형에 적용했습니다. 카페의 다음 모델 로드부터 사용합니다.");
        }) { text = "기본 체형에 적용" });
        UpdateStatus();
    }
    public static void CopyValues(PersonaBodyShapeProfile from, PersonaBodyShapeProfile to)
    {
        if (from == null || to == null || from == to) return;
        Undo.RecordObject(to, "체형 설정 복사");
        var source = new SerializedObject(from);
        var target = new SerializedObject(to);
        foreach (string field in PersonaBodyShapeProfileEditor.Fields)
            target.FindProperty(field).floatValue = source.FindProperty(field).floatValue;
        foreach (string field in PersonaBodyShapeProfileEditor.ProportionFields)
            target.FindProperty(field).floatValue = source.FindProperty(field).floatValue;
        target.ApplyModifiedProperties();
        EditorUtility.SetDirty(to);
        AssetDatabase.SaveAssetIfDirty(to);
        PersonaBodyPreviewScene.ApplyShapes();
    }
    void UpdateStatus()
    {
        if (status != null) status.text = PersonaBodyPreviewScene.Status;
        poseField?.SetValueWithoutNotify(PersonaBodyPreviewScene.CurrentPose);
        controls?.SetEnabled(PersonaBodyPreviewScene.IsOpen && !PersonaBodyPreviewScene.Busy);
        bool cafe = PersonaBodyPreviewCafe.IsActive;
        cafeButton?.SetEnabled(!cafe && !PersonaBodyPreviewScene.Busy);
        studioButton?.SetEnabled(cafe && !PersonaBodyPreviewScene.Busy);
        playerViewButton?.SetEnabled(cafe && PersonaBodyPreviewScene.Model != null && !PersonaBodyPreviewScene.Busy);
        eyeHeight?.SetEnabled(cafe && !PersonaBodyPreviewScene.Busy);
        eyeHeight?.SetValueWithoutNotify(PersonaBodyPreviewCafe.EyeHeight);
        if (dimensions != null)
        {
            dimensions.text = (cafe ? "현재 배경: 실제 카페\n" : "현재 배경: 단독 미리보기\n") + PersonaBodyPreviewCafe.ScaleDescription;
            if (PersonaBodyPreviewScene.Model != null)
                dimensions.text += $"\n{PersonaBodyPreviewScene.CurrentPose} 모델 높이: {PersonaBodyPreviewScene.Bounds().size.y * 100f:0.#}cm (Unity 1단위 = 1m)";
        }
        Repaint();
    }
}
