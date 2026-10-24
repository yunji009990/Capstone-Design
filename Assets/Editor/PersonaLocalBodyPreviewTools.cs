using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using Object = UnityEngine.Object;

/// <summary>개인 자료를 씬에 저장하지 않는 새 몸체의 로컬 입장·착석 검사.</summary>
public static class PersonaLocalBodyPreviewTools
{
    const string ToggleMenu = "Tools/다시봄/캐릭터/새 몸체 로컬 시험 사용";
    const string Folder = "tools/_work/body_candidates/rocketbox/private/unity_validation";
    static PersonaSpawner watched;
    static double started, lastSample;
    static readonly List<string> phases = new List<string>();
    [Serializable] sealed class FloorFrame
    {
        public float seconds, distance, floorY, feetY, rootY, rootAboveFloor;
        public string phase;
    }
    [Serializable] sealed class FloorFrames { public List<FloorFrame> samples = new List<FloorFrame>(); }
    static FloorFrames floorFrames;

    [Serializable]
    sealed class Report
    {
        public string utc, scene, preparation, phase, status;
        public bool playing, sceneDirty, loaded, ready, newBody, active, humanoidValid, shadersSupported, finiteVertices;
        public int mappedBones, skins, vertices;
        public float visibleHeight;
        public float modelSeatHeightOffset, seatSurfaceY, pelvisBottomY, seatGap, feetBottomY;
        public bool seatContactMeasured;
        public string[] visitedPhases;
        public string[] clipPaths;
    }

    [MenuItem(ToggleMenu, priority = 21)]
    public static void Toggle()
    {
        var settings = PersonaLocalBodyPreview.ReadSettings();
        if (settings == null)
        {
            Debug.LogWarning("새 몸체 로컬 시험본이 준비되지 않았습니다.");
            return;
        }
        settings.enabled = !settings.enabled;
        File.WriteAllText(PersonaLocalBodyPreview.SettingsPath, JsonUtility.ToJson(settings, true));
        Debug.Log("새 몸체 로컬 시험 " + (settings.enabled ? "사용" : "해제") + " — 다음 Play부터 적용합니다.");
    }

    [MenuItem(ToggleMenu, true)]
    static bool ToggleValid()
    {
        try { Menu.SetChecked(ToggleMenu, PersonaLocalBodyPreview.ReadSettings()?.enabled == true); }
        catch { Menu.SetChecked(ToggleMenu, false); }
        return !EditorApplication.isPlayingOrWillChangePlaymode;
    }

    [MenuItem("Tools/다시봄/개발 도구/캐릭터 검사/새 몸체 상태 기록")]
    public static void Inspect()
    {
        var report = Record("상태 확인");
        Debug.Log($"[NewBodyPreview] loaded={report.loaded}, ready={report.ready}, newBody={report.newBody}, " +
            $"humanoid={report.humanoidValid}, phase={report.phase}, shaders={report.shadersSupported}");
    }

    [MenuItem("Tools/다시봄/개발 도구/캐릭터 검사/새 몸체 입장·착석 검사 (Play)")]
    public static void Begin()
    {
        var spawner = Object.FindObjectOfType<PersonaSpawner>(true);
        if (!EditorApplication.isPlaying || spawner == null || !spawner.ModelReady ||
            PersonaHumanoid.Mapping(spawner.Spawned.transform) != PersonaHumanoid.RocketboxMap)
        {
            Inspect();
            Debug.LogWarning("Play에서 새 몸체 준비가 완료된 뒤 검사해 주세요.");
            return;
        }
        EditorApplication.update -= Watch;
        phases.Clear();
        floorFrames = new FloorFrames();
        watched = spawner;
        started = EditorApplication.timeSinceStartup;
        lastSample = 0;
        // 체험 시작과 같은 등장 경로를 쓰며 마이크·서버 대화는 시작하지 않는다.
        spawner.EndPresentation();
        spawner.BeginPresentation();
        EditorApplication.update += Watch;
        Record("입장 검사 중");
    }

    static void Watch()
    {
        double now = EditorApplication.timeSinceStartup;
        if (now - lastSample < .25) return;
        lastSample = now;
        if (!EditorApplication.isPlaying || watched == null)
        {
            Finish("Play 종료로 검사 중단");
            return;
        }
        string phase = watched.arrival != null ? watched.arrival.phase.ToString() : "";
        if (watched.arrival != null && watched.arrival.groundPath != null &&
            (phase == "서서대기" || phase == "걷기" || phase == "인사"))
        {
            var report = Record("이동 바닥 높이 검사");
            floorFrames.samples.Add(new FloorFrame
            {
                seconds = (float)(now - started), phase = phase,
                distance = watched.arrival.PathDistance, floorY = watched.arrival.groundPath.FloorHeight,
                feetY = report.feetBottomY, rootY = watched.Spawned.transform.position.y,
                rootAboveFloor = watched.arrival.RootHeightAboveFloor,
            });
            File.WriteAllText(Folder + "/floor_samples.json", JsonUtility.ToJson(floorFrames, true));
        }
        if (!phases.Contains(phase))
        {
            phases.Add(phase);
            Record("입장 검사 중");
            if (phase == "서서대기" || phase == "걷기" || phase == "인사" || phase == "앉음") Capture(phase, false);
        }
        if (phase == "앉음" && now - started > 3)
        {
            Capture("착석_체험자시점", true);
            Finish("착석 도달");
        }
        else if (now - started > 120) Finish("입장 검사 시간 초과");
    }

    static void Finish(string status)
    {
        EditorApplication.update -= Watch;
        var report = Record(status);
        Debug.Log($"[NewBodyPreview] {status}: newBody={report.newBody}, humanoid={report.humanoidValid}, finite={report.finiteVertices}");
        watched = null;
    }

    static Report Record(string status)
    {
        var spawner = Object.FindObjectOfType<PersonaSpawner>(true);
        var model = spawner != null ? spawner.Spawned : null;
        var skins = model != null ? model.GetComponentsInChildren<SkinnedMeshRenderer>(true) : Array.Empty<SkinnedMeshRenderer>();
        var report = new Report
        {
            utc = DateTime.UtcNow.ToString("o"), scene = UnityEngine.SceneManagement.SceneManager.GetActiveScene().name,
            sceneDirty = UnityEngine.SceneManagement.SceneManager.GetActiveScene().isDirty,
            playing = EditorApplication.isPlaying, loaded = model != null, ready = spawner != null && spawner.ModelReady,
            preparation = spawner != null ? spawner.PreparationMessage : "스포너 없음",
            phase = spawner != null && spawner.arrival != null ? spawner.arrival.phase.ToString() : "",
            status = status, skins = skins.Length, active = model != null && model.activeInHierarchy,
            newBody = model != null && PersonaHumanoid.Mapping(model.transform) == PersonaHumanoid.RocketboxMap,
            humanoidValid = model != null && model.GetComponentsInChildren<Animator>(true).Any(a => a.avatar != null && a.avatar.isValid && a.avatar.isHuman),
            shadersSupported = skins.Length > 0 && skins.SelectMany(s => s.sharedMaterials).All(m => m != null && m.shader != null && m.shader.isSupported),
            visitedPhases = phases.ToArray(), finiteVertices = true,
            modelSeatHeightOffset = spawner != null && spawner.arrival != null ? spawner.arrival.ModelSeatHeightOffset : 0f,
            clipPaths = spawner != null && spawner.arrival != null ? new[] { spawner.arrival.idleClip, spawner.arrival.walkClip, spawner.arrival.seatedClip }.Select(AssetDatabase.GetAssetPath).ToArray() : Array.Empty<string>(),
        };
        if (model != null)
            report.mappedBones = PersonaHumanoid.Mapping(model.transform).Count(p => model.GetComponentsInChildren<Transform>(true).Any(t => t.name == p.tripo));
        Bounds bounds = new Bounds(); bool any = false;
        var hip = model != null ? PersonaHumanoid.FindBone(model.transform, HumanBodyBones.Hips) : null;
        var leftThigh = model != null ? PersonaHumanoid.FindBone(model.transform, HumanBodyBones.LeftUpperLeg) : null;
        var rightThigh = model != null ? PersonaHumanoid.FindBone(model.transform, HumanBodyBones.RightUpperLeg) : null;
        Vector3 facing = model != null ? PersonaHumanoid.MeasureFacing(model.transform) : Vector3.forward;
        float pelvisBottom = float.PositiveInfinity;
        foreach (var skin in skins)
        {
            var mesh = new Mesh();
            try
            {
                skin.BakeMesh(mesh);
                var points = mesh.vertices;
                var weights = skin.sharedMesh.boneWeights;
                var bones = skin.bones;
                for (int i = 0; i < points.Length; i++)
                {
                    // BakeMesh 결과에는 스킨 배율이 반영돼 있다. 배율을 다시 곱하지 않는다.
                    Vector3 world = skin.transform.position + skin.transform.rotation * points[i];
                    if (float.IsNaN(world.x) || float.IsNaN(world.y) || float.IsNaN(world.z) ||
                        float.IsInfinity(world.x) || float.IsInfinity(world.y) || float.IsInfinity(world.z)) report.finiteVertices = false;
                    if (!any) { bounds = new Bounds(world, Vector3.zero); any = true; } else bounds.Encapsulate(world);
                    if (hip != null && weights.Length == points.Length)
                    {
                        var weight = weights[i];
                        float influence = PelvisWeight(weight.boneIndex0, weight.weight0, bones, hip, leftThigh, rightThigh) +
                            PelvisWeight(weight.boneIndex1, weight.weight1, bones, hip, leftThigh, rightThigh) +
                            PelvisWeight(weight.boneIndex2, weight.weight2, bones, hip, leftThigh, rightThigh) +
                            PelvisWeight(weight.boneIndex3, weight.weight3, bones, hip, leftThigh, rightThigh);
                        Vector3 delta = world - hip.position;
                        float along = Vector3.Dot(delta, facing);
                        if (influence > .5f && delta.y < .02f && delta.y > -.25f && along < .04f && along > -.2f)
                            pelvisBottom = Mathf.Min(pelvisBottom, world.y);
                    }
                }
                report.vertices += mesh.vertexCount;
            }
            finally { Object.DestroyImmediate(mesh); }
        }
        report.visibleHeight = bounds.size.y;
        report.feetBottomY = bounds.min.y;
        if (hip != null && !float.IsPositiveInfinity(pelvisBottom))
        {
            var hit = Physics.RaycastAll(hip.position + Vector3.up * .12f, Vector3.down, .9f, ~0, QueryTriggerInteraction.Ignore)
                .Where(h => !h.transform.IsChildOf(model.transform) && h.collider.name.StartsWith("Chair", StringComparison.OrdinalIgnoreCase) && h.normal.y > .5f)
                .OrderBy(h => h.distance).FirstOrDefault();
            if (hit.collider != null)
            {
                report.seatContactMeasured = true; report.seatSurfaceY = hit.point.y;
                report.pelvisBottomY = pelvisBottom; report.seatGap = pelvisBottom - hit.point.y;
            }
        }
        Directory.CreateDirectory(Folder);
        File.WriteAllText(Folder + "/runtime.json", JsonUtility.ToJson(report, true));
        return report;
    }

    static float PelvisWeight(int index, float weight, Transform[] bones, Transform hip, Transform leftThigh, Transform rightThigh)
    {
        if (weight <= 0f || index >= bones.Length) return 0f;
        var bone = bones[index];
        return bone == hip || bone == leftThigh || bone == rightThigh ? weight : 0f;
    }

    static void Capture(string label, bool playerView)
    {
        if (watched == null || watched.Spawned == null) return;
        var source = Camera.main;
        var holder = new GameObject("Temporary Body Review Camera") { hideFlags = HideFlags.HideAndDontSave };
        var camera = holder.AddComponent<Camera>();
        var target = new RenderTexture(960, 960, 24);
        var texture = new Texture2D(960, 960, TextureFormat.RGB24, false);
        var previous = RenderTexture.active;
        try
        {
            if (source != null) camera.CopyFrom(source);
            camera.stereoTargetEye = StereoTargetEyeMask.None;
            camera.enabled = false; camera.nearClipPlane = .05f; camera.farClipPlane = 150f;
            if (playerView && source != null) camera.transform.SetPositionAndRotation(source.transform.position, source.transform.rotation);
            else
            {
                var root = watched.Spawned.transform;
                var hip = PersonaHumanoid.FindBone(root, HumanBodyBones.Hips);
                var head = PersonaHumanoid.FindBone(root, HumanBodyBones.Head);
                Vector3 center = hip != null && head != null ? (hip.position + head.position) * .5f : root.position + Vector3.up;
                Vector3 forward = PersonaHumanoid.MeasureFacing(root);
                Vector3 right = Vector3.Cross(Vector3.up, forward).normalized;
                camera.transform.position = center + forward * 2.8f + right * .65f + Vector3.up * .05f;
                camera.transform.LookAt(center);
                camera.fieldOfView = 42f;
            }
            camera.targetTexture = target; camera.aspect = 1f; camera.Render();
            RenderTexture.active = target;
            texture.ReadPixels(new Rect(0, 0, 960, 960), 0, 0); texture.Apply();
            File.WriteAllBytes(Folder + "/" + label + ".private.png", texture.EncodeToPNG());
        }
        finally
        {
            RenderTexture.active = previous; camera.targetTexture = null;
            target.Release(); Object.DestroyImmediate(target); Object.DestroyImmediate(texture); Object.DestroyImmediate(holder);
        }
    }
}
