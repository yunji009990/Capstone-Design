using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>참여자 자료가 없는 기본 몸체의 온전한 옷깃. 결합 시 수평으로 잘린 부분만 복원한다.</summary>
public sealed class PersonaCollarTemplate : ScriptableObject
{
    public const string ResourceName = "PersonaCollarTemplate";
    public Mesh collar;
    public string[] boneNames;
    public Vector3 neck, head;
    public string sourceHash;

    struct Vertex
    {
        public Vector3 position, normal;
        public Vector2 uv;
        public BoneWeight weight;
    }

    public Mesh Restore(SkinnedMeshRenderer skin)
    {
        var source = skin.sharedMesh;
        if (collar == null || source == null || boneNames == null) return null;
        int neckIndex = Find(skin, "neck.x"), headIndex = Find(skin, "head.x");
        int leftFoot = Find(skin, "foot.l"), leftToe = Find(skin, "toes_01.l");
        if (neckIndex < 0 || headIndex < 0 || leftFoot < 0 || leftToe < 0) return null;
        var binds = source.bindposes;
        Vector3 targetNeck = binds[neckIndex].inverse.GetColumn(3);
        Vector3 targetHead = binds[headIndex].inverse.GetColumn(3);
        Vector3 facing = Vector3.ProjectOnPlane((Vector3)(binds[leftToe].inverse.GetColumn(3) - binds[leftFoot].inverse.GetColumn(3)), Vector3.up).normalized;
        if (facing.sqrMagnitude < .5f) return null;
        float scale = Vector3.Distance(targetNeck, targetHead) / Vector3.Distance(neck, head);
        var rotation = Quaternion.LookRotation(facing, Vector3.up);
        var cp = collar.vertices; var cn = collar.normals; var cu = collar.uv; var cw = collar.boneWeights;
        var transformed = new Vertex[cp.Length];
        for (int i = 0; i < cp.Length; i++)
            transformed[i] = new Vertex { position = targetNeck + rotation * (cp[i] - neck) * scale,
                normal = rotation * cn[i], uv = cu[i], weight = Map(cw[i], skin) };
        float cut = source.bounds.max.y;
        float fullTop = float.MinValue;
        foreach (var v in transformed) fullTop = Mathf.Max(fullTop, v.position.y);
        // 온전한 몸체나 다른 리그는 다시 덧대지 않는다.
        if (fullTop < cut + source.bounds.size.y * .003f) return null;
        var points = new List<Vector3>(source.vertices);
        var normals = new List<Vector3>(source.normals);
        var uv = new List<Vector2>(source.uv);
        var weights = new List<BoneWeight>(source.boneWeights);
        int originalCount = points.Count;
        var extra = new List<int>();
        var triangles = collar.triangles;
        for (int i = 0; i < triangles.Length; i += 3)
        {
            var polygon = Clip(new[] { transformed[triangles[i]], transformed[triangles[i + 1]], transformed[triangles[i + 2]] }, cut);
            for (int k = 1; k + 1 < polygon.Count; k++)
            {
                if (Vector3.Cross(polygon[k].position - polygon[0].position, polygon[k + 1].position - polygon[0].position).sqrMagnitude < 1e-14f) continue;
                foreach (var v in new[] { polygon[0], polygon[k], polygon[k + 1] })
                {
                    extra.Add(points.Count); points.Add(v.position); normals.Add(v.normal); uv.Add(v.uv); weights.Add(v.weight);
                }
            }
        }
        if (extra.Count == 0) return null;
        var result = new Mesh { name = "Persona Restored Collar (Instance)", hideFlags = HideFlags.DontSave, indexFormat = source.indexFormat };
        result.SetVertices(points); result.SetNormals(normals); result.SetUVs(0, uv);
        result.boneWeights = weights.ToArray(); result.bindposes = binds;
        if (source.tangents.Length == originalCount)
        {
            var tangents = new List<Vector4>(source.tangents);
            for (int i = originalCount; i < points.Count; i++)
            {
                Vector3 tangent = Vector3.Cross(Vector3.up, normals[i]);
                if (tangent.sqrMagnitude < .01f) tangent = Vector3.right;
                tangent.Normalize(); tangents.Add(new Vector4(tangent.x, tangent.y, tangent.z, 1f));
            }
            result.SetTangents(tangents);
        }
        if (source.colors.Length == originalCount)
        {
            var colors = new List<Color>(source.colors);
            while (colors.Count < points.Count) colors.Add(Color.white);
            result.SetColors(colors);
        }
        if (source.uv2.Length == originalCount)
        {
            var second = new List<Vector2>(source.uv2);
            while (second.Count < points.Count) second.Add(Vector2.zero);
            result.SetUVs(1, second);
        }
        result.subMeshCount = source.subMeshCount;
        for (int sub = 0; sub < source.subMeshCount; sub++)
        {
            var indices = new List<int>(source.GetTriangles(sub));
            if (sub == 0) indices.AddRange(extra);
            result.SetTriangles(indices, sub);
        }
        for (int shape = 0; shape < source.blendShapeCount; shape++)
            for (int frame = 0; frame < source.GetBlendShapeFrameCount(shape); frame++)
            {
                var dv = new Vector3[originalCount]; var dn = new Vector3[originalCount]; var dt = new Vector3[originalCount];
                source.GetBlendShapeFrameVertices(shape, frame, dv, dn, dt);
                Array.Resize(ref dv, points.Count); Array.Resize(ref dn, points.Count); Array.Resize(ref dt, points.Count);
                result.AddBlendShapeFrame(source.GetBlendShapeName(shape), source.GetBlendShapeFrameWeight(shape, frame), dv, dn, dt);
            }
        result.RecalculateBounds();
        return result;
    }

    static int Find(SkinnedMeshRenderer skin, string name) => Array.FindIndex(skin.bones, b => b != null && b.name == name);
    BoneWeight Map(BoneWeight source, SkinnedMeshRenderer target)
    {
        int MapIndex(int index) => index >= 0 && index < boneNames.Length ? Find(target, boneNames[index]) : -1;
        int a = MapIndex(source.boneIndex0), b = MapIndex(source.boneIndex1), c = MapIndex(source.boneIndex2), d = MapIndex(source.boneIndex3);
        return Make(new[] { a, b, c, d }, new[] { source.weight0, source.weight1, source.weight2, source.weight3 });
    }
    static List<Vertex> Clip(Vertex[] input, float y)
    {
        var result = new List<Vertex>();
        for (int i = 0; i < input.Length; i++)
        {
            var a = input[i]; var b = input[(i + 1) % input.Length];
            bool insideA = a.position.y >= y, insideB = b.position.y >= y;
            if (insideA) result.Add(a);
            if (insideA == insideB) continue;
            float t = (y - a.position.y) / (b.position.y - a.position.y);
            result.Add(new Vertex { position = Vector3.Lerp(a.position, b.position, t), normal = Vector3.Lerp(a.normal, b.normal, t).normalized,
                uv = Vector2.Lerp(a.uv, b.uv, t), weight = Lerp(a.weight, b.weight, t) });
        }
        return result;
    }
    static BoneWeight Lerp(BoneWeight a, BoneWeight b, float t) => Make(
        new[] { a.boneIndex0, a.boneIndex1, a.boneIndex2, a.boneIndex3, b.boneIndex0, b.boneIndex1, b.boneIndex2, b.boneIndex3 },
        new[] { a.weight0 * (1-t), a.weight1 * (1-t), a.weight2 * (1-t), a.weight3 * (1-t), b.weight0*t, b.weight1*t, b.weight2*t, b.weight3*t });
    static BoneWeight Make(int[] indices, float[] weights)
    {
        var sum = new Dictionary<int, float>();
        for (int i = 0; i < indices.Length; i++)
            if (indices[i] >= 0 && weights[i] > 0f) sum[indices[i]] = (sum.TryGetValue(indices[i], out float v) ? v : 0f) + weights[i];
        var list = new List<KeyValuePair<int, float>>(sum); list.Sort((a,b) => b.Value.CompareTo(a.Value));
        float total = 0f; for (int i = 0; i < Mathf.Min(4, list.Count); i++) total += list[i].Value;
        var r = new BoneWeight();
        if (total <= 0f) return r;
        for (int i = 0; i < Mathf.Min(4, list.Count); i++)
        {
            int bone = list[i].Key; float w = list[i].Value / total;
            switch (i)
            {
                case 0: r.boneIndex0 = bone; r.weight0 = w; break;
                case 1: r.boneIndex1 = bone; r.weight1 = w; break;
                case 2: r.boneIndex2 = bone; r.weight2 = w; break;
                case 3: r.boneIndex3 = bone; r.weight3 = w; break;
            }
        }
        return r;
    }
}
