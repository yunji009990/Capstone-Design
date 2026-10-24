using System;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

/// <summary>카페의 저장된 크기·의자·카메라를 읽고, 저장되지 않는 체형 미리보기만 배치한다.</summary>
public static class PersonaBodyPreviewCafe
{
    public const string ScenePath = "Assets/Scenes/Scene_2.unity";
    const string EnabledKey = "PersonaBodyPreview.CafeEnabled";
    public static bool Enabled { get => SessionState.GetBool(EnabledKey, false); set => SessionState.SetBool(EnabledKey, value); }
    public static bool IsActive => Enabled && SceneManager.GetActiveScene().path == ScenePath;
    public static float TargetHeight { get; private set; }
    public static float ScaleMultiplier { get; private set; } = 1f;
    public static float NormalizationScale { get; private set; } = 1f;
    public static float EyeHeight
    {
        get => SessionState.GetFloat("PersonaBodyPreview.EyeHeight", 1.2f);
        set
        {
            if (float.IsNaN(value) || float.IsInfinity(value)) return;
            SessionState.SetFloat("PersonaBodyPreview.EyeHeight", Mathf.Clamp(value, .8f, 2.1f));
            PlayerView();
        }
    }
    public static Vector3 PlayerEyePosition
    {
        get
        {
            var source = SourceCamera;
            if (source == null) return Vector3.zero;
            var position = source.transform.position;
            float floor = Arrival != null && Arrival.groundPath != null ? Arrival.groundPath.FloorHeight : source.transform.root.position.y;
            position.y = floor + EyeHeight;
            return position;
        }
    }
    public static string ScaleDescription => TargetHeight > 0f
        ? $"카페 기준 키 {TargetHeight * 100f:0.#}cm × 추가 배율 {ScaleMultiplier:0.##}"
        : $"카페 원본 크기 × 추가 배율 {ScaleMultiplier:0.##}";
    static Camera previewCamera;

    internal static PersonaSpawner Spawner(Scene scene) => scene.GetRootGameObjects()
        .SelectMany(root => root.GetComponentsInChildren<PersonaSpawner>(true)).FirstOrDefault();
    internal static PersonaSpawner CurrentSpawner => IsActive ? Spawner(SceneManager.GetActiveScene()) : null;
    internal static PersonaArrival Arrival => CurrentSpawner != null ? CurrentSpawner.arrival : null;

    internal static void Normalize(GameObject model)
    {
        // 스튜디오도 카페와 같은 수치를 사용한다. 잠깐 읽는 씬은 저장하거나 활성 씬으로 바꾸지 않는다.
        var scene = SceneManager.GetSceneByPath(ScenePath);
        bool temporary = !scene.IsValid() || !scene.isLoaded;
        if (temporary) scene = EditorSceneManager.OpenScene(ScenePath, OpenSceneMode.Additive);
        try
        {
            var spawner = Spawner(scene);
            if (spawner == null) throw new InvalidOperationException("카페에서 PersonaSpawner를 찾지 못했습니다.");
            TargetHeight = spawner.targetHeightMeters;
            ScaleMultiplier = spawner.scaleMultiplier > 0f && Mathf.Abs(spawner.scaleMultiplier - 1f) > .0001f ? spawner.scaleMultiplier : 1f;
            NormalizationScale = 1f;
            var renderers = model.GetComponentsInChildren<Renderer>(true);
            if (TargetHeight > 0f && renderers.Length > 0)
            {
                var bounds = renderers[0].bounds;
                foreach (var renderer in renderers.Skip(1)) bounds.Encapsulate(renderer.bounds);
                if (bounds.size.y > .0001f) NormalizationScale = TargetHeight / bounds.size.y;
            }
            // PersonaSpawner와 동일하게 원본 메시 상태에서 정규화하고 그 뒤 체형 프로필을 적용한다.
            model.transform.localScale *= NormalizationScale * ScaleMultiplier;
        }
        finally { if (temporary) EditorSceneManager.CloseScene(scene, true); }
    }

    internal static AnimationClip Clip(PersonaBodyPreviewScene.Pose pose) => Arrival == null ? null
        : pose == PersonaBodyPreviewScene.Pose.앉기 ? Arrival.seatedClip : Arrival.idleClip;

    internal static void Place()
    {
        var model = PersonaBodyPreviewScene.Model;
        var spawner = CurrentSpawner;
        if (model == null || spawner == null) return;
        var arrival = spawner.arrival;
        var seat = arrival != null && arrival.seat != null ? arrival.seat
            : spawner.spawnPoint != null ? spawner.spawnPoint : spawner.transform;
        var facing = Vector3.ProjectOnPlane(PersonaHumanoid.MeasureFacing(model.transform), Vector3.up);
        var wanted = Vector3.ProjectOnPlane(seat.forward, Vector3.up);
        if (facing.sqrMagnitude > .0001f && wanted.sqrMagnitude > .0001f)
            model.transform.rotation = Quaternion.FromToRotation(facing, wanted) * model.transform.rotation;
        if (PersonaBodyPreviewScene.CurrentPose == PersonaBodyPreviewScene.Pose.앉기)
        {
            // 착석 때는 발 기준으로 다시 띄우지 않는다. 운영 연출과 같은 루트 위치를 쓴다.
            model.transform.position = seat.position + (arrival != null ? arrival.seatOffset : Vector3.zero);
            return;
        }
        Vector3 floor = seat.position;
        if (arrival != null && arrival.groundPath != null && arrival.groundPath.isActiveAndEnabled && arrival.groundPath.PointCount >= 2)
            floor = arrival.groundPath.GetWorldPoint(arrival.groundPath.PointCount - 1);
        else if (arrival != null && arrival.waypoints != null && arrival.waypoints.LastOrDefault(t => t != null) != null)
            floor = arrival.waypoints.Last(t => t != null).position;
        model.transform.position = floor;
        var bounds = PersonaBodyPreviewScene.Bounds();
        model.transform.position += Vector3.up * (floor.y - bounds.min.y);
    }

    internal static Camera SourceCamera => !IsActive ? null : SceneManager.GetActiveScene().GetRootGameObjects()
        .SelectMany(root => root.GetComponentsInChildren<Camera>(true))
        .Where(camera => camera.name != PersonaBodyPreviewScene.CameraName)
        .OrderByDescending(camera => camera.CompareTag("MainCamera")).ThenByDescending(camera => camera.enabled).FirstOrDefault();

    internal static Camera Camera()
    {
        if (!IsActive) return null;
        if (previewCamera != null) return previewCamera;
        var go = new GameObject(PersonaBodyPreviewScene.CameraName) { hideFlags = HideFlags.DontSaveInEditor };
        SceneManager.MoveGameObjectToScene(go, SceneManager.GetActiveScene());
        previewCamera = go.AddComponent<Camera>();
        previewCamera.hideFlags = HideFlags.DontSaveInEditor;
        var source = SourceCamera;
        if (source != null) previewCamera.CopyFrom(source);
        previewCamera.stereoTargetEye = StereoTargetEyeMask.None;
        previewCamera.targetTexture = null;
        previewCamera.targetDisplay = 0;
        previewCamera.depth = 100f;
        previewCamera.orthographic = false;
        previewCamera.fieldOfView = 60f;
        previewCamera.nearClipPlane = .03f;
        previewCamera.enabled = true;
        return previewCamera;
    }

    public static void PlayerView()
    {
        var camera = Camera();
        var source = SourceCamera;
        if (camera == null || source == null) return;
        camera.transform.SetPositionAndRotation(PlayerEyePosition, source.transform.rotation);
        AlignSceneView(camera, Vector3.Distance(camera.transform.position, PersonaBodyPreviewScene.Bounds().center));
        PersonaBodyPreviewScene.Repaint();
    }

    internal static void Frame(Vector3 center, Vector3 direction, float size)
    {
        var camera = Camera();
        if (camera == null) return;
        var facing = PersonaHumanoid.MeasureFacing(PersonaBodyPreviewScene.Model.transform).normalized;
        if (Mathf.Abs(Vector3.Dot(direction, facing)) < .25f)
        {
            // 벽에 붙은 의자는 카페 안쪽에서 측면을 본다. 벽에 콜라이더가 없는 소품도 있다.
            var renderers = Scene2GraphicsCapture.CafeRenderers();
            if (renderers.Length > 0)
            {
                var cafeBounds = renderers[0].bounds;
                foreach (var renderer in renderers.Skip(1)) cafeBounds.Encapsulate(renderer.bounds);
                if (Vector3.Dot(direction, cafeBounds.center - center) < 0f) direction = -direction;
            }
        }
        float distance = size / Mathf.Tan(camera.fieldOfView * .5f * Mathf.Deg2Rad);
        // 카페 벽 바깥에서 촬영하지 않도록 배경 콜라이더 앞에서 멈춘다.
        var model = PersonaBodyPreviewScene.Model.transform;
        foreach (var hit in Physics.RaycastAll(center, direction, distance, ~0, QueryTriggerInteraction.Ignore).OrderBy(hit => hit.distance))
        {
            if (hit.transform.IsChildOf(model)) continue;
            distance = Mathf.Max(.2f, hit.distance - .15f);
            break;
        }
        camera.transform.SetPositionAndRotation(center + direction * distance, Quaternion.LookRotation(-direction, Vector3.up));
        AlignSceneView(camera, distance);
    }

    static void AlignSceneView(Camera camera, float distance)
    {
        var view = SceneView.lastActiveSceneView;
        if (view == null) return;
        view.LookAt(camera.transform.position + camera.transform.forward * Mathf.Max(.1f, distance), camera.transform.rotation,
            Mathf.Max(.1f, distance) * Mathf.Sin(view.camera.fieldOfView * .5f * Mathf.Deg2Rad), false, true);
    }

    internal static void Clear()
    {
        if (previewCamera != null) Object.DestroyImmediate(previewCamera.gameObject);
        previewCamera = null;
    }
}
