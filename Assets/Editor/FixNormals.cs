using UnityEngine;
using UnityEditor;
using System.Collections.Generic;

public class BatchFixNormals
{
    [MenuItem("Tools/Mesh/Batch Recalculate Normals & Tangents (Selected)")]
    static void BatchFix()
    {
        Object[] objs = Selection.objects;

        if (objs.Length == 0)
        {
            Debug.LogError("Select multiple Mesh assets (.mesh or FBX Mesh) in the Project window.");
            return;
        }

        int count = 0;

        foreach (var obj in objs)
        {
            if (obj is Mesh mesh)
            {
                Debug.Log($"[Fix] Recalculating: {mesh.name}");

                mesh.RecalculateNormals();
                mesh.RecalculateTangents();

                EditorUtility.SetDirty(mesh);
                count++;
            }
        }

        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();

        Debug.Log($"[Done] Fixed Normals/Tangents for {count} mesh assets.");
    }
}
