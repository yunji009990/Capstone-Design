using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

// 기존 Inspector의 잠금·선택을 바꾸지 않고 반사 Inspector를 별도 창에서도 표시한다.
public sealed class CafeGlassReflectionWindow : EditorWindow
{
    [SerializeField] CafePlanarReflection owner;
    Editor inspector;

    public static void Open(CafePlanarReflection target)
    {
        var window = GetWindow<CafeGlassReflectionWindow>("유리 반사 조절");
        window.minSize = new Vector2(420, 520);
        window.owner = target;
        window.CreateGUI();
        window.Show();
        window.Focus();
    }

    public void CreateGUI()
    {
        rootVisualElement.Clear();
        if (inspector != null) DestroyImmediate(inspector);
        if (owner == null)
        {
            rootVisualElement.Add(new HelpBox("Scene_2에서 ‘유리 반사 조절 열기’를 실행해 주세요.", HelpBoxMessageType.Info));
            return;
        }
        inspector = Editor.CreateEditor(owner, typeof(CafePlanarReflectionEditor));
        var scroll = new ScrollView();
        scroll.style.flexGrow = 1;
        scroll.style.paddingLeft = 8;
        scroll.style.paddingRight = 8;
        scroll.Add(inspector.CreateInspectorGUI());
        rootVisualElement.Add(scroll);
    }

    void OnDisable()
    {
        if (inspector != null) DestroyImmediate(inspector);
    }
}
