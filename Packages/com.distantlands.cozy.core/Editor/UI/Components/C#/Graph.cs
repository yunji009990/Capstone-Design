using DistantLands.Cozy;
using UnityEditor;
using UnityEngine;
using UnityEngine.UIElements;

#if UNITY_6000_5_OR_NEWER
[UxmlElement]
#endif
public partial class Graph : VisualElement
{
#if UNITY_6000_5_OR_NEWER

#else
    public new class UxmlFactory : UxmlFactory<Graph> { }
#endif
    public Graph()
    {
        Init();
    }

    public void Init()
    {
        style.display = CozyWeather.Graphs ? DisplayStyle.Flex : DisplayStyle.None;
    }

}