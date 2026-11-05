using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEngine;

/// <summary>런타임 GLB의 불투명 PBR 셰이더를 플레이어에서도 사용할 수 있게 보존한다.</summary>
public sealed class LocalDemoBuildSetup : IPreprocessBuildWithReport
{
    const string MaterialPath = "Assets/Resources/RuntimeGltfOpaque.mat";
    public int callbackOrder => -100;

    public void OnPreprocessBuild(BuildReport report)
    {
        // glTFast 권장 방식: Resources의 텍스처 없는 대표 재질로 사용 변형만 포함한다.
        // 현재 준비된 몸체의 세 재질은 모두 같은 불투명·양면 PBR 변형이다.
        var shader = AssetDatabase.LoadAssetAtPath<Shader>(
            "Packages/com.unity.cloud.gltfast/Runtime/Shader/glTF-pbrMetallicRoughness.shadergraph");
        if (shader == null || !shader.isSupported)
            throw new BuildFailedException("런타임 인물의 glTF PBR 셰이더를 찾을 수 없습니다.");
        if (!AssetDatabase.IsValidFolder("Assets/Resources"))
            AssetDatabase.CreateFolder("Assets", "Resources");
        var material = AssetDatabase.LoadAssetAtPath<Material>(MaterialPath);
        if (material != null && material.shader == shader) return;
        if (material == null)
        {
            material = new Material(shader) { name = "RuntimeGltfOpaque" };
            AssetDatabase.CreateAsset(material, MaterialPath);
        }
        material.shader = shader;
        material.doubleSidedGI = true;
        material.SetFloat("_Cull", 0);
        material.renderQueue = 2000;
        EditorUtility.SetDirty(material);
        AssetDatabase.SaveAssets();
    }
}
