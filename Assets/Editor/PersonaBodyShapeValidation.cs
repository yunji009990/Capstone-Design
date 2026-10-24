// 실제 FBX/glTFast 메시를 굽고 형태 변화·음수 가중치·보호 부위·기존 동작을 검사한다.
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

public static class PersonaBodyShapeValidation
{
    public const string Folder = "tools/_work/unity_body_shapes_20261013";
    static bool running;
    [Serializable] public class Result
    {
        public string model, pose;
        public int combinations, bones, shapeCount;
        public float frameWeight, maximumMovement, maximumProtectedMovement, maximumResetError;
    }
    [Serializable] public class Report
    {
        public string utc, error;
        public bool success, rebindPassed, undoRedoPassed;
        public List<Result> results = new List<Result>();
    }
    sealed class Snapshot
    {
        public SkinnedMeshRenderer skin;
        public Vector3[] vertices;
        public bool[] protectedVertices;
    }

    [MenuItem("Tools/다시봄/개발 도구/캐릭터 검사/체형 변형·리깅 보존")]
    public static async void Run()
    {
        if (running || EditorApplication.isPlayingOrWillChangePlaymode) return;
        running = true;
        var report = new Report { utc = DateTime.UtcNow.ToString("o") };
        Directory.CreateDirectory(Folder);
        var scene = EditorSceneManager.NewPreviewScene();
        GameObject root = null;
        GltfImport gltf = null;
        try
        {
            root = NewRoot(scene);
            var body = (GameObject)PrefabUtility.InstantiatePrefab(
                AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Models/human/Human.fbx"), scene);
            body.transform.SetParent(root.transform, false);
            Validate(root, "fbx", report);
            Object.DestroyImmediate(root);
            root = NewRoot(scene);
            gltf = new GltfImport(deferAgent: new UninterruptedDeferAgent());
            Require(await gltf.Load(File.ReadAllBytes("tools/_work/fixed_body_animation/body.glb")), "GLB 로딩 실패");
            Require(await gltf.InstantiateMainSceneAsync(root.transform), "GLB 생성 실패");
            root.transform.SetPositionAndRotation(new Vector3(2, 1, -3), Quaternion.Euler(0, 35, 0));
            root.transform.localScale = Vector3.one * .7f;
            Validate(root, "glb", report);
            report.success = true;
        }
        catch (Exception error)
        {
            report.error = error.ToString();
            Debug.LogError("[BodyShapeValidation] " + report.error);
        }
        finally
        {
            if (root != null) Object.DestroyImmediate(root);
            gltf?.Dispose();
            EditorSceneManager.ClosePreviewScene(scene);
            File.WriteAllText(Folder + "/unity_shapes.json", JsonUtility.ToJson(report, true));
            running = false;
            Debug.Log($"[BodyShapeValidation] success={report.success}, combinations=" +
                $"{report.results.Sum(r => r.combinations)}; {Folder}/unity_shapes.json");
        }
    }

    static GameObject NewRoot(Scene scene)
    {
        var root = new GameObject("BodyShapeValidation") { hideFlags = HideFlags.HideAndDontSave };
        SceneManager.MoveGameObjectToScene(root, scene);
        return root;
    }

    static void Validate(GameObject root, string label, Report report)
    {
        var profile = ScriptableObject.CreateInstance<PersonaBodyShapeProfile>();
        var controller = root.AddComponent<PersonaBodyShapeController>();
        controller.Bind(profile);
        Require(controller.BoundShapeCount == 6, "체형 키 6개를 연결하지 못했다");
        PlayableGraph graph = default;
        Avatar avatar = null;
        Animator animator = null;
        try
        {
            var skins = root.GetComponentsInChildren<SkinnedMeshRenderer>(true);
            foreach (var skin in skins) skin.updateWhenOffscreen = true;
            Require(skins.Max(s => s.bones.Length) == 60, "스킨 본 구조가 바뀌었다");
            var skinWithShapes = skins.First(s => s.sharedMesh.blendShapeCount > 0);
            float unit = skinWithShapes.sharedMesh.GetBlendShapeFrameWeight(0, 0);
            Require(Mathf.Approximately(unit, label == "fbx" ? 100f : 1f), "실제 Blend Shape 단위가 예상과 다르다");
            CheckPose(root, controller, profile, label, "rest", unit, report, true);

            var reference = AssetDatabase.LoadAssetAtPath<PersonaHumanoidReferencePose>(PersonaReferencePoseSetup.AssetPath);
            Require(PersonaHumanoid.TryPrepare(root.transform, out var skeleton, out avatar, out var error,
                label == "glb" ? root.transform : null, reference), error);
            Require(avatar.isValid && avatar.isHuman, "Humanoid Avatar가 유효하지 않다");
            animator = skeleton.GetComponent<Animator>();
            if (animator == null) animator = skeleton.gameObject.AddComponent<Animator>();
            animator.avatar = avatar;
            animator.applyRootMotion = false;
            animator.cullingMode = AnimatorCullingMode.AlwaysAnimate;
            foreach (string name in new[] { "Walking", "Waving", "Sitting Talking" })
            {
                var clip = AssetDatabase.LoadAllAssetsAtPath(PersonaArrivalSetup.AnimationFolder + "/" + name + ".fbx")
                    .OfType<AnimationClip>().First(c => c.isHumanMotion && !c.name.StartsWith("__preview__"));
                graph = PlayableGraph.Create("BodyShapeValidation");
                graph.SetTimeUpdateMode(DirectorUpdateMode.Manual);
                var playable = AnimationClipPlayable.Create(graph, clip);
                playable.SetApplyFootIK(false);
                AnimationPlayableOutput.Create(graph, "body", animator).SetSourcePlayable(playable);
                graph.Play();
                playable.SetTime(clip.length * .45f);
                graph.Evaluate(0f);
                CheckPose(root, controller, profile, label, name, unit, report, false, graph);
                graph.Destroy();
            }

            // 같은 컨트롤러를 다시 연결해도 설정을 잃거나 변형이 누적되지 않는다.
            SetValues(profile, -1, .55f);
            controller.Bind(profile);
            Require(controller.BoundShapeCount == 6, "반복 연결에서 키가 사라졌다");
            var changed = SnapshotAll(root);
            controller.Bind(profile);
            Require(Distance(changed, SnapshotAll(root)) < .00001f, "반복 연결에서 변형이 누적됐다");
            report.rebindPassed = true;

            Undo.IncrementCurrentGroup();
            SetValues(profile, -1, 0f, true);
            Undo.PerformUndo();
            Require(Mathf.Approximately(profile.GetValue(0), .55f), "설정 Undo 실패");
            Undo.PerformRedo();
            Require(Mathf.Approximately(profile.GetValue(0), 0f), "설정 Redo 실패");
            Undo.ClearUndo(profile); // 사용자의 다른 편집 Undo 기록은 유지한다.
            report.undoRedoPassed = true;
        }
        finally
        {
            if (graph.IsValid()) graph.Destroy();
            if (animator != null) animator.avatar = null;
            if (avatar != null) Object.DestroyImmediate(avatar);
            Object.DestroyImmediate(profile);
        }
    }

    static void CheckPose(GameObject root, PersonaBodyShapeController controller, PersonaBodyShapeProfile profile,
        string model, string pose, float unit, Report report, bool individual, PlayableGraph graph = default)
    {
        SetValues(profile, -1, 0f);
        controller.ApplyNow();
        var baseline = SnapshotAll(root);
        var bones = root.GetComponentsInChildren<Transform>(true);
        var positions = bones.Select(b => b.localPosition).ToArray();
        var rotations = bones.Select(b => b.localRotation).ToArray();
        var result = new Result { model = model, pose = pose, bones = 60, shapeCount = controller.BoundShapeCount, frameWeight = unit };
        int count = individual ? 7 : 1;
        for (int setting = 0; setting < count; setting++)
        {
            int shape = individual && setting < 6 ? setting : -1;
            foreach (float value in new[] { -1f, 1f })
            {
                SetValues(profile, shape, value);
                controller.ApplyNow();
                if (graph.IsValid()) graph.Evaluate(0f);
                var deformed = SnapshotAll(root);
                float movement = Distance(baseline, deformed);
                Require(movement > .003f && movement < .2f, "체형 변형이 없거나 과도하다");
                result.maximumMovement = Mathf.Max(result.maximumMovement, movement);
                for (int s = 0; s < baseline.Length; s++)
                    for (int v = 0; v < baseline[s].vertices.Length; v++)
                    {
                        var p = deformed[s].vertices[v];
                        Require(!float.IsNaN(p.sqrMagnitude) && !float.IsInfinity(p.sqrMagnitude), "변형 메시가 유효 좌표를 벗어났다");
                        if (baseline[s].protectedVertices[v])
                            result.maximumProtectedMovement = Mathf.Max(result.maximumProtectedMovement,
                                Vector3.Distance(p, baseline[s].vertices[v]));
                    }
                Require(result.maximumProtectedMovement < .00001f, "체형 키가 양손 또는 검은 소매 안감을 변형했다");
                for (int b = 0; b < bones.Length; b++)
                {
                    Require(Vector3.Distance(positions[b], bones[b].localPosition) < .00001f, "체형 키가 본 위치를 변경했다");
                    Require(Quaternion.Angle(rotations[b], bones[b].localRotation) < .1f, "체형 키가 본 회전을 변경했다");
                }
                result.combinations++;
            }
        }
        SetValues(profile, -1, 0f);
        controller.ApplyNow();
        result.maximumResetError = Distance(baseline, SnapshotAll(root));
        Require(result.maximumResetError < .00001f, "체형 0에서 기본 메시로 돌아오지 않았다");
        report.results.Add(result);
    }

    public static void SetValues(PersonaBodyShapeProfile profile, int shape, float value, bool undo = false)
    {
        var serialized = new SerializedObject(profile);
        serialized.Update();
        for (int i = 0; i < PersonaBodyShapeProfileEditor.Fields.Length; i++)
            serialized.FindProperty(PersonaBodyShapeProfileEditor.Fields[i]).floatValue = shape < 0 || shape == i ? value : 0f;
        if (undo) serialized.ApplyModifiedProperties();
        else serialized.ApplyModifiedPropertiesWithoutUndo();
    }

    static Snapshot[] SnapshotAll(GameObject root)
    {
        var snapshots = new List<Snapshot>();
        foreach (var skin in root.GetComponentsInChildren<SkinnedMeshRenderer>(true))
        {
            var mesh = new Mesh();
            try
            {
                skin.BakeMesh(mesh);
                var vertices = mesh.vertices;
                var mask = new bool[vertices.Length];
                var materials = skin.sharedMaterials;
                for (int sub = 0; sub < mesh.subMeshCount && sub < materials.Length; sub++)
                    if (materials[sub] != null && (materials[sub].name.StartsWith("Natural_Hand_Male") ||
                        materials[sub].name.StartsWith("Cuff_Inner_Black")))
                        foreach (int vertex in mesh.GetTriangles(sub)) mask[vertex] = true;
                snapshots.Add(new Snapshot { skin = skin, vertices = vertices, protectedVertices = mask });
            }
            finally { Object.DestroyImmediate(mesh); }
        }
        return snapshots.ToArray();
    }

    static float Distance(Snapshot[] a, Snapshot[] b)
    {
        Require(a.Length == b.Length, "변형 중 렌더러 수 변경");
        float maximum = 0f;
        for (int s = 0; s < a.Length; s++)
        {
            Require(a[s].vertices.Length == b[s].vertices.Length, "변형 중 정점 수 변경");
            for (int v = 0; v < a[s].vertices.Length; v++)
                maximum = Mathf.Max(maximum, Vector3.Distance(a[s].vertices[v], b[s].vertices[v]));
        }
        return maximum;
    }

    static void Require(bool condition, string message)
    {
        if (!condition) throw new InvalidOperationException(message);
    }
}
