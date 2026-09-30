// 고정 몸체의 실제 메시·Humanoid 클립을 별도 미리보기 씬에서 검사한다.
// 현재 씬·모델 임포트 설정·서버 세션은 바꾸지 않는다.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using GLTFast;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.Animations;
using UnityEngine.Playables;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

public static class PersonaFixedBodyValidation
{
    const string BodyPath = "Assets/Models/human/Human.fbx";
    const string OutputFolder = "tools/_work/fixed_body_animation";
    static readonly string[] ClipNames =
    {
        "Idle", "Waving", PersonaArrivalSetup.WalkAnimationName, "Left Turn", "Sitting Idle", "Sitting Talking",
        "Walking", // 기존 Avatar 기준 자세의 손 간격 회귀 검사도 유지한다.
    };
    static bool running;

    [Serializable] public class ClipResult
    {
        public string model, clip;
        public float maxBoneAngle, maxVertexMovement, maxFingerAngle, maxBoundsRatio;
        public float minWalkingHandSpan;
    }
    [Serializable] public class Report
    {
        public string utc;
        public bool success, fbxPassed, glbPassed, retryPassed, rollbackPassed, idleEntryPassed, idleSkipPassed;
        public bool referencePoseUsed;
        public List<ClipResult> clips = new List<ClipResult>();
        public string error;
    }

    [MenuItem("Tools/Persona/고정 몸체 애니메이션 검사", priority = 3)]
    public static async void Run()
    {
        if (running || EditorApplication.isPlayingOrWillChangePlaymode) return;
        running = true;
        var report = new Report { utc = DateTime.UtcNow.ToString("o") };
        Directory.CreateDirectory(OutputFolder);
        Scene scene = EditorSceneManager.NewPreviewScene();
        GltfImport gltf = null;
        GameObject root = null;
        try
        {
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>(BodyPath);
            Require(asset != null, "고정 몸체 FBX를 찾지 못했다");
            root = NewRoot(scene);
            var body = (GameObject)PrefabUtility.InstantiatePrefab(asset, scene);
            body.transform.SetParent(root.transform, false);
            ValidateModel(root, "fbx", scene, report);
            report.fbxPassed = true;
            ValidateRetry(root, report);
            ValidateRollback(root, report);
            Object.DestroyImmediate(root);
            root = null;

            // 운영에 설정된 몸체 GLB를 이 경로에 복사하면 같은 런타임 로더로도 검사한다.
            string glbPath = Path.Combine(OutputFolder, "body.glb");
            if (File.Exists(glbPath))
            {
                gltf = new GltfImport(deferAgent: new UninterruptedDeferAgent());
                var settings = new ImportSettings { AnimationMethod = AnimationMethod.Legacy };
                Require(await gltf.Load(File.ReadAllBytes(glbPath), null, settings), "몸체 GLB 로드 실패");
                root = NewRoot(scene);
                Require(await gltf.InstantiateMainSceneAsync(root.transform), "몸체 GLB 인스턴스 생성 실패");
                // 실제 스폰처럼 부모의 위치·회전·배율이 있어도 본 길이와 동작이 유지돼야 한다.
                root.transform.SetPositionAndRotation(new Vector3(2, 1, -3), Quaternion.Euler(0, 35, 0));
                root.transform.localScale = Vector3.one * .7f;
                ValidateModel(root, "glb", scene, report);
                report.glbPassed = true;
                ValidateRetry(root, report, true);
                ValidateRollback(root, report, true);
            }
            report.success = true;
        }
        catch (Exception exception)
        {
            report.error = exception.ToString();
            Debug.LogError("[FixedBodyValidation] " + report.error);
        }
        finally
        {
            if (root != null) Object.DestroyImmediate(root);
            gltf?.Dispose();
            if (scene.IsValid()) EditorSceneManager.ClosePreviewScene(scene);
            File.WriteAllText(Path.Combine(OutputFolder, "report.json"), JsonUtility.ToJson(report, true));
            running = false;
            Debug.Log($"[FixedBodyValidation] success={report.success}, FBX={report.fbxPassed}, " +
                      $"GLB={report.glbPassed}, clips={report.clips.Count}, " +
                      $"retry={report.retryPassed}, rollback={report.rollbackPassed}; {OutputFolder}/report.json");
        }
    }

    static GameObject NewRoot(Scene scene)
    {
        var root = new GameObject("FixedBodyValidation") { hideFlags = HideFlags.HideAndDontSave };
        SceneManager.MoveGameObjectToScene(root, scene);
        return root;
    }

    static AnimationClip LoadClip(string name)
    {
        string folder = name == "Left Turn" ? "Assets/Models" : PersonaArrivalSetup.AnimationFolder;
        var clip = AssetDatabase.LoadAllAssetsAtPath(folder + "/" + name + ".fbx")
            .OfType<AnimationClip>().FirstOrDefault(c => !c.name.StartsWith("__preview__") && c.isHumanMotion);
        Require(clip != null, name + "의 Humanoid 클립이 없다");
        return clip;
    }

    static void ValidateModel(GameObject root, string label, Scene scene, Report report)
    {
        var legacy = root.GetComponentInChildren<Animation>();
        if (legacy != null) legacy.enabled = false;
        foreach (var skin in root.GetComponentsInChildren<SkinnedMeshRenderer>()) skin.updateWhenOffscreen = true;
        Capture(root, scene, label + "_original.png");
        var arm = PersonaHumanoid.FindBone(root.transform, HumanBodyBones.LeftUpperArm);
        var forearm = PersonaHumanoid.FindBone(root.transform, HumanBodyBones.LeftLowerArm);
        float armLength = Vector3.Distance(arm.position, forearm.position);
        Require(PersonaHumanoid.TryPrepare(root.transform, out var skeleton, out var avatar, out var error,
            label == "glb" ? root.transform : null, ReferencePose()), error);
        report.referencePoseUsed = true;
        Capture(root, scene, label + "_bind.png");
        Animator animator = skeleton.GetComponent<Animator>();
        if (animator == null) animator = skeleton.gameObject.AddComponent<Animator>();
        animator.avatar = avatar;
        animator.applyRootMotion = false;
        animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
        var mesh = new Mesh();
        PlayableGraph graph = default;
        try
        {
            Require(Mathf.Abs(Vector3.Distance(arm.position, forearm.position) / armLength - 1f) < .02f,
                    "기준 자세 복원 중 몸체의 팔 길이가 변했다");
            Require(avatar.isHuman && avatar.isValid, "Humanoid Avatar가 유효하지 않다");
            var mapping = PersonaHumanoid.Mapping(skeleton);
            var bones = mapping.Select(entry => animator.GetBoneTransform(
                (HumanBodyBones)Enum.Parse(typeof(HumanBodyBones), entry.human))).ToArray();
            Require(bones.All(b => b != null), "Humanoid에 연결되지 않은 본이 있다");
            var renderer = root.GetComponentsInChildren<SkinnedMeshRenderer>().OrderByDescending(r => r.bones.Length).First();
            renderer.BakeMesh(mesh);
            float originalExtent = mesh.bounds.size.magnitude;
            Debug.Log($"[FixedBodyValidation] {label}: bones={mapping.Length}, vertices={mesh.vertexCount}, " +
                      $"meshBounds={mesh.bounds}, rendererScale={renderer.transform.lossyScale}, " +
                      $"rendererBounds={renderer.bounds}");
            Require(originalExtent > .01f, "몸체 메시가 비었다");

            foreach (string name in ClipNames)
            {
                graph = PlayableGraph.Create("FixedBodyValidation");
                graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                var clip = LoadClip(name);
                var playable = AnimationClipPlayable.Create(graph, clip);
                playable.SetApplyFootIK(false);
                AnimationPlayableOutput.Create(graph, "body", animator).SetSourcePlayable(playable);
                graph.Play();
                playable.SetTime(0);
                graph.Evaluate(0);
                var rotations = bones.Select(b => b.localRotation).ToArray();
                renderer.BakeMesh(mesh);
                var startVertices = mesh.vertices;
                bool walking = name == "Walking" || name == PersonaArrivalSetup.WalkAnimationName;
                var result = new ClipResult { model = label, clip = name,
                    minWalkingHandSpan = walking ? float.MaxValue : 0f };
                for (int frame = 1; frame <= 8; frame++)
                {
                    playable.SetTime(clip.length * frame / 9f);
                    graph.Evaluate(0);
                    for (int b = 0; b < bones.Length; b++)
                    {
                        float angle = Quaternion.Angle(rotations[b], bones[b].localRotation);
                        result.maxBoneAngle = Mathf.Max(result.maxBoneAngle, angle);
                        if (b >= 22) result.maxFingerAngle = Mathf.Max(result.maxFingerAngle, angle);
                    }
                    renderer.BakeMesh(mesh);
                    var vertices = mesh.vertices;
                    Require(vertices.Length == startVertices.Length, "재생 중 정점 수 변경");
                    for (int v = 0; v < vertices.Length; v++)
                    {
                        var p = vertices[v];
                        Require(!float.IsNaN(p.sqrMagnitude) && !float.IsInfinity(p.sqrMagnitude), "메시가 유한 좌표를 벗어났다");
                        result.maxVertexMovement = Mathf.Max(result.maxVertexMovement, Vector3.Distance(p, startVertices[v]));
                    }
                    result.maxBoundsRatio = Mathf.Max(result.maxBoundsRatio, mesh.bounds.size.magnitude / originalExtent);
                    if (walking)
                    {
                        float span = Vector3.Distance(animator.GetBoneTransform(HumanBodyBones.LeftHand).position,
                            animator.GetBoneTransform(HumanBodyBones.RightHand).position) / Mathf.Abs(root.transform.lossyScale.x);
                        result.minWalkingHandSpan = Mathf.Min(result.minWalkingHandSpan, span);
                    }
                    if (frame == 4 && (walking || name == "Idle" || name == "Sitting Idle" || name == "Sitting Talking"))
                        Capture(root, scene, label + "_" + name.Replace(' ', '_') + ".png");
                }
                Require(result.maxBoneAngle > .1f && result.maxVertexMovement > .0001f, name + "에서 몸체가 움직이지 않았다");
                Require(result.maxBoundsRatio < 2.5f, name + "에서 메시가 과도하게 늘어났다");
                // 현재 Human용 원본 Walking의 손 간격은 약 0.55m 이상이다.
                // 잘못된 Avatar 기준 자세에서는 약 0.40m로 좁아졌던 회귀를 검사한다.
                if (name == "Walking") Require(result.minWalkingHandSpan > .50f,
                    "걷기 중 양손이 몸통 쪽으로 좁아졌다. 고정 몸체 Avatar 기준 자세를 확인할 것");
                report.clips.Add(result);
                graph.Destroy();
            }
        }
        finally
        {
            if (graph.IsValid()) graph.Destroy();
            animator.avatar = null;
            Object.DestroyImmediate(avatar);
            Object.DestroyImmediate(mesh);
        }
    }

    static void ValidateRetry(GameObject root, Report report, bool gltf = false)
    {
        var owner = NewRoot(root.scene);
        try
        {
            var spawner = owner.AddComponent<PersonaSpawner>();
            spawner.enabled = false; // 서버 조회·Start를 실행하지 않는다.
            var arrival = owner.AddComponent<PersonaArrival>();
            arrival.referencePose = ReferencePose();
            arrival.walkClip = LoadClip(PersonaArrivalSetup.WalkAnimationName);
            arrival.greetClip = LoadClip("Waving");
            arrival.greetFacesUser = false;
            typeof(PersonaSpawner).GetMethod("FindBreathBones", System.Reflection.BindingFlags.Instance | System.Reflection.BindingFlags.NonPublic)
                .Invoke(spawner, new object[] { root });
            Require(spawner.GazeReady, "고정 몸체의 머리 본을 시선 제어에 연결하지 못했다");
            for (int attempt = 0; attempt < 2; attempt++)
            {
                Require(arrival.TryBegin(root.transform, spawner, gltf ? root.transform : null), "반복 연결 실패");
                Require(spawner.posedExternally && arrival.phase == PersonaArrival.Phase.인사, "도착 연출 시작 실패");
            }
            report.retryPassed = true;

            arrival.idleClip = LoadClip("Idle");
            arrival.initialIdleSeconds = 1f;
            Require(arrival.TryBegin(root.transform, spawner, gltf ? root.transform : null), "Idle 연결 실패");
            Require(arrival.phase == PersonaArrival.Phase.서서대기, "서 있는 Idle부터 시작하지 않았다");
            report.idleEntryPassed = true;
            arrival.initialIdleSeconds = 0f;
            Require(arrival.TryBegin(root.transform, spawner, gltf ? root.transform : null), "Idle 생략 연결 실패");
            Require(arrival.phase == PersonaArrival.Phase.인사, "대기 시간이 0일 때 바로 인사하지 않았다");
            arrival.greetClip = null;
            Require(arrival.TryBegin(root.transform, spawner, gltf ? root.transform : null), "인사 생략 연결 실패");
            Require(arrival.phase == PersonaArrival.Phase.걷기, "인사가 없을 때 바로 걷지 않았다");
            report.idleSkipPassed = true;
        }
        finally { Object.DestroyImmediate(owner); }
    }

    static void ValidateRollback(GameObject root, Report report, bool gltf = false)
    {
        var head = PersonaHumanoid.FindBone(root.transform, HumanBodyBones.Head);
        string originalName = head.name;
        head.name = "unmapped_head_for_validation";
        var bones = root.GetComponentsInChildren<Transform>(true);
        var poses = bones.Select(b => new Pose(b.localPosition, b.localRotation)).ToArray();
        try
        {
            Require(!PersonaHumanoid.TryPrepare(root.transform, out _, out var avatar, out _, gltf ? root.transform : null,
                ReferencePose()), "누락된 본을 성공으로 처리했다");
            Require(avatar == null, "실패한 Avatar가 남았다");
            for (int i = 0; i < bones.Length; i++)
            {
                Require(Vector3.Distance(bones[i].localPosition, poses[i].position) < .00001f, "실패 뒤 본 위치가 변했다");
                Require(Quaternion.Angle(bones[i].localRotation, poses[i].rotation) < .1f, "실패 뒤 본 회전이 변했다");
            }
            report.rollbackPassed = true;
        }
        finally { head.name = originalName; }
    }

    static PersonaHumanoidReferencePose ReferencePose()
    {
        var pose = AssetDatabase.LoadAssetAtPath<PersonaHumanoidReferencePose>(PersonaReferencePoseSetup.AssetPath);
        Require(pose != null, "고정 몸체 기준 자세가 없다. 먼저 도착 연출 준비를 실행할 것");
        return pose;
    }

    static void Capture(GameObject root, Scene scene, string name)
    {
        var holder = NewRoot(scene);
        var snapshot = NewRoot(scene);
        var camera = holder.AddComponent<Camera>();
        var skins = root.GetComponentsInChildren<SkinnedMeshRenderer>();
        var enabled = skins.Select(s => s.enabled).ToArray();
        var meshes = new List<Mesh>();
        Bounds bounds = default;
        // 에디터에서 GPU 스킨 캐시가 첫 Render의 자세를 재사용하므로,
        // 실제 본 변형으로 BakeMesh한 현재 프레임을 임시 정적 메시로 그린다.
        for (int i = 0; i < skins.Length; i++)
        {
            var skin = skins[i];
            var mesh = new Mesh();
            skin.BakeMesh(mesh);
            meshes.Add(mesh);
            var copy = new GameObject("Posed body");
            copy.transform.SetParent(snapshot.transform, false);
            copy.transform.SetPositionAndRotation(skin.transform.position, skin.transform.rotation);
            copy.AddComponent<MeshFilter>().sharedMesh = mesh;
            var renderer = copy.AddComponent<MeshRenderer>();
            renderer.sharedMaterials = skin.sharedMaterials;
            if (i == 0) bounds = renderer.bounds;
            else bounds.Encapsulate(renderer.bounds);
            skin.enabled = false;
        }
        camera.scene = scene;
        camera.enabled = false;
        camera.clearFlags = CameraClearFlags.SolidColor;
        camera.backgroundColor = new Color(.13f, .16f, .2f);
        camera.nearClipPlane = .01f;
        camera.farClipPlane = 100f;
        camera.orthographic = true;
        camera.orthographicSize = Mathf.Max(bounds.size.y, bounds.size.x) * .7f;
        Vector3 facing = PersonaHumanoid.MeasureFacing(root.transform);
        camera.transform.position = bounds.center + (facing * 3f + Vector3.up * .6f) * bounds.size.y;
        camera.transform.LookAt(bounds.center);
        var light = new GameObject("Validation Light").AddComponent<Light>();
        light.transform.SetParent(holder.transform, false);
        light.transform.rotation = camera.transform.rotation * Quaternion.Euler(25, -25, 0);
        light.type = LightType.Directional;
        light.intensity = .7f;
        var target = new RenderTexture(640, 640, 24);
        var image = new Texture2D(640, 640, TextureFormat.RGB24, false);
        var previous = RenderTexture.active;
        try
        {
            camera.targetTexture = target;
            camera.Render();
            RenderTexture.active = target;
            image.ReadPixels(new Rect(0, 0, 640, 640), 0, 0);
            image.Apply();
            File.WriteAllBytes(Path.Combine(OutputFolder, name), image.EncodeToPNG());
        }
        finally
        {
            RenderTexture.active = previous;
            camera.targetTexture = null;
            target.Release();
            Object.DestroyImmediate(target);
            Object.DestroyImmediate(image);
            Object.DestroyImmediate(holder);
            Object.DestroyImmediate(snapshot);
            foreach (var mesh in meshes) Object.DestroyImmediate(mesh);
            for (int i = 0; i < skins.Length; i++) skins[i].enabled = enabled[i];
        }
    }

    static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
