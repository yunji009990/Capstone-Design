// PersonaArrivalSetup.cs — 도착 연출(PersonaArrival)을 메뉴 한 번으로 준비한다.
//
// Mixamo FBX 는 기본이 Generic 으로 들어온다. Humanoid 가 아니면 인물에 붙지 않으므로
// 여기서 Rig 설정을 Humanoid 로 바꾸고 다시 임포트한 뒤 클립을 꽂는다.
// 씬 파일은 건드리지 않는다 — Unity 가 열어 둔 씬은 디스크 수정이 덮이기 때문이다.

using System.Collections.Generic;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class PersonaArrivalSetup
{
    const string ModelFolder = "Assets/Models";
    const string EntranceName = "Persona 입구(시험용)";

    [MenuItem("Tools/Persona/도착 연출 준비", priority = 10)]
    public static void Prepare()
    {
        var report = new List<string>();

        // 1) 동작 클립만 Humanoid 로 바꾼다.
        //    대상은 Assets/Models "바로 아래" 에 있고 애니메이션이 실제로 들어 있는 파일뿐이다.
        //    하위 폴더까지 뒤지면 카페 가구·화분·바리스타 리그까지 Humanoid 로 바꿔 버린다(실제로 겪음).
        int converted = 0;
        foreach (var guid in AssetDatabase.FindAssets("t:Model", new[] { ModelFolder }))
        {
            string path = AssetDatabase.GUIDToAssetPath(guid);
            if (Path.GetDirectoryName(path).Replace('\\', '/') != ModelFolder) continue;

            var importer = AssetImporter.GetAtPath(path) as ModelImporter;
            if (importer == null || importer.animationType == ModelImporterAnimationType.Human) continue;
            bool hasMotion = AssetDatabase.LoadAllAssetsAtPath(path)
                .Any(a => a is AnimationClip c && !c.name.StartsWith("__preview__"));
            if (!hasMotion) { report.Add($"  건너뜀(동작 없음): {Path.GetFileName(path)}"); continue; }

            importer.animationType = ModelImporterAnimationType.Human;
            importer.avatarSetup = ModelImporterAvatarSetup.CreateFromThisModel;
            importer.SaveAndReimport();
            converted++;
            report.Add($"  Humanoid 로 다시 임포트: {Path.GetFileName(path)}");
        }

        // 2) 클립을 모은다.
        var clips = new List<(string path, AnimationClip clip)>();
        foreach (var guid in AssetDatabase.FindAssets("t:Model", new[] { ModelFolder }))
        {
            string path = AssetDatabase.GUIDToAssetPath(guid);
            if (Path.GetDirectoryName(path).Replace('\\', '/') != ModelFolder) continue;
            foreach (var asset in AssetDatabase.LoadAllAssetsAtPath(path))
                if (asset is AnimationClip c && !c.name.StartsWith("__preview__") && c.isHumanMotion)
                    clips.Add((path, c));
        }
        if (clips.Count == 0)
        {
            EditorUtility.DisplayDialog("Humanoid 클립 없음",
                $"{ModelFolder} 에서 Humanoid 클립을 찾지 못했다.\n" +
                "FBX 를 고른 뒤 Rig > Animation Type 을 Humanoid 로 바꾸고 Apply 할 것.", "확인");
            return;
        }

        // 파일 이름으로 정확히 고른다. 부분일치로 "Sitting" 을 찾으면 "Sitting Clap" 이 먼저 걸린다.
        AnimationClip Exact(string file) =>
            clips.FirstOrDefault(c => string.Equals(Path.GetFileNameWithoutExtension(c.path), file,
                                                   System.StringComparison.OrdinalIgnoreCase)).clip;
        AnimationClip Loose(string keyword) =>
            clips.FirstOrDefault(c => c.path.IndexOf(keyword, System.StringComparison.OrdinalIgnoreCase) >= 0).clip;

        var greet = Exact("Standing Greeting") ?? Loose("Greeting") ?? Loose("Wav");
        var walk = Exact("Walking") ?? Loose("Walk");
        var turn = Exact("Left Turn") ?? Exact("Right Turn") ?? Loose("Turn");
        var sitDown = Exact("Sitting") ?? Loose("Sit Down") ?? Loose("Stand To Sit");
        var seated = Exact("Sitting Clap") ?? Loose("Clap") ?? Loose("Sitting Idle");

        // 3) 프로브를 세운다.
        var spawnerForProbe = Object.FindObjectOfType<PersonaSpawner>();
        if (spawnerForProbe == null)
        {
            EditorUtility.DisplayDialog("PersonaSpawner 없음",
                "열려 있는 씬에서 PersonaSpawner 를 찾지 못했다. 인물 스포너가 있는 씬을 열고 실행할 것.", "확인");
            return;
        }
        // 연출은 스포너와 같은 오브젝트에 둔다. 스포너가 스폰 직후 Begin() 을 부른다.
        var probe = spawnerForProbe.GetComponent<PersonaArrival>();
        if (probe == null) probe = Undo.AddComponent<PersonaArrival>(spawnerForProbe.gameObject);
        if (spawnerForProbe.arrival != probe)
        {
            Undo.RecordObject(spawnerForProbe, "Link arrival");
            spawnerForProbe.arrival = probe;
            EditorUtility.SetDirty(spawnerForProbe);
            report.Add("  PersonaSpawner.arrival 에 연결했다");
        }
        Undo.RecordObject(probe, "Setup walk-in test");
        probe.greetClip = greet;
        probe.walkClip = walk;
        probe.turnClip = turn;
        probe.sitDownClip = sitDown;
        probe.seatedClip = seated;

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

        report.Insert(0, $"[PersonaWalkInTest] 준비 완료 — Humanoid 변환 {converted}개, 클립 {clips.Count}개 발견");
        report.Add($"  인사   : {Name(greet)}");
        report.Add($"  걷기   : {Name(walk)}");
        report.Add($"  돌기   : {Name(turn)}");
        report.Add($"  앉기   : {Name(sitDown)}");
        report.Add($"  앉음   : {Name(seated)}");
        report.Add($"  경유지 : {(probe.waypoints != null ? probe.waypoints.Length : 0)}개");
        report.Add($"  입구   : {(probe.entrance != null ? probe.entrance.name : "없음")}  " +
                   $"의자: {(probe.seat != null ? probe.seat.name : "없음 — 스포너 spawnPoint 를 확인할 것")}");
        report.Add("  「" + EntranceName + "」 를 카페 문 앞으로 옮긴 뒤 Play 하면 된다.");
        Debug.Log(string.Join("\n", report), probe);
    }

    static string Name(AnimationClip c) => c == null ? "없음" : $"{c.name} ({c.length:0.00}s)";
}
