using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

#if UNITY_6000_5_OR_NEWER
[UxmlElement]
#endif
public partial class StatusMessage : VisualElement
{
#if UNITY_6000_5_OR_NEWER

#else
    public new class UxmlFactory : UxmlFactory<StatusMessage> { }
#endif
    public StatusMessage()
    {
        Init(0);
    }

    private Image Image => this.Q<Image>("status-icon");

    public StatusMessage(int messageType)
    {
        Init(messageType);
    }

    public void UpdateStatus(bool goodStatus)
    {
        if (goodStatus)
            Image.image = EditorGUIUtility.IconContent("Installed").image;
        else
            Image.image = EditorGUIUtility.IconContent("Warning").image;
    }

    public void Init(
        int version
    )
    {
        VisualTreeAsset asset = AssetDatabase.LoadAssetAtPath<VisualTreeAsset>(
            "Packages/com.distantlands.cozy.core/Editor/UI/Components/UXML/status-message.uxml"
        );

        asset.CloneTree(this);
        Image.image = EditorGUIUtility.IconContent("Warning").image;

    }

}
