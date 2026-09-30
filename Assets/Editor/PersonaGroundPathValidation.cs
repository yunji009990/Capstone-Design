using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;
using Object = UnityEngine.Object;

// 씬·서버를 변경하지 않는 계산/Undo 검사와, 사용자가 Play한 씬의 읽기 전용 관측을 분리한다.
[InitializeOnLoad]
public static class PersonaGroundPathValidation
{
    const string Folder = "tools/_work/path_greeting_20260930";
    const string ObserveKey = "PersonaGroundPathValidation.Observe";
    static PlayReport playReport;
    static double startedAt;

    [Serializable] class Report
    {
        public string utc, error;
        public bool success, corners, noOvershoot, duplicatePoints, snapshot, floorPlane, undoRedo, invalidPath, feetOnFloor;
        public bool greetingProjection, greetingUndo, walkBeforeGreeting, greetingAtStart, missingGreetingSkipped, legacyGreeting;
        public float soleError;
    }
    [Serializable] class PlayReport
    {
        public string utc, error;
        public bool success, reachedSeat;
        public int walkSamples;
        public int greetingCount, greetingSamples, walkBeforeGreeting, walkAfterGreeting;
        public List<string> phases = new List<string>();
        public float length, maxPathError, maxFloorError, endpointError, rootHeight;
        public bool expectsPathGreeting;
        public float greetingDistance, maxGreetingPositionError;
    }

    static PersonaGroundPathValidation()
    {
        if (SessionState.GetBool(ObserveKey, false)) EditorApplication.update += Observe;
    }

    [MenuItem("Tools/Persona/바닥 경로 검사", priority = 24)]
    public static void Run()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode) return;
        Directory.CreateDirectory(Folder);
        var report = new Report { utc = DateTime.UtcNow.ToString("o") };
        var scene = EditorSceneManager.NewPreviewScene();
        GameObject go = null, bodyRoot = null;
        try
        {
            var route = PersonaGroundRoute.Create(new[] { Vector3.zero, Vector3.right, Vector3.right, new Vector3(1, 0, 2) });
            Require(route != null && Mathf.Abs(route.Length - 3f) < 1e-5f, "중복점 경로 길이");
            report.duplicatePoints = true;
            Require(Vector3.Distance(route.Evaluate(1.5f, out _, out _), new Vector3(1, 0, .5f)) < 1e-5f, "직각 코너 이후 위치");
            report.corners = true;
            Require(route.Evaluate(300f, out _, out _) == route.End && route.Evaluate(-3f, out _, out _) == route.Start, "큰 이동량의 끝점 고정");
            report.noOvershoot = true;
            Require(Mathf.Abs(route.FindClosestDistance(new Vector3(1.2f, 0, .5f)) - 1.5f) < 1e-5f &&
                    route.FindClosestDistance(new Vector3(-2, 0, 0)) == 0f &&
                    Mathf.Abs(route.FindClosestDistance(new Vector3(1, 0, 5)) - route.Length) < 1e-5f, "경로 위 인사 위치 선택과 양 끝 고정");
            report.greetingProjection = true;
            Require(PersonaGroundRoute.Create(new[] { Vector3.zero, Vector3.zero }) == null &&
                    PersonaGroundRoute.Create(new[] { Vector3.zero, new Vector3(float.NaN, 0, 0) }) == null, "잘못된 경로 거부");
            report.invalidPath = true;

            go = new GameObject("GroundPathValidation");
            SceneManager.MoveGameObjectToScene(go, scene);
            var path = go.AddComponent<PersonaGroundPath>();
            go.transform.SetPositionAndRotation(new Vector3(2, .4f, -3), Quaternion.Euler(0, 35, 0));
            go.transform.localScale = Vector3.one * 1.2f;
            var original = new[] { new Vector3(2, 5, -3), new Vector3(3, 8, -3), new Vector3(3, -8, -1) };
            path.SetWorldPoints(original);
            var frozen = path.CreateRoute();
            Require(Mathf.Abs(frozen.Length - 3) < 1e-5f && Mathf.Abs(frozen.End.y - .4f) < 1e-5f, "바닥 높이/부모 회전과 배율");
            report.floorPlane = true;

            Undo.IncrementCurrentGroup();
            PersonaGroundPathWindow.ApplyStroke(path, new[] { new Vector3(1, 9, 0), new Vector3(2, 8, 0), new Vector3(2, 0, 2) }, .01f);
            Undo.FlushUndoRecordObjects();
            Require(Vector3.Distance(path.GetWorldPoint(0), new Vector3(1, .4f, 0)) < 1e-5f, "그린 선 적용");
            Require(Vector3.Distance(frozen.Start, new Vector3(2, .4f, -3)) < 1e-5f, "재생용 경로 스냅샷 보존");
            report.snapshot = true;
            Undo.PerformUndo();
            Require(Vector3.Distance(path.GetWorldPoint(0), frozen.Start) < 1e-5f, "경로 그리기 Undo");
            Undo.PerformRedo();
            Require(Vector3.Distance(path.GetWorldPoint(0), new Vector3(1, .4f, 0)) < 1e-5f, "경로 그리기 Redo");
            report.undoRedo = true;
            Undo.ClearUndo(path);

            // 현재 고정 몸체를 실제 Avatar와 연결해 피벗이 아닌 신발 밑면이 경로 높이에 놓이는지 확인한다.
            bodyRoot = new GameObject("GroundPathBodyValidation");
            SceneManager.MoveGameObjectToScene(bodyRoot, scene);
            bodyRoot.transform.localScale = Vector3.one * 1.2f;
            var asset = AssetDatabase.LoadAssetAtPath<GameObject>("Assets/Models/human/Human.fbx");
            var body = (GameObject)PrefabUtility.InstantiatePrefab(asset, scene);
            body.transform.SetParent(bodyRoot.transform, false);
            var arrival = go.AddComponent<PersonaArrival>();
            var spawner = go.AddComponent<PersonaSpawner>();
            spawner.enabled = false;
            arrival.walkClip = AssetDatabase.LoadAllAssetsAtPath(PersonaArrivalSetup.AnimationFolder + "/" + PersonaArrivalSetup.WalkAnimationName + ".fbx")
                .OfType<AnimationClip>().First(c => !c.name.StartsWith("__preview__"));
            arrival.greetFacesUser = false;
            arrival.groundPath = path;
            arrival.referencePose = AssetDatabase.LoadAssetAtPath<PersonaHumanoidReferencePose>(PersonaReferencePoseSetup.AssetPath);
            Require(arrival.TryBegin(bodyRoot.transform, spawner) && arrival.UsesDrawnPath, "그린 경로로 실제 몸체 시작");
            var baked = new Mesh();
            float bottom = float.PositiveInfinity;
            foreach (var skin in bodyRoot.GetComponentsInChildren<SkinnedMeshRenderer>())
            {
                skin.BakeMesh(baked, true);
                foreach (var vertex in baked.vertices)
                    bottom = Mathf.Min(bottom, skin.transform.TransformPoint(vertex).y);
            }
            Object.DestroyImmediate(baked);
            report.soleError = Mathf.Abs(bottom - path.FloorHeight);
            Require(report.soleError < .001f, "신발 밑면 높이 보정");
            report.feetOnFloor = true;

            arrival.greetClip = AssetDatabase.LoadAllAssetsAtPath(PersonaArrivalSetup.AnimationFolder + "/Waving.fbx")
                .OfType<AnimationClip>().First(c => !c.name.StartsWith("__preview__"));
            arrival.initialIdleSeconds = 0f;
            arrival.greetOnPath = true;
            arrival.greetPathProgress = .5f;
            Require(arrival.TryBegin(bodyRoot.transform, spawner) && arrival.phase == PersonaArrival.Phase.걷기 &&
                    Mathf.Abs(arrival.PathGreetingDistance - arrival.PathLength * .5f) < 1e-5f, "중간 인사에서는 먼저 걷기");
            report.walkBeforeGreeting = true;
            Undo.IncrementCurrentGroup();
            var picked = path.CreateRoute().Evaluate(path.CreateRoute().Length * .7f, out _, out _) + Vector3.up * 5f;
            PersonaGroundPathWindow.SetGreetingPoint(arrival, picked);
            Undo.FlushUndoRecordObjects();
            Require(Mathf.Abs(arrival.greetPathProgress - .7f) < 1e-5f, "인사 위치 찍기");
            Undo.PerformUndo();
            Require(Mathf.Abs(arrival.greetPathProgress - .5f) < 1e-5f, "인사 위치 Undo");
            Undo.ClearUndo(arrival);
            report.greetingUndo = true;
            arrival.greetPathProgress = 0f;
            Require(arrival.TryBegin(bodyRoot.transform, spawner) && arrival.phase == PersonaArrival.Phase.인사방향전환 &&
                    Vector3.Distance(arrival.GroundPosition, path.CreateRoute().Start) < .001f, "시작점 인사도 제자리에서 진입");
            report.greetingAtStart = true;
            var greetingClip = arrival.greetClip;
            arrival.greetClip = null;
            Require(arrival.TryBegin(bodyRoot.transform, spawner) && arrival.phase == PersonaArrival.Phase.걷기, "인사 클립이 없으면 중간 정지 생략");
            report.missingGreetingSkipped = true;
            arrival.greetClip = greetingClip;
            path.enabled = false;
            Require(arrival.TryBegin(bodyRoot.transform, spawner) && !arrival.UsesDrawnPath &&
                    arrival.phase == PersonaArrival.Phase.인사, "경로를 끄면 기존 입구 인사로 복귀");
            report.legacyGreeting = true;
            report.success = true;
        }
        catch (Exception exception) { report.error = exception.ToString(); Debug.LogError("[GroundPathValidation] " + report.error); }
        finally
        {
            if (go != null) Object.DestroyImmediate(go);
            if (bodyRoot != null) Object.DestroyImmediate(bodyRoot);
            EditorSceneManager.ClosePreviewScene(scene);
            File.WriteAllText(Folder + "/validation.json", JsonUtility.ToJson(report, true));
            Debug.Log($"[GroundPathValidation] success={report.success}; {Folder}/validation.json");
        }
    }

    [MenuItem("Tools/Persona/다음 Play 경로 관측", priority = 25)]
    public static void ArmObservation()
    {
        playReport = null;
        SessionState.SetBool(ObserveKey, true);
        EditorApplication.update -= Observe;
        EditorApplication.update += Observe;
        Debug.Log("[GroundPathValidation] 다음 Play의 경로·착석을 읽기 전용으로 관측합니다.");
    }

    static void Observe()
    {
        if (!EditorApplication.isPlaying)
        {
            if (playReport != null) Finish("착석 확인 전에 Play가 종료되었습니다.");
            return;
        }
        if (EditorApplication.isPaused) return;
        if (playReport == null)
        {
            playReport = new PlayReport { utc = DateTime.UtcNow.ToString("o") };
            startedAt = EditorApplication.timeSinceStartup;
        }
        if (EditorApplication.timeSinceStartup - startedAt > 180) { Finish("180초 안에 착석하지 않았습니다."); return; }
        var arrival = SceneManager.GetActiveScene().GetRootGameObjects()
            .SelectMany(root => root.GetComponentsInChildren<PersonaArrival>(true)).FirstOrDefault();
        if (arrival == null || !arrival.UsesDrawnPath) return;
        string phase = arrival.phase.ToString();
        bool phaseChanged = playReport.phases.Count == 0 || playReport.phases[playReport.phases.Count - 1] != phase;
        if (phaseChanged) playReport.phases.Add(phase);
        if (phaseChanged && arrival.phase == PersonaArrival.Phase.인사) playReport.greetingCount++;
        playReport.length = arrival.PathLength;
        playReport.rootHeight = arrival.RootHeightAboveFloor;
        playReport.expectsPathGreeting = arrival.greetOnPath && arrival.greetClip != null;
        playReport.greetingDistance = arrival.PathGreetingDistance;
        if (playReport.expectsPathGreeting && (arrival.phase == PersonaArrival.Phase.인사 ||
            arrival.phase == PersonaArrival.Phase.인사방향전환 || arrival.phase == PersonaArrival.Phase.경로방향전환))
        {
            playReport.greetingSamples++;
            Vector3 stop = arrival.groundPath.CreateRoute().Evaluate(arrival.PathGreetingDistance, out _, out _);
            stop.y = arrival.GroundPosition.y;
            playReport.maxGreetingPositionError = Mathf.Max(playReport.maxGreetingPositionError,
                Vector3.Distance(stop, arrival.GroundPosition));
        }
        if (arrival.phase == PersonaArrival.Phase.걷기)
        {
            playReport.walkSamples++;
            if (playReport.greetingCount == 0) playReport.walkBeforeGreeting++;
            else playReport.walkAfterGreeting++;
            Vector3 actual = arrival.GroundPosition;
            float actualGroundY = actual.y;
            actual.y = arrival.groundPath.FloorHeight;
            float error = float.PositiveInfinity;
            for (int i = 1; i < arrival.groundPath.PointCount; i++)
            {
                Vector3 a = arrival.groundPath.GetWorldPoint(i - 1), d = arrival.groundPath.GetWorldPoint(i) - a;
                float t = d.sqrMagnitude > 1e-8f ? Mathf.Clamp01(Vector3.Dot(actual - a, d) / d.sqrMagnitude) : 0f;
                error = Mathf.Min(error, Vector3.Distance(actual, a + t * d));
            }
            playReport.maxPathError = Mathf.Max(playReport.maxPathError, error);
            playReport.maxFloorError = Mathf.Max(playReport.maxFloorError,
                Mathf.Abs(actualGroundY - arrival.groundPath.FloorHeight));
        }
        if (arrival.phase == PersonaArrival.Phase.돌기)
        {
            var end = arrival.groundPath.GetWorldPoint(arrival.groundPath.PointCount - 1);
            end.y = arrival.GroundPosition.y;
            playReport.endpointError = Vector3.Distance(arrival.GroundPosition, end);
        }
        if (arrival.IsSeated)
        {
            playReport.reachedSeat = true;
            bool greetingOK = !playReport.expectsPathGreeting || (playReport.greetingCount == 1 &&
                playReport.greetingSamples > 3 && playReport.maxGreetingPositionError < .002f &&
                (arrival.greetPathProgress <= 0f || playReport.walkBeforeGreeting > 0) &&
                (arrival.greetPathProgress >= 1f || playReport.walkAfterGreeting > 0));
            Finish(greetingOK && playReport.walkSamples > 3 && playReport.maxPathError < .002f && playReport.maxFloorError < .002f && playReport.endpointError < .002f
                ? null : "경로 오차 또는 관측 프레임 수를 확인하세요.");
        }
    }

    static void Finish(string error)
    {
        SessionState.SetBool(ObserveKey, false);
        EditorApplication.update -= Observe;
        playReport.error = error;
        playReport.success = error == null;
        Directory.CreateDirectory(Folder);
        File.WriteAllText(Folder + "/play_validation.json", JsonUtility.ToJson(playReport, true));
        Debug.Log($"[GroundPathValidation] Play success={playReport.success}, frames={playReport.walkSamples}; {Folder}/play_validation.json");
        playReport = null;
    }

    static void Require(bool condition, string message) { if (!condition) throw new InvalidOperationException(message); }
}
