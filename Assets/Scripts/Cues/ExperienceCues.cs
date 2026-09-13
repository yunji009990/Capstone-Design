// ExperienceCues.cs
// 헤드셋 안에서 체험 상태를 알리고, 시작과 끝을 부드럽게 잇는다.
//
// "듣고 있습니다 · 생각 중 · 말하는 중"은 DialogueVoiceUI 가 운영자 화면에만 적는다. 헤드셋을
// 쓴 사람은 그 화면을 못 본다. 말을 걸고 몇 초 동안 아무 반응이 없으면 못 들은 줄 알고
// 같은 말을 되풀이하게 된다. 여기서는 그 순간들을 시야 가장자리의 빛, 인물 얼굴의 빛,
// 손의 진동으로 알린다. 추모 콘텐츠라 세게 하면 안 된다 — 기본값은 눈에 겨우 띄는 정도고,
// 세기는 세 단계 중에서 고른다.
//
//   발화 전달  — 말이 서버로 간 순간. 가장자리가 한 번 밝아졌다 잦아들고, 손이 짧게 떨린다.
//   응답 준비  — 서버가 답을 만드는 동안. 가장자리가 숨 쉬듯 느리게 오르내린다.
//   인물 발화  — 인물이 말을 시작하면 얼굴 쪽에 따뜻한 빛이 켜지고, 끝나면 꺼진다.
//   시작·종료  — 눈을 감았다 뜨듯 어두워졌다가 밝아진다. 장면이 급하게 바뀌면 VR 에서는 어지럽다.
//
// 연출은 전부 Feel(MMF_Player)로 만든다. 인스펙터의 플레이어 칸이 비어 있으면 실행할 때
// 기본 연출을 조립한다. 메뉴 「다시봄/Feel 연출을 씬에 굽기」로 씬에 박아 두면 그때부터는
// 인스펙터에서 곡선·시간·세기를 고칠 수 있다 — 코드를 안 열고 연출을 맞추려고 Feel 을 쓴다.

using System;
using MoreMountains.Feedbacks;
using MoreMountains.Tools;
using UnityEngine;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class ExperienceCues : MonoBehaviour
{
    public enum Strength
    {
        [InspectorName("최소 — 눈에 겨우 띈다")] Minimal,
        [InspectorName("표준")] Standard,
        [InspectorName("강조 — 시연·검증용")] Emphasized,
    }

    [Header("연결 (비워두면 씬에서 찾는다)")]
    public DialogueVoiceClient voice;
    public ExperienceControl experience;
    public PersonaSpawner spawner;
    public DialogueVoiceUI operatorUI;

    [Header("세기")]
    [Tooltip("발화 전달·응답 준비·인물 발화 신호의 세기. 시작·종료 페이드와 운영자 화면에는 영향 없다.")]
    public Strength strength = Strength.Standard;

    [Header("색")]
    [Tooltip("시야 가장자리 빛. 촛불 정도의 따뜻한 색.")]
    public Color edgeColor = new Color(1f, 0.87f, 0.68f);
    public Color fadeColor = Color.black;
    [Tooltip("인물이 말할 때 얼굴에 드는 빛.")]
    public Color personaLightColor = new Color(1f, 0.85f, 0.65f);
    [Tooltip("표준 세기일 때 인물 빛의 밝기.")]
    public float personaLightIntensity = 0.9f;

    [Header("시간(초)")]
    public float beginDarkenSec = 0.6f;
    public float beginHoldSec = 0.4f;
    public float beginBrightenSec = 1.6f;
    public float finishDarkenSec = 1.2f;
    public float finishHoldSec = 1.0f;
    public float finishBrightenSec = 1.2f;
    public float sentPulseSec = 0.7f;
    public float breathSec = 2.6f;
    public float speakRiseSec = 0.8f;
    public float speakFallSec = 1.2f;

    [Header("시야 덮개")]
    [Tooltip("눈앞 몇 m 에 덮개를 둘지. 카메라 near clip 보다 멀어야 한다.")]
    public float canvasDistance = 0.6f;
    [Tooltip("덮개 한 변의 길이(m). 시야 전체를 덮을 만큼 넉넉하게.")]
    public float canvasWidthMeters = 3f;

    [Header("Feel 플레이어 (비워두면 실행할 때 조립한다)")]
    public MMF_Player beginCue;
    public MMF_Player finishCue;
    public MMF_Player sentCue;
    public MMF_Player waitingCue;
    public MMF_Player speakStartCue;
    public MMF_Player speakEndCue;
    public MMF_Player operatorButtonCue;
    public MMF_Player operatorStatusCue;

    [Header("연출 대상 (비워두면 실행할 때 만든다)")]
    public RectTransform canvasRoot;
    public CanvasGroup fadeGroup;
    public CanvasGroup edgePulseGroup;
    public CanvasGroup edgeBreathGroup;
    public Light personaLight;

    bool _started, _waiting, _speaking, _recording;
    bool _attached;
    Sprite _vignette;

    // ───────────────────────── 생명주기 ─────────────────────────

    void Awake()
    {
        if (voice == null) voice = FindObjectOfType<DialogueVoiceClient>();
        if (experience == null) experience = FindObjectOfType<ExperienceControl>();
        if (spawner == null) spawner = FindObjectOfType<PersonaSpawner>();
        if (operatorUI == null) operatorUI = FindObjectOfType<DialogueVoiceUI>();
    }

    void Start()
    {
        Build(null);
        ApplyVignetteSprite();
        ApplyStrength();

        foreach (var p in AllPlayers())
        {
            if (p == null) continue;
            p.PreInitialization();   // 플레이어의 Awake 는 피드백을 넣기 전에 지나갔다
            p.Initialization();
        }

        if (experience != null && experience.startButton != null && operatorButtonCue != null)
            experience.startButton.onClick.AddListener(() => operatorButtonCue.PlayFeedbacks());

        ResetVisuals();
        TryAttachToHead();
    }

    void Update()
    {
        if (!_attached) TryAttachToHead();
        PollExperience();
        PollVoice();
    }

    void OnDisable()
    {
        foreach (var p in AllPlayers())
            if (p != null && p.IsPlaying) p.StopFeedbacks();
        ResetVisuals();
    }

    // ───────────────────────── 상태 감시 ─────────────────────────

    void PollExperience()
    {
        if (experience == null) return;
        bool started = experience.Started;
        if (started == _started) return;
        _started = started;

        if (started) Play(beginCue);
        else
        {
            // 끝나면 듣지 않으니 숨 쉬는 빛도 같이 걷는다. 인물이 말하던 중이면
            // 발화 종료는 아래 PollVoice 가 알아서 처리한다.
            Stop(waitingCue);
            if (edgeBreathGroup) edgeBreathGroup.alpha = 0f;
            Play(finishCue);
        }
    }

    void PollVoice()
    {
        if (voice == null) return;
        bool recording = voice.isRecording;
        bool waiting = voice.isWaiting;
        bool speaking = voice.IsSpeaking;

        if (waiting && !_waiting)          // 말이 서버로 갔다
        {
            Play(sentCue);
            Play(waitingCue);
        }
        else if (!waiting && _waiting)     // 답이 왔다(거나 실패했다)
        {
            Stop(waitingCue);
            if (edgeBreathGroup) edgeBreathGroup.alpha = 0f;
        }

        if (speaking && !_speaking)
        {
            PlacePersonaLight();
            Stop(speakEndCue);
            Play(speakStartCue);
        }
        else if (!speaking && _speaking)
        {
            Stop(speakStartCue);
            Play(speakEndCue);
        }

        if (recording != _recording || waiting != _waiting || speaking != _speaking)
            Play(operatorStatusCue);

        _recording = recording;
        _waiting = waiting;
        _speaking = speaking;
    }

    static void Play(MMF_Player p) { if (p != null) p.PlayFeedbacks(); }
    static void Stop(MMF_Player p) { if (p != null && p.IsPlaying) p.StopFeedbacks(); }

    /// <summary>세기를 바꾼다. 운영자 화면에서 단추로 바꿀 수 있게 공개해 둔다.</summary>
    public void SetStrength(Strength s)
    {
        strength = s;
        ApplyStrength();
    }

    void ApplyStrength()
    {
        float k = strength == Strength.Minimal ? 0.55f
                : strength == Strength.Emphasized ? 1.6f
                : 1f;
        foreach (var p in new[] { sentCue, waitingCue, speakStartCue })
            if (p != null) p.FeedbacksIntensity = k;
    }

    void ResetVisuals()
    {
        if (fadeGroup) fadeGroup.alpha = 0f;
        if (edgePulseGroup) edgePulseGroup.alpha = 0f;
        if (edgeBreathGroup) edgeBreathGroup.alpha = 0f;
        if (personaLight) { personaLight.intensity = 0f; personaLight.enabled = false; }
        OVRInput.SetControllerVibration(0f, 0f, OVRInput.Controller.Touch);
    }

    // ───────────────────────── 머리에 붙이기 ─────────────────────────

    /// <summary>
    /// 덮개 캔버스를 체험자 눈앞에 건다. OVRCameraRig 는 카메라를 실행 중에 만들기 때문에
    /// 첫 프레임에 없을 수 있어, 붙을 때까지 매 프레임 다시 본다.
    /// </summary>
    void TryAttachToHead()
    {
        if (canvasRoot == null) return;
        Camera cam = Camera.main;
        if (cam == null) return;

        canvasRoot.SetParent(cam.transform, false);
        canvasRoot.localPosition = new Vector3(0f, 0f, canvasDistance);
        canvasRoot.localRotation = Quaternion.identity;
        _attached = true;
    }

    /// <summary>
    /// 인물 빛을 얼굴 앞에 둔다. 인물은 실행 중에 서버에서 받아 스폰되므로 말하기 직전에
    /// 위치를 잰다. 아직 없으면 스폰 지점 위쪽에 둔다.
    /// </summary>
    void PlacePersonaLight()
    {
        if (personaLight == null) return;

        Vector3 head;
        GameObject persona = spawner != null ? spawner.Spawned : null;
        var renderers = persona != null ? persona.GetComponentsInChildren<Renderer>() : null;
        if (renderers != null && renderers.Length > 0)
        {
            Bounds b = renderers[0].bounds;
            foreach (var r in renderers) b.Encapsulate(r.bounds);
            head = b.center + Vector3.up * (b.extents.y * 0.7f);
        }
        else
        {
            Transform anchor = spawner != null && spawner.spawnPoint != null ? spawner.spawnPoint : transform;
            head = anchor.position + Vector3.up * 1.2f;
        }

        // 얼굴 안쪽에 두면 안에서 비추는 꼴이라 까맣게 보인다. 보는 사람 쪽으로 조금 뺀다.
        Camera cam = Camera.main;
        Vector3 toward = cam != null ? (cam.transform.position - head).normalized : Vector3.back;
        personaLight.transform.position = head + toward * 0.45f;
    }

    // ───────────────────────── 조립 ─────────────────────────

    /// <summary>
    /// 비어 있는 대상과 플레이어를 만든다. 실행 중에도, 에디터에서 굽기 메뉴로도 부른다.
    /// created 는 에디터가 Undo 에 등록하려고 넘긴다.
    /// </summary>
    public void Build(Action<UnityEngine.Object> created)
    {
        BuildTargets(created);
        BuildPlayers(created);
    }

    void BuildTargets(Action<UnityEngine.Object> created)
    {
        if (canvasRoot == null)
        {
            var go = NewChild("시야 덮개", created);
            var canvas = go.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.WorldSpace;
            canvas.sortingOrder = 32767;
            canvasRoot = go.GetComponent<RectTransform>();
            float px = canvasWidthMeters * 1000f;
            canvasRoot.sizeDelta = new Vector2(px, px);
            canvasRoot.localScale = Vector3.one * 0.001f;
            canvasRoot.localPosition = new Vector3(0f, 0f, canvasDistance);
        }
        if (fadeGroup == null) fadeGroup = NewImageLayer("페이드", fadeColor, created);
        if (edgeBreathGroup == null) edgeBreathGroup = NewImageLayer("가장자리 숨", edgeColor, created);
        if (edgePulseGroup == null) edgePulseGroup = NewImageLayer("가장자리 한 번", edgeColor, created);

        if (personaLight == null)
        {
            var go = NewChild("인물 빛", created);
            personaLight = go.AddComponent<Light>();
            personaLight.type = LightType.Point;
            personaLight.color = personaLightColor;
            personaLight.range = 1.8f;
            personaLight.intensity = 0f;
            personaLight.shadows = LightShadows.None;
            personaLight.enabled = false;
        }
    }

    CanvasGroup NewImageLayer(string name, Color color, Action<UnityEngine.Object> created)
    {
        var go = new GameObject(name, typeof(RectTransform), typeof(CanvasGroup), typeof(Image));
        created?.Invoke(go);
        var rt = go.GetComponent<RectTransform>();
        rt.SetParent(canvasRoot, false);
        rt.anchorMin = Vector2.zero;
        rt.anchorMax = Vector2.one;
        rt.offsetMin = rt.offsetMax = Vector2.zero;
        var img = go.GetComponent<Image>();
        img.color = color;
        img.raycastTarget = false;
        var group = go.GetComponent<CanvasGroup>();
        group.alpha = 0f;
        group.interactable = false;
        group.blocksRaycasts = false;
        return group;
    }

    /// <summary>가장자리 두 겹에 방사형 그라데이션을 입힌다. 실행할 때마다 만들어서 에셋이 필요 없다.</summary>
    void ApplyVignetteSprite()
    {
        if (_vignette == null) _vignette = MakeVignette(256);
        foreach (var g in new[] { edgePulseGroup, edgeBreathGroup })
        {
            var img = g != null ? g.GetComponent<Image>() : null;
            if (img != null) img.sprite = _vignette;
        }
    }

    static Sprite MakeVignette(int size)
    {
        var tex = new Texture2D(size, size, TextureFormat.RGBA32, false)
        {
            name = "Vignette", wrapMode = TextureWrapMode.Clamp, filterMode = FilterMode.Bilinear,
        };
        var px = new Color32[size * size];
        float half = size * 0.5f;
        for (int y = 0; y < size; y++)
        for (int x = 0; x < size; x++)
        {
            float dx = (x + 0.5f - half) / half, dy = (y + 0.5f - half) / half;
            float d = Mathf.Sqrt(dx * dx + dy * dy);            // 가운데 0, 변 가운데 1, 모서리 1.41
            float a = Mathf.SmoothStep(0f, 1f, Mathf.InverseLerp(0.55f, 1.2f, d));
            px[y * size + x] = new Color32(255, 255, 255, (byte)(a * 255f));
        }
        tex.SetPixels32(px);
        tex.Apply(false, true);
        return Sprite.Create(tex, new Rect(0, 0, size, size), new Vector2(0.5f, 0.5f), 100f);
    }

    void BuildPlayers(Action<UnityEngine.Object> created)
    {
        RectTransform button = experience != null && experience.startButton != null
            ? experience.startButton.transform as RectTransform : null;
        RectTransform dot = operatorUI != null && operatorUI.StatusDot != null
            ? operatorUI.StatusDot.rectTransform : null;

        if (beginCue == null)
        {
            beginCue = NewPlayer("시작 — 눈 감았다 뜨기", created);
            Fade(beginCue, "어두워짐", 0f, 1f, beginDarkenSec);
            Pause(beginCue, "어두운 채로", beginDarkenSec + beginHoldSec);
            Fade(beginCue, "밝아짐", 1f, 0f, beginBrightenSec);
        }
        if (finishCue == null)
        {
            finishCue = NewPlayer("종료 — 눈 감았다 뜨기", created);
            Fade(finishCue, "어두워짐", 0f, 1f, finishDarkenSec);
            Pause(finishCue, "어두운 채로", finishDarkenSec + finishHoldSec);
            Fade(finishCue, "밝아짐", 1f, 0f, finishBrightenSec);
        }
        if (sentCue == null)
        {
            sentCue = NewPlayer("발화 전달", created);
            Edge(sentCue, "가장자리 한 번", edgePulseGroup, 0.35f, sentPulseSec,
                new AnimationCurve(new Keyframe(0, 0), new Keyframe(0.25f, 1f), new Keyframe(1, 0)));
            var tick = (MMF_OVRHaptics)sentCue.AddFeedback(typeof(MMF_OVRHaptics));
            tick.Label = "손 진동";
            tick.Frequency = 0.3f;
            tick.Amplitude = 0.25f;
            tick.Duration = 0.08f;
        }
        if (waitingCue == null)
        {
            waitingCue = NewPlayer("응답 준비 — 숨", created);
            var breath = Edge(waitingCue, "가장자리 숨", edgeBreathGroup, 0.16f, breathSec,
                new AnimationCurve(new Keyframe(0, 0, 0, 0), new Keyframe(0.5f, 1f, 0, 0), new Keyframe(1, 0, 0, 0)));
            breath.Timing.InitialDelay = 0.3f;
            breath.Timing.RepeatForever = true;
            breath.Timing.DelayBetweenRepeats = 0f;
        }
        if (speakStartCue == null)
        {
            speakStartCue = NewPlayer("인물 발화 — 빛 켜짐", created);
            PersonaLight(speakStartCue, "빛 켜짐", personaLightIntensity, speakRiseSec, false);
        }
        if (speakEndCue == null)
        {
            speakEndCue = NewPlayer("인물 발화 끝 — 빛 꺼짐", created);
            PersonaLight(speakEndCue, "빛 꺼짐", 0f, speakFallSec, true);
        }
        if (operatorButtonCue == null)
        {
            operatorButtonCue = NewPlayer("운영자 — 시작 단추", created);
            Punch(operatorButtonCue, "단추 눌림", button, 0.06f, 0.25f);
        }
        if (operatorStatusCue == null)
        {
            operatorStatusCue = NewPlayer("운영자 — 상태 점", created);
            Punch(operatorStatusCue, "상태 바뀜", dot, 0.35f, 0.3f);
        }

        foreach (var p in AllPlayers())
            if (p != null) p.ComputeCachedTotalDuration();
    }

    MMF_Player NewPlayer(string name, Action<UnityEngine.Object> created)
    {
        var go = NewChild(name, created);
        var p = go.AddComponent<MMF_Player>();
        // 대상이 실행 중에 만들어지므로 초기화 시점을 우리가 정한다(Start 에서 부른다).
        p.InitializationMode = MMFeedbacks.InitializationModes.Script;
        return p;
    }

    GameObject NewChild(string name, Action<UnityEngine.Object> created)
    {
        var go = new GameObject(name);
        created?.Invoke(go);
        go.transform.SetParent(transform, false);
        return go;
    }

    MMF_CanvasGroup Fade(MMF_Player p, string label, float from, float to, float sec)
    {
        var f = (MMF_CanvasGroup)p.AddFeedback(typeof(MMF_CanvasGroup));
        f.Label = label;
        f.Mode = MMF_FeedbackBase.Modes.OverTime;
        f.Duration = sec;
        f.RelativeValues = false;
        f.AlphaCurve = new MMTweenType(MMTween.MMTweenCurve.EaseInOutSinusoidal);
        f.RemapZero = from;
        f.RemapOne = to;
        f.TargetCanvasGroup = fadeGroup;
        return f;
    }

    static void Pause(MMF_Player p, string label, float sec)
    {
        var f = (MMF_Pause)p.AddFeedback(typeof(MMF_Pause));
        f.Label = label;
        f.PauseDuration = sec;
    }

    static MMF_CanvasGroup Edge(MMF_Player p, string label, CanvasGroup group, float peak, float sec, AnimationCurve curve)
    {
        var f = (MMF_CanvasGroup)p.AddFeedback(typeof(MMF_CanvasGroup));
        f.Label = label;
        f.Mode = MMF_FeedbackBase.Modes.OverTime;
        f.Duration = sec;
        f.RelativeValues = false;
        f.AlphaCurve = new MMTweenType(curve);
        f.RemapZero = 0f;
        f.RemapOne = peak;
        f.TargetCanvasGroup = group;
        return f;
    }

    void PersonaLight(MMF_Player p, string label, float intensity, float sec, bool turnOffAfter)
    {
        var f = (MMF_Light)p.AddFeedback(typeof(MMF_Light));
        f.Label = label;
        f.BoundLight = personaLight;
        f.Mode = MMF_Light.Modes.ToDestination;
        f.Duration = sec;
        f.StartsOff = true;
        f.DisableOnStop = turnOffAfter;
        f.ModifyColor = false;
        f.ModifyRange = false;
        f.ModifyShadowStrength = false;
        f.IntensityCurve = AnimationCurve.EaseInOut(0f, 0f, 1f, 1f);
        f.ToDestinationIntensity = intensity;
    }

    static void Punch(MMF_Player p, string label, RectTransform target, float amount, float sec)
    {
        if (target == null) return;   // 운영자 화면이 없는 씬이면 빈 플레이어로 둔다
        var f = (MMF_Scale)p.AddFeedback(typeof(MMF_Scale));
        f.Label = label;
        f.Mode = MMF_Scale.Modes.Additive;
        f.AnimateScaleTarget = target;
        f.AnimateScaleDuration = sec;
        f.RemapCurveZero = 0f;
        f.RemapCurveOne = amount;
        var curve = new AnimationCurve(new Keyframe(0, 0), new Keyframe(0.35f, 1f), new Keyframe(1, 0));
        f.AnimateScaleTweenX = new MMTweenType(curve);
        f.AnimateScaleTweenY = new MMTweenType(curve);
        f.AnimateZ = false;
    }

    MMF_Player[] AllPlayers() => new[]
    {
        beginCue, finishCue, sentCue, waitingCue, speakStartCue, speakEndCue, operatorButtonCue, operatorStatusCue,
    };

    // ───────────────────────── 시험용 ─────────────────────────
    // 서버 없이도 연출을 볼 수 있게. 플레이 중 컴포넌트 메뉴(⋮)에서 고른다.

    [ContextMenu("시험: 시작 연출")]      void TestBegin() => Play(beginCue);
    [ContextMenu("시험: 종료 연출")]      void TestFinish() => Play(finishCue);
    [ContextMenu("시험: 발화 전달")]      void TestSent() { Play(sentCue); Play(waitingCue); }
    [ContextMenu("시험: 응답 준비 끝")]   void TestWaitEnd() { Stop(waitingCue); if (edgeBreathGroup) edgeBreathGroup.alpha = 0f; }
    [ContextMenu("시험: 인물 발화 시작")] void TestSpeak() { PlacePersonaLight(); Play(speakStartCue); }
    [ContextMenu("시험: 인물 발화 끝")]   void TestSpeakEnd() { Stop(speakStartCue); Play(speakEndCue); }
}
