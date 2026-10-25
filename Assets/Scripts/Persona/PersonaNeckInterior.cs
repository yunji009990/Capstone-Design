using System;
using System.Collections.Generic;
using UnityEngine;

/// <summary>분리된 목의 열린 윗면과 머리 아래의 어두운 내부 면을 피부색으로 채운다. 원본 자산과 리그는 보존한다.</summary>
[DisallowMultipleComponent]
public sealed class PersonaNeckInterior : MonoBehaviour
{
    readonly List<Mesh> ownedMeshes = new List<Mesh>();
    readonly List<SkinnedMeshRenderer> caps = new List<SkinnedMeshRenderer>();
    readonly List<SurfacePatch> patches = new List<SurfacePatch>();
    public int CapCount => caps.Count;
    public int ColoredTriangles { get; private set; }
    sealed class SurfacePatch
    {
        public SkinnedMeshRenderer renderer;
        public Mesh original, filled;
        public Material[] originalMaterials, filledMaterials;
    }

    public static void Attach(Transform model)
    {
        if (model == null || model.GetComponent<PersonaNeckInterior>() != null) return;
        var interior = model.gameObject.AddComponent<PersonaNeckInterior>();
        interior.Build(model);
    }

    void Build(Transform model)
    {
        var head = PersonaHumanoid.FindBone(model, HumanBodyBones.Head);
        var neck = PersonaHumanoid.FindBone(model, HumanBodyBones.Neck);
        if (head == null || neck == null) return;
        var skins = model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        float bodyHeight = 0f;
        foreach (var skin in skins)
            if (skin.sharedMesh != null) bodyHeight = Mathf.Max(bodyHeight, skin.sharedMesh.bounds.size.y);
        foreach (var skin in skins)
        {
            var source = skin.sharedMesh;
            // 머리·몸통 전체나 손은 건드리지 않는다. 별도 색상 목 메시가 있는 모델에만 적용한다.
            if (source == null || !source.isReadable || skin.sharedMaterial == null || source.vertexCount > 4096 ||
                source.bounds.size.y < bodyHeight * .015f || source.bounds.size.y > bodyHeight * .15f ||
                skin.sharedMaterial.mainTexture != null) continue;
            var points = source.vertices; var colors = source.colors; var weights = source.boneWeights;
            if (colors.Length != points.Length || weights.Length != points.Length) continue;
            int headIndex = Array.IndexOf(skin.bones, head), neckIndex = Array.IndexOf(skin.bones, neck);
            if (headIndex < 0 && neckIndex < 0) continue;
            var ring = TopBoundary(source, points);
            if (ring.Count < 8) continue;
            float influence = 0f;
            foreach (int index in ring)
            {
                var w = weights[index];
                influence += NeckWeight(w.boneIndex0, w.weight0, headIndex, neckIndex) + NeckWeight(w.boneIndex1, w.weight1, headIndex, neckIndex) +
                             NeckWeight(w.boneIndex2, w.weight2, headIndex, neckIndex) + NeckWeight(w.boneIndex3, w.weight3, headIndex, neckIndex);
            }
            // 경계에는 가슴 본도 일부 섞여 있으므로 목·머리의 과반 가중치로 구분한다.
            if (influence / ring.Count < .5f) continue;
            var mesh = Cap(source, ring, points, colors, weights);
            ownedMeshes.Add(mesh);
            var child = new GameObject("Neck Interior Skin");
            child.transform.SetParent(skin.transform, false); child.layer = skin.gameObject.layer;
            var fill = child.AddComponent<SkinnedMeshRenderer>();
            fill.sharedMesh = mesh; fill.sharedMaterial = skin.sharedMaterial;
            fill.bones = skin.bones; fill.rootBone = skin.rootBone; fill.localBounds = skin.localBounds;
            fill.updateWhenOffscreen = skin.updateWhenOffscreen;
            fill.shadowCastingMode = UnityEngine.Rendering.ShadowCastingMode.Off;
            fill.receiveShadows = skin.receiveShadows;
            caps.Add(fill);
            foreach (var other in skins) ColorHeadInterior(other, skin, bodyHeight, head);
            break;
        }
    }

    void ColorHeadInterior(SkinnedMeshRenderer skin, SkinnedMeshRenderer neckSkin, float bodyHeight, Transform head)
    {
        var source = skin.sharedMesh;
        // 얼굴 표면·몸체는 제외한다. 현재 제작 경로의 별도 머리 아래, 목 범위 안의 면에만 적용한다.
        if (skin == neckSkin || source == null || !source.isReadable || skin.sharedMaterial == null || skin.sharedMaterial.mainTexture == null ||
            skin.sharedMaterials.Length != source.subMeshCount ||
            source.blendShapeCount != 0 || source.bounds.size.y < bodyHeight * .1f || source.bounds.size.y > bodyHeight * .45f ||
            Array.IndexOf(skin.bones, head) < 0 || source.boneWeights.Length != source.vertexCount) return;
        var neckMesh = neckSkin.sharedMesh;
        var neckPoints = neckMesh.vertices; var neckColors = neckMesh.colors; var neckNormals = neckMesh.normals; var neckWeights = neckMesh.boneWeights;
        var boneMap = new int[neckSkin.bones.Length];
        for (int i = 0; i < boneMap.Length; i++) boneMap[i] = Array.IndexOf(skin.bones, neckSkin.bones[i]);
        var toNeck = neckSkin.transform.worldToLocalMatrix * skin.transform.localToWorldMatrix;
        var points = source.vertices; var vertices = new List<Vector3>(points);
        var normals = new List<Vector3>(source.normals); var tangents = new List<Vector4>(source.tangents);
        var weights = new List<BoneWeight>(source.boneWeights);
        var colors = source.colors.Length == points.Length ? new List<Color>(source.colors) : new List<Color>();
        if (colors.Count == 0) for (int i = 0; i < points.Length; i++) colors.Add(Color.white);
        var uv = new List<Vector4>[8];
        for (int channel = 0; channel < uv.Length; channel++) { uv[channel] = new List<Vector4>(); source.GetUVs(channel, uv[channel]); }
        var duplicate = new Dictionary<int, int>(); var inside = new List<int>(); var submeshes = new List<int[]>();
        int Duplicate(int original)
        {
            if (duplicate.TryGetValue(original, out int index)) return index;
            index = vertices.Count; duplicate.Add(original, index); vertices.Add(points[original]); weights.Add(weights[original]);
            if (normals.Count > 0) normals.Add(normals[original]);
            if (tangents.Count > 0) tangents.Add(tangents[original]);
            for (int channel = 0; channel < uv.Length; channel++) if (uv[channel].Count > 0) uv[channel].Add(uv[channel][original]);
            var point = toNeck.MultiplyPoint3x4(points[original]); int nearest = 0; float distance = float.PositiveInfinity;
            for (int i = 0; i < neckPoints.Length; i++)
            {
                float d = (neckPoints[i] - point).sqrMagnitude;
                if (d < distance) { distance = d; nearest = i; }
            }
            colors.Add(neckColors[nearest]);
            // 원래 옷 주름의 법선까지 남으면 피부색을 넣어도 밝은 절단선이 생긴다.
            if (normals.Count > 0 && neckNormals.Length == neckPoints.Length)
                normals[index] = toNeck.transpose.MultiplyVector(neckNormals[nearest]).normalized;
            // 새로 분리한 안쪽 정점은 이웃 목을 따라가게 해 회전 때 두 면의 조명이 갈라지는 것도 줄인다.
            var w = neckWeights[nearest];
            bool Mapped(int bone, float weight) => weight <= 0f || (bone >= 0 && bone < boneMap.Length && boneMap[bone] >= 0);
            if (Mapped(w.boneIndex0, w.weight0) && Mapped(w.boneIndex1, w.weight1) && Mapped(w.boneIndex2, w.weight2) && Mapped(w.boneIndex3, w.weight3))
            {
                weights[index] = new BoneWeight { boneIndex0 = w.weight0 > 0 ? boneMap[w.boneIndex0] : 0, weight0 = w.weight0,
                    boneIndex1 = w.weight1 > 0 ? boneMap[w.boneIndex1] : 0, weight1 = w.weight1,
                    boneIndex2 = w.weight2 > 0 ? boneMap[w.boneIndex2] : 0, weight2 = w.weight2,
                    boneIndex3 = w.weight3 > 0 ? boneMap[w.boneIndex3] : 0, weight3 = w.weight3 };
            }
            return index;
        }
        var bounds = neckMesh.bounds;
        for (int submesh = 0; submesh < source.subMeshCount; submesh++)
        {
            var outside = new List<int>(); var indices = source.GetTriangles(submesh);
            for (int i = 0; i + 2 < indices.Length; i += 3)
            {
                Vector3 center = toNeck.MultiplyPoint3x4((points[indices[i]] + points[indices[i + 1]] + points[indices[i + 2]]) / 3f);
                bool isInside = center.y < bounds.max.y + bounds.size.y * .002f && center.y > bounds.min.y - bounds.size.y * .1f &&
                    Mathf.Abs(center.x - bounds.center.x) < bounds.extents.x * 1.2f && Mathf.Abs(center.z - bounds.center.z) < bounds.extents.z * 1.2f;
                for (int k = 0; k < 3; k++) if (isInside) inside.Add(Duplicate(indices[i + k])); else outside.Add(indices[i + k]);
            }
            submeshes.Add(outside.ToArray());
        }
        if (inside.Count == 0) return;
        // 생성 머리의 옷 잔여 면에는 안팎이 뒤집힌 삼각형도 있다. 양면 조명에서 밝은 선이 생기지 않게 맞춘다.
        if (normals.Count == vertices.Count)
            for (int i = 0; i < inside.Count; i += 3)
            {
                int a = inside[i], b = inside[i + 1], c = inside[i + 2];
                if (Vector3.Dot(Vector3.Cross(vertices[b] - vertices[a], vertices[c] - vertices[a]), normals[a] + normals[b] + normals[c]) < 0f)
                { inside[i + 1] = c; inside[i + 2] = b; }
            }
        var mesh = Instantiate(source); mesh.name = "Neck Interior Head Surface (Instance)"; mesh.hideFlags = HideFlags.DontSave;
        if (vertices.Count > 65535) mesh.indexFormat = UnityEngine.Rendering.IndexFormat.UInt32;
        mesh.SetVertices(vertices); mesh.SetColors(colors); mesh.boneWeights = weights.ToArray();
        if (normals.Count > 0) mesh.SetNormals(normals);
        if (tangents.Count > 0) mesh.SetTangents(tangents);
        for (int channel = 0; channel < uv.Length; channel++) if (uv[channel].Count > 0) mesh.SetUVs(channel, uv[channel]);
        mesh.subMeshCount = source.subMeshCount + 1;
        for (int i = 0; i < submeshes.Count; i++) mesh.SetTriangles(submeshes[i], i, false);
        mesh.SetTriangles(inside, source.subMeshCount, false); mesh.RecalculateBounds();
        var materials = new List<Material>(skin.sharedMaterials); materials.Add(neckSkin.sharedMaterial);
        patches.Add(new SurfacePatch { renderer = skin, original = source, filled = mesh, originalMaterials = skin.sharedMaterials, filledMaterials = materials.ToArray() });
        ownedMeshes.Add(mesh); ColoredTriangles += inside.Count / 3;
        SetInteriorVisible(true);
    }

    public void SetInteriorVisible(bool visible)
    {
        foreach (var patch in patches) if (patch.renderer != null)
        {
            patch.renderer.sharedMesh = visible ? patch.filled : patch.original;
            patch.renderer.sharedMaterials = visible ? patch.filledMaterials : patch.originalMaterials;
        }
        foreach (var cap in caps) if (cap != null) cap.enabled = visible;
    }

    void OnEnable() => SetInteriorVisible(true);
    void OnDisable() => SetInteriorVisible(false);

    static float NeckWeight(int index, float weight, int head, int neck) => index == head || index == neck ? weight : 0f;

    static List<int> TopBoundary(Mesh source, Vector3[] points)
    {
        float tolerance = Mathf.Max(source.bounds.size.y * .0001f, .000001f);
        var welded = new Dictionary<Vector3Int, int>(); var representative = new List<int>(); var ids = new int[points.Length];
        for (int i = 0; i < points.Length; i++)
        {
            var p = points[i]; var key = new Vector3Int(Mathf.RoundToInt(p.x / tolerance), Mathf.RoundToInt(p.y / tolerance), Mathf.RoundToInt(p.z / tolerance));
            if (!welded.TryGetValue(key, out int id)) { id = representative.Count; welded.Add(key, id); representative.Add(i); }
            ids[i] = id;
        }
        var edges = new Dictionary<ulong, int>(); var triangles = source.triangles;
        for (int i = 0; i + 2 < triangles.Length; i += 3)
        {
            CountEdge(ids[triangles[i]], ids[triangles[i + 1]], edges);
            CountEdge(ids[triangles[i + 1]], ids[triangles[i + 2]], edges);
            CountEdge(ids[triangles[i + 2]], ids[triangles[i]], edges);
        }
        float top = source.bounds.max.y, planeTolerance = Mathf.Max(source.bounds.size.y * .002f, tolerance * 2);
        var indices = new HashSet<int>();
        foreach (var edge in edges)
        {
            if (edge.Value != 1) continue;
            int a = representative[(int)(edge.Key >> 32)], b = representative[(int)(edge.Key & uint.MaxValue)];
            if (Mathf.Abs(points[a].y - top) > planeTolerance || Mathf.Abs(points[b].y - top) > planeTolerance) continue;
            indices.Add(a); indices.Add(b);
        }
        var ring = new List<int>(indices); Vector3 center = Vector3.zero;
        foreach (int i in ring) center += points[i];
        if (ring.Count > 0) center /= ring.Count;
        ring.Sort((a, b) => Mathf.Atan2(points[a].z - center.z, points[a].x - center.x).CompareTo(Mathf.Atan2(points[b].z - center.z, points[b].x - center.x)));
        return ring;
    }

    static void CountEdge(int a, int b, Dictionary<ulong, int> edges)
    {
        if (a == b) return;
        ulong key = ((ulong)(uint)Mathf.Min(a, b) << 32) | (uint)Mathf.Max(a, b);
        edges.TryGetValue(key, out int count); edges[key] = count + 1;
    }

    static Mesh Cap(Mesh source, List<int> ring, Vector3[] points, Color[] sourceColors, BoneWeight[] sourceWeights)
    {
        int count = ring.Count;
        var vertices = new Vector3[count + 1]; var normals = new Vector3[vertices.Length];
        var colors = new Color[vertices.Length]; var weights = new BoneWeight[vertices.Length];
        var triangles = new int[count * 3];
        Vector3 center = Vector3.zero; Color color = Color.clear; var totals = new Dictionary<int, float>();
        for (int i = 0; i < count; i++)
        {
            int sourceIndex = ring[i]; center += points[sourceIndex]; color += sourceColors[sourceIndex];
            var w = sourceWeights[sourceIndex];
            AddWeight(totals, w.boneIndex0, w.weight0); AddWeight(totals, w.boneIndex1, w.weight1);
            AddWeight(totals, w.boneIndex2, w.weight2); AddWeight(totals, w.boneIndex3, w.weight3);
        }
        center /= count; color /= count;
        // 경계 바로 안쪽에 얇은 덮개를 둔다. 목 바깥으로 돌출하는 원통은 만들지 않는다.
        for (int i = 0; i < count; i++)
        {
            int sourceIndex = ring[i]; vertices[i] = Vector3.Lerp(points[sourceIndex], center, .002f) - Vector3.up * .0001f;
            normals[i] = Vector3.up; colors[i] = sourceColors[sourceIndex]; weights[i] = sourceWeights[sourceIndex];
            triangles[i * 3] = count; triangles[i * 3 + 1] = (i + 1) % count; triangles[i * 3 + 2] = i;
        }
        vertices[count] = center - Vector3.up * .0001f; normals[count] = Vector3.up; colors[count] = color; weights[count] = PackWeights(totals);
        var mesh = new Mesh { name = "Neck Interior Skin (Instance)", hideFlags = HideFlags.DontSave };
        mesh.vertices = vertices; mesh.normals = normals; mesh.colors = colors; mesh.boneWeights = weights;
        mesh.bindposes = source.bindposes; mesh.SetTriangles(triangles, 0); mesh.RecalculateBounds(); return mesh;
    }

    static BoneWeight PackWeights(Dictionary<int, float> weights)
    {
        var ranked = new List<KeyValuePair<int, float>>(weights); ranked.Sort((a, b) => b.Value.CompareTo(a.Value));
        float total = 0f; for (int i = 0; i < Mathf.Min(4, ranked.Count); i++) total += ranked[i].Value;
        var packed = new BoneWeight();
        if (total <= 0f) return packed;
        if (ranked.Count > 0) { packed.boneIndex0 = ranked[0].Key; packed.weight0 = ranked[0].Value / total; }
        if (ranked.Count > 1) { packed.boneIndex1 = ranked[1].Key; packed.weight1 = ranked[1].Value / total; }
        if (ranked.Count > 2) { packed.boneIndex2 = ranked[2].Key; packed.weight2 = ranked[2].Value / total; }
        if (ranked.Count > 3) { packed.boneIndex3 = ranked[3].Key; packed.weight3 = ranked[3].Value / total; }
        return packed;
    }

    static void AddWeight(Dictionary<int, float> weights, int index, float value)
    {
        if (value <= 0f) return;
        weights.TryGetValue(index, out float current); weights[index] = current + value;
    }

    public void Dispose()
    {
        SetInteriorVisible(false); patches.Clear();
        foreach (var cap in caps) if (cap != null) { if (Application.isPlaying) Destroy(cap.gameObject); else DestroyImmediate(cap.gameObject); }
        caps.Clear(); ColoredTriangles = 0;
        foreach (var mesh in ownedMeshes)
            if (mesh != null) { if (Application.isPlaying) Destroy(mesh); else DestroyImmediate(mesh); }
        ownedMeshes.Clear();
    }

    void OnDestroy() => Dispose();
}
