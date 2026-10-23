// StartSceneSetup.cs
// Tools > 다시봄 > 시작화면 > 시작 씬 만들기
//
// 시작화면 씬(Assets/Scenes/StartScene.unity)을 새로 만들고 빌드 첫 씬으로 둔다.
//   - Player_CRC 리그, 파스텔 하늘, 눈앞의 World Space 패널(제목·배경 선택 3개·시작하기)
//   - VRUIPointer: 컨트롤러 레이저 + 손 집기로 단추를 누른다
//   - Resources/CafeBackgroundSet: Scene_2 실행 중 배경을 바꿀 재질 짝
// Scene_2 파일은 열거나 고치지 않는다. 배경은 Scene_2 가 열릴 때 CafeBackgroundSet 이 바꾼다.

using System.Collections.Generic;
using System.IO;
using System.Linq;
using TMPro;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.InputSystem.UI;
using UnityEngine.Rendering;
using UnityEngine.SceneManagement;
using UnityEngine.UI;

public static class StartSceneSetup
{
    const string ScenePath = "Assets/Scenes/StartScene.unity";
    const string ExperienceScenePath = "Assets/Scenes/Scene_2.unity";
    const string PlayerPrefab = "Assets/Prefabs/Player_CRC.prefab";
    const string InteractionRigName = "OVRInteractionComprehensive";
    const string FontPath = "Assets/Scripts/Raon/Fonts/Malgun SDF.asset";
    const string PanelSprite = "Assets/Scripts/Raon/Theme/OperatorTheme_soft16_22.png";
    const string BackgroundSetPath = "Assets/Resources/" + CafeBackgroundSet.ResourceName + ".asset";

    static readonly Color Ink = new Color(0.96f, 0.94f, 0.97f);
    static readonly Color SoftInk = new Color(0.85f, 0.80f, 0.88f);
    static readonly Color Rose = new Color(0.773f, 0.561f, 0.627f);

    [MenuItem("Tools/다시봄/시작화면/시작 씬 만들기")]
    static void Build()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode)
        {
            EditorUtility.DisplayDialog("시작 씬 만들기", "Play 를 멈춘 뒤 실행하세요.", "확인");
            return;
        }
        if (File.Exists(ScenePath) &&
            !EditorUtility.DisplayDialog("시작 씬 만들기", ScenePath + " 이 이미 있습니다. 새로 만들까요?", "새로 만들기", "취소"))
            return;
        if (!EditorSceneManager.SaveCurrentModifiedScenesIfUserWantsTo()) return;

        var backgrounds = BuildBackgroundSet();
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

        // 하늘·빛: Scene_2 의 파스텔 하늘과 같은 분위기로 맞춘다.
        RenderSettings.skybox = backgrounds.pastelSky;
        RenderSettings.ambientMode = AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = new Color(.58f, .64f, .92f);
        RenderSettings.ambientEquatorColor = new Color(.65f, .57f, .76f);
        RenderSettings.ambientGroundColor = new Color(.32f, .34f, .49f);
        RenderSettings.fog = false;
        var light = new GameObject("Directional Light").AddComponent<Light>();
        light.type = LightType.Directional;
        light.intensity = 0.9f;
        light.color = new Color(1f, 0.96f, 0.98f);
        light.transform.rotation = Quaternion.Euler(50f, -30f, 0f);

        var player = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(PlayerPrefab), scene);
        if (player == null) Debug.LogWarning("[StartScene] " + PlayerPrefab + " 를 찾지 못했습니다.");
        DisableInteractionRig(player);
        var eyeCamera = player != null ? player.GetComponentsInChildren<Camera>(true).FirstOrDefault() : null;

        var events = new GameObject("EventSystem");
        events.AddComponent<EventSystem>();
        events.AddComponent<InputSystemUIInputModule>();

        var screen = BuildCanvas(eyeCamera);

        var pointer = new GameObject("VRUIPointer").AddComponent<VRUIPointer>();
        pointer.canvases.Add(screen.GetComponent<Canvas>());

        EditorSceneManager.SaveScene(scene, ScenePath);
        PutFirstInBuild();
        Selection.activeGameObject = screen.gameObject;
        Debug.Log("[StartScene] 시작 씬을 만들고 빌드 첫 씬으로 두었습니다: " + ScenePath);
    }

    /// <summary>
    /// Player_CRC 의 OVRInteractionComprehensive 는 손·컨트롤러 데이터 소스와 HMD 참조가 비어 있어
    /// 그대로 두면 실행 중 NullReferenceException 이 매 프레임 난다. Scene_2 도 이 오브젝트를 빼고 쓴다.
    /// 시작화면은 VRUIPointer 로 단추를 누르므로 끈다.
    /// </summary>
    static void DisableInteractionRig(GameObject player)
    {
        if (player == null) return;
        var rig = player.GetComponentsInChildren<Transform>(true).FirstOrDefault(t => t.name == InteractionRigName);
        if (rig == null)
        {
            Debug.LogWarning("[StartScene] " + InteractionRigName + " 를 찾지 못했습니다.");
            return;
        }
        rig.gameObject.SetActive(false);
    }

    /// <summary>Scene_2 의 밤하늘·파스텔 재질 짝을 Resources 에 담는다. 실행 중에는 AssetDatabase 를 쓸 수 없다.</summary>
    static CafeBackgroundSet BuildBackgroundSet()
    {
        var set = AssetDatabase.LoadAssetAtPath<CafeBackgroundSet>(BackgroundSetPath);
        if (set == null)
        {
            Directory.CreateDirectory(Path.GetDirectoryName(BackgroundSetPath));
            set = ScriptableObject.CreateInstance<CafeBackgroundSet>();
            AssetDatabase.CreateAsset(set, BackgroundSetPath);
        }
        string folder = Scene2SkyCafe.Folder;
        var pairs = new List<CafeBackgroundSet.MaterialPair>();
        foreach (string name in Scene2SkyCafe.AppearanceMaterials)
        {
            var night = AssetDatabase.LoadAssetAtPath<Material>(folder + "/" + name + ".mat");
            var day = AssetDatabase.LoadAssetAtPath<Material>(folder + "/Pastel" + name + ".mat");
            if (night == null || day == null)
                Debug.LogWarning("[StartScene] 배경 재질 짝이 없습니다: " + name + " (파스텔 버전을 한 번 적용하면 만들어집니다)");
            pairs.Add(new CafeBackgroundSet.MaterialPair { moonlit = night, pastel = day });
        }
        set.materials = pairs.ToArray();
        set.moonlitSky = AssetDatabase.LoadAssetAtPath<Material>(folder + "/Sky.mat");
        set.pastelSky = AssetDatabase.LoadAssetAtPath<Material>(folder + "/PastelSky.mat");
        EditorUtility.SetDirty(set);
        AssetDatabase.SaveAssets();
        return set;
    }

    static StartScreen BuildCanvas(Camera eyeCamera)
    {
        var font = AssetDatabase.LoadAssetAtPath<TMP_FontAsset>(FontPath);
        var sprite = AssetDatabase.LoadAssetAtPath<Sprite>(PanelSprite);

        var go = new GameObject("StartCanvas", typeof(RectTransform));
        var canvas = go.AddComponent<Canvas>();
        canvas.renderMode = RenderMode.WorldSpace;
        canvas.worldCamera = eyeCamera;   // Editor·PC 화면에서 마우스로도 누를 수 있게 한다.
        go.AddComponent<CanvasScaler>().dynamicPixelsPerUnit = 10f;
        go.AddComponent<GraphicRaycaster>();
        var rect = (RectTransform)go.transform;
        rect.sizeDelta = new Vector2(1000f, 760f);
        rect.localScale = Vector3.one * 0.0012f;
        rect.position = new Vector3(0f, 1.5f, 1.6f);   // 실행하면 StartScreen 이 눈앞으로 옮긴다.

        var panel = Box(rect, "Panel", Vector2.zero, rect.sizeDelta, new Color(0.10f, 0.09f, 0.16f, 0.78f), sprite);
        panel.raycastTarget = false;

        Text(rect, "Title", "다시, 봄", font, 104, Ink, new Vector2(0f, 270f), new Vector2(900f, 130f), FontStyles.Bold);
        Text(rect, "Subtitle", "Seeing You Again", font, 40, SoftInk, new Vector2(0f, 185f), new Vector2(900f, 60f), FontStyles.Italic);
        Text(rect, "Caption", "그리운 사람과 다시 만나는 자리", font, 32, SoftInk, new Vector2(0f, 130f), new Vector2(900f, 50f), FontStyles.Normal);

        Text(rect, "BackgroundLabel", "배경을 골라 주세요", font, 34, Ink, new Vector2(0f, 45f), new Vector2(900f, 50f), FontStyles.Normal);
        var choices = new[]
        {
            (StartSelection.Background.Moonlit, "밤하늘 카페"),
            (StartSelection.Background.Pastel, "파스텔 하늘 카페"),
            (StartSelection.Background.Garden, "정원 카페"),
        };
        var backgroundButtons = new List<StartScreen.BackgroundButton>();
        for (int i = 0; i < choices.Length; i++)
        {
            var button = MakeButton(rect, "Background_" + choices[i].Item1, choices[i].Item2, font, 32,
                new Vector2((i - 1) * 300f, -40f), new Vector2(280f, 100f), new Color(1f, 1f, 1f, 0.16f), sprite);
            backgroundButtons.Add(new StartScreen.BackgroundButton { background = choices[i].Item1, button = button });
        }

        var start = MakeButton(rect, "StartButton", "시작하기", font, 46,
            new Vector2(0f, -190f), new Vector2(420f, 120f), Rose, sprite);
        var status = Text(rect, "Status", "", font, 28, SoftInk, new Vector2(0f, -305f), new Vector2(900f, 50f), FontStyles.Normal);

        var screen = go.AddComponent<StartScreen>();
        screen.panel = rect;
        screen.backgroundButtons = backgroundButtons.ToArray();
        screen.startButton = start;
        screen.statusText = status;
        return screen;
    }

    static Image Box(RectTransform parent, string name, Vector2 position, Vector2 size, Color color, Sprite sprite)
    {
        var go = new GameObject(name, typeof(RectTransform));
        var rect = (RectTransform)go.transform;
        rect.SetParent(parent, false);
        rect.anchoredPosition = position;
        rect.sizeDelta = size;
        var image = go.AddComponent<Image>();
        image.color = color;
        if (sprite != null)
        {
            image.sprite = sprite;
            image.type = Image.Type.Sliced;
        }
        return image;
    }

    static TextMeshProUGUI Text(RectTransform parent, string name, string value, TMP_FontAsset font, float size,
        Color color, Vector2 position, Vector2 box, FontStyles style)
    {
        var go = new GameObject(name, typeof(RectTransform));
        var rect = (RectTransform)go.transform;
        rect.SetParent(parent, false);
        rect.anchoredPosition = position;
        rect.sizeDelta = box;
        var text = go.AddComponent<TextMeshProUGUI>();
        if (font != null) text.font = font;
        text.text = value;
        text.fontSize = size;
        text.fontStyle = style;
        text.color = color;
        text.alignment = TextAlignmentOptions.Center;
        text.raycastTarget = false;
        return text;
    }

    static Button MakeButton(RectTransform parent, string name, string label, TMP_FontAsset font, float size,
        Vector2 position, Vector2 box, Color color, Sprite sprite)
    {
        var image = Box(parent, name, position, box, color, sprite);
        var button = image.gameObject.AddComponent<Button>();
        button.targetGraphic = image;
        // 평소엔 조금 어둡게, 가리키면 원래 밝기로. 선택 상태는 쓰지 않는다(누른 단추가 계속 밝게 남는다).
        var colors = button.colors;
        colors.normalColor = new Color(0.82f, 0.82f, 0.82f);
        colors.highlightedColor = Color.white;
        colors.pressedColor = new Color(0.65f, 0.65f, 0.65f);
        colors.selectedColor = colors.normalColor;
        button.colors = colors;
        button.navigation = new Navigation { mode = Navigation.Mode.None };
        var text = Text((RectTransform)image.transform, "Label", label, font, size, Ink, Vector2.zero, box, FontStyles.Normal);
        text.rectTransform.anchorMin = Vector2.zero;
        text.rectTransform.anchorMax = Vector2.one;
        text.rectTransform.sizeDelta = Vector2.zero;
        return button;
    }

    /// <summary>StartScene 을 0번으로 두고 Scene_2 가 빠져 있으면 넣는다. 나머지 순서는 건드리지 않는다.</summary>
    static void PutFirstInBuild()
    {
        var scenes = EditorBuildSettings.scenes.Where(s => s.path != ScenePath).ToList();
        if (!scenes.Any(s => s.path == ExperienceScenePath))
            scenes.Add(new EditorBuildSettingsScene(ExperienceScenePath, true));
        scenes.Insert(0, new EditorBuildSettingsScene(ScenePath, true));
        EditorBuildSettings.scenes = scenes.ToArray();
    }
}
