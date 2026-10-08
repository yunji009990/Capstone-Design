// 창밖 근경을 실제 크기의 마당으로 구성한다. 실행 중 생성하거나 운영 서버를 사용하지 않는다.
using System;
using System.Collections.Generic;
using System.IO;
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

    [MenuItem("Tools/다시봄/그래픽/먼 수목 둘레 적용")]
    public static void ApplyDistantTrees()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("조명 베이크가 끝난 뒤 적용하세요.");
        DistantTrees(Scene2GraphicsCapture.SceneObject(RootName).transform);
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
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
        Box(root, "뒤편 목재 담장", new Vector3(-1.4f, .47f, 11.6f), new Vector3(12.4f, .94f, .16f), wood);
        Box(root, "왼쪽 낮은 담장", new Vector3(-7.45f, .41f, 7.5f), new Vector3(.20f, .82f, 8.4f), plaster);
        Box(root, "오른쪽 낮은 담장", new Vector3(4.65f, .41f, 7.5f), new Vector3(.20f, .82f, 8.4f), plaster);
        for (int i = 0; i < 7; i++)
            Box(root, "담장 기둥 " + i, new Vector3(-7.4f + i * 2f, .52f, 11.48f),
                new Vector3(.085f, 1.04f, .10f), wood);
        Box(root, "담장 윗대", new Vector3(-1.4f, .97f, 11.52f), new Vector3(12.4f, .08f, .22f), wood);

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
                    new Vector3(x - 2f + i * .8f + .11f * Mathf.Sin(i * 3f), .45f,
                        10.34f + .28f * Mathf.Sin(i * 2.3f + bay)),
                    67f * i + 20f * bay, .78f + (i % 4) * .21f);
                // 공개 화분 자산의 식물 메시를 사용하고 화분 외피는 화단 안에서 숨긴다.
                if (foliage.TryGetComponent<Renderer>(out var pot))
                {
                    Undo.RecordObject(pot, UndoName);
                    pot.enabled = false;
                }
                AssignPlants(foliage, plants);
                AlignToGround(foliage, .45f);
                var groundcover = Prefab(root, "화단 낮은 잎 " + bay + "-" + i, "Ivy_Short",
                    new Vector3(x - 2.1f + i * .81f, .45f, 10.04f + .10f * (i % 3)),
                    i * 41f, 1.4f + (i % 3) * .22f);
                AssignPlants(groundcover, plants);
                AlignToGround(groundcover, .45f);
            }
        }
        for (int i = 0; i < 15; i++)
        {
            var ivy = Prefab(root, "담장 담쟁이 " + i, i % 3 == 0 ? "Ivy_Middle" : "Ivy_long",
                new Vector3(-6.95f + i * .78f, 1.03f + (i % 3) * .10f, 11.37f), (i % 3 - 1) * 22f,
                .55f + (i % 4) * .13f);
            AssignPlants(ivy, plants);
        }

        TableSet(root, "창가 야외 자리", new Vector3(.65f, FloorY, 6.30f), 24f);
        TableSet(root, "왼쪽 야외 자리", new Vector3(-3.85f, FloorY, 7.05f), -18f);
        Pergola(root, wood, plants);
        Bench(root, wood);
        SideGarden(root, wood, plants, plaster, soil, grout);
        DistantTrees(root);
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
            bool distant = renderer.sharedMaterials.Any(m => m != null && m.name == "GardenDistantTrees");
            GameObjectUtility.SetStaticEditorFlags(renderer.gameObject, distant ? StaticEditorFlags.BatchingStatic :
                StaticEditorFlags.ContributeGI | StaticEditorFlags.BatchingStatic);
            renderer.receiveGI = distant ? ReceiveGI.LightProbes : ReceiveGI.Lightmaps;
            renderer.scaleInLightmap = .25f;
            renderer.shadowCastingMode = distant ? ShadowCastingMode.Off : ShadowCastingMode.TwoSided;
            renderer.lightProbeUsage = LightProbeUsage.BlendProbes;
            renderer.reflectionProbeUsage = ReflectionProbeUsage.BlendProbes;
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer);
            PrefabUtility.RecordPrefabInstancePropertyModifications(renderer.gameObject);
        }
    }

    static void Pergola(Transform root, Material wood, Material plants)
    {
        var pergola = Child(root, "목재 그늘막").transform;
        for (int x = 0; x < 2; x++)
        for (int z = 0; z < 2; z++)
            Box(pergola, "기둥 " + x + "-" + z, new Vector3(-5.55f + x * 3.35f, 1.27f, 5.6f + z * 3.4f),
                new Vector3(.13f, 2.54f, .13f), wood);
        for (int z = 0; z < 2; z++)
            Box(pergola, "지붕 보 " + z, new Vector3(-3.875f, 2.54f, 5.6f + z * 3.4f),
                new Vector3(3.75f, .18f, .14f), wood);
        for (int i = 0; i < 9; i++)
            Box(pergola, "지붕 살 " + i, new Vector3(-5.55f + i * .419f, 2.66f, 7.3f),
                new Vector3(.075f, .08f, 3.8f), wood);
        for (int i = 0; i < 7; i++)
        {
            var ivy = Prefab(pergola, "그늘막 덩굴 " + i, i % 2 == 0 ? "Ivy_Middle" : "Ivy_long",
                new Vector3(-5.4f + i * .52f, 2.65f, 8.98f - .18f * (i % 2)), i * 39f,
                .82f + (i % 3) * .16f);
            AssignPlants(ivy, plants);
        }
    }

    static void Bench(Transform root, Material wood)
    {
        var bench = Child(root, "정원 목재 벤치").transform;
        Box(bench, "앉는 판", new Vector3(1.7f, .45f, 9.0f), new Vector3(2.4f, .09f, .48f), wood);
        for (int i = 0; i < 2; i++)
        {
            Box(bench, "다리 " + i, new Vector3(.75f + i * 1.9f, .22f, 9.0f), new Vector3(.10f, .44f, .38f), wood);
            Box(bench, "등받이 세로대 " + i, new Vector3(.75f + i * 1.9f, .69f, 9.24f), new Vector3(.09f, .56f, .09f), wood);
            Box(bench, "등받이 판 " + i, new Vector3(1.7f, .66f + i * .18f, 9.24f), new Vector3(2.4f, .13f, .065f), wood);
        }
    }

    static void SideGarden(Transform root, Material wood, Material plants, Material plaster, Material soil, Material grout)
    {
        // 타원 창 앞에 실제 바닥과 화단을 둔다. 실내 소품과 사용자가 옮긴 테이블은 건드리지 않는다.
        Box(root, "옆 정원 바닥 받침", new Vector3(-7.2f, FloorY - .064f, -1.52f),
            new Vector3(6.6f, .12f, 10.38f), grout);
        Paving(root, "GardenSidePaving", "옆 정원 석재 포장", -10.5f, -3.9f, -6.71f, 3.67f);
        Box(root, "옆 정원 낮은 담장", new Vector3(-10.55f, .38f, -1.52f),
            new Vector3(.18f, .76f, 10.38f), plaster);
        Box(root, "옆 정원 뒤 담장", new Vector3(-7.2f, .38f, -6.71f),
            new Vector3(6.7f, .76f, .18f), plaster);
        var lawn = Lit("GardenLawn", new Color(.29f, .35f, .22f), .05f);
        Box(root, "옆 정원 바깥 녹지", new Vector3(-22.5f, FloorY - .026f, -1f),
            new Vector3(24f, .05f, 58f), lawn);
        for (int bay = 0; bay < 3; bay++)
        {
            float z = -4.5f + bay * 3.05f;
            Box(root, "옆 화단 " + bay, new Vector3(-9.57f, .22f, z), new Vector3(1.45f, .44f, 2.5f), plaster);
            Box(root, "옆 화단 흙 " + bay, new Vector3(-9.57f, .445f, z), new Vector3(1.3f, .015f, 2.35f), soil);
            for (int i = 0; i < 4; i++)
            {
                var foliage = Prefab(root, "옆 화단 식물 " + bay + "-" + i, "Flower_Pot",
                    new Vector3(-9.55f + .24f * Mathf.Sin(i * 2f + bay), .45f, z - .88f + i * .59f),
                    i * 73f + bay * 21f, 1.1f + (i % 3) * .27f);
                if (foliage.TryGetComponent<Renderer>(out var pot))
                {
                    Undo.RecordObject(pot, UndoName);
                    pot.enabled = false;
                }
                AssignPlants(foliage, plants);
                AlignToGround(foliage, .45f);
                var cover = Prefab(root, "옆 화단 낮은 잎 " + bay + "-" + i, "Ivy_Short",
                    new Vector3(-8.99f, .45f, z - .9f + i * .59f), i * 37f, 1.65f);
                AssignPlants(cover, plants);
                AlignToGround(cover, .45f);
            }
        }
        for (int i = 0; i < 3; i++)
            Box(root, "옆 담장 목재대 " + i, new Vector3(-10.43f, .78f, -4.5f + i * 3.05f),
                new Vector3(.13f, .075f, 2.7f), wood);
    }

    static void DistantTrees(Transform root)
    {
        const string texturePath = AssetsRoot + "/DistantTreesComplete.png";
        var importer = AssetImporter.GetAtPath(texturePath) as TextureImporter;
        if (importer == null) throw new InvalidOperationException("먼 수목 텍스처를 먼저 가져오세요.");
        if (!importer.alphaIsTransparency || importer.wrapModeU != TextureWrapMode.Repeat ||
            importer.wrapModeV != TextureWrapMode.Clamp || importer.maxTextureSize != 2048)
        {
            importer.alphaIsTransparency = true;
            importer.wrapModeU = TextureWrapMode.Repeat;
            importer.wrapModeV = TextureWrapMode.Clamp;
            importer.mipmapEnabled = true;
            importer.maxTextureSize = 2048;
            importer.textureCompression = TextureImporterCompression.CompressedHQ;
            importer.SaveAndReimport();
        }
        var texture = AssetDatabase.LoadAssetAtPath<Texture2D>(texturePath);
        var material = Material("GardenDistantTrees", Shader.Find("Universal Render Pipeline/Unlit"));
        material.SetTexture("_BaseMap", texture);
        material.SetColor("_BaseColor", new Color(.9f, .92f, .88f, 1f));
        material.SetFloat("_Surface", 0f);
        material.SetFloat("_AlphaClip", 1f);
        material.SetFloat("_Cutoff", .4f);
        material.SetFloat("_Cull", 0f);
        BaseShaderGUI.SetMaterialKeywords(material);
        EditorUtility.SetDirty(material);
        float rootUv = TreeGroundContact(texturePath);
        DistantArc(root, "먼 수목 배경", "DistantTreesGeometry", texture, material, rootUv, 0f);
        DistantArc(root, "왼쪽 먼 수목", "LeftDistantTreesGeometry", texture, material, rootUv, -120f);
        DistantArc(root, "뒤쪽 먼 수목", "RearDistantTreesGeometry", texture, material, rootUv, 120f);
    }

    static float TreeGroundContact(string texturePath)
    {
        // PNG의 투명 여백을 땅 밑으로 보내고, 실제 밑동 픽셀을 바닥 높이에 맞춘다.
        // 원본 PNG를 읽기만 하므로 플레이어의 텍스처 Read/Write 설정은 켜지 않는다.
        var source = new Texture2D(2, 2, TextureFormat.RGBA32, false);
        try
        {
            if (!source.LoadImage(File.ReadAllBytes(texturePath)))
                throw new InvalidOperationException("수목 PNG를 읽을 수 없습니다.");
            var pixels = source.GetPixels32();
            int minX = source.width, maxX = -1, minY = source.height, maxY = -1;
            for (int y = 0; y < source.height; y++)
            for (int x = 0; x < source.width; x++)
            {
                if (pixels[y * source.width + x].a < 102) continue;
                minX = Mathf.Min(minX, x);
                maxX = Mathf.Max(maxX, x);
                minY = Mathf.Min(minY, y);
                maxY = Mathf.Max(maxY, y);
            }
            if (maxX < 0 || minX <= 0 || maxX >= source.width - 1 ||
                minY <= 0 || maxY >= source.height - 1)
                throw new InvalidOperationException("전체 나무와 사방의 투명 여백이 있는 수목 PNG가 필요합니다.");
            return (minY + .5f) / source.height;
        }
        finally
        {
            UnityEngine.Object.DestroyImmediate(source);
        }
    }

    static void DistantArc(Transform root, string name, string meshName, Texture2D texture, Material material,
        float rootUv, float yaw)
    {
        var found = root.Find(name);
        var go = found != null ? found.gameObject : GameObject.CreatePrimitive(PrimitiveType.Quad);
        if (found == null)
        {
            go.name = name;
            go.transform.SetParent(root, false);
            Undo.RegisterCreatedObjectUndo(go, UndoName);
            Undo.DestroyObjectImmediate(go.GetComponent<Collider>());
        }
        // 같은 반경의 120도 호 3개를 맞붙인다. 겹친 판의 잘린 끝은 만들지 않는다.
        // 전역 각도에서 연속 UV를 계산해 메시가 나뉘어도 하나의 수목 둘레로 이어진다.
        const int segments = 32;
        const float arcDegrees = 120f;
        const float radius = 30f;
        const float height = 10f;
        float circumference = 2f * Mathf.PI * radius;
        int imageRepeats = Mathf.Max(1, Mathf.RoundToInt(circumference * texture.height / (texture.width * height)));
        string meshPath = AssetsRoot + "/" + meshName + ".asset";
        var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(meshPath);
        if (mesh == null)
        {
            mesh = new Mesh { name = meshName };
            AssetDatabase.CreateAsset(mesh, meshPath);
        }
        Undo.RecordObject(mesh, UndoName);
        mesh.Clear();
        var vertices = new List<Vector3>();
        var uv = new List<Vector2>();
        var triangles = new List<int>();
        for (int i = 0; i <= segments; i++)
        {
            float u = (float)i / segments;
            float angleDegrees = (u - .5f) * arcDegrees + yaw;
            float angle = angleDegrees * Mathf.Deg2Rad;
            var bottom = new Vector3(-1.4f + Mathf.Sin(angle) * radius, FloorY - height * rootUv,
                1.7f + Mathf.Cos(angle) * radius);
            vertices.Add(bottom);
            vertices.Add(bottom + Vector3.up * height);
            float textureU = (angleDegrees + 180f) / 360f * imageRepeats;
            uv.Add(new Vector2(textureU, 0f));
            uv.Add(new Vector2(textureU, 1f));
            if (i == segments) continue;
            int start = i * 2;
            triangles.AddRange(new[] { start, start + 1, start + 3, start, start + 3, start + 2 });
        }
        mesh.SetVertices(vertices);
        mesh.SetUVs(0, uv);
        mesh.SetTriangles(triangles, 0);
        mesh.RecalculateNormals();
        mesh.RecalculateBounds();
        EditorUtility.SetDirty(mesh);
        Undo.RecordObject(go.transform, UndoName);
        go.transform.SetPositionAndRotation(Vector3.zero, Quaternion.identity);
        go.transform.localScale = Vector3.one;
        var filter = go.GetComponent<MeshFilter>();
        Undo.RecordObject(filter, UndoName);
        filter.sharedMesh = mesh;
        var renderer = go.GetComponent<MeshRenderer>();
        Undo.RecordObject(renderer, UndoName);
        renderer.sharedMaterial = material;
        GameObjectUtility.SetStaticEditorFlags(go, StaticEditorFlags.BatchingStatic);
        renderer.receiveGI = ReceiveGI.LightProbes;
        renderer.shadowCastingMode = ShadowCastingMode.Off;
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
        float tabletop = table.GetComponentsInChildren<Renderer>().Max(r => r.bounds.max.y);
        var teapot = Prefab(root, name + " 주전자", "Teapot", position + rotation * new Vector3(.12f, tabletop, .13f), angle + 15f, 1f);
        AlignToGround(teapot, tabletop);
        for (int i = 0; i < 2; i++)
        {
            var cup = Prefab(root, name + " 찻잔 " + i, "Cup",
                position + rotation * new Vector3(-.14f + i * .24f, tabletop, -.18f + i * .35f), angle + i * 80f, 1f);
            AlignToGround(cup, tabletop);
        }
    }

    static void Paving(Transform root)
        => Paving(root, "GardenPaving", "마당 석재 포장", -7.4f, 4.6f, 3.67f, 11.70f);

    static void Paving(Transform root, string meshName, string name, float minX, float maxX, float minZ, float maxZ)
    {
        string path = AssetsRoot + "/" + meshName + ".asset";
        var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
        if (mesh == null)
        {
            mesh = new Mesh { name = meshName };
            AssetDatabase.CreateAsset(mesh, path);
        }
        Undo.RecordObject(mesh, UndoName);
        mesh.Clear();
        var vertices = new List<Vector3>();
        var uv = new List<Vector2>();
        var triangles = new[] { new List<int>(), new List<int>(), new List<int>(), new List<int>() };
        for (int row = 0; row < Mathf.CeilToInt((maxZ - minZ) / .73f); row++)
        for (int col = 0; col < Mathf.CeilToInt(maxX - minX) + 1; col++)
        {
            float x0 = Mathf.Max(minX, minX + col - (row % 2) * .5f) + .004f;
            float x1 = Mathf.Min(maxX, minX + col + 1f - (row % 2) * .5f) - .004f;
            float z0 = minZ + row * .73f + .004f;
            float z1 = Mathf.Min(maxZ, minZ + (row + 1) * .73f) - .004f;
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
        var paving = Child(root, name);
        var filter = paving.TryGetComponent<MeshFilter>(out var existingFilter) ? existingFilter : Undo.AddComponent<MeshFilter>(paving);
        var renderer = paving.TryGetComponent<MeshRenderer>(out var existingRenderer) ? existingRenderer : Undo.AddComponent<MeshRenderer>(paving);
        Undo.RecordObjects(new UnityEngine.Object[] { filter, renderer }, UndoName);
        filter.sharedMesh = mesh;
        renderer.sharedMaterials = Enumerable.Range(0, 4).Select(i =>
            Lit("GardenPaver" + i, new Color(.68f + i * .012f, .655f + i * .011f, .595f + i * .010f), .20f)).ToArray();
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
