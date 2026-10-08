using System.Collections.Generic;
using UnityEditor;
using UnityEditor.UIElements;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UIElements;

// 유리 재질을 원본으로 사용한다. Inspector를 열 때 씬이나 현재 반사 값을 다시 설정하지 않는다.
[CustomEditor(typeof(CafePlanarReflection))]
public sealed class CafePlanarReflectionEditor : Editor
{
    const string MenuPath = "Tools/다시봄/그래픽/유리 반사 조절 열기";

    sealed class Control
    {
        public Material material;
        public int property;
        public Slider slider;
    }

    [MenuItem(MenuPath)]
    static void OpenControls()
    {
        var scene = SceneManager.GetActiveScene();
        foreach (var owner in Object.FindObjectsOfType<CafePlanarReflection>(true))
        {
            if (owner.gameObject.scene != scene) continue;
            Selection.activeGameObject = owner.gameObject;
            EditorGUIUtility.PingObject(owner.gameObject);
            // 원래 Inspector가 다른 오브젝트에 잠겨 있어도 같은 조절 화면을 열 수 있다.
            CafeGlassReflectionWindow.Open(owner);
            return;
        }
        Debug.LogWarning("[CafeGlass] 현재 씬에 유리 반사 컴포넌트가 없습니다. Scene_2를 열어 주세요.");
    }

    public override VisualElement CreateInspectorGUI()
    {
        serializedObject.Update();
        var root = new VisualElement();
        var script = new PropertyField(serializedObject.FindProperty("m_Script"), "스크립트");
        script.SetEnabled(false);
        root.Add(script);
        root.Add(new HelpBox("창별 반사 강도를 조절합니다. 변경은 바로 보이며, ‘반사 설정 저장’으로 재질에 저장합니다.",
            HelpBoxMessageType.Info));

        var materials = FindMaterials();
        var controls = new List<Control>();
        foreach (var material in materials)
        {
            var group = new VisualElement();
            group.style.marginTop = 10;
            group.style.marginBottom = 4;
            var heading = new Label(material.name == "WindowGlass" ? "정면 큰 창" :
                material.name == "OvalWindowGlass" ? "왼쪽 타원 창" : material.name);
            heading.style.unityFontStyleAndWeight = FontStyle.Bold;
            group.Add(heading);
            AddSlider(group, controls, material, "_ReflectionOpacity", "정면 반사", "정면에서 보는 반사 강도입니다. 낮추면 바깥 풍경이 더 잘 보입니다.");
            AddSlider(group, controls, material, "_GrazingOpacity", "비스듬한 반사", "옆으로 비껴 볼 때의 반사 강도입니다.");
            AddSlider(group, controls, material, "_ReflectionGain", "반사 밝기", "반사된 내부 풍경의 밝기입니다.");
            AddSlider(group, controls, material, "_HighlightCompression", "밝은 반사 완화", "높이면 간판·조명처럼 밝은 반사를 더 누릅니다.");
            root.Add(group);
        }
        if (materials.Count == 0)
            root.Add(new HelpBox("연결된 평면 반사 유리 재질이 없습니다. 고급 설정의 반사 표면 연결을 확인해 주세요.", HelpBoxMessageType.Warning));

        var save = new Button(() =>
        {
            foreach (var material in materials)
                if (material != null) AssetDatabase.SaveAssetIfDirty(material);
        }) { text = "반사 설정 저장", name = "save-glass-settings" };
        save.SetEnabled(materials.Count > 0);
        root.Add(save);

        var advanced = new Foldout { text = "고급 설정", value = false };
        advanced.style.marginTop = 10;
        advanced.Add(new PropertyField(serializedObject.FindProperty("surfaces"), "반사 표면 연결"));
        advanced.Add(new PropertyField(serializedObject.FindProperty("frameInterval"), "반사 갱신 간격"));
        advanced.Add(new PropertyField(serializedObject.FindProperty("clipOffset"), "반사 경계 보정"));
        root.Add(advanced);
        root.Bind(serializedObject);

        // Undo/Redo 및 다른 Material Inspector의 변경도 표시한다. 패널이 닫히면 예약은 자동으로 멈춘다.
        root.schedule.Execute(() =>
        {
            foreach (var control in controls)
            {
                if (control.material == null) continue;
                float value = control.material.GetFloat(control.property);
                if (!Mathf.Approximately(control.slider.value, value))
                    control.slider.SetValueWithoutNotify(value);
            }
        }).Every(200);
        return root;
    }

    List<Material> FindMaterials()
    {
        var result = new List<Material>();
        var surfaces = serializedObject.FindProperty("surfaces");
        for (int i = 0; i < surfaces.arraySize; i++)
        {
            var renderers = surfaces.GetArrayElementAtIndex(i).FindPropertyRelative("renderers");
            for (int j = 0; j < renderers.arraySize; j++)
            {
                var renderer = renderers.GetArrayElementAtIndex(j).objectReferenceValue as Renderer;
                if (renderer == null) continue;
                foreach (var material in renderer.sharedMaterials)
                    if (material != null && material.HasProperty("_ReflectionOpacity") && !result.Contains(material))
                        result.Add(material);
            }
        }
        return result;
    }

    static void AddSlider(VisualElement parent, List<Control> controls, Material material, string property, string label, string tooltip)
    {
        if (!material.HasProperty(property)) return;
        int propertyId = Shader.PropertyToID(property);
        var slider = new Slider(label, 0f, 1f)
        {
            name = material.name + property,
            showInputField = true,
            tooltip = tooltip
        };
        slider.SetValueWithoutNotify(material.GetFloat(propertyId));
        slider.RegisterValueChangedCallback(change =>
        {
            if (material == null) return;
            float value = Mathf.Clamp01(change.newValue);
            if (Mathf.Approximately(material.GetFloat(propertyId), value)) return;
            Undo.RecordObject(material, "유리 " + label + " 조절");
            material.SetFloat(propertyId, value);
            EditorUtility.SetDirty(material);
            EditorApplication.QueuePlayerLoopUpdate();
            SceneView.RepaintAll();
        });
        controls.Add(new Control { material = material, property = propertyId, slider = slider });
        parent.Add(slider);
    }
}
