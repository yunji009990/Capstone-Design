// 새 고정 몸체의 재질과 Humanoid 기준 자세를 기존 자산 경로에 연결한다.
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;

public static class PersonaBodyModelSetup
{
    const string BodyPath = "Assets/Models/human/Human.fbx";
    const string TextureFolder = "Assets/Models/human/tex/Hands/";
    const string MaterialFolder = "Assets/Models/human/tex/Materials/";

    [MenuItem("Tools/Persona/기본 몸체 재질·기준 자세 적용")]
    public static void Apply()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode)
            throw new InvalidOperationException("Play를 종료한 뒤 몸체 자산을 적용해 주세요.");
        var importer = AssetImporter.GetAtPath(BodyPath) as ModelImporter;
        if (importer == null) throw new InvalidOperationException("기본 몸체 FBX를 찾을 수 없습니다.");
        var shader = Shader.Find("Universal Render Pipeline/Lit");
        if (shader == null || !shader.isSupported)
            throw new InvalidOperationException("현재 파이프라인에서 URP/Lit을 사용할 수 없습니다.");

        ConfigureTexture("MaleHands_Color.png", TextureImporterType.Default, true);
        ConfigureTexture("MaleHands_Normal.png", TextureImporterType.NormalMap, false);
        ConfigureTexture("MaleHands_MetallicSmoothness.png", TextureImporterType.Default, false);
        ConfigureTexture("MaleHands_MetalRoughness.png", TextureImporterType.Default, false);

        var hand = MaterialAt("Natural_Hand_Male", shader);
        hand.SetTexture("_BaseMap", Texture("MaleHands_Color.png"));
        hand.SetColor("_BaseColor", Color.white);
        hand.SetTexture("_BumpMap", Texture("MaleHands_Normal.png"));
        hand.SetFloat("_BumpScale", .65f);
        hand.EnableKeyword("_NORMALMAP");
        hand.SetTexture("_MetallicGlossMap", Texture("MaleHands_MetallicSmoothness.png"));
        hand.SetFloat("_Metallic", 0f);
        hand.SetFloat("_Smoothness", 1f);
        hand.SetFloat("_SmoothnessTextureChannel", 0f);
        hand.EnableKeyword("_METALLICSPECGLOSSMAP");
        hand.DisableKeyword("_SMOOTHNESS_TEXTURE_ALBEDO_CHANNEL_A");
        EditorUtility.SetDirty(hand);
        AssetDatabase.SaveAssetIfDirty(hand);

        var cuff = MaterialAt("Cuff_Inner_Black", shader);
        cuff.SetColor("_BaseColor", new Color(.006f, .007f, .009f, 1f));
        cuff.SetFloat("_Metallic", 0f);
        cuff.SetFloat("_Smoothness", .14f);
        EditorUtility.SetDirty(cuff);
        AssetDatabase.SaveAssetIfDirty(cuff);

        var cloth = AssetDatabase.LoadAssetAtPath<Material>(MaterialFolder + "avatar_BaseColor.mat");
        if (cloth == null) throw new InvalidOperationException("기존 의상 재질이 없습니다.");
        Undo.RecordObject(importer, "Connect fixed-body materials");
        importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), "mtl_avatar_body.001"), cloth);
        importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), "Natural_Hand_Male"), hand);
        importer.AddRemap(new AssetImporter.SourceAssetIdentifier(typeof(Material), "Cuff_Inner_Black"), cuff);
        importer.SaveAndReimport();
        PersonaReferencePoseSetup.Build(PersonaArrivalSetup.AnimationFolder + "/Walking.fbx");
        Inspect();
        Debug.Log("[BodyModelSetup] 기본 몸체의 양손 재질과 Humanoid 기준 자세를 적용했습니다.");
    }

    [MenuItem("Tools/Persona/기본 몸체 자산 검사")]
    public static void Inspect()
    {
        var prefab = AssetDatabase.LoadAssetAtPath<GameObject>(BodyPath);
        if (prefab == null) throw new InvalidOperationException("기본 몸체가 임포트되지 않았습니다.");
        var skins = prefab.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        if (skins.Length != 1 || skins[0].bones.Length != 60 || skins[0].sharedMesh.subMeshCount != 3)
            throw new InvalidOperationException("예상한 단일 몸체·60개 본·3개 재질 구조와 다릅니다.");
        var materials = skins[0].sharedMaterials;
        if (materials.Length != 3 || materials.Any(m => m == null || !m.shader.isSupported))
            throw new InvalidOperationException("몸체 재질 또는 셰이더가 누락됐습니다.");
        if (materials[1].name != "Natural_Hand_Male" || materials[2].name != "Cuff_Inner_Black")
            throw new InvalidOperationException("손·소매 안감 재질 연결을 확인해 주세요.");
        var scene = UnityEngine.SceneManagement.SceneManager.GetActiveScene();
        var report = new Report
        {
            utc = DateTime.UtcNow.ToString("o"), asset = BodyPath,
            guid = AssetDatabase.AssetPathToGUID(BodyPath), vertices = skins[0].sharedMesh.vertexCount,
            bones = skins[0].bones.Length, materials = materials.Select(m => m.name).ToArray(),
            shaders = materials.Select(m => m.shader.name).ToArray(),
            scene = scene.path, sceneDirty = scene.isDirty,
        };
        const string output = "tools/_work/fixed_body_animation/body_asset.json";
        Directory.CreateDirectory(Path.GetDirectoryName(output));
        File.WriteAllText(output, JsonUtility.ToJson(report, true));
        Selection.activeObject = prefab;
        EditorGUIUtility.PingObject(prefab);
        Debug.Log("[BodyModelSetup] 자산 검사 통과: " + output);
    }

    static Texture Texture(string name) => AssetDatabase.LoadAssetAtPath<Texture>(TextureFolder + name)
        ?? throw new InvalidOperationException("몸체 텍스처가 없습니다: " + name);

    static Material MaterialAt(string name, Shader shader)
    {
        string path = MaterialFolder + name + ".mat";
        var material = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (material == null)
        {
            material = new Material(shader) { name = name };
            AssetDatabase.CreateAsset(material, path);
        }
        else
        {
            Undo.RecordObject(material, "Apply fixed-body materials");
            material.shader = shader;
        }
        return material;
    }

    static void ConfigureTexture(string name, TextureImporterType type, bool srgb)
    {
        var importer = AssetImporter.GetAtPath(TextureFolder + name) as TextureImporter;
        if (importer == null) throw new InvalidOperationException("텍스처가 임포트되지 않았습니다: " + name);
        if (importer.textureType == type && importer.sRGBTexture == srgb &&
            importer.textureCompression == TextureImporterCompression.Uncompressed) return;
        Undo.RecordObject(importer, "Configure fixed-body texture");
        importer.textureType = type;
        importer.sRGBTexture = srgb;
        importer.textureCompression = TextureImporterCompression.Uncompressed;
        importer.SaveAndReimport();
    }

    [Serializable] class Report
    {
        public string utc, asset, guid, scene;
        public int vertices, bones;
        public bool sceneDirty;
        public string[] materials, shaders;
    }
}
