// ExperienceControl.cs
// 체험을 시작하고 끝낸다.
//
// 이 프로젝트에는 "게임 시작"에 해당하는 코드가 따로 없었다. MainScene 의
// ButtonController.StartContent 는 치료 콘텐츠 애니메이터를 트리거하는 것이라 여기와
// 상관이 없다. 시작 버튼으로 대화를 열고, 연결된 인물의 입장 연출도 시작한다.
//
// 말을 걸 때 단추를 누를 필요는 없다 — 자동 감지가 켜져 있어 그냥 말하면 된다.
// 그래서 단추 하나가 시작과 종료를 번갈아 맡는다.

using TMPro;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class ExperienceControl : MonoBehaviour
{
    [Tooltip("비워두면 씬에서 찾는다.")]
    public DialogueVoiceClient voice;
    public ConversationLog log;
    [Tooltip("체험 시작을 기다리도록 설정한 인물. 비워두면 씬에서 찾는다.")]
    public PersonaSpawner spawner;
    [Tooltip("캐릭터 모델 없이 음성만 사용하는 체험. AI 테스트 씬처럼 스포너가 없으면 자동으로 음성 전용으로 동작한다.")]
    public bool voiceOnlyExperience;
    public bool UsesCharacter => !voiceOnlyExperience && spawner != null && spawner.waitForExperienceStart;
    public enum PreparationState { 등록필요, 서버대기, 마이크필요, 모델대기, 모델로딩, 모델실패, 준비완료, 입장중, 음성연결중, 대화가능, 종료중 }
    public bool IsFinishing { get; private set; }
    ExperienceCues _cues;
    public PreparationState Preparation { get; private set; }
    public string PreparationMessage { get; private set; } = "체험 준비 상태를 확인하는 중입니다.";
    public bool CanStart { get; private set; }
    public bool CheckingPreparation { get; private set; }
    /// <summary>시작을 요청한 뒤에만 표시하는 미완료 항목. 시작 버튼 아래 UI가 사용한다.</summary>
    public string StartWarning { get; private set; } = "";
    bool _startAttempted;
    int _warningKey = -1;
    string _missingPreparation = "";
    public string PreparationSummary
    {
        get
        {
            switch (Preparation)
            {
                case PreparationState.등록필요: return "인물 등록 필요";
                case PreparationState.서버대기: return "대화 서버 확인 필요";
                case PreparationState.마이크필요: return "마이크 확인 필요";
                case PreparationState.모델대기: return "캐릭터 모델 대기 중";
                case PreparationState.모델로딩: return "캐릭터 불러오는 중";
                case PreparationState.모델실패: return "캐릭터 모델 확인 필요";
                case PreparationState.준비완료: return "준비 완료 · 체험 시작 가능";
                case PreparationState.입장중: return "캐릭터 입장 중 · 마이크 대기";
                case PreparationState.음성연결중: return "음성 연결 준비 중";
                case PreparationState.종료중: return "페이드아웃 · 종료 중";
                default: return "대화 가능 · 말을 걸어주세요";
            }
        }
    }

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
        if (voice == null) voice = FindObjectOfType<DialogueVoiceClient>();
        if (log == null) log = FindObjectOfType<ConversationLog>();
        if (spawner == null) spawner = FindObjectOfType<PersonaSpawner>();
        _cues = FindObjectOfType<ExperienceCues>();
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
        if (IsFinishing) return;
        // 어떤 입력이 단추를 눌렀는지 확정할 수는 없다. 이 시점에 눌려 있던 키만 관측으로 남긴다.
        // EventSystem 의 Submit 바인딩(Space/Enter)으로도 onClick 이 불릴 수 있어 함께 본다.
        _lastToggleKeys =
            (Input.GetKey(KeyCode.Space) ? "space" : "") +
            (Input.GetKey(KeyCode.Return) || Input.GetKey(KeyCode.KeypadEnter) ? "enter" : "");
        if (Started)
        {
            // 이 Toggle 에서 바로 끝내는 경우에만 지금 관측한 키를 원인에 붙인다.
            FinishWith("ui_finish" + (string.IsNullOrEmpty(_lastToggleKeys) ? "" : "_" + _lastToggleKeys), true);
        }
        else
        {
            _startAttempted = true;
            Refresh();
            if (CanStart) Begin();
            else RetryPreparation(true);
        }
    }

    /// <summary>마지막 Toggle 시점에 눌려 있던 키. 확정 원인이 아니라 관측값이다.
    /// 프로그램이 부르는 종료에는 이 값을 쓰지 않는다. 지난 클릭의 키가 섞이면 안 된다.</summary>
    public string LastToggleKeys => _lastToggleKeys;
    string _lastToggleKeys = "";

    /// <summary>대화 맥락과 기록을 비우고 듣기 시작한다. 앞사람 이야기를 이어받으면 안 된다.</summary>
    public void Begin()
    {
        if (!isActiveAndEnabled || Started || IsFinishing) return;
        _startAttempted = true;
        Refresh();
        if (!CanStart) return;
        StartSelection.startRequested = false;
        voice.SetConversationInputAllowed(!UsesCharacter);
        if (!voice.BeginExperience()) { voice.SetConversationInputAllowed(true); Refresh(); return; }
        if (log != null) log.Clear();
        Started = true;
        if (UsesCharacter) spawner.BeginPresentation();
        Refresh();
    }

    /// <summary>
    /// 듣기를 닫는다. 기록은 지우지 않는다 — 끝난 뒤에 무슨 이야기가 오갔는지
    /// 확인할 일이 있다. 다음 사람을 위해 비우는 것은 다시 시작할 때 한다.
    /// </summary>
    public void Finish() => FinishWith("ui_finish", true);

    /// <summary>종료 원인을 코드로 넘겨 끝낸다. 지난 Toggle 의 키를 다시 붙이지 않는다.</summary>
    void FinishWith(string reason, bool fade = false)
    {
        if (fade && (!Started || IsFinishing)) return;
        bool fadeOut = fade && Started && isActiveAndEnabled && _cues != null && _cues.isActiveAndEnabled;
        IsFinishing = fadeOut;
        // 이미 실패 등으로 끝난 뒤라면 그때 잡힌 실제 원인을 덮어쓰지 않는다.
        if (voice != null && (voice.ExperienceActive || string.IsNullOrEmpty(voice.LastEndReason)))
            voice.EndExperience(reason);
        Started = false;
        _startAttempted = false;
        if (fadeOut && _cues.TryFadeOutForFinish(CompleteFinish)) { Refresh(); return; }
        if (_cues != null) _cues.CancelScreenFade();
        CompleteFinish();
    }

    void CompleteFinish()
    {
        if (spawner != null && spawner.waitForExperienceStart) spawner.EndPresentation();
        IsFinishing = false;
        Refresh();
    }

    void Update()
    {
        if (IsFinishing)
        {
            if (_cues == null || !_cues.isActiveAndEnabled) CompleteFinish();
            Refresh();
            return;
        }
        if (Started && (voice == null || !voice.ExperienceActive))
        {
            if (spawner != null && spawner.waitForExperienceStart) spawner.EndPresentation();
            Started = false;
        }
        if (Started && UsesCharacter && (!spawner.ModelReady || !spawner.isActiveAndEnabled))
            FinishWith("character_preparation_failed");
        if (Started && voice != null)
            voice.SetConversationInputAllowed(!UsesCharacter || spawner.ReadyForConversation);
        if (StartSelection.startRequested && !Started) _startAttempted = true;
        Refresh();
        // 시작 화면의 요청도 수동 시작과 같은 최신 준비 조건을 통과해야 한다.
        // 요청을 먼저 소비해 시작 실패나 종료 뒤에 매 프레임 다시 시작하지 않는다.
        if (StartSelection.startRequested && !Started && CanStart)
        {
            StartSelection.startRequested = false;
            Begin();
        }
    }

    /// <summary>시작 버튼에서 마이크·서버·미완료 모델을 다시 확인한다.</summary>
    public void RetryPreparation(bool startAfterCheck = false)
    {
        if (!isActiveAndEnabled || Started || IsFinishing || CheckingPreparation || voice == null) return;
        voice.RefreshMicrophones();
        if (UsesCharacter) spawner.RetryPreparation();
        StartCoroutine(CheckPreparation(startAfterCheck));
    }

    IEnumerator CheckPreparation(bool startAfterCheck)
    {
        CheckingPreparation = true;
        Refresh();
        try { yield return voice.CheckHealth(); }
        finally { CheckingPreparation = false; Refresh(); }
        if (startAfterCheck && isActiveAndEnabled && !Started && !IsFinishing && CanStart) Begin();
    }

    // 사용자가 단추를 누른 것이 아니다. ui_finish 로 위장하지 않는다.
    void OnDisable() { FinishWith("control_disabled"); CheckingPreparation = false; }

    void OnDestroy()
    {
        if (startButton) startButton.onClick.RemoveListener(Toggle);
    }

    void Refresh()
    {
        bool registered = voice != null && (voice.HasSession || voice.useTestProfile);
        CanStart = false;
        if (IsFinishing) SetPreparation(PreparationState.종료중, "화면이 어두워지며 체험을 마칩니다.");
        else if (!registered) SetPreparation(PreparationState.등록필요, "웹에서 인물을 등록한 뒤 체험 시작을 눌러주세요.");
        else if (Started)
        {
            if (UsesCharacter && !spawner.ReadyForConversation)
                SetPreparation(PreparationState.입장중, "캐릭터가 입장하고 있습니다. 착석 후 대화가 시작됩니다.");
            else if (!voice.DialogueConnected)
                SetPreparation(PreparationState.음성연결중, "음성을 준비하는 중입니다. 연결이 끝나면 대화를 시작합니다.");
            else SetPreparation(PreparationState.대화가능, "대화 준비 완료 · 편하게 말을 걸어주세요.");
        }
        else if (!voice.serverReady) SetPreparation(PreparationState.서버대기, "대화 서버 연결을 확인하고 체험 시작을 눌러주세요.");
        else if (!voice.MicrophoneAvailable) SetPreparation(PreparationState.마이크필요, "마이크를 연결·선택한 뒤 체험 시작을 눌러주세요.");
        else if (UsesCharacter && !spawner.ModelReady)
        {
            var phase = spawner.Preparation;
            if (phase == PersonaSpawner.PreparationPhase.실패)
                SetPreparation(PreparationState.모델실패, spawner.PreparationMessage);
            else if (phase == PersonaSpawner.PreparationPhase.다운로드 || phase == PersonaSpawner.PreparationPhase.표시준비)
                SetPreparation(PreparationState.모델로딩, spawner.PreparationMessage);
            else SetPreparation(PreparationState.모델대기, spawner.PreparationMessage);
        }
        else
        {
            CanStart = !voice.isWaiting && !CheckingPreparation;
            SetPreparation(PreparationState.준비완료, "준비 완료 · 체험 시작을 눌러주세요.");
        }

        RefreshStartWarning(registered);
        if (startLabel)
            startLabel.text = IsFinishing ? "종료 중…" : Started ? "체험 종료"
                            : CheckingPreparation ? "준비 확인 중…" : "체험 시작";

        // 진행 중일 때만 색이 달라야 지금 어느 쪽인지 한눈에 보인다.
        if (buttonImage)
            buttonImage.color = Started ? stopColor : readyColor;

        // 미준비 상태에서도 눌러 원인 확인·재시도를 할 수 있다. 실제 시작은 CanStart로 차단한다.
        if (startButton)
            startButton.interactable = !IsFinishing && (Started || !CheckingPreparation);
    }

    void RefreshStartWarning(bool registered)
    {
        int key = (!registered ? 1 : 0) | (voice == null || !voice.serverReady ? 2 : 0)
                | (voice != null && !voice.MicrophoneAvailable ? 4 : 0)
                | (registered && UsesCharacter && !spawner.ModelReady ? 8 : 0)
                | (voice != null && voice.isWaiting ? 16 : 0)
                | (UsesCharacter && spawner.Preparation == PersonaSpawner.PreparationPhase.실패 ? 32 : 0);
        // 준비 상태가 바뀔 때만 문자열을 만든다.
        if (key != _warningKey)
        {
            _warningKey = key;
            var missing = new List<string>();
            if ((key & 1) != 0) missing.Add("인물 등록 필요");
            if ((key & 2) != 0) missing.Add("대화 서버 연결 필요");
            if ((key & 4) != 0) missing.Add("마이크 연결·선택 필요");
            if ((key & 8) != 0) missing.Add((key & 32) != 0 ? "캐릭터 불러오기 실패" : "캐릭터 모델 준비 중");
            if ((key & 16) != 0) missing.Add("이전 응답 정리 중");
            _missingPreparation = string.Join("\n", missing);
        }
        StartWarning = _startAttempted && !Started && !IsFinishing ? _missingPreparation : "";
    }

    void SetPreparation(PreparationState state, string message)
    {
        Preparation = state;
        PreparationMessage = message;
    }
}
