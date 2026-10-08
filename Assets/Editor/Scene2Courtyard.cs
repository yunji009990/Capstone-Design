// 창밖 근경을 실제 크기의 마당으로 구성한다. 실행 중 생성하거나 운영 서버를 사용하지 않는다.
using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;

public static class Scene2Courtyard
{
    const string RootName = "카페 앞 정원";
    const string AssetsRoot = "Assets/Graphics/Scene2";
    const string UndoName = "Scene_2 창밖 마당 구성";
    const float FloorY = .0004f;

    [MenuItem("Tools/다시봄/그래픽/창밖 마당 적용")]
    public static void Apply()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("조명 베이크가 끝난 뒤 적용하세요.");
        Undo.IncrementCurrentGroup();
        int undoGroup = Undo.GetCurrentGroup();
        Undo.SetCurrentGroupName(UndoName);
        ConfigureEnvironment();
        ConfigureGarden();
        DynamicGI.UpdateEnvironment();
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Undo.CollapseUndoOperations(undoGroup);
        SceneView.RepaintAll();
        Debug.Log("[Scene2Graphics] 창밖 마당 적용 완료. 변경된 하늘·마당을 조명과 반사에 다시 베이크하세요.");
    }

    internal static void ConfigureEnvironment()
    {
        var shader = Shader.Find("Skybox/Procedural");
        if (shader == null || !shader.isSupported)
            throw new InvalidOperationException("Unity 기본 Procedural Skybox 셰이더를 확인하세요.");
        var sky = Material("CafeDaylightSky", shader);
        sky.shader = shader;
        sky.SetColor("_SkyTint", new Color(.62f, .65f, .67f));
        sky.SetColor("_GroundColor", new Color(.49f, .48f, .44f));
        sky.SetFloat("_AtmosphereThickness", .85f);
        sky.SetFloat("_Exposure", .95f);
        sky.SetFloat("_SunDisk", 1f);
        sky.SetFloat("_SunSize", .025f);
        EditorUtility.SetDirty(sky);
        RenderSettings.skybox = sky;
        RenderSettings.fog = false;
    }

    internal static void ConfigureGarden()
    {
        var background = Scene2GraphicsCapture.SceneObject("04_카페 배경");
        if (background == null) throw new InvalidOperationException("카페 배경 그룹을 찾지 못했습니다.");
        var root = Child(background.transform, RootName).transform;
        Undo.RecordObject(root, UndoName);
        root.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
        root.localScale = Vector3.one;

        var grout = Lit("GardenGrout", new Color(.30f, .29f, .26f), .10f);
        var plaster = Lit("GardenPlaster", new Color(.72f, .70f, .63f), .15f);
        var soil = Lit("GardenSoil", new Color(.12f, .10f, .075f), .08f);
        var wood = Copy("GardenWood", Scene2GraphicsCapture.CafeAssets + "Models/Materials/Outside_Wall.mat");
        wood.SetColor("_BaseColor", new Color(.68f, .68f, .64f));
        wood.SetFloat("_Smoothness", .22f);
        wood.SetFloat("_Metallic", 0f);
        wood.SetTexture("_MetallicGlossMap", null);
        wood.SetTextureScale("_BaseMap", new Vector2(2f, 1f));
        Validate(wood);

        // 기존 테라스 상단과 같은 y=.0004. 사진 속 도로의 높이·원근은 사용하지 않는다.
        Box(root, "포장 바닥 받침", new Vector3(-1.4f, FloorY - .064f, 7.685f),
            new Vector3(12f, .12f, 8.03f), grout);
        Paving(root);
        Box(root, "뒤편 목재 담장", new Vector3(-1.4f, 1.08f, 11.6f), new Vector3(12.4f, 2.16f, .16f), wood);
        Box(root, "왼쪽 낮은 담장", new Vector3(-7.45f, .96f, 7.5f), new Vector3(.20f, 1.92f, 8.4f), plaster);
        Box(root, "오른쪽 낮은 담장", new Vector3(4.65f, .96f, 7.5f), new Vector3(.20f, 1.92f, 8.4f), plaster);
        for (int i = 0; i < 7; i++)
            Box(root, "담장 기둥 " + i, new Vector3(-7.4f + i * 2f, 1.09f, 11.48f),
                new Vector3(.075f, 2.18f, .10f), wood);
        Box(root, "담장 윗대", new Vector3(-1.4f, 2.16f, 11.52f), new Vector3(12.4f, .08f, .22f), wood);

        var plants = Copy("GardenPlants", AssetsRoot + "/Materials/Plants.mat");
        plants.SetColor("_BaseColor", new Color(.86f, .94f, .84f));
        plants.SetFloat("_Cull", 0f);
        plants.SetFloat("_Smoothness", .16f);
        plants.doubleSidedGI = true;
        Validate(plants);
        for (int bay = 0; bay < 2; bay++)
        {
            float x = bay == 0 ? -4.2f : 2.0f;
            Box(root, "뒤편 화단 " + bay, new Vector3(x, .22f, 10.60f), new Vector3(4.9f, .44f, 1.25f), plaster);
            Box(root, "화단 흙 " + bay, new Vector3(x, .445f, 10.60f), new Vector3(4.75f, .015f, 1.1f), soil);
            for (int i = 0; i < 6; i++)
            {
                var foliage = Prefab(root, "화단 식물 " + bay + "-" + i, "Flower_Pot",
                    new Vector3(x - 2f + i * .8f, .45f, 10.55f + (i % 2) * .14f),
                    37f * i + 20f * bay, 1.45f + (i % 3) * .13f);
                // 공개 화분 자산의 식물 메시를 사용하고 화분 외피는 화단 안에서 숨긴다.
                if (foliage.TryGetComponent<Renderer>(out var pot))
                {
                    Undo.RecordObject(pot, UndoName);
                    pot.enabled = false;
                }
                AssignPlants(foliage, plants);
                AlignToGround(foliage, .45f);
            }
        }
        for (int i = 0; i < 15; i++)
        {
            var ivy = Prefab(root, "담장 담쟁이 " + i, i % 3 == 0 ? "Ivy_Middle" : "Ivy_long",
                new Vector3(-6.95f + i * .78f, 2.19f, 11.37f), (i % 3 - 1) * 22f,
                1.3f + (i % 4) * .18f);
            AssignPlants(ivy, plants);
        }

        TableSet(root, "창가 야외 자리", new Vector3(.1f, FloorY, 6.15f), 18f);
        TableSet(root, "왼쪽 야외 자리", new Vector3(-4.2f, FloorY, 7.4f), -12f);
        var nearPot = Prefab(root, "테라스 오른쪽 화분", "Flower_Pot", new Vector3(.95f, FloorY, 3.22f), 25f, .88f);
        AssignPlants(nearPot, plants);
        AlignToGround(nearPot, FloorY);
        nearPot = Prefab(root, "테라스 왼쪽 화분", "Flower_Pot", new Vector3(-3.85f, FloorY, 3.25f), -32f, .9f);
        AssignPlants(nearPot, plants);
        AlignToGround(nearPot, FloorY);

        foreach (var renderer in root.GetComponentsInChildren<MeshRenderer>(true))
        {
            Undo.RecordObject(renderer, UndoName);
            Undo.RecordObject(renderer.gameObject, UndoName);
            GameObjectUtility.SetStaticEditorFlags(renderer.gameObject,
                StaticEditorFlags.ContributeGI | StaticEditorFlags.BatchingStatic);
            renderer.receiveGI = ReceiveGI.Lightmaps;
            renderer.scaleInLightmap = .25f;
            renderer.shadowCastingMode = ShadowCastingMode.TwoSided;
            renderer.lightProbeUsage = LightProbeUsage.BlendProbes;
            renderer.reflectionProbeUsage = ReflectionProbeUsage.BlendProbes;
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer.gameObject);
        }
    }

    static void TableSet(Transform root, string name, Vector3 position, float angle)
    {
        var rotation = Quaternion.Euler(0f, angle, 0f);
        var table = Prefab(root, name + " 테이블", "Table", position, angle, 1f);
        AlignToGround(table, FloorY);
        var chair = Prefab(root, name + " 의자 A", "Chair", position + rotation * new Vector3(0f, 0f, -.72f), angle, 1f);
        AlignToGround(chair, FloorY);
        chair = Prefab(root, name + " 의자 B", "Chair", position + rotation * new Vector3(0f, 0f, .72f), angle + 180f, 1f);
        AlignToGround(chair, FloorY);
    }

    static void Paving(Transform root)
    {
        const string path = AssetsRoot + "/GardenPaving.asset";
        var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
        if (mesh == null)
        {
            mesh = new Mesh { name = "GardenPaving" };
            AssetDatabase.CreateAsset(mesh, path);
        }
        Undo.RecordObject(mesh, UndoName);
        mesh.Clear();
        var vertices = new List<Vector3>();
        var uv = new List<Vector2>();
        var triangles = new[] { new List<int>(), new List<int>(), new List<int>(), new List<int>() };
        for (int row = 0; row < 11; row++)
        for (int col = 0; col < 13; col++)
        {
            float x0 = Mathf.Max(-7.4f, -7.4f + col - (row % 2) * .5f) + .004f;
            float x1 = Mathf.Min(4.6f, -6.4f + col - (row % 2) * .5f) - .004f;
            float z0 = 3.67f + row * .73f + .004f;
            float z1 = Mathf.Min(11.70f, 3.67f + (row + 1) * .73f) - .004f;
            if (x1 <= x0 || z1 <= z0) continue;
            int first = vertices.Count;
            vertices.AddRange(new[] { new Vector3(x0, FloorY, z0), new Vector3(x0, FloorY, z1),
                new Vector3(x1, FloorY, z1), new Vector3(x1, FloorY, z0) });
            uv.AddRange(new[] { new Vector2(x0, z0), new Vector2(x0, z1), new Vector2(x1, z1), new Vector2(x1, z0) });
            triangles[(row * 7 + col * 3) % 4].AddRange(new[] { first, first + 1, first + 2, first, first + 2, first + 3 });
        }
        mesh.SetVertices(vertices);
        mesh.SetUVs(0, uv);
        mesh.subMeshCount = 4;
        for (int i = 0; i < 4; i++) mesh.SetTriangles(triangles[i], i);
        mesh.RecalculateNormals();
        mesh.RecalculateBounds();
        Unwrapping.GenerateSecondaryUVSet(mesh);
        EditorUtility.SetDirty(mesh);
        var paving = Child(root, "마당 석재 포장");
        var filter = paving.TryGetComponent<MeshFilter>(out var existingFilter) ? existingFilter : Undo.AddComponent<MeshFilter>(paving);
        var renderer = paving.TryGetComponent<MeshRenderer>(out var existingRenderer) ? existingRenderer : Undo.AddComponent<MeshRenderer>(paving);
        Undo.RecordObjects(new UnityEngine.Object[] { filter, renderer }, UndoName);
        filter.sharedMesh = mesh;
        renderer.sharedMaterials = Enumerable.Range(0, 4).Select(i =>
            Lit("GardenPaver" + i, new Color(.64f + i * .012f, .625f + i * .011f, .58f + i * .010f), .20f)).ToArray();
    }

    static GameObject Prefab(Transform parent, string name, string asset, Vector3 position, float angle, float scale)
    {
        var found = parent.Find(name);
        GameObject go;
        if (found != null) go = found.gameObject;
        else
        {
            var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(Scene2GraphicsCapture.CafeAssets + "Models/Prefab/" + asset + ".prefab");
            if (prefab == null) throw new InvalidOperationException("공개 카페 소품이 없습니다: " + asset);
            go = (GameObject)PrefabUtility.InstantiatePrefab(prefab, parent);
            go.name = name;
            Undo.RegisterCreatedObjectUndo(go, UndoName);
        }
        Undo.RecordObject(go.transform, UndoName);
        go.transform.SetPositionAndRotation(position, Quaternion.Euler(0f, angle, 0f));
        go.transform.localScale = Vector3.one * scale;
        PrefabUtility.RecordPrefabInstancePropertyModifications(go.transform);
        return go;
    }

    static void AlignToGround(GameObject go, float floor)
    {
        var renderers = go.GetComponentsInChildren<Renderer>().Where(r => r.enabled).ToArray();
        if (renderers.Length == 0) return;
        go.transform.position += Vector3.up * (floor - renderers.Min(r => r.bounds.min.y));
        PrefabUtility.RecordPrefabInstancePropertyModifications(go.transform);
    }

    static void AssignPlants(GameObject go, Material material)
    {
        foreach (var renderer in go.GetComponentsInChildren<Renderer>(true))
        {
            Undo.RecordObject(renderer, UndoName);
            renderer.sharedMaterials = renderer.sharedMaterials.Select(m => m != null && m.name == "Plants" ? material : m).ToArray();
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
        }
    }

    static GameObject Child(Transform parent, string name)
    {
        var found = parent.Find(name);
        if (found != null) return found.gameObject;
        var go = new GameObject(name);
        go.transform.SetParent(parent, false);
        Undo.RegisterCreatedObjectUndo(go, UndoName);
        return go;
    }

    static void Box(Transform parent, string name, Vector3 position, Vector3 scale, Material material)
    {
        var found = parent.Find(name);
        var go = found != null ? found.gameObject : GameObject.CreatePrimitive(PrimitiveType.Cube);
        if (found == null)
        {
            go.name = name;
            go.transform.SetParent(parent, false);
            Undo.RegisterCreatedObjectUndo(go, UndoName);
        }
        Undo.RecordObject(go.transform, UndoName);
        go.transform.SetPositionAndRotation(position, Quaternion.identity);
        go.transform.localScale = scale;
        var renderer = go.GetComponent<MeshRenderer>();
        Undo.RecordObject(renderer, UndoName);
        renderer.sharedMaterial = material;
    }

    static Material Material(string name, Shader shader)
    {
        string folder = shader.name.StartsWith("Skybox/", StringComparison.Ordinal) ? AssetsRoot : AssetsRoot + "/Materials";
        var material = AssetDatabase.LoadAssetAtPath<Material>(folder + "/" + name + ".mat");
        if (material == null)
        {
            material = new Material(shader) { name = name };
            AssetDatabase.CreateAsset(material, folder + "/" + name + ".mat");
        }
        Undo.RecordObject(material, UndoName);
        return material;
    }

    static Material Copy(string name, string sourcePath)
    {
        var source = AssetDatabase.LoadAssetAtPath<Material>(sourcePath);
        if (source == null) throw new InvalidOperationException("원본 재질이 없습니다: " + sourcePath);
        var material = Material(name, source.shader);
        material.CopyPropertiesFromMaterial(source);
        material.name = name;
        return material;
    }

    static Material Lit(string name, Color color, float smoothness)
    {
        var material = Material(name, Shader.Find("Universal Render Pipeline/Lit"));
        material.SetColor("_BaseColor", color);
        material.SetFloat("_Smoothness", smoothness);
        if (name.StartsWith("GardenPaver", StringComparison.Ordinal) || name == "GardenPlaster")
        {
            // 보유한 선형 노이즈를 약한 표면 얼룩으로 사용한다. 원본 텍스처는 변경하지 않는다.
            material.SetTexture("_DetailAlbedoMap", AssetDatabase.LoadAssetAtPath<Texture2D>(
                "Assets/Feel/MMTools/Accessories/MMVFX/MMNoise/MMPerlinNoise.png"));
            material.SetTextureScale("_DetailAlbedoMap", name == "GardenPlaster" ? new Vector2(3f, 2f) : Vector2.one * 1.4f);
            material.SetFloat("_DetailAlbedoMapScale", name == "GardenPlaster" ? .08f : .12f);
            material.SetFloat("_DetailNormalMapScale", 0f);
            material.EnableKeyword("_DETAIL_SCALED");
            material.DisableKeyword("_DETAIL_MULX2");
        }
        Validate(material);
        return material;
    }

    static void Validate(Material material)
    {
        BaseShaderGUI.SetMaterialKeywords(material, UnityEditor.Rendering.Universal.ShaderGUI.LitGUI.SetMaterialKeywords);
        EditorUtility.SetDirty(material);
    }
}
