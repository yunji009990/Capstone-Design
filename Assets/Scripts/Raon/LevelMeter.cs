// LevelMeter.cs
// 마이크로 들어오는 소리 크기를 막대로 보여준다.
//
// Image 의 Filled 로 그리면 스프라이트를 잘라 쓰게 되는데, 유니티 기본 스프라이트는
// 모서리가 둥글어서 잘린 자리가 일그러진다. 그래서 자식 칸의 너비를 직접 늘린다.
//
// 세로선은 감지 기준이다. 이 선을 넘어야 말로 알아듣는다.

using TMPro;
using UnityEngine;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class LevelMeter : MonoBehaviour
{
    [Tooltip("비워두면 씬에서 찾는다.")]
    public RaonVoiceClient voice;

    [Tooltip("늘어날 칸.")]
    public RectTransform fill;

    [Tooltip("감지 기준을 표시하는 세로선.")]
    public RectTransform marker;

    [Tooltip("소리가 없을 때 보여줄 안내.")]
    public TMP_Text hint;

    [Tooltip("막대가 가득 찰 때의 입력 크기. 말소리는 보통 0.05~0.25 다.")]
    public float range = 0.3f;

    [Tooltip("기준 아래일 때 / 넘었을 때 색.")]
    public Color quiet = new Color(0.36f, 0.44f, 0.52f);
    public Color loud = new Color(0.77f, 0.56f, 0.63f);

    Image _fillImg;
    float _shown;          // 올라갈 땐 즉시, 내려올 땐 부드럽게
    float _peak;           // 잠깐 남는 최고점. 순간을 놓치지 않게 한다
    float _peakAt;

    void Awake()
    {
        if (voice == null) voice = FindObjectOfType<RaonVoiceClient>();
        if (fill != null) _fillImg = fill.GetComponent<Image>();
    }

    void Update()
    {
        if (voice == null || fill == null) return;

        float r = Mathf.Max(0.001f, range);
        float target = Mathf.Clamp01(voice.MicLevel / r);
        _shown = target > _shown ? target : Mathf.Lerp(_shown, target, Time.deltaTime * 9f);

        if (_shown >= _peak) { _peak = _shown; _peakAt = Time.time; }
        else if (Time.time - _peakAt > 0.6f)
            _peak = Mathf.Lerp(_peak, _shown, Time.deltaTime * 4f);

        fill.anchorMin = new Vector2(0f, 0f);
        fill.anchorMax = new Vector2(_shown, 1f);
        fill.offsetMin = Vector2.zero;
        fill.offsetMax = Vector2.zero;

        bool over = voice.MicLevel > voice.VadThreshold;
        if (_fillImg) _fillImg.color = over ? loud : quiet;

        if (marker != null)
        {
            float x = Mathf.Clamp01(voice.VadThreshold / r);
            marker.anchorMin = new Vector2(x, 0f);
            marker.anchorMax = new Vector2(x, 1f);
            marker.offsetMin = new Vector2(-1f, 2f);
            marker.offsetMax = new Vector2(1f, -2f);
        }

        if (hint != null)
        {
            // 마이크가 아예 안 열렸는지, 열렸는데 조용한지 구분해 준다.
            hint.text = !voice.IsListening ? "<color=#C4614F>마이크가 열리지 않았습니다</color>"
                      : _peak < 0.02f ? "<color=#6B7280>소리가 들어오지 않습니다</color>"
                      : over ? "<color=#C58FA0>말이 들어옵니다</color>"
                      : "<color=#6B7280>조용합니다</color>";
        }
    }
}
