using System;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.UI;

/// <summary>Desktop test scene using the same audio transport and dialogue client as VR.</summary>
[DefaultExecutionOrder(-200)]
[RequireComponent(typeof(DialogueVoiceClient))]
public partial class DialogueTestPanel : MonoBehaviour
{
    public Font koreanFont;
    public const string PreferencePrefix = "DialogueTest.";
    public static string PreferenceKey => PreferencePrefix + Application.dataPath;
    public DialogueVoiceClient Voice { get; private set; }
    public bool CheckingServer => _checking;
    public string ConversationText => _conversation != null ? _conversation.text : "";

    readonly List<string> _turns = new List<string>();
    readonly List<string> _interjections = new List<string>();
    readonly Color _ink = new Color(.90f, .93f, .98f);
    readonly Color _muted = new Color(.61f, .68f, .79f);
    readonly Color _fieldColor = new Color(.075f, .11f, .18f);
    Font _font;
    Transform _root;
    InputField _url, _token, _persona;
    Text _health, _state, _metadata, _timing, _transcript, _conversation, _micName, _decision, _subtitle;
    Button _start, _stop, _reset, _cancel, _context, _check, _nextMic;
    Image _level;
    ScrollRect _scroll;
    string _heard = "", _answer = "";
    bool _checking;
    bool _previousRunInBackground;

    void Awake()
    {
        _previousRunInBackground = Application.runInBackground;
        Application.runInBackground = true;
        Voice = GetComponent<DialogueVoiceClient>();
        Voice.useTestProfile = true;
        Voice.followServerSession = false;
        Voice.checkHealthOnStart = false; // This panel owns the health request and its buttons.
        Voice.showHeardSubtitle = true;
        Voice.autoDetect = false;
        if (string.IsNullOrEmpty(Voice.dialogueServerUrl))
            Voice.dialogueServerUrl = "http://220.69.208.201:8002";
#if UNITY_EDITOR
        // Project-local AI test settings, independent of the experience scenes.
        Voice.dialogueServerUrl = UnityEditor.EditorPrefs.GetString(PreferenceKey + ".url", Voice.dialogueServerUrl);
        Voice.token = UnityEditor.EditorPrefs.GetString(PreferenceKey + ".token", Voice.token);
#endif
        _font = koreanFont != null ? koreanFont : Font.CreateDynamicFontFromOSFont(
            new[] { "Malgun Gothic", "Arial" }, 22);
        BuildPanel();
        Voice.OnHealth += HealthChanged;
        Voice.OnError += ShowError;
        Voice.OnTranscriptUpdated += TranscriptChanged;
        Voice.OnAnswerUpdated += AnswerChanged;
        Voice.OnAnswer += AnswerDone;
        Voice.OnAnswerInterrupted += AnswerInterrupted;
        Voice.OnInterruptionDecision += InterruptionDecided;
        Voice.OnDialogueReset += ClearLog;
    }

    IEnumerator Start()
    {
        // Run after DialogueVoiceClient.Start has finished initializing.
        yield return null;
        yield return CheckServer(false);
    }

    void OnDisable()
    {
        StopAllCoroutines();
        _checking = false;
        _referenceBusy = false;
        StopReferencePreview();
        if (Voice != null) Voice.EndExperience();
    }

    void OnDestroy()
    {
        Application.runInBackground = _previousRunInBackground;
        if (_previewClip != null) Destroy(_previewClip);
        if (Voice == null) return;
        Voice.OnHealth -= HealthChanged;
        Voice.OnError -= ShowError;
        Voice.OnTranscriptUpdated -= TranscriptChanged;
        Voice.OnAnswerUpdated -= AnswerChanged;
        Voice.OnAnswer -= AnswerDone;
        Voice.OnAnswerInterrupted -= AnswerInterrupted;
        Voice.OnInterruptionDecision -= InterruptionDecided;
        Voice.OnDialogueReset -= ClearLog;
    }

    void Update()
    {
        bool active = Voice.ExperienceActive, connected = Voice.DialogueConnected;
        bool editable = !active && !_checking && !_referenceBusy;
        _url.interactable = _token.interactable = _persona.interactable = editable;
        _check.interactable = editable;
        UpdateReferencePanel(editable);
        _subtitle.text = Voice.TtsEnabled ? "마이크 입력 · SenseVoice 분석 · Qwen3-TTS 음성 답변     /     인물 등록 없이 바로 테스트" :
            "마이크 입력 · SenseVoice 분석 · LLM 텍스트 답변     /     TTS 꺼짐";
        _start.interactable = editable && Voice.MicDevices.Length > 0;
        _nextMic.interactable = editable && Voice.MicDevices.Length > 1;
        _stop.interactable = active;
        _reset.interactable = _context.interactable = connected;
        _cancel.interactable = connected && (Voice.isWaiting || Voice.IsSpeaking || Voice.ResponsePaused);
        _state.text = _checking ? "서버를 확인하고 있습니다" : !active ? "테스트를 시작하세요" :
            !connected ? Voice.ConnectionStatus : Voice.isRecording ? "사용자의 말을 듣고 있습니다" :
            Voice.DecisionPending ? "응답 방향을 판단하고 있습니다" :
            Voice.ResponsePaused ? "이전 답변 보관 중 · 말씀을 기다립니다" :
            Voice.IsSpeaking ? "AI가 말하고 있습니다 · 끼어들기 가능" :
            Voice.isWaiting ? "AI가 답변을 만들고 있습니다" : "입력을 기다리고 있습니다";
        _decision.text = Voice.DecisionPending ? "끼어들기 판정 중 · 이전 답변을 보관하고 있습니다." :
            string.IsNullOrEmpty(Voice.InterruptionAction) ? (Voice.TtsEnabled ?
                "끼어들면 잠시 멈춘 뒤, 말씀하신 의도에 따라 이어 말하거나 답변을 바꿉니다." :
                "TTS 꺼짐 · 생성 중 끼어들면 의도를 판정하고, 완료된 답변은 대화 기록으로 남깁니다.") :
            "끼어들기  " + DecisionName(Voice.InterruptionAction) + " · " + Voice.InterruptionReason +
            (Voice.DecisionSeconds >= 0 ? $" · {Voice.DecisionSeconds:F2}초" : "");
        string inputSource = Voice.VoiceAudioEvent == "text" ? "텍스트 입력 · STT·감정 추출 생략" :
            "SenseVoiceSmall · CPU · " + (Voice.TranscriptIsFinal ? "최종 결과" : "중간 결과");
        _metadata.text = inputSource +
            "\n감정  " + EmotionName(Voice.VoiceEmotion) + " (" + Voice.VoiceEmotion + ")    ·    소리  " + Voice.VoiceAudioEvent +
            $"\n언어  {Voice.VoiceLanguage}    ·    발화 {Voice.VoiceDuration:F1}초    ·    음량 {Voice.VoiceRmsDb:F1} dBFS    ·    처리 {RouteName(Voice.ResponseRoute)}";
        if (Voice.VoiceAudioEvent == "text")
            _metadata.text = inputSource + "\n음성 감정·소리·음량: 측정하지 않음" +
                $"\n언어  {Voice.VoiceLanguage}    ·    처리 {RouteName(Voice.ResponseRoute)}";
        _timing.text = Voice.TotalResponseSeconds < 0 ? "음성 감정은 모델의 추정입니다. 정확도 점수는 제공하지 않습니다." :
            !Voice.TtsEnabled ? $"첫 글자 {Voice.FirstTextSeconds:F2}초  ·  생성 완료 {Voice.TotalResponseSeconds:F2}초" :
            $"첫 글자 {Voice.FirstTextSeconds:F2}초  ·  첫 음성 {Voice.FirstAudioSeconds:F2}초  ·  생성 완료 {Voice.TotalResponseSeconds:F2}초";
        _level.rectTransform.sizeDelta = new Vector2(146f * Mathf.Clamp01(Voice.MicLevel * 12f), 8f);
        _micName.text = "마이크: " + (string.IsNullOrEmpty(Voice.CurrentMic) ? "장치 없음 · 연결 후 다시 실행하세요" : Voice.CurrentMic);
    }

    IEnumerator CheckServer(bool begin)
    {
        if (_checking || Voice.ExperienceActive) yield break;
        if (!Uri.TryCreate(_url.text.Trim(), UriKind.Absolute, out var uri) ||
            (uri.Scheme != "http" && uri.Scheme != "https"))
        { ShowError("서버 주소를 http://주소:포트 형식으로 입력하세요."); yield break; }
        Voice.dialogueServerUrl = _url.text.Trim().TrimEnd('/');
        Voice.token = _token.text;
        Voice.testPersona = _persona.text;
        Voice.captureRealtimeMicrophone = true;
        ValidateReferenceServer();
        if (begin && string.IsNullOrWhiteSpace(Voice.testPersona))
        { ShowError("테스트 인물 설정을 입력하세요."); yield break; }
#if UNITY_EDITOR
        UnityEditor.EditorPrefs.SetString(PreferenceKey + ".url", Voice.dialogueServerUrl);
        UnityEditor.EditorPrefs.SetString(PreferenceKey + ".token", Voice.token);
#endif
        if (string.IsNullOrWhiteSpace(Voice.token))
        {
            Voice.serverReady = false;
            ShowError("접속 토큰을 입력한 뒤 '확인'을 눌러 주세요.");
            yield break;
        }
        _checking = true;
        _health.text = "연결 확인 중…";
        yield return Voice.CheckHealth();
        _checking = false;
        if (begin && Voice.serverReady)
        {
            ClearLog();
            StopReferencePreview();
            Voice.BeginExperience();
        }
    }

#if UNITY_EDITOR
    public bool BeginAudioFileCheck()
    {
        if (Voice.ExperienceActive || _checking || !Voice.serverReady) return false;
        Voice.captureRealtimeMicrophone = false;
        StopReferencePreview();
        ClearLog();
        return Voice.BeginExperience();
    }
#endif

    void HealthChanged(bool ready, string info)
    {
        _health.color = ready ? new Color(.45f, .88f, .72f) : new Color(1f, .66f, .48f);
        _health.text = info;
    }

    void ShowError(string message) => HealthChanged(false, message);
    void TranscriptChanged(string heard) { if (!Voice.ResponsePaused) _heard = heard; RenderLog(); }
    void AnswerChanged(string heard, string answer) { _heard = heard; _answer = answer; RenderLog(); }
    void AnswerDone(string heard, string answer) => AddTurn(heard, answer, false);
    void AnswerInterrupted(string heard, string answer) => AddTurn(heard, answer, true);

    void InterruptionDecided(string action, string heard)
    {
        if ((action == "resume" || action == "hold") && !string.IsNullOrWhiteSpace(heard))
        {
            _interjections.Add("끼어들기  ·  " + heard + "  [" + DecisionName(action) + "]");
            if (_interjections.Count > 10) _interjections.RemoveAt(0);
        }
        RenderLog();
    }

    void AddTurn(string heard, string answer, bool interrupted)
    {
        if (!string.IsNullOrWhiteSpace(heard) || !string.IsNullOrWhiteSpace(answer))
        {
            _turns.Add("나  ·  " + heard + "\nAI  ·  " + answer + (interrupted ? "  [중단됨]" : "") + InterjectionText());
            if (_turns.Count > 30) _turns.RemoveAt(0);
        }
        _heard = _answer = "";
        _interjections.Clear();
        RenderLog();
    }

    void ClearLog()
    {
        _turns.Clear();
        _interjections.Clear();
        _heard = _answer = "";
        RenderLog();
    }

    void RenderLog()
    {
        _transcript.text = string.IsNullOrEmpty(Voice.lastHeard) ? "테스트를 시작하고 마이크로 말해 주세요." : Voice.lastHeard;
        string pending = string.IsNullOrEmpty(_heard) && string.IsNullOrEmpty(_answer) ? "" :
            "나  ·  " + _heard + "\nAI  ·  " + (string.IsNullOrEmpty(_answer) ? "…" : _answer) + InterjectionText();
        _conversation.text = string.Join("\n\n", _turns) +
            (pending.Length > 0 ? (_turns.Count > 0 ? "\n\n" : "") + pending : "");
        Canvas.ForceUpdateCanvases();
        _scroll.verticalNormalizedPosition = 0;
    }

    string InterjectionText() => _interjections.Count == 0 ? "" : "\n" + string.Join("\n", _interjections);

    static string DecisionName(string action)
    {
        switch (action)
        {
            case "resume": return "이어 말하기";
            case "revise": return "수정·보완";
            case "switch": return "주제 전환";
            case "hold": return "대기";
            case "clarify": return "의도 확인";
            case "expired": return "보관 종료";
            default: return action;
        }
    }

    void NextMicrophone()
    {
        var devices = Voice.MicDevices;
        if (devices.Length == 0) return;
        int current = Array.IndexOf(devices, Voice.CurrentMic);
        Voice.SelectMic(devices[(current + 1) % devices.Length]);
    }

    static string EmotionName(string value)
    {
        switch (value)
        {
            case "neutral": return "중립";
            case "happy": return "기쁨";
            case "sad": return "슬픔";
            case "angry": return "분노";
            case "fearful": return "두려움";
            case "disgusted": return "혐오";
            case "surprised": return "놀람";
            default: return "미확인";
        }
    }

    static string RouteName(string value) => value == "normal" ? "일반" : value == "reasoning" ? "추론" :
        string.IsNullOrEmpty(value) ? "대기" : value;

    void BuildPanel()
    {
        var canvas = new GameObject("AI test canvas", typeof(RectTransform), typeof(Canvas),
            typeof(CanvasScaler), typeof(GraphicRaycaster));
        canvas.transform.SetParent(transform, false);
        canvas.GetComponent<Canvas>().renderMode = RenderMode.ScreenSpaceOverlay;
        var scaler = canvas.GetComponent<CanvasScaler>();
        scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
        scaler.referenceResolution = new Vector2(1440, 900);
        scaler.screenMatchMode = CanvasScaler.ScreenMatchMode.Expand;
        var frame = new GameObject("Test frame", typeof(RectTransform)).GetComponent<RectTransform>();
        frame.SetParent(canvas.transform, false);
        frame.anchorMin = frame.anchorMax = frame.pivot = new Vector2(.5f, .5f);
        frame.sizeDelta = new Vector2(1440, 900);
        _root = frame;
        var bg = Box(_root, "Background", 0, 0, 1440, 900, new Color(.035f, .055f, .095f));
        bg.raycastTarget = false;
        Label(_root, "AI 응답 테스트", 24, 24, 900, 46, 34);
        _subtitle = Label(_root, "", 26, 74, 1360, 28, 19, true);
        Box(_root, "Settings", 24, 112, 390, 764, new Color(.065f, .09f, .145f));
        Box(_root, "Conversation", 430, 112, 986, 764, new Color(.065f, .09f, .145f));

        Label(_root, "대화 서버", 44, 130, 340, 26, 20);
        _url = Field("Server URL", Voice.dialogueServerUrl, 44, 164, 266, 38, 512);
        _check = ButtonAt("확인", 318, 164, 76, 38, () => StartCoroutine(CheckServer(false)));
        _health = Label(_root, "연결 확인 중…", 44, 210, 350, 62, 16, true);
        Label(_root, "접속 토큰", 44, 274, 340, 24, 18, true);
        _token = Field("Token", Voice.token, 44, 302, 350, 38, 512);
        _token.contentType = InputField.ContentType.Password;
        _token.ForceLabelUpdate();
        Label(_root, "음성으로 대화합니다", 44, 354, 350, 32, 22);
        _micName = Label(_root, "", 44, 402, 350, 46, 16, true);
        _nextMic = ButtonAt("다음 마이크 선택", 44, 454, 350, 34, NextMicrophone);
        _referenceSettings = ButtonAt("참조 목소리 · 억양 설정", 44, 500, 350, 38, () => _referenceModal.SetActive(true));
        Label(_root, "테스트 인물 · 답변 지침", 44, 548, 350, 26, 20);
        _persona = Field("Test persona", Voice.testPersona, 44, 582, 350, 86, 3000, true);
        _start = ButtonAt("테스트 시작", 44, 686, 171, 46, () => StartCoroutine(CheckServer(true)), true);
        _stop = ButtonAt("종료", 223, 686, 171, 46, () => Voice.EndExperience());
        _reset = ButtonAt("새 대화 · 기록 초기화", 44, 744, 350, 42, () => Voice.ResetDialogueHistory());
        ButtonAt("대화 기록 복사", 44, 798, 350, 36, () => GUIUtility.systemCopyBuffer = ConversationText);
        Label(_root, "설정 변경은 다음 테스트 시작부터 적용됩니다.", 44, 844, 350, 22, 15, true);

        _state = Label(_root, "테스트를 시작하세요", 452, 132, 620, 34, 25);
        var meter = Box(_root, "Microphone level", 1090, 146, 146, 8, _fieldColor);
        _level = Box(meter.transform, "Level", 0, 0, 146, 8, new Color(.32f, .85f, .68f));
        _level.rectTransform.sizeDelta = new Vector2(0, 8);
        _cancel = ButtonAt("답변 중단", 1252, 132, 142, 38, () => Voice.CancelDialogueResponse());
        _metadata = Label(_root, "", 452, 184, 942, 86, 19);
        _timing = Label(_root, "", 452, 278, 942, 25, 17, true);
        Label(_root, "입력 문장", 452, 320, 942, 24, 18, true);
        Box(_root, "Transcript background", 452, 350, 942, 70, _fieldColor);
        _transcript = Label(_root, "테스트를 시작하고 마이크로 말해 주세요.", 466, 358, 914, 54, 22);
        _transcript.verticalOverflow = VerticalWrapMode.Truncate;
        Label(_root, "대화 기록 · AI 답변", 452, 438, 942, 26, 20);
        BuildConversationScroll();
        _decision = Label(_root, "", 452, 726, 942, 32, 16, true);
        _decision.verticalOverflow = VerticalWrapMode.Truncate;
        _context = ButtonAt("사진 집기 행동 전달", 452, 784, 270, 44,
            () => Voice.ReportUnityAction("사용자가 가족 사진을 집어 들었다."));
        Label(_root, "행동 전달 후 15초 안에 물건에 대해 음성으로 물어보세요.", 744, 792, 640, 40, 17, true);
        Label(_root, "스피커 소리가 마이크로 다시 들어오지 않도록 헤드폰을 사용하세요.", 452, 844, 942, 22, 15, true);
        _referenceSummary = Label(_root, "", 452, 760, 942, 22, 16, true);
        BuildReferencePanel();

        if (EventSystem.current == null)
        {
            var events = new GameObject("Test EventSystem", typeof(EventSystem), typeof(StandaloneInputModule));
            events.transform.SetParent(transform, false);
        }
    }

    RectTransform Rect(Transform parent, string name, float x, float y, float width, float height)
    {
        var rect = new GameObject(name, typeof(RectTransform)).GetComponent<RectTransform>();
        rect.SetParent(parent, false);
        rect.anchorMin = rect.anchorMax = new Vector2(0, 1);
        rect.pivot = new Vector2(0, 1);
        rect.anchoredPosition = new Vector2(x, -y);
        rect.sizeDelta = new Vector2(width, height);
        return rect;
    }

    Image Box(Transform parent, string name, float x, float y, float w, float h, Color color)
    {
        var image = Rect(parent, name, x, y, w, h).gameObject.AddComponent<Image>();
        image.color = color;
        return image;
    }

    Text Label(Transform parent, string text, float x, float y, float w, float h, int size, bool muted = false)
    {
        var label = Rect(parent, "Label", x, y, w, h).gameObject.AddComponent<Text>();
        label.font = _font;
        label.fontSize = size;
        label.color = muted ? _muted : _ink;
        label.text = text;
        label.supportRichText = false;
        label.raycastTarget = false;
        label.horizontalOverflow = HorizontalWrapMode.Wrap;
        label.verticalOverflow = VerticalWrapMode.Overflow;
        return label;
    }

    InputField Field(string name, string value, float x, float y, float w, float h, int limit, bool multiline = false)
    {
        var background = Box(_root, name, x, y, w, h, _fieldColor);
        var field = background.gameObject.AddComponent<InputField>();
        field.targetGraphic = background;
        field.textComponent = Label(background.transform, "", 10, 7, w - 20, h - 14, 18);
        field.textComponent.verticalOverflow = VerticalWrapMode.Truncate;
        field.lineType = multiline ? InputField.LineType.MultiLineNewline : InputField.LineType.SingleLine;
        field.characterLimit = limit;
        field.text = value;
        field.customCaretColor = true;
        field.caretColor = _ink;
        return field;
    }

    Button ButtonAt(string text, float x, float y, float w, float h, Action click, bool primary = false)
    {
        var bg = Box(_root, text, x, y, w, h, primary ? new Color(.15f, .40f, .82f) : new Color(.15f, .21f, .31f));
        var button = bg.gameObject.AddComponent<Button>();
        button.targetGraphic = bg;
        button.onClick.AddListener(() => click());
        var label = Label(bg.transform, text, 0, 0, w, h, 18);
        label.alignment = TextAnchor.MiddleCenter;
        var colors = button.colors;
        colors.disabledColor = new Color(.45f, .45f, .45f, .8f);
        button.colors = colors;
        return button;
    }

    void BuildConversationScroll()
    {
        var viewport = Box(_root, "Conversation viewport", 452, 474, 942, 238, _fieldColor);
        viewport.gameObject.AddComponent<RectMask2D>();
        _scroll = viewport.gameObject.AddComponent<ScrollRect>();
        _scroll.viewport = viewport.rectTransform;
        _scroll.horizontal = false;
        _scroll.movementType = ScrollRect.MovementType.Clamped;
        _conversation = Label(viewport.transform, "", 12, 8, 918, 222, 22);
        _conversation.verticalOverflow = VerticalWrapMode.Overflow;
        _conversation.lineSpacing = 1.15f;
        var fit = _conversation.gameObject.AddComponent<ContentSizeFitter>();
        fit.verticalFit = ContentSizeFitter.FitMode.PreferredSize;
        _scroll.content = _conversation.rectTransform;
    }
}
