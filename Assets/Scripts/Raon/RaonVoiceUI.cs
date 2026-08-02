using System.Collections;
using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

/// <summary>
/// Raon 음성 대화 테스트용 Canvas UI.
/// 상태 표시등 / 마이크 선택 / 입력 레벨 미터 / 자막을 담당합니다.
/// </summary>
public class RaonVoiceUI : MonoBehaviour
{
    [Header("연결")]
    [SerializeField] private RaonVoiceClient client;

    [Header("상태")]
    [SerializeField] private Image statusDot;
    [SerializeField] private TMP_Text statusLabel;
    [SerializeField] private TMP_Text messageLabel;

    [Header("마이크")]
    [SerializeField] private TMP_Dropdown micDropdown;
    [SerializeField] private Image levelFill;
    [Tooltip("감지 기준값 위치를 표시하는 세로선")]
    [SerializeField] private RectTransform thresholdMarker;
    [Tooltip("레벨 미터가 가득 찰 때의 입력 크기(RMS). 말소리는 보통 0.05~0.25")]
    [SerializeField] private float levelDisplayRange = 0.3f;

    [Header("자막")]
    [SerializeField] private TMP_Text heardLabel;
    [SerializeField] private TMP_Text answerLabel;

    [Header("버튼")]
    [SerializeField] private Button talkButton;
    [SerializeField] private TMP_Text talkButtonLabel;
    [SerializeField] private Button resetButton;
    [SerializeField] private Button healthButton;

    static readonly Color Idle = new Color(0.45f, 0.45f, 0.48f);
    static readonly Color Recording = new Color(0.90f, 0.30f, 0.30f);
    static readonly Color Waiting = new Color(0.95f, 0.75f, 0.20f);
    static readonly Color Speaking = new Color(0.30f, 0.80f, 0.55f);
    static readonly Color Offline = new Color(0.55f, 0.15f, 0.15f);

    float _shownLevel;
    float _waitStart;
    bool _wasWaiting;

    void OnEnable()
    {
        client.OnAnswer += HandleAnswer;
        client.OnError += HandleError;
        client.OnHealth += HandleHealth;
    }

    void OnDisable()
    {
        client.OnAnswer -= HandleAnswer;
        client.OnError -= HandleError;
        client.OnHealth -= HandleHealth;
    }

    void Start()
    {
        PopulateMicDropdown();

        if (talkButton) talkButton.onClick.AddListener(ToggleTalk);
        if (resetButton) resetButton.onClick.AddListener(ResetConversation);
        if (healthButton) healthButton.onClick.AddListener(() => StartCoroutine(client.CheckHealth()));

        SetText(heardLabel, "");
        SetText(answerLabel, "");
        SetText(messageLabel, "서버 확인 중…");
    }

    /// <summary>
    /// 마이크 목록을 다시 읽는다. 한 번만 채우면 나중에 꽂은 마이크가 안 보인다 —
    /// 목록을 펼치는 순간에 다시 부르라고 public 으로 둔다.
    /// </summary>
    public void PopulateMicDropdown()
    {
        if (micDropdown == null) return;

        var devices = new List<string>(client.MicDevices);
        micDropdown.onValueChanged.RemoveAllListeners();   // 다시 채우면 두 번 걸린다
        micDropdown.ClearOptions();

        if (devices.Count == 0)
        {
            micDropdown.AddOptions(new List<string> { "마이크 없음" });
            micDropdown.interactable = false;
            return;
        }

        micDropdown.AddOptions(devices);
        int current = devices.IndexOf(client.CurrentMic);
        micDropdown.SetValueWithoutNotify(current < 0 ? 0 : current);
        micDropdown.onValueChanged.AddListener(i => client.SelectMic(devices[i]));
    }

    void ToggleTalk()
    {
        if (client.isRecording) client.StopAndSend();
        else if (!client.isWaiting) client.StartRecording();
    }

    void ResetConversation()
    {
        client.ResetSession();
        SetText(heardLabel, "");
        SetText(answerLabel, "");
        SetText(messageLabel, "대화 맥락을 초기화했습니다.");
    }

    void HandleAnswer(string heard, string answer)
    {
        SetText(heardLabel, string.IsNullOrEmpty(heard) ? "" : "나: " + heard);
        SetText(answerLabel, string.IsNullOrEmpty(answer) ? "" : "캐릭터: " + answer);
        SetText(messageLabel, "");
    }

    void HandleError(string error) => SetText(messageLabel, "오류: " + error);

    void HandleHealth(bool ready, string info) => SetText(messageLabel, info);

    void Update()
    {
        Color color;
        string state;

        // 대기 시간을 세어 보여준다. 멈춘 것인지 기다리는 것인지 구분되지 않으면 훨씬 길게 느껴진다.
        if (client.isWaiting && !_wasWaiting) _waitStart = Time.time;
        _wasWaiting = client.isWaiting;

        if (client.isRecording) { color = Recording; state = "● 듣고 있습니다 — 말을 멈추면 자동으로 보냅니다"; }
        else if (client.isWaiting) { color = Waiting; state = $"생각 중… {Time.time - _waitStart:F1}초"; }
        else if (client.IsSpeaking) { color = Speaking; state = "말하는 중"; }
        else if (!client.serverReady) { color = Offline; state = "서버 대기 중"; }
        // 서버에 기본 인물이 없다. 등록 전에는 시작할 수 없다는 것을 분명히 알린다.
        else if (!client.HasSession) { color = Offline; state = "인물이 등록되지 않았습니다 — 웹에서 등록하세요"; }
        else if (!client.IsListening) { color = Offline; state = "마이크를 열 수 없습니다"; }
        else if (client.autoDetect) { color = Idle; state = "대기 중 — 그냥 말을 걸어보세요"; }
        else { color = Idle; state = "말하기 버튼 또는 스페이스바"; }

        if (statusDot) statusDot.color = color;
        SetText(statusLabel, state);

        if (talkButtonLabel) talkButtonLabel.text = client.isRecording ? "전송" : "말하기";
        if (talkButton) talkButton.interactable = !client.isWaiting && client.HasSession;
        if (micDropdown) micDropdown.interactable = !client.isRecording && client.MicDevices.Length > 0;

        // 레벨 미터: 올라갈 땐 즉시, 내려올 땐 부드럽게
        float range = Mathf.Max(0.001f, levelDisplayRange);
        float target = client.MicLevel / range;
        _shownLevel = target > _shownLevel ? target : Mathf.Lerp(_shownLevel, target, Time.deltaTime * 8f);
        if (levelFill)
        {
            levelFill.fillAmount = Mathf.Clamp01(_shownLevel);
            // 기준값을 넘는 동안에는 색으로도 알려준다
            levelFill.color = client.MicLevel > client.VadThreshold ? Recording : Speaking;
        }

        // 감지 기준선 위치
        if (thresholdMarker)
        {
            float x = Mathf.Clamp01(client.VadThreshold / range);
            thresholdMarker.anchorMin = new Vector2(x, 0f);
            thresholdMarker.anchorMax = new Vector2(x, 1f);
        }
    }

    static void SetText(TMP_Text label, string value)
    {
        if (label) label.text = value;
    }
}
