// 실제 서버에서 받은 몸체를 검사한다. 인물 ID·토큰·얼굴 자료는 로그에 출력하지 않는다.
using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class PersonaServerBodyValidation
{
    const string Folder = "tools/_work/body_update_20261013";

    [MenuItem("Tools/Persona/서버 몸체 반영 검사")]
    public static void Inspect()
    {
        var spawner = UnityEngine.Object.FindObjectsOfType<PersonaSpawner>(true)
            .Single(s => s.gameObject.scene == SceneManager.GetActiveScene());
        var voice = spawner.voiceClient ?? UnityEngine.Object.FindObjectOfType<DialogueVoiceClient>(true);
        Directory.CreateDirectory(Folder);
        // SSH의 작업 대상 선택에만 사용한다. 이 파일은 Git 제외이며 토큰은 쓰지 않는다.
        var selection = new Selection
        {
            followsCurrent = string.IsNullOrWhiteSpace(spawner.sessionIdOverride) &&
                (voice == null || voice.followServerSession),
            sessionId = !string.IsNullOrWhiteSpace(spawner.sessionIdOverride) ? spawner.sessionIdOverride.Trim()
                : voice != null && !voice.followServerSession ? voice.sessionId : "",
            serverUrl = voice != null ? voice.serverUrl : spawner.serverUrl,
            localModel = !string.IsNullOrWhiteSpace(spawner.localGlbPath),
            testProfile = voice != null && voice.useTestProfile,
        };
        File.WriteAllText(Folder + "/selection.private.json", JsonUtility.ToJson(selection, true));
        var model = spawner.Spawned;
        var skins = model != null ? model.GetComponentsInChildren<SkinnedMeshRenderer>(true)
            : Array.Empty<SkinnedMeshRenderer>();
        var materials = skins.SelectMany(s => s.sharedMaterials).Where(m => m != null).ToArray();
        var report = new Report
        {
            utc = DateTime.UtcNow.ToString("o"), playing = EditorApplication.isPlaying,
            followsCurrent = selection.followsCurrent, localModel = selection.localModel,
            testProfile = selection.testProfile, modelLoaded = model != null,
            modelActive = model != null && model.activeInHierarchy, presentationStarted = spawner.PresentationStarted,
            newHands = materials.Any(m => m.name.StartsWith("Natural_Hand_Male", StringComparison.Ordinal)),
            blackCuffs = materials.Any(m => m.name.StartsWith("Cuff_Inner_Black", StringComparison.Ordinal)),
            allShadersSupported = materials.Length > 0 && materials.All(m => m.shader != null && m.shader.isSupported),
            skinCount = skins.Length, maximumSkinBones = skins.Length == 0 ? 0 : skins.Max(s => s.bones.Length),
            humanoidValid = model != null && model.GetComponentsInChildren<Animator>(true)
                .Any(a => a.avatar != null && a.avatar.isValid && a.avatar.isHuman),
            gazeReady = spawner.GazeReady,
            bodyShapeCount = model != null && model.GetComponent<PersonaBodyShapeController>() != null
                ? model.GetComponent<PersonaBodyShapeController>().BoundShapeCount : 0,
            defaultBodyShapeProfile = model != null && model.GetComponent<PersonaBodyShapeController>() != null &&
                model.GetComponent<PersonaBodyShapeController>().Profile == PersonaBodyShapeProfile.LoadDefault(),
            phase = spawner.arrival != null ? spawner.arrival.phase.ToString() : "",
        };
        File.WriteAllText(Folder + "/unity_runtime.json", JsonUtility.ToJson(report, true));
        Debug.Log($"[BodyRuntimeValidation] playing={report.playing}, loaded={report.modelLoaded}, " +
            $"newHands={report.newHands}, cuffs={report.blackCuffs}, active={report.modelActive}, phase={report.phase}");
    }

    [MenuItem("Tools/Persona/서버 몸체 등장 확인")]
    public static void ShowModel()
    {
        if (!EditorApplication.isPlaying)
            throw new InvalidOperationException("Play에서 서버 모델을 받은 뒤 확인해 주세요.");
        Inspect();
        var spawner = UnityEngine.Object.FindObjectOfType<PersonaSpawner>(true);
        if (spawner.Spawned == null || !spawner.Spawned.GetComponentsInChildren<SkinnedMeshRenderer>(true)
            .SelectMany(s => s.sharedMaterials).Any(m => m != null && m.name.StartsWith("Natural_Hand_Male")))
            throw new InvalidOperationException("새 서버 몸체를 아직 받지 못했습니다.");
        // 시작 버튼이 호출하는 동일한 등장·도착 경로만 실행한다. 마이크와 대화는 시작하지 않는다.
        spawner.BeginPresentation();
        Inspect();
    }

    [MenuItem("Tools/Persona/서버 캐릭터 체형 검사")]
    public static void CheckBodyShapes()
    {
        if (!EditorApplication.isPlaying) throw new InvalidOperationException("Play에서 검사해 주세요.");
        var spawner = UnityEngine.Object.FindObjectOfType<PersonaSpawner>(true);
        var model = spawner.Spawned;
        if (model == null) throw new InvalidOperationException("서버 모델을 아직 받지 못했습니다.");
        var controller = model.GetComponent<PersonaBodyShapeController>();
        if (controller == null || controller.BoundShapeCount != 6)
            throw new InvalidOperationException("서버 모델의 체형 키 6개가 연결되지 않았습니다.");
        var originalProfile = controller.Profile;
        var testProfile = ScriptableObject.CreateInstance<PersonaBodyShapeProfile>();
        var skins = model.GetComponentsInChildren<SkinnedMeshRenderer>(true);
        var meshes = skins.Select(_ => new Mesh()).ToArray();
        var report = new ShapeReport { utc = DateTime.UtcNow.ToString("o"), shapeCount = controller.BoundShapeCount };
        try
        {
            controller.Bind(testProfile);
            var baseline = Bake(skins, meshes);
            var bones = model.GetComponentsInChildren<Transform>(true);
            var positions = bones.Select(b => b.localPosition).ToArray();
            var rotations = bones.Select(b => b.localRotation).ToArray();
            for (int shape = 0; shape < 6; shape++)
                foreach (float value in new[] { -1f, 1f })
                {
                    PersonaBodyShapeValidation.SetValues(testProfile, shape, value);
                    controller.ApplyNow();
                    var changed = Bake(skins, meshes);
                    float movement = Difference(baseline, changed);
                    if (movement < .002f || movement > .2f) throw new InvalidOperationException("서버 체형 변형 범위 오류");
                    report.maximumMovement = Mathf.Max(report.maximumMovement, movement);
                    // 머리에는 체형 morph가 없다. 실제 BakeMesh에서도 머리·손·안감 정점이 고정돼야 한다.
                    for (int s = 0; s < skins.Length; s++)
                        for (int sub = 0; sub < meshes[s].subMeshCount; sub++)
                        {
                            var material = skins[s].sharedMaterials[sub];
                            if (material != null && material.name.StartsWith("mtl_avatar_body")) continue;
                            foreach (int vertex in meshes[s].GetTriangles(sub))
                                report.maximumProtectedMovement = Mathf.Max(report.maximumProtectedMovement,
                                    Vector3.Distance(baseline[s][vertex], changed[s][vertex]));
                        }
                    for (int b = 0; b < bones.Length; b++)
                        if (Vector3.Distance(positions[b], bones[b].localPosition) > .00001f ||
                            Quaternion.Angle(rotations[b], bones[b].localRotation) > .1f)
                            throw new InvalidOperationException("체형 변경 중 본 기준 자세 변경");
                    report.combinations++;
                }
            if (report.maximumProtectedMovement > .00001f)
                throw new InvalidOperationException("체형 변경이 머리·손·안감을 변형했습니다.");
            PersonaBodyShapeValidation.SetValues(testProfile, -1, 0f);
            controller.ApplyNow();
            report.maximumResetError = Difference(baseline, Bake(skins, meshes));
            if (report.maximumResetError > .00001f) throw new InvalidOperationException("기본 체형 복원 오류");
            report.success = true;
        }
        finally
        {
            controller.Bind(originalProfile);
            UnityEngine.Object.DestroyImmediate(testProfile);
            foreach (var mesh in meshes) UnityEngine.Object.DestroyImmediate(mesh);
            Directory.CreateDirectory(PersonaBodyShapeValidation.Folder);
            File.WriteAllText(PersonaBodyShapeValidation.Folder + "/server_runtime_shapes.json", JsonUtility.ToJson(report, true));
        }
        Inspect();
        Debug.Log($"[BodyShapeRuntimeValidation] success={report.success}, combinations={report.combinations}");
    }

    static Vector3[][] Bake(SkinnedMeshRenderer[] skins, Mesh[] meshes)
    {
        for (int i = 0; i < skins.Length; i++) skins[i].BakeMesh(meshes[i]);
        return meshes.Select(m => m.vertices).ToArray();
    }

    static float Difference(Vector3[][] a, Vector3[][] b)
    {
        float difference = 0f;
        for (int s = 0; s < a.Length; s++)
            for (int v = 0; v < a[s].Length; v++) difference = Mathf.Max(difference, Vector3.Distance(a[s][v], b[s][v]));
        return difference;
    }

    [Serializable] class Selection
    {
        public string sessionId, serverUrl;
        public bool followsCurrent, localModel, testProfile;
    }
    [Serializable] class Report
    {
        public string utc, phase;
        public bool playing, followsCurrent, localModel, testProfile, modelLoaded, modelActive,
            presentationStarted, newHands, blackCuffs, allShadersSupported, humanoidValid, gazeReady, defaultBodyShapeProfile;
        public int skinCount, maximumSkinBones, bodyShapeCount;
    }
    [Serializable] class ShapeReport
    {
        public string utc;
        public bool success;
        public int shapeCount, combinations;
        public float maximumMovement, maximumProtectedMovement, maximumResetError;
    }
}
