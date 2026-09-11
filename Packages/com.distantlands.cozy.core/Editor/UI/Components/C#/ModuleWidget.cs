using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

#if UNITY_6000_5_OR_NEWER
[UxmlElement]
#endif
public partial class ModuleWidget : VisualElement
{
#if UNITY_6000_5_OR_NEWER

#else
    public new class UxmlFactory : UxmlFactory<ModuleWidget> { }
#endif
    public ModuleWidget()
    {
        Init(GUIContent.none, "");
    }

    private Image Image => this.Q<Image>("icon");
    private Label Label => this.Q<Label>("name");
    private Label DynamicStatus => this.Q<Label>("dynamic-status");

    public ModuleWidget(GUIContent content)
    {
        Init(content, "Yep. It works :)");
    }
    public ModuleWidget(GUIContent content, string status)
    {
        Init(content, status);
    }

    public void Init(
        GUIContent module, 
        string dynamicStatus
    )
    {

        

    }

}
