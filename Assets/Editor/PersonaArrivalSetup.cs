// PersonaArrivalSetup.cs — 도착 연출(PersonaArrival)을 메뉴 한 번으로 준비한다.
//
// Mixamo FBX 는 기본이 Generic 으로 들어온다. Humanoid 가 아니면 인물에 붙지 않으므로
// 여기서 Rig 설정을 Humanoid 로 바꾸고 다시 임포트한 뒤 클립을 꽂는다.
// 현재 씬의 컴포넌트만 수정하고 dirty로 표시한다. 저장은 Unity의 씬 저장으로 수행한다.

using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

public static class PersonaArrivalSetup
{
    const string ModelFolder = "Assets/Models";
    public const string AnimationFolder = ModelFolder + "/use_animation";
    public const string WalkAnimationName = "Catwalk Walk Forward 03";
    const string ReferenceAnimationPath = AnimationFolder + "/Walking.fbx";
    const string EntranceName = "Persona 입구(시험용)";

    [MenuItem("Tools/Persona/도착 연출 준비", priority = 10)]
    public static void Prepare()
    {
        if (EditorApplication.isPlayingOrWillChangePlaymode)
        {
            Debug.LogWarning("[PersonaArrivalSetup] Play를 종료한 뒤 애니메이션을 교체하세요.");
            return;
        }
        var report = new List<string>();
        var activeScene = SceneManager.GetActiveScene();
        var spawnerForProbe = activeScene.GetRootGameObjects()
            .SelectMany(root => root.GetComponentsInChildren<PersonaSpawner>(true)).FirstOrDefault();
        if (spawnerForProbe == null)
        {
            Debug.LogError("[PersonaArrivalSetup] 현재 씬에 PersonaSpawner가 없습니다.");
            return;
        }
        var probe = spawnerForProbe.GetComponent<PersonaArrival>();

        // 1) 재생 클립과 몸체 기준 자세용 Walking만 변환한다. 같은 이름의 예전 FBX를 고르지 않는다.
        //    턴은 현재 연결된 클립을 보존하고, 없을 때만 기존 Left Turn을 사용한다.
        string turnPath = probe != null && probe.turnClip != null
            ? AssetDatabase.GetAssetPath(probe.turnClip) : ModelFolder + "/Left Turn.fbx";
        var paths = new[]
        {
            AnimationFolder + "/Idle.fbx", AnimationFolder + "/Waving.fbx",
            AnimationFolder + "/" + WalkAnimationName + ".fbx", AnimationFolder + "/Sitting Idle.fbx", turnPath,
            ReferenceAnimationPath, AnimationFolder + "/Sitting Talking.fbx",
        };
        if (paths.Any(path => !(AssetImporter.GetAtPath(path) is ModelImporter)))
        {
            Debug.LogError("[PersonaArrivalSetup] 필요한 FBX가 없습니다: " +
                string.Join(", ", paths.Where(path => !(AssetImporter.GetAtPath(path) is ModelImporter))));
            return;
        }
        int converted = 0;
        foreach (string path in paths.Distinct())
        {
            var importer = (ModelImporter)AssetImporter.GetAtPath(path);
            bool changed = importer.animationType != ModelImporterAnimationType.Human ||
                           importer.avatarSetup != ModelImporterAvatarSetup.CreateFromThisModel;
            if (importer.animationType != ModelImporterAnimationType.Human)
                importer.animationType = ModelImporterAnimationType.Human;
            if (importer.avatarSetup != ModelImporterAvatarSetup.CreateFromThisModel)
                importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            if (path.StartsWith(AnimationFolder + "/", System.StringComparison.Ordinal))
            {
                var settings = importer.clipAnimations;
                if (settings.Length == 0) { settings = importer.defaultClipAnimations; changed = true; }
                string name = Path.GetFileNameWithoutExtension(path);
                bool loop = name == "Idle" || name == WalkAnimationName || name == "Walking" || name == "Sitting Idle";
                foreach (var clip in settings)
                {
                    if (settings.Length == 1 && clip.name != name) { clip.name = name; changed = true; }
                    if (clip.loopTime != loop) { clip.loopTime = loop; changed = true; }
                }
                if (changed) importer.clipAnimations = settings;
            }
            if (!changed) continue;
            importer.SaveAndReimport();
            converted++;
            report.Add($"  Humanoid로 다시 임포트: {path}");
        }

        // 2) 모든 파일의 Humanoid Avatar와 동작을 확인한 뒤 씬 참조를 한꺼번에 교체한다.
        AnimationClip Clip(string path) => AssetDatabase.LoadAllAssetsAtPath(path)
            .OfType<AnimationClip>().FirstOrDefault(c => !c.name.StartsWith("__preview__") && c.isHumanMotion);
        if (paths.Any(path => Clip(path) == null || !AssetDatabase.LoadAllAssetsAtPath(path)
            .OfType<Avatar>().Any(avatar => avatar.isValid && avatar.isHuman)))
        {
            Debug.LogError("[PersonaArrivalSetup] Humanoid 임포트 실패. 씬의 기존 연결은 유지했습니다.");
            return;
        }
        var idle = Clip(paths[0]);
        var greet = Clip(paths[1]);
        var walk = Clip(paths[2]);
        var seated = Clip(paths[3]);
        var turn = probe != null && probe.turnClip != null ? probe.turnClip : Clip(turnPath);
        // 재생할 걷기를 바꿔도 이미 검증한 몸체 기준 자세는 그대로 쓴다.
        var referencePose = PersonaReferencePoseSetup.Build(ReferenceAnimationPath);

        // 3) 기존 경로·배율을 유지하며 클립 연결만 교체한다.
        // 연출은 스포너와 같은 오브젝트에 둔다. 스포너가 스폰 직후 Begin() 을 부른다.
        if (probe == null) probe = Undo.AddComponent<PersonaArrival>(spawnerForProbe.gameObject);
        if (spawnerForProbe.arrival != probe)
        {
            Undo.RecordObject(spawnerForProbe, "Link arrival");
            spawnerForProbe.arrival = probe;
            EditorUtility.SetDirty(spawnerForProbe);
            report.Add("  PersonaSpawner.arrival 에 연결했다");
        }
        Undo.RecordObject(probe, "Setup walk-in test");
        probe.referencePose = referencePose;
        probe.idleClip = idle;
        probe.greetClip = greet;
        probe.walkClip = walk;
        probe.turnClip = turn;
        probe.sitDownClip = null;
        probe.useSitDown = false;
        probe.blendSec = .4f;
        probe.sitTransitionSeconds = 1.1f;
        probe.seatedClip = seated;
        probe.loopSeated = true;
        probe.seatedPoseTime = 0f;
        probe.talkClip = Clip(paths[6]);
        probe.autoTalkGesture = true;
        probe.talkGestureRate = .25f;
        probe.nodClip = null;
        probe.shakeClip = null;
        probe.autoNodWhileListening = false;

        // 4) 입구 표시를 만든다. 씬 뷰에서 카페 문 앞으로 끌어다 놓으면 된다.
        if (probe.entrance == null)
        {
            var existing = GameObject.Find(EntranceName);
            if (existing == null)
            {
                existing = new GameObject(EntranceName);
                var spawner = Object.FindObjectOfType<PersonaSpawner>();
                Transform seat = spawner != null ? spawner.spawnPoint : null;
                // 의자에서 뒤로 4m 떨어진 곳을 임시 시작점으로 둔다.
                existing.transform.position = seat != null
                    ? seat.position - seat.forward * 4f
                    : Vector3.zero;
                Undo.RegisterCreatedObjectUndo(existing, "Create entrance marker");
            }
            probe.entrance = existing.transform;
        }
        if (probe.seat == null)
        {
            var spawner = Object.FindObjectOfType<PersonaSpawner>();
            if (spawner != null) probe.seat = spawner.spawnPoint;
        }

        // 4-2) 거쳐 갈 지점. 직선으로 오면 테이블을 뚫으므로 중간 지점을 둔다.
        //      입구→의자 사이를 2등분한 자리에 임시로 놓는다. 씬 뷰에서 끌어다 맞추면 된다.
        if (probe.waypoints == null || probe.waypoints.Length == 0)
        {
            Vector3 from = probe.entrance != null ? probe.entrance.position : Vector3.zero;
            Vector3 to = probe.seat != null ? probe.seat.position : from;
            var made = new Transform[2];
            for (int i = 0; i < made.Length; i++)
            {
                string name = $"Persona 경유지 {i + 1}(시험용)";
                var go = GameObject.Find(name);
                if (go == null)
                {
                    go = new GameObject(name);
                    go.transform.position = Vector3.Lerp(from, to, (i + 1) / (float)(made.Length + 1));
                    Undo.RegisterCreatedObjectUndo(go, "Create waypoint");
                }
                made[i] = go.transform;
            }
            probe.waypoints = made;
            report.Add("  경유지 2개를 입구~의자 사이에 임시로 놓았다 — 테이블을 피하도록 옮길 것");
        }

        // 5) 같은 뼈를 두고 싸우는 진단용 프로브는 꺼 둔다.
        var humanoid = Object.FindObjectOfType<PersonaHumanoidProbe>();
        if (humanoid != null && humanoid.runOnSpawn)
        {
            Undo.RecordObject(humanoid, "Disable humanoid probe");
            humanoid.runOnSpawn = false;
            EditorUtility.SetDirty(humanoid);
            report.Add("  PersonaHumanoidProbe 의 Run On Spawn 을 껐다(같은 뼈를 두고 겹친다)");
        }

        EditorUtility.SetDirty(probe);
        EditorSceneManager.MarkSceneDirty(probe.gameObject.scene);
        Selection.activeObject = probe.gameObject;

        report.Insert(0, $"[PersonaWalkInTest] 준비 완료 — Humanoid 임포트 {converted}개, 새 클립 4개 + 기존 턴");
        report.Add($"  서서 대기: {Name(idle)} ({probe.initialIdleSeconds:0.00}초)");
        report.Add($"  인사   : {Name(greet)}");
        report.Add($"  걷기   : {Name(walk)}");
        report.Add($"  돌기   : {Name(turn)}");
        report.Add($"  앉음   : {Name(seated)} 반복");
        report.Add("  착석 전환: 1.1초 동안 자세·위치를 함께 보간");
        report.Add("  대화 동작: Sitting Talking, 평균 4회 중 1회 / 연속 선택 금지");
        report.Add("  기존 앉기·박수·끄덕임·고개 젓기 클립은 연결 해제");
        report.Add($"  경유지 : {(probe.waypoints != null ? probe.waypoints.Length : 0)}개");
        report.Add($"  입구   : {(probe.entrance != null ? probe.entrance.name : "없음")}  " +
                   $"의자: {(probe.seat != null ? probe.seat.name : "없음 — 스포너 spawnPoint 를 확인할 것")}");
        report.Add("  경로를 확인하고 씬을 저장한 뒤 Play 하면 된다.");
        Debug.Log(string.Join("\n", report), probe);
    }

    static string Name(AnimationClip c) => c == null ? "없음" : $"{c.name} ({c.length:0.00}s)";
}
