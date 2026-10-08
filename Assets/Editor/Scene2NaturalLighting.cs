// Scene_2의 자연광·재질을 준비하는 Editor 전용 도구. 운영 서버를 사용하지 않는다.
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

public static class Scene2NaturalLighting
{
    const string AssetsRoot = "Assets/Graphics/Scene2";
    const string GroupName = "자연광과 실내 반사";
    const string UndoName = "Scene_2 자연광 구성";

    [MenuItem("Tools/다시봄/그래픽/자연광·재질 적용")]
    public static void Apply()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("조명 베이크가 끝난 뒤 적용하세요.");
        EnsureFolder(AssetsRoot + "/Materials");

        Undo.IncrementCurrentGroup();
        int undoGroup = Undo.GetCurrentGroup();
        Undo.SetCurrentGroupName(UndoName);
        var lightingRoot = Scene2GraphicsCapture.SceneObject("05_조명과 날씨");
        if (lightingRoot == null) throw new InvalidOperationException("Scene_2 조명 그룹을 찾지 못했습니다.");
        var group = Child(lightingRoot.transform, GroupName);

        // COZY의 매 프레임 색·안개 갱신과 구름 메시를 함께 중지한다.
        // 원본 컴포넌트와 프로필은 그대로 두어 기존 연출을 다시 선택할 수 있다.
        var weather = Scene2GraphicsCapture.SceneObject("하늘과 날씨 (Cozy Weather Sphere)");
        if (weather != null)
        {
            Undo.RecordObject(weather, UndoName);
            weather.SetActive(false);
        }
        Scene2Courtyard.ConfigureEnvironment();
        Scene2Courtyard.ConfigureGarden();
        RenderSettings.fog = false;
        RenderSettings.ambientMode = AmbientMode.Skybox;
        RenderSettings.ambientIntensity = .85f;
        RenderSettings.defaultReflectionMode = DefaultReflectionMode.Skybox;
        RenderSettings.defaultReflectionResolution = 256;
        RenderSettings.reflectionIntensity = .8f;
        RenderSettings.reflectionBounces = 2;

        var sun = Component<Light>(Child(group.transform, "창가 자연광"));
        Undo.RecordObjects(new UnityEngine.Object[] { sun, sun.transform }, UndoName);
        sun.type = LightType.Directional;
        sun.transform.rotation = Quaternion.Euler(38f, 155f, 0f);
        sun.color = Color.white;
        sun.useColorTemperature = true;
        sun.colorTemperature = 5500f;
        sun.intensity = 1.25f;
        sun.bounceIntensity = 1f;
        sun.lightmapBakeType = LightmapBakeType.Mixed;
        sun.shadows = LightShadows.Soft;
        sun.shadowBias = .035f;
        sun.shadowNormalBias = .22f;
        sun.enabled = true;
        RenderSettings.sun = sun;

        ConfigureIndoorLights(lightingRoot, group);
        ConfigureMaterials();
        ConfigureWindowGlass();
        ConfigureStaticGeometry();
        ConfigureProbes(group);
        ConfigureReflectionResolution();
        ConfigureVolume(group);
        ConfigureCameras();
        ConfigureBakeSettings();
        DynamicGI.UpdateEnvironment();
        EditorSceneManager.MarkSceneDirty(scene);
        AssetDatabase.SaveAssets();
        EditorSceneManager.SaveScene(scene);
        Undo.CollapseUndoOperations(undoGroup);
        SceneView.RepaintAll();
        Debug.Log("[Scene2Graphics] 자연광·실내 재질 적용 완료. 다음으로 조명 베이크를 실행하세요.");
    }

    static void ConfigureIndoorLights(GameObject lightingRoot, GameObject ownedGroup)
    {
        foreach (var light in lightingRoot.GetComponentsInChildren<Light>(true))
        {
            if (!light.gameObject.activeInHierarchy || !light.enabled ||
                light.transform.IsChildOf(ownedGroup.transform) || light.type == LightType.Directional ||
                light.name == "인물 빛")
                continue;
            // 대화 음성에 맞춰 켜지는 인물 빛은 이 그룹 밖에 있고 변경하지 않는다.
            Undo.RecordObject(light, UndoName);
            light.lightmapBakeType = LightmapBakeType.Baked;
            light.color = Color.white;
            light.useColorTemperature = true;
            light.colorTemperature = 3400f;
            light.bounceIntensity = 1f;
            if (light.type == LightType.Point)
            {
                light.intensity = .55f;
                light.range = Mathf.Max(light.range, 2.2f);
            }
            else if (light.type == LightType.Spot)
            {
                light.intensity = .9f;
                light.range = Mathf.Max(light.range, 2.5f);
            }
            else
            {
                light.intensity = .65f;
                light.areaSize = new Vector2(.8f, .8f);
            }
            light.shadows = LightShadows.Soft;
            EditorUtility.SetDirty(light);
        }
    }

    static void ConfigureMaterials()
    {
        // 원본 재질을 보존하고 Scene_2에서 조정할 재질만 복제한다.
        var smoothness = new Dictionary<string, float>
        {
            ["Furniture"] = .34f, ["Doors_Windows"] = .32f,
            ["Internal_Walls_d"] = .22f, ["Baseboards"] = .24f,
            ["Floor_Tiles"] = .28f, ["Floors_Hall"] = .28f,
            ["Plants"] = .22f, ["Ground"] = .12f,
            ["Coffee_Machine"] = .68f, ["Coffee Grinder"] = .55f,
            ["Package_A"] = .2f, ["Package_B"] = .2f,
            ["Croissant_Pistachios"] = .18f, ["Croissant_Cream"] = .18f,
            ["Blueberry_Pie"] = .2f, ["Mirror"] = .96f,
            ["Glass"] = .92f
        };
        var copies = new Dictionary<Material, Material>();
        foreach (var renderer in Scene2GraphicsCapture.CafeRenderers())
        {
            var materials = renderer.sharedMaterials;
            bool changed = false;
            for (int i = 0; i < materials.Length; i++)
            {
                var original = materials[i];
                if (original == null || !smoothness.TryGetValue(original.name, out var value)) continue;
                string sourcePath = AssetDatabase.GetAssetPath(original);
                if (!sourcePath.StartsWith(Scene2GraphicsCapture.CafeAssets, StringComparison.Ordinal) &&
                    !sourcePath.StartsWith(AssetsRoot + "/Materials/", StringComparison.Ordinal)) continue;
                if (!copies.TryGetValue(original, out var material))
                {
                    material = CloneMaterial(original, AssetsRoot + "/Materials/" + original.name + ".mat");
                    material.SetFloat("_Smoothness", value);
                    if (material.name == "Ground") material.SetFloat("_Metallic", 0f);
                    if (material.name == "Glass")
                    {
                        // 옅은 유리 색과 낮은 불투명도를 사용한다.
                        material.SetColor("_BaseColor", new Color(.92f, .97f, .98f, .08f));
                    }
                    if (material.name == "Plants") material.doubleSidedGI = true;
                    ValidateLitMaterial(material);
                    EditorUtility.SetDirty(material);
                    copies.Add(original, material);
                }
                materials[i] = material;
                changed |= original != material;
            }
            if (!changed) continue;
            Undo.RecordObject(renderer, UndoName);
            renderer.sharedMaterials = materials;
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
        }
    }

    static void ConfigureStaticGeometry()
    {
        foreach (var renderer in Scene2GraphicsCapture.CafeRenderers().OfType<MeshRenderer>())
        {
            if (!renderer.gameObject.activeInHierarchy) continue;
            var flags = GameObjectUtility.GetStaticEditorFlags(renderer.gameObject);
            var materials = renderer.sharedMaterials.Where(m => m != null).ToArray();
            bool transparent = materials.Length > 0 && materials.All(m => m.renderQueue >= 3000);
            bool largeGround = renderer.bounds.size.x > 20f || renderer.bounds.size.z > 20f;
            bool movable = renderer.GetComponentInParent<Rigidbody>() != null ||
                           renderer.GetComponentInParent<Animator>() != null;
            Undo.RecordObject(renderer.gameObject, UndoName);
            Undo.RecordObject(renderer, UndoName);
            // 거대한 외부 평면·유리·움직이는 소품은 실내 라이트맵을 차지하지 않는다.
            if (largeGround || transparent || movable)
            {
                GameObjectUtility.SetStaticEditorFlags(renderer.gameObject, flags & ~StaticEditorFlags.ContributeGI);
                renderer.receiveGI = ReceiveGI.LightProbes;
                if (transparent) renderer.shadowCastingMode = ShadowCastingMode.Off;
            }
            else
            {
                GameObjectUtility.SetStaticEditorFlags(renderer.gameObject, flags | StaticEditorFlags.ContributeGI);
                renderer.receiveGI = ReceiveGI.Lightmaps;
                // 작은 장식까지 같은 밀도로 굽지 않아 메모리와 아틀라스 사용량을 줄인다.
                renderer.scaleInLightmap = renderer.bounds.size.magnitude < .3f ? .25f : .8f;
            }
            renderer.lightProbeUsage = LightProbeUsage.BlendProbes;
            renderer.reflectionProbeUsage = renderer.sharedMaterials.Any(m => m != null && m.name == "WindowGlass")
                ? ReflectionProbeUsage.Off : ReflectionProbeUsage.BlendProbes;
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer.gameObject);
        }
    }

    static void ConfigureProbes(GameObject group)
    {
        var probes = Component<LightProbeGroup>(Child(group.transform, "실내 캐릭터 간접광"));
        Undo.RecordObject(probes, UndoName);
        var positions = new List<Vector3>();
        foreach (float x in new[] { -3.65f, -2.6f, -1.45f, -.3f, .7f })
        foreach (float z in new[] { -5.8f, -4.6f, -3.4f, -2.2f, -1f, .2f, 1.3f })
        foreach (float y in new[] { .25f, 1f, 1.7f, 2.6f })
            positions.Add(probes.transform.InverseTransformPoint(new Vector3(x, y, z)));
        probes.probePositions = positions.ToArray();

        var reflection = Component<ReflectionProbe>(Child(group.transform, "카페 실내 반사"));
        Undo.RecordObjects(new UnityEngine.Object[] { reflection, reflection.transform }, UndoName);
        reflection.transform.position = new Vector3(-1.4f, 1.45f, -2.35f);
        reflection.mode = ReflectionProbeMode.Baked;
        reflection.resolution = 256;
        reflection.hdr = true;
        reflection.boxProjection = true;
        reflection.center = Vector3.zero;
        reflection.size = new Vector3(5.3f, 3.1f, 8.4f);
        reflection.blendDistance = .4f;
        reflection.intensity = 1f;
        reflection.nearClipPlane = .05f;
        reflection.farClipPlane = 80f;
        reflection.clearFlags = ReflectionProbeClearFlags.Skybox;
        reflection.cullingMask = ~0;
    }

    static void ValidateLitMaterial(Material material)
    {
        if (material.shader.name != "Universal Render Pipeline/Lit") return;
        if (material.name == "Glass")
        {
            // URP 14의 Premultiply는 이미 알파가 곱해진 색을 전제로 한다.
            // 곡면 진열장 유리는 투과와 반사를 분리한다. 큰 평면 창은 아래 전용 재질을 쓴다.
            material.SetFloat("_Blend", 0f);
            material.SetFloat("_BlendModePreserveSpecular", 1f);
        }
        // 투명 블렌딩·노멀맵 키워드를 URP의 Inspector와 같은 규칙으로 검증한다.
        BaseShaderGUI.SetMaterialKeywords(material,
            UnityEditor.Rendering.Universal.ShaderGUI.LitGUI.SetMaterialKeywords);
    }

    [MenuItem("Tools/다시봄/그래픽/창 유리 반사 수정")]
    public static void ApplyWindowGlass()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("조명 베이크가 끝난 뒤 적용하세요.");
        ConfigureWindowGlass();
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        SceneView.RepaintAll();
    }

    static void ConfigureWindowGlass()
    {
        EnsureFolder(AssetsRoot + "/Materials");
        var source = AssetDatabase.LoadAssetAtPath<Material>(AssetsRoot + "/Materials/Glass.mat") ??
            AssetDatabase.LoadAssetAtPath<Material>(Scene2GraphicsCapture.CafeAssets + "Models/Materials/Glass.mat");
        if (source == null) throw new InvalidOperationException("카페 유리 원본 재질을 찾지 못했습니다.");
        var window = CloneMaterial(source, AssetsRoot + "/Materials/WindowGlass.mat");
        window.name = "WindowGlass";
        window.SetFloat("_Surface", 1f);
        window.SetFloat("_Blend", 0f);
        window.SetFloat("_BlendModePreserveSpecular", 0f);
        window.SetFloat("_EnvironmentReflections", 0f);
        window.SetFloat("_ReceiveShadows", 0f);
        window.SetFloat("_Metallic", 0f);
        window.SetFloat("_Smoothness", .86f);
        window.SetColor("_BaseColor", new Color(.96f, .985f, 1f, .03f));
        // 정적인 실내 큐브맵은 큰 창의 평면 반사와 위치가 맞지 않는다.
        // 창에서는 공간 이미지 반사를 끄고, 약한 색·빛만 알파에 비례해 남긴다.
        ValidateLitMaterial(window);
        EditorUtility.SetDirty(window);
        foreach (var renderer in Scene2GraphicsCapture.CafeRenderers().Where(r =>
            r.name == "door_glass" || r.name == "window_glass_l1" || r.name == "window_glass_r"))
        {
            Undo.RecordObject(renderer, UndoName);
            renderer.sharedMaterials = renderer.sharedMaterials.Select(m =>
                m != null && (m.name == "Glass" || m.name == "WindowGlass") ? window : m).ToArray();
            renderer.reflectionProbeUsage = ReflectionProbeUsage.Off;
            renderer.shadowCastingMode = ShadowCastingMode.Off;
            renderer.receiveShadows = false;
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
        }
    }

    static ReflectionProbe[] SceneProbes() => Scene2GraphicsCapture.RequireScene().GetRootGameObjects()
        .SelectMany(root => root.GetComponentsInChildren<ReflectionProbe>(true)).ToArray();

    static void ConfigureReflectionResolution()
    {
        foreach (var probe in SceneProbes())
        {
            if (probe.resolution <= 256) continue;
            Undo.RecordObject(probe, UndoName);
            probe.resolution = 256;
            PrefabUtility.RecordPrefabInstancePropertyModifications(probe);
        }
    }

    [MenuItem("Tools/다시봄/그래픽/재질 검증·반사 해상도 정리")]
    public static void RefineMaterials()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("베이크가 끝난 뒤 실행하세요.");
        foreach (var guid in AssetDatabase.FindAssets("t:Material", new[] { AssetsRoot + "/Materials" }))
        {
            var material = AssetDatabase.LoadAssetAtPath<Material>(AssetDatabase.GUIDToAssetPath(guid));
            Undo.RecordObject(material, UndoName);
            ValidateLitMaterial(material);
            EditorUtility.SetDirty(material);
        }
        ConfigureReflectionResolution();
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Debug.Log("[Scene2Graphics] URP 재질 키워드와 반사 해상도 검증 완료.");
    }

    static void ConfigureVolume(GameObject group)
    {
        string path = AssetsRoot + "/NaturalCafeVolume.asset";
        var profile = AssetDatabase.LoadAssetAtPath<VolumeProfile>(path);
        if (profile == null)
        {
            profile = ScriptableObject.CreateInstance<VolumeProfile>();
            AssetDatabase.CreateAsset(profile, path);
        }
        Undo.RecordObject(profile, UndoName);
        var tonemapping = Effect<Tonemapping>(profile);
        tonemapping.mode.Override(TonemappingMode.Neutral);
        var color = Effect<ColorAdjustments>(profile);
        color.postExposure.Override(0f);
        color.contrast.Override(3f);
        color.saturation.Override(-3f);
        var bloom = Effect<Bloom>(profile);
        bloom.threshold.Override(1.3f);
        bloom.intensity.Override(.08f);
        bloom.scatter.Override(.55f);
        bloom.highQualityFiltering.Override(false);
        EditorUtility.SetDirty(profile);
        var volume = Component<Volume>(Child(group.transform, "자연스러운 색감"));
        Undo.RecordObject(volume, UndoName);
        volume.isGlobal = true;
        volume.priority = 5f;
        volume.weight = 1f;
        volume.sharedProfile = profile;
    }

    static T Effect<T>(VolumeProfile profile) where T : VolumeComponent
    {
        if (profile.TryGet<T>(out var value)) return value;
        value = profile.Add<T>(true);
        AssetDatabase.AddObjectToAsset(value, profile);
        return value;
    }

    static void ConfigureCameras()
    {
        var root = Scene2GraphicsCapture.SceneObject("02_VR 플레이어");
        foreach (var camera in root.GetComponentsInChildren<Camera>(true))
        {
            var data = camera.GetComponent<UniversalAdditionalCameraData>();
            if (data == null) continue;
            Undo.RecordObject(data, UndoName);
            data.renderPostProcessing = true;
            data.volumeLayerMask |= 1; // 기존 마스크를 보존하며 Default의 Volume을 포함한다.
            data.dithering = true;
            PrefabUtility.RecordPrefabInstancePropertyModifications(data);
        }
    }

    static void ConfigureBakeSettings()
    {
        string path = AssetsRoot + "/CafeLightingSettings.lighting";
        var settings = AssetDatabase.LoadAssetAtPath<LightingSettings>(path);
        if (settings == null)
        {
            settings = new LightingSettings();
            AssetDatabase.CreateAsset(settings, path);
        }
        Undo.RecordObject(settings, UndoName);
        settings.bakedGI = true;
        settings.realtimeGI = false;
        settings.autoGenerate = false;
        settings.lightmapper = LightingSettings.Lightmapper.ProgressiveCPU;
        settings.lightmapResolution = 20f;
        settings.lightmapMaxSize = 1024;
        settings.directSampleCount = 32;
        settings.indirectSampleCount = 256;
        settings.environmentSampleCount = 128;
        settings.maxBounces = 3;
        settings.mixedBakeMode = MixedLightingMode.IndirectOnly;
        settings.directionalityMode = LightmapsMode.CombinedDirectional;
        settings.lightmapCompression = LightmapCompression.HighQuality;
        settings.ao = true;
        settings.aoMaxDistance = .25f;
        settings.aoExponentIndirect = .6f;
        settings.aoExponentDirect = 0f;
        Lightmapping.lightingSettings = settings;
        EditorUtility.SetDirty(settings);
    }

    [MenuItem("Tools/다시봄/그래픽/실내 조명 베이크 시작")]
    public static void Bake()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("이미 조명을 베이크하고 있습니다.");
        if (Scene2GraphicsCapture.SceneObject(GroupName) == null)
            throw new InvalidOperationException("먼저 자연광·재질을 적용하세요.");
        AssetDatabase.SaveAssets();
        EditorSceneManager.SaveScene(scene);
        if (!Lightmapping.BakeAsync()) throw new InvalidOperationException("조명 베이크를 시작하지 못했습니다.");
        Debug.Log("[Scene2Graphics] 실내 조명 비동기 베이크 시작.");
    }

    [MenuItem("Tools/다시봄/그래픽/실내 반사 베이크·저장")]
    public static void BakeReflection()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("간접광 베이크가 끝난 뒤 실행하세요.");
        var probes = SceneProbes().Where(p => p.enabled && p.gameObject.activeInHierarchy).ToArray();
        if (probes.Length == 0) throw new InvalidOperationException("실내 반사 프로브를 찾지 못했습니다.");
        int index = 0;
        foreach (var probe in probes)
        {
            string path = probe.name == "카페 실내 반사" ? AssetsRoot + "/CafeInteriorReflection.exr" :
                AssetDatabase.GetAssetPath(probe.bakedTexture);
            // 베이크로 새로 만든 텍스처만 덮어쓰며 판매자 원본은 건드리지 않는다.
            if (string.IsNullOrEmpty(path) ||
                (!path.StartsWith("Assets/Scenes/Scene_2/", StringComparison.Ordinal) &&
                 !path.StartsWith(AssetsRoot + "/", StringComparison.Ordinal)))
                path = AssetsRoot + "/CafeReflection_" + index + ".exr";
            if (!Lightmapping.BakeReflectionProbe(probe, path))
                throw new InvalidOperationException("실내 반사 베이크에 실패했습니다: " + probe.name);
            index++;
        }
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Debug.Log("[Scene2Graphics] 실내 반사와 씬 저장 완료.");
    }

    static Material CloneMaterial(Material source, string path)
    {
        var material = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (material == null)
        {
            material = new Material(source) { name = source.name };
            AssetDatabase.CreateAsset(material, path);
        }
        Undo.RecordObject(material, UndoName);
        return material;
    }

    static GameObject Child(Transform parent, string name)
    {
        var child = parent.Find(name);
        if (child != null) return child.gameObject;
        var go = new GameObject(name);
        Undo.RegisterCreatedObjectUndo(go, UndoName);
        go.transform.SetParent(parent, false);
        return go;
    }

    static T Component<T>(GameObject go) where T : Component
        => go.TryGetComponent<T>(out var value) ? value : Undo.AddComponent<T>(go);

    static void EnsureFolder(string path)
    {
        string parent = "Assets";
        foreach (string segment in path.Split('/').Skip(1))
        {
            string next = parent + "/" + segment;
            if (!AssetDatabase.IsValidFolder(next)) AssetDatabase.CreateFolder(parent, segment);
            parent = next;
        }
    }
}
