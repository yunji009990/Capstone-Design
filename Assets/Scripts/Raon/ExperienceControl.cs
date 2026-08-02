// ExperienceControl.cs
// 체험을 시작하고 끝낸다.
//
// 이 프로젝트에는 "게임 시작"에 해당하는 코드가 따로 없었다. MainScene 의
// ButtonController.StartContent 는 치료 콘텐츠 애니메이터를 트리거하는 것이라 여기와
// 상관이 없다. 여기서 체험이 시작된다는 것은 곧 대화를 받기 시작한다는 뜻이다.
//
// 말을 걸 때 단추를 누를 필요는 없다 — 자동 감지가 켜져 있어 그냥 말하면 된다.
// 그래서 단추 하나가 시작과 종료를 번갈아 맡는다.

using TMPro;
using UnityEngine;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class ExperienceControl : MonoBehaviour
{
    [Tooltip("비워두면 씬에서 찾는다.")]
    public RaonVoiceClient voice;
    public ConversationLog log;

    [Header("단추")]
    public Button startButton;
    public TMP_Text startLabel;
    [Tooltip("색을 바꿀 대상. 비워두면 단추에서 찾는다.")]
    public Image buttonImage;

    [Tooltip("시작할 수 있을 때 / 진행 중일 때 색.")]
    public Color readyColor = new Color(0.773f, 0.561f, 0.627f);   // 로즈
    public Color stopColor  = new Color(0.667f, 0.310f, 0.267f);   // 붉은 벽돌

    /// <summary>진행 중인가. 화면 표시와 듣기 여부를 함께 정한다.</summary>
    public bool Started { get; private set; }

    void Awake()
    {
        if (voice == null) voice = FindObjectOfType<RaonVoiceClient>();
        if (log == null) log = FindObjectOfType<ConversationLog>();
        if (buttonImage == null && startButton != null)
            buttonImage = startButton.GetComponent<Image>();
    }

    void Start()
    {
        if (startButton) startButton.onClick.AddListener(Toggle);
        // 시작을 누르기 전에는 듣지 않는다. 자동 감지가 켜져 있으면 준비 중에 오간 말이
        // 그대로 서버로 가서, 체험이 시작되기도 전에 인물이 대답한다.
        if (voice != null) voice.autoDetect = false;
        Refresh();
    }

    public void Toggle()
    {
        if (Started) Finish();
        else Begin();
    }

    /// <summary>대화 맥락과 기록을 비우고 듣기 시작한다. 앞사람 이야기를 이어받으면 안 된다.</summary>
    public void Begin()
    {
        if (voice == null || !voice.HasSession) return;
        voice.ResetSession();
        if (log != null) log.Clear();
        voice.autoDetect = true;
        Started = true;
        Refresh();
    }

    /// <summary>
    /// 듣기를 닫는다. 기록은 지우지 않는다 — 끝난 뒤에 무슨 이야기가 오갔는지
    /// 확인할 일이 있다. 다음 사람을 위해 비우는 것은 다시 시작할 때 한다.
    /// </summary>
    public void Finish()
    {
        if (voice != null) voice.autoDetect = false;
        Started = false;
        Refresh();
    }

    void Update() => Refresh();

    void Refresh()
    {
        bool ready = voice != null && voice.HasSession;

        if (startLabel)
            startLabel.text = Started ? "체험 종료"
                            : !ready ? "인물 등록 필요"
                            : "체험 시작";

        // 진행 중일 때만 색이 달라야 지금 어느 쪽인지 한눈에 보인다.
        if (buttonImage)
            buttonImage.color = Started ? stopColor
                              : ready ? readyColor
                              : new Color(readyColor.r, readyColor.g, readyColor.b, 0.35f);

        // 끝내는 것은 언제든 되어야 한다. 시작만 조건을 본다.
        if (startButton)
            startButton.interactable = Started || (ready && !voice.isWaiting);
    }
}
