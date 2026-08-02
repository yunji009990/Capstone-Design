// SubtitleToggle.cs
// 체험자가 한 말을 받아쓸지 정한다.
//
// 켜면 서버가 답을 만들기 전에 먼저 받아쓰기를 끝내고 돌려준다. 그래야 대화 기록에
// 체험자의 말이 남는다. 대신 0.5초쯤 늦어진다.
//
// 끄면 받아쓰기를 뒤로 미뤄 그만큼 빨라지지만, 기록에는 인물의 말만 남는다.
// 시연 때는 끄고, 무슨 말이 오갔는지 확인해야 할 때는 켠다.

using TMPro;
using UnityEngine;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class SubtitleToggle : MonoBehaviour
{
    [Tooltip("비워두면 씬에서 찾는다.")]
    public RaonVoiceClient voice;

    public Toggle toggle;
    public TMP_Text hint;

    void Awake()
    {
        if (voice == null) voice = FindObjectOfType<RaonVoiceClient>();
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
        hint.text = on ? "기록에 체험자 말이 남습니다. 답이 0.5초쯤 늦어집니다."
                       : "0.5초 빠릅니다. 기록에는 인물의 말만 남습니다.";
    }
}
