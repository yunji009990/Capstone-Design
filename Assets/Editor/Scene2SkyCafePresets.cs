// 승인된 밤하늘 재질을 보존하고 파스텔 변형을 별도 자산으로 전환한다.
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

public static partial class Scene2SkyCafe
{
    static readonly string[] AppearanceMaterials =
        { "VolumeCloud0", "VolumeCloud1", "VolumeCloudSea", "TerraceStone", "FloatingStone" };

    public static void MoonlitAppearance() => SelectAppearance(CafeSkyThemeState.Appearance.Moonlit);

    public static void PastelAppearance() => SelectAppearance(CafeSkyThemeState.Appearance.Pastel);

    static void SelectAppearance(CafeSkyThemeState.Appearance appearance)
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        var state = FindState();
        if (Lightmapping.isRunning) throw new InvalidOperationException("조명 베이크 중에는 전환할 수 없습니다.");
        if (state == null) { Apply(); state = FindState(); }
        if (appearance == CafeSkyThemeState.Appearance.Pastel) EnsurePastelMaterials();
        Undo.IncrementCurrentGroup();
        int group = Undo.GetCurrentGroup();
        Undo.RecordObject(state, UndoName);
        state.appearance = appearance;
        SetTheme(state, true);
        Undo.CollapseUndoOperations(group);
        AssetDatabase.SaveAssets();
        EditorSceneManager.SaveScene(scene);
        ViewCafe();
        CaptureViews(appearance == CafeSkyThemeState.Appearance.Pastel ? "pastel" : "moonlit");
        InspectCloudDepth();
        Debug.Log("[SkyCafe] " + (appearance == CafeSkyThemeState.Appearance.Pastel ? "파스텔 하늘" : "보관한 밤하늘") + " 버전으로 전환했습니다.");
    }

    static void ApplyAppearance(CafeSkyThemeState state)
    {
        bool pastel = state.appearance == CafeSkyThemeState.Appearance.Pastel;
        if (pastel) EnsurePastelMaterials();
        foreach (var renderer in state.GetComponentsInChildren<Renderer>(true))
        {
            var materials = renderer.sharedMaterials;
            bool changed = false;
            for (int i = 0; i < materials.Length; i++)
            {
                foreach (string name in AppearanceMaterials)
                {
                    var night = AssetDatabase.LoadAssetAtPath<Material>(Folder + "/" + name + ".mat");
                    var day = AssetDatabase.LoadAssetAtPath<Material>(Folder + "/Pastel" + name + ".mat");
                    if (materials[i] != night && (day == null || materials[i] != day)) continue;
                    var target = pastel ? day : night;
                    if (materials[i] != target) { materials[i] = target; changed = true; }
                    break;
                }
            }
            if (!changed) continue;
            Undo.RecordObject(renderer, UndoName);
            renderer.sharedMaterials = materials;
            EditorUtility.SetDirty(renderer);
        }
        RenderSettings.skybox = AssetDatabase.LoadAssetAtPath<Material>(Folder + (pastel ? "/PastelSky.mat" : "/Sky.mat"));
        RenderSettings.ambientMode = AmbientMode.Trilight;
        RenderSettings.ambientSkyColor = pastel ? new Color(.58f, .64f, .92f) : new Color(.17f, .23f, .40f);
        RenderSettings.ambientEquatorColor = pastel ? new Color(.65f, .57f, .76f) : new Color(.26f, .27f, .39f);
        RenderSettings.ambientGroundColor = pastel ? new Color(.32f, .34f, .49f) : new Color(.11f, .14f, .23f);
        RenderSettings.ambientIntensity = .85f;
    }

    static Material CloneAppearanceMaterial(string sourceName, string targetName, string shaderName)
    {
        var existing = AssetDatabase.LoadAssetAtPath<Material>(Folder + "/" + targetName + ".mat");
        if (existing != null) return null; // 전환으로 사용자의 Inspector 조절을 덮어쓰지 않는다.
        var source = AssetDatabase.LoadAssetAtPath<Material>(Folder + "/" + sourceName + ".mat");
        var shader = Shader.Find(shaderName);
        if (source == null || shader == null || !shader.isSupported || ShaderUtil.ShaderHasError(shader))
            throw new InvalidOperationException("하늘 버전 재질을 준비할 수 없습니다: " + targetName);
        var material = new Material(source) { name = targetName, shader = shader };
        AssetDatabase.CreateAsset(material, Folder + "/" + targetName + ".mat");
        return material;
    }

    static void EnsurePastelMaterials()
    {
        var sky = CloneAppearanceMaterial("Sky", "PastelSky", "AgainSpring/Sky Cafe Pastel Atmosphere");
        if (sky != null)
        {
            sky.SetColor("_Zenith", new Color(.10f,.30f,.95f));
            sky.SetColor("_Horizon", new Color(.30f,.48f,1f));
            sky.SetColor("_CloudColor", new Color(1.02f,.98f,1.10f));
            sky.SetColor("_RoseColor", new Color(1f,.61f,.81f));
            sky.SetFloat("_CloudAmount", .85f); sky.SetFloat("_Exposure", 1);
            EditorUtility.SetDirty(sky);
        }
        foreach (string name in AppearanceMaterials)
        {
            bool cloud = name.StartsWith("VolumeCloud", StringComparison.Ordinal);
            var material = CloneAppearanceMaterial(name, "Pastel" + name,
                cloud ? "AgainSpring/Sky Cafe Pastel Cloud Volume" : "AgainSpring/Sky Cafe Cloud");
            if (material == null) continue;
            if (cloud)
            {
                material.SetColor("_LightColor", new Color(1.10f,1.04f,1.15f));
                material.SetColor("_MidColor", new Color(.62f,.55f,.91f));
                material.SetColor("_ShadeColor", new Color(.23f,.20f,.56f));
                material.SetColor("_PinkColor", new Color(.96f,.50f,.76f));
                material.SetColor("_RimColor", new Color(1.10f,.95f,1.15f));
                material.SetFloat("_PinkAmount", .42f); material.SetFloat("_PhotoExposure", 1.55f);
            }
            else
            {
                material.SetColor("_LightColor", new Color(.69f,.66f,.83f));
                material.SetColor("_ShadeColor", new Color(.25f,.25f,.40f));
                material.SetColor("_HazeColor", new Color(.62f,.57f,.83f));
            }
            EditorUtility.SetDirty(material);
        }
    }

    static string NightAssetDigest()
    {
        var names = AppearanceMaterials.Concat(new[] { "Sky" }).Select(n => n + ".mat")
            .Concat(new[] { "SkyCafeAtmosphere.shader", "SkyCafeCloudVolume.shader", "SkyCafeCloud.shader" });
        return Digest(string.Join("\n", names.Select(n => n + "\n" + File.ReadAllText(Folder + "/" + n))));
    }

    static bool AppearanceMatches(CafeSkyThemeState state, CafeSkyThemeState.Appearance appearance)
    {
        bool pastel = appearance == CafeSkyThemeState.Appearance.Pastel;
        string prefix = pastel ? "Pastel" : "";
        var volumes = state.GetComponentsInChildren<MeshRenderer>(true).Where(r => IsVolumeCloud(r.sharedMaterial)).ToArray();
        return state.skyActive && state.gameObject.activeSelf && volumes.Length == 9 &&
            volumes.All(r => AssetDatabase.GetAssetPath(r.sharedMaterial).StartsWith(Folder + "/" + prefix + "VolumeCloud", StringComparison.Ordinal)) &&
            RenderSettings.skybox == AssetDatabase.LoadAssetAtPath<Material>(Folder + "/" + prefix + "Sky.mat");
    }

    public static void ValidateAppearances()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        var state = FindState();
        if (state == null) throw new InvalidOperationException("하늘 카페가 없습니다.");
        EnsurePastelMaterials(); AssetDatabase.SaveAssets();
        var original = state.appearance; bool active = state.skyActive;
        string night = NightAssetDigest(), before = ProtectedDigest(), glass = GlassDigest();
        string geometry = LayoutDigest(new[] { state.gameObject });
        string garden = LayoutDigest(state.originalObjects.Select(o => o.target));
        bool moonlit = false, pastel = false, gardenRestored = false;
        try
        {
            state.appearance = CafeSkyThemeState.Appearance.Moonlit; SetTheme(state, true);
            moonlit = AppearanceMatches(state, state.appearance);
            CaptureViews("moonlit");
            state.appearance = CafeSkyThemeState.Appearance.Pastel; SetTheme(state, true);
            pastel = AppearanceMatches(state, state.appearance);
            CaptureViews("pastel");
            SetTheme(state, false);
            gardenRestored = state.originalObjects.All(o => o.target != null && o.target.activeSelf == o.active) &&
                state.originalRenderers.All(o => o.target != null && o.target.enabled == o.enabled) &&
                RenderSettings.skybox == state.originalSky;
        }
        finally { state.appearance = original; SetTheme(state, active); }
        AssetDatabase.SaveAssets(); EditorSceneManager.SaveScene(scene);
        bool nightUnchanged = night == NightAssetDigest(), protectedMatch = before == ProtectedDigest(), glassMatch = glass == GlassDigest();
        bool geometryMatch = geometry == LayoutDigest(new[] { state.gameObject });
        bool gardenMatch = garden == LayoutDigest(state.originalObjects.Select(o => o.target));
        bool restoredSelection = state.appearance == original && state.skyActive == active;
        WriteReport("appearance_validation", new { moonlit, pastel, gardenRestored, nightUnchanged, protectedMatch, glassMatch, geometryMatch, gardenMatch, restoredSelection });
        if (!moonlit || !pastel || !gardenRestored || !nightUnchanged || !protectedMatch || !glassMatch || !geometryMatch || !gardenMatch || !restoredSelection)
            throw new InvalidOperationException("두 하늘 버전의 보존 검사 실패.");
        InspectCloudDepth();
        Debug.Log("[SkyCafe] 밤하늘·파스텔·정원 전환과 원래 밤 재질·구름 배치·유리·플레이어 구성 보존 검사 통과.");
    }
}
