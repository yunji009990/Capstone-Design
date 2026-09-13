// SubtitleToggle.cs
// 인식된 사용자 발화를 화면과 기록에 표시할지 정한다. STT는 항상 수행한다.

using TMPro;
using UnityEngine;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class SubtitleToggle : MonoBehaviour
{
    [Tooltip("비워두면 씬에서 찾는다.")]
    public DialogueVoiceClient voice;

    public Toggle toggle;
    public TMP_Text hint;

    void Awake()
    {
        if (voice == null) voice = FindObjectOfType<DialogueVoiceClient>();
    }

    void Start()
    {
        if (voice == null || toggle == null) return;
        toggle.SetIsOnWithoutNotify(voice.showHeardSubtitle);
        toggle.onValueChanged.AddListener(Apply);
        Show(voice.showHeardSubtitle);
    }

    void Apply(bool on)
    {
        if (voice != null) voice.showHeardSubtitle = on;
        Show(on);
    }

    void Show(bool on)
    {
        if (hint == null) return;
        hint.text = on ? "체험자의 말을 자막과 기록에 표시합니다."
                       : "체험자의 자막을 숨깁니다. 음성 인식은 계속합니다.";
    }
}
