// BuildOperatorHUD.cs — 일회용 도구.
//
// 운영자 화면을 손으로 40번 클릭해 만드는 대신 한 번에 짓는다. 배치가 마음에 안 들면
// 값만 고쳐 다시 실행하면 된다. 자리를 잡고 나면 이 파일은 지운다.
//
//   메뉴: 다시봄/운영자 화면 다시 짓기

using TMPro;
using UnityEditor;
using UnityEngine;
using UnityEngine.UI;

public static class BuildOperatorHUD
{
    const float SPLIT = 0.30f;      // 좌우를 가르는 자리. 오른쪽이 2/3 넘게
    const float ROW = 0.34f;        // 오른쪽에서 화면과 설정을 가르는 자리

    static readonly Color Ink = new Color(0.055f, 0.067f, 0.086f, 1f);      // 바탕
    static readonly Color Card = new Color(0.094f, 0.110f, 0.137f, 1f);     // 카드
    static readonly Color Line = new Color(0.176f, 0.196f, 0.235f, 1f);
    static readonly Color Text = new Color(0.902f, 0.918f, 0.941f, 1f);
    static readonly Color Dim = new Color(0.545f, 0.576f, 0.639f, 1f);
    static readonly Color Accent = new Color(0.773f, 0.561f, 0.627f, 1f);   // 로즈 — 웹과 같은 색

    static TMP_FontAsset _font;

    [MenuItem("다시봄/운영자 화면 다시 짓기")]
    public static void Build()
    {
        var hud = GameObject.Find("OperatorHUD");
        if (hud == null) { Debug.LogError("OperatorHUD 를 못 찾았습니다."); return; }

        _font = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>("Assets/Scripts/Raon/Fonts/Malgun SDF.asset");
        if (_font == null) Debug.LogWarning("Malgun SDF 를 못 찾았습니다 — 한글이 안 나올 수 있습니다.");

        var voice = Object.FindObjectOfType<RaonVoiceClient>();
        Undo.RegisterFullObjectHierarchyUndo(hud, "운영자 화면 다시 짓기");

        // 기존 자식은 전부 걷어낸다. 조금씩 고치면 옛 것이 남아 겹친다.
        for (int i = hud.transform.childCount - 1; i >= 0; i--)
            Undo.DestroyObjectImmediate(hud.transform.GetChild(i).gameObject);

        var scaler = hud.GetComponent<CanvasScaler>();
        if (scaler) { scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
                      scaler.referenceResolution = new Vector2(1920, 1080);
                      scaler.matchWidthOrHeight = 0.5f; }

        // 바탕 — 게임 화면이 뒤로 비치면 무엇이 UI 인지 알 수 없다
        var back = Panel(hud.transform, "Backdrop", Ink, 0, 0, 1, 1, 0);

        // AspectRatioFitter 는 앵커를 무시하고 "부모"에 맞춘다. 화면 전체를 부모로 두면
        // 화면 전체를 채워 버리므로, 놓일 자리만큼의 빈 칸으로 한 번 감싼다.
        var pane = Panel(back, "VRPane", new Color(0, 0, 0, 0), SPLIT, ROW, 1, 1, 10);

        // RawImage 로 바로 만든다. Image 를 지우고 갈아 끼우면 참조가 안 붙었다.
        var vrGo = new GameObject("VRView", typeof(RectTransform), typeof(CanvasRenderer), typeof(RawImage));
        vrGo.transform.SetParent(pane, false);
        var vrView = (RectTransform)vrGo.transform;
        vrView.anchorMin = Vector2.zero; vrView.anchorMax = Vector2.one;
        vrView.offsetMin = Vector2.zero; vrView.offsetMax = Vector2.zero;
        var raw = vrGo.GetComponent<RawImage>();
        raw.color = Color.white;
        Label(pane, "VRViewCaption", "체험자가 보는 화면", 20, Dim,
              TextAlignmentOptions.BottomRight, 0, 0, 1, 0.06f);

        var status = Panel(back, "StatusCard", Card, 0, 0, SPLIT, 1, 10);
        var setting = Panel(back, "SettingsCard", Card, SPLIT, 0, 1, ROW, 10);

        var info = BuildStatus(status, voice);
        BuildSettings(setting, voice);

        // 자식을 새로 지었으니 다시 이어 준다. 한 번에 넣어야 서로 덮지 않는다.
        var hudComp = hud.GetComponent<OperatorHUD>();
        if (hudComp != null)
        {
            var so = new SerializedObject(hudComp);
            so.FindProperty("vrView").objectReferenceValue = raw;
            so.FindProperty("infoText").objectReferenceValue = info;
            so.ApplyModifiedPropertiesWithoutUndo();
        }

        EditorUtility.SetDirty(hud);
        Debug.Log("[운영자 화면] 다 지었습니다.");
    }

    // ── 왼쪽 위 · 현재 상태 ───────────────────────────────────────
    static TMP_Text BuildStatus(RectTransform root, RaonVoiceClient voice)
    {
        Label(root, "Title", "현재 상태", 28, Text, TextAlignmentOptions.TopLeft,
              0, 0.955f, 1, 1, 20);

        // 한 줄 상태. 색깔 점이 제일 먼저 눈에 들어온다.
        var dotRow = Panel(root, "StateRow", new Color(0, 0, 0, 0), 0, 0.905f, 1, 0.95f, 0);
        var dot = Panel(dotRow, "StatusDot", Dim, 0, 0.5f, 0, 0.5f, 0);
        dot.anchoredPosition = new Vector2(28, 0);
        dot.sizeDelta = new Vector2(14, 14);
        var stateLabel = Label(dotRow, "StatusLabel", "…", 20, Text,
                               TextAlignmentOptions.Left, 0, 0, 1, 1, 50);

        // 서버·인물 요약. 대화는 아래 기록 창이 맡으므로 짧게 둔다.
        var info = Label(root, "InfoText", "", 20, Text, TextAlignmentOptions.TopLeft,
                         0, 0.62f, 1, 0.895f, 20);

        // 대화 기록 — 마지막 한 마디만 보이면 흐름을 못 따라간다
        Label(root, "LogTitle", "대화 기록", 20, Text, TextAlignmentOptions.Left,
              0, 0.565f, 1, 0.615f, 20);
        var logBody = ScrollBox(root, "ConversationLog", 0, 0.02f, 1, 0.56f);

        var ui = root.GetComponentInParent<RaonVoiceUI>();
        Wire(ui, "client", voice);
        Wire(ui, "statusDot", dot.GetComponent<Image>());
        Wire(ui, "statusLabel", stateLabel);
        Wire(ui, "messageLabel", null);      // 상태 줄과 겹친다
        Wire(ui, "heardLabel", null);        // 대화는 기록 창이 보여준다
        Wire(ui, "answerLabel", null);
        Wire(ui, "healthButton", null);      // 상태가 실시간이라 눌러 볼 이유가 없다

        // 기록 창을 굴리는 부품
        var owner = root.GetComponentInParent<OperatorHUD>().gameObject;
        var log = owner.GetComponent<ConversationLog>() ?? owner.AddComponent<ConversationLog>();
        var so = new SerializedObject(log);
        so.FindProperty("voice").objectReferenceValue = voice;
        so.FindProperty("body").objectReferenceValue = logBody.body;
        so.FindProperty("scroll").objectReferenceValue = logBody.scroll;
        so.ApplyModifiedPropertiesWithoutUndo();
        return info;
    }

    // ── 오른쪽 아래 · 설정 ────────────────────────────────────────
    // 넓고 낮은 칸이라 세로로 쌓지 않고 세 줄기로 나눈다.
    static void BuildSettings(RectTransform root, RaonVoiceClient voice)
    {
        Label(root, "Title", "설정", 26, Text, TextAlignmentOptions.TopLeft,
              0, 0.80f, 0.3f, 1, 20);

        // ① 마이크 고르기 — 고르고 나면 잘 안 건드리니 작게 둔다
        Label(root, "MicLabel", "마이크", 17, Dim, TextAlignmentOptions.Left,
              0.02f, 0.66f, 0.30f, 0.80f, 12);
        var drop = Dropdown(root, "MicDropdown", 0.02f, 0.50f, 0.30f, 0.645f);

        // 마이크가 살아 있는지는 말해 보면 안다. 막대가 움직이면 들어오고 있는 것이다.
        Label(root, "MicMeterLabel", "입력 세기", 15, Dim, TextAlignmentOptions.Left,
              0.02f, 0.38f, 0.30f, 0.47f, 12);
        var bar = Panel(root, "LevelBar", Line, 0.02f, 0.27f, 0.30f, 0.36f, 0);
        bar.offsetMin = new Vector2(12, 0); bar.offsetMax = new Vector2(-12, 0);
        var fill = Panel(bar, "Fill", Accent, 0, 0, 1, 1, 0);
        var fillImg = fill.GetComponent<Image>();
        fillImg.type = Image.Type.Filled;
        fillImg.fillMethod = Image.FillMethod.Horizontal;
        fillImg.fillAmount = 0f;
        // 이 선을 넘어야 말로 인식된다
        var marker = Panel(bar, "ThresholdMarker", Text, 0.1f, 0, 0.1f, 1, 0);
        marker.sizeDelta = new Vector2(2, 0);

        // ② 체험 조작
        var start = Button(root, "StartButton", "체험 시작", Accent, 0.34f, 0.55f, 0.58f, 0.72f);
        var reset = Button(root, "ResetButton", "대화 초기화", Line, 0.34f, 0.33f, 0.58f, 0.50f);

        // ③ 자리 옮기기 — 가운데가 처음 자리로 되돌리기다
        Label(root, "MoveLabel", "체험자 자리 옮기기", 18, Dim, TextAlignmentOptions.Left,
              0.63f, 0.80f, 1, 0.98f, 12);

        var owner = root.GetComponentInParent<OperatorHUD>().gameObject;
        var move = owner.GetComponent<VRMoveControl>() ?? owner.AddComponent<VRMoveControl>();

        var pad = Panel(root, "MovePad", new Color(0, 0, 0, 0), 0.63f, 0.10f, 0.83f, 0.76f, 0);
        PadButton(pad, "Forward",  "앞",   0.34f, 0.68f, 0.66f, 1.00f, move.MoveForward);
        PadButton(pad, "Left",     "좌",   0.00f, 0.34f, 0.32f, 0.66f, move.MoveLeft);
        PadButton(pad, "Recenter", "처음", 0.34f, 0.34f, 0.66f, 0.66f, move.Recenter);
        PadButton(pad, "Right",    "우",   0.68f, 0.34f, 1.00f, 0.66f, move.MoveRight);
        PadButton(pad, "Backward", "뒤",   0.34f, 0.00f, 0.66f, 0.32f, move.MoveBackward);

        var col = Panel(root, "HeightPad", new Color(0, 0, 0, 0), 0.855f, 0.22f, 0.96f, 0.76f, 0);
        PadButton(col, "Up",   "위로",   0, 0.53f, 1, 1.00f, move.MoveUp);
        PadButton(col, "Down", "아래로", 0, 0.00f, 1, 0.47f, move.MoveDown);

        var ui = root.GetComponentInParent<RaonVoiceUI>();
        Wire(ui, "micDropdown", drop);
        Wire(ui, "levelFill", fillImg);
        Wire(ui, "thresholdMarker", marker);
        Wire(ui, "resetButton", reset.button);
        Wire(ui, "talkButton", null);        // 자동 감지라 누를 일이 없다
        Wire(ui, "talkButtonLabel", null);

        // 체험 시작 — 이 프로젝트에 원래 "게임 시작" 코드가 없어서 새로 만들었다
        var exp = owner.GetComponent<ExperienceControl>() ?? owner.AddComponent<ExperienceControl>();
        var so = new SerializedObject(exp);
        so.FindProperty("voice").objectReferenceValue = voice;
        so.FindProperty("log").objectReferenceValue = owner.GetComponent<ConversationLog>();
        so.FindProperty("startButton").objectReferenceValue = start.button;
        so.FindProperty("startLabel").objectReferenceValue = start.label;
        so.ApplyModifiedPropertiesWithoutUndo();
    }

    // ── 만들기 도구 ───────────────────────────────────────────────
    static RectTransform Panel(Transform parent, string name, Color c,
                               float ax, float ay, float bx, float by, float pad)
    {
        var go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(Image));
        go.transform.SetParent(parent, false);
        var rt = (RectTransform)go.transform;
        rt.anchorMin = new Vector2(ax, ay);
        rt.anchorMax = new Vector2(bx, by);
        rt.offsetMin = new Vector2(pad, pad);
        rt.offsetMax = new Vector2(-pad, -pad);
        go.GetComponent<Image>().color = c;
        return rt;
    }

    static TMP_Text Label(Transform parent, string name, string text, float size, Color c,
                          TextAlignmentOptions align, float ax, float ay, float bx, float by,
                          float pad = 0)
    {
        var go = new GameObject(name, typeof(RectTransform));
        go.transform.SetParent(parent, false);
        var t = go.AddComponent<TextMeshProUGUI>();
        if (_font) t.font = _font;
        t.text = text; t.fontSize = size; t.color = c; t.alignment = align;
        t.richText = true; t.enableWordWrapping = true;
        var rt = (RectTransform)go.transform;
        rt.anchorMin = new Vector2(ax, ay);
        rt.anchorMax = new Vector2(bx, by);
        rt.offsetMin = new Vector2(pad, pad);
        rt.offsetMax = new Vector2(-pad, -pad);
        return t;
    }

    struct Box { public TMP_Text body; public ScrollRect scroll; }

    /// <summary>글이 쌓이면 스크롤로 되짚어 볼 수 있는 칸.</summary>
    static Box ScrollBox(Transform parent, string name, float ax, float ay, float bx, float by)
    {
        var clear = new Color(0, 0, 0, 0);
        var root = Panel(parent, name, new Color(0, 0, 0, 0.22f), ax, ay, bx, by, 0);
        root.offsetMin = new Vector2(20, 0); root.offsetMax = new Vector2(-20, 0);

        var sr = root.gameObject.AddComponent<ScrollRect>();
        sr.horizontal = false;
        sr.movementType = ScrollRect.MovementType.Clamped;
        sr.scrollSensitivity = 30f;

        var vp = Panel(root, "Viewport", clear, 0, 0, 1, 1, 0);
        vp.offsetMax = new Vector2(-14, -6);     // 오른쪽은 스크롤바 자리
        vp.offsetMin = new Vector2(0, 6);
        vp.gameObject.AddComponent<RectMask2D>();

        // 글 자체가 내용이 된다. 높이는 글 길이가 정한다.
        var contentGo = new GameObject("Content", typeof(RectTransform));
        contentGo.transform.SetParent(vp, false);
        var content = (RectTransform)contentGo.transform;
        content.anchorMin = new Vector2(0, 1); content.anchorMax = new Vector2(1, 1);
        content.pivot = new Vector2(0.5f, 1f);
        content.offsetMin = new Vector2(12, 0); content.offsetMax = new Vector2(-12, 0);
        var body = contentGo.AddComponent<TextMeshProUGUI>();
        if (_font) body.font = _font;
        body.fontSize = 18; body.color = Text;
        body.alignment = TextAlignmentOptions.TopLeft;
        body.richText = true; body.enableWordWrapping = true;
        var fit = contentGo.AddComponent<ContentSizeFitter>();
        fit.verticalFit = ContentSizeFitter.FitMode.PreferredSize;

        // 눈에 보이는 스크롤바. 얼마나 긴지도 같이 알려 준다.
        var sbRt = Panel(root, "Scrollbar", new Color(1, 1, 1, 0.06f), 1, 0, 1, 1, 0);
        sbRt.pivot = new Vector2(1, 0.5f);
        sbRt.sizeDelta = new Vector2(10, 0);
        sbRt.anchoredPosition = Vector2.zero;
        var area = Panel(sbRt, "Sliding Area", clear, 0, 0, 1, 1, 0);
        var handle = Panel(area, "Handle", Dim, 0, 0, 1, 1, 0);
        var sb = sbRt.gameObject.AddComponent<Scrollbar>();
        sb.direction = Scrollbar.Direction.BottomToTop;
        sb.handleRect = handle;
        sb.targetGraphic = handle.GetComponent<Image>();

        sr.content = content; sr.viewport = vp; sr.verticalScrollbar = sb;
        sr.verticalScrollbarVisibility = ScrollRect.ScrollbarVisibility.AutoHideAndExpandViewport;
        return new Box { body = body, scroll = sr };
    }

    struct Btn { public Button button; public TMP_Text label; }

    static Btn Button(Transform parent, string name, string text, Color c,
                      float ax, float ay, float bx, float by)
    {
        var rt = Panel(parent, name, c, ax, ay, bx, by, 0);
        var b = rt.gameObject.AddComponent<Button>();
        b.targetGraphic = rt.GetComponent<Image>();
        var l = Label(rt, "Label", text, 17, Color.white, TextAlignmentOptions.Center, 0, 0, 1, 1);
        return new Btn { button = b, label = l };
    }

    static void PadButton(Transform parent, string name, string text,
                          float ax, float ay, float bx, float by, UnityEngine.Events.UnityAction act)
    {
        var b = Button(parent, name, text, Line, ax, ay, bx, by);
        b.label.fontSize = 16;
        UnityEditor.Events.UnityEventTools.AddPersistentListener(b.button.onClick, act);
    }

    static TMP_Dropdown Dropdown(Transform parent, string name,
                                 float ax, float ay, float bx, float by)
    {
        var rt = Panel(parent, name, Line, ax, ay, bx, by, 0);
        rt.offsetMin = new Vector2(20, 0); rt.offsetMax = new Vector2(-20, 0);
        var d = rt.gameObject.AddComponent<TMP_Dropdown>();
        d.targetGraphic = rt.GetComponent<Image>();

        var lbl = Label(rt, "Label", "", 16, Text, TextAlignmentOptions.Left, 0, 0, 1, 1, 10);
        d.captionText = lbl;

        // 펼쳐지는 목록
        var tmpl = Panel(rt, "Template", Card, 0, 0, 1, 0, 0);
        tmpl.pivot = new Vector2(0.5f, 1f);
        tmpl.anchoredPosition = Vector2.zero;
        tmpl.sizeDelta = new Vector2(0, 160);
        var vp = Panel(tmpl, "Viewport", new Color(0, 0, 0, 0), 0, 0, 1, 1, 0);
        vp.gameObject.AddComponent<Mask>().showMaskGraphic = false;
        var content = Panel(vp, "Content", new Color(0, 0, 0, 0), 0, 1, 1, 1, 0);
        content.pivot = new Vector2(0.5f, 1f);
        content.sizeDelta = new Vector2(0, 40);
        var item = Panel(content, "Item", new Color(0, 0, 0, 0), 0, 0.5f, 1, 0.5f, 0);
        item.sizeDelta = new Vector2(0, 40);
        var itemLbl = Label(item, "Item Label", "", 16, Text, TextAlignmentOptions.Left, 0, 0, 1, 1, 10);
        var toggle = item.gameObject.AddComponent<Toggle>();
        toggle.targetGraphic = item.GetComponent<Image>();
        var sr = tmpl.gameObject.AddComponent<ScrollRect>();
        sr.content = content; sr.viewport = vp; sr.horizontal = false;
        sr.movementType = ScrollRect.MovementType.Clamped;
        d.template = tmpl; d.itemText = itemLbl;
        tmpl.gameObject.SetActive(false);
        return d;
    }

    /// <summary>[SerializeField] private 라 코드로는 못 넣는다. 직렬화 창구로 넣는다.</summary>
    static void Wire(Object target, string field, Object value)
    {
        if (target == null) return;
        var so = new SerializedObject(target);
        var p = so.FindProperty(field);
        if (p == null) { Debug.LogWarning($"[운영자 화면] 없는 항목: {field}"); return; }
        p.objectReferenceValue = value;
        so.ApplyModifiedPropertiesWithoutUndo();
    }
}
