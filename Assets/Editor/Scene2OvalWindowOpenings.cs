// 판매자 메시를 보존하고 Scene_2 전용 벽 복제에 실제 창 개구부를 만든다.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using Unity.Collections;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

public static class Scene2OvalWindowOpenings
{
    const string Root = "Assets/Graphics/Scene2/";
    const string SourceTag = "CafeOvalWindowSource:";
    const string UndoName = "타원 창 개구부";

    internal static void EnsureConfigured()
    {
        // 반사 강도만 다시 조정할 때는 벽의 UV2와 완료한 조명 베이크를 다시 만들지 않는다.
        if (Scene2GraphicsCapture.CafeRenderers().OfType<MeshRenderer>().Any(r =>
            r.TryGetComponent<MeshFilter>(out var filter) && filter.sharedMesh != null &&
            AssetDatabase.GetAssetPath(filter.sharedMesh).StartsWith(Root + "CafeWallOval_", StringComparison.Ordinal))) return;
        Apply();
    }

    struct Vertex
    {
        public Vector3 position, normal;
        public Vector4 tangent;
        public Vector2 uv, lightmapUV;
        public static Vertex Lerp(Vertex a, Vertex b, float t) => new Vertex
        {
            position = Vector3.Lerp(a.position, b.position, t), normal = Vector3.Lerp(a.normal, b.normal, t).normalized,
            tangent = Vector4.Lerp(a.tangent, b.tangent, t), uv = Vector2.Lerp(a.uv, b.uv, t),
            lightmapUV = Vector2.Lerp(a.lightmapUV, b.lightmapUV, t)
        };
    }

    [MenuItem("Tools/다시봄/그래픽/타원 창 개구부 적용")]
    public static void Apply()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        if (Lightmapping.isRunning) throw new InvalidOperationException("조명 베이크가 끝난 뒤 적용하세요.");
        var cafe = Scene2GraphicsCapture.CafeRenderers();
        var windows = cafe.Where(r => r.sharedMaterials.Any(m => m != null &&
            (m.name == "Mirror" || m.name == "PlanarMirror" || m.name == "OvalWindowGlass"))).ToArray();
        if (windows.Length != 2) throw new InvalidOperationException("타원 창 두 곳을 확인하세요.");
        var holes = windows.Select(Contour).ToArray();
        var reports = new List<object>();
        float innerX = float.NegativeInfinity, outerX = float.PositiveInfinity;
        foreach (var renderer in cafe.OfType<MeshRenderer>().Where(r => r.sharedMaterials.Any(m => m != null &&
                     (m.name.StartsWith("Internal_Walls", StringComparison.Ordinal) || m.name == "Outside_Wall"))))
        {
            if (!renderer.TryGetComponent<MeshFilter>(out var filter) || filter.sharedMesh == null) continue;
            var source = Original(filter.sharedMesh);
            var input = Read(source, out bool hasLightmapUV);
            var output = new List<Vertex>();
            var submeshes = new List<int[]>();
            int removed = 0;
            for (int submesh = 0; submesh < source.subMeshCount; submesh++)
            {
                var triangles = source.GetTriangles(submesh);
                var result = new List<int>();
                for (int i = 0; i < triangles.Length; i += 3)
                {
                    var polygon = new List<Vertex> { input[triangles[i]], input[triangles[i + 1]], input[triangles[i + 2]] };
                    var world = polygon.Select(v => filter.transform.TransformPoint(v.position)).ToArray();
                    bool leftWall = world.All(p => p.x > -4.6f && p.x < -3.7f) &&
                        Mathf.Abs(Vector3.Cross(world[1] - world[0], world[2] - world[0]).normalized.x) > .9f;
                    var pieces = new List<List<Vertex>> { polygon };
                    if (leftWall)
                    {
                        innerX = Mathf.Max(innerX, world.Max(p => p.x));
                        outerX = Mathf.Min(outerX, world.Min(p => p.x));
                        float oldArea = Area(pieces, filter.transform);
                        foreach (var hole in holes)
                        {
                            var next = new List<List<Vertex>>();
                            foreach (var piece in pieces) next.AddRange(Subtract(piece, hole, filter.transform));
                            pieces = next;
                        }
                        if (oldArea - Area(pieces, filter.transform) > .00001f) removed++;
                    }
                    foreach (var piece in pieces)
                    {
                        int first = output.Count;
                        output.AddRange(piece);
                        for (int j = 1; j + 1 < piece.Count; j++)
                            if (Vector3.Cross(piece[j].position - piece[0].position,
                                piece[j + 1].position - piece[0].position).sqrMagnitude > 1e-12f)
                                result.AddRange(new[] { first, first + j, first + j + 1 });
                    }
                }
                submeshes.Add(result.ToArray());
            }
            if (removed == 0) continue;
            var path = AssetDatabase.GetAssetPath(filter.sharedMesh);
            if (!path.StartsWith(Root + "CafeWallOval_", StringComparison.Ordinal))
                path = Root + "CafeWallOval_" + GlobalObjectId.GetGlobalObjectIdSlow(renderer).targetObjectId + ".asset";
            var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
            if (mesh == null)
            {
                mesh = new Mesh();
                AssetDatabase.CreateAsset(mesh, path);
            }
            Undo.RecordObject(mesh, UndoName);
            mesh.Clear();
            mesh.name = renderer.name + "_OvalWindows";
            mesh.indexFormat = output.Count > 65535 ? IndexFormat.UInt32 : IndexFormat.UInt16;
            mesh.SetVertices(output.Select(v => v.position).ToList());
            mesh.SetNormals(output.Select(v => v.normal).ToList());
            mesh.SetTangents(output.Select(v => v.tangent).ToList());
            mesh.SetUVs(0, output.Select(v => v.uv).ToList());
            if (hasLightmapUV) mesh.SetUVs(1, output.Select(v => v.lightmapUV).ToList());
            mesh.subMeshCount = submeshes.Count;
            for (int i = 0; i < submeshes.Count; i++) mesh.SetTriangles(submeshes[i], i);
            mesh.RecalculateBounds();
            // 원본 벽의 연속된 UV2를 보존한다. 잘린 조각을 따로 펼치면 삼각형마다 조명 이음새가 생긴다.
            if (!hasLightmapUV) Unwrapping.GenerateSecondaryUVSet(mesh);
            EditorUtility.SetDirty(mesh);
            AssetDatabase.TryGetGUIDAndLocalFileIdentifier(source, out string guid, out long id);
            var importer = AssetImporter.GetAtPath(path);
            importer.userData = SourceTag + guid + ":" + id;
            importer.SaveAndReimport();
            Undo.RecordObject(filter, UndoName);
            filter.sharedMesh = mesh;
            PrefabUtility.RecordPrefabInstancePropertyModifications(filter);
            if (renderer.TryGetComponent<MeshCollider>(out var collider) &&
                (collider.sharedMesh == source || collider.sharedMesh == mesh))
            {
                Undo.RecordObject(collider, UndoName);
                collider.sharedMesh = mesh;
                PrefabUtility.RecordPrefabInstancePropertyModifications(collider);
            }
            reports.Add(new { renderer.name, modifiedTriangles = removed, source = AssetDatabase.GetAssetPath(source),
                mesh = path, vertices = mesh.vertexCount, preservedLightmapUV = hasLightmapUV });
        }
        if (reports.Count == 0) throw new InvalidOperationException("창 뒤의 벽 메시를 찾지 못했습니다.");
        Reveals(holes, innerX, outerX);
        AssetDatabase.SaveAssets();
        EditorSceneManager.MarkSceneDirty(scene);
        EditorSceneManager.SaveScene(scene);
        Directory.CreateDirectory(Scene2GraphicsCapture.OutputDirectory);
        File.WriteAllText(Path.Combine(Scene2GraphicsCapture.OutputDirectory, "oval_window_openings.json"),
            JsonConvert.SerializeObject(new { walls = reports,
                contours = holes.Select(h => h.Select(p => new[] { p.x, p.y }).ToArray()).ToArray() }, Formatting.Indented));
        Debug.Log("[Scene2Graphics] 원본을 보존한 타원 창 벽 개구부 적용 완료. 조명을 다시 베이크하세요.");
    }

    static void Reveals(List<Vector2>[] holes, float innerX, float outerX)
    {
        if (!float.IsFinite(innerX) || !float.IsFinite(outerX) || innerX - outerX < .001f)
            throw new InvalidOperationException("창 개구부 벽 두께를 확인하세요.");
        const string path = Root + "OvalWindowReveals.asset";
        var mesh = AssetDatabase.LoadAssetAtPath<Mesh>(path);
        if (mesh == null)
        {
            mesh = new Mesh { name = "OvalWindowReveals" };
            AssetDatabase.CreateAsset(mesh, path);
        }
        var vertices = new List<Vector3>();
        var uv = new List<Vector2>();
        var indices = new List<int>();
        foreach (var hole in holes)
        {
            float distance = 0f;
            for (int i = 0; i < hole.Count; i++)
            {
                var a = hole[i];
                var b = hole[(i + 1) % hole.Count];
                float next = distance + Vector2.Distance(a, b);
                int first = vertices.Count;
                vertices.AddRange(new[] { new Vector3(innerX, a.y, a.x), new Vector3(outerX, a.y, a.x),
                    new Vector3(outerX, b.y, b.x), new Vector3(innerX, b.y, b.x) });
                uv.AddRange(new[] { new Vector2(0f, distance), new Vector2(innerX - outerX, distance),
                    new Vector2(innerX - outerX, next), new Vector2(0f, next) });
                indices.AddRange(new[] { first, first + 1, first + 2, first, first + 2, first + 3 });
                distance = next;
            }
        }
        Undo.RecordObject(mesh, UndoName);
        mesh.Clear();
        mesh.SetVertices(vertices);
        mesh.SetUVs(0, uv);
        mesh.SetTriangles(indices, 0);
        mesh.RecalculateNormals();
        mesh.RecalculateBounds();
        Unwrapping.GenerateSecondaryUVSet(mesh);
        EditorUtility.SetDirty(mesh);
        var background = Scene2GraphicsCapture.SceneObject("04_카페 배경").transform;
        var found = background.Find("타원 창 벽 안쪽");
        var go = found != null ? found.gameObject : new GameObject("타원 창 벽 안쪽");
        if (found == null)
        {
            go.transform.SetParent(background, false);
            Undo.RegisterCreatedObjectUndo(go, UndoName);
        }
        var filter = go.TryGetComponent<MeshFilter>(out var existingFilter) ? existingFilter : Undo.AddComponent<MeshFilter>(go);
        var renderer = go.TryGetComponent<MeshRenderer>(out var existingRenderer) ? existingRenderer : Undo.AddComponent<MeshRenderer>(go);
        Undo.RecordObjects(new UnityEngine.Object[] { filter, renderer }, UndoName);
        filter.sharedMesh = mesh;
        renderer.sharedMaterial = AssetDatabase.LoadAssetAtPath<Material>(Root + "Materials/GardenPlaster.mat");
        renderer.receiveGI = ReceiveGI.Lightmaps;
        renderer.shadowCastingMode = ShadowCastingMode.TwoSided;
        GameObjectUtility.SetStaticEditorFlags(go, StaticEditorFlags.ContributeGI | StaticEditorFlags.BatchingStatic);
    }

    static Mesh Original(Mesh current)
    {
        var path = AssetDatabase.GetAssetPath(current);
        var importer = AssetImporter.GetAtPath(path);
        if (importer == null || !importer.userData.StartsWith(SourceTag, StringComparison.Ordinal)) return current;
        var fields = importer.userData.Substring(SourceTag.Length).Split(':');
        long id = long.Parse(fields[1]);
        var original = AssetDatabase.LoadAllAssetsAtPath(AssetDatabase.GUIDToAssetPath(fields[0])).OfType<Mesh>()
            .FirstOrDefault(m => AssetDatabase.TryGetGUIDAndLocalFileIdentifier(m, out string _, out long value) && value == id);
        if (original == null) throw new InvalidOperationException("벽의 원본 메시를 찾지 못했습니다.");
        return original;
    }

    static Vertex[] Read(Mesh mesh) => Read(mesh, out _);

    static Vertex[] Read(Mesh mesh, out bool hasLightmapUV)
    {
        using (var snapshot = MeshUtility.AcquireReadOnlyMeshData(mesh))
        using (var positions = new NativeArray<Vector3>(mesh.vertexCount, Allocator.Temp))
        using (var normals = new NativeArray<Vector3>(mesh.vertexCount, Allocator.Temp))
        using (var tangents = new NativeArray<Vector4>(mesh.vertexCount, Allocator.Temp))
        using (var uv = new NativeArray<Vector2>(mesh.vertexCount, Allocator.Temp))
        using (var lightmapUV = new NativeArray<Vector2>(mesh.vertexCount, Allocator.Temp))
        {
            var data = snapshot[0];
            data.GetVertices(positions);
            if (data.HasVertexAttribute(VertexAttribute.Normal)) data.GetNormals(normals);
            if (data.HasVertexAttribute(VertexAttribute.Tangent)) data.GetTangents(tangents);
            if (data.HasVertexAttribute(VertexAttribute.TexCoord0)) data.GetUVs(0, uv);
            hasLightmapUV = data.HasVertexAttribute(VertexAttribute.TexCoord1);
            if (hasLightmapUV) data.GetUVs(1, lightmapUV);
            return Enumerable.Range(0, mesh.vertexCount).Select(i => new Vertex
                { position = positions[i], normal = normals[i], tangent = tangents[i], uv = uv[i], lightmapUV = lightmapUV[i] }).ToArray();
        }
    }

    static List<Vector2> Contour(Renderer renderer)
    {
        var mesh = renderer.GetComponent<MeshFilter>().sharedMesh;
        var input = Read(mesh);
        int index = Array.FindIndex(renderer.sharedMaterials, m => m != null &&
            (m.name == "Mirror" || m.name == "PlanarMirror" || m.name == "OvalWindowGlass"));
        var points = mesh.GetTriangles(index).Distinct().Select(i => renderer.transform.TransformPoint(input[i].position))
            .Select(p => new Vector2(p.z, p.y)).Distinct().OrderBy(p => p.x).ThenBy(p => p.y).ToArray();
        var hull = new List<Vector2>();
        foreach (var point in points)
        {
            while (hull.Count >= 2 && Cross(hull[hull.Count - 1] - hull[hull.Count - 2], point - hull[hull.Count - 1]) <= 0f)
                hull.RemoveAt(hull.Count - 1);
            hull.Add(point);
        }
        int lower = hull.Count;
        for (int i = points.Length - 2; i >= 0; i--)
        {
            while (hull.Count > lower && Cross(hull[hull.Count - 1] - hull[hull.Count - 2], points[i] - hull[hull.Count - 1]) <= 0f)
                hull.RemoveAt(hull.Count - 1);
            hull.Add(points[i]);
        }
        hull.RemoveAt(hull.Count - 1);
        var center = new Vector2(hull.Average(p => p.x), hull.Average(p => p.y));
        return hull.Select(p => center + (p - center) * .995f).ToList();
    }

    static List<List<Vertex>> Subtract(List<Vertex> polygon, List<Vector2> hole, Transform transform)
    {
        var result = new List<List<Vertex>>();
        var remaining = polygon;
        for (int edge = 0; edge < hole.Count && remaining.Count >= 3; edge++)
        {
            var a = hole[edge];
            var b = hole[(edge + 1) % hole.Count];
            var outside = Clip(remaining, a, b, false, transform);
            if (outside.Count >= 3) result.Add(outside);
            remaining = Clip(remaining, a, b, true, transform);
        }
        return result;
    }

    static List<Vertex> Clip(List<Vertex> polygon, Vector2 a, Vector2 b, bool inside, Transform transform)
    {
        var result = new List<Vertex>();
        float Distance(Vertex vertex)
        {
            var world = transform.TransformPoint(vertex.position);
            return Cross(b - a, new Vector2(world.z, world.y) - a) * (inside ? 1f : -1f);
        }
        for (int i = 0; i < polygon.Count; i++)
        {
            var current = polygon[i];
            var next = polygon[(i + 1) % polygon.Count];
            float currentDistance = Distance(current), nextDistance = Distance(next);
            bool currentInside = currentDistance >= 0f, nextInside = nextDistance >= 0f;
            if (currentInside) result.Add(current);
            if (currentInside != nextInside)
                result.Add(Vertex.Lerp(current, next, currentDistance / (currentDistance - nextDistance)));
        }
        return result;
    }

    static float Area(List<List<Vertex>> polygons, Transform transform)
    {
        float area = 0f;
        foreach (var polygon in polygons)
        for (int i = 1; i + 1 < polygon.Count; i++)
            area += Vector3.Cross(transform.TransformVector(polygon[i].position - polygon[0].position),
                transform.TransformVector(polygon[i + 1].position - polygon[0].position)).magnitude * .5f;
        return area;
    }

    static float Cross(Vector2 a, Vector2 b) => a.x * b.y - a.y * b.x;

    [MenuItem("Tools/다시봄/그래픽/타원 창 외부 가시성 검사")]
    public static void ValidateVisibility()
    {
        Scene2GraphicsCapture.RequireScene();
        var renderers = Scene2GraphicsCapture.CafeRenderers();
        var windows = renderers.Where(r => r.sharedMaterials.Any(m => m != null && m.name == "OvalWindowGlass")).ToArray();
        var wall = renderers.OfType<MeshRenderer>().Select(r => r.GetComponent<MeshFilter>()).Where(f => f != null &&
            AssetDatabase.GetAssetPath(f.sharedMesh).StartsWith(Root + "CafeWallOval_", StringComparison.Ordinal)).Single();
        var cutMesh = wall.sharedMesh;
        var originalMesh = Original(cutMesh);
        var objects = new List<UnityEngine.Object>();
        var samples = new List<object>();
        var oldActive = RenderTexture.active;
        const int size = 512;
        try
        {
            var marker = GameObject.CreatePrimitive(PrimitiveType.Cube);
            marker.hideFlags = HideFlags.HideAndDontSave;
            marker.name = "__OvalOutsideMarker";
            marker.transform.localScale = Vector3.one * .2f;
            objects.Add(marker);
            var material = new Material(Shader.Find("Universal Render Pipeline/Unlit"));
            material.SetColor("_BaseColor", Color.green);
            objects.Add(material);
            marker.GetComponent<Renderer>().sharedMaterial = material;
            var go = new GameObject("__OvalVisibilityCamera") { hideFlags = HideFlags.HideAndDontSave };
            objects.Add(go);
            var camera = go.AddComponent<Camera>();
            camera.enabled = false;
            camera.stereoTargetEye = StereoTargetEyeMask.None;
            camera.fieldOfView = 60f;
            camera.aspect = 1f;
            camera.nearClipPlane = .05f;
            camera.farClipPlane = 100f;
            camera.GetUniversalAdditionalCameraData().allowXRRendering = false;
            camera.GetUniversalAdditionalCameraData().renderPostProcessing = false;
            var target = new RenderTexture(size, size, 24, RenderTextureFormat.ARGB32);
            target.Create();
            objects.Add(target);
            camera.targetTexture = target;
            var texture = new Texture2D(size, size, TextureFormat.RGB24, false);
            objects.Add(texture);
            Directory.CreateDirectory(Scene2GraphicsCapture.OutputDirectory);
            int VisiblePixels(string diagnostic)
            {
                camera.Render();
                RenderTexture.active = target;
                texture.ReadPixels(new Rect(0, 0, size, size), 0, 0);
                texture.Apply();
                File.WriteAllBytes(Path.Combine(Scene2GraphicsCapture.OutputDirectory, "visibility_" + diagnostic + ".png"), texture.EncodeToPNG());
                var colors = texture.GetPixels32();
                var center = camera.WorldToViewportPoint(marker.transform.position) * size;
                int count = 0;
                for (int y = Mathf.Max(0, (int)center.y - 30); y < Mathf.Min(size, (int)center.y + 30); y++)
                for (int x = Mathf.Max(0, (int)center.x - 30); x < Mathf.Min(size, (int)center.x + 30); x++)
                {
                    var color = colors[y * size + x];
                    if (color.g > 180 && color.r < 20 && color.b < 20) count++;
                }
                return count;
            }
            foreach (var window in windows)
            foreach (float offset in new[] { -.032f, .032f, .35f })
            {
                var center = window.bounds.center;
                marker.transform.position = new Vector3(-6.25f, center.y, center.z);
                camera.transform.SetPositionAndRotation(new Vector3(-2.5f, center.y, center.z + offset),
                    Quaternion.Euler(0f, -90f, 0f));
                wall.sharedMesh = cutMesh;
                int visible = VisiblePixels("open_" + samples.Count);
                wall.sharedMesh = originalMesh;
                int blocked = VisiblePixels("blocked_" + samples.Count);
                wall.sharedMesh = cutMesh;
                if (visible < 30 || blocked > 2)
                    throw new InvalidOperationException("타원 창 외부 가시성/벽 차단: " + window.name + ", offset=" + offset +
                        ", visible=" + visible + ", blocked=" + blocked);
                samples.Add(new { window.name, offset, visibleMarkerPixels = visible, originalWallMarkerPixels = blocked });
            }
            Directory.CreateDirectory(Scene2GraphicsCapture.OutputDirectory);
            File.WriteAllText(Path.Combine(Scene2GraphicsCapture.OutputDirectory, "oval_window_visibility.json"),
                JsonConvert.SerializeObject(new { passed = true, cases = samples.Count, samples }, Formatting.Indented));
            Debug.Log("[Scene2Graphics] 타원 창 두 곳·좌우 눈·고개 이동에서 외부 물체가 보이고, 원본 벽은 가리는 대조 6건 통과.");
        }
        finally
        {
            wall.sharedMesh = cutMesh;
            RenderTexture.active = oldActive;
            foreach (var item in objects.AsEnumerable().Reverse())
            {
                if (item is RenderTexture texture) texture.Release();
                if (item != null) UnityEngine.Object.DestroyImmediate(item);
            }
        }
    }
}
