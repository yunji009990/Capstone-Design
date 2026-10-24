// StartScreen.cs
// 시작화면. 배경을 고르고 시작을 누르면 Scene_2 를 연다.
//
// 체험자는 헤드셋을 쓴 채 컨트롤러 레이저나 손 집기(VRUIPointer)로 단추를 누른다.
// 운영자는 Editor·PC 화면에서 마우스로 눌러도 된다(같은 uGUI 단추다).
//
// 고른 값은 StartSelection 에 남기고, Scene_2 쪽(CafeBackgroundSet·ExperienceControl)이 읽는다.
// 단추와 글자는 Tools > 다시봄 > 시작화면 > 시작 씬 만들기 가 만들어 연결한다.

using System.Collections;
using TMPro;
using UnityEngine;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

[DisallowMultipleComponent]
public class StartScreen : MonoBehaviour
{
    [System.Serializable]
    public struct BackgroundButton
    {
        public StartSelection.Background background;
        public Button button;
    }

    [Header("다음 씬")]
    public string experienceScene = "Scene_2";

    [Header("위치")]
    [Tooltip("체험자 눈앞에 옮겨 놓을 패널. 헤드셋 높이는 사람마다 달라서 시작할 때 맞춘다.")]
    public Transform panel;
    [Tooltip("눈에서 패널까지 거리(m).")]
    public float distance = 1.6f;
    [Tooltip("눈높이보다 얼마나 내려 둘지(m). 살짝 내려다보는 쪽이 편하다.")]
    public float drop = 0.1f;

    [Header("배경")]
    public BackgroundButton[] backgroundButtons = new BackgroundButton[0];
    [Tooltip("처음에 골라 둘 배경.")]
    public StartSelection.Background defaultBackground = StartSelection.Background.Pastel;

    [Header("시작")]
    public Button startButton;
    public TMP_Text statusText;

    [Header("색")]
    public Color idleColor = new Color(1f, 1f, 1f, 0.16f);
    public Color selectedColor = new Color(0.773f, 0.561f, 0.627f);   // ExperienceControl 의 로즈

    StartSelection.Background _chosen;
    bool _loading;

    void Start()
    {
        // 지난 체험에서 돌아온 경우에도 시작화면에서 새로 고르게 한다.
        StartSelection.startRequested = false;
        foreach (var item in backgroundButtons)
        {
            if (item.button == null) continue;
            var background = item.background;
            item.button.onClick.AddListener(() => Choose(background));
        }
        if (startButton) startButton.onClick.AddListener(Begin);
        Choose(defaultBackground);
        if (statusText) statusText.text = "";
        StartCoroutine(PlaceInFront());
    }

    /// <summary>헤드셋 추적이 잡힌 뒤 패널을 눈앞에 둔다. 처음 몇 프레임은 머리 위치가 0 이다.</summary>
    IEnumerator PlaceInFront()
    {
        if (panel == null) yield break;
        for (int i = 0; i < 10; i++) yield return null;
        yield return new WaitForSecondsRealtime(0.3f);
        var rig = FindObjectOfType<OVRCameraRig>();
        Transform eye = rig != null && rig.centerEyeAnchor != null ? rig.centerEyeAnchor
                      : Camera.main != null ? Camera.main.transform : null;
        if (eye == null) yield break;
        Vector3 forward = eye.forward;
        forward.y = 0f;
        if (forward.sqrMagnitude < 0.0001f) forward = Vector3.forward;
        forward.Normalize();
        panel.position = eye.position + forward * distance + Vector3.down * drop;
        panel.rotation = Quaternion.LookRotation(forward, Vector3.up);
    }

    public void Choose(StartSelection.Background background)
    {
        if (_loading) return;
        _chosen = background;
        foreach (var item in backgroundButtons)
        {
            if (item.button == null) continue;
            var image = item.button.targetGraphic as Image;
            if (image != null) image.color = item.background == background ? selectedColor : idleColor;
        }
    }

    public void Begin()
    {
        if (_loading) return;
        _loading = true;
        StartSelection.background = _chosen;
        StartSelection.startRequested = true;
        foreach (var item in backgroundButtons)
            if (item.button != null) item.button.interactable = false;
        if (startButton) startButton.interactable = false;
        StartCoroutine(Load());
    }

    IEnumerator Load()
    {
        if (statusText) statusText.text = "카페로 가는 중…";
        // 등록되지 않은 씬은 LoadSceneAsync가 예외를 던지므로 먼저 확인한다.
        if (!Application.CanStreamedLevelBeLoaded(experienceScene))
        {
            LoadFailed();
            yield break;
        }
        var op = SceneManager.LoadSceneAsync(experienceScene);
        if (op == null)
        {
            LoadFailed();
            yield break;
        }
        while (!op.isDone)
        {
            if (statusText) statusText.text = $"카페로 가는 중… {Mathf.RoundToInt(Mathf.Clamp01(op.progress / 0.9f) * 100f)}%";
            yield return null;
        }
    }

    void LoadFailed()
    {
        if (statusText) statusText.text = experienceScene + " 을 열 수 없습니다. 빌드 설정을 확인해 주세요.";
        StartSelection.startRequested = false;
        _loading = false;
        foreach (var item in backgroundButtons)
            if (item.button != null) item.button.interactable = true;
        if (startButton) startButton.interactable = true;
    }
}
