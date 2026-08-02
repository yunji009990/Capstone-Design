// ExperienceControl.cs
// 체험을 시작한다.
//
// 이 프로젝트에는 "게임 시작"에 해당하는 코드가 따로 없었다. MainScene 의
// ButtonController.StartContent 는 치료 콘텐츠 애니메이터를 트리거하는 것이라 여기와
// 상관이 없다. 여기서 체험이 시작된다는 것은 곧 대화를 처음부터 시작한다는 뜻이다.
//
// 말을 걸 때 단추를 누를 필요는 없다 — 자동 감지가 켜져 있어 그냥 말하면 된다.
// 그래서 "말하기" 대신 이 단추 하나만 둔다.

using TMPro;
using UnityEngine;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class ExperienceControl : MonoBehaviour
{
    [Tooltip("비워두면 씬에서 찾는다.")]
    public RaonVoiceClient voice;
    public ConversationLog log;

    [Tooltip("눌리는 단추와 그 글자.")]
    public Button startButton;
    public TMP_Text startLabel;

    /// <summary>한 번이라도 시작했는가. 화면 표시에 쓴다.</summary>
    public bool Started { get; private set; }

    void Awake()
    {
        if (voice == null) voice = FindObjectOfType<RaonVoiceClient>();
        if (log == null) log = FindObjectOfType<ConversationLog>();
    }

    void Start()
    {
        if (startButton) startButton.onClick.AddListener(Begin);
        Refresh();
    }

    /// <summary>서버의 대화 맥락을 비우고 기록을 지운다. 다음 사람이 앞사람 이야기를 이어받으면 안 된다.</summary>
    public void Begin()
    {
        if (voice == null || !voice.HasSession) return;
        voice.ResetSession();
        if (log != null) log.Clear();
        Started = true;
        Refresh();
    }

    void Update() => Refresh();

    void Refresh()
    {
        bool ready = voice != null && voice.HasSession;
        if (startButton) startButton.interactable = ready && !voice.isWaiting;
        if (startLabel)
            startLabel.text = !ready ? "인물 등록 필요"
                            : Started ? "체험 다시 시작"
                            : "체험 시작";
    }
}
