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
        "tools", "_work", "graphics_20261007");

    [MenuItem("Tools/다시봄/그래픽/변경 전 촬영·검사")]
    public static void CaptureBefore() => CaptureAndInspect("before");

    [MenuItem("Tools/다시봄/그래픽/변경 후 촬영·검사")]
    public static void CaptureAfter() => CaptureAndInspect("after");

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
            emission = Components(material.HasProperty("_EmissionColor") ? material.GetColor("_EmissionColor") : Color.black)
        };
    }

    static void CaptureAndInspect(string stage)
    {
        var scene = RequireScene();
        Directory.CreateDirectory(OutputDirectory);
        var renderers = CafeRenderers();
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
