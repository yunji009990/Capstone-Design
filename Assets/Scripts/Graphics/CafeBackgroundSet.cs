// CafeBackgroundSet.cs
// 시작화면에서 고른 배경을 Scene_2 실행 중에 적용한다.
//
// 배경 전환은 원래 Editor 메뉴(Tools > 다시봄 > 그래픽 > 배경 선택)만 할 수 있었다. 그 메뉴는
// AssetDatabase 로 재질을 찾으므로 실행 중에는 쓸 수 없다. 그래서 밤하늘·파스텔 재질 짝을
// 이 에셋(Resources/CafeBackgroundSet)에 미리 담아 두고, 씬이 열릴 때 같은 순서로 바꾼다.
// 하늘 카페의 원래 상태는 CafeSkyThemeState 가 이미 기록해 두었다. 씬 파일은 고치지 않는다.
//
// 에셋은 Tools > 다시봄 > 시작화면 > 시작 씬 만들기 가 함께 만든다.
// 환경광 색은 Scene2SkyCafePresets.ApplyAppearance 와 같은 값이다. 그쪽을 바꾸면 여기도 맞춘다.

using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;

[CreateAssetMenu(menuName = "다시봄/Cafe Background Set")]
public sealed class CafeBackgroundSet : ScriptableObject
{
    public const string ResourceName = "CafeBackgroundSet";

    [System.Serializable]
    public struct MaterialPair { public Material moonlit; public Material pastel; }

    public MaterialPair[] materials = new MaterialPair[0];
    public Material moonlitSky;
    public Material pastelSky;

    [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
    static void Hook()
    {
        SceneManager.sceneLoaded -= OnSceneLoaded;
        SceneManager.sceneLoaded += OnSceneLoaded;
        // 첫 씬은 sceneLoaded 보다 늦게 등록되므로 지금 열린 씬에도 한 번 적용한다.
        OnSceneLoaded(SceneManager.GetActiveScene(), LoadSceneMode.Single);
    }

    static void OnSceneLoaded(Scene scene, LoadSceneMode mode)
    {
        if (StartSelection.background == StartSelection.Background.Keep) return;
        CafeSkyThemeState state = null;
        foreach (var root in scene.GetRootGameObjects())
        {
            state = root.GetComponentInChildren<CafeSkyThemeState>(true);
            if (state != null) break;
        }
        if (state == null || !state.captured) return;

        var set = Resources.Load<CafeBackgroundSet>(ResourceName);
        if (set == null)
        {
            Debug.LogWarning("[CafeBackgroundSet] Resources/" + ResourceName + " 이 없어 저장된 배경을 그대로 씁니다.");
            return;
        }
        set.Apply(state, StartSelection.background);
    }

    /// <summary>Editor 의 SetTheme·ApplyAppearance 와 같은 순서로 바꾼다. Undo·저장은 하지 않는다.</summary>
    public void Apply(CafeSkyThemeState state, StartSelection.Background background)
    {
        bool sky = background != StartSelection.Background.Garden;
        foreach (var item in state.originalObjects)
            if (item.target != null) item.target.SetActive(sky ? false : item.active);
        foreach (var item in state.originalRenderers)
            if (item.target != null) item.target.enabled = sky ? false : item.enabled;
        state.gameObject.SetActive(sky);
        state.skyActive = sky;
        RenderSettings.fog = sky ? false : state.originalFog;

        if (sky)
        {
            bool pastel = background == StartSelection.Background.Pastel;
            state.appearance = pastel ? CafeSkyThemeState.Appearance.Pastel : CafeSkyThemeState.Appearance.Moonlit;
            SwapMaterials(state, pastel);
            RenderSettings.skybox = pastel ? pastelSky : moonlitSky;
            RenderSettings.ambientMode = AmbientMode.Trilight;
            RenderSettings.ambientSkyColor = pastel ? new Color(.58f, .64f, .92f) : new Color(.17f, .23f, .40f);
            RenderSettings.ambientEquatorColor = pastel ? new Color(.65f, .57f, .76f) : new Color(.26f, .27f, .39f);
            RenderSettings.ambientGroundColor = pastel ? new Color(.32f, .34f, .49f) : new Color(.11f, .14f, .23f);
            RenderSettings.ambientIntensity = .85f;
        }
        else
        {
            RenderSettings.skybox = state.originalSky;
            RenderSettings.ambientMode = state.originalAmbientMode;
            RenderSettings.ambientSkyColor = state.originalAmbientSky;
            RenderSettings.ambientEquatorColor = state.originalAmbientEquator;
            RenderSettings.ambientGroundColor = state.originalAmbientGround;
            RenderSettings.ambientIntensity = state.originalAmbientIntensity;
        }
        DynamicGI.UpdateEnvironment();
        Debug.Log("[CafeBackgroundSet] 시작화면에서 고른 배경을 적용했습니다: " + background);
    }

    void SwapMaterials(CafeSkyThemeState state, bool pastel)
    {
        foreach (var renderer in state.GetComponentsInChildren<Renderer>(true))
        {
            var shared = renderer.sharedMaterials;
            bool changed = false;
            for (int i = 0; i < shared.Length; i++)
            {
                foreach (var pair in materials)
                {
                    if (shared[i] != pair.moonlit && shared[i] != pair.pastel) continue;
                    var target = pastel ? pair.pastel : pair.moonlit;
                    if (target != null && shared[i] != target) { shared[i] = target; changed = true; }
                    break;
                }
            }
            if (changed) renderer.sharedMaterials = shared;
        }
    }
}
