// OperatorTheme.cs
// 운영자 화면(OperatorHUD)의 겉모습을 "슬레이트 + 블루/골드" 톤으로 입힌다.
//
// 씬 파일을 고치지 않는다. 씬이 열리면 이름으로 오브젝트를 찾아 색·스프라이트·
// 버튼 상태색을 덮어쓴다. 레이아웃(크기·위치·간격)은 건드리지 않는다.
// 되돌리려면 이 파일을 지우거나 Enabled 를 false 로 두면 된다.
//
// 스프라이트는 코드로 그린다(둥근 사각형·글로우·그라데이션). 따로 이미지 에셋이 없다.

using System.Collections.Generic;
using TMPro;
using UnityEngine;
using UnityEngine.UI;

public static class OperatorTheme
{
    /// <summary>false 로 두면 운영자 화면이 예전 모양 그대로 나온다.</summary>
    public static bool Enabled = true;

    // ── 팔레트 ───────────────────────────────────────────────────────────
    static readonly Color BgTop      = Hex("1E232B");
    static readonly Color BgBottom   = Hex("12151A");
    static readonly Color Slate      = Hex("3A4250");   // 카드 바탕(회색조)
    static readonly Color SlateDeep  = Hex("2A3039");   // 안쪽으로 파인 면(입력·기록 창)
    static readonly Color Inset      = Hex("161A20");
    static readonly Color Blue       = Hex("4C74A8");   // 파란 카드·기본 버튼
    static readonly Color BlueHover  = Hex("6B9BE0");
    static readonly Color BluePress  = Hex("365683");
    static readonly Color GlowBlue   = Hex("7FB2FF");
    static readonly Color Gold       = Hex("E9C46A");
    static readonly Color Disabled   = Hex("5A5D64");
    static readonly Color TextMain   = Hex("EAEDF2");
    static readonly Color TextMuted  = Hex("A3AAB7");

    // ── 토큰 (이미지의 Tokens 사이드바) ──────────────────────────────────
    const int RadiusS = 8, RadiusM = 12, RadiusL = 16;
    const int GlowSize = 18;     // 글로우가 카드 밖으로 번지는 폭
    const int ShadowSize = 22;

    static readonly Dictionary<string, Sprite> Cache = new Dictionary<string, Sprite>();

    /// <summary>
    /// 에디터의 Bake 도구가 건다. 코드로 그린 텍스처를 받아 PNG 에셋으로 저장하고,
    /// 그 에셋의 스프라이트를 돌려준다. null 이면 실행 중에만 쓰는 임시 스프라이트를 만든다.
    /// 인자: 이름, 텍스처, 9-slice 테두리(px).
    /// </summary>
    public static System.Func<string, Texture2D, int, Sprite> SpriteSink;

    public static void ClearCache() => Cache.Clear();

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void AutoApply()
    {
        if (!Enabled) return;
        var hud = Object.FindObjectOfType<OperatorHUD>(true);
        if (hud == null) return;
        // 이미 씬에 구워 넣었으면(Bake) 다시 입히지 않는다.
        if (hud.transform.Find("Backdrop/StatusCard/_Face") != null) return;
        Apply(hud.transform);
    }

    public static void Apply(Transform root)
    {
        // 바탕
        var backdrop = Img(root, "Backdrop");
        if (backdrop != null)
        {
            backdrop.sprite = VerticalGradient(BgTop, BgBottom);
            backdrop.type = Image.Type.Simple;
            backdrop.color = Color.white;
        }

        // 카드들
        Card(Img(root, "Backdrop/VRPane"), Slate, GlowBlue, 0.22f, RadiusL);
        Card(Img(root, "Backdrop/StatusCard"), Blue, GlowBlue, 0.38f, RadiusL);
        Card(Img(root, "Backdrop/SettingsCard"), Slate, GlowBlue, 0.14f, RadiusL);

        Text(root, "Backdrop/VRPane/VRViewCaption", TextMuted);

        // 상태 카드
        string sc = "Backdrop/StatusCard/";
        Text(root, sc + "Title", TextMain);
        Text(root, sc + "InfoText", TextMain);
        Text(root, sc + "LogTitle", TextMain);
        var dot = Img(root, sc + "StateRow/StatusDot");
        if (dot != null) { dot.sprite = Circle(); dot.type = Image.Type.Simple; }   // 색은 DialogueVoiceUI 가 정한다
        Panel(Img(root, sc + "ConversationLog"), Inset, RadiusM);
        Scrollbar(root.Find(sc + "ConversationLog/Scrollbar"));

        // 설정 카드
        string st = "Backdrop/SettingsCard/";
        Text(root, st + "Title", TextMain);
        Text(root, st + "MicLabel", TextMuted);
        Text(root, st + "MicMeterLabel", TextMuted);
        Text(root, st + "MicHint", TextMuted);
        Text(root, st + "MoveLabel", TextMuted);
        Text(root, st + "SubtitleHint", TextMuted);

        Dropdown(root.Find(st + "MicDropdown"));
        LevelBar(root.Find(st + "LevelBar"));

        Btn(root, st + "StartButton", Blue, BlueHover, BluePress, RadiusL);
        Btn(root, st + "ResetButton", Slate, Hex("56607A"), Hex("2E3541"), RadiusL);
        foreach (var n in new[] { "Forward", "Left", "Recenter", "Right", "Backward" })
            Btn(root, st + "MovePad/" + n, SlateDeep, Hex("4A5568"), Hex("232830"), RadiusS);
        foreach (var n in new[] { "Up", "Down" })
            Btn(root, st + "HeightPad/" + n, SlateDeep, Hex("4A5568"), Hex("232830"), RadiusS);

        var box = Img(root, st + "SubtitleToggle/Box");
        if (box != null) { box.sprite = Face(RadiusS - 2); box.type = Image.Type.Sliced; box.color = SlateDeep; }
        var check = Img(root, st + "SubtitleToggle/Box/Check");
        if (check != null) check.color = Gold;
        Text(root, st + "SubtitleToggle/Label", TextMain);
    }

    // ── 부품 ─────────────────────────────────────────────────────────────

    /// <summary>
    /// 카드. 부모 Image 는 투명하게 두고, 맨 뒤에 글로우·면을 자식으로 깐다.
    /// (자식은 부모 위에 그려지므로 글로우를 부모 Image 로 둘 수 없다.)
    /// </summary>
    static void Card(Image rootImg, Color face, Color glow, float glowAlpha, int radius)
    {
        if (rootImg == null) return;
        var rt = (RectTransform)rootImg.transform;
        rootImg.color = new Color(1, 1, 1, 0);

        var shadow = Layer(rt, "_Shadow", 0, ShadowSize);
        shadow.sprite = Soft(radius, ShadowSize);
        shadow.type = Image.Type.Sliced;
        shadow.color = new Color(0, 0, 0, 0.55f);
        shadow.rectTransform.offsetMin += new Vector2(0, -8);
        shadow.rectTransform.offsetMax += new Vector2(0, -8);

        var halo = Layer(rt, "_Glow", 1, GlowSize);
        halo.sprite = Soft(radius, GlowSize);
        halo.type = Image.Type.Sliced;
        halo.color = new Color(glow.r, glow.g, glow.b, glowAlpha);

        var body = Layer(rt, "_Face", 2, 0);
        body.sprite = Face(radius);
        body.type = Image.Type.Sliced;
        body.color = face;
    }

    /// <summary>자식 Image 하나를 부모 크기에 맞춰(여백 포함) 깐다. 이미 있으면 재사용.</summary>
    static Image Layer(RectTransform parent, string name, int sibling, float grow)
    {
        var existing = parent.Find(name);
        GameObject go;
        if (existing != null) go = existing.gameObject;
        else
        {
            go = new GameObject(name, typeof(RectTransform), typeof(CanvasRenderer), typeof(Image), typeof(LayoutElement));
        }
        var rt = (RectTransform)go.transform;
        rt.SetParent(parent, false);
        rt.anchorMin = Vector2.zero;
        rt.anchorMax = Vector2.one;
        rt.offsetMin = new Vector2(-grow, -grow);
        rt.offsetMax = new Vector2(grow, grow);
        rt.SetSiblingIndex(sibling);
        go.GetComponent<LayoutElement>().ignoreLayout = true;
        var img = go.GetComponent<Image>();
        img.raycastTarget = false;
        return img;
    }

    /// <summary>안쪽으로 파인 면(기록 창 등).</summary>
    static void Panel(Image img, Color color, int radius)
    {
        if (img == null) return;
        img.sprite = Face(radius);
        img.type = Image.Type.Sliced;
        img.color = color;
    }

    static void Btn(Transform root, string path, Color normal, Color hover, Color pressed, int radius)
    {
        var t = root.Find(path);
        if (t == null) return;
        var img = t.GetComponent<Image>();
        var btn = t.GetComponent<Button>();
        if (img == null || btn == null) return;

        img.sprite = Face(radius);
        img.type = Image.Type.Sliced;
        img.color = Color.white;
        btn.targetGraphic = img;
        btn.transition = Selectable.Transition.ColorTint;
        var c = btn.colors;
        c.normalColor = normal;
        c.highlightedColor = hover;
        c.pressedColor = pressed;
        c.selectedColor = normal;
        c.disabledColor = Disabled;
        c.colorMultiplier = 1f;
        c.fadeDuration = 0.08f;
        btn.colors = c;

        var label = t.GetComponentInChildren<TMP_Text>(true);
        if (label != null) label.color = TextMain;
    }

    static void Dropdown(Transform dd)
    {
        if (dd == null) return;
        var img = dd.GetComponent<Image>();
        if (img != null)
        {
            img.sprite = Face(RadiusS);
            img.type = Image.Type.Sliced;
            img.color = Color.white;
        }
        var drop = dd.GetComponent<TMP_Dropdown>();
        if (drop != null)
        {
            var c = drop.colors;
            c.normalColor = SlateDeep;
            c.highlightedColor = Hex("465063");
            c.pressedColor = Hex("232830");
            c.selectedColor = SlateDeep;
            c.disabledColor = Disabled;
            drop.colors = c;
        }
        var cap = dd.Find("Label");
        if (cap != null && cap.TryGetComponent<TMP_Text>(out var capText)) capText.color = TextMain;

        var template = dd.Find("Template");
        if (template == null) return;
        var tImg = template.GetComponent<Image>();
        if (tImg != null)
        {
            tImg.sprite = Face(RadiusS);
            tImg.type = Image.Type.Sliced;
            tImg.color = Slate;
        }
        var item = template.Find("Viewport/Content/Item");
        if (item == null) return;
        var iImg = item.GetComponent<Image>();
        var iTog = item.GetComponent<Toggle>();
        if (iImg != null) iImg.color = Color.white;
        if (iTog != null)
        {
            var c = iTog.colors;
            c.normalColor = new Color(1, 1, 1, 0);
            c.highlightedColor = new Color(Gold.r, Gold.g, Gold.b, 0.28f);
            c.pressedColor = new Color(Gold.r, Gold.g, Gold.b, 0.45f);
            c.selectedColor = new Color(Gold.r, Gold.g, Gold.b, 0.22f);
            c.disabledColor = new Color(1, 1, 1, 0);
            iTog.colors = c;
        }
        var lbl = item.Find("Item Label");
        if (lbl != null && lbl.TryGetComponent<TMP_Text>(out var lt)) lt.color = TextMain;
    }

    static void LevelBar(Transform bar)
    {
        if (bar == null) return;
        Panel(bar.GetComponent<Image>(), Inset, 6);
        var fill = bar.Find("Fill");
        if (fill != null && fill.TryGetComponent<Image>(out var f))
        {
            f.sprite = HorizontalGradient(BlueHover, Gold);
            f.color = Color.white;
        }
        var mark = bar.Find("ThresholdMarker");
        if (mark != null && mark.TryGetComponent<Image>(out var m)) m.color = Gold;
    }

    static void Scrollbar(Transform bar)
    {
        if (bar == null) return;
        Panel(bar.GetComponent<Image>(), new Color(1, 1, 1, 0.06f), 4);
        var handle = bar.Find("Sliding Area/Handle");
        if (handle != null && handle.TryGetComponent<Image>(out var h))
        {
            h.sprite = Face(4);
            h.type = Image.Type.Sliced;
            h.color = new Color(Gold.r, Gold.g, Gold.b, 0.7f);
        }
    }

    static void Text(Transform root, string path, Color color)
    {
        var t = root.Find(path);
        if (t != null && t.TryGetComponent<TMP_Text>(out var tmp)) tmp.color = color;
    }

    static Image Img(Transform root, string path)
    {
        var t = root.Find(path);
        return t != null ? t.GetComponent<Image>() : null;
    }

    // ── 스프라이트 (코드로 그린다) ───────────────────────────────────────

    /// <summary>둥근 면. 위가 밝고 아래가 어두운 회색조 + 가장자리 밝은 테. Image.color 로 물들인다.</summary>
    static Sprite Face(int r)
    {
        string key = "face" + r;
        if (Cache.TryGetValue(key, out var s) && s != null) return s;

        int n = 2 * r + 2;
        var px = new Color32[n * n];
        for (int y = 0; y < n; y++)
        for (int x = 0; x < n; x++)
        {
            float d = RoundedDist(x + 0.5f, y + 0.5f, n, n, r);
            float a = Mathf.Clamp01(0.5f - d);
            float v = Mathf.Lerp(0.78f, 0.92f, y / (float)(n - 1));          // 위가 밝다
            float rim = Mathf.Clamp01(1f - (-d) / 1.6f) * (d < 0.5f ? 1f : 0f);
            float b = Mathf.Lerp(v, 1f, rim * 0.85f);
            px[y * n + x] = new Color(b, b, b, a);
        }
        return Store(key, px, n, n, r);
    }

    /// <summary>번지는 빛·그림자. 가운데는 꽉 차고 바깥으로 부드럽게 사라진다. blur 만큼 밖으로 번진다.</summary>
    static Sprite Soft(int r, int blur)
    {
        string key = "soft" + r + "_" + blur;
        if (Cache.TryGetValue(key, out var s) && s != null) return s;

        int half = r + blur;
        int n = 2 * half + 2;
        var px = new Color32[n * n];
        for (int y = 0; y < n; y++)
        for (int x = 0; x < n; x++)
        {
            // 안쪽 사각형은 blur 만큼 들어간 자리에서 시작한다. 밖은 거리로 감쇠.
            float d = RoundedDist(x + 0.5f, y + 0.5f, n, n, r, blur);
            float a = Mathf.Clamp01(1f - Mathf.Max(d, 0f) / blur);
            a = a * a * (3f - 2f * a);                                       // smoothstep
            px[y * n + x] = new Color(1, 1, 1, a);
        }
        return Store(key, px, n, n, half);
    }

    static Sprite Circle()
    {
        const string key = "circle";
        if (Cache.TryGetValue(key, out var s) && s != null) return s;
        const int n = 64;
        var px = new Color32[n * n];
        for (int y = 0; y < n; y++)
        for (int x = 0; x < n; x++)
        {
            float d = Vector2.Distance(new Vector2(x + 0.5f, y + 0.5f), new Vector2(n / 2f, n / 2f)) - n / 2f;
            float a = Mathf.Clamp01(0.5f - d);
            px[y * n + x] = new Color(1, 1, 1, a);
        }
        return Store(key, px, n, n, 0);
    }

    static Sprite VerticalGradient(Color top, Color bottom)
    {
        string key = "vg_" + ColorUtility.ToHtmlStringRGB(top) + "_" + ColorUtility.ToHtmlStringRGB(bottom);
        if (Cache.TryGetValue(key, out var s) && s != null) return s;
        const int n = 64;
        var px = new Color32[n];
        for (int y = 0; y < n; y++) px[y] = Color.Lerp(bottom, top, y / (float)(n - 1));
        return Store(key, px, 1, n, 0);
    }

    static Sprite HorizontalGradient(Color left, Color right)
    {
        string key = "hg_" + ColorUtility.ToHtmlStringRGB(left) + "_" + ColorUtility.ToHtmlStringRGB(right);
        if (Cache.TryGetValue(key, out var s) && s != null) return s;
        const int n = 64;
        var px = new Color32[n];
        for (int x = 0; x < n; x++)
        {
            float t = x / (float)(n - 1);
            // 앞 60% 는 파랑, 끝에서 금색으로 넘어간다 (이미지의 진행 표시줄처럼).
            px[x] = Color.Lerp(left, right, Mathf.SmoothStep(0.55f, 1f, t));
        }
        return Store(key, px, n, 1, 0);
    }

    /// <summary>
    /// 둥근 사각형까지의 부호 있는 거리(안쪽이 음수). inset 만큼 안쪽에서 시작하는 사각형이다.
    /// </summary>
    static float RoundedDist(float x, float y, int w, int h, float r, float inset = 0f)
    {
        float hx = w * 0.5f - inset, hy = h * 0.5f - inset;
        float qx = Mathf.Abs(x - w * 0.5f) - (hx - r);
        float qy = Mathf.Abs(y - h * 0.5f) - (hy - r);
        float outside = new Vector2(Mathf.Max(qx, 0f), Mathf.Max(qy, 0f)).magnitude;
        float inside = Mathf.Min(Mathf.Max(qx, qy), 0f);
        return outside + inside - r;
    }

    static Sprite Store(string key, Color32[] px, int w, int h, int border)
    {
        var tex = new Texture2D(w, h, TextureFormat.RGBA32, false)
        {
            wrapMode = TextureWrapMode.Clamp,
            filterMode = FilterMode.Bilinear,
            name = "OperatorTheme_" + key,
            hideFlags = HideFlags.HideAndDontSave,
        };
        tex.SetPixels32(px);
        tex.Apply(false, false);

        if (SpriteSink != null)
        {
            var saved = SpriteSink(key, tex, border);
            Object.DestroyImmediate(tex);
            Cache[key] = saved;
            return saved;
        }

        var sprite = Sprite.Create(tex, new Rect(0, 0, w, h), new Vector2(0.5f, 0.5f), 100f, 0,
                                   SpriteMeshType.FullRect, new Vector4(border, border, border, border));
        sprite.hideFlags = HideFlags.HideAndDontSave;
        Cache[key] = sprite;
        return sprite;
    }

    static Color Hex(string rgb)
    {
        ColorUtility.TryParseHtmlString("#" + rgb, out var c);
        return c;
    }
}
