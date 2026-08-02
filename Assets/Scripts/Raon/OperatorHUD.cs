// OperatorHUD.cs
// "다시, 봄" — 운영자용 데스크톱 화면.
//
// 체험자는 헤드셋을 쓰고 있어서 무엇을 보고 있는지, 대화가 되고 있는지 옆에서 알 수
// 없다. 이 화면이 그것을 대신 보여 준다.
//
//   ┌───────────┬──────────────────────────┐
//   │  정보     │                          │
//   │  (세션·   │   체험자가 보는 화면      │
//   │   상태)   │        (2/3)             │
//   ├───────────┴──────────────────────────┤
//   │  대화 UI (구석)                       │
//   └──────────────────────────────────────┘
//
// 체험자의 시야를 그대로 띄우려고 카메라를 하나 더 만들어 같은 자리에서 찍는다.
// VR 카메라 자체에 targetTexture 를 걸면 헤드셋에 아무것도 안 보이게 된다.

using System.Text;
using TMPro;
using UnityEngine;
using UnityEngine.UI;
using UnityEngine.XR;

[DisallowMultipleComponent]
public class OperatorHUD : MonoBehaviour
{
    [Header("연결")]
    [Tooltip("체험자가 보는 화면이 나올 자리.")]
    public RawImage vrView;

    [Tooltip("정보가 나올 자리.")]
    public TMP_Text infoText;

    [Tooltip("비워두면 씬에서 찾는다.")]
    public RaonVoiceClient voice;
    public PersonaSpawner spawner;

    [Tooltip("체험자의 시점 카메라. 비워두면 Camera.main 을 쓴다.")]
    public Camera vrCamera;

    [Header("설정")]
    [Tooltip("띄울 화면의 가로 해상도. 세로는 비율에 맞춰 정한다.")]
    public int captureWidth = 1280;

    [Tooltip("정보를 몇 초마다 새로 그릴지. 매 프레임 문자열을 만들 이유는 없다.")]
    public float refreshSec = 0.25f;

    Camera _spectator;
    RenderTexture _rt;
    float _next;

    void Start()
    {
        if (voice == null) voice = FindObjectOfType<RaonVoiceClient>();
        if (spawner == null) spawner = FindObjectOfType<PersonaSpawner>();
        if (vrCamera == null) vrCamera = Camera.main;
        // 화면을 다시 지으면 인스펙터 연결이 끊긴다. 이름으로 다시 찾아 잇는다.
        if (vrView == null) vrView = transform.Find("Backdrop/VRView")?.GetComponent<RawImage>();
        if (infoText == null) infoText = transform.Find("Backdrop/StatusCard/InfoText")?.GetComponent<TMP_Text>();

        if (vrCamera == null)
        {
            Debug.LogWarning("[OperatorHUD] 체험자 카메라를 못 찾았습니다. 화면을 띄울 수 없습니다.");
            return;
        }
        SetupSpectator();
    }

    /// <summary>
    /// 헤드셋 한쪽 눈의 가로세로 비. Quest 는 세로가 더 긴 편이라 16:9 로 찍으면
    /// 실제로 보이는 것과 다른 화면이 된다.
    /// </summary>
    float EyeAspect()
    {
        if (XRSettings.enabled && XRSettings.eyeTextureWidth > 0 && XRSettings.eyeTextureHeight > 0)
            return (float)XRSettings.eyeTextureWidth / XRSettings.eyeTextureHeight;
        if (vrCamera != null && vrCamera.aspect > 0.01f) return vrCamera.aspect;
        return 16f / 9f;
    }

    /// <summary>체험자 카메라와 같은 자리에서 같은 것을 찍는 카메라를 하나 더 둔다.</summary>
    void SetupSpectator()
    {
        float aspect = EyeAspect();
        int h = Mathf.Max(360, Mathf.RoundToInt(captureWidth / aspect));
        _rt = new RenderTexture(captureWidth, h, 24) { name = "VRSpectator" };

        var go = new GameObject("SpectatorCamera");
        go.transform.SetParent(vrCamera.transform, false);   // 시점을 그대로 따라간다
        _spectator = go.AddComponent<Camera>();
        _spectator.CopyFrom(vrCamera);
        _spectator.targetTexture = _rt;
        _spectator.stereoTargetEye = StereoTargetEyeMask.None;   // 헤드셋용이 아니다
        _spectator.depth = vrCamera.depth - 1;
        _spectator.aspect = aspect;
        // 카메라를 하나 더 두면 소리도 두 번 들린다.
        var listener = go.GetComponent<AudioListener>();
        if (listener) Destroy(listener);

        if (vrView != null)
        {
            vrView.texture = _rt;
            // 늘려서 채우면 얼굴이 넓어진다. 비율을 지키고 남는 자리는 비워 둔다.
            var fit = vrView.GetComponent<AspectRatioFitter>();
            if (fit == null) fit = vrView.gameObject.AddComponent<AspectRatioFitter>();
            fit.aspectMode = AspectRatioFitter.AspectMode.FitInParent;
            fit.aspectRatio = aspect;
        }
    }

    void Update()
    {
        if (Time.unscaledTime < _next) return;
        _next = Time.unscaledTime + Mathf.Max(0.05f, refreshSec);
        if (infoText != null) infoText.text = BuildInfo();
    }

    string BuildInfo()
    {
        var s = new StringBuilder();

        s.AppendLine("<b>서버</b>");
        if (voice == null)
        {
            s.AppendLine("  RaonVoiceClient 가 씬에 없습니다");
            return s.ToString();
        }
        s.AppendLine($"  {voice.serverUrl}");
        s.AppendLine(voice.serverReady ? "  <color=#7ED9A5>연결됨</color>"
                                       : "  <color=#E08C7E>응답 없음</color>");
        s.AppendLine();

        s.AppendLine("<b>인물</b>");
        if (voice.HasSession)
        {
            s.AppendLine($"  {voice.sessionId}");
            s.AppendLine(voice.SessionHasModel ? "  3D 모델 있음" : "  3D 모델 없음");
        }
        else
        {
            s.AppendLine("  <color=#E0B36A>등록된 인물이 없습니다</color>");
            s.AppendLine("  웹에서 먼저 등록하세요");
        }
        s.AppendLine();

        s.AppendLine("<b>상태</b>");
        string state = voice.isRecording ? "<color=#E08C7E>듣는 중</color>"
                     : voice.isWaiting ? "<color=#E0B36A>생각하는 중</color>"
                     : "대기";
        s.AppendLine($"  {state}");
        s.AppendLine($"  마이크 {Bar(voice.MicLevel)}");
        s.AppendLine();

        s.AppendLine("<b>방금 오간 말</b>");
        s.AppendLine(string.IsNullOrEmpty(voice.lastHeard) ? "  —" : $"  체험자: {voice.lastHeard}");
        s.AppendLine(string.IsNullOrEmpty(voice.lastAnswer) ? "  —" : $"  인물: {voice.lastAnswer}");
        return s.ToString();
    }

    /// <summary>마이크 세기를 글자로 그린다. 옆에서 보는 사람에게는 이게 제일 빠르다.</summary>
    static string Bar(float level)
    {
        int n = Mathf.Clamp(Mathf.RoundToInt(level * 40f), 0, 12);
        return new string('|', n).PadRight(12, '·');
    }

    void OnDestroy()
    {
        if (_spectator) Destroy(_spectator.gameObject);
        if (_rt) { _rt.Release(); Destroy(_rt); }
    }
}
