using Oculus.Interaction;
using UnityEngine;

/// <summary>Human-readable scene objects, with Meta grab/release events.</summary>
[DisallowMultipleComponent]
public class DialogueContextObject : MonoBehaviour
{
    [Tooltip("LLM에 전달할 대상 이름. 예: 가족 사진, 찻잔")]
    public string displayName;
    public DialogueVoiceClient voice;
    public string DisplayName => string.IsNullOrWhiteSpace(displayName) ? gameObject.name : displayName;
    Grabbable _grabbable;

    void Awake()
    {
        if (voice == null) voice = FindObjectOfType<DialogueVoiceClient>();
        _grabbable = GetComponent<Grabbable>();
    }

    void OnEnable()
    {
        if (_grabbable != null) _grabbable.WhenPointerEventRaised += OnPointer;
    }

    void OnDisable()
    {
        if (_grabbable != null) _grabbable.WhenPointerEventRaised -= OnPointer;
    }

    void OnPointer(PointerEvent evt)
    {
        if (evt.Type == PointerEventType.Select) ReportGrab();
        else if (evt.Type == PointerEventType.Unselect) ReportRelease();
    }

    public void ReportGrab() => voice?.ReportUnityAction("사용자가 물건을 집어 들었다. 대상: " + DisplayName);
    public void ReportRelease() => voice?.ReportUnityAction("사용자가 물건을 놓았다. 대상: " + DisplayName);
}
