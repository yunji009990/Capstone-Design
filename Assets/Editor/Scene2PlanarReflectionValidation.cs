// 운영 인물·서버 없이 평면 반사의 실제 렌더링 위치, 배율, 리소스 해제를 확인한다.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json;
using UnityEditor;
using UnityEngine;
using UnityEngine.Rendering.Universal;

public static class Scene2PlanarReflectionValidation
{
    const int Size = 800;

    [MenuItem("Tools/다시봄/그래픽/평면 반사 렌더링 검사")]
    public static void Validate()
    {
        var scene = Scene2GraphicsCapture.RequireScene();
        var owner = scene.GetRootGameObjects().SelectMany(go => go.GetComponentsInChildren<CafePlanarReflection>())
            .Single();
        if (!owner.enabled) throw new InvalidOperationException("평면 반사를 켠 뒤 검사하세요.");
        var mirrors = Scene2GraphicsCapture.CafeRenderers().Where(r =>
            r.sharedMaterials.Any(m => m != null && (m.name == "PlanarMirror" || m.name == "OvalWindowGlass"))).ToArray();
        var windows = Scene2GraphicsCapture.CafeRenderers().Where(r =>
            r.name == "window_glass_l1" || r.name == "window_glass_r").ToArray();
        var surfaces = mirrors.Concat(windows).ToArray();
        var originalLayers = surfaces.Select(r => r.gameObject.layer).ToArray();
        var originalMaterials = surfaces.Select(r => r.sharedMaterials).ToArray();
        var resources = new List<UnityEngine.Object>();
        var samples = new List<object>();
        var oldActive = RenderTexture.active;
        try
        {
            foreach (var renderer in surfaces) renderer.gameObject.layer = 31;
            // 창의 약한 반사를 측정할 때만 불투명한 임시 재질로 바꾼다. 원본 재질은 수정하지 않는다.
            var windowMaterial = new Material(windows[0].sharedMaterial);
            resources.Add(windowMaterial);
            windowMaterial.SetFloat("_ReflectionOpacity", 1f);
            windowMaterial.SetFloat("_GrazingOpacity", 1f);
            windowMaterial.SetColor("_BaseColor", new Color(1f, 1f, 1f, 0f));
            // 위치·배율 측정은 반사 강도와 독립적으로 한다. 약하게 만든 벽 표면도 같은 임시 재질을 쓴다.
            foreach (var renderer in surfaces) renderer.sharedMaterial = windowMaterial;
            var cameraObject = new GameObject("__PlanarValidation") { hideFlags = HideFlags.HideAndDontSave };
            resources.Add(cameraObject);
            var camera = cameraObject.AddComponent<Camera>();
            camera.enabled = false;
            camera.cullingMask = 1 << 31;
            camera.stereoTargetEye = StereoTargetEyeMask.None;
            camera.fieldOfView = 60f;
            camera.aspect = 1f;
            camera.nearClipPlane = .05f;
            camera.farClipPlane = 30f;
            camera.clearFlags = CameraClearFlags.SolidColor;
            camera.backgroundColor = Color.black;
            camera.GetUniversalAdditionalCameraData().allowXRRendering = false;
            var target = new RenderTexture(Size, Size, 24, RenderTextureFormat.ARGB32);
            resources.Add(target);
            target.Create();
            camera.targetTexture = target;
            var pixels = new Texture2D(Size, Size, TextureFormat.RGB24, false);
            resources.Add(pixels);
            var markers = new GameObject[2];
            for (int i = 0; i < markers.Length; i++)
            {
                var marker = GameObject.CreatePrimitive(PrimitiveType.Cube);
                marker.hideFlags = HideFlags.HideAndDontSave;
                marker.name = "__PlanarMarker";
                marker.layer = 31;
                resources.Add(marker);
                markers[i] = marker;
                marker.transform.localScale = Vector3.one * .16f;
                var material = new Material(Shader.Find("Universal Render Pipeline/Unlit"));
                resources.Add(material);
                material.SetColor("_BaseColor", i == 0 ? Color.green : Color.magenta);
                marker.GetComponent<Renderer>().sharedMaterial = material;
            }
            foreach (bool window in new[] { false, true })
            {
                var surface = (window ? windows : mirrors).OrderByDescending(r => r.bounds.size.sqrMagnitude).First();
                var points = window ? new[]
                {
                    new Vector3(surface.bounds.center.x - .24f, surface.bounds.center.y - .18f, -2.5f),
                    new Vector3(surface.bounds.center.x + .24f, surface.bounds.center.y + .18f, -2.5f)
                } : new[]
                {
                    new Vector3(-1.4f, surface.bounds.center.y - .18f, surface.bounds.center.z - .24f),
                    new Vector3(-1.4f, surface.bounds.center.y + .18f, surface.bounds.center.z + .24f)
                };
                for (int i = 0; i < markers.Length; i++) markers[i].transform.position = points[i];
                foreach (float offset in new[] { -.032f, .032f, .4f })
                {
                    var eye = window ? new Vector3(surface.bounds.center.x + offset, surface.bounds.center.y, .2f) :
                        new Vector3(-2.5f, surface.bounds.center.y, surface.bounds.center.z + offset);
                    camera.transform.SetPositionAndRotation(eye, Quaternion.Euler(0f, window ? 0f : -90f, 0f));
                    camera.Render();
                    RenderTexture.active = target;
                    pixels.ReadPixels(new Rect(0, 0, Size, Size), 0, 0);
                    pixels.Apply();
                    var data = pixels.GetPixels32();
                    for (int markerIndex = 0; markerIndex < points.Length; markerIndex++)
                    {
                        long sumX = 0, sumY = 0;
                        int count = 0, minX = Size, maxX = -1;
                        for (int index = 0; index < data.Length; index++)
                        {
                            var color = data[index];
                            bool match = markerIndex == 0 ? color.g > 110 && color.r < 35 && color.b < 35 :
                                color.r > 110 && color.b > 110 && color.g < 35;
                            if (!match) continue;
                            int x = index % Size, y = index / Size;
                            sumX += x;
                            sumY += y;
                            minX = Mathf.Min(minX, x);
                            maxX = Mathf.Max(maxX, x);
                            count++;
                        }
                        if (count < 20) throw new InvalidOperationException("반사 표본을 렌더링하지 못했습니다.");
                        var point = points[markerIndex];
                        // 실제 창·거울 표면과 대칭인 허상 위치를 독립적으로 계산한다.
                        if (window) point.z = 2f * surface.bounds.min.z - point.z;
                        else point.x = 2f * surface.bounds.max.x - point.x;
                        var expected = camera.WorldToViewportPoint(point);
                        var observed = new Vector2((float)sumX / count + .5f, (float)sumY / count + .5f);
                        float positionError = Vector2.Distance(observed, new Vector2(expected.x, expected.y) * Size);
                        var horizontal = window ? Vector3.right : Vector3.forward;
                        var edgeA = camera.WorldToViewportPoint(point + horizontal * .08f);
                        var edgeB = camera.WorldToViewportPoint(point - horizontal * .08f);
                        float expectedWidth = Mathf.Abs(edgeA.x - edgeB.x) * Size;
                        float observedWidth = maxX - minX + 1;
                        float widthError = Mathf.Abs(observedWidth - expectedWidth);
                        if (positionError > 4f || widthError > expectedWidth * .15f + 2f)
                            throw new InvalidOperationException("반사의 위치·배율·상하 방향이 실제 투영과 다릅니다.");
                        samples.Add(new { surface = window ? "window" : "mirror", offset, markerIndex, positionErrorPixels = positionError,
                            expectedWidthPixels = expectedWidth, observedWidthPixels = observedWidth });
                    }
                }
            }
            owner.enabled = false;
            int remainingTextures = Resources.FindObjectsOfTypeAll<RenderTexture>().Count(t => t.name == "CafePlanarEye");
            int remainingCameras = Resources.FindObjectsOfTypeAll<Camera>().Count(c => c.name == "__CafePlanarCamera");
            if (remainingTextures != 0 || remainingCameras != 0)
                throw new InvalidOperationException("비활성화 후 반사 리소스가 남았습니다.");
            Directory.CreateDirectory(Scene2GraphicsCapture.OutputDirectory);
            File.WriteAllText(Path.Combine(Scene2GraphicsCapture.OutputDirectory, "planar_render_validation.json"),
                JsonConvert.SerializeObject(new { passed = true, cases = samples.Count, samples,
                    remainingTextures, remainingCameras, hardwareStereoTested = false }, Formatting.Indented));
            Debug.Log("[Scene2Graphics] 평면 반사 렌더링: 창·거울 12개 위치·배율 표본 및 리소스 해제 통과. HMD 검사는 별도.");
        }
        finally
        {
            RenderTexture.active = oldActive;
            for (int i = 0; i < surfaces.Length; i++)
            {
                surfaces[i].gameObject.layer = originalLayers[i];
                surfaces[i].sharedMaterials = originalMaterials[i];
            }
            owner.enabled = false;
            foreach (var resource in resources.AsEnumerable().Reverse())
            {
                if (resource is RenderTexture texture) texture.Release();
                if (resource != null) UnityEngine.Object.DestroyImmediate(resource);
            }
            owner.enabled = true;
        }
    }
}
