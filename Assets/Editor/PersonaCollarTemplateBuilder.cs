using System;
using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using Object = UnityEngine.Object;

/// <summary>기본 FBX에서 목 부분의 옷만 추출한다. 참가자 모델은 읽거나 저장하지 않는다.</summary>
public static class PersonaCollarTemplateBuilder
{
    public const string AssetPath = "Assets/Resources/PersonaCollarTemplate.asset";
    const string SourcePath = "Assets/Models/human/Human.fbx";
    public static void Ensure()
    {
        if (AssetDatabase.LoadAssetAtPath<PersonaCollarTemplate>(AssetPath) != null) return;
        var scene = EditorSceneManager.NewPreviewScene();
        GameObject instance = null;
        try
        {
            instance = (GameObject)PrefabUtility.InstantiatePrefab(AssetDatabase.LoadAssetAtPath<GameObject>(SourcePath), scene);
            var skin = instance.GetComponentsInChildren<SkinnedMeshRenderer>(true).First(s => s.sharedMesh.blendShapeCount >= 6);
            var source = skin.sharedMesh; var bones = skin.bones; var binds = source.bindposes;
            int Find(string name) => Array.FindIndex(bones, b => b != null && b.name == name);
            Vector3 Position(string name) => (skin.transform.localToWorldMatrix * binds[Find(name)].inverse).GetColumn(3);
            Vector3 facing = Vector3.ProjectOnPlane(Position("toes_01.l") - Position("foot.l"), Vector3.up).normalized;
            var toCanonical = Matrix4x4.Rotate(Quaternion.Inverse(Quaternion.LookRotation(facing, Vector3.up)));
            var vertexMatrix = toCanonical * skin.transform.localToWorldMatrix;
            Vector3 neck = toCanonical.MultiplyPoint3x4(Position("neck.x"));
            Vector3 head = toCanonical.MultiplyPoint3x4(Position("head.x"));
            var vertices = source.vertices.Select(v => vertexMatrix.MultiplyPoint3x4(v)).ToArray();
            var originalNormals = source.normals; var originalUv = source.uv; var originalWeights = source.boneWeights;
            var points = new List<Vector3>(); var normals = new List<Vector3>(); var uv = new List<Vector2>(); var weights = new List<BoneWeight>(); var triangles = new List<int>();
            var map = new Dictionary<int, int>();
            var normalMatrix = vertexMatrix.inverse.transpose;
            // 원래 의상 재질 슬롯만 저장한다. 손과 소매 안감은 포함하지 않는다.
            var indices = source.GetTriangles(0);
            for (int i = 0; i < indices.Length; i += 3)
            {
                if (Mathf.Max(vertices[indices[i]].y, vertices[indices[i+1]].y, vertices[indices[i+2]].y) < neck.y - .04f) continue;
                for (int k = 0; k < 3; k++)
                {
                    int old = indices[i+k];
                    if (!map.TryGetValue(old, out int index))
                    {
                        index = points.Count; map[old] = index;
                        points.Add(vertices[old]); normals.Add(normalMatrix.MultiplyVector(originalNormals[old]).normalized); uv.Add(originalUv[old]); weights.Add(originalWeights[old]);
                    }
                    triangles.Add(index);
                }
            }
            var mesh = new Mesh { name = "Original Body Collar" };
            mesh.SetVertices(points); mesh.SetNormals(normals); mesh.SetUVs(0,uv); mesh.boneWeights = weights.ToArray(); mesh.SetTriangles(triangles,0); mesh.RecalculateBounds();
            mesh.bindposes = binds.Select(b => (vertexMatrix * b.inverse).inverse).ToArray();
            var template = ScriptableObject.CreateInstance<PersonaCollarTemplate>();
            template.collar = mesh; template.boneNames = bones.Select(b => b.name).ToArray(); template.neck = neck; template.head = head;
            template.sourceHash = AssetDatabase.GetAssetDependencyHash(SourcePath).ToString();
            AssetDatabase.CreateAsset(template, AssetPath); AssetDatabase.AddObjectToAsset(mesh, template); AssetDatabase.SaveAssetIfDirty(template);
        }
        finally
        {
            if (instance != null) Object.DestroyImmediate(instance);
            EditorSceneManager.ClosePreviewScene(scene);
        }
    }
}
