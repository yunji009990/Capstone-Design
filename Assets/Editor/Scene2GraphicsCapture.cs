// Scene_2 배경을 같은 눈높이·카메라 설정으로 비교하고 공개 배경 자산만 검사한다.
// 운영 인물 자료, 대화 설정, API 키는 보고서에 넣지 않는다.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Text;
using Newtonsoft.Json;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;
using UnityEngine.SceneManagement;

public static class Scene2GraphicsCapture
{
    internal const string ScenePath = "Assets/Scenes/Scene_2.unity";
    internal const string CafeAssets = "Assets/Models/Cozy cafe - confectionery and bakery/";
    internal static string OutputDirectory => Path.Combine(Path.GetDirectoryName(Application.dataPath),
        "tools", "_work", "graphics_" + DateTime.Now.ToString("yyyyMMdd") + "_rework");

    [MenuItem("Tools/다시봄/그래픽/변경 전 촬영·검사")]
    public static void CaptureBefore() => CaptureAndInspect("before");

    [MenuItem("Tools/다시봄/그래픽/변경 후 촬영·검사")]
    public static void CaptureAfter() => CaptureAndInspect("after");

    [MenuItem("Tools/다시봄/그래픽/창 유리 조정 비교 촬영")]
    public static void CaptureGlass() => CaptureAndInspect("glass");

    [MenuItem("Tools/다시봄/그래픽/수목 크기 조정 전 촬영")]
    public static void CaptureTreesBefore()
    {
        CaptureAndInspect("trees_before");
        CaptureOvalWindows("trees_before");
    }

    [MenuItem("Tools/다시봄/그래픽/수목 크기 조정 후 촬영")]
    public static void CaptureTreesAfter()
    {
        CaptureAndInspect("trees_after");
        CaptureOvalWindows("trees_after");
    }

    [MenuItem("Tools/다시봄/그래픽/타원 창 바깥 촬영")]
    public static void CaptureOvalWindows() => CaptureOvalWindows("after");

    static void CaptureOvalWindows(string stage)
    {
        RequireScene();
        Directory.CreateDirectory(OutputDirectory);
        Capture(stage + "_oval_front", new Vector3(-2.5f, 1.55f, -.76f), new Vector3(0f, -90f, 0f));
        Capture(stage + "_oval_back", new Vector3(-2.5f, 1.55f, -3.86f), new Vector3(0f, -90f, 0f));
        Capture(stage + "_oval_oblique", new Vector3(-2.9f, 1.55f, -2.0f), new Vector3(0f, -125f, 0f));
        Debug.Log("[Scene2Graphics] 타원 창 두 곳·비스듬한 시점 촬영 완료.");
    }

    [MenuItem("Tools/다시봄/그래픽/완성된 카페 보기")]
    public static void ViewCafe()
    {
        RequireScene();
        var view = SceneView.lastActiveSceneView;
        if (view == null) return;
        var rotation = Quaternion.Euler(0f, 75f, 0f);
        view.sceneLighting = true;
        view.orthographic = false;
        view.LookAtDirect(new Vector3(-3.1f, 1.35f, -2.2f) + rotation * Vector3.forward * 2f,
            rotation, 1.4f);
        view.Repaint();
    }

    internal static Scene RequireScene()
    {
        var scene = SceneManager.GetActiveScene();
        if (scene.path != ScenePath || EditorApplication.isPlayingOrWillChangePlaymode)
            throw new InvalidOperationException("Scene_2를 Edit Mode에서 연 뒤 실행하세요.");
        return scene;
    }

    internal static GameObject SceneObject(string name)
    {
        return RequireScene().GetRootGameObjects().SelectMany(root =>
            root.GetComponentsInChildren<Transform>(true)).Select(t => t.gameObject)
            .FirstOrDefault(go => go.name == name);
    }

    internal static Renderer[] CafeRenderers()
    {
        var root = SceneObject("카페 구조와 소품");
        if (root == null) throw new InvalidOperationException("카페 배경 그룹을 찾지 못했습니다.");
        return root.GetComponentsInChildren<Renderer>(true);
    }

    static float[] Components(Color value) => new[] { value.r, value.g, value.b, value.a };
    static float[] Components(Vector3 value) => new[] { value.x, value.y, value.z };

    static object MaterialInfo(Material material)
    {
        string texture = material.HasProperty("_BaseMap") ? "_BaseMap" : "_MainTex";
        string color = material.HasProperty("_BaseColor") ? "_BaseColor" : "_Color";
        string smoothness = material.HasProperty("_Smoothness") ? "_Smoothness" : "_Glossiness";
        return new
        {
            path = AssetDatabase.GetAssetPath(material),
            name = material.name,
            shader = material.shader.name,
            supported = material.shader.isSupported,
            queue = material.renderQueue,
            texture = material.HasProperty(texture) ? AssetDatabase.GetAssetPath(material.GetTexture(texture)) : "",
            normalMap = material.HasProperty("_BumpMap") ? AssetDatabase.GetAssetPath(material.GetTexture("_BumpMap")) : "",
            smoothness = material.HasProperty(smoothness) ? material.GetFloat(smoothness) : 0f,
            metallic = material.HasProperty("_Metallic") ? material.GetFloat("_Metallic") : 0f,
            baseColor = Components(material.HasProperty(color) ? material.GetColor(color) : Color.white),
            emission = Components(material.HasProperty("_EmissionColor") ? material.GetColor("_EmissionColor") : Color.black),
            preserveSpecular = material.HasProperty("_BlendModePreserveSpecular") ? material.GetFloat("_BlendModePreserveSpecular") : 0f,
            environmentReflections = material.HasProperty("_EnvironmentReflections") ? material.GetFloat("_EnvironmentReflections") : 0f,
            planarFrontal = material.HasProperty("_ReflectionOpacity") ? material.GetFloat("_ReflectionOpacity") : 0f,
            planarGrazing = material.HasProperty("_GrazingOpacity") ? material.GetFloat("_GrazingOpacity") : 0f,
            planarGain = material.HasProperty("_ReflectionGain") ? material.GetFloat("_ReflectionGain") : 0f,
            planarHighlights = material.HasProperty("_HighlightCompression") ? material.GetFloat("_HighlightCompression") : 0f,
            keywords = material.shaderKeywords
        };
    }

    static void CaptureAndInspect(string stage)
    {
        var scene = RequireScene();
        Directory.CreateDirectory(OutputDirectory);
        var exterior = SceneObject("카페 앞 정원");
        var renderers = CafeRenderers().Concat(exterior != null ?
            exterior.GetComponentsInChildren<Renderer>(true) : Array.Empty<Renderer>()).ToArray();
        var materials = renderers.SelectMany(r => r.sharedMaterials).Where(m => m != null).Distinct().ToArray();
        var lights = scene.GetRootGameObjects().SelectMany(root => root.GetComponentsInChildren<Light>(true))
            .Select(l => new
            {
                l.name, l.enabled, active = l.gameObject.activeInHierarchy,
                type = l.type.ToString(), bake = l.lightmapBakeType.ToString(),
                l.intensity, l.range, color = Components(l.color), l.colorTemperature, l.useColorTemperature,
                position = Components(l.transform.position), rotation = Components(l.transform.eulerAngles),
                shadows = l.shadows.ToString()
            }).ToArray();
        var report = new
        {
            stage, scene = scene.path, unity = Application.unityVersion,
            pipeline = QualitySettings.renderPipeline ? QualitySettings.renderPipeline.name : GraphicsSettings.defaultRenderPipeline.name,
            lightmaps = LightmapSettings.lightmaps.Length,
            probes = LightmapSettings.lightProbes ? LightmapSettings.lightProbes.count : 0,
            rendererCount = renderers.Length,
            reflections = scene.GetRootGameObjects().SelectMany(root => root.GetComponentsInChildren<ReflectionProbe>(true))
                .Select(p => new { p.name, p.enabled, active = p.gameObject.activeInHierarchy, p.resolution,
                    texture = AssetDatabase.GetAssetPath(p.bakedTexture), position = Components(p.transform.position),
                    size = Components(p.size), p.boxProjection }).ToArray(),
            probeGroups = scene.GetRootGameObjects().SelectMany(root => root.GetComponentsInChildren<LightProbeGroup>(true))
                .Select(p => new { p.name, count = p.probePositions.Length }).ToArray(),
            materials = materials.Select(MaterialInfo).ToArray(),
            glass = renderers.Where(r => r.sharedMaterials.Any(m => m != null && m.name.Contains("Glass")))
                .Select(r => new { r.name, center = Components(r.bounds.center), size = Components(r.bounds.size),
                    materials = r.sharedMaterials.Where(m => m != null).Select(m => m.name).ToArray(),
                    reflections = r.reflectionProbeUsage.ToString() }).ToArray(),
            exteriorPrefabs = new[] { "Flower_Pot", "Ivy_long", "Ivy_Middle", "Table", "Chair" }.Select(name =>
            {
                var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(CafeAssets + "Models/Prefab/" + name + ".prefab");
                var parts = prefab != null ? prefab.GetComponentsInChildren<Renderer>(true) : Array.Empty<Renderer>();
                var bounds = new Bounds();
                if (parts.Length > 0)
                {
                    bounds = parts[0].bounds;
                    foreach (var part in parts.Skip(1)) bounds.Encapsulate(part.bounds);
                }
                return new { name, center = Components(bounds.center), size = Components(bounds.size) };
            }).ToArray(),
            geometry = renderers.OrderByDescending(r => r.bounds.size.sqrMagnitude).Take(20)
                .Select(r => new { r.name, center = Components(r.bounds.center), size = Components(r.bounds.size), isStatic = r.gameObject.isStatic }).ToArray(),
            lights,
            skybox = RenderSettings.skybox ? AssetDatabase.GetAssetPath(RenderSettings.skybox) : "",
            ambientMode = RenderSettings.ambientMode.ToString(),
            ambientIntensity = RenderSettings.ambientIntensity
        };
        File.WriteAllText(Path.Combine(OutputDirectory, stage + "_audit.json"),
            JsonConvert.SerializeObject(report, Formatting.Indented), new UTF8Encoding(false));
        Capture(stage + "_entrance", new Vector3(-3.1f, 1.35f, -2.2f), new Vector3(0f, 0f, 0f));
        Capture(stage + "_counter", new Vector3(-3.1f, 1.35f, -2.2f), new Vector3(0f, 75f, 0f));
        Capture(stage + "_seating", new Vector3(-2.35f, 1.35f, -0.1f), new Vector3(0f, 235f, 0f));
        // 실제 HMD 검사를 대신하지 않는다. 눈 간격과 고개 이동 시 창밖의 상대 위치를 비교한다.
        var windowEye = new Vector3(-.3f, 1.55f, .2f);
        Capture(stage + "_window_left", windowEye + Vector3.left * .032f, new Vector3(8f, 0f, 0f));
        Capture(stage + "_window_right", windowEye + Vector3.right * .032f, new Vector3(8f, 0f, 0f));
        Capture(stage + "_window_shift", windowEye + Vector3.left * .5f, new Vector3(8f, 0f, 0f));
        Capture(stage + "_standing", new Vector3(-3.1f, 1.7f, -2.2f), new Vector3(10f, 0f, 0f));
        Debug.Log($"[Scene2Graphics] {stage}: 배경 {renderers.Length}개, 재질 {materials.Length}개, 라이트맵 {LightmapSettings.lightmaps.Length}개. 촬영·검사 완료.");
    }

    static void Capture(string name, Vector3 position, Vector3 rotation)
    {
        const int width = 1280, height = 720;
        var go = new GameObject("__Scene2GraphicsCapture") { hideFlags = HideFlags.HideAndDontSave };
        var camera = go.AddComponent<Camera>();
        var source = Camera.main;
        if (source != null) camera.CopyFrom(source);
        camera.enabled = false;
        camera.stereoTargetEye = StereoTargetEyeMask.None;
        camera.fieldOfView = 70f;
        camera.aspect = (float)width / height;
        camera.nearClipPlane = .05f;
        camera.farClipPlane = 300f;
        camera.allowHDR = true;
        camera.allowMSAA = true;
        camera.transform.SetPositionAndRotation(position, Quaternion.Euler(rotation));
        var data = camera.GetUniversalAdditionalCameraData();
        data.allowXRRendering = false;
        if (source != null && source.TryGetComponent<UniversalAdditionalCameraData>(out var sourceData))
        {
            data.renderPostProcessing = sourceData.renderPostProcessing;
            data.volumeLayerMask = sourceData.volumeLayerMask;
            data.dithering = sourceData.dithering;
        }
        var oldTarget = RenderTexture.active;
        RenderTexture target = null;
        Texture2D texture = null;
        try
        {
            target = RenderTexture.GetTemporary(width, height, 24, RenderTextureFormat.ARGB32);
            camera.targetTexture = target;
            camera.Render();
            RenderTexture.active = target;
            texture = new Texture2D(width, height, TextureFormat.RGB24, false);
            texture.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            texture.Apply();
            File.WriteAllBytes(Path.Combine(OutputDirectory, name + ".png"), texture.EncodeToPNG());
        }
        finally
        {
            RenderTexture.active = oldTarget;
            camera.targetTexture = null;
            if (texture != null) UnityEngine.Object.DestroyImmediate(texture);
            if (target != null) RenderTexture.ReleaseTemporary(target);
            UnityEngine.Object.DestroyImmediate(go);
        }
    }
}
