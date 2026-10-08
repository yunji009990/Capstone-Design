using System;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

public static class Scene2PlanarReflectionSetup
{
    const string AssetsRoot = "Assets/Graphics/Scene2";
    const string UndoName = "카페 평면 반사 구성";

    [MenuItem("Tools/다시봄/그래픽/평면 반사 적용")]
    public static void Apply()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("베이크 완료 후 적용하세요.");
        Configure();
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        SceneView.RepaintAll();
    }

    internal static void Configure()
    {
        var shader = Shader.Find("AgainSpring/Cafe Planar Surface");
        if (shader == null || !shader.isSupported) throw new InvalidOperationException("평면 반사 셰이더를 확인하세요.");
        var root = Scene2GraphicsCapture.SceneObject("05_조명과 날씨");
        var group = Child(root.transform, "창과 거울의 평면 반사");
        var owner = group.TryGetComponent<CafePlanarReflection>(out var current) ? current : Undo.AddComponent<CafePlanarReflection>(group);
        Undo.RecordObject(owner, UndoName);
        var renderers = Scene2GraphicsCapture.CafeRenderers();
        var windows = renderers.Where(r => r.name == "window_glass_l1" || r.name == "window_glass_r").ToArray();
        var mirrors = renderers.Where(r => r.sharedMaterials.Any(m => m != null &&
            (m.name == "Mirror" || m.name == "PlanarMirror" || m.name == "OvalWindowGlass"))).ToArray();
        if (windows.Length != 2 || mirrors.Length == 0) throw new InvalidOperationException("카페의 평면 표면을 확인하세요.");
        if (windows.Max(r => r.bounds.min.z) - windows.Min(r => r.bounds.min.z) > .01f ||
            mirrors.Max(r => r.bounds.max.x) - mirrors.Min(r => r.bounds.max.x) > .01f)
            throw new InvalidOperationException("서로 다른 평면의 창·거울을 한 표면으로 공유할 수 없습니다.");
        var windowMaterial = Material("WindowGlass", shader, .006f, .035f, new Color(.96f, .985f, 1f, .003f), .6f);
        string oldMaterial = AssetsRoot + "/Materials/PlanarMirror.mat";
        string newMaterial = AssetsRoot + "/Materials/OvalWindowGlass.mat";
        if (AssetDatabase.LoadAssetAtPath<Material>(newMaterial) == null && AssetDatabase.LoadAssetAtPath<Material>(oldMaterial) != null)
        {
            string error = AssetDatabase.MoveAsset(oldMaterial, newMaterial);
            if (!string.IsNullOrEmpty(error)) throw new InvalidOperationException(error);
        }
        var mirrorMaterial = Material("OvalWindowGlass", shader, .075f, .16f, new Color(.97f, .99f, 1f, .003f), .8f, .35f);
        Assign(windows, windowMaterial, new[] { "Glass", "WindowGlass" });
        Assign(mirrors, mirrorMaterial, new[] { "Mirror", "PlanarMirror", "OvalWindowGlass" });
        ConfigureDoor(renderers);
        var windowPlane = Child(group.transform, "앞쪽 창 평면").transform;
        Undo.RecordObject(windowPlane, UndoName);
        windowPlane.SetPositionAndRotation(new Vector3(-1.4f, 1.3f, windows.Average(r => r.bounds.min.z)),
            Quaternion.LookRotation(Vector3.back, Vector3.up));
        var mirrorPlane = Child(group.transform, "왼쪽 벽 거울 평면").transform;
        Undo.RecordObject(mirrorPlane, UndoName);
        mirrorPlane.SetPositionAndRotation(new Vector3(mirrors.Average(r => r.bounds.max.x), 1.5f, -2.2f),
            Quaternion.LookRotation(Vector3.right, Vector3.up));
        owner.Configure(new[]
        {
            new CafePlanarReflection.Surface { plane = windowPlane, renderers = windows },
            new CafePlanarReflection.Surface { plane = mirrorPlane, renderers = mirrors }
        });
        Scene2OvalWindowOpenings.EnsureConfigured();
    }

    static void Assign(Renderer[] renderers, Material material, string[] names)
    {
        foreach (var renderer in renderers)
        {
            Undo.RecordObject(renderer, UndoName);
            renderer.sharedMaterials = renderer.sharedMaterials.Select(m => m != null && names.Contains(m.name) ? material : m).ToArray();
            renderer.reflectionProbeUsage = ReflectionProbeUsage.Off;
            renderer.shadowCastingMode = ShadowCastingMode.Off;
            renderer.receiveShadows = false;
            GameObjectUtility.SetStaticEditorFlags(renderer.gameObject,
                GameObjectUtility.GetStaticEditorFlags(renderer.gameObject) & ~StaticEditorFlags.ContributeGI);
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer.gameObject);
        }
    }

    static void ConfigureDoor(Renderer[] renderers)
    {
        // 열린 문은 앞 창과 다른 평면이다. 앞 창의 영상을 문에 공유하지 않는다.
        var source = AssetDatabase.LoadAssetAtPath<Material>(AssetsRoot + "/Materials/Glass.mat");
        if (source == null) throw new InvalidOperationException("유리 원본 재질이 없습니다.");
        var path = AssetsRoot + "/Materials/ClearDoorGlass.mat";
        var material = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (material == null)
        {
            material = new Material(source);
            AssetDatabase.CreateAsset(material, path);
        }
        Undo.RecordObject(material, UndoName);
        material.CopyPropertiesFromMaterial(source);
        material.name = "ClearDoorGlass";
        material.SetFloat("_Blend", 0f);
        material.SetFloat("_BlendModePreserveSpecular", 0f);
        material.SetFloat("_EnvironmentReflections", 0f);
        material.SetFloat("_ReceiveShadows", 0f);
        material.SetColor("_BaseColor", new Color(.96f, .985f, 1f, .01f));
        BaseShaderGUI.SetMaterialKeywords(material, UnityEditor.Rendering.Universal.ShaderGUI.LitGUI.SetMaterialKeywords);
        EditorUtility.SetDirty(material);
        Assign(renderers.Where(r => r.name == "door_glass").ToArray(), material,
            new[] { "Glass", "WindowGlass", "ClearDoorGlass" });
    }

    static Material Material(string name, Shader shader, float frontal, float grazing, Color color,
        float gain = .9f, float highlightCompression = 0f)
    {
        var path = AssetsRoot + "/Materials/" + name + ".mat";
        var material = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (material == null)
        {
            material = new Material(shader) { name = name };
            AssetDatabase.CreateAsset(material, path);
        }
        Undo.RecordObject(material, UndoName);
        material.shader = shader;
        material.name = name;
        material.shaderKeywords = Array.Empty<string>();
        material.SetColor("_BaseColor", color);
        material.SetFloat("_ReflectionOpacity", frontal);
        material.SetFloat("_GrazingOpacity", grazing);
        material.SetFloat("_ReflectionGain", gain);
        material.SetFloat("_HighlightCompression", highlightCompression);
        material.SetFloat("_PlanarValid", 0f);
        material.SetFloat("_Cull", 2f);
        material.renderQueue = 3000;
        EditorUtility.SetDirty(material);
        return material;
    }

    static GameObject Child(Transform parent, string name)
    {
        var child = parent.Find(name);
        if (child != null) return child.gameObject;
        var go = new GameObject(name);
        go.transform.SetParent(parent, false);
        Undo.RegisterCreatedObjectUndo(go, UndoName);
        return go;
    }

    [MenuItem("Tools/다시봄/그래픽/평면 반사 기하 검사")]
    public static void ValidateGeometry()
    {
        Scene2GraphicsCapture.RequireScene();
        var reports = new System.Collections.Generic.List<object>();
        foreach (var normal in new[] { Vector3.back, Vector3.right })
        foreach (float eyeOffset in new[] { -.032f, .032f })
        {
            var plane = normal == Vector3.back ? new Vector3(0f, 0f, 1.71f) : new Vector3(-3.88f, 0f, 0f);
            var eye = new Vector3(-2f + eyeOffset, 1.55f, -.5f);
            var rotation = normal == Vector3.back ? Quaternion.identity : Quaternion.Euler(0f, -75f, 0f);
            var view = Matrix4x4.Scale(new Vector3(1f, 1f, -1f)) * Matrix4x4.TRS(eye, rotation, Vector3.one).inverse;
            var reflection = CafePlanarReflection.ReflectionMatrix(new Vector4(normal.x, normal.y, normal.z, -Vector3.Dot(normal, plane)));
            var projection = Matrix4x4.Perspective(70f, 1280f / 720f, .05f, 100f);
            foreach (var point in new[] { new Vector3(-3f, 1f, -4.5f), new Vector3(.5f, 2f, -3f) })
            {
                // 물체의 허상 위치는 평면까지의 거리로 별도 계산한다.
                var virtualPoint = point - 2f * Vector3.Dot(normal, point - plane) * normal;
                var expected = projection * view * new Vector4(virtualPoint.x, virtualPoint.y, virtualPoint.z, 1f);
                var actual = projection * view * reflection * new Vector4(point.x, point.y, point.z, 1f);
                var delta = new Vector2(actual.x / actual.w - expected.x / expected.w, actual.y / actual.w - expected.y / expected.w);
                if (delta.magnitude > .00001f) throw new InvalidOperationException("평면 반사 배율·투영이 일치하지 않습니다.");
                reports.Add(new { plane = normal.ToString(), eyeOffset, projectionError = delta.magnitude });
            }
        }
        Directory.CreateDirectory(Scene2GraphicsCapture.OutputDirectory);
        File.WriteAllText(Path.Combine(Scene2GraphicsCapture.OutputDirectory, "planar_geometry.json"),
            JsonConvert.SerializeObject(new { passed = true, cases = reports.Count, reports }, Formatting.Indented));
        Debug.Log("[Scene2Graphics] 평면 반사: 두 평면·좌우 눈·표본 물체의 실제 투영 배율 일치.");
    }
}
