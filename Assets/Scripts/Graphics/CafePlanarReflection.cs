using System;
using System.Collections.Generic;
using UnityEngine;
using UnityEngine.Rendering;
using UnityEngine.Rendering.Universal;

// 평면을 기준으로 눈 위치와 투영을 대칭시킨다. 파노라마를 확대해 붙이지 않는다.
[ExecuteAlways, DisallowMultipleComponent]
public sealed class CafePlanarReflection : MonoBehaviour
{
    [Serializable]
    public sealed class Surface
    {
        public Transform plane;
        public Renderer[] renderers;
        [Range(128, 1024)] public int textureResolution = 512;
    }

    [SerializeField] Surface[] surfaces = Array.Empty<Surface>();
    [SerializeField, Range(1, 3)] int frameInterval = 1;
    [SerializeField] float clipOffset = .005f;
    readonly Dictionary<Camera, Frame> frames = new Dictionary<Camera, Frame>();
    readonly List<Camera> expired = new List<Camera>();
    readonly Plane[] frustum = new Plane[6];
    readonly Plane[] rightFrustum = new Plane[6];
    readonly List<Renderer> hidden = new List<Renderer>();
    readonly List<bool> wasHidden = new List<bool>();
    bool rendering;
    bool stereoFrustum;
    int visibleLayers;

    static readonly int LeftTexture = Shader.PropertyToID("_PlanarLeft");
    static readonly int RightTexture = Shader.PropertyToID("_PlanarRight");
    static readonly int LeftMatrix = Shader.PropertyToID("_PlanarMatrixLeft");
    static readonly int RightMatrix = Shader.PropertyToID("_PlanarMatrixRight");
    static readonly int ValidTexture = Shader.PropertyToID("_PlanarValid");

    sealed class Frame
    {
        public Camera camera;
        public Slice[] slices;
        public int lastFrame = -10;
    }

    sealed class Slice
    {
        public RenderTexture left, right;
        public Matrix4x4 leftMatrix, rightMatrix;
        public MaterialPropertyBlock block = new MaterialPropertyBlock();
        public bool valid;
    }

    public void Configure(Surface[] configuredSurfaces)
    {
        ClearFrames();
        surfaces = configuredSurfaces ?? Array.Empty<Surface>();
    }

    void OnEnable()
    {
        RenderPipelineManager.beginCameraRendering -= Render;
        RenderPipelineManager.beginCameraRendering += Render;
    }

    void OnDisable()
    {
        RenderPipelineManager.beginCameraRendering -= Render;
        ClearFrames();
        foreach (var surface in surfaces)
        {
            if (surface == null || surface.renderers == null) continue;
            foreach (var renderer in surface.renderers)
                if (renderer != null) renderer.SetPropertyBlock(null);
        }
    }

    void OnDestroy() => ClearFrames();

    void Render(ScriptableRenderContext context, Camera source)
    {
        if (rendering || source == null || surfaces.Length == 0 ||
            source.cameraType == CameraType.Reflection || source.cameraType == CameraType.Preview)
            return;
        if (source.TryGetComponent<UniversalAdditionalCameraData>(out var sourceData) &&
            sourceData.renderType == CameraRenderType.Overlay) return;
        expired.Clear();
        foreach (var entry in frames)
            if (entry.Key == null) expired.Add(entry.Key);
        foreach (var key in expired)
        {
            Dispose(frames[key]);
            frames.Remove(key);
        }
        if (!frames.TryGetValue(source, out var frame))
        {
            var go = new GameObject("__CafePlanarCamera") { hideFlags = HideFlags.HideAndDontSave };
            frame = new Frame { camera = go.AddComponent<Camera>(), slices = new Slice[surfaces.Length] };
            frame.camera.enabled = false;
            frame.camera.cameraType = CameraType.Reflection;
            for (int i = 0; i < surfaces.Length; i++) frame.slices[i] = new Slice();
            frames.Add(source, frame);
        }
        bool update = !Application.isPlaying || Time.frameCount - frame.lastFrame >= frameInterval;
        visibleLayers = source.cullingMask;
        stereoFrustum = source.stereoEnabled;
        if (stereoFrustum)
        {
            GeometryUtility.CalculateFrustumPlanes(source.GetStereoProjectionMatrix(Camera.StereoscopicEye.Left) *
                source.GetStereoViewMatrix(Camera.StereoscopicEye.Left), frustum);
            GeometryUtility.CalculateFrustumPlanes(source.GetStereoProjectionMatrix(Camera.StereoscopicEye.Right) *
                source.GetStereoViewMatrix(Camera.StereoscopicEye.Right), rightFrustum);
        }
        else GeometryUtility.CalculateFrustumPlanes(source, frustum);
        bool invertCulling = GL.invertCulling;
        rendering = true;
        hidden.Clear();
        wasHidden.Clear();
        // 자기 표면과 다른 반사 표면을 숨겨 반사의 재귀 렌더링을 막는다.
        foreach (var surface in surfaces)
        {
            if (surface == null || surface.renderers == null) continue;
            foreach (var renderer in surface.renderers)
            {
                if (renderer == null) continue;
                hidden.Add(renderer);
                wasHidden.Add(renderer.forceRenderingOff);
                renderer.forceRenderingOff = true;
            }
        }
        try
        {
            for (int i = 0; i < surfaces.Length; i++)
            {
                var surface = surfaces[i];
                if (surface == null || surface.plane == null || !Visible(surface)) continue;
                var slice = frame.slices[i];
                if (update || !slice.valid)
                {
                    ConfigureCamera(frame.camera, source);
                    bool stereo = source.stereoEnabled;
                    EnsureTexture(ref slice.left, source, surface.textureResolution);
                    slice.leftMatrix = RenderEye(context, frame.camera, source, surface.plane, slice.left,
                        stereo ? source.GetStereoViewMatrix(Camera.StereoscopicEye.Left) : source.worldToCameraMatrix,
                        stereo ? source.GetStereoProjectionMatrix(Camera.StereoscopicEye.Left) : source.projectionMatrix,
                        invertCulling);
                    if (stereo)
                    {
                        EnsureTexture(ref slice.right, source, surface.textureResolution);
                        slice.rightMatrix = RenderEye(context, frame.camera, source, surface.plane, slice.right,
                            source.GetStereoViewMatrix(Camera.StereoscopicEye.Right),
                            source.GetStereoProjectionMatrix(Camera.StereoscopicEye.Right), invertCulling);
                    }
                    else slice.rightMatrix = slice.leftMatrix;
                    slice.valid = true;
                }
                foreach (var renderer in surface.renderers)
                {
                    if (renderer == null) continue;
                    renderer.GetPropertyBlock(slice.block);
                    slice.block.SetTexture(LeftTexture, slice.left);
                    slice.block.SetTexture(RightTexture, source.stereoEnabled ? slice.right : slice.left);
                    slice.block.SetMatrix(LeftMatrix, slice.leftMatrix);
                    slice.block.SetMatrix(RightMatrix, slice.rightMatrix);
                    slice.block.SetFloat(ValidTexture, 1f);
                    renderer.SetPropertyBlock(slice.block);
                }
            }
            if (update) frame.lastFrame = Time.frameCount;
        }
        finally
        {
            GL.invertCulling = invertCulling;
            for (int i = 0; i < hidden.Count; i++)
                if (hidden[i] != null) hidden[i].forceRenderingOff = wasHidden[i];
            rendering = false;
        }
    }

    bool Visible(Surface surface)
    {
        if (surface.renderers == null) return false;
        foreach (var renderer in surface.renderers)
            if (renderer != null && renderer.enabled && renderer.gameObject.activeInHierarchy &&
                (visibleLayers & (1 << renderer.gameObject.layer)) != 0 &&
                (GeometryUtility.TestPlanesAABB(frustum, renderer.bounds) ||
                 (stereoFrustum && GeometryUtility.TestPlanesAABB(rightFrustum, renderer.bounds)))) return true;
        return false;
    }

    void ConfigureCamera(Camera target, Camera source)
    {
        target.CopyFrom(source);
        target.enabled = false;
        target.cameraType = CameraType.Reflection;
        target.stereoTargetEye = StereoTargetEyeMask.None;
        target.allowHDR = true;
        target.allowMSAA = false;
        target.useOcclusionCulling = false;
        target.clearFlags = CameraClearFlags.Skybox;
        var data = target.GetUniversalAdditionalCameraData();
        data.allowXRRendering = false;
        data.renderPostProcessing = false;
        data.renderShadows = false;
        data.requiresColorOption = CameraOverrideOption.Off;
        data.requiresDepthOption = CameraOverrideOption.Off;
    }

    Matrix4x4 RenderEye(ScriptableRenderContext context, Camera target, Camera source, Transform plane,
        RenderTexture texture, Matrix4x4 sourceView, Matrix4x4 projection, bool originalCulling)
    {
        var normal = plane.forward.normalized;
        var eye = sourceView.inverse.MultiplyPoint(Vector3.zero);
        // 양쪽에서 보아도 클립 평면은 원래 관찰자가 있는 공간을 남긴다.
        if (Vector3.Dot(normal, eye - plane.position) < 0f) normal = -normal;
        var reflection = ReflectionMatrix(new Vector4(normal.x, normal.y, normal.z, -Vector3.Dot(normal, plane.position)));
        var view = sourceView * reflection;
        var inverse = view.inverse;
        target.transform.SetPositionAndRotation(inverse.MultiplyPoint(Vector3.zero),
            Quaternion.LookRotation(-inverse.GetColumn(2), inverse.GetColumn(1)));
        target.worldToCameraMatrix = view;
        target.projectionMatrix = projection;
        var point = view.MultiplyPoint(plane.position + normal * clipOffset);
        var clipNormal = view.MultiplyVector(normal).normalized;
        target.projectionMatrix = target.CalculateObliqueMatrix(new Vector4(clipNormal.x, clipNormal.y, clipNormal.z,
            -Vector3.Dot(point, clipNormal)));
        target.targetTexture = texture;
        GL.invertCulling = !originalCulling;
        // URP 14의 콜백 내부에서는 같은 ScriptableRenderContext의 독립 카메라 렌더링을 사용한다.
        // API는 이 버전에서 지원되며 새 RenderRequest 경로로 옮기면 콜백 중첩도 함께 검증해야 한다.
#pragma warning disable CS0618
        UniversalRenderPipeline.RenderSingleCamera(context, target);
#pragma warning restore CS0618
        GL.invertCulling = originalCulling;
        // 렌더 텍스처의 UV를 계산하므로, 렌더 대상용 GPU 투영의 Y 반전을 다시 적용하지 않는다.
        return GL.GetGPUProjectionMatrix(target.projectionMatrix, false) * view;
    }

    public static Matrix4x4 ReflectionMatrix(Vector4 plane)
    {
        var m = Matrix4x4.identity;
        for (int row = 0; row < 3; row++)
        {
            for (int col = 0; col < 3; col++) m[row, col] -= 2f * plane[row] * plane[col];
            m[row, 3] = -2f * plane.w * plane[row];
        }
        return m;
    }

    void EnsureTexture(ref RenderTexture texture, Camera source, int surfaceResolution)
    {
        int size = Mathf.Clamp(surfaceResolution > 0 ? surfaceResolution : 512, 128, 1024);
        float aspect = source.stereoEnabled ? 1f : Mathf.Max(.25f, source.aspect);
        int width = aspect >= 1f ? size : Mathf.RoundToInt(size * aspect);
        int height = aspect >= 1f ? Mathf.RoundToInt(size / aspect) : size;
        if (texture != null && texture.width == width && texture.height == height) return;
        Release(texture);
        texture = new RenderTexture(width, height, 24, RenderTextureFormat.ARGBHalf)
        {
            name = "CafePlanarEye", hideFlags = HideFlags.HideAndDontSave,
            filterMode = FilterMode.Bilinear, wrapMode = TextureWrapMode.Clamp
        };
        texture.Create();
    }

    void ClearFrames()
    {
        foreach (var frame in frames.Values) Dispose(frame);
        frames.Clear();
    }

    static void Dispose(Frame frame)
    {
        foreach (var slice in frame.slices) { Release(slice.left); Release(slice.right); }
        if (frame.camera != null) Release(frame.camera.gameObject);
    }

    static void Release(UnityEngine.Object value)
    {
        if (value == null) return;
        if (value is RenderTexture texture) texture.Release();
        if (Application.isPlaying) Destroy(value); else DestroyImmediate(value);
    }
}
